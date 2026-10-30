"""ArchModel v2 → explanation.md: overview, components, violations, messages."""

from __future__ import annotations

from archdiag.model.schema_v2 import ArchModelV2


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


def render_explanation_v2(model: ArchModelV2) -> str:
    lines = [f"# {model.title}", ""]
    if model.explanation.strip():
        lines += [model.explanation.strip(), ""]
    else:
        lines += ["_No written overview: this run used static analysis only (--no-llm)._", ""]

    components = [n for n in model.nodes if n.level == "component"]
    lines += [
        "## Components",
        "",
        "| Component | Layer | Files | Lines | Paths | Description |",
        "|---|---|---|---|---|---|",
    ]
    for node in sorted(components, key=lambda n: (n.layer or "", n.label)):
        paths = ", ".join(f"`{p}`" for p in node.paths[:4])
        lines.append(
            f"| {_cell(node.label)} | {_cell(node.layer or '')} | {node.metrics.get('files', 0)} "
            f"| {node.metrics.get('lines', 0)} | {paths} | {_cell(node.description)} |"
        )
    lines.append("")

    names = {n.id: n.label for n in model.nodes}
    lines += ["## Violations", ""]
    if not model.violations:
        lines += ["None found.", ""]
    for violation in model.violations:
        where = ", ".join(f"`{e.file}:{e.line}`" for e in violation.evidence[:3])
        lines.append(f"- **{violation.rule}** — {_cell(violation.message)} {where}".rstrip())
    if model.violations:
        lines.append("")

    if model.messages:
        lines += [
            "## Messages",
            "",
            "| Protocol | Name | Id | Senders | Receivers | Confidence |",
            "|---|---|---|---|---|---|",
        ]
        for message in model.messages[:200]:
            ident = f"0x{message.message_id:X}" if message.message_id is not None else ""
            senders = ", ".join(names.get(s, s) for s in message.senders)
            receivers = ", ".join(names.get(r, r) for r in message.receivers)
            lines.append(
                f"| {message.protocol} | {_cell(message.name)} | {ident} | "
                f"{_cell(senders)} | {_cell(receivers)} | {message.confidence} |"
            )
        lines.append("")

    lines += ["## Views", ""]
    lines += [f"- `{v.id}` — {v.title}" for v in model.views]
    return "\n".join(lines) + "\n"
