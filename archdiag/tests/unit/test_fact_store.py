from pathlib import Path

from archdiag.facts.model import INCLUDE, TREESITTER, Confidence, Fact
from archdiag.facts.store import FactStore


def fact(line: int) -> Fact:
    return Fact(INCLUDE, "a.c", line, TREESITTER, Confidence.MEDIUM, {"spelled": "b.h"})


def test_round_trip_and_cache_key(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.db")
    assert store.cached("a.c", "k1") is None
    store.replace("a.c", "k1", [fact(1), fact(2)])
    assert store.cached("a.c", "k1") == [fact(1), fact(2)]
    assert store.cached("a.c", "k2") is None
    store.replace("a.c", "k2", [fact(3)])
    assert store.cached("a.c", "k2") == [fact(3)]
    store.close()
    reopened = FactStore(tmp_path / "facts.db")
    assert reopened.cached("a.c", "k2") == [fact(3)]


def test_prune_and_clear(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.db")
    store.replace("a.c", "k", [fact(1)])
    store.replace("b.c", "k", [fact(1)])
    store.prune({"a.c"})
    assert store.cached("b.c", "k") is None
    assert store.cached("a.c", "k") is not None
    store.clear()
    assert store.cached("a.c", "k") is None


def test_annotations(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.db")
    assert store.get_annotation("x") is None
    store.put_annotation("x", '{"label": "A"}')
    assert store.get_annotation("x") == '{"label": "A"}'


def test_corrupt_database_is_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "facts.db"
    path.write_bytes(b"this is not sqlite" * 100)
    store = FactStore(path)
    assert store.warnings
    store.replace("a.c", "k", [fact(1)])
    assert store.cached("a.c", "k") == [fact(1)]
