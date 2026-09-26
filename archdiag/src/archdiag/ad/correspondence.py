"""Correspondence rules between views and notations (42010 §5.7)."""

from __future__ import annotations

from collections.abc import Mapping

from archdiag.ad.model import AD, Correspondence
from archdiag.ad.validate import ExportResult

_SYNTHETIC = {"system", "bus"}


def check_correspondences(ad: AD, results: Mapping[str, ExportResult]) -> list[Correspondence]:
    out: list[Correspondence] = []
    components = {e.id for e in ad.of_type("component")}

    traced = [
        e.id
        for e in ad.elements.values()
        if e.type not in _SYNTHETIC | {"person", "external_system", "layer", "requirement"}
        and not e.b_ids
    ]
    out.append(
        Correspondence(
            "elements-traceable",
            "Every element derived from code traces back to the static model.",
            "violated" if traced else "ok",
            f"{len(traced)} element(s) without a source node" if traced else "",
            traced[:20],
        )
    )

    def compare(rule: str, description: str, a: str, b: str) -> None:
        if a not in results or b not in results:
            out.append(
                Correspondence(rule, description, "not-applicable", f"needs both {a} and {b}")
            )
            return
        missing = sorted((components & results[a].covered) - results[b].covered)
        out.append(
            Correspondence(
                rule,
                description,
                "violated" if missing else "ok",
                f"{len(missing)} component(s) missing in {b}" if missing else "",
                missing[:20],
            )
        )

    compare(
        "c4-archimate",
        "Every C4 component has an ArchiMate application component.",
        "c4",
        "archimate",
    )
    compare("c4-sysml", "Every C4 component is a SysML part.", "c4", "sysml")
    compare("c4-autosar", "Every C4 component maps to an AUTOSAR-style SWC.", "c4", "autosar")
    if "safety" in results:
        untraced = sorted(components - results["safety"].covered)
        out.append(
            Correspondence(
                "safety-allocation",
                "Every component appears in the safety architecture.",
                "violated" if untraced else "ok",
                f"{len(untraced)} component(s) absent" if untraced else "",
                untraced[:20],
            )
        )
    return out
