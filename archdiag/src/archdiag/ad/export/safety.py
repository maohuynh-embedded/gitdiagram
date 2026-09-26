"""ISO 26262 / ASPICE support: ASIL allocation, freedom-from-interference warnings and the
requirement ↔ component ↔ test trace matrix.

ASIL and requirements are never inferred. They come from [safety] in archdiag.toml, from
@req / @asil / @safety_goal tags in code comments, and from ReqIF or CSV exports. The output
is a draft work product for review, not qualified evidence.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field

from archdiag.ad.config import ASIL_LEVELS
from archdiag.ad.context import ExportContext
from archdiag.ad.importers.requirements import (
    Requirement,
    parse_reqif,
    parse_requirements_csv,
)
from archdiag.ad.model import AD, ADView, Element, Relation
from archdiag.ad.validate import ExportResult
from archdiag.arch.components import snake_id
from archdiag.facts.model import TAG
from archdiag.render.mermaid import escape_label

RANK = {level: index for index, level in enumerate(ASIL_LEVELS)}  # QM < A < B < C < D
COLORS = {"QM": "#9e9e9e", "A": "#4caf50", "B": "#fbc02d", "C": "#fb8c00", "D": "#e53935"}


@dataclass
class SafetyModel:
    asil: dict[str, str] = field(default_factory=dict)  # component → ASIL
    asil_sources: dict[str, dict[str, str]] = field(default_factory=dict)
    requirements: dict[str, Requirement] = field(default_factory=dict)
    allocated: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    verified: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    evidence: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    goals: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))


def _resolve(ad: AD, name: str) -> str | None:
    if name in ad.elements:
        return name
    for element in ad.of_type("component", "test"):
        if name in (element.name, *element.paths):
            return element.id
    return None


def _set_asil(model: SafetyModel, component: str, level: str, source: str) -> None:
    model.asil_sources.setdefault(component, {})[source] = level
    current = model.asil.get(component)
    if current is None or RANK[level] > RANK[current]:
        model.asil[component] = level


def collect(ad: AD, ctx: ExportContext, result: ExportResult) -> SafetyModel:
    model = SafetyModel()

    for declared in ctx.config.safety:
        component = _resolve(ad, declared.name)
        if component is None:
            result.warning(f"[safety] names '{declared.name}', which matches no component.")
            continue
        if declared.asil:
            _set_asil(model, component, declared.asil, "archdiag.toml")
        for req_id in declared.requirements:
            model.requirements.setdefault(req_id, Requirement(req_id, source="archdiag.toml"))
            model.allocated[req_id].add(component)
            model.evidence[req_id].append("archdiag.toml")
        model.goals[component].update(declared.safety_goals)

    for fact in ctx.facts:
        if fact.kind != TAG:
            continue
        component = ctx.component_of.get(fact.file)
        if component is None:
            continue
        tag, value = str(fact.data["tag"]), str(fact.data["value"])
        where = f"{fact.file}:{fact.line}"
        is_test = ad.elements[component].type == "test"
        if tag == "asil" and value.upper() in RANK and not is_test:
            _set_asil(model, component, value.upper(), where)
        elif tag == "req":
            model.requirements.setdefault(value, Requirement(value, source=where))
            (model.verified if is_test else model.allocated)[value].add(component)
            model.evidence[value].append(where)
        elif tag == "safety_goal" and not is_test:
            model.goals[component].add(value)

    for source in ctx.config.requirement_sources:
        try:
            text = (ctx.repo / source.path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result.warning(f"{source.path}: cannot read ({exc})")
            continue
        parse = parse_reqif if source.kind == "reqif" else parse_requirements_csv
        imported, warnings = parse(text, source)
        for warning in warnings:
            result.warning(warning)
        for requirement in imported:
            known = model.requirements.get(requirement.id)
            if known is None or not known.text:
                model.requirements[requirement.id] = requirement
            for name in requirement.components:
                component = _resolve(ad, name)
                if component:
                    model.allocated[requirement.id].add(component)
                else:
                    result.warning(
                        f"{source.path}: requirement {requirement.id} names unknown "
                        f"component '{name}'."
                    )
            if requirement.asil:
                for component in model.allocated[requirement.id]:
                    _set_asil(model, component, requirement.asil, source.path)

    for component, sources in model.asil_sources.items():
        if len(set(sources.values())) > 1:
            detail = ", ".join(f"{level} ({where})" for where, level in sources.items())
            result.warning(
                f"{ad.elements[component].name}: conflicting ASIL {detail}; using "
                f"the highest, {model.asil[component]}.",
                component,
            )
    return model


def check(ad: AD, model: SafetyModel, result: ExportResult) -> None:
    components = [e for e in ad.of_type("component")]
    if model.asil:
        for component in components:
            if component.id not in model.asil:
                result.warning(
                    f"asil-missing: {component.name} has no ASIL while others do.", component.id
                )
    for relation in ad.relations_of("depends", "flows"):
        source_level = model.asil.get(relation.source)
        target_level = model.asil.get(relation.target)
        if source_level is None or target_level is None:
            continue
        if RANK[source_level] > RANK[target_level]:
            where = f" ({', '.join(relation.evidence[:3])})" if relation.evidence else ""
            result.warning(
                f"ffi-dependency: {ad.elements[relation.source].name} (ASIL {source_level}) "
                f"{'depends on' if relation.type == 'depends' else 'receives from'} "
                f"{ad.elements[relation.target].name} (ASIL {target_level}); freedom from "
                f"interference must be argued{where}.",
                relation.id,
            )
    for requirement in sorted(model.requirements):
        if not model.allocated.get(requirement):
            result.warning(f"req-unallocated: {requirement} is not allocated to any component.")
        if not model.verified.get(requirement):
            result.warning(f"req-unverified: no test verifies {requirement}.")
    traced = {c for comps in model.allocated.values() for c in comps}
    for component in components:
        level = model.asil.get(component.id)
        if level and level != "QM" and component.id not in traced:
            result.warning(
                f"component-untraced: {component.name} is ASIL {level} but no "
                "requirement is allocated to it.",
                component.id,
            )


def _add_to_ad(ad: AD, model: SafetyModel) -> None:
    for requirement in model.requirements.values():
        element_id = snake_id(requirement.id, "req_")
        properties = {"asil": requirement.asil or "", "requirement_id": requirement.id}
        ad.elements[element_id] = Element(
            element_id,
            requirement.id,
            "requirement",
            requirement.text,
            properties=properties,
            paths=model.evidence.get(requirement.id, [])[:5],
        )
        for component in sorted(model.allocated.get(requirement.id, set())):
            ad.relations.append(
                Relation(
                    f"satisfies__{component}__{element_id}",
                    "satisfies",
                    component,
                    element_id,
                    "implements",
                )
            )
        for test in sorted(model.verified.get(requirement.id, set())):
            ad.relations.append(
                Relation(
                    f"verifies__{test}__{element_id}", "verifies", test, element_id, "verifies"
                )
            )
    for component, level in model.asil.items():
        ad.elements[component].properties["asil"] = level


def _diagram(ad: AD, model: SafetyModel, result: ExportResult) -> str:
    lines = ["flowchart TD"]
    components = [e for e in ad.of_type("component")]
    for component in components:
        level = model.asil.get(component.id)
        label = escape_label(
            f"{component.name} · ASIL {level}" if level else f"{component.name} · no ASIL"
        )
        lines.append(f'  n_{component.id}["{label}"]')
    ffi: list[int] = []
    index = 0
    ids = {c.id for c in components}
    for relation in ad.relations_of("depends", "flows"):
        if relation.source not in ids or relation.target not in ids:
            continue
        source_level = model.asil.get(relation.source)
        target_level = model.asil.get(relation.target)
        is_ffi = bool(source_level and target_level and RANK[source_level] > RANK[target_level])
        arrow = "-.->" if relation.type == "flows" else "-->"
        label = "FFI?" if is_ffi else ""
        lines.append(
            f'  n_{relation.source} {arrow}|"{label}"| n_{relation.target}'
            if label
            else f"  n_{relation.source} {arrow} n_{relation.target}"
        )
        if is_ffi:
            ffi.append(index)
        index += 1
    for level, color in COLORS.items():
        lines.append(f"  classDef asil{level} fill:{color},stroke:#333,color:#000")
    for component in components:
        level = model.asil.get(component.id)
        if level:
            lines.append(f"  class n_{component.id} asil{level}")
    if ffi:
        lines.append(f"  linkStyle {','.join(map(str, ffi))} stroke:#d1242f,stroke-width:3px")
    return "\n".join(lines) + "\n"


def _trace_matrix(ad: AD, model: SafetyModel) -> tuple[str, str]:
    rows = []
    for requirement_id in sorted(model.requirements):
        requirement = model.requirements[requirement_id]
        components = sorted(ad.elements[c].name for c in model.allocated.get(requirement_id, ()))
        tests = sorted(ad.elements[t].name for t in model.verified.get(requirement_id, ()))
        rows.append(
            [
                requirement_id,
                requirement.text,
                requirement.asil or "",
                "; ".join(components),
                "; ".join(tests),
                "; ".join(model.evidence.get(requirement_id, [])[:10]),
            ]
        )
    header = ["requirement", "text", "asil", "components", "tests", "evidence"]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    md = [
        "# Trace matrix",
        "",
        "| Requirement | ASIL | Components | Tests | Evidence |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        cells = [" ".join(str(c).replace("|", "\\|").split()) for c in row]
        md.append(
            f"| {cells[0]} | {cells[2]} | {cells[3] or '—'} | {cells[4] or '—'} | {cells[5]} |"
        )
    if not rows:
        md.append("| — | | | | |")
    return buffer.getvalue(), "\n".join(md) + "\n"


def export_safety(ad: AD, ctx: ExportContext) -> ExportResult:
    result = ExportResult("safety")
    model = collect(ad, ctx, result)
    if not model.asil and not model.requirements:
        result.note(
            "No ASIL or requirement is declared ([safety], @asil/@req tags, ReqIF or "
            "CSV); the safety view only lists the components."
        )
    check(ad, model, result)
    _add_to_ad(ad, model)
    mermaid = _diagram(ad, model, result)
    csv_text, md_text = _trace_matrix(ad, model)
    result.files["safety/safety.mmd"] = mermaid
    result.files["safety/trace-matrix.csv"] = csv_text
    result.files["safety/trace-matrix.md"] = md_text
    result.covered.update(e.id for e in ad.of_type("component"))
    result.views.append(
        ADView(
            "safety-architecture",
            "safety-architecture",
            "Safety architecture",
            "Components coloured by ASIL. Edges marked FFI? go from a higher to a lower ASIL and "
            "need a freedom-from-interference argument.",
            element_ids=[e.id for e in ad.of_type("component", "requirement")],
            files=["safety/safety.mmd", "safety/trace-matrix.csv", "safety/trace-matrix.md"],
            mermaid=mermaid,
        )
    )
    return result
