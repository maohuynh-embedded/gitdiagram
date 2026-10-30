from pathlib import Path

import pytest

from archdiag.scan.walker import detect_language, walk
from tests.conftest import MakeTree


def paths(root: Path, **kwargs: list[str]) -> list[str]:
    return [entry.path for entry in walk(root, **kwargs)]


def test_skips_build_outputs_vendored_generated_and_binary_files(
    tmp_path: Path, make_tree: MakeTree
) -> None:
    make_tree(
        tmp_path,
        {
            "CMakeLists.txt": "project(x)",
            "include/can.h": "#pragma once",
            "src/app/main.cpp": "int main() {}",
            "src/app/moc_main.cpp": "generated",
            "src/app/main.moc": "generated",
            "src/app/ui_form.h": "generated",
            "build/obj/main.o": "x",
            "build-debug/x.cpp": "x",
            "cmake-build-release/y.cpp": "x",
            "3rdparty/lib/lib.c": "x",
            "third_party/lib/lib.c": "x",
            "firmware/fw.hex": "x",
            "assets/logo.png": "x",
            "data/blob.dat": b"\x00\x01binary",
            ".git/config": "x",
        },
    )
    assert paths(tmp_path) == ["CMakeLists.txt", "include/can.h", "src/app/main.cpp"]


def test_respects_gitignore_and_config_patterns(tmp_path: Path, make_tree: MakeTree) -> None:
    make_tree(
        tmp_path,
        {
            ".gitignore": "generated/\n*.tmp\n",
            "generated/a.c": "x",
            "src/a.c": "x",
            "src/b.tmp": "x",
            "docs/x.md": "x",
            "3rdparty/keep/k.c": "x",
        },
    )
    result = paths(tmp_path, include=["3rdparty/"], exclude=["docs/"])
    assert result == [".gitignore", "3rdparty/keep/k.c", "src/a.c"]


def test_skips_symlinks(tmp_path: Path, make_tree: MakeTree) -> None:
    outside = tmp_path / "outside"
    make_tree(outside, {"secret.c": "x"})
    repo = make_tree(tmp_path / "repo", {"src/a.c": "x"})
    (repo / "src" / "link.c").symlink_to(outside / "secret.c")
    (repo / "linked_dir").symlink_to(outside, target_is_directory=True)
    assert paths(repo) == ["src/a.c"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("CMakeLists.txt", "cmake"),
        ("startup.S", "asm"),
        ("Driver.HPP", "cpp-header"),
        ("main.c", "c"),
        ("view.qml", "qml"),
        ("app.pro", "qmake"),
        ("notes.xyz", None),
    ],
)
def test_detect_language(name: str, expected: str | None) -> None:
    assert detect_language(name) == expected
