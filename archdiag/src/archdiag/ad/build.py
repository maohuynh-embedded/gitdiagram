"""ArchModel v2 + declarations → notation-neutral 42010 architecture description."""

from __future__ import annotations

import posixpath
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from archdiag import __version__
from archdiag.ad.config import ADConfig, Party
from archdiag.ad.decisions import collect_decisions
from archdiag.ad.model import AD, Concern, Element, Relation, Stakeholder
from archdiag.ad.viewpoints import DEFAULT_CONCERNS, DEFAULT_STAKEHOLDERS, VIEWPOINTS
from archdiag.arch.components import snake_id
from archdiag.model.schema_v2 import ArchModelV2, EdgeV2, NodeV2
from archdiag.scan.snapshot import RepoSnapshot

SYSTEM_ID = "system"
BUS_ID = "bus_can"
LANGUAGE_NAMES = {
    "c": "C",
    "c-header": "C",
    "cpp": "C++",
    "cpp-header": "C++",
    "qml": "QML",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "python": "Python",
    "asm": "Assembly",
    "rust": "Rust",
    "go": "Go",
    "java": "Java",
    "kotlin": "Kotlin",
    "csharp": "C#",
}
_FRAMEWORKS = {"Qt": "Qt"}
_CLASS_RELATIONS = {"inherits": "specializes", "implements": "realizes", "composes": "composes"}


def _first_sentence(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    end = text.find(". ")
    sentence = text[: end + 1] if 0 < end < limit else text[:limit]
    return sentence


def _files_under(paths: Iterable[str], snapshot: RepoSnapshot) -> list[str]:
    out = []
    prefixes = [(p, f"{p}/") for p in paths]
    for entry in snapshot.files:
        if any(entry.path == p or entry.path.startswith(prefix) for p, prefix in prefixes):
            out.append(entry.path)
    return out


def _languages(files: Iterable[str], snapshot: RepoSnapshot, top: int) -> list[str]:
    language = {f.path: f.language or "" for f in snapshot.files}
    counts = Counter(
        LANGUAGE_NAMES[language[f]] for f in files if language.get(f, "") in LANGUAGE_NAMES
    )
    return [name for name, _ in counts.most_common(top)]


def _evidence(edge: EdgeV2) -> list[str]:
    return [f"{e.file}:{e.line}" for e in edge.evidence[:5]]


def _relation(
    kind: str,
    source: str,
    target: str,
    *,
    description: str = "",
    technology: str = "",
    weight: int = 1,
    confidence: str = "medium",
    b_edge_ids: list[str] | None = None,
    evidence: list[str] | None = None,
) -> Relation:
    return Relation(
        id=f"{kind}__{source}__{target}",
        type=kind,
        source=source,
        target=target,
        description=description,
        technology=technology,
        weight=weight,
        confidence=confidence,
        b_edge_ids=b_edge_ids or [],
        evidence=evidence or [],
    )


class _Builder:
    def __init__(self, model: ArchModelV2, snapshot: RepoSnapshot, config: ADConfig) -> None:
        self.model = model
        self.snapshot = snapshot
        self.config = config
        self.elements: dict[str, Element] = {}
        self.relations: dict[str, Relation] = {}
        self.issues: list[str] = []

    def add(self, element: Element) -> None:
        self.elements[element.id] = element

    def relate(self, relation: Relation) -> None:
        existing = self.relations.get(relation.id)
        if existing is None:
            self.relations[relation.id] = relation
        else:
            existing.weight += relation.weight
            existing.b_edge_ids.extend(relation.b_edge_ids)

    def build(self) -> None:
        nodes = self.model.nodes
        by_level: dict[str, list[NodeV2]] = {}
        for node in nodes:
            by_level.setdefault(node.level, []).append(node)
        uses_qt = {
            e.source
            for e in self.model.edges
            if e.kind == "uses"
            and (self.model.node(e.target) or NodeV2(id="x", label="", kind="")).label == "Qt"
        }

        self.add(
            Element(
                SYSTEM_ID,
                self.model.title,
                "system",
                _first_sentence(self.model.explanation)
                or f"The software built from {self.snapshot.name}.",
                b_ids=[],
                properties={"repository": self.snapshot.name},
            )
        )

        container_of: dict[str, str] = {}
        for node in by_level.get("system", []):
            member_files = [
                f for m in node.members for f in _files_under(self._paths(m), self.snapshot)
            ]
            technology = ", ".join(_languages(member_files, self.snapshot, 2))
            if any(m in uses_qt for m in node.members):
                technology = f"{technology} / Qt" if technology else "Qt"
            self.add(
                Element(
                    node.id,
                    node.label,
                    "container",
                    node.description,
                    parent=SYSTEM_ID,
                    paths=node.paths,
                    b_ids=[node.id],
                    properties={"technology": technology, "kind": node.kind},
                )
            )
            self.relate(_relation("contains", SYSTEM_ID, node.id))
            for member in node.members:
                container_of[member] = node.id

        for node in by_level.get("component", []):
            files = _files_under(node.paths, self.snapshot)
            technology = ", ".join(_languages(files, self.snapshot, 1))
            if node.id in uses_qt:
                technology = f"{technology} / Qt" if technology else "Qt"
            kind = "test" if node.kind == "test" else "component"
            parent = container_of.get(node.id)
            self.add(
                Element(
                    node.id,
                    node.label,
                    kind,
                    node.description,
                    parent=parent,
                    paths=node.paths,
                    b_ids=[node.id],
                    properties={
                        "technology": technology,
                        "layer": node.layer or "",
                        "files": str(node.metrics.get("files", 0)),
                        "lines": str(node.metrics.get("lines", 0)),
                        "origin": node.origin,
                    },
                )
            )
            if parent:
                self.relate(_relation("contains", parent, node.id))
            if node.group:
                layer_id = node.group
                if layer_id not in self.elements:
                    group = next((g for g in self.model.groups if g.id == layer_id), None)
                    self.add(Element(layer_id, group.label if group else layer_id, "layer"))
                self.relate(_relation("contains", layer_id, node.id))

        for node in by_level.get("class", []):
            self.add(
                Element(
                    node.id,
                    node.label,
                    "interface" if node.kind == "interface" else "class",
                    node.description,
                    parent=node.group,
                    paths=node.paths,
                    b_ids=[node.id],
                    properties={"kind": node.kind},
                )
            )

        for node in by_level.get("external", []):
            self.add(
                Element(
                    node.id,
                    node.label,
                    "external",
                    "Library or platform the code includes.",
                    b_ids=[node.id],
                    properties={"technology": _FRAMEWORKS.get(node.label, "")},
                )
            )

        protocols = {m.protocol for m in self.model.messages}
        for node in by_level.get("actor", []):
            self.add(Element(node.id, node.label, "device", node.description, b_ids=[node.id]))
            if protocols & {"can", "canopen"}:
                self.add(
                    Element(
                        BUS_ID,
                        "CAN bus",
                        "bus",
                        "Bus the device is reached through (from protocol patterns).",
                        properties={"protocols": ", ".join(sorted(protocols))},
                    )
                )
                self.relate(_relation("interacts", node.id, BUS_ID, description="connected to"))

        for message in self.model.messages:
            properties = {"protocol": message.protocol}
            if message.message_id is not None:
                properties["id"] = f"0x{message.message_id:X}"
            self.add(
                Element(
                    message.id,
                    message.name,
                    "message",
                    b_ids=[message.id],
                    paths=[message.file] if message.file else [],
                    properties=properties,
                )
            )

        for edge in self.model.edges:
            if edge.source not in self.elements or edge.target not in self.elements:
                continue
            base: dict[str, Any] = dict(
                weight=edge.weight,
                confidence=edge.confidence,
                b_edge_ids=[edge.id],
                evidence=_evidence(edge),
            )
            if edge.kind in ("depends", "uses"):
                self.relate(
                    _relation(
                        "depends",
                        edge.source,
                        edge.target,
                        description=edge.label or f"{edge.weight} static dependencies",
                        technology="#include / C++ types",
                        **base,
                    )
                )
            elif edge.kind == "message":
                protocol = next(
                    (m.protocol for m in self.model.messages if m.name == edge.label), ""
                )
                self.relate(
                    _relation(
                        "flows",
                        edge.source,
                        edge.target,
                        description=edge.label or "messages",
                        technology=protocol or "function calls (pattern)",
                        **base,
                    )
                )
            elif edge.kind == "test":
                self.relate(
                    _relation("verifies", edge.source, edge.target, description="tests", **base)
                )
            elif edge.kind in _CLASS_RELATIONS:
                self.relate(
                    _relation(
                        _CLASS_RELATIONS[edge.kind],
                        edge.source,
                        edge.target,
                        description=edge.kind,
                        **base,
                    )
                )

        for kind, parties in (
            ("person", self.config.people),
            ("external_system", self.config.external_systems),
        ):
            for party in parties:
                self._party(kind, party)

    def _paths(self, node_id: str) -> list[str]:
        node = self.model.node(node_id)
        return node.paths if node else []

    def resolve(self, name: str) -> str | None:
        if name in self.elements:
            return name
        lowered = name.lower()
        for element in self.elements.values():
            if element.name.lower() == lowered:
                return element.id
        for element in self.elements.values():
            if name in element.paths:
                return element.id
        return None

    def _party(self, kind: str, party: Party) -> None:
        element_id = snake_id(party.name, "person_" if kind == "person" else "extsys_")
        self.add(
            Element(
                element_id,
                party.name,
                kind,
                party.description,
                properties={"declared": "archdiag.toml"},
            )
        )
        for target in party.uses:
            resolved = self.resolve(target)
            if resolved is None:
                self.issues.append(
                    f"[c4] {party.name} uses '{target}', which matches no "
                    "container, component or path."
                )
                continue
            self.relate(
                _relation("interacts", element_id, resolved, description=party.label or "uses")
            )


def build_ad(model: ArchModelV2, snapshot: RepoSnapshot, config: ADConfig, version: str) -> AD:
    builder = _Builder(model, snapshot, config)
    builder.build()
    concerns = (
        [Concern(c.id, c.text) for c in config.concerns]
        if config.concerns
        else list(DEFAULT_CONCERNS)
    )
    stakeholders = (
        [Stakeholder(s.id, s.name, list(s.concerns)) for s in config.stakeholders]
        if config.stakeholders
        else list(DEFAULT_STAKEHOLDERS)
    )
    names = {e.name: e.id for e in builder.elements.values()}
    decisions = collect_decisions(snapshot.root, config, names)
    inconsistencies = list(builder.issues)
    names_by_id = {e.id: e.name for e in builder.elements.values()}
    for violation in model.violations:
        target = (
            f" → {names_by_id.get(violation.target, violation.target)}" if violation.target else ""
        )
        where = ", ".join(f"{e.file}:{e.line}" for e in violation.evidence[:3])
        inconsistencies.append(
            f"[{violation.rule}] "
            f"{names_by_id.get(violation.source, violation.source)}{target}: "
            f"{violation.message}" + (f" ({where})" if where else "")
        )
    return AD(
        system_name=model.title,
        version=version,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        tool_version=__version__,
        overview=model.explanation,
        concerns=concerns,
        stakeholders=stakeholders,
        viewpoints=list(VIEWPOINTS),
        elements=builder.elements,
        relations=sorted(builder.relations.values(), key=lambda r: r.id),
        decisions=decisions,
        inconsistencies=inconsistencies,
    )


def relative_to(path: str, base: str) -> str:
    return posixpath.relpath(path, base) if base else path
