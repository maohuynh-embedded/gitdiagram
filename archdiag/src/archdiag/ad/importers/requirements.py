"""Requirements from ReqIF (DOORS, Polarion, Jama, codebeamer exports) or CSV."""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from archdiag.ad.config import ASIL_LEVELS, RequirementSource
from archdiag.ad.importers.xmlsafe import UnsafeXML, child, descendants, local, parse_xml


@dataclass
class Requirement:
    id: str
    text: str = ""
    asil: str | None = None
    components: list[str] = field(default_factory=list)
    source: str = ""


def _asil(value: str) -> str | None:
    cleaned = value.strip().upper().replace("ASIL", "").replace("-", "").strip()
    return cleaned if cleaned in ASIL_LEVELS else None


def _xhtml_text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())


def parse_reqif(text: str, spec: RequirementSource) -> tuple[list[Requirement], list[str]]:
    try:
        root = parse_xml(text)
    except (ET.ParseError, UnsafeXML) as exc:
        return [], [f"{spec.path}: not read ({exc})"]
    names: dict[str, str] = {}
    for tag in (
        "ATTRIBUTE-DEFINITION-STRING",
        "ATTRIBUTE-DEFINITION-XHTML",
        "ATTRIBUTE-DEFINITION-ENUMERATION",
        "ATTRIBUTE-DEFINITION-INTEGER",
    ):
        for definition in descendants(root, tag):
            names[definition.get("IDENTIFIER", "")] = definition.get("LONG-NAME", "")
    enum_values = {
        v.get("IDENTIFIER", ""): v.get("LONG-NAME", "") for v in descendants(root, "ENUM-VALUE")
    }

    requirements: list[Requirement] = []
    for spec_object in descendants(root, "SPEC-OBJECT"):
        values: dict[str, str] = {}
        container = child(spec_object, "VALUES")
        for value in container if container is not None else []:
            definition_ref = child(value, "DEFINITION")
            ref = "".join(definition_ref.itertext()).strip() if definition_ref is not None else ""
            field_name = names.get(ref, ref)
            kind = local(value.tag)
            if kind in ("ATTRIBUTE-VALUE-STRING", "ATTRIBUTE-VALUE-INTEGER"):
                values[field_name] = value.get("THE-VALUE", "")
            elif kind == "ATTRIBUTE-VALUE-XHTML":
                the_value = child(value, "THE-VALUE")
                values[field_name] = _xhtml_text(the_value) if the_value is not None else ""
            elif kind == "ATTRIBUTE-VALUE-ENUMERATION":
                refs = descendants(value, "ENUM-VALUE-REF")
                values[field_name] = ", ".join(
                    enum_values.get((r.text or "").strip(), "") for r in refs
                )
        identifier = values.get(spec.id_field) or spec_object.get("IDENTIFIER", "")
        if not identifier:
            continue
        components = (
            [c.strip() for c in values.get(spec.component_field, "").split(",") if c.strip()]
            if spec.component_field
            else []
        )
        requirements.append(
            Requirement(
                identifier,
                values.get(spec.text_field, ""),
                _asil(values.get(spec.asil_field, "")),
                components,
                spec.path,
            )
        )
    return requirements, []


def parse_requirements_csv(
    text: str, spec: RequirementSource
) -> tuple[list[Requirement], list[str]]:
    reader = csv.DictReader(io.StringIO(text), delimiter=spec.delimiter)
    if not reader.fieldnames or spec.id_field not in reader.fieldnames:
        return [], [f"{spec.path}: no '{spec.id_field}' column"]
    requirements = []
    for row in reader:
        identifier = (row.get(spec.id_field) or "").strip()
        if not identifier:
            continue
        components = (
            [c.strip() for c in (row.get(spec.component_field) or "").split(";") if c.strip()]
            if spec.component_field
            else []
        )
        requirements.append(
            Requirement(
                identifier,
                (row.get(spec.text_field) or "").strip(),
                _asil(row.get(spec.asil_field) or ""),
                components,
                spec.path,
            )
        )
    return requirements, []
