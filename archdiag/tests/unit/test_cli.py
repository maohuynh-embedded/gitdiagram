import json
from pathlib import Path

import pytest

from archdiag import cli
from archdiag.config import Settings
from archdiag.llm.client import LLMUnavailable
from tests.fakes import FakeClient, good_reply

EXPECTED = Path(__file__).parents[2] / "benchmarks" / "mini-repo.expected.toml"


@pytest.fixture(autouse=True)
def isolated_user_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "USER_CONFIG", tmp_path / "no-user-config.toml")


def use_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    def factory(settings: Settings) -> object:
        return client

    monkeypatch.setattr(cli, "make_client", factory)


def test_missing_repository_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_remote_endpoint_needs_explicit_flag(
    mini_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["analyze", str(mini_repo), "--endpoint", "https://llm.example.com"])
    assert code == 2
    assert "--allow-remote-llm" in capsys.readouterr().err


def test_repo_config_cannot_silently_send_code_away(
    mini_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (mini_repo / "archdiag.toml").write_text('endpoint = "http://10.0.0.8:11434"\n')
    assert cli.main(["analyze", str(mini_repo)]) == 2
    assert "not on this machine" in capsys.readouterr().err


def test_analyze_succeeds(
    mini_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_client(monkeypatch, FakeClient([good_reply()]))
    assert cli.main(["analyze", str(mini_repo), "--mode", "llm"]) == 0
    assert (mini_repo / ".archdiag" / "model.json").is_file()
    captured = capsys.readouterr()
    assert "llm: 3 nodes, 2 edges" in captured.out
    assert "[scan]" in captured.err


def test_validation_failure_exits_1(mini_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_client(monkeypatch, FakeClient(["x", "y", "z"]))
    assert cli.main(["analyze", str(mini_repo), "--mode", "llm"]) == 1


def test_unavailable_runtime_exits_2(mini_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_client(monkeypatch, FakeClient([LLMUnavailable("no ollama")]))
    assert cli.main(["analyze", str(mini_repo), "--mode", "llm"]) == 2


def test_doctor_reports_ready_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_client(monkeypatch, FakeClient(models={"qwen3-coder:30b": 18_000_000_000}))
    assert cli.main(["doctor"]) == 0
    assert "model qwen3-coder:30b is available" in capsys.readouterr().out


def test_doctor_reports_missing_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_client(monkeypatch, FakeClient(models={"other:7b": 1}))
    assert cli.main(["doctor"]) == 2
    assert "ollama pull qwen3-coder:30b" in capsys.readouterr().out


def test_doctor_reports_unreachable_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    class Down(FakeClient):
        def list_models(self) -> dict[str, int]:
            raise LLMUnavailable("Cannot reach the LLM runtime")

    use_client(monkeypatch, Down())
    assert cli.main(["doctor"]) == 2


def test_bench_writes_report(
    mini_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_client(monkeypatch, FakeClient([good_reply()]))
    assert cli.main(["bench", str(mini_repo), "--mode", "llm", "--expected", str(EXPECTED)]) == 0
    report = json.loads((mini_repo / ".archdiag" / "bench.json").read_text())
    assert report["component_recall"] == 1.0
    assert "component recall" in capsys.readouterr().out


def test_auto_mode_runs_static_analysis_without_llm(
    mini_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["analyze", str(mini_repo), "--no-llm"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("static: ")
    assert "4 components, 0 violation(s), 3 views" in out
    assert (mini_repo / ".archdiag" / "views" / "system.mmd").is_file()


def test_llm_mode_needs_a_model(mini_repo: Path) -> None:
    assert cli.main(["analyze", str(mini_repo), "--mode", "llm", "--no-llm"]) == 2


def test_check_exits_3_on_violations(mini_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["check", str(mini_repo)]) == 0
    (mini_repo / "archdiag.toml").write_text(
        '[architecture]\nlayers = ["hal", "app"]\n'
        '[[architecture.components]]\nname = "app"\nlayer = "app"\npaths = ["src/app/**"]\n'
        '[[architecture.components]]\nname = "hal"\nlayer = "hal"\n'
        'paths = ["src/hal/**", "src/drivers/**"]\n'
        '[[architecture.components]]\nname = "tests"\npaths = ["tests/**"]\n'
    )
    capsys.readouterr()
    assert cli.main(["check", str(mini_repo)]) == 3
    out = capsys.readouterr().out
    assert "layer-order: app -> hal" in out
    assert "src/app/main.c:1" in out


def test_bench_compare(
    mini_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_client(monkeypatch, FakeClient([good_reply()] + ["{}"] * 20))
    code = cli.main(["bench", str(mini_repo), "--compare", "--expected", str(EXPECTED)])
    assert code == 0
    out = capsys.readouterr().out
    assert "llm" in out and "static" in out
    assert (mini_repo / ".archdiag" / "static" / "bench.json").is_file()
