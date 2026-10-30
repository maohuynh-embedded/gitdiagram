"""Sub-project B pipeline: facts → components → rules → views → (annotation) → outputs."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from archdiag import __version__
from archdiag.annotate.annotate import ANNOTATION_VERSION, annotate
from archdiag.arch.aggregate import aggregate
from archdiag.arch.components import build_components, target_dirs
from archdiag.arch.config import load_architecture
from archdiag.arch.rules import check_rules
from archdiag.arch.views import build_views
from archdiag.config import Settings
from archdiag.facts.model import BUILD_TARGET
from archdiag.facts.protocol_config import load_protocol_config
from archdiag.facts.runner import collect_facts
from archdiag.facts.store import FactStore
from archdiag.llm.client import ChatClient, LLMError
from archdiag.model.schema_v2 import ArchModelV2
from archdiag.pipeline import (
    DIAGRAM_FILE,
    EXPLANATION_FILE,
    MODEL_FILE,
    RUN_FILE,
    VIEWS_DIR,
    _excluded_output,
    _write_atomic,
)
from archdiag.render.explanation_v2 import render_explanation_v2
from archdiag.render.views import compile_view, safe_view_file
from archdiag.scan.snapshot import build_snapshot
from archdiag.scan.walker import SOURCE_LANGUAGES

Progress = Callable[[str, str], None]
FACTS_FILE = "facts.db"
PRIMARY_VIEW = "dependencies"


@dataclass(frozen=True)
class StaticResult:
    model: ArchModelV2
    warnings: tuple[str, ...]
    stats: dict[str, Any]
    out_dir: Path


def _noop(step: str, message: str) -> None:
    return None


def analyze_static(
    repo: Path,
    out: Path,
    settings: Settings,
    sections: dict[str, Any],
    *,
    client: ChatClient | None = None,
    compile_db: Path | None = None,
    rebuild: bool = False,
    progress: Progress | None = None,
) -> StaticResult:
    report = progress or _noop
    started = time.monotonic()
    timings: dict[str, float] = {}

    architecture = load_architecture(sections.get("architecture"))
    protocols = load_protocol_config(sections.get("protocol"))

    report("scan", f"scanning {repo}")
    snapshot = build_snapshot(
        repo, settings.include, (*settings.exclude, *_excluded_output(repo, out))
    )
    report("scan", f"{len(snapshot.files)} files, {len(snapshot.dirs)} directories")

    out.mkdir(parents=True, exist_ok=True)
    store = FactStore(out / FACTS_FILE)
    try:
        step = time.monotonic()
        facts = collect_facts(
            snapshot, protocols, store, compile_db=compile_db, rebuild=rebuild, progress=report
        )
        timings["facts"] = time.monotonic() - step

        step = time.monotonic()
        source_files = [f.path for f in snapshot.files if f.language in SOURCE_LANGUAGES]
        targets = [f.data for f in facts.facts if f.kind == BUILD_TARGET]
        components = build_components(
            source_files, target_dirs(targets, len(source_files)), architecture
        )
        line_counts = {
            path: (snapshot.root / path).read_bytes().count(b"\n") for path in source_files
        }
        agg = aggregate(components, facts.facts, line_counts, snapshot.name)
        views = build_views(components, agg, snapshot.name)
        violations = check_rules(components, views.edges, architecture)
        model = ArchModelV2(
            title=snapshot.name,
            mode="static",
            groups=agg.groups,
            nodes=views.nodes,
            edges=views.edges,
            messages=agg.messages,
            violations=violations,
            views=views.views,
        )
        timings["model"] = time.monotonic() - step
        report(
            "model",
            f"{len(components)} components, {len(model.edges)} edges, "
            f"{len(violations)} violation(s), {len(model.views)} views",
        )

        warnings = list(facts.warnings)
        annotation_stats: dict[str, int] = {}
        annotated_ok = False
        if client is not None:
            step = time.monotonic()
            try:
                annotated = annotate(
                    model,
                    {c.id: c.files for c in components},
                    list(agg.classes.values()),
                    snapshot,
                    client,
                    store,
                    settings,
                    report,
                )
            except LLMError as exc:
                warnings.append(f"No annotation: {exc} The diagrams use static labels.")
            else:
                model = annotated.model
                warnings.extend(annotated.warnings)
                annotation_stats = annotated.stats
                annotated_ok = True
            timings["annotate"] = time.monotonic() - step
    finally:
        store.close()

    report("render", "writing outputs")
    views_dir = out / VIEWS_DIR
    shutil.rmtree(views_dir, ignore_errors=True)
    for view in model.views:
        _write_atomic(views_dir / safe_view_file(view.id), compile_view(model, view))
    primary = model.view(PRIMARY_VIEW) or (model.views[0] if model.views else None)
    if primary is not None:
        _write_atomic(out / DIAGRAM_FILE, compile_view(model, primary))
    _write_atomic(out / MODEL_FILE, model.to_json())
    _write_atomic(out / EXPLANATION_FILE, render_explanation_v2(model))
    timings["total"] = time.monotonic() - started
    stats: dict[str, Any] = {**facts.stats, **annotation_stats}
    run = {
        "archdiag_version": __version__,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "content_hash": snapshot.content_hash,
        "mode": "static",
        "annotated": annotated_ok,
        "api": settings.api if client else None,
        "endpoint": settings.endpoint if client else None,
        "model": settings.model if client else None,
        "annotation_version": ANNOTATION_VERSION,
        "schema_version": 2,
        "compile_db": facts.compile_db,
        "stats": stats,
        "warnings": warnings,
        "timings_s": {name: round(value, 3) for name, value in timings.items()},
    }
    _write_atomic(out / RUN_FILE, json.dumps(run, indent=2))
    return StaticResult(model, tuple(warnings), stats, out)
