from pathlib import Path

import pytest

from archdiag.scan.snapshot import ScanError, build_snapshot
from tests.conftest import MakeTree


def test_snapshot_collects_dirs_readme_and_build_files(tmp_path: Path, make_tree: MakeTree) -> None:
    make_tree(
        tmp_path,
        {
            "README.md": "# Demo",
            "CMakeLists.txt": "project(x)",
            "app/app.pro": "TEMPLATE = app",
            "src/can/driver.c": "int x;",
        },
    )
    snap = build_snapshot(tmp_path)

    assert snap.root == tmp_path.resolve()
    assert snap.name == tmp_path.name
    assert snap.readme == "README.md"
    assert snap.build_files == ("CMakeLists.txt", "app/app.pro")
    assert snap.dirs == ("app", "src", "src/can")
    assert {"src/can", "src/can/driver.c", "README.md"} <= snap.known_paths()
    assert [f.language for f in snap.files if f.path == "src/can/driver.c"] == ["c"]


def test_content_hash_follows_file_contents(tmp_path: Path, make_tree: MakeTree) -> None:
    make_tree(tmp_path, {"a.c": "int a;"})
    first = build_snapshot(tmp_path).content_hash
    assert build_snapshot(tmp_path).content_hash == first
    (tmp_path / "a.c").write_text("int b;")
    assert build_snapshot(tmp_path).content_hash != first


def test_empty_repository_is_an_error(tmp_path: Path, make_tree: MakeTree) -> None:
    make_tree(tmp_path, {"build/out.o": "x"})
    with pytest.raises(ScanError, match="No analysable files"):
        build_snapshot(tmp_path)


def test_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ScanError, match="not a directory"):
        build_snapshot(tmp_path / "nope")


def test_read_text_is_limited_to_snapshot_files(tmp_path: Path, make_tree: MakeTree) -> None:
    make_tree(tmp_path, {"a.c": "abcdef", "build/b.c": "hidden"})
    snap = build_snapshot(tmp_path)
    assert snap.read_text("a.c", 3) == "abc"
    with pytest.raises(KeyError):
        snap.read_text("build/b.c", 10)
    with pytest.raises(KeyError):
        snap.read_text("../etc/passwd", 10)
