from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from archdiag.config import Settings
from archdiag.pipeline import analyze
from archdiag.run import run_analysis
from archdiag.web.server import create_app
from tests.fakes import FakeClient, good_reply

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def app_for(
    repo: Path, replies: list[str | Exception] | None = None, mode: str = "llm"
) -> TestClient:
    out = repo / ".archdiag"
    client = FakeClient(replies or [good_reply()])

    def runner(progress):  # type: ignore[no-untyped-def]
        return run_analysis(
            repo,
            out,
            Settings(),
            client_factory=lambda: client,
            mode=mode,
            use_cache=False,
            progress=progress,
        )

    return TestClient(create_app(repo, out, runner, PORT), base_url=BASE)


@pytest.fixture
def analysed(mini_repo: Path) -> Path:
    analyze(mini_repo, mini_repo / ".archdiag", Settings(), FakeClient([good_reply()]))
    return mini_repo


def test_model_endpoint(analysed: Path) -> None:
    response = app_for(analysed).get("/api/model")
    assert response.status_code == 200
    body = response.json()
    assert body["root"] == str(analysed.resolve())
    assert body["model"]["nodes"][0]["id"] == "app"
    assert body["explanation"].startswith("# Mini ECU")
    assert body["run"]["attempts"] == 1


def test_model_endpoint_before_any_analysis(mini_repo: Path) -> None:
    response = app_for(mini_repo).get("/api/model")
    assert response.status_code == 404
    assert "archdiag analyze" in response.json()["detail"]


def test_diagram_endpoint(analysed: Path) -> None:
    response = app_for(analysed).get("/api/diagram")
    assert response.status_code == 200
    assert response.text.splitlines()[1] == "flowchart TD"


def test_file_preview(analysed: Path) -> None:
    response = app_for(analysed).get("/api/file", params={"path": "src/app/main.c"})
    assert response.status_code == 200
    assert "can_init" in response.json()["text"]
    assert response.json()["abs_path"] == str((analysed / "src/app/main.c").resolve())


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "src/../../outside.txt", ""])
def test_file_preview_refuses_paths_outside_the_repo(analysed: Path, path: str) -> None:
    (analysed.parent / "outside.txt").write_text("secret")
    response = app_for(analysed).get("/api/file", params={"path": path})
    assert response.status_code in (400, 403)


def test_file_preview_refuses_symlink_escape(analysed: Path) -> None:
    (analysed.parent / "outside.txt").write_text("secret")
    (analysed / "src" / "leak.c").symlink_to(analysed.parent / "outside.txt")
    response = app_for(analysed).get("/api/file", params={"path": "src/leak.c"})
    assert response.status_code == 403


def test_file_preview_missing_binary_and_large(analysed: Path) -> None:
    client = app_for(analysed)
    assert client.get("/api/file", params={"path": "src/none.c"}).status_code == 404
    assert client.get("/api/file", params={"path": "build/main.o"}).status_code == 415
    (analysed / "big.txt").write_text("x" * 600_000)
    assert client.get("/api/file", params={"path": "big.txt"}).status_code == 413


def test_wrong_host_is_rejected(analysed: Path) -> None:
    out = analysed / ".archdiag"
    app = create_app(analysed, out, lambda progress: None, PORT)  # type: ignore[arg-type,return-value]
    client = TestClient(app, base_url=f"http://evil.example:{PORT}")
    assert client.get("/api/model").status_code == 400


def test_analyze_requires_same_origin(analysed: Path) -> None:
    client = app_for(analysed)
    assert client.post("/api/analyze").status_code == 403
    response = client.post("/api/analyze", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_analyze_streams_progress_and_done(analysed: Path) -> None:
    client = app_for(analysed, [good_reply(title="Mini ECU v2")])
    response = client.post("/api/analyze", headers={"Origin": BASE})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in response.text
    assert "event: done" in response.text
    assert client.get("/api/model").json()["model"]["title"] == "Mini ECU v2"


def test_analyze_reports_errors_as_events(analysed: Path) -> None:
    client = app_for(analysed, ["bad", "bad", "bad"])
    response = client.post("/api/analyze", headers={"Origin": BASE})
    assert "event: error" in response.text


def test_index_has_security_headers(analysed: Path) -> None:
    response = app_for(analysed).get("/")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "unsafe-eval" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"


def test_views_and_view_diagrams(mini_repo: Path) -> None:
    run_analysis(mini_repo, mini_repo / ".archdiag", Settings(), client_factory=None)
    client = app_for(mini_repo)
    views = client.get("/api/views").json()
    assert [v["id"] for v in views] == ["system", "dependencies", "interfaces"]
    system = client.get("/api/diagram", params={"view": "system"})
    assert system.status_code == 200
    assert "flowchart LR" in system.text
    for bad in ("../model", "x/../../y", "nope"):
        assert client.get("/api/diagram", params={"view": bad}).status_code in (400, 404)
    model = client.get("/api/model").json()["model"]
    assert model["schema_version"] == 2


def test_static_reanalysis_over_sse(mini_repo: Path) -> None:
    client = app_for(mini_repo, ["{}"] * 20, mode="static")
    response = client.post("/api/analyze", headers={"Origin": BASE})
    assert "event: done" in response.text
    assert '"mode": "static"' in response.text
