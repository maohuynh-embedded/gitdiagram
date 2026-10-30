"""ArchModel → explanation.md."""

from __future__ import annotations

from archdiag.model.schema import ArchModel


def render_explanation(model: ArchModel) -> str:
    lines = [f"# {model.title}", "", model.explanation.strip(), "", "## Components", ""]
    for node in model.nodes:
        parts = [f"- **{node.label}** ({node.kind})"]
        if node.paths:
            parts.append(", ".join(f"`{path}`" for path in node.paths))
        if node.description.strip():
            parts.append(node.description.strip())
        lines.append(" — ".join(parts))
    return "\n".join(lines) + "\n"
