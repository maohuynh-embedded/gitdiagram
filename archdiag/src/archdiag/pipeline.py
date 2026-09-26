"""Run the analysis stages and write the results.

This module and cli.py are the only places that touch the file system or the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from archdiag import __version__
from archdiag.config import Settings
from archdiag.context.select import select_evidence
from archdiag.llm.client import ChatClient, LLMTimeout, Message
from archdiag.llm.prompts import PROMPT_VERSION, build_messages, retry_message
from archdiag.model.schema import SCHEMA_VERSION, ArchModel, llm_json_schema
from archdiag.model.validate import ValidationResult, validate_raw
from archdiag.render.explanation import render_explanation
from archdiag.render.mermaid import compile_mermaid
from archdiag.scan.snapshot import RepoSnapshot, build_snapshot

Progress = Callable[[str, str], None]

MODEL_FILE = "model.json"
DIAGRAM_FILE = "diagram.mmd"
EXPLANATION_FILE = "explanation.md"
RUN_FILE = "run.json"
DEBUG_DIR = "debug"


class AnalysisError(Exception):
    """The model output could not be turned into a valid ArchModel (CLI exit code 1)."""


@dataclass(frozen=True)
class AnalysisResult:
    model: ArchModel
    mermaid: str
    warnings: tuple[str, ...]
    attempts: int
    cached: bool
    out_dir: Path


def _noop(step: str, message: str) -> None:
    return None


def cache_key(snapshot: RepoSnapshot, settings: Settings) -> str:
    material = json.dumps([
        snapshot.content_hash, settings.api, settings.model, settings.language,
        settings.context_budget_tokens, PROMPT_VERSION, SCHEMA_VERSION,
    ])
    return hashlib.sha256(material.encode()).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_cached(out: Path, key: str) -> AnalysisResult | None:
    try:
        run = json.loads((out / RUN_FILE).read_text(encoding="utf-8"))
        if run.get("cache_key") != key:
            return None
        model = ArchModel.model_validate_json((out / MODEL_FILE).read_text(encoding="utf-8"))
        mermaid = (out / DIAGRAM_FILE).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    warnings = tuple(str(w) for w in run.get("warnings", []))
    return AnalysisResult(model, mermaid, warnings, int(run.get("attempts", 0)), True, out)


def _chat(client: ChatClient, messages: list[Message], schema: dict[str, Any],
          progress: Progress) -> str:
    try:
        return client.chat(messages, schema)
    except LLMTimeout:
        progress("llm", "timed out, retrying once")
    try:
        return client.chat(messages, schema)
    except LLMTimeout as exc:
        raise AnalysisError(
            f"{exc} Try a smaller model or a lower context_budget_tokens."
        ) from exc


def _excluded_output(repo: Path, out: Path) -> tuple[str, ...]:
    try:
        rel = out.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return ()
    return (f"/{rel}/",) if rel != "." else ()


def analyze(
    repo: Path,
    out: Path,
    settings: Settings,
    client: ChatClient,
    *,
    use_cache: bool = True,
    progress: Progress | None = None,
) -> AnalysisResult:
    report = progress or _noop
    timings: dict[str, float] = {}
    started = time.monotonic()

    report("scan", f"scanning {repo}")
    snapshot = build_snapshot(
        repo, settings.include, (*settings.exclude, *_excluded_output(repo, out))
    )
    timings["scan"] = time.monotonic() - started
    report("scan", f"{len(snapshot.files)} files, {len(snapshot.dirs)} directories")

    key = cache_key(snapshot, settings)
    if use_cache:
        cached = _load_cached(out, key)
        if cached is not None:
            report("cache", "repository unchanged, reusing the previous result")
            return cached

    step_start = time.monotonic()
    evidence = select_evidence(snapshot, settings.context_budget_tokens)
    timings["context"] = time.monotonic() - step_start
    warnings: list[str] = []
    if evidence.truncated:
        warnings.append(
            f"Context truncated: {len(evidence.excerpts)} of {evidence.candidates_total} "
            "candidate files were shown to the model."
        )
    report("context", f"{len(evidence.excerpts)} excerpts, ~{evidence.budget_used} tokens")

    debug_dir = out / DEBUG_DIR
    shutil.rmtree(debug_dir, ignore_errors=True)
    schema = llm_json_schema()
    messages = build_messages(snapshot.name, evidence, settings.language)
    known = snapshot.known_paths()

    step_start = time.monotonic()
    result: ValidationResult | None = None
    attempts = 0
    for attempt in range(1, settings.max_attempts + 1):
        attempts = attempt
        report("llm", f"attempt {attempt}/{settings.max_attempts} with {settings.model}")
        raw = _chat(client, messages, schema, report)
        result = validate_raw(raw, known)
        if result.ok:
            break
        _write_atomic(debug_dir / f"attempt-{attempt}.txt", raw)
        report("validate", f"rejected with {len(result.errors)} problem(s)")
        messages = [*messages, {"role": "assistant", "content": raw},
                    retry_message(result.errors)]
    timings["llm"] = time.monotonic() - step_start

    if result is None or result.model is None:
        errors = list(result.errors) if result else []
        _write_atomic(debug_dir / "errors.json", json.dumps(errors, indent=2))
        raise AnalysisError(
            f"The model output failed validation after {attempts} attempts; see {debug_dir}"
        )

    model = result.model
    warnings.extend(result.warnings)
    report("render", "writing outputs")
    mermaid = compile_mermaid(model)
    _write_atomic(out / MODEL_FILE, model.to_json())
    _write_atomic(out / DIAGRAM_FILE, mermaid)
    _write_atomic(out / EXPLANATION_FILE, render_explanation(model))
    timings["total"] = time.monotonic() - started
    run = {
        "archdiag_version": __version__,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cache_key": key,
        "content_hash": snapshot.content_hash,
        "mode": "llm",
        "api": settings.api,
        "endpoint": settings.endpoint,
        "model": settings.model,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "attempts": attempts,
        "warnings": warnings,
        "evidence": {
            "excerpts": len(evidence.excerpts),
            "candidates": evidence.candidates_total,
            "budget_used": evidence.budget_used,
            "budget_limit": evidence.budget_limit,
            "truncated": evidence.truncated,
        },
        "timings_s": {name: round(value, 3) for name, value in timings.items()},
    }
    _write_atomic(out / RUN_FILE, json.dumps(run, indent=2))
    return AnalysisResult(model, mermaid, tuple(warnings), attempts, False, out)
