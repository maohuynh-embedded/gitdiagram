from pathlib import Path

from archdiag.context.select import estimate_tokens, select_evidence, summarize_tree
from archdiag.scan.snapshot import build_snapshot
from tests.conftest import MakeTree


def test_small_repo_fits_without_truncation(mini_repo: Path) -> None:
    evidence = select_evidence(build_snapshot(mini_repo), budget_tokens=20000)
    assert not evidence.truncated
    assert evidence.budget_used <= evidence.budget_limit == 20000
    paths = [excerpt.path for excerpt in evidence.excerpts]
    assert paths[:3] == ["README.md", "CMakeLists.txt", "src/app/main.c"]
    assert set(paths) == {
        "README.md", "CMakeLists.txt", "src/app/main.c", "src/drivers/can/can_driver.h",
        "src/drivers/can/can_driver.c", "src/hal/gpio.h", "src/hal/gpio.c", "tests/test_gpio.c",
    }
    assert evidence.candidates_total == len(paths)
    assert "build" not in evidence.tree_summary
    assert "src/hal/ (2 files: 1 c, 1 c-header)" in evidence.tree_summary


def test_budget_is_respected_and_truncation_flagged(tmp_path: Path, make_tree: MakeTree) -> None:
    files: dict[str, str | bytes] = {
        f"mod{d}/file{i}.c": "x" * 3000 for d in range(6) for i in range(10)
    }
    make_tree(tmp_path, files)
    evidence = select_evidence(build_snapshot(tmp_path), budget_tokens=2000)
    assert evidence.truncated
    assert evidence.budget_used <= 2000
    assert 0 < len(evidence.excerpts) < 60


def test_round_robin_visits_every_directory_before_repeating(
    tmp_path: Path, make_tree: MakeTree
) -> None:
    files: dict[str, str | bytes] = {}
    for d in ("a", "b", "c"):
        files[f"{d}/impl.c"] = "int x;" * 20
        files[f"{d}/big.c"] = "int y;" * 50
        files[f"{d}/api.h"] = "void f(void);"
    make_tree(tmp_path, files)
    paths = [e.path for e in select_evidence(build_snapshot(tmp_path), 20000).excerpts]
    assert paths[:3] == ["a/api.h", "b/api.h", "c/api.h"]
    assert paths[3:6] == ["a/big.c", "b/big.c", "c/big.c"]


def test_large_tree_falls_back_to_counts(tmp_path: Path, make_tree: MakeTree) -> None:
    files: dict[str, str | bytes] = {
        f"pkg{d}/a_long_file_name_number_{i}.c": "x" for d in range(40) for i in range(15)
    }
    make_tree(tmp_path, files)
    snapshot = build_snapshot(tmp_path)
    full = summarize_tree(snapshot)
    evidence = select_evidence(snapshot, budget_tokens=3000)
    assert estimate_tokens(full) > 1000
    assert evidence.truncated
    assert "a_long_file_name_number_0.c" not in evidence.tree_summary
    assert "pkg0/ (15 files: 15 c)" in evidence.tree_summary
