"""ArchModel v2: the static-analysis model with evidence, messages, violations and views.

v1 (LLM-only) models are upgraded with upgrade_v1 so every consumer reads one format.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from archdiag.model.schema import ArchModel, Group, _Base

SCHEMA_VERSION_V2: Literal[2] = 2
MAX_VIEW_NODES = 60
MAX_MODEL_NODES = 2000
MAX_MODEL_EDGES = 10000
MAX_EVIDENCE = 20

Level = Literal["system", "layer", "component", "class", "external", "actor"]


class Evidence(_Base):
    file: str
    line: int
    fact: str
    source: str
    confidence: str
    detail: str = ""


class NodeV2(_Base):
    id: str
    label: str
    kind: str
    level: Level = "component"
    group: str | None = None
    layer: str | None = None
    origin: str = "inferred"
    paths: list[str] = Field(default_factory=list)
    description: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    metrics: dict[str, int] = Field(default_factory=dict)


class EdgeV2(_Base):
    id: str
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    label: str | None = None
    kind: str = "depends"
    weight: int = 1
    confidence: str = "medium"
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_total: int = 0


class MessageV2(_Base):
    id: str
    protocol: str
    name: str
    message_id: int | None = None
    senders: list[str] = Field(default_factory=list)
    receivers: list[str] = Field(default_factory=list)
    confidence: str = "medium"
    file: str | None = None
    line: int | None = None


class Violation(_Base):
    rule: str
    source: str = Field(alias="from")
    target: str | None = Field(default=None, alias="to")
    message: str
    edge_id: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_total: int = 0


class View(_Base):
    id: str
    title: str
    level: str
    description: str = ""
    node_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)
    collapsed_from: str | None = None
    parent: str | None = None


class ArchModelV2(_Base):
    schema_version: Literal[2] = SCHEMA_VERSION_V2
    title: str
    mode: str = "static"
    groups: list[Group] = Field(default_factory=list)
    nodes: list[NodeV2] = Field(default_factory=list)
    edges: list[EdgeV2] = Field(default_factory=list)
    messages: list[MessageV2] = Field(default_factory=list)
    violations: list[Violation] = Field(default_factory=list)
    views: list[View] = Field(default_factory=list)
    explanation: str = ""

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2)

    def node(self, node_id: str) -> NodeV2 | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def view(self, view_id: str) -> View | None:
        return next((v for v in self.views if v.id == view_id), None)


def upgrade_v1(model: ArchModel) -> ArchModelV2:
    nodes = [
        NodeV2(id=n.id, label=n.label, kind=n.kind, group=n.group, paths=n.paths,
               description=n.description, origin="llm")
        for n in model.nodes
    ]
    edges = [
        EdgeV2.model_validate({
            "id": f"e{index}", "from": e.source, "to": e.target, "label": e.label,
            "kind": e.kind, "confidence": "low",
        })
        for index, e in enumerate(model.edges)
    ]
    view = View(id="overview", title="Overview", level="component",
                node_ids=[n.id for n in nodes], edge_ids=[e.id for e in edges])
    return ArchModelV2(title=model.title, mode="llm", groups=model.groups, nodes=nodes,
                       edges=edges, views=[view], explanation=model.explanation)


def load_model(text: str) -> ArchModelV2:
    """Read model.json of either schema version."""
    import json

    data = json.loads(text)
    if data.get("schema_version") == 2:
        return ArchModelV2.model_validate(data)
    return upgrade_v1(ArchModel.model_validate(data))
