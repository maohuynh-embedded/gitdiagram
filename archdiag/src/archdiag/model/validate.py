"""Check LLM output against the schema, the model rules and the real repository."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import ValidationError

from archdiag.model.schema import (
    ID_PATTERN,
    MAX_EDGES,
    MAX_GROUPS,
    MAX_NODES,
    ArchModel,
    Edge,
)

_ID_RE = re.compile(ID_PATTERN)
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.DOTALL)
_MAX_REPORTED = 25


@dataclass(frozen=True)
class ValidationResult:
    model: ArchModel | None
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.model is not None and not self.errors


def normalize_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.strip("/")


def parse_model(raw: str) -> tuple[ArchModel | None, list[str]]:
    match = _FENCE_RE.match(raw)
    text = match.group(1) if match else raw
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [
            f"Output is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}."
        ]
    try:
        return ArchModel.model_validate(data), []
    except ValidationError as exc:
        errors = []
        for err in exc.errors()[:_MAX_REPORTED]:
            location = ".".join(str(part) for part in err["loc"]) or "<root>"
            errors.append(f"Schema error at {location}: {err['msg']}.")
        return None, errors


def _check_ids(kind: str, ids: Iterable[str], seen: set[str], errors: list[str]) -> None:
    for item_id in ids:
        if not _ID_RE.match(item_id):
            errors.append(f"{kind} has invalid id '{item_id}'; use lowercase snake_case.")
        if item_id in seen:
            errors.append(f"duplicate id '{item_id}'; ids must be unique across groups and nodes.")
        seen.add(item_id)


def check_structure(model: ArchModel) -> list[str]:
    errors: list[str] = []
    if len(model.nodes) > MAX_NODES:
        errors.append(f"too many nodes ({len(model.nodes)}); the limit is {MAX_NODES}.")
    if len(model.edges) > MAX_EDGES:
        errors.append(f"too many edges ({len(model.edges)}); the limit is {MAX_EDGES}.")
    if len(model.groups) > MAX_GROUPS:
        errors.append(f"too many groups ({len(model.groups)}); the limit is {MAX_GROUPS}.")
    if not model.nodes:
        errors.append("the model has no nodes.")

    seen: set[str] = set()
    _check_ids("Group", (g.id for g in model.groups), seen, errors)
    _check_ids("Node", (n.id for n in model.nodes), seen, errors)

    group_ids = {g.id for g in model.groups}
    node_ids = {n.id for n in model.nodes}
    parents = {g.id: g.parent for g in model.groups}

    for group in model.groups:
        if not group.label.strip():
            errors.append(f"Group '{group.id}' has an empty label.")
        if group.parent is not None and group.parent not in group_ids:
            errors.append(f"Group '{group.id}' has unknown parent '{group.parent}'.")
    for group in model.groups:
        visited: set[str] = set()
        current: str | None = group.id
        while current is not None and current in parents:
            if current in visited:
                errors.append(f"Group '{group.id}' is part of a parent cycle.")
                break
            visited.add(current)
            current = parents[current]

    for node in model.nodes:
        if not node.label.strip():
            errors.append(f"Node '{node.id}' has an empty label.")
        if node.group is not None and node.group not in group_ids:
            errors.append(f"Node '{node.id}' references unknown group '{node.group}'.")

    for index, edge in enumerate(model.edges):
        for end in (edge.source, edge.target):
            if end not in node_ids:
                errors.append(
                    f"Edge {index} ({edge.source} -> {edge.target}) references "
                    f"unknown node '{end}'."
                )
        if edge.source == edge.target:
            errors.append(f"Edge {index} points to itself ('{edge.source}').")
    return errors[: _MAX_REPORTED * 2]


def _path_problems(model: ArchModel, known_paths: frozenset[str]) -> list[str]:
    return [
        f"Node '{node.id}' path '{path}' does not exist in the repository."
        for node in model.nodes
        for path in node.paths
        if normalize_path(path) not in known_paths
    ]


def _repair_paths(model: ArchModel, known_paths: frozenset[str]) -> ArchModel:
    nodes = []
    for node in model.nodes:
        kept: list[str] = []
        for path in node.paths:
            normalized = normalize_path(path)
            if normalized in known_paths and normalized not in kept:
                kept.append(normalized)
        nodes.append(node.model_copy(update={"paths": kept}))
    return model.model_copy(update={"nodes": nodes})


def _dedupe_edges(model: ArchModel) -> tuple[ArchModel, list[str]]:
    seen: set[tuple[str, str, str | None]] = set()
    kept: list[Edge] = []
    warnings: list[str] = []
    for edge in model.edges:
        key = (edge.source, edge.target, edge.label)
        if key in seen:
            warnings.append(f"Removed duplicate edge {edge.source} -> {edge.target}.")
            continue
        seen.add(key)
        kept.append(edge)
    return model.model_copy(update={"edges": kept}), warnings


def validate_raw(raw: str, known_paths: frozenset[str]) -> ValidationResult:
    model, errors = parse_model(raw)
    if model is None:
        return ValidationResult(None, tuple(errors), ())
    errors = check_structure(model)
    path_problems = _path_problems(model, known_paths)
    if errors:
        return ValidationResult(None, tuple(errors + path_problems), ())
    # A path only drives a node's file link, so a bad one is dropped instead of retried.
    model = _repair_paths(model, known_paths)
    model, edge_warnings = _dedupe_edges(model)
    warnings = [f"Removed unknown path: {problem}" for problem in path_problems]
    return ValidationResult(model, (), tuple(warnings + edge_warnings))
