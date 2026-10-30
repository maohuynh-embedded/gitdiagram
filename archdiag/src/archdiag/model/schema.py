"""ArchModel: the contract every stage agrees on."""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Literal[1] = 1
MAX_NODES = 60
MAX_EDGES = 120
MAX_GROUPS = 20
ID_PATTERN = r"^[a-z][a-z0-9_]{0,47}$"


class _Base(BaseModel):
    # Extra keys from an LLM are ignored rather than rejected: they cost a retry for nothing.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Group(_Base):
    id: str
    label: str
    parent: str | None = None


class Node(_Base):
    id: str
    label: str
    kind: str
    group: str | None = None
    paths: list[str] = Field(default_factory=list)
    description: str = ""


class Edge(_Base):
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    label: str | None = None
    kind: str = "uses"


class ArchModel(_Base):
    schema_version: Literal[1] = SCHEMA_VERSION
    title: str
    groups: list[Group] = Field(default_factory=list)
    nodes: list[Node]
    edges: list[Edge] = Field(default_factory=list)
    explanation: str

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2)


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return _inline_refs(copy.deepcopy(defs[ref.removeprefix("#/$defs/")]), defs)
        return {key: _inline_refs(value, defs) for key, value in node.items() if key != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def llm_json_schema() -> dict[str, Any]:
    """JSON Schema for constrained decoding; local runtimes handle inline schemas best."""
    schema = ArchModel.model_json_schema(by_alias=True)
    schema["properties"].pop("schema_version", None)
    inlined: dict[str, Any] = _inline_refs(schema, schema.get("$defs", {}))
    return inlined
