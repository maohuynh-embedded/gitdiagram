"""An immutable view of the files archdiag will reason about."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from archdiag.scan.walker import FileEntry, walk

BUILD_FILE_NAMES = frozenset({
    "CMakeLists.txt", "CMakePresets.json", "Makefile", "makefile", "GNUmakefile", "meson.build",
    "SConstruct", "Kconfig", "conanfile.txt", "conanfile.py", "vcpkg.json",
})
BUILD_SUFFIXES = frozenset({".pro", ".pri", ".qbs", ".cmake"})
README_NAMES = ("README.md", "README.rst", "README.txt", "README", "Readme.md", "readme.md")


class ScanError(Exception):
    """The repository cannot be scanned (CLI exit code 2)."""


@dataclass(frozen=True)
class RepoSnapshot:
    root: Path
    files: tuple[FileEntry, ...]
    dirs: tuple[str, ...]
    readme: str | None
    build_files: tuple[str, ...]
    content_hash: str
    _file_paths: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_file_paths", frozenset(f.path for f in self.files))

    @property
    def name(self) -> str:
        return self.root.name

    def known_paths(self) -> frozenset[str]:
        return self._file_paths | frozenset(self.dirs)

    def read_text(self, path: str, max_chars: int) -> str:
        if path not in self._file_paths:
            raise KeyError(f"{path} is not part of the snapshot")
        with (self.root / path).open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(max_chars)


def _is_build_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in BUILD_FILE_NAMES or Path(name).suffix in BUILD_SUFFIXES


def _hash_files(root: Path, files: Sequence[FileEntry]) -> str:
    digest = hashlib.sha256()
    for entry in files:
        with (root / entry.path).open("rb") as fh:
            file_digest = hashlib.file_digest(fh, "sha256").hexdigest()
        digest.update(f"{entry.path}\0{file_digest}\n".encode())
    return digest.hexdigest()


def build_snapshot(
    root: Path, include: Sequence[str] = (), exclude: Sequence[str] = ()
) -> RepoSnapshot:
    if not root.is_dir():
        raise ScanError(f"{root} is not a directory")
    root = root.resolve()
    try:
        files = walk(root, include, exclude)
        content_hash = _hash_files(root, files)
    except OSError as exc:
        raise ScanError(f"Cannot read {root}: {exc}") from exc
    if not files:
        raise ScanError(f"No analysable files found in {root} (everything is empty or excluded)")
    dirs = sorted({
        "/".join(parts[:depth])
        for entry in files
        for parts in [entry.path.split("/")]
        for depth in range(1, len(parts))
    })
    paths = {entry.path for entry in files}
    readme = next((name for name in README_NAMES if name in paths), None)
    build_files = tuple(entry.path for entry in files if _is_build_file(entry.path))
    return RepoSnapshot(root, tuple(files), tuple(dirs), readme, build_files, content_hash)
