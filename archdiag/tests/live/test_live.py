"""End-to-end run against a real local LLM. Run with: pytest -m live"""

from pathlib import Path

import pytest

from archdiag.bench import load_expected, score
from archdiag.config import Settings
from archdiag.llm.client import LLMError, make_client
from archdiag.pipeline import analyze
from archdiag.scan.snapshot import build_snapshot

EXPECTED = Path(__file__).parents[2] / "benchmarks" / "mini-repo.expected.toml"


@pytest.mark.live
def test_mini_repo_with_local_model(mini_repo: Path) -> None:
    settings = Settings()
    client = make_client(settings)
    try:
        if settings.model not in client.list_models():
            pytest.skip(f"model {settings.model} is not installed")
    except LLMError as exc:
        pytest.skip(f"no local LLM runtime: {exc}")

    result = analyze(mini_repo, mini_repo / ".archdiag", settings, client)
    snapshot = build_snapshot(mini_repo)
    report = score(
        result.model,
        load_expected(EXPECTED),
        snapshot.known_paths(),
        [f.path for f in snapshot.files],
    )

    assert report.junk_nodes == []
    assert report.path_validity == 1.0
    assert report.component_recall >= 2 / 3
