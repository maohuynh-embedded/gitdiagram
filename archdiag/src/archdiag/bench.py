"""Score an ArchModel against a hand-written answer file."""

from __future__ import annotations

import tomllib
from collections.abc import Collection, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pathspec

from archdiag.config import ConfigError
from archdiag.model.schema_v2 import ArchModelV2
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


MAJORITY = 0.5  # a node "holds" a component when it contains at least half of its files
INSIDE = 0.8  # a node "sits inside" a component when most of its own files belong to it


def _files_under(paths: Iterable[str], files: Collection[str]) -> set[str]:
    out: set[str] = set()
    for raw in paths:
        path = normalize_path(raw)
        prefix = f"{path}/"
        out.update(f for f in files if f == path or f.startswith(prefix))
    return out


def score(model: ArchModelV2, expected: Expected, known_paths: frozenset[str],
          files: Collection[str]) -> BenchReport:
    """Compare by file sets, so a coarser or finer split of the same code is still credited."""
    specs = {c.name: pathspec.GitIgnoreSpec.from_lines(c.paths) for c in expected.components}
    component_files = {
        name: {f for f in files if _matches(spec, f)} for name, spec in specs.items()
    }
    nodes = [n for n in model.nodes if n.level in ("component", "layer")]
    node_files = {n.id: _files_under(n.paths, files) for n in nodes}

    membership: dict[str, set[str]] = {n.id: set() for n in nodes}
    found: list[str] = []
    for name, wanted in component_files.items():
        if not wanted:
            continue
        holders = [nid for nid, owned in node_files.items()
                   if owned and len(owned & wanted) >= MAJORITY * len(wanted)]
        inside = [nid for nid, owned in node_files.items()
                  if owned and len(owned & wanted) >= INSIDE * len(owned)]
        covered = len(set().union(*(node_files[n] & wanted for n in inside))) if inside else 0
        for nid in {*holders, *inside}:
            membership[nid].add(name)
        if holders or covered >= MAJORITY * len(wanted):
            found.append(name)
    found.sort()
    missing = [c.name for c in expected.components if c.name not in found]

    found_relations: list[tuple[str, str]] = []
    missing_relations: list[tuple[str, str]] = []
    for source, target in expected.relations:
        hit = any(
            edge.source != edge.target
            and source in membership.get(edge.source, set())
            and target in membership.get(edge.target, set())
            for edge in model.edges
        )
        (found_relations if hit else missing_relations).append((source, target))

    all_paths = [normalize_path(p) for node in nodes for p in node.paths]
    valid = sum(1 for p in all_paths if p in known_paths)
    forbidden = pathspec.GitIgnoreSpec.from_lines(expected.forbidden)
    junk = [
        node.id for node in nodes
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
