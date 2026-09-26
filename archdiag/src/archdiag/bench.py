"""Score an ArchModel against a hand-written answer file."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pathspec

from archdiag.config import ConfigError
from archdiag.model.schema import ArchModel, Node
from archdiag.model.validate import normalize_path

_DIR_PROBE = "__archdiag_probe__"


@dataclass(frozen=True)
class ExpectedComponent:
    name: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Expected:
    components: tuple[ExpectedComponent, ...]
    relations: tuple[tuple[str, str], ...]
    forbidden: tuple[str, ...]


@dataclass
class BenchReport:
    component_recall: float
    relation_recall: float
    path_validity: float
    junk_nodes: list[str] = field(default_factory=list)
    found_components: list[str] = field(default_factory=list)
    missing_components: list[str] = field(default_factory=list)
    found_relations: list[tuple[str, str]] = field(default_factory=list)
    missing_relations: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value) or not value:
        raise ConfigError(f"{where} must be a non-empty list of strings")
    return tuple(value)


def load_expected(path: Path) -> Expected:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    components = []
    for index, item in enumerate(data.get("components", [])):
        name = item.get("name")
        if not isinstance(name, str):
            raise ConfigError(f"{path}: components[{index}] needs a name")
        components.append(
            ExpectedComponent(name, _string_list(item.get("paths"), f"{path}: {name}.paths"))
        )
    names = {c.name for c in components}
    relations = []
    for item in data.get("relations", []):
        pair = (item.get("from"), item.get("to"))
        for end in pair:
            if end not in names:
                raise ConfigError(f"{path}: relation references unknown component '{end}'")
        relations.append((str(pair[0]), str(pair[1])))
    forbidden = tuple(data.get("forbidden", []))
    return Expected(tuple(components), tuple(relations), forbidden)


def _matches(spec: pathspec.GitIgnoreSpec, path: str) -> bool:
    return spec.match_file(path) or spec.match_file(f"{path}/{_DIR_PROBE}")


def _node_components(node: Node, specs: dict[str, pathspec.GitIgnoreSpec]) -> set[str]:
    paths = [normalize_path(p) for p in node.paths]
    return {name for name, spec in specs.items() if any(_matches(spec, p) for p in paths)}


def score(model: ArchModel, expected: Expected, known_paths: frozenset[str]) -> BenchReport:
    specs = {
        c.name: pathspec.GitIgnoreSpec.from_lines(c.paths) for c in expected.components
    }
    by_node = {node.id: _node_components(node, specs) for node in model.nodes}
    found = sorted({name for names in by_node.values() for name in names})
    missing = [c.name for c in expected.components if c.name not in found]

    found_relations: list[tuple[str, str]] = []
    missing_relations: list[tuple[str, str]] = []
    for source, target in expected.relations:
        hit = any(
            source in by_node.get(edge.source, set()) and target in by_node.get(edge.target, set())
            for edge in model.edges
        )
        (found_relations if hit else missing_relations).append((source, target))

    all_paths = [normalize_path(p) for node in model.nodes for p in node.paths]
    valid = sum(1 for p in all_paths if p in known_paths)
    forbidden = pathspec.GitIgnoreSpec.from_lines(expected.forbidden)
    junk = [
        node.id for node in model.nodes
        if any(_matches(forbidden, normalize_path(p)) for p in node.paths)
    ]

    def ratio(hits: int, total: int) -> float:
        return hits / total if total else 1.0

    return BenchReport(
        component_recall=ratio(len(found), len(expected.components)),
        relation_recall=ratio(len(found_relations), len(expected.relations)),
        path_validity=valid / len(all_paths) if all_paths else 0.0,
        junk_nodes=junk,
        found_components=found,
        missing_components=missing,
        found_relations=found_relations,
        missing_relations=missing_relations,
    )


def format_report(report: BenchReport) -> str:
    rows = [
        ("component recall", f"{report.component_recall:.1%}"),
        ("relation recall", f"{report.relation_recall:.1%}"),
        ("path validity", f"{report.path_validity:.1%}"),
        ("junk nodes", str(len(report.junk_nodes))),
    ]
    width = max(len(name) for name, _ in rows)
    lines = [f"{name.ljust(width)}  {value}" for name, value in rows]
    if report.missing_components:
        lines.append(f"missing components: {', '.join(report.missing_components)}")
    if report.missing_relations:
        pairs = ", ".join(f"{a} -> {b}" for a, b in report.missing_relations)
        lines.append(f"missing relations: {pairs}")
    return "\n".join(lines)
