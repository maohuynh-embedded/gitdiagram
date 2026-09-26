import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from archdiag.ad.build import build_ad
from archdiag.ad.config import load_ad_config
from archdiag.ad.export.archimate import (
    NS,
    SCHEMA_DIR,
    XSI,
    export_archimate,
    is_valid,
    validate_xsd,
)
from archdiag.ad.model import AD, Relation
from archdiag.config import Settings
from archdiag.scan.snapshot import build_snapshot
from archdiag.static_pipeline import analyze_static

NCNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def ad_for(repo: Path) -> AD:
    model = analyze_static(repo, repo / ".archdiag", Settings(), {}).model
    return build_ad(model, build_snapshot(repo), load_ad_config({}), "test")


def parse(xml: str) -> ET.Element:
    return ET.fromstring(xml.split("\n", 1)[1])


def types(root: ET.Element) -> dict[str, str]:
    return {
        e.get("identifier") or "": e.get(f"{{{XSI}}}type") or ""
        for e in root.iter(f"{{{NS}}}element")
    }


def test_elements_relationships_and_views(mini_repo: Path) -> None:
    result = export_archimate(ad_for(mini_repo))
    root = parse(result.files["archimate/model.xml"])
    kinds = types(root)
    assert kinds["id-src_app"] == "ApplicationComponent"
    assert kinds["id-layer_src"] == "Grouping"
    assert kinds["id-tgt_mini_ecu"] == "Artifact"
    assert kinds["id-ext_c_c_standard_library"] == "SystemSoftware"
    assert kinds["id-actor_device"] == "Device"
    relations = {
        (r.get(f"{{{XSI}}}type"), r.get("source"), r.get("target"))
        for r in root.iter(f"{{{NS}}}relationship")
    }
    # src/app depends on src/drivers → drivers serves app
    assert ("Serving", "id-src_drivers", "id-src_app") in relations
    assert ("Aggregation", "id-layer_src", "id-src_app") in relations
    assert ("Realization", "id-tgt_mini_ecu", "id-src_app") in relations
    assert ("Serving", "id-ext_c_c_standard_library", "id-src_drivers") in relations
    assert any(
        t == "Association" and s == "id-src_drivers" and d == "id-actor_device"
        for t, s, d in relations
    )
    views = [v.find(f"{{{NS}}}name").text for v in root.iter(f"{{{NS}}}view")]  # type: ignore[union-attr]
    assert views == ["Application cooperation", "Technology"]
    assert {"src_app", "src_drivers", "src_hal"} <= result.covered
    assert not [i for i in result.issues if i.severity == "error"]


def test_identifiers_are_unique_ncnames(mini_repo: Path) -> None:
    root = parse(export_archimate(ad_for(mini_repo)).files["archimate/model.xml"])
    identifiers = [e.get("identifier") for e in root.iter() if e.get("identifier")]
    assert len(identifiers) == len(set(identifiers))
    assert all(NCNAME.match(i or "") for i in identifiers)
    references = {
        e.get(a)
        for e in root.iter()
        for a in ("elementRef", "relationshipRef", "source", "target", "identifierRef")
        if e.get(a)
    }
    assert references <= set(identifiers)


def test_nested_group_nodes_have_room_for_children(mini_repo: Path) -> None:
    root = parse(export_archimate(ad_for(mini_repo)).files["archimate/model.xml"])
    for group in root.iter(f"{{{NS}}}node"):
        children = group.findall(f"{{{NS}}}node")
        for child in children:
            assert (
                int(child.get("x", 0)) + int(child.get("w", 0))
                <= int(group.get("x", 0)) + int(group.get("w", 0)) + 20
            )
            assert int(child.get("y", 0)) >= int(group.get("y", 0))


@pytest.mark.parametrize(
    ("source", "relationship", "target", "valid"),
    [
        ("ApplicationComponent", "Serving", "ApplicationComponent", True),
        ("Artifact", "Realization", "ApplicationComponent", True),
        ("Grouping", "Aggregation", "Device", True),
        ("Device", "Association", "ApplicationInterface", True),
        ("SystemSoftware", "Serving", "Artifact", False),
        ("ApplicationComponent", "Realization", "Device", False),
    ],
)
def test_relationship_table(source: str, relationship: str, target: str, valid: bool) -> None:
    assert is_valid(source, relationship, target) is valid


def test_invalid_relationship_is_reported(mini_repo: Path) -> None:
    ad = ad_for(mini_repo)
    # "the library depends on src/app" maps to Serving from an application component to
    # system software, which the ArchiMate relationship table does not allow.
    ad.relations.append(
        Relation("bogus", "depends", "ext_c_c_standard_library", "src_app", "made up")
    )
    result = export_archimate(ad)
    errors = [i for i in result.issues if "not allowed" in i.message]
    assert len(errors) == 1
    assert "Serving from ApplicationComponent to SystemSoftware" in errors[0].message


def test_missing_schemas_are_a_note(mini_repo: Path, tmp_path: Path) -> None:
    result = export_archimate(ad_for(mini_repo))
    result.issues.clear()
    validate_xsd(result, schema_dir=tmp_path)
    assert [i.severity for i in result.issues] == ["note"]
    assert "fetch-schemas" in result.issues[0].message


@pytest.mark.skipif(
    not (SCHEMA_DIR / "archimate3_Diagram.xsd").is_file(),
    reason="run `archdiag fetch-schemas` to enable XSD tests",
)
def test_output_is_valid_against_the_open_group_xsd(mini_repo: Path) -> None:
    pytest.importorskip("lxml")
    result = export_archimate(ad_for(mini_repo))
    assert not [i for i in result.issues if i.severity == "error"], result.issues
    assert any("valid against" in i.message for i in result.issues)


@pytest.mark.skipif(
    not (SCHEMA_DIR / "archimate3_Diagram.xsd").is_file(),
    reason="run `archdiag fetch-schemas` to enable XSD tests",
)
def test_xsd_catches_broken_documents(mini_repo: Path) -> None:
    pytest.importorskip("lxml")
    result = export_archimate(ad_for(mini_repo))
    result.issues.clear()
    result.files["archimate/model.xml"] = result.files["archimate/model.xml"].replace(
        'xsi:type="ApplicationComponent"', 'xsi:type="NotAnArchiMateType"', 1
    )
    validate_xsd(result)
    assert any(i.severity == "error" and i.message.startswith("XSD") for i in result.issues)
