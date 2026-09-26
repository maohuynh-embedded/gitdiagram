"""Walk a repository, keeping hand-written source and dropping build noise."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pathspec

# gitignore syntax; a trailing "/" matches directories at any depth.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git/",
    ".svn/",
    ".hg/",
    ".archdiag/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "node_modules/",
    ".idea/",
    ".vscode/",
    ".cache/",
    "build/",
    "build-*/",
    "build_*/",
    "cmake-build-*/",
    "out/",
    "dist/",
    "3rdparty/",
    "third_party/",
    "thirdparty/",
    "external/",
    "vendor/",
    "*.o",
    "*.obj",
    "*.a",
    "*.lib",
    "*.so",
    "*.so.*",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.pdb",
    "*.hex",
    "*.dfu",
    "*.bin",
    "*.elf",
    "*.map",
    "*.srec",
    "*.axf",
    "moc_*",
    "*.moc",
    "qrc_*",
    "ui_*.h",
    "*.qmlc",
    "*.jsc",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.bmp",
    "*.ico",
    "*.icns",
    "*.svg",
    "*.webp",
    "*.psd",
    "*.ttf",
    "*.otf",
    "*.woff",
    "*.woff2",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.tgz",
    "*.xz",
    "*.7z",
    "*.rar",
    "*.dmg",
    "*.pkg",
    "*.msi",
    "*.pdf",
    "*.log",
    ".DS_Store",
)

_BY_SUFFIX = {
    ".c": "c",
    ".h": "c-header",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".c++": "cpp",
    ".hh": "cpp-header",
    ".hpp": "cpp-header",
    ".hxx": "cpp-header",
    ".inl": "cpp-header",
    ".s": "asm",
    ".asm": "asm",
    ".qml": "qml",
    ".js": "javascript",
    ".ts": "typescript",
    ".py": "python",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".cmake": "cmake",
    ".pro": "qmake",
    ".pri": "qmake",
    ".qbs": "qbs",
    ".arxml": "arxml",
    ".dbc": "dbc",
    ".eds": "eds",
    ".dcf": "eds",
    ".ld": "linker",
    ".md": "markdown",
    ".rst": "text",
    ".txt": "text",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".ui": "qt-ui",
    ".qrc": "qt-resource",
    ".sh": "shell",
    ".ps1": "powershell",
}
_BY_NAME = {
    "CMakeLists.txt": "cmake",
    "Makefile": "make",
    "makefile": "make",
    "GNUmakefile": "make",
    "Kconfig": "kconfig",
    "meson.build": "meson",
    "SConstruct": "scons",
}

SOURCE_LANGUAGES = frozenset(
    {
        "c",
        "c-header",
        "cpp",
        "cpp-header",
        "asm",
        "qml",
        "javascript",
        "typescript",
        "python",
        "rust",
        "go",
        "java",
        "kotlin",
        "csharp",
    }
)
HEADER_LANGUAGES = frozenset({"c-header", "cpp-header"})

_SNIFF_BYTES = 8192


@dataclass(frozen=True)
class FileEntry:
    path: str  # relative, "/"-separated
    size: int
    language: str | None


def detect_language(name: str) -> str | None:
    if name in _BY_NAME:
        return _BY_NAME[name]
    suffix = Path(name).suffix
    if suffix == ".S":
        return "asm"
    return _BY_SUFFIX.get(suffix.lower())


def _is_binary(path: Path) -> bool:
    with path.open("rb") as fh:
        return b"\0" in fh.read(_SNIFF_BYTES)


def _build_spec(
    root: Path, include: Sequence[str], exclude: Sequence[str]
) -> pathspec.GitIgnoreSpec:
    lines = [*DEFAULT_EXCLUDES, *exclude]
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        lines.extend(gitignore.read_text(encoding="utf-8", errors="replace").splitlines())
    # Negations come last so an explicit include wins over every exclude.
    lines.extend(f"!{pattern}" for pattern in include)
    return pathspec.GitIgnoreSpec.from_lines(lines)


def walk(root: Path, include: Sequence[str] = (), exclude: Sequence[str] = ()) -> list[FileEntry]:
    root = root.resolve()
    spec = _build_spec(root, include, exclude)
    entries: list[FileEntry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel_dir = current.relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else f"{rel_dir}/"
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if not (current / name).is_symlink() and not spec.match_file(f"{prefix}{name}/")
        ]
        for name in sorted(filenames):
            rel = f"{prefix}{name}"
            full = current / name
            if full.is_symlink() or not full.is_file() or spec.match_file(rel):
                continue
            if _is_binary(full):
                continue
            entries.append(FileEntry(rel, full.stat().st_size, detect_language(name)))
    entries.sort(key=lambda entry: entry.path)
    return entries
