from pathlib import Path

import pytest

from archdiag.ad.build import SYSTEM_ID, build_ad
from archdiag.ad.config import ADConfig, load_ad_config
from archdiag.ad.document import render_document
from archdiag.ad.export.c4 import export_c4
from archdiag.ad.model import AD
from archdiag.ad.validate import plain_text
from archdiag.config import ConfigError, Settings
from archdiag.scan.snapshot import build_snapshot
from archdiag.static_pipeline import analyze_static


def ad_for(repo: Path, sections: dict | None = None) -> AD:  # type: ignore[type-arg]
    model = analyze_static(repo, repo / ".archdiag", Settings(), {}).model
    config = load_ad_config(sections or {})
    return build_ad(model, build_snapshot(repo), config, "test")


def test_plain_text_escaping() -> None:
    assert plain_text('a "b" \\ c ${x} {y}\n z') == "a 'b' / c (x) (y) z"
    assert plain_text("x" * 10, limit=5) == "xxxx…"


def test_elements_are_derived_from_the_static_model(mini_repo: Path) -> None:
    ad = ad_for(mini_repo)
    types = {e.id: e.type for e in ad.elements.values()}
    assert types[SYSTEM_ID] == "system"
    assert types["tgt_mini_ecu"] == "container"
    assert types["src_app"] == "component"
    assert types["tests"] == "test"
    assert types["actor_device"] == "device"
    assert types["ext_c_c_standard_library"] == "external"
    assert "person" not in types.values()  # people only come from declarations
    app = ad.elements["src_app"]
    assert app.parent == "tgt_mini_ecu"
    assert app.properties["technology"] == "C"
    assert app.b_ids == ["src_app"]
    container = ad.elements["tgt_mini_ecu"]
    assert container.properties["technology"] == "C"
    depends = next(r for r in ad.relations if r.id == "depends__src_app__src_drivers")
    assert depends.evidence == ["src/app/main.c:1"]
    assert any(r.type == "verifies" and r.source == "tests" for r in ad.relations)


def test_declared_people_and_external_systems(mini_repo: Path) -> None:
    ad = ad_for(
        mini_repo,
        {
            "c4": {
                "people": [
                    {
                        "name": "Service technician",
                        "description": "Flashes the ECU",
                        "uses": ["mini_ecu"],
                        "label": "configures",
                    }
                ],
                "external_systems": [
                    {"name": "Vehicle gateway", "uses": ["src/drivers", "nowhere"]}
                ],
            }
        },
    )
    person = ad.elements["person_service_technician"]
    assert person.type == "person"
    assert any(
        r.source == person.id and r.target == "tgt_mini_ecu" and r.description == "configures"
        for r in ad.relations
    )
    assert any(
        r.source == "extsys_vehicle_gateway" and r.target == "src_drivers" for r in ad.relations
    )
    assert any("'nowhere'" in i for i in ad.inconsistencies)


def test_c4_files_and_views(mini_repo: Path) -> None:
    result = export_c4(ad_for(mini_repo))
    assert set(result.files) == {
        "c4/context.mmd",
        "c4/context.puml",
        "c4/container.mmd",
        "c4/container.puml",
        "c4/component-tgt_mini_ecu.mmd",
        "c4/component-tgt_mini_ecu.puml",
        "c4/workspace.dsl",
    }
    context = result.files["c4/context.mmd"]
    assert context.startswith("C4Context\n")
    assert 'System(system, "mini-repo"' in context
    assert 'System_Ext(actor_device, "Device / bus"' in context
    assert "src_app" not in context
    container = result.files["c4/container.mmd"]
    assert 'Container(tgt_mini_ecu, "mini_ecu", "C"' in container
    component = result.files["c4/component-tgt_mini_ecu.mmd"]
    assert 'Component(src_app, "src/app", "C"' in component
    assert "Rel(src_app, src_drivers" in component
    puml = result.files["c4/container.puml"]
    assert puml.startswith("@startuml\n!include <C4/C4_Container>")
    assert puml.rstrip().endswith("@enduml")
    dsl = result.files["c4/workspace.dsl"]
    assert "e_src_app = component" in dsl
    assert "e_src_app -> e_src_drivers" in dsl
    assert dsl.count("{") == dsl.count("}")
    assert [v.viewpoint_id for v in result.views] == ["c4-context", "c4-container", "c4-component"]
    assert {"src_app", "src_drivers", "src_hal"} <= result.covered
    assert [i for i in result.issues if i.severity == "error"] == []


def test_c4_validation_flags_problems(mini_repo: Path) -> None:
    ad = ad_for(mini_repo)
    for relation in ad.relations:
        if relation.type == "depends":
            relation.description = " "
    ad.elements["src_hal"].name = "src/app"
    result = export_c4(ad)
    messages = [i.message for i in result.issues]
    assert any("needs a description" in m for m in messages)
    assert any("named 'src/app'" in m for m in messages)


def test_document_has_the_42010_sections(mini_repo: Path) -> None:
    ad = ad_for(mini_repo)
    result = export_c4(ad)
    ad.views.extend(result.views)
    text = render_document(ad, {"c4": result})
    for heading in (
        "## 1. Identification",
        "## 2. Stakeholders and concerns",
        "## 3. Viewpoints",
        "## 4. Views",
        "## 5. Correspondences",
        "## 6. Architecture decisions",
        "## 7. Known inconsistencies",
        "## 8. Appendix: traceability",
    ):
        assert heading in text
    assert "Draft work product" in text
    assert "```mermaid\nC4Context" in text
    assert "No architecture decision has been recorded" in text
    assert "`src/app/main.c:1`" in text


def test_adr_files_become_decisions(mini_repo: Path) -> None:
    adr = mini_repo / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-use-can.md").write_text(
        "# Use CAN for device access\n\nStatus: accepted\n\nThe ECU only exposes CAN.\n"
    )
    ad = ad_for(
        mini_repo,
        {"decisions": [{"id": "D1", "title": "Layered firmware", "affects": ["src/app"]}]},
    )
    ids = {d.id: d for d in ad.decisions}
    assert ids["D1"].affected == ["src_app"]
    assert ids["0001-use-can"].title == "Use CAN for device access"
    assert ids["0001-use-can"].status == "accepted"
    assert ids["0001-use-can"].source == "docs/adr/0001-use-can.md"


@pytest.mark.parametrize(
    ("sections", "fragment"),
    [
        ({"c4": {"people": [{"description": "x"}]}}, "name"),
        ({"safety": {"components": [{"name": "x", "asil": "E"}]}}, "asil"),
        (
            {
                "concerns": [{"id": "c", "text": "t"}],
                "stakeholders": [{"id": "s", "name": "S", "concerns": ["nope"]}],
            },
            "unknown concern",
        ),
        ({"autosar": {"layers": {"x": "kernel"}}}, "unknown layer"),
        ({"sysml": {"validator": 3}}, "validator"),
    ],
)
def test_invalid_ad_config(sections: dict, fragment: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(ConfigError, match=fragment):
        load_ad_config(sections)


def test_empty_config_defaults() -> None:
    assert load_ad_config({}) == ADConfig()
