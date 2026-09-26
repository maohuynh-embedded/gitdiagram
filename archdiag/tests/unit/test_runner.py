import json
from pathlib import Path

import pytest

from archdiag.facts.extract_clang import clang_setup, find_compile_db, load_compile_db
from archdiag.facts.model import CLANG, INCLUDE, INHERITS, INTERFACE, Confidence, Fact
from archdiag.facts.protocol_config import load_protocol_config
from archdiag.facts.runner import collect_facts
from archdiag.facts.store import FactStore
from archdiag.scan.snapshot import build_snapshot
from tests.conftest import MakeTree

PROTOCOLS = load_protocol_config(None)


def canonical(facts: list[Fact]) -> list[str]:
    return sorted(
        json.dumps([f.kind, f.file, f.line, f.source, int(f.confidence), f.data], sort_keys=True)
        for f in facts
    )


def test_mini_repo_facts_and_include_resolution(mini_repo: Path, tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.db")
    result = collect_facts(build_snapshot(mini_repo), PROTOCOLS, store)
    includes = {
        (f.file, f.data["spelled"]): f.data.get("target") or f.data.get("external")
        for f in result.facts
        if f.kind == INCLUDE
    }
    assert includes[("src/app/main.c", "can_driver.h")] == "src/drivers/can/can_driver.h"
    assert includes[("src/app/main.c", "gpio.h")] == "src/hal/gpio.h"
    assert includes[("src/drivers/can/can_driver.h", "stdint.h")] == "C/C++ standard library"
    assert includes[("tests/test_gpio.c", "gpio.h")] == "src/hal/gpio.h"
    assert result.stats["parsed"] == 6
    assert result.stats["cached"] == 0


def test_second_run_is_cached_and_changes_are_reparsed(mini_repo: Path, tmp_path: Path) -> None:
    store = FactStore(tmp_path / "facts.db")
    first = collect_facts(build_snapshot(mini_repo), PROTOCOLS, store)
    second = collect_facts(build_snapshot(mini_repo), PROTOCOLS, store)
    assert second.stats["parsed"] == 0
    assert canonical(second.facts) == canonical(first.facts)

    (mini_repo / "src" / "hal" / "gpio.c").write_text('#include "gpio.h"\n#include "extra.h"\n')
    third = collect_facts(build_snapshot(mini_repo), PROTOCOLS, store)
    assert third.stats["parsed"] == 1
    assert any(f.data.get("spelled") == "extra.h" for f in third.facts)

    fourth = collect_facts(build_snapshot(mini_repo), PROTOCOLS, store, rebuild=True)
    assert fourth.stats["parsed"] == 6


def test_parallel_extraction_matches_serial(
    tmp_path: Path, make_tree: MakeTree, monkeypatch: pytest.MonkeyPatch
) -> None:
    files: dict[str, str | bytes] = {
        f"m{i}/f{i}.c": f'#include "m{(i + 1) % 50}/f{(i + 1) % 50}.h"\nint f{i};\n'
        for i in range(50)
    }
    files.update({f"m{i}/f{i}.h": f"int g{i};\n" for i in range(50)})
    repo = make_tree(tmp_path / "repo", files)
    monkeypatch.setattr("archdiag.facts.runner._PARALLEL_THRESHOLD", 10)
    serial = collect_facts(build_snapshot(repo), PROTOCOLS, FactStore(tmp_path / "a.db"), workers=1)
    parallel = collect_facts(
        build_snapshot(repo), PROTOCOLS, FactStore(tmp_path / "b.db"), workers=4
    )
    assert canonical(serial.facts) == canonical(parallel.facts)


def test_missing_explicit_compile_db_is_a_warning(mini_repo: Path, tmp_path: Path) -> None:
    result = collect_facts(
        build_snapshot(mini_repo),
        PROTOCOLS,
        FactStore(tmp_path / "f.db"),
        compile_db=tmp_path / "nope.json",
    )
    assert any("not found" in w for w in result.warnings)


CPP_FILES: dict[str, str | bytes] = {
    "include/port.h": "#pragma once\nclass Port { public: virtual ~Port(); "
    "virtual void send(int id) = 0; };\n",
    "src/impl.h": '#pragma once\n#include "port.h"\n#ifdef NEVER\n#include "ghost.h"\n#endif\n'
    "class Impl : public Port { public: void send(int id) override; };\n",
    "src/impl.cpp": '#include "impl.h"\nvoid Impl::send(int id) { (void)id; }\n',
}


def write_compile_db(repo: Path) -> None:
    (repo / "build").mkdir()
    (repo / "build" / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(repo),
                    "arguments": [
                        "c++",
                        "-std=c++17",
                        "-Iinclude",
                        "-c",
                        "src/impl.cpp",
                        "-o",
                        "impl.o",
                    ],
                    "file": "src/impl.cpp",
                }
            ]
        )
    )


def test_compile_db_discovery_and_loading(tmp_path: Path, make_tree: MakeTree) -> None:
    repo = make_tree(tmp_path / "repo", CPP_FILES)
    write_compile_db(repo)
    db = find_compile_db(repo, None)
    assert db == repo / "build" / "compile_commands.json"
    entries = load_compile_db(db, repo.resolve(), ["src/impl.cpp"])
    assert [(e.file, e.args) for e in entries] == [("src/impl.cpp", ("-std=c++17", "-Iinclude"))]


@pytest.mark.skipif(clang_setup() is None, reason="libclang is not available")
def test_clang_facts_replace_tree_sitter_for_covered_files(
    tmp_path: Path, make_tree: MakeTree
) -> None:
    repo = make_tree(tmp_path / "repo", CPP_FILES)
    write_compile_db(repo)
    result = collect_facts(build_snapshot(repo), PROTOCOLS, FactStore(tmp_path / "f.db"))

    assert result.stats["clang_units"] == 1
    assert result.stats["clang_failed"] == 0
    includes = [f for f in result.facts if f.kind == INCLUDE and f.file == "src/impl.h"]
    assert {f.source for f in includes} == {CLANG}
    assert [f.data["target"] for f in includes] == ["include/port.h"]  # the #ifdef branch is off
    inherits = [f for f in result.facts if f.kind == INHERITS]
    assert [(f.data["child"], f.data["base"], f.confidence) for f in inherits] == [
        ("Impl", "Port", Confidence.HIGH)
    ]
    assert [f.data["class"] for f in result.facts if f.kind == INTERFACE] == ["Port"]
