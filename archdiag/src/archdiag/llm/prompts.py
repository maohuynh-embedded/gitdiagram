"""Prompts for the LLM-only analysis path."""

from __future__ import annotations

from collections.abc import Sequence

from archdiag.context.select import Evidence
from archdiag.llm.client import Message

PROMPT_VERSION = "a1"

SYSTEM_PROMPT = """\
You are a senior software architect documenting an existing code base.
You receive a filtered directory tree and excerpts of selected files. Produce a component-level
architecture model of the repository as one JSON object.

Rules:
- Model architectural components (layers, subsystems, drivers, protocol stacks, services,
  applications, tools), not functions. A single file is a node only when it is a component on
  its own, such as the program entry point.
- Aim for 8 to 30 nodes. Use groups for layers or subsystems; a group may nest through "parent".
- Every "id" is lowercase snake_case: a letter, then letters, digits or "_", at most 48
  characters. Ids are unique across groups and nodes.
- "paths" lists the repository paths that implement a node. Copy them exactly from the tree:
  relative, "/"-separated, no leading "./". Prefer directories over single files. Never invent
  a path.
- "edges" point from the dependent component to the component it uses ("from" uses "to").
  Give each edge a short label of 2 to 5 words, such as "sends CAN frames" or "reads config".
  Set "kind" to uses, calls, data, event, owns or configures.
- A node "kind" is one word: application, layer, service, driver, protocol, hal, library, ui,
  tool, test or config.
- Base every claim on the tree and the excerpts. Leave out anything you are unsure of.
- Ignore build outputs, vendored third-party code and generated files.
- "explanation" is {language} prose of 150 to 400 words: what the system does, its main parts,
  and how data and control flow between them. Name the directories you rely on.
- Reply with the JSON object only: no markdown fences, no commentary.

Shape:
{"title": str,
 "groups": [{"id": str, "label": str, "parent": str | null}],
 "nodes": [{"id": str, "label": str, "kind": str, "group": str | null, "paths": [str],
            "description": str}],
 "edges": [{"from": str, "to": str, "label": str, "kind": str}],
 "explanation": str}
"""


def build_messages(repo_name: str, evidence: Evidence, language: str) -> list[Message]:
    parts = [
        f"Repository: {repo_name}",
        "",
        "## Directory tree (filtered)",
        evidence.tree_summary,
        "",
        "## File excerpts",
    ]
    for excerpt in evidence.excerpts:
        parts.extend([f"<<<FILE {excerpt.path}>>>", excerpt.text, "<<<END FILE>>>"])
    parts.append("")
    note = (
        f"Excerpts cover {len(evidence.excerpts)} of {evidence.candidates_total} candidate files."
    )
    if evidence.truncated:
        note += " The context was truncated; infer the rest of the structure from the tree."
    parts.extend([note, "Produce the architecture model JSON now."])
    return [
        {"role": "system", "content": SYSTEM_PROMPT.replace("{language}", language)},
        {"role": "user", "content": "\n".join(parts)},
    ]


def retry_message(errors: Sequence[str]) -> Message:
    listed = "\n".join(f"- {error}" for error in errors[:25])
    return {
        "role": "user",
        "content": (
            "Your previous JSON was rejected for these reasons:\n"
            f"{listed}\n"
            "Return the complete corrected JSON object only."
        ),
    }
