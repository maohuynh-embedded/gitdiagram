import json
from typing import Any

import pytest

from archdiag.model.schema import MAX_NODES, ArchModel, llm_json_schema
from archdiag.model.validate import normalize_path, validate_raw

KNOWN = frozenset({"src", "src/app", "src/app/main.c", "src/hal", "src/hal/gpio.c"})


def model_dict(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "Mini",
        "groups": [{"id": "firmware", "label": "Firmware", "parent": None}],
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
                "id": "gpio",
                "label": "GPIO",
                "kind": "hal",
                "group": "firmware",
                "paths": ["src/hal/gpio.c"],
                "description": "Pins",
            },
        ],
        "edges": [{"from": "app", "to": "gpio", "label": "drives", "kind": "uses"}],
        "explanation": "App drives GPIO.",
    }
    data.update(overrides)
    return data


def raw(**overrides: Any) -> str:
    return json.dumps(model_dict(**overrides))


def test_valid_model_passes() -> None:
    result = validate_raw(raw(), KNOWN)
    assert result.ok
    assert result.model is not None
    assert result.model.edges[0].source == "app"
    assert result.warnings == ()


def test_markdown_fences_are_stripped() -> None:
    assert validate_raw(f"```json\n{raw()}\n```", KNOWN).ok


def test_invalid_json_reports_position() -> None:
    result = validate_raw('{"title": ', KNOWN)
    assert not result.ok
    assert "not valid JSON" in result.errors[0]
    assert "line 1" in result.errors[0]


def test_schema_errors_carry_location() -> None:
    data = model_dict()
    del data["nodes"][0]["label"]
    result = validate_raw(json.dumps(data), KNOWN)
    assert not result.ok
    assert any("nodes.0.label" in error for error in result.errors)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda d: d["nodes"][0].update(id="App-1"), "invalid id"),
        (lambda d: d["nodes"][1].update(id="app"), "duplicate id 'app'"),
        (lambda d: d["nodes"][0].update(group="missing"), "unknown group 'missing'"),
        (lambda d: d["edges"].append({"from": "app", "to": "ghost"}), "unknown node 'ghost'"),
        (lambda d: d["edges"].append({"from": "app", "to": "app"}), "points to itself"),
        (
            lambda d: d["groups"].append({"id": "firmware2", "label": "X", "parent": "nowhere"}),
            "unknown parent 'nowhere'",
        ),
        (lambda d: d["nodes"][0].update(label="  "), "empty label"),
    ],
)
def test_structural_errors(mutate: Any, fragment: str) -> None:
    data = model_dict()
    mutate(data)
    result = validate_raw(json.dumps(data), KNOWN)
    assert not result.ok
    assert any(fragment in error for error in result.errors), result.errors


def test_group_parent_cycle_is_reported() -> None:
    groups = [
        {"id": "a", "label": "A", "parent": "b"},
        {"id": "b", "label": "B", "parent": "a"},
    ]
    result = validate_raw(
        raw(
            groups=groups,
            nodes=[
                {
                    "id": "app",
                    "label": "App",
                    "kind": "x",
                    "group": "a",
                    "paths": [],
                    "description": "",
                },
            ],
            edges=[],
        ),
        KNOWN,
    )
    assert any("cycle" in error for error in result.errors)


def test_node_limit_is_enforced() -> None:
    nodes = [
        {"id": f"n{i}", "label": f"N{i}", "kind": "x", "paths": [], "description": ""}
        for i in range(MAX_NODES + 1)
    ]
    result = validate_raw(raw(groups=[], nodes=nodes, edges=[]), KNOWN)
    assert any("too many nodes" in error for error in result.errors)


def test_unknown_paths_alone_are_repaired_with_a_warning() -> None:
    data = model_dict()
    data["nodes"][0]["paths"] = ["./src/app/", "src/imaginary"]
    result = validate_raw(json.dumps(data), KNOWN)
    assert result.ok
    assert result.model is not None
    assert result.model.nodes[0].paths == ["src/app"]
    assert any("src/imaginary" in warning for warning in result.warnings)


def test_unknown_paths_are_reported_with_structural_errors() -> None:
    data = model_dict()
    data["nodes"][0]["paths"] = ["src/imaginary"]
    data["edges"].append({"from": "app", "to": "ghost"})
    result = validate_raw(json.dumps(data), KNOWN)
    assert not result.ok
    assert any("src/imaginary" in error for error in result.errors)


def test_duplicate_edges_are_removed_with_a_warning() -> None:
    data = model_dict()
    data["edges"].append(dict(data["edges"][0]))
    result = validate_raw(json.dumps(data), KNOWN)
    assert result.ok
    assert result.model is not None
    assert len(result.model.edges) == 1
    assert any("duplicate edge" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("given", "expected"),
    [("./src/app/", "src/app"), ("src\\hal\\gpio.c", "src/hal/gpio.c"), ("  src ", "src")],
)
def test_normalize_path(given: str, expected: str) -> None:
    assert normalize_path(given) == expected


def test_llm_schema_has_no_references_and_uses_aliases() -> None:
    schema = llm_json_schema()
    text = json.dumps(schema)
    assert "$ref" not in text
    assert "$defs" not in text
    edge_props = schema["properties"]["edges"]["items"]["properties"]
    assert {"from", "to"} <= set(edge_props)


def test_model_serialises_with_aliases() -> None:
    model = ArchModel.model_validate(model_dict())
    dumped = json.loads(model.to_json())
    assert dumped["edges"][0]["from"] == "app"
    assert dumped["schema_version"] == 1
