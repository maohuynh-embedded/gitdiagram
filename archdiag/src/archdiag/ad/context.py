"""What exporters may read besides the AD: the snapshot, declarations and raw facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from archdiag.ad.config import ADConfig
from archdiag.facts.model import Fact
from archdiag.scan.snapshot import RepoSnapshot


@dataclass
class ExportContext:
    repo: Path
    snapshot: RepoSnapshot
    config: ADConfig
    facts: list[Fact] = field(default_factory=list)
    component_of: dict[str, str] = field(default_factory=dict)  # file → component element id
