"""Local web viewer. Binds to 127.0.0.1 and only answers to its own host name."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from archdiag.config import Settings
from archdiag.llm.client import ChatClient
from archdiag.pipeline import (
    DIAGRAM_FILE,
    EXPLANATION_FILE,
    MODEL_FILE,
    RUN_FILE,
    analyze,
)

STATIC_DIR = Path(__file__).parent / "static"
MAX_PREVIEW_BYTES = 512 * 1024
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _resolve_inside(root: Path, rel: str) -> Path:
    if not rel or "\0" in rel:
        raise HTTPException(400, "A repository-relative path is required.")
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(403, "The path is outside the repository.")
    return candidate


def create_app(
    repo: Path,
    out: Path,
    settings: Settings,
    client_factory: Callable[[], ChatClient],
    port: int,
) -> FastAPI:
    root = repo.resolve()
    out = out.resolve()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    busy = threading.Lock()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # A Host check defeats DNS rebinding against this local server.
        if request.headers.get("host") not in allowed_hosts:
            return PlainTextResponse("Invalid host", status_code=400)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/model")
    def model() -> dict[str, Any]:
        try:
            data = json.loads((out / MODEL_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HTTPException(
                404, "No analysis yet. Run `archdiag analyze` or press Analyze."
            ) from exc
        explanation_path = out / EXPLANATION_FILE
        run_path = out / RUN_FILE
        return {
            "root": str(root),
            "repo_name": root.name,
            "model": data,
            "explanation": explanation_path.read_text(encoding="utf-8")
            if explanation_path.is_file() else "",
            "run": json.loads(run_path.read_text(encoding="utf-8")) if run_path.is_file() else {},
        }

    @app.get("/api/diagram")
    def diagram() -> PlainTextResponse:
        path = out / DIAGRAM_FILE
        if not path.is_file():
            raise HTTPException(404, "No diagram yet.")
        return PlainTextResponse(path.read_text(encoding="utf-8"))

    @app.get("/api/file")
    def file_preview(path: str = "") -> dict[str, Any]:
        target = _resolve_inside(root, path)
        if not target.is_file():
            raise HTTPException(404, "No such file.")
        if target.stat().st_size > MAX_PREVIEW_BYTES:
            raise HTTPException(413, "The file is too large to preview.")
        data = target.read_bytes()
        if b"\0" in data[:8192]:
            raise HTTPException(415, "Binary files cannot be previewed.")
        return {
            "path": target.relative_to(root).as_posix(),
            "abs_path": str(target),
            "text": data.decode("utf-8", errors="replace"),
        }

    @app.post("/api/analyze")
    def reanalyze(request: Request) -> Response:
        if request.headers.get("origin") not in allowed_origins:
            return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
        if not busy.acquire(blocking=False):
            return JSONResponse({"detail": "An analysis is already running."}, status_code=409)

        events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()

        def work() -> None:
            try:
                result = analyze(
                    root, out, settings, client_factory(), use_cache=False,
                    progress=lambda step, message: events.put(
                        ("progress", {"step": step, "message": message})
                    ),
                )
                events.put(("done", {
                    "nodes": len(result.model.nodes),
                    "edges": len(result.model.edges),
                    "warnings": list(result.warnings),
                }))
            except Exception as exc:  # reported to the page, never swallowed silently
                events.put(("error", {"message": str(exc)}))
            finally:
                busy.release()
                events.put(("close", {}))

        threading.Thread(target=work, daemon=True).start()

        def stream() -> Iterator[str]:
            while True:
                event, data = events.get()
                if event == "close":
                    return
                yield f"event: {event}\ndata: {json.dumps(data)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def serve(
    repo: Path, out: Path, settings: Settings, client_factory: Callable[[], ChatClient], port: int
) -> None:
    import uvicorn

    app = create_app(repo, out, settings, client_factory, port)
    print(f"archdiag is serving {repo.resolve()} at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
