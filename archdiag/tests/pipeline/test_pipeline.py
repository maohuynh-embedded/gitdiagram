import json
from pathlib import Path

import pytest

from archdiag.config import Settings
from archdiag.llm.client import LLMTimeout
from archdiag.pipeline import AnalysisError, analyze
from tests.fakes import GOOD_MODEL, FakeClient, good_reply

SETTINGS = Settings()


def test_first_valid_reply_writes_all_outputs(mini_repo: Path) -> None:
    out = mini_repo / ".archdiag"
    client = FakeClient([good_reply()])
    steps: list[str] = []

    result = analyze(mini_repo, out, SETTINGS, client, progress=lambda step, _: steps.append(step))

    assert result.attempts == 1
    assert not result.cached
    assert json.loads((out / "model.json").read_text())["title"] == "Mini ECU"
    assert 'n_app -->|"receives frames"| n_can_driver' in (out / "diagram.mmd").read_text()
    assert (out / "explanation.md").read_text().startswith("# Mini ECU")
    run = json.loads((out / "run.json").read_text())
    assert run["attempts"] == 1
    assert run["model"] == SETTINGS.model
    assert run["evidence"]["truncated"] is False
    assert {"scan", "context", "llm", "render"} <= set(steps)
    assert not (out / "debug").exists()


def test_invalid_json_is_retried_with_feedback(mini_repo: Path) -> None:
    out = mini_repo / ".archdiag"
    client = FakeClient(["not json", good_reply()])

    result = analyze(mini_repo, out, SETTINGS, client)

    assert result.attempts == 2
    second_call = client.calls[1]
    assert second_call[-2] == {"role": "assistant", "content": "not json"}
    assert "rejected" in second_call[-1]["content"]
    assert (out / "debug" / "attempt-1.txt").read_text() == "not json"


def test_unknown_paths_are_stripped_without_retry(mini_repo: Path) -> None:
    nodes = json.loads(json.dumps(GOOD_MODEL["nodes"]))
    nodes[0]["paths"] = ["src/app", "src/imaginary"]
    client = FakeClient([good_reply(nodes=nodes)])

    result = analyze(mini_repo, mini_repo / ".archdiag", SETTINGS, client)

    assert result.attempts == 1
    assert result.model.nodes[0].paths == ["src/app"]
    assert any("src/imaginary" in warning for warning in result.warnings)


def test_gives_up_after_max_attempts(mini_repo: Path) -> None:
    out = mini_repo / ".archdiag"
    client = FakeClient(["x", "y", "z"])

    with pytest.raises(AnalysisError, match="3 attempts"):
        analyze(mini_repo, out, SETTINGS, client)

    assert sorted(p.name for p in (out / "debug").iterdir()) == [
        "attempt-1.txt", "attempt-2.txt", "attempt-3.txt", "errors.json",
    ]
    assert not (out / "model.json").exists()


def test_cache_hit_skips_the_llm(mini_repo: Path) -> None:
    out = mini_repo / ".archdiag"
    analyze(mini_repo, out, SETTINGS, FakeClient([good_reply()]))

    result = analyze(mini_repo, out, SETTINGS, FakeClient([]))

    assert result.cached
    assert result.model.title == "Mini ECU"


def test_cache_is_bypassed_when_the_code_changes(mini_repo: Path) -> None:
    out = mini_repo / ".archdiag"
    analyze(mini_repo, out, SETTINGS, FakeClient([good_reply()]))
    (mini_repo / "src" / "app" / "main.c").write_text("int main(void) { return 1; }\n")

    result = analyze(mini_repo, out, SETTINGS, FakeClient([good_reply()]))

    assert not result.cached


def test_one_timeout_is_retried(mini_repo: Path) -> None:
    client = FakeClient([LLMTimeout("slow"), good_reply()])
    result = analyze(mini_repo, mini_repo / ".archdiag", SETTINGS, client)
    assert result.attempts == 1
    assert len(client.calls) == 2


def test_two_timeouts_fail(mini_repo: Path) -> None:
    client = FakeClient([LLMTimeout("slow"), LLMTimeout("slow")])
    with pytest.raises(AnalysisError, match="smaller model"):
        analyze(mini_repo, mini_repo / ".archdiag", SETTINGS, client)


def test_output_directory_inside_repo_is_not_scanned(mini_repo: Path) -> None:
    out = mini_repo / "docs" / "arch"
    analyze(mini_repo, out, SETTINGS, FakeClient([good_reply()]))
    client = FakeClient([good_reply()])

    analyze(mini_repo, out, SETTINGS, client, use_cache=False)

    prompt = client.calls[0][1]["content"]
    assert "docs/arch" not in prompt
    assert "model.json" not in prompt
