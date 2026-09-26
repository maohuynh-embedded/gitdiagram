from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

MakeTree = Callable[[Path, dict[str, str | bytes]], Path]


def _make_tree(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return root


@pytest.fixture
def make_tree() -> MakeTree:
    return _make_tree


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    """A private copy of the mini-repo fixture, safe to write outputs into."""
    target = tmp_path / "mini-repo"
    shutil.copytree(FIXTURES / "mini-repo", target)
    return target
