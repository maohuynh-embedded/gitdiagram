"""ISO/IEC/IEEE 42010 architecture description metamodel, notation-neutral.

Every element and relation keeps the ids of the ArchModel v2 nodes and edges it came from
(`b_ids`, `b_edge_ids`), so any view traces back to file:line evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

ELEMENT_TYPES = frozenset(
    {
        "system",
        "container",
        "component",
        "class",
        "interface",
        "message",
        "actor",
        "external",
        "external_system",
        "person",
        "layer",
        "requirement",
        "test",
        "device",
        "bus",
    }
)
RELATION_TYPES = frozenset(
    {
        "depends",
        "realizes",
        "composes",
        "specializes",
        "flows",
        "allocated",
        "verifies",
        "satisfies",
        "contains",
        "interacts",
    }
)


@dataclass
class Element:
    id: str
    name: str
    type: str
    description: str = ""
    parent: str | None = None
    paths: list[str] = field(default_factory=list)
    b_ids: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class Relation:
    id: str
    type: str
    source: str
    target: str
    description: str = ""
    technology: str = ""
    weight: int = 1
    confidence: str = "medium"
    b_edge_ids: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)  # "file:line" samples


@dataclass
class Concern:
    id: str
    text: str


@dataclass
class Stakeholder:
    id: str
    name: str
    concern_ids: list[str]


@dataclass
class Viewpoint:
    id: str
    name: str
    stage: str
    notation: str
    concern_ids: list[str]
    model_kinds: list[str]
    element_types: list[str]
    relation_types: list[str]
    correspondence_rules: list[str] = field(default_factory=list)


@dataclass
class ADView:
    id: str
    viewpoint_id: str
    title: str
    description: str = ""
    element_ids: list[str] = field(default_factory=list)
    relation_ids: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    mermaid: str = ""  # embedded in the document when set
    issues: list[str] = field(default_factory=list)


@dataclass
class Correspondence:
    rule_id: str
    description: str
    status: str  # ok | violated | not-applicable
    detail: str = ""
    element_ids: list[str] = field(default_factory=list)


@dataclass
class Decision:
    id: str
    title: str
    status: str = "accepted"
    rationale: str = ""
    affected: list[str] = field(default_factory=list)
    source: str = "archdiag.toml"


@dataclass
class AD:
    system_name: str
    version: str
    created_at: str
    tool_version: str
    overview: str
    concerns: list[Concern]
    stakeholders: list[Stakeholder]
    viewpoints: list[Viewpoint]
    elements: dict[str, Element]
    relations: list[Relation]
    views: list[ADView] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    correspondences: list[Correspondence] = field(default_factory=list)
    inconsistencies: list[str] = field(default_factory=list)

    def of_type(self, *types: str) -> list[Element]:
        return [e for e in self.elements.values() if e.type in types]

    def children(self, parent: str, *types: str) -> list[Element]:
        return [
            e
            for e in self.elements.values()
            if e.parent == parent and (not types or e.type in types)
        ]

    def relations_of(self, *types: str) -> list[Relation]:
        return [r for r in self.relations if r.type in types]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["elements"] = list(data["elements"].values())
        return data
