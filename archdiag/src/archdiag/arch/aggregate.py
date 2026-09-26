"""Facts → nodes and edges: components, externals, the device actor, classes and messages.

Every edge carries its weight (number of facts), the best confidence among them, and up to
MAX_EVIDENCE file:line samples.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from archdiag.arch.components import Component, snake_id
from archdiag.facts.model import (
    BUILD_TARGET,
    FIELD_TYPE,
    INCLUDE,
    INHERITS,
    INTERFACE,
    MESSAGE,
    MESSAGE_IO,
    SYMBOL,
    Confidence,
    Fact,
)
from archdiag.model.schema import Group
from archdiag.model.schema_v2 import MAX_EVIDENCE, EdgeV2, Evidence, MessageV2, NodeV2

ACTOR_ID = "actor_device"
_NUMBER = re.compile(r"^(0[xX][0-9a-fA-F]+|\d+)[uUlL]*$")
_CLASS_KINDS = frozenset({"class", "struct"})


def layer_group_id(layer: str) -> str:
    return snake_id(layer, "layer_")


@dataclass
class _EdgeAcc:
    source: str
    target: str
    kind: str
    weight: int = 0
    confidence: Confidence = Confidence.LOW
    evidence: list[Evidence] = field(default_factory=list)
    labels: Counter[str] = field(default_factory=Counter)

    def add(self, fact: Fact, detail: str, confidence: Confidence | None = None,
            label: str | None = None) -> None:
        conf = confidence or fact.confidence
        self.weight += 1
        self.confidence = max(self.confidence, conf)
        self.evidence.append(Evidence(file=fact.file, line=fact.line, fact=fact.kind,
                                      source=fact.source, confidence=conf.label, detail=detail))
        if label:
            self.labels[label] += 1


class _Edges:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str], _EdgeAcc] = {}

    def add(self, source: str, target: str, kind: str, fact: Fact, detail: str,
            confidence: Confidence | None = None, label: str | None = None) -> None:
        if source == target:
            return
        key = (source, target, kind)
        if key not in self.items:
            self.items[key] = _EdgeAcc(source, target, kind)
        self.items[key].add(fact, detail, confidence, label)

    def build(self) -> list[EdgeV2]:
        out = []
        for (source, target, kind), acc in sorted(self.items.items()):
            evidence = sorted(acc.evidence, key=lambda e: (e.file, e.line))
            label = ", ".join(name for name, _ in acc.labels.most_common(3)) or None
            out.append(EdgeV2.model_validate({
                "id": f"{source}__{kind}__{target}", "from": source, "to": target,
                "label": label, "kind": kind, "weight": acc.weight,
                "confidence": acc.confidence.label, "evidence": evidence[:MAX_EVIDENCE],
                "evidence_total": len(evidence),
            }))
        return out


@dataclass
class ClassInfo:
    id: str
    name: str  # qualified
    kind: str
    file: str
    line: int
    component: str
    interface: bool = False
    references: int = 0


@dataclass
class Aggregation:
    groups: list[Group]
    nodes: list[NodeV2]
    edges: list[EdgeV2]
    messages: list[MessageV2]
    classes: dict[str, ClassInfo]
    component_of: dict[str, str]
    targets: list[dict[str, object]]


def _covering_dirs(files: Sequence[str]) -> list[str]:
    dirs = sorted({posixpath.dirname(f) for f in files})
    out: list[str] = []
    for directory in dirs:
        if not directory:
            continue
        if not any(directory == d or directory.startswith(f"{d}/") for d in out):
            out.append(directory)
    root_files = [f for f in files if "/" not in f]
    return out + root_files


class _ClassIndex:
    def __init__(self, classes: Iterable[ClassInfo]) -> None:
        self.by_name: dict[str, ClassInfo] = {}
        self.by_simple: dict[str, list[ClassInfo]] = defaultdict(list)
        for info in classes:
            self.by_name.setdefault(info.name, info)
            self.by_simple[info.name.rsplit("::", 1)[-1]].append(info)

    def resolve(self, name: str, near: str) -> ClassInfo | None:
        if name in self.by_name:
            return self.by_name[name]
        candidates = self.by_simple.get(name.rsplit("::", 1)[-1], [])
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            near_dir = posixpath.dirname(near)
            return max(candidates, key=lambda c: (
                len(posixpath.commonpath([posixpath.dirname(c.file) or ".", near_dir or "."])),
                c.name,
            ))
        return None


def _message_ref(args: Sequence[str], by_name: Mapping[str, MessageV2],
                 by_number: Mapping[int, MessageV2]) -> MessageV2 | None:
    for arg in args:
        token = arg.strip().split("::")[-1]
        if token in by_name:
            return by_name[token]
        if _NUMBER.match(arg.strip()):
            number = int(re.sub(r"[uUlL]+$", "", arg.strip()), 0)
            if number in by_number:
                return by_number[number]
    return None


def aggregate(components: Sequence[Component], facts: Sequence[Fact],
              line_counts: Mapping[str, int], repo_name: str) -> Aggregation:
    component_of = {path: c.id for c in components for path in c.files}
    kind_of = {c.id: c.kind for c in components}

    # --- groups and component nodes ------------------------------------------------------
    layers = list(dict.fromkeys(c.layer for c in components if c.layer))
    groups = [Group(id=layer_group_id(layer), label=layer) for layer in layers]
    nodes: list[NodeV2] = []
    for c in components:
        nodes.append(NodeV2(
            id=c.id, label=c.name, kind=c.kind, level="component",
            group=layer_group_id(c.layer) if c.layer else None, layer=c.layer, origin=c.origin,
            paths=list(c.paths) if c.origin == "inferred" and c.paths else _covering_dirs(c.files),
            metrics={"files": len(c.files),
                     "lines": sum(line_counts.get(f, 0) for f in c.files)},
        ))

    # --- classes --------------------------------------------------------------------------
    classes: dict[str, ClassInfo] = {}
    for fact in facts:
        if fact.kind == SYMBOL and fact.data.get("kind") in _CLASS_KINDS:
            name = str(fact.data["name"])
            owner = component_of.get(fact.file)
            if owner is None or name in classes:
                continue
            classes[name] = ClassInfo(snake_id(name, "cls_"), name, str(fact.data["kind"]),
                                      fact.file, fact.line, owner)
    for fact in facts:
        if fact.kind == INTERFACE and fact.data.get("class") in classes:
            classes[str(fact.data["class"])].interface = True
    index = _ClassIndex(classes.values())

    edges = _Edges()
    class_edges = _Edges()

    def dep_kind(source: str) -> str:
        return "test" if kind_of.get(source) == "test" else "depends"

    # --- includes -------------------------------------------------------------------------
    externals: dict[str, str] = {}
    for fact in facts:
        if fact.kind != INCLUDE or fact.file not in component_of:
            continue
        source = component_of[fact.file]
        target_file = fact.data.get("target")
        detail = f"#include {'<' if fact.data.get('system') else '\"'}{fact.data.get('spelled')}"
        if target_file and target_file in component_of:
            edges.add(source, component_of[target_file], dep_kind(source), fact, detail)
        elif fact.data.get("external") and kind_of.get(source) != "test":
            group = str(fact.data["external"])
            ext_id = externals.setdefault(group, snake_id(group, "ext_"))
            edges.add(source, ext_id, "uses", fact, detail)

    # --- inheritance and composition ------------------------------------------------------
    for fact in facts:
        if fact.kind == INHERITS and fact.file in component_of:
            child = classes.get(str(fact.data["child"]))
            base = index.resolve(str(fact.data["base"]), fact.file)
            if child is None or base is None:
                continue
            base.references += 1
            child.references += 1
            kind = "implements" if base.interface else "inherits"
            detail = f"{child.name} : {fact.data.get('access', 'public')} {base.name}"
            class_edges.add(child.id, base.id, kind, fact, detail)
            edges.add(child.component, base.component, dep_kind(child.component), fact, detail)
        elif fact.kind == FIELD_TYPE and fact.file in component_of:
            owner = classes.get(str(fact.data["owner"]))
            if owner is None:
                continue
            for type_name in fact.data.get("types", []):
                used = index.resolve(str(type_name), fact.file)
                if used is None or used.name == owner.name:
                    continue
                used.references += 1
                owner.references += 1
                detail = f"{owner.name} has {used.name}"
                class_edges.add(owner.id, used.id, "composes", fact, detail)
                edges.add(owner.component, used.component, dep_kind(owner.component), fact,
                          detail)

    # --- messages -------------------------------------------------------------------------
    messages: dict[str, MessageV2] = {}
    for fact in facts:
        if fact.kind != MESSAGE:
            continue
        name = str(fact.data["name"])
        protocol = str(fact.data["protocol"])
        message_id = snake_id(f"{protocol}_{name}", "msg_")
        if message_id in messages:
            continue
        messages[message_id] = MessageV2(
            id=message_id, protocol=protocol, name=name,
            message_id=fact.data.get("id") if isinstance(fact.data.get("id"), int) else None,
            senders=[str(s) for s in fact.data.get("senders", [])],
            receivers=[str(r) for r in fact.data.get("receivers", [])],
            confidence=fact.confidence.label, file=fact.file, line=fact.line,
        )
    by_name = {m.name: m for m in messages.values()}
    by_number = {m.message_id: m for m in messages.values() if m.message_id is not None}
    definers: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        if fact.kind == SYMBOL and fact.data.get("kind") == "function" and fact.file in component_of:
            definers[str(fact.data["name"]).rsplit("::", 1)[-1]].add(component_of[fact.file])

    uses_actor = False
    for fact in facts:
        if fact.kind != MESSAGE_IO or fact.file not in component_of:
            continue
        component = component_of[fact.file]
        if kind_of.get(component) == "test":
            continue
        function = str(fact.data["function"])
        send = fact.data.get("direction") == "send"
        message = _message_ref(fact.data.get("args", []), by_name, by_number)
        confidence = Confidence.MEDIUM if message else Confidence.LOW
        label = message.name if message else str(fact.data.get("protocol", "message"))
        detail = f"{'sends' if send else 'receives'} via {function}()"
        others = definers.get(function, set()) - {component}
        if fact.data.get("role") == "call" and others:
            peers = sorted(others)
        else:
            peers = [ACTOR_ID]
            uses_actor = True
        for peer in peers:
            source, target = (component, peer) if send else (peer, component)
            edges.add(source, target, "message", fact, detail, confidence, label)
            if message:
                side = message.senders if send else message.receivers
                if component not in side:
                    side.append(component)

    # --- externals, actor and class nodes -------------------------------------------------
    for group, ext_id in sorted(externals.items(), key=lambda kv: kv[1]):
        nodes.append(NodeV2(id=ext_id, label=group, kind="library", level="external",
                            origin="inferred"))
    if uses_actor:
        nodes.append(NodeV2(id=ACTOR_ID, label="Device / bus", kind="actor", level="actor",
                            description="Hardware or bus reached through protocol calls "
                                        "(inferred from name patterns)."))
    for info in sorted(classes.values(), key=lambda c: c.id):
        nodes.append(NodeV2(
            id=info.id, label=info.name.rsplit("::", 1)[-1],
            kind="interface" if info.interface else info.kind, level="class",
            group=info.component, paths=[info.file],
            metrics={"line": info.line, "references": info.references},
        ))

    targets = [dict(f.data, file=f.file, line=f.line) for f in facts if f.kind == BUILD_TARGET]
    return Aggregation(groups, nodes, edges.build() + class_edges.build(),
                       sorted(messages.values(), key=lambda m: m.id), classes, component_of,
                       targets)
