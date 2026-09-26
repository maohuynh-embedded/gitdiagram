"""libclang extractor: exact facts when the project provides compile_commands.json.

Optional. When libclang is missing, or a translation unit fails fatally, tree-sitter facts
stay in place for those files.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from archdiag.facts.model import (
    CLANG,
    FIELD_TYPE,
    INCLUDE,
    INHERITS,
    INTERFACE,
    SYMBOL,
    Confidence,
    Fact,
)
from archdiag.facts.resolve import external_group

EXTRACTOR_VERSION = "clang-1"
_DB_NAME = "compile_commands.json"
_DROP_FLAGS = {"-c", "-MD", "-MMD", "-MP"}
_DROP_WITH_VALUE = {"-o", "-MF", "-MT", "-MQ"}


@dataclass(frozen=True)
class CompileEntry:
    file: str  # repository-relative
    directory: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class ClangSetup:
    library: str | None
    extra_args: tuple[str, ...]


def find_compile_db(root: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    candidates = [root / _DB_NAME]
    for pattern in ("build*", "cmake-build-*", "out/build/*"):
        candidates.extend(sorted(root.glob(f"{pattern}/{_DB_NAME}")))
    return next((c for c in candidates if c.is_file()), None)


def _clean_args(raw: list[str], source: str) -> tuple[str, ...]:
    args: list[str] = []
    skip = False
    for index, arg in enumerate(raw):
        if index == 0 or skip:
            skip = False
            continue
        if arg in _DROP_WITH_VALUE:
            skip = True
            continue
        if arg in _DROP_FLAGS or arg == source or arg.endswith(os.sep + Path(source).name):
            continue
        args.append(arg)
    return tuple(args)


def load_compile_db(path: Path, root: Path, repo_files: Iterable[str]) -> list[CompileEntry]:
    known = set(repo_files)
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[CompileEntry] = []
    seen: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        directory = Path(entry.get("directory", root))
        file_path = Path(entry.get("file", ""))
        absolute = (file_path if file_path.is_absolute() else directory / file_path).resolve()
        try:
            rel = absolute.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel not in known or rel in seen:
            continue
        raw = entry.get("arguments") or shlex.split(entry.get("command", ""))
        seen.add(rel)
        out.append(CompileEntry(rel, str(directory), _clean_args(list(raw), entry.get("file", ""))))
    return out


@cache
def clang_setup() -> ClangSetup | None:
    """Prefer the libclang that belongs to the clang on PATH, with its builtin headers."""
    try:
        import clang.cindex as ci
    except ImportError:
        return None
    library: str | None = None
    extra: list[str] = []
    clang_bin = shutil.which("clang")
    if clang_bin:
        try:
            resource = subprocess.run(
                [clang_bin, "-print-resource-dir"], capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            resource = ""
        if resource and Path(resource).is_dir():
            libdir = Path(resource).parents[1]
            for name in ("libclang.dylib", "libclang.so", "libclang.dll"):
                if (libdir / name).is_file():
                    library = str(libdir / name)
                    extra += ["-resource-dir", resource]
                    break
    if platform.system() == "Darwin" and shutil.which("xcrun"):
        try:
            sdk = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True, text=True,
                                 timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            sdk = ""
        if sdk:
            extra += ["-isysroot", sdk]
    try:
        if library and not ci.Config.loaded:
            ci.Config.set_library_file(library)
        ci.Index.create()
    except Exception:  # libclang could not be loaded at all
        return None
    return ClangSetup(library, tuple(extra))


def _qualified(cursor: Any) -> str:
    import clang.cindex as ci

    parts: list[str] = []
    current = cursor
    while current is not None and current.kind != ci.CursorKind.TRANSLATION_UNIT:
        if current.spelling:
            parts.append(current.spelling)
        current = current.semantic_parent
    return "::".join(reversed(parts))


def _type_names(type_: Any) -> list[str]:
    import clang.cindex as ci

    names: list[str] = []
    pending = [type_]
    while pending and len(names) < 8:
        current = pending.pop(0)
        while current.kind in (ci.TypeKind.POINTER, ci.TypeKind.LVALUEREFERENCE,
                               ci.TypeKind.RVALUEREFERENCE):
            current = current.get_pointee()
        declaration = current.get_declaration()
        if declaration.kind != ci.CursorKind.NO_DECL_FOUND:
            qualified = _qualified(declaration)
            if qualified and not qualified.startswith("std::"):
                names.append(qualified)
        for index in range(max(current.get_num_template_arguments(), 0)):
            pending.append(current.get_template_argument_type(index))
    return list(dict.fromkeys(names))


def extract_tu(root: str, entry: CompileEntry, repo_files: frozenset[str]
               ) -> tuple[list[Fact], list[str], str | None]:
    """Returns (facts, repository files covered, error)."""
    import clang.cindex as ci

    setup = clang_setup()
    if setup is None:
        return [], [], "libclang is not available"
    base = Path(root)

    def rel(name: str | None) -> str | None:
        if not name:
            return None
        path = Path(name)
        if not path.is_absolute():
            path = Path(entry.directory) / path
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            return None

    index = ci.Index.create()
    options = (ci.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES
               | ci.TranslationUnit.PARSE_INCOMPLETE)
    cwd = os.getcwd()
    try:
        os.chdir(entry.directory)
        tu = index.parse(str(base / entry.file), args=[*entry.args, *setup.extra_args],
                         options=options)
    except (ci.TranslationUnitLoadError, OSError) as exc:
        return [], [], f"{entry.file}: {exc}"
    finally:
        os.chdir(cwd)
    fatal = [d for d in tu.diagnostics if d.severity >= ci.Diagnostic.Fatal]
    if fatal:
        return [], [], f"{entry.file}: {fatal[0].spelling}"

    facts: list[Fact] = []
    covered: set[str] = {entry.file}

    def add(kind: str, file: str, line: int, data: dict[str, Any]) -> None:
        facts.append(Fact(kind, file, line, CLANG, Confidence.HIGH, data))

    for inclusion in tu.get_includes():
        source = rel(inclusion.source.name if inclusion.source else None)
        if source not in repo_files:
            continue
        covered.add(source)
        target = rel(inclusion.include.name if inclusion.include else None)
        name = Path(inclusion.include.name).name if inclusion.include else ""
        data: dict[str, Any] = {"spelled": name, "system": target not in repo_files,
                                "conditional": False,
                                "target": target if target in repo_files else None}
        if data["target"] is None:
            data["external"] = external_group(name)
        add(INCLUDE, source, inclusion.location.line, data)

    class_kinds = {ci.CursorKind.CLASS_DECL: "class", ci.CursorKind.STRUCT_DECL: "struct",
                   ci.CursorKind.CLASS_TEMPLATE: "class", ci.CursorKind.UNION_DECL: "union"}

    def visit(cursor: Any) -> None:
        for child in cursor.get_children():
            file = rel(child.location.file.name) if child.location.file else None
            if file not in repo_files:
                continue
            covered.add(file)
            kind = child.kind
            if kind == ci.CursorKind.NAMESPACE:
                visit(child)
            elif kind in class_kinds and child.is_definition():
                owner = _qualified(child)
                add(SYMBOL, file, child.location.line, {"kind": class_kinds[kind], "name": owner})
                pure = False
                for member in child.get_children():
                    if member.kind == ci.CursorKind.CXX_BASE_SPECIFIER:
                        declaration = member.type.get_declaration()
                        base_name = (_qualified(declaration)
                                     if declaration.kind != ci.CursorKind.NO_DECL_FOUND
                                     else member.type.spelling)
                        add(INHERITS, file, member.location.line, {
                            "child": owner, "base": base_name,
                            "access": member.access_specifier.name.lower(),
                        })
                    elif member.kind == ci.CursorKind.CXX_METHOD and member.is_pure_virtual_method():
                        pure = True
                    elif member.kind == ci.CursorKind.FIELD_DECL:
                        names = _type_names(member.type)
                        if names:
                            add(FIELD_TYPE, file, member.location.line,
                                {"owner": owner, "types": names})
                if pure:
                    add(INTERFACE, file, child.location.line, {"class": owner})
                visit(child)
            elif kind == ci.CursorKind.ENUM_DECL and child.is_definition() and child.spelling:
                add(SYMBOL, file, child.location.line, {"kind": "enum", "name": _qualified(child)})
            elif kind in (ci.CursorKind.FUNCTION_DECL, ci.CursorKind.CXX_METHOD) and (
                child.is_definition()
            ):
                add(SYMBOL, file, child.location.line,
                    {"kind": "function", "name": _qualified(child)})

    visit(tu.cursor)
    return facts, sorted(covered), None
