"""Declarations for the architecture description: people, external systems, stakeholders,
concerns, decisions, safety, AUTOSAR and requirement sources. Nothing here is ever inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from archdiag.config import ConfigError

ASIL_LEVELS = ("QM", "A", "B", "C", "D")


@dataclass(frozen=True)
class Party:
    """A person or an external system that C4 shows, declared in [c4]."""

    name: str
    description: str = ""
    uses: tuple[str, ...] = ()
    label: str = ""


@dataclass(frozen=True)
class DeclaredConcern:
    id: str
    text: str


@dataclass(frozen=True)
class DeclaredStakeholder:
    id: str
    name: str
    concerns: tuple[str, ...]


@dataclass(frozen=True)
class DeclaredDecision:
    id: str
    title: str
    status: str = "accepted"
    rationale: str = ""
    affects: tuple[str, ...] = ()


@dataclass(frozen=True)
class SafetyComponent:
    name: str
    asil: str | None = None
    requirements: tuple[str, ...] = ()
    safety_goals: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequirementSource:
    kind: str  # reqif | csv
    path: str
    id_field: str = "id"
    text_field: str = "text"
    asil_field: str = "asil"
    component_field: str = ""
    delimiter: str = ","


@dataclass(frozen=True)
class ADConfig:
    people: tuple[Party, ...] = ()
    external_systems: tuple[Party, ...] = ()
    stakeholders: tuple[DeclaredStakeholder, ...] = ()
    concerns: tuple[DeclaredConcern, ...] = ()
    decisions: tuple[DeclaredDecision, ...] = ()
    safety: tuple[SafetyComponent, ...] = ()
    requirement_sources: tuple[RequirementSource, ...] = ()
    autosar_layers: dict[str, str] = field(default_factory=dict)
    autosar_mapping: dict[str, str] = field(default_factory=dict)
    arxml: tuple[str, ...] = ()
    sysml_validator: str | None = None


def _text(item: dict[str, Any], key: str, where: str, required: bool = False) -> str:
    value = item.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ConfigError(f"{where}: '{key}' must be {'a non-empty ' if required else 'a '}string")
    return value.strip()


def _strings(item: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{where}: '{key}' must be a list of non-empty strings")
    return tuple(value)


def _tables(value: Any, where: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ConfigError(f"{where} must be an array of tables ([[...]])")
    return value


def _parties(value: Any, where: str) -> tuple[Party, ...]:
    out = []
    for index, item in enumerate(_tables(value, where)):
        at = f"{where}[{index}]"
        out.append(
            Party(
                _text(item, "name", at, True),
                _text(item, "description", at),
                _strings(item, "uses", at),
                _text(item, "label", at),
            )
        )
    return tuple(out)


def load_ad_config(sections: dict[str, Any], source: str = "archdiag.toml") -> ADConfig:
    c4 = sections.get("c4") or {}
    if not isinstance(c4, dict):
        raise ConfigError(f"{source}: [c4] must be a table")

    concerns = tuple(
        DeclaredConcern(
            _text(c, "id", f"{source}: concerns", True),
            _text(c, "text", f"{source}: concerns", True),
        )
        for c in _tables(sections.get("concerns"), f"{source}: concerns")
    )
    stakeholders = tuple(
        DeclaredStakeholder(
            _text(s, "id", f"{source}: stakeholders", True),
            _text(s, "name", f"{source}: stakeholders", True),
            _strings(s, "concerns", f"{source}: stakeholders"),
        )
        for s in _tables(sections.get("stakeholders"), f"{source}: stakeholders")
    )
    known_concerns = {c.id for c in concerns}
    for stakeholder in stakeholders:
        missing = [c for c in stakeholder.concerns if concerns and c not in known_concerns]
        if missing:
            raise ConfigError(
                f"{source}: stakeholder '{stakeholder.id}' refers to unknown "
                f"concern(s) {', '.join(missing)}"
            )

    decisions = tuple(
        DeclaredDecision(
            _text(d, "id", f"{source}: decisions", True),
            _text(d, "title", f"{source}: decisions", True),
            _text(d, "status", f"{source}: decisions") or "accepted",
            _text(d, "rationale", f"{source}: decisions"),
            _strings(d, "affects", f"{source}: decisions"),
        )
        for d in _tables(sections.get("decisions"), f"{source}: decisions")
    )

    safety_section = sections.get("safety") or {}
    if not isinstance(safety_section, dict):
        raise ConfigError(f"{source}: [safety] must be a table")
    safety = []
    for index, item in enumerate(_tables(safety_section.get("components"), f"{source}: safety")):
        where = f"{source}: safety.components[{index}]"
        asil = item.get("asil")
        if asil is not None and asil not in ASIL_LEVELS:
            raise ConfigError(f"{where}: asil must be one of {', '.join(ASIL_LEVELS)}")
        safety.append(
            SafetyComponent(
                _text(item, "name", where, True),
                asil,
                _strings(item, "requirements", where),
                _strings(item, "safety_goals", where),
            )
        )

    requirements = sections.get("requirements") or {}
    if not isinstance(requirements, dict):
        raise ConfigError(f"{source}: [requirements] must be a table")
    req_sources = []
    for kind in ("reqif", "csv"):
        table = requirements.get(kind)
        if table is None:
            continue
        where = f"{source}: requirements.{kind}"
        if not isinstance(table, dict):
            raise ConfigError(f"{where} must be a table")
        req_sources.append(
            RequirementSource(
                kind,
                _text(table, "path", where, True),
                _text(table, "id_field", where) or ("ReqIF.ForeignID" if kind == "reqif" else "id"),
                _text(table, "text_field", where) or ("ReqIF.Text" if kind == "reqif" else "text"),
                _text(table, "asil_field", where) or ("ASIL" if kind == "reqif" else "asil"),
                _text(table, "component_field", where),
                _text(table, "delimiter", where) or ",",
            )
        )

    autosar = sections.get("autosar") or {}
    if not isinstance(autosar, dict):
        raise ConfigError(f"{source}: [autosar] must be a table")
    layers = autosar.get("layers", {})
    mapping = autosar.get("mapping", {})
    for name, table in (("layers", layers), ("mapping", mapping)):
        if not isinstance(table, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in table.items()
        ):
            raise ConfigError(f"{source}: autosar.{name} must map names to strings")
    valid_layers = {"application", "rte", "services", "ecu_abstraction", "mcal", "complex_drivers"}
    bad = sorted(set(layers.values()) - valid_layers)
    if bad:
        raise ConfigError(
            f"{source}: autosar.layers uses unknown layer(s) {', '.join(bad)}; "
            f"use {', '.join(sorted(valid_layers))}"
        )

    sysml = sections.get("sysml") or {}
    validator = sysml.get("validator") if isinstance(sysml, dict) else None
    if validator is not None and not isinstance(validator, str):
        raise ConfigError(f"{source}: sysml.validator must be a command string")

    return ADConfig(
        people=_parties(c4.get("people"), f"{source}: c4.people"),
        external_systems=_parties(c4.get("external_systems"), f"{source}: c4.external_systems"),
        stakeholders=stakeholders,
        concerns=concerns,
        decisions=decisions,
        safety=tuple(safety),
        requirement_sources=tuple(req_sources),
        autosar_layers=dict(layers),
        autosar_mapping=dict(mapping),
        arxml=_strings(autosar, "arxml", f"{source}: autosar"),
        sysml_validator=validator,
    )
