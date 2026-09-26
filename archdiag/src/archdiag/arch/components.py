"""Group files into components (L2) and layers (L1), inferred or declared."""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

import pathspec

from archdiag.arch.config import ArchitectureConfig

ROOT = "root"
UNASSIGNED = "unassigned"
UNLAYERED = "unlayered"
_TEST_SEGMENTS = frozenset(
    {
        "test",
        "tests",
        "testing",
        "unittest",
        "unittests",
        "unit_tests",
        "ut",
        "it",
        "ft",
        "spec",
        "specs",
    }
)
_TEST_NAME = re.compile(r"(^tests?_|_tests?$|^tst_)", re.I)
_PROBE = "__archdiag_probe__"


@dataclass(frozen=True)
class Component:
    id: str
    name: str
    paths: tuple[str, ...]
    files: tuple[str, ...]
    layer: str | None
    kind: str  # module | test | root | unassigned
    origin: str  # inferred | declared


def snake_id(name: str, prefix: str = "") -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "x"
    candidate = f"{prefix}{base}"
    if not candidate[0].isalpha():
        candidate = f"c_{candidate}"
    if len(candidate) > 48:
        digest = hashlib.sha1(candidate.encode()).hexdigest()[:8]
        candidate = f"{candidate[:39]}_{digest}"
    return candidate


def unique_ids(names: Iterable[str], prefix: str = "") -> list[str]:
    seen: set[str] = set()
    out = []
    for name in names:
        candidate = snake_id(name, prefix)
        base, counter = candidate, 2
        while candidate in seen:
            suffix = f"_{counter}"
            candidate = f"{base[: 48 - len(suffix)]}{suffix}"
            counter += 1
        seen.add(candidate)
        out.append(candidate)
    return out


def is_test_path(path: str) -> bool:
    for segment in path.split("/")[:-1] or [path]:
        if segment.lower() in _TEST_SEGMENTS or _TEST_NAME.search(segment):
            return True
    return False


def _children(files: Sequence[str], directory: str) -> tuple[list[str], dict[str, list[str]]]:
    prefix = f"{directory}/" if directory else ""
    direct: list[str] = []
    sub: dict[str, list[str]] = defaultdict(list)
    for path in files:
        rest = path[len(prefix) :]
        if "/" in rest:
            sub[prefix + rest.split("/", 1)[0]].append(path)
        else:
            direct.append(path)
    return direct, sub


def _split(
    directory: str, files: list[str], max_files: int, atomic: Collection[str]
) -> list[tuple[str, list[str]]]:
    if directory in atomic:
        return [(directory, files)]
    direct, sub = _children(files, directory)
    # A directory that mostly holds sub-packages (e.g. infrastructure/{can,serial}) is a
    # container, not a component, even when it is small.
    container = len(direct) <= 2 and sum(1 for s in sub.values() if len(s) >= 2) >= 2
    if not sub or (len(files) <= max_files and not container):
        return [(directory, files)]
    parts: list[tuple[str, list[str]]] = []
    for child in sorted(sub):
        parts.extend(_split(child, sub[child], max_files, atomic))
    if direct:
        parts.append((directory, direct))
    return parts


def _merge_tiny(parts: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    by_dir = {d: list(f) for d, f in parts}
    for directory in sorted(by_dir, key=lambda d: -d.count("/")):
        if len(by_dir.get(directory, [])) >= 2:
            continue
        parent = posixpath.dirname(directory)
        while parent and parent not in by_dir:
            parent = posixpath.dirname(parent)
        if parent and parent in by_dir and directory in by_dir:
            by_dir[parent].extend(by_dir.pop(directory))
    return sorted(by_dir.items())


def infer_components(
    files: Sequence[str], atomic_dirs: Collection[str], max_files: int
) -> list[Component]:
    root_files, top = _children(sorted(files), "")
    parts: list[tuple[str, list[str]]] = []
    for directory in sorted(top):
        parts.extend(_split(directory, top[directory], max_files, atomic_dirs))
    parts = _merge_tiny(parts)
    if root_files:
        parts.insert(0, ("", root_files))
    names = [d or ROOT for d, _ in parts]
    ids = unique_ids(names)
    return [
        Component(
            cid,
            name,
            (directory,) if directory else (),
            tuple(sorted(members)),
            None,
            ROOT if not directory else ("test" if is_test_path(f"{directory}/x") else "module"),
            "inferred",
        )
        for cid, name, (directory, members) in zip(ids, names, parts, strict=True)
    ]


def _matches(spec: pathspec.GitIgnoreSpec, path: str) -> bool:
    return spec.match_file(path) or spec.match_file(f"{path}/{_PROBE}")


def declared_components(files: Sequence[str], config: ArchitectureConfig) -> list[Component]:
    specs = [(c, pathspec.GitIgnoreSpec.from_lines(c.paths)) for c in config.components]
    members: dict[str, list[str]] = defaultdict(list)
    for path in files:
        owner = next((c.name for c, spec in specs if _matches(spec, path)), UNASSIGNED)
        members[owner].append(path)
    names = [c.name for c in config.components] + ([UNASSIGNED] if members.get(UNASSIGNED) else [])
    ids = unique_ids(names)
    layers = {c.name: c.layer for c in config.components}
    out = []
    for cid, name in zip(ids, names, strict=True):
        declared = next((c for c in config.components if c.name == name), None)
        kind = (
            UNASSIGNED
            if name == UNASSIGNED
            else (
                "test"
                if members[name] and all(is_test_path(p) for p in members[name])
                else "module"
            )
        )
        out.append(
            Component(
                cid,
                name,
                declared.paths if declared else (),
                tuple(sorted(members[name])),
                layers.get(name),
                kind,
                "declared" if declared else "inferred",
            )
        )
    return out


def assign_layers(components: Sequence[Component], config: ArchitectureConfig) -> list[Component]:
    out = []
    for component in components:
        if component.layer:
            out.append(component)
            continue
        first = component.files[0].split("/")[0] if component.files else ""
        top = component.name.split("/")[0] if component.origin == "inferred" else first
        if component.kind == ROOT:
            layer = ROOT
        elif config.layers:
            layer = top if top in config.layers else UNLAYERED
        else:
            layer = top or ROOT
        out.append(replace(component, layer=layer))
    return out


def build_components(
    files: Sequence[str], atomic_dirs: Collection[str], config: ArchitectureConfig
) -> list[Component]:
    if config.components:
        components = declared_components(files, config)
    else:
        components = infer_components(files, atomic_dirs, config.max_component_files)
    return assign_layers(components, config)


def target_dirs(targets: Iterable[Mapping[str, object]], total_files: int) -> set[str]:
    """Directories that own a library, or a small target, stay one component.

    An executable that spans most of the tree (the main application) says nothing about
    internal boundaries, so it never makes its directory atomic.
    """
    dirs: set[str] = set()
    for target in targets:
        sources = [str(s) for s in target.get("sources", []) or []]  # type: ignore[attr-defined]
        folders = {posixpath.dirname(s) for s in sources}
        if not folders or not all(folders):
            continue
        is_library = target.get("kind") == "library"
        if not is_library and len(sources) * 2 >= max(total_files, 1):
            continue
        common = posixpath.commonpath(sorted(folders))
        if common:
            dirs.add(common)
    return dirs
