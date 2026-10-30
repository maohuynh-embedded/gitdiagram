"""Generate the architecture description: static model → 42010 AD → notations → validation."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from archdiag.ad.build import _files_under, build_ad
from archdiag.ad.config import load_ad_config
from archdiag.ad.context import ExportContext
from archdiag.ad.correspondence import check_correspondences
from archdiag.ad.document import render_document
from archdiag.ad.export.archimate import export_archimate
from archdiag.ad.export.autosar import export_autosar
from archdiag.ad.export.c4 import export_c4
from archdiag.ad.export.safety import export_safety
from archdiag.ad.export.sysml import export_sysml
from archdiag.ad.model import AD
from archdiag.ad.validate import ExportResult
from archdiag.config import ConfigError, Settings, load_sections
from archdiag.facts.model import Fact
from archdiag.facts.protocol_config import load_protocol_config
from archdiag.facts.runner import collect_facts
from archdiag.facts.store import FactStore
from archdiag.llm.client import ChatClient
from archdiag.pipeline import _excluded_output, _write_atomic
from archdiag.run import run_analysis
from archdiag.scan.snapshot import RepoSnapshot, build_snapshot
from archdiag.static_pipeline import FACTS_FILE

Progress = Callable[[str, str], None]
AD_DIR = "ad"
DOCUMENT = "architecture-description.md"
Exporter = Callable[[AD, ExportContext], ExportResult]


def _c4(ad: AD, ctx: ExportContext) -> ExportResult:
    return export_c4(ad)


def _archimate(ad: AD, ctx: ExportContext) -> ExportResult:
    return export_archimate(ad)


def _sysml(ad: AD, ctx: ExportContext) -> ExportResult:
    return export_sysml(ad, ctx.config.sysml_validator)


# Order matters: safety runs last because it adds requirement elements to the AD.
EXPORTERS: dict[str, Exporter] = {
    "c4": _c4,
    "archimate": _archimate,
    "sysml": _sysml,
    "autosar": export_autosar,
    "safety": export_safety,
}
ALL_NOTATIONS = tuple(EXPORTERS)


@dataclass
class ADResult:
    ad: AD
    results: dict[str, ExportResult]
    out_dir: Path
    errors: int
    warnings: int


def _version(repo: Path, snapshot: RepoSnapshot) -> str:
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return f"content {snapshot.content_hash[:12]}"
    if head.returncode != 0:
        return f"content {snapshot.content_hash[:12]}"
    return head.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def _facts(snapshot: RepoSnapshot, sections: dict[str, Any], out: Path) -> list[Fact]:
    """The static run just filled the cache, so this is a cheap read."""
    store = FactStore(out / FACTS_FILE)
    try:
        return collect_facts(snapshot, load_protocol_config(sections.get("protocol")), store).facts
    finally:
        store.close()


def generate_ad(
    repo: Path,
    out: Path,
    settings: Settings,
    *,
    notations: Sequence[str] = ALL_NOTATIONS,
    client_factory: Callable[[], ChatClient] | None = None,
    compile_db: Path | None = None,
    progress: Progress | None = None,
) -> ADResult:
    unknown = sorted(set(notations) - set(ALL_NOTATIONS))
    if unknown:
        raise ConfigError(
            f"unknown notation(s) {', '.join(unknown)}; use {', '.join(ALL_NOTATIONS)}"
        )
    sections = load_sections(repo)
    config = load_ad_config(sections)
    outcome = run_analysis(
        repo,
        out,
        settings,
        client_factory=client_factory,
        mode="static",
        compile_db=compile_db,
        progress=progress,
    )
    snapshot = build_snapshot(
        repo, settings.include, (*settings.exclude, *_excluded_output(repo, out))
    )
    ad = build_ad(outcome.model, snapshot, config, _version(repo, snapshot))
    ctx = ExportContext(repo, snapshot, config, _facts(snapshot, sections, out), {})
    for element in ad.of_type("component", "test"):
        for path in _files_under(element.paths, snapshot):
            ctx.component_of.setdefault(path, element.id)

    results: dict[str, ExportResult] = {}
    for name in EXPORTERS:
        if name not in notations:
            continue
        exporter = EXPORTERS[name]
        if progress:
            progress("ad", f"exporting {name}")
        result = exporter(ad, ctx)
        results[name] = result
        ad.views.extend(result.views)
    ad.correspondences = check_correspondences(ad, results)

    target = out / AD_DIR
    shutil.rmtree(target, ignore_errors=True)
    for result in results.values():
        for relative, text in result.files.items():
            _write_atomic(target / relative, text)
    _write_atomic(target / DOCUMENT, render_document(ad, results))
    _write_atomic(target / "model.json", json.dumps(ad.to_dict(), indent=2, ensure_ascii=False))
    validation: dict[str, Any] = {
        name: {"issues": [asdict(i) for i in result.issues], "files": sorted(result.files)}
        for name, result in results.items()
    }
    validation["correspondences"] = [asdict(c) for c in ad.correspondences]
    _write_atomic(target / "validation.json", json.dumps(validation, indent=2))
    errors = sum(1 for r in results.values() for i in r.issues if i.severity == "error")
    errors += sum(1 for c in ad.correspondences if c.status == "violated")
    warnings = sum(1 for r in results.values() for i in r.issues if i.severity == "warning")
    return ADResult(ad, results, target, errors, warnings)
