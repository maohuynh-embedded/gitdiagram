"""Architecture decisions: declared in archdiag.toml or kept as ADR files. Never generated."""

from __future__ import annotations

import re
from pathlib import Path

from archdiag.ad.config import ADConfig
from archdiag.ad.model import Decision

ADR_DIRS = ("docs/adr", "docs/architecture/decisions", "doc/adr", "adr")
_STATUS = re.compile(r"^\s*(?:##\s*)?status\s*[:\n]\s*(\w[\w -]*)", re.I | re.M)
_TITLE = re.compile(r"^#\s+(.+)$", re.M)


def read_adrs(repo: Path) -> list[Decision]:
    decisions: list[Decision] = []
    for directory in ADR_DIRS:
        base = repo / directory
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:20000]
            except OSError:
                continue
            title = _TITLE.search(text)
            status = _STATUS.search(text)
            body = _TITLE.sub("", text, count=1).strip()
            decisions.append(
                Decision(
                    id=path.stem,
                    title=title.group(1).strip() if title else path.stem,
                    status=status.group(1).strip().lower() if status else "unknown",
                    rationale=" ".join(body.split())[:400],
                    source=path.relative_to(repo).as_posix(),
                )
            )
    return decisions


def collect_decisions(repo: Path, config: ADConfig, names: dict[str, str]) -> list[Decision]:
    """names maps a component or container name to its element id."""
    declared = [
        Decision(
            d.id,
            d.title,
            d.status,
            d.rationale,
            [names.get(a, a) for a in d.affects],
            "archdiag.toml",
        )
        for d in config.decisions
    ]
    return declared + read_adrs(repo)
