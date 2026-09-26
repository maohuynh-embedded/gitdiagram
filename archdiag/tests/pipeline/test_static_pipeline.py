import json
from pathlib import Path
from typing import Any

import pytest

from archdiag.config import ConfigError, Settings
from archdiag.model.schema_v2 import load_model
from archdiag.static_pipeline import analyze_static
from tests.conftest import MakeTree
from tests.fakes import FakeClient

SETTINGS = Settings()


def edges(model: Any) -> set[tuple[str, str, str]]:
    return {(e.source, e.target, e.kind) for e in model.edges}


def test_mini_repo_model_is_faithful_to_the_code(mini_repo: Path) -> None:
    out = mini_repo / ".archdiag"
    result = analyze_static(mini_repo, out, SETTINGS, {})
    model = result.model

    components = {n.id: n for n in model.nodes if n.level == "component"}
    assert set(components) == {"src_app", "src_drivers", "src_hal", "tests"}
    assert components["tests"].kind == "test"
    assert {("src_app", "src_drivers", "depends"), ("src_app", "src_hal", "depends"),
            ("tests", "src_hal", "test"), ("src_drivers", "src_app", "message"),
            ("src_drivers", "actor_device", "message")} <= edges(model)
    # nothing the code does not show
    assert ("src_hal", "src_app", "depends") not in edges(model)
    assert ("src_drivers", "src_app", "depends") not in edges(model)
    for edge in model.edges:
        for evidence in edge.evidence:
            assert (mini_repo / evidence.file).is_file()

    assert [v.id for v in model.views] == ["system", "dependencies", "interfaces"]
    assert (out / "views" / "dependencies.mmd").is_file()
    assert (out / "diagram.mmd").read_text() == (out / "views" / "dependencies.mmd").read_text()
    assert load_model((out / "model.json").read_text()).mode == "static"
    run = json.loads((out / "run.json").read_text())
    assert run["mode"] == "static"
    assert run["annotated"] is False
    assert "_No written overview" in (out / "explanation.md").read_text()


def test_facts_are_cached_between_runs(mini_repo: Path) -> None:
    out = mini_repo / ".archdiag"
    analyze_static(mini_repo, out, SETTINGS, {})
    second = analyze_static(mini_repo, out, SETTINGS, {})
    assert second.stats["parsed"] == 0
    rebuilt = analyze_static(mini_repo, out, SETTINGS, {}, rebuild=True)
    assert rebuilt.stats["parsed"] == 6


def test_declared_architecture_reports_violations(mini_repo: Path) -> None:
    (mini_repo / "src" / "hal" / "gpio.c").write_text(
        '#include "gpio.h"\n#include "../app/app_state.h"\n'
    )
    (mini_repo / "src" / "app" / "app_state.h").write_text("int app_state;\n")
    sections = {"architecture": {
        "layers": ["application", "hal"],
        "components": [
            {"name": "app", "layer": "application", "paths": ["src/app/**"]},
            {"name": "hal", "layer": "hal", "paths": ["src/hal/**", "src/drivers/**"]},
            {"name": "tests", "paths": ["tests/**"]},
        ],
    }}
    model = analyze_static(mini_repo, mini_repo / ".archdiag", SETTINGS, sections).model
    rules = sorted((v.rule, v.source, v.target) for v in model.violations)
    assert ("layer-order", "hal", "app") in rules
    assert ("cycle", "app", "hal") in rules
    view = (mini_repo / ".archdiag" / "views" / "dependencies.mmd").read_text()
    assert "linkStyle" in view and "stroke:#d1242f" in view


def test_invalid_declaration_is_a_config_error(mini_repo: Path) -> None:
    with pytest.raises(ConfigError):
        analyze_static(mini_repo, mini_repo / ".archdiag", SETTINGS,
                       {"architecture": {"layers": ["a", "a"]}})


def test_annotation_labels_components_and_edges_only(mini_repo: Path) -> None:
    replies = []
    # One reply per component (src_app, src_drivers, src_hal) then the system overview.
    for label in ("Application loop", "CAN driver", "GPIO HAL"):
        replies.append(json.dumps({
            "label": label, "description": f"{label} description.",
            "responsibilities": ["one"],
            "edge_labels": {"src_app__depends__src_drivers": "reads frames",
                            "ghost__depends__x": "ignored"},
        }))
    replies.append(json.dumps({"title": "Mini ECU firmware", "explanation": "Overview text."}))
    client = FakeClient(replies)
    out = mini_repo / ".archdiag"

    model = analyze_static(mini_repo, out, SETTINGS, {}, client=client).model

    labels = {n.id: n.label for n in model.nodes if n.level == "component"}
    assert labels["src_app"] == "Application loop"
    assert labels["tests"] == "tests"
    app_to_drivers = next(e for e in model.edges if e.id == "src_app__depends__src_drivers")
    assert app_to_drivers.label == "reads frames"
    assert model.title == "Mini ECU firmware"
    assert model.explanation == "Overview text."
    assert len(model.edges) == len(analyze_static(mini_repo, out, SETTINGS, {}).model.edges)

    # Everything is cached now: a second annotated run makes no LLM call.
    again = analyze_static(mini_repo, out, SETTINGS, {}, client=FakeClient([]))
    assert again.model.title == "Mini ECU firmware"
    assert again.stats["llm_calls"] == 0


def test_annotation_failure_keeps_static_labels(mini_repo: Path) -> None:
    client = FakeClient(["nope"] * 20)
    result = analyze_static(mini_repo, mini_repo / ".archdiag", SETTINGS, {}, client=client)
    labels = {n.id: n.label for n in result.model.nodes if n.level == "component"}
    assert labels["src_app"] == "src/app"
    assert any("Annotation of src_app failed" in w for w in result.warnings)


def test_large_repositories_collapse_to_layers(tmp_path: Path, make_tree: MakeTree) -> None:
    files: dict[str, str | bytes] = {}
    for layer in range(4):
        for module in range(18):
            base = f"layer{layer}/mod{module}"
            files[f"{base}/a.c"] = f'#include "../../layer{(layer + 1) % 4}/mod0/a.h"\n'
            files[f"{base}/a.h"] = "int x;\n"
    repo = make_tree(tmp_path / "big", files)
    model = analyze_static(repo, repo / ".archdiag", SETTINGS, {}).model
    top = model.view("dependencies")
    assert top is not None
    assert top.level == "layer"
    assert top.collapsed_from == "component"
    assert len(top.node_ids) == 4
    children = [v for v in model.views if v.parent == "dependencies"]
    assert len(children) == 4
    assert all(len(v.node_ids) <= 60 for v in model.views)
