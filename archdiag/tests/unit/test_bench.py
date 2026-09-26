import json
from pathlib import Path

import pytest

from archdiag.bench import format_report, load_expected, score
from archdiag.config import ConfigError
from archdiag.model.schema import ArchModel
from archdiag.model.schema_v2 import ArchModelV2, upgrade_v1
from tests.fakes import GOOD_MODEL

EXPECTED = Path(__file__).parents[2] / "benchmarks" / "mini-repo.expected.toml"
FILES = ["src/app/main.c", "src/drivers/can/can_driver.c", "src/drivers/can/can_driver.h",
         "src/hal/gpio.c", "src/hal/gpio.h", "build/main.o"]
KNOWN = frozenset({"src", "src/app", "src/drivers", "src/drivers/can", "src/hal", "build", *FILES})


def model(**overrides: object) -> ArchModelV2:
    data = json.loads(json.dumps(GOOD_MODEL))
    data.update(overrides)
    return upgrade_v1(ArchModel.model_validate(data))


def test_perfect_model_scores_one() -> None:
    report = score(model(), load_expected(EXPECTED), KNOWN, FILES)
    assert report.component_recall == 1.0
    assert report.relation_recall == 1.0
    assert report.path_validity == 1.0
    assert report.junk_nodes == []


def test_missing_component_lowers_recall() -> None:
    nodes = [n for n in GOOD_MODEL["nodes"] if n["id"] != "gpio_hal"]
    edges = [e for e in GOOD_MODEL["edges"] if e["to"] != "gpio_hal"]
    report = score(model(nodes=nodes, edges=edges), load_expected(EXPECTED), KNOWN, FILES)
    assert report.component_recall == pytest.approx(2 / 3)
    assert report.missing_components == ["gpio"]
    assert report.relation_recall == 0.5
    assert report.missing_relations == [("app", "gpio")]


def test_junk_nodes_and_invalid_paths_are_counted() -> None:
    nodes = json.loads(json.dumps(GOOD_MODEL["nodes"]))
    nodes.append({"id": "objs", "label": "Objects", "kind": "x", "paths": ["build"]})
    nodes[0]["paths"] = ["src/app", "src/nowhere"]
    report = score(model(nodes=nodes), load_expected(EXPECTED), KNOWN, FILES)
    assert report.junk_nodes == ["objs"]
    assert report.path_validity == pytest.approx(4 / 5)


def test_format_report_is_a_table() -> None:
    text = format_report(score(model(), load_expected(EXPECTED), KNOWN, FILES))
    assert "component recall" in text
    assert "100.0%" in text


def test_invalid_expected_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('[[components]]\nname = "x"\n')
    with pytest.raises(ConfigError, match="paths"):
        load_expected(bad)
    bad.write_text('[[relations]]\nfrom = "a"\nto = "b"\n')
    with pytest.raises(ConfigError, match="unknown component"):
        load_expected(bad)


def test_coarser_and_finer_splits_are_credited() -> None:
    coarse = model(nodes=[
        {"id": "firmware", "label": "Firmware", "kind": "x", "paths": ["src"]},
    ], edges=[])
    report = score(coarse, load_expected(EXPECTED), KNOWN, FILES)
    assert report.component_recall == 1.0  # one node holds every component
    assert report.relation_recall == 0.0  # but it cannot show relations between them

    fine = model(nodes=[
        {"id": "app", "label": "A", "kind": "x", "paths": ["src/app"]},
        {"id": "can_c", "label": "C", "kind": "x", "paths": ["src/drivers/can/can_driver.c"]},
        {"id": "can_h", "label": "H", "kind": "x", "paths": ["src/drivers/can/can_driver.h"]},
        {"id": "hal", "label": "G", "kind": "x", "paths": ["src/hal"]},
    ], edges=[{"from": "app", "to": "can_h"}, {"from": "app", "to": "hal"}])
    report = score(fine, load_expected(EXPECTED), KNOWN, FILES)
    assert report.component_recall == 1.0
    assert report.relation_recall == 1.0
