from pathlib import Path

import pytest

from archdiag.ad.build import build_ad
from archdiag.ad.config import load_ad_config
from archdiag.ad.export.sysml import Port, build_sysml, check_sysml, export_sysml, name
from archdiag.ad.model import AD
from archdiag.ad.validate import ExportResult
from archdiag.config import Settings
from archdiag.scan.snapshot import build_snapshot
from archdiag.static_pipeline import analyze_static


def ad_for(repo: Path) -> AD:
    model = analyze_static(repo, repo / ".archdiag", Settings(), {}).model
    return build_ad(model, build_snapshot(repo), load_ad_config({}), "test")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CanDriver", "CanDriver"),
        ("part", "'part'"),
        ("src/app", "'src/app'"),
        ("it's", "'it\\'s'"),
        ("a\\b", "'a\\\\b'"),
        ("Bộ điều khiển", "'Bộ điều khiển'"),
    ],
)
def test_names_are_quoted_when_needed(text: str, expected: str) -> None:
    assert name(text) == expected


def test_mini_repo_model(mini_repo: Path) -> None:
    result = export_sysml(ad_for(mini_repo))
    text = result.files["sysml/model.sysml"]
    assert text.count("{") == text.count("}")
    assert "package 'mini-repo' {" in text
    assert "part def 'src/app' {" in text
    assert "port can_message_out : 'can message port';" in text
    assert "port can_message_in : ~'can message port';" in text
    assert "connect src_drivers.can_message_out to src_app.can_message_in;" in text
    assert "dependency from 'src/app' to 'src/hal';" in text
    assert "part system : System;" in text
    assert not [i for i in result.issues if i.severity == "error"]
    assert {"src_app", "src_drivers", "src_hal"} <= result.covered
    assert "tests" not in result.covered


def test_checker_finds_missing_defs_and_wrong_directions(mini_repo: Path) -> None:
    model = build_sysml(ad_for(mini_repo))
    drivers = model.part_defs["src_drivers"]
    drivers.ports.append(Port("ghost", "undefined port", False))
    model.connects.append(("src_app", "can_message_in", "src_drivers", "can_message_in"))
    model.dependencies.append(("src_app", "nowhere"))
    result = ExportResult("sysml")
    check_sysml(model, result)
    messages = " ".join(i.message for i in result.issues)
    assert "undefined port def 'undefined port'" in messages
    assert "must join an out port to a conjugated port" in messages
    assert "dependency src_app → nowhere" in messages


def test_system_name_does_not_clash_with_a_component(mini_repo: Path) -> None:
    ad = ad_for(mini_repo)
    ad.elements["src_hal"].name = "System"
    text = export_sysml(ad).files["sysml/model.sysml"]
    assert "part def System {" in text
    assert "part def System_ {" in text
    assert "part system : System_;" in text


def test_external_validator_is_used(mini_repo: Path, tmp_path: Path) -> None:
    ok = tmp_path / "ok.sh"
    ok.write_text("#!/bin/sh\ngrep -q 'part def' \"$1\"\n")
    ok.chmod(0o755)
    passed = export_sysml(ad_for(mini_repo), f"{ok} {{file}}")
    assert any("passed the external" in i.message for i in passed.issues)
    bad = tmp_path / "bad.sh"
    bad.write_text("#!/bin/sh\necho 'syntax error at line 3'\nexit 1\n")
    bad.chmod(0o755)
    failed = export_sysml(ad_for(mini_repo), str(bad))
    assert any(
        i.severity == "error" and "syntax error at line 3" in i.message for i in failed.issues
    )
