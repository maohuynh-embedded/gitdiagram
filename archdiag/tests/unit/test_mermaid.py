from typing import Any

import pytest

from archdiag.model.schema import ArchModel
from archdiag.render.explanation import render_explanation
from archdiag.render.mermaid import compile_mermaid, escape_label


def build(**overrides: Any) -> ArchModel:
    data: dict[str, Any] = {
        "title": "Mini",
        "groups": [
            {"id": "firmware", "label": "Firmware"},
            {"id": "drivers", "label": "Drivers", "parent": "firmware"},
            {"id": "empty", "label": "Empty"},
        ],
        "nodes": [
            {
                "id": "app",
                "label": "App",
                "kind": "application",
                "group": "firmware",
                "paths": ["src/app"],
                "description": "Main loop",
            },
            {
                "id": "can",
                "label": "CAN driver",
                "kind": "driver",
                "group": "drivers",
                "paths": ["src/drivers/can"],
                "description": "Frames",
            },
            {"id": "tool", "label": "Flasher", "kind": "tool", "paths": [], "description": ""},
        ],
        "edges": [
            {"from": "app", "to": "can", "label": "receives frames"},
            {"from": "tool", "to": "app"},
        ],
        "explanation": "App reads CAN.\n\nThe flasher updates it.",
    }
    data.update(overrides)
    return ArchModel.model_validate(data)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('say "hi"', "say #34;hi#34;"),
        ("a[b]{c}", "a#91;b#93;#123;c#125;"),
        ("50% %% x", "50#37; #37;#37; x"),
        ("a|b;c#d", "a#124;b#59;c#35;d"),
        ("<script>alert(1)</script>", "#60;script#62;alert(1)#60;/script#62;"),
        ("line\nbreak\ttab", "line break tab"),
        ("Bộ điều khiển CAN", "Bộ điều khiển CAN"),
    ],
)
def test_escape_label(text: str, expected: str) -> None:
    assert escape_label(text) == expected


def test_escape_label_caps_length() -> None:
    assert escape_label("x" * 200, max_len=10) == "x" * 9 + "…"


def test_compile_emits_nested_subgraphs_nodes_and_edges() -> None:
    lines = compile_mermaid(build()).splitlines()
    assert lines[1] == "flowchart TD"
    assert '  subgraph g_firmware["Firmware"]' in lines
    assert '    subgraph g_drivers["Drivers"]' in lines
    assert '      n_can["CAN driver"]' in lines
    assert '    n_app["App"]' in lines
    assert '  n_tool["Flasher"]' in lines
    assert '  n_app -->|"receives frames"| n_can' in lines
    assert "  n_tool --> n_app" in lines
    # Groups first, then ungrouped nodes, then edges.
    assert lines.index('  subgraph g_firmware["Firmware"]') < lines.index('  n_tool["Flasher"]')
    assert lines.index('  n_tool["Flasher"]') < lines.index("  n_tool --> n_app")


def test_empty_groups_are_skipped() -> None:
    assert "g_empty" not in compile_mermaid(build())


def test_output_is_deterministic() -> None:
    assert compile_mermaid(build()) == compile_mermaid(build())


def test_output_never_contains_links_or_scripts() -> None:
    hostile = build(
        nodes=[
            {
                "id": "evil",
                "label": 'x"] click evil href "javascript:alert(1)',
                "kind": "x",
                "paths": [],
                "description": "",
            },
        ],
        edges=[],
        groups=[],
    )
    text = compile_mermaid(hostile)
    body = "\n".join(line for line in text.splitlines() if not line.startswith("%%"))
    assert "x#34;#93; click" in body
    assert '"javascript' not in body
    for line in body.splitlines():
        assert not line.strip().startswith(("click", "href", "style", "classDef"))


def test_explanation_lists_components() -> None:
    text = render_explanation(build())
    assert text.startswith("# Mini\n")
    assert "App reads CAN." in text
    assert "- **CAN driver** (driver) — `src/drivers/can` — Frames" in text
    assert "- **Flasher** (tool)" in text
