"""Pick the analysis path for a repository and run it: static (B) or LLM-only (A)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from archdiag.config import ConfigError, Settings, load_sections
from archdiag.facts.extract_ts import EXTRACTABLE
from archdiag.llm.client import ChatClient
from archdiag.model.schema_v2 import ArchModelV2
from archdiag.pipeline import analyze
from archdiag.scan.walker import walk
from archdiag.static_pipeline import analyze_static

Progress = Callable[[str, str], None]
MODES = ("auto", "static", "llm")


@dataclass(frozen=True)
class RunOutcome:
    model: ArchModelV2
    warnings: tuple[str, ...]
    mode: str
    cached: bool
    out_dir: Path
    detail: str


def detect_mode(repo: Path, settings: Settings) -> str:
    files = walk(repo, settings.include, settings.exclude)
    return "static" if any(f.language in EXTRACTABLE for f in files) else "llm"


def run_analysis(
    repo: Path,
    out: Path,
    settings: Settings,
    *,
    client_factory: Callable[[], ChatClient] | None,
    mode: str = "auto",
    compile_db: Path | None = None,
    rebuild: bool = False,
    use_cache: bool = True,
    progress: Progress | None = None,
) -> RunOutcome:
    if mode not in MODES:
        raise ConfigError(f"mode must be one of {', '.join(MODES)}")
    chosen = detect_mode(repo, settings) if mode == "auto" else mode
    if progress:
        progress("mode", f"{chosen} analysis" + (" (auto)" if mode == "auto" else ""))
    if chosen == "llm":
        if client_factory is None:
            raise ConfigError(
                "This repository has no C/C++ code, so it needs the LLM path; run without --no-llm."
            )
        result = analyze(
            repo, out, settings, client_factory(), use_cache=use_cache, progress=progress
        )
        detail = "cached result" if result.cached else f"{result.attempts} LLM attempt(s)"
        return RunOutcome(
            result.model, result.warnings, "llm", result.cached, result.out_dir, detail
        )
    static = analyze_static(
        repo,
        out,
        settings,
        load_sections(repo),
        client=client_factory() if client_factory else None,
        compile_db=compile_db,
        rebuild=rebuild,
        progress=progress,
    )
    components = sum(1 for n in static.model.nodes if n.level == "component")
    detail = (
        f"{components} components, {len(static.model.violations)} violation(s), "
        f"{len(static.model.views)} views"
    )
    return RunOutcome(static.model, static.warnings, "static", False, static.out_dir, detail)
