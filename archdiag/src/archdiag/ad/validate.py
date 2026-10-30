"""Issues found while exporting or validating a notation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from archdiag.ad.model import ADView

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class Issue:
    notation: str
    severity: str  # error | warning | note
    message: str
    element: str | None = None


@dataclass
class ExportResult:
    notation: str
    files: dict[str, str] = field(default_factory=dict)
    views: list[ADView] = field(default_factory=list)
    covered: set[str] = field(default_factory=set)
    issues: list[Issue] = field(default_factory=list)

    def error(self, message: str, element: str | None = None) -> None:
        self.issues.append(Issue(self.notation, "error", message, element))

    def warning(self, message: str, element: str | None = None) -> None:
        self.issues.append(Issue(self.notation, "warning", message, element))

    def note(self, message: str, element: str | None = None) -> None:
        self.issues.append(Issue(self.notation, "note", message, element))


def plain_text(text: str, limit: int = 160) -> str:
    """Text safe inside double-quoted strings of C4/PlantUML/Structurizr/Mermaid C4."""
    cleaned = _CONTROL.sub(" ", text)
    cleaned = cleaned.replace('"', "'").replace("\\", "/").replace("$", "")
    cleaned = cleaned.replace("{", "(").replace("}", ")")
    cleaned = " ".join(cleaned.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"
