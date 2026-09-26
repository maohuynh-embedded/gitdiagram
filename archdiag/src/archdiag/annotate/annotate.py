"""LLM annotation of a static model (map-reduce). The LLM names and explains; it never adds,
removes or rewires nodes and edges. Every result is cached, and failures fall back to the
plain static labels.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from archdiag.arch.aggregate import ClassInfo
from archdiag.config import Settings
from archdiag.facts.store import FactStore
from archdiag.llm.client import ChatClient, LLMError, LLMUnavailable, Message
from archdiag.model.schema_v2 import ArchModelV2, EdgeV2, NodeV2
from archdiag.scan.snapshot import RepoSnapshot

ANNOTATION_VERSION = "b1"
MAX_SYMBOLS = 30
MAX_EXCERPTS = 3
EXCERPT_CHARS = 2000
MAX_LABEL = 60
MAX_EDGE_LABEL = 40
MAX_DESCRIPTION = 600
MAX_RESPONSIBILITIES = 6
MAP_RETRIES = 2

Progress = Callable[[str, str], None]

MAP_SYSTEM = """\
You annotate one component of a software architecture that static analysis has already
determined. You may only name and describe. You must not invent components, relations,
files or behaviour that the input does not show.

Return one JSON object:
{"label": short human name of the component (2 to 5 words),
 "description": one or two sentences on what it is for,
 "responsibilities": up to 6 short phrases,
 "edge_labels": {"<edge id>": "2 to 5 word label of why this component uses the target"}}

Only use edge ids listed under "Outgoing dependencies". Write in {language}.
Reply with the JSON object only.
"""

REDUCE_SYSTEM = """\
You write the overview of a software architecture from facts that static analysis and
per-component notes established. Do not add components or relations that are not listed.

Return one JSON object:
{"title": short name of the system,
 "explanation": {language} prose of 150 to 500 words: what the system does, how its layers
 and main components fit together, how data flows (including device or bus communication if
 listed), and any architecture violations worth attention. Name the components you rely on.}

Reply with the JSON object only.
"""


class ComponentNote(BaseModel):
    label: str
    description: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    edge_labels: dict[str, str] = Field(default_factory=dict)


class SystemNote(BaseModel):
    title: str
    explanation: str


@dataclass
class AnnotationResult:
    model: ArchModelV2
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


def _parse(raw: str, model: type[BaseModel]) -> tuple[Any, str | None]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        return model.model_validate(json.loads(text)), None
    except (ValueError, ValidationError) as exc:
        return None, str(exc)[:500]


def _clean_note(note: ComponentNote, allowed_edges: set[str]) -> tuple[ComponentNote, list[str]]:
    warnings = []
    unknown = sorted(set(note.edge_labels) - allowed_edges)
    if unknown:
        warnings.append(f"ignored labels for unknown edges: {', '.join(unknown[:5])}")
    labels = {
        k: " ".join(v.split())[:MAX_EDGE_LABEL]
        for k, v in note.edge_labels.items()
        if k in allowed_edges and v.strip()
    }
    return ComponentNote(
        label=" ".join(note.label.split())[:MAX_LABEL],
        description=" ".join(note.description.split())[:MAX_DESCRIPTION],
        responsibilities=[" ".join(r.split())[:80] for r in note.responsibilities if r.strip()][
            :MAX_RESPONSIBILITIES
        ],
        edge_labels=labels,
    ), warnings


def _excerpts(snapshot: RepoSnapshot, files: Sequence[str]) -> list[tuple[str, str]]:
    headers = [f for f in files if f.endswith((".h", ".hpp", ".hh", ".hxx"))]
    sizes = {f.path: f.size for f in snapshot.files}
    chosen = sorted(headers or list(files), key=lambda f: -sizes.get(f, 0))[:MAX_EXCERPTS]
    out = []
    for path in chosen:
        try:
            out.append((path, snapshot.read_text(path, EXCERPT_CHARS)))
        except (KeyError, OSError):
            continue
    return out


def _component_prompt(
    node: NodeV2,
    files: Sequence[str],
    classes: Sequence[ClassInfo],
    outgoing: Sequence[EdgeV2],
    incoming: Sequence[EdgeV2],
    names: dict[str, str],
    snapshot: RepoSnapshot,
) -> str:
    lines = [
        f"Component id: {node.id}",
        f"Path(s): {', '.join(node.paths) or '(root files)'}",
        f"Layer: {node.layer}",
        f"Size: {node.metrics.get('files', 0)} files, {node.metrics.get('lines', 0)} lines",
        "",
        "Key types:",
    ]
    ranked = sorted(classes, key=lambda c: (-c.references, c.name))[:MAX_SYMBOLS]
    lines.extend(f"- {c.name} ({'interface' if c.interface else c.kind})" for c in ranked)
    if not ranked:
        lines.append("- (none found)")
    lines += ["", "Outgoing dependencies (edge id: target, kind, weight, examples):"]
    for edge in outgoing:
        samples = "; ".join(e.detail for e in edge.evidence[:3] if e.detail)
        lines.append(
            f"- {edge.id}: {names.get(edge.target, edge.target)}, {edge.kind}, "
            f"{edge.weight}, {samples}"
        )
    if not outgoing:
        lines.append("- (none)")
    lines += [
        "",
        "Used by: "
        + (", ".join(sorted({names.get(e.source, e.source) for e in incoming})) or "(nobody)"),
    ]
    lines += ["", "Header excerpts:"]
    for path, text in _excerpts(snapshot, files):
        lines += [f"<<<FILE {path}>>>", text, "<<<END FILE>>>"]
    return "\n".join(lines)


def _chat(
    client: ChatClient,
    messages: list[Message],
    schema: dict[str, Any],
    kind: type[BaseModel],
    retries: int,
) -> tuple[Any, str | None]:
    error: str | None = None
    for _ in range(retries + 1):
        raw = client.chat(messages, schema)
        parsed, error = _parse(raw, kind)
        if parsed is not None:
            return parsed, None
        messages = [
            *messages,
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": f"That was invalid ({error}). Return the corrected JSON object only.",
            },
        ]
    return None, error


def annotate(
    model: ArchModelV2,
    component_files: dict[str, Sequence[str]],
    classes: Sequence[ClassInfo],
    snapshot: RepoSnapshot,
    client: ChatClient,
    store: FactStore,
    settings: Settings,
    progress: Progress | None = None,
) -> AnnotationResult:
    def report(message: str) -> None:
        if progress:
            progress("annotate", message)

    result = AnnotationResult(model.model_copy(deep=True))
    target = result.model
    names = {n.id: n.label for n in target.nodes}
    components = [
        n for n in target.nodes if n.level == "component" and n.kind not in ("test", "unassigned")
    ]
    by_component: dict[str, list[ClassInfo]] = {}
    for info in classes:
        by_component.setdefault(info.component, []).append(info)
    edge_by_id = {e.id: e for e in target.edges}
    notes: dict[str, ComponentNote] = {}
    cached = called = failed = 0

    for position, node in enumerate(components, start=1):
        outgoing = [
            e
            for e in target.edges
            if e.source == node.id and e.kind in ("depends", "message", "uses")
        ]
        incoming = [e for e in target.edges if e.target == node.id and e.kind == "depends"]
        files = component_files.get(node.id, [])
        key = hashlib.sha256(
            json.dumps(
                [
                    ANNOTATION_VERSION,
                    settings.model,
                    settings.language,
                    [snapshot.hashes.get(f) for f in files],
                    [(e.id, e.weight) for e in outgoing],
                ]
            ).encode()
        ).hexdigest()
        stored = store.get_annotation(key)
        note: ComponentNote | None = None
        if stored:
            try:
                note = ComponentNote.model_validate_json(stored)
                cached += 1
            except ValidationError:
                note = None
        if note is None:
            report(f"component {position}/{len(components)}: {node.label}")
            prompt = _component_prompt(
                node, files, by_component.get(node.id, []), outgoing, incoming, names, snapshot
            )
            messages: list[Message] = [
                {"role": "system", "content": MAP_SYSTEM.replace("{language}", settings.language)},
                {"role": "user", "content": prompt},
            ]
            try:
                parsed, error = _chat(
                    client, messages, _schema(ComponentNote), ComponentNote, MAP_RETRIES
                )
            except LLMUnavailable:
                raise
            except LLMError as exc:
                parsed, error = None, str(exc)
            called += 1
            if parsed is None:
                failed += 1
                result.warnings.append(
                    f"Annotation of {node.id} failed ({error}); kept the static label."
                )
                continue
            note, note_warnings = _clean_note(parsed, {e.id for e in outgoing})
            result.warnings.extend(f"{node.id}: {w}" for w in note_warnings)
            store.put_annotation(key, note.model_dump_json())
        notes[node.id] = note

    for node in target.nodes:
        note = notes.get(node.id)
        if note is None:
            continue
        if note.label:
            node.label = note.label
        node.description = note.description
        node.responsibilities = note.responsibilities
        for edge_id, label in note.edge_labels.items():
            edge = edge_by_id.get(edge_id)
            if edge is not None and edge.kind == "depends":
                edge.label = label

    summary = {
        "components": [
            {
                "id": n.id,
                "label": n.label,
                "layer": n.layer,
                "description": n.description,
                "responsibilities": n.responsibilities,
            }
            for n in target.nodes
            if n.level == "component" and n.kind != "test"
        ],
        "dependencies": [
            {
                "from": names.get(e.source, e.source),
                "to": names.get(e.target, e.target),
                "kind": e.kind,
                "weight": e.weight,
                "label": e.label,
            }
            for e in sorted(target.edges, key=lambda e: -e.weight)
            if e.kind in ("depends", "message")
            and not e.source.startswith(("cls_", "lyr_", "tgt_", "sys_"))
        ][:40],
        "violations": [{"rule": v.rule, "message": v.message} for v in target.violations][:20],
        "messages": [m.name for m in target.messages][:30],
    }
    reduce_key = hashlib.sha256(
        json.dumps(
            [ANNOTATION_VERSION, settings.model, settings.language, summary], sort_keys=True
        ).encode()
    ).hexdigest()
    stored = store.get_annotation(reduce_key)
    system: SystemNote | None = None
    if stored:
        try:
            system = SystemNote.model_validate_json(stored)
            cached += 1
        except ValidationError:
            system = None
    if system is None:
        report("system overview")
        messages = [
            {"role": "system", "content": REDUCE_SYSTEM.replace("{language}", settings.language)},
            {
                "role": "user",
                "content": f"System: {snapshot.name}\n\n"
                + json.dumps(summary, indent=1, ensure_ascii=False),
            },
        ]
        try:
            parsed, error = _chat(client, messages, _schema(SystemNote), SystemNote, MAP_RETRIES)
        except LLMUnavailable:
            raise
        except LLMError as exc:
            parsed, error = None, str(exc)
        called += 1
        if parsed is None:
            failed += 1
            result.warnings.append(f"The system overview failed ({error}).")
        else:
            system = parsed
            store.put_annotation(reduce_key, parsed.model_dump_json())
    if system is not None:
        target.title = " ".join(system.title.split())[:80] or target.title
        target.explanation = system.explanation.strip()
    result.stats = {"llm_calls": called, "annotations_cached": cached, "annotations_failed": failed}
    return result
