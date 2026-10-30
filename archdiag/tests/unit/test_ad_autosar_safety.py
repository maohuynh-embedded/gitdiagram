import json
import shutil
from pathlib import Path

import pytest

from archdiag.ad.config import RequirementSource
from archdiag.ad.importers.arxml import parse_arxml
from archdiag.ad.importers.requirements import parse_reqif, parse_requirements_csv
from archdiag.ad.importers.xmlsafe import UnsafeXML, parse_xml
from archdiag.ad.pipeline import generate_ad
from archdiag.config import Settings
from tests.conftest import FIXTURES

ARXML = (FIXTURES / "autosar-repo" / "arxml" / "system.arxml").read_text()

REQIF = """<?xml version="1.0" encoding="UTF-8"?>
<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd">
  <CORE-CONTENT><REQ-IF-CONTENT>
    <DATATYPES>
      <DATATYPE-DEFINITION-ENUMERATION IDENTIFIER="dt-asil" LONG-NAME="ASIL">
        <SPECIFIED-VALUES>
          <ENUM-VALUE IDENTIFIER="ev-b" LONG-NAME="ASIL B"/>
          <ENUM-VALUE IDENTIFIER="ev-d" LONG-NAME="ASIL D"/>
        </SPECIFIED-VALUES>
      </DATATYPE-DEFINITION-ENUMERATION>
    </DATATYPES>
    <SPEC-TYPES><SPEC-OBJECT-TYPE IDENTIFIER="t-req" LONG-NAME="Requirement">
      <SPEC-ATTRIBUTES>
        <ATTRIBUTE-DEFINITION-STRING IDENTIFIER="a-id" LONG-NAME="ReqIF.ForeignID"/>
        <ATTRIBUTE-DEFINITION-XHTML IDENTIFIER="a-text" LONG-NAME="ReqIF.Text"/>
        <ATTRIBUTE-DEFINITION-ENUMERATION IDENTIFIER="a-asil" LONG-NAME="ASIL"/>
      </SPEC-ATTRIBUTES>
    </SPEC-OBJECT-TYPE></SPEC-TYPES>
    <SPEC-OBJECTS>
      <SPEC-OBJECT IDENTIFIER="so-1">
        <VALUES>
          <ATTRIBUTE-VALUE-STRING THE-VALUE="SW-REQ-9">
            <DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>a-id</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION>
          </ATTRIBUTE-VALUE-STRING>
          <ATTRIBUTE-VALUE-XHTML>
            <DEFINITION><ATTRIBUTE-DEFINITION-XHTML-REF>a-text</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>
            <THE-VALUE><xhtml:div xmlns:xhtml="http://www.w3.org/1999/xhtml">
              Stop within <xhtml:b>2 s</xhtml:b></xhtml:div></THE-VALUE>
          </ATTRIBUTE-VALUE-XHTML>
          <ATTRIBUTE-VALUE-ENUMERATION>
            <DEFINITION><ATTRIBUTE-DEFINITION-ENUMERATION-REF>a-asil</ATTRIBUTE-DEFINITION-ENUMERATION-REF></DEFINITION>
            <VALUES><ENUM-VALUE-REF>ev-d</ENUM-VALUE-REF></VALUES>
          </ATTRIBUTE-VALUE-ENUMERATION>
        </VALUES>
      </SPEC-OBJECT>
    </SPEC-OBJECTS>
  </REQ-IF-CONTENT></CORE-CONTENT>
</REQ-IF>
"""


def test_arxml_components_ports_interfaces_and_connectors() -> None:
    model = parse_arxml(ARXML)
    assert {n: s.type for n, s in model.swcs.items()} == {
        "SpeedSensor": "SensorActuator",
        "BrakeCtrl": "Application",
        "DiagLogger": "Service",
        "Vehicle": "Composition",
    }
    assert [(p.name, p.kind, p.interface) for p in model.swcs["SpeedSensor"].ports] == [
        ("PpSpeed", "P", "SpeedIf"),
        ("PpSpare", "P", "SpeedIf"),
    ]
    assert model.interfaces["SpeedIf"].kind == "SR"
    assert model.interfaces["SpeedIf"].members == ["value"]
    assert model.interfaces["LogIf"].kind == "CS"
    assert [
        (c.provider_swc, c.provider_port, c.requester_swc, c.requester_port)
        for c in model.connectors
    ] == [("SpeedSensor", "PpSpeed", "BrakeCtrl", "RpSpeed")]
    assert model.warnings == []


@pytest.mark.parametrize(
    "text",
    [
        '<!DOCTYPE lolz [<!ENTITY lol "lol">]><AUTOSAR>&lol;</AUTOSAR>',
        '<AUTOSAR><!ENTITY x SYSTEM "file:///etc/passwd"></AUTOSAR>',
    ],
)
def test_entities_are_refused(text: str) -> None:
    with pytest.raises(UnsafeXML):
        parse_xml(text)
    assert parse_arxml(text).warnings


def test_reqif_import() -> None:
    requirements, warnings = parse_reqif(
        REQIF, RequirementSource("reqif", "req.reqif", "ReqIF.ForeignID", "ReqIF.Text", "ASIL")
    )
    assert warnings == []
    assert [(r.id, r.text, r.asil) for r in requirements] == [("SW-REQ-9", "Stop within 2 s", "D")]


def test_csv_import() -> None:
    text = (FIXTURES / "autosar-repo" / "req" / "requirements.csv").read_text()
    spec = RequirementSource("csv", "req.csv", component_field="component")
    requirements, _ = parse_requirements_csv(text, spec)
    assert [(r.id, r.asil, r.components) for r in requirements] == [
        ("SW-REQ-1", "B", ["swc/speed"]),
        ("SW-REQ-2", "D", []),
        ("SW-REQ-3", "QM", []),
    ]
    _, warnings = parse_requirements_csv("name\nx\n", spec)
    assert warnings == ["req.csv: no 'id' column"]


@pytest.fixture
def autosar_repo(tmp_path: Path) -> Path:
    target = tmp_path / "autosar-repo"
    shutil.copytree(FIXTURES / "autosar-repo", target)
    return target


def messages(result: object, notation: str) -> list[str]:
    return [i.message for i in result.results[notation].issues]  # type: ignore[attr-defined]


def test_arxml_code_differences(autosar_repo: Path) -> None:
    result = generate_ad(
        autosar_repo, autosar_repo / ".archdiag", Settings(), notations=["autosar"]
    )
    found = messages(result, "autosar")
    assert any("swc-unmapped: SWC DiagLogger" in m for m in found)
    assert any(
        "call-undeclared: `Rte_Write_PpGhost_value` in swc/speed/speed.c:13" in m for m in found
    )
    assert any("port-unused: Port SpeedSensor.PpSpare" in m for m in found)
    assert not any("connector-without-flow" in m for m in found)
    swc = (result.out_dir / "autosar" / "swc.mmd").read_text()
    assert 'title: "AUTOSAR-style (derived), not an AUTOSAR model"' in swc
    assert 'swc_swc_speed -->|"SR: SpeedIf"| swc_swc_brake' in swc
    assert "Device" not in swc  # RTE calls are not device traffic
    assert (result.out_dir / "autosar" / "arxml-diff.md").is_file()


def test_safety_checks_and_trace_matrix(autosar_repo: Path) -> None:
    result = generate_ad(autosar_repo, autosar_repo / ".archdiag", Settings(), notations=["safety"])
    found = messages(result, "safety")
    assert any("conflicting ASIL A (archdiag.toml), B (swc/speed/speed.c:6)" in m for m in found)
    assert any(
        m.startswith("ffi-dependency: swc/brake (ASIL D) depends on swc/speed (ASIL B)")
        and "swc/brake/brake.c:3" in m
        for m in found
    )
    assert "asil-missing: swc/logger has no ASIL while others do." in found
    assert "req-unallocated: SW-REQ-3 is not allocated to any component." in found
    assert "req-unverified: no test verifies SW-REQ-1." in found
    assert not any("req-unverified: no test verifies SW-REQ-2" in m for m in found)
    matrix = (result.out_dir / "safety" / "trace-matrix.csv").read_text().splitlines()
    assert matrix[0] == "requirement,text,asil,components,tests,evidence"
    assert matrix[2].startswith("SW-REQ-2,Control brake pressure from wheel speed,D,swc/brake,")
    diagram = (result.out_dir / "safety" / "safety.mmd").read_text()
    assert "swc/speed #183; ASIL B" in diagram
    assert "linkStyle" in diagram
    ad = json.loads((result.out_dir / "model.json").read_text())
    requirement_ids = {e["id"] for e in ad["elements"] if e["type"] == "requirement"}
    assert requirement_ids == {"req_sw_req_1", "req_sw_req_2", "req_sw_req_3"}


def test_all_notations_together_are_consistent(autosar_repo: Path) -> None:
    result = generate_ad(autosar_repo, autosar_repo / ".archdiag", Settings())
    assert result.errors == 0
    statuses = {c.rule_id: c.status for c in result.ad.correspondences}
    assert statuses == {
        "elements-traceable": "ok",
        "c4-archimate": "ok",
        "c4-sysml": "ok",
        "c4-autosar": "ok",
        "safety-allocation": "ok",
    }
    document = (result.out_dir / "architecture-description.md").read_text()
    assert "AUTOSAR-style software components (derived)" in document
    assert "Safety architecture" in document
    validation = json.loads((result.out_dir / "validation.json").read_text())
    assert set(validation) == {"c4", "archimate", "sysml", "autosar", "safety", "correspondences"}
