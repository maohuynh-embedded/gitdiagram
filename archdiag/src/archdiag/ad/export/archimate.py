"""ArchiMate 3.x in the Open Group Model Exchange File Format (opens in Archi, Sparx EA, …).

Mapping (spec C §5.2):
component → ApplicationComponent, interface → ApplicationInterface (Composition from owner,
Serving to users), layer → Grouping (Aggregation), A depends on B → Serving B → A, message
flow → Flow, container → Artifact (Realization → ApplicationComponent), library →
SystemSoftware (Serving), device → Device, bus → CommunicationNetwork (Association).
"""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from archdiag.ad.model import AD, ADView, Element
from archdiag.ad.validate import ExportResult

NS = "http://www.opengroup.org/xsd/archimate/3.0/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XML_NS = "http://www.w3.org/XML/1998/namespace"
SCHEMA_BASE = "http://www.opengroup.org/xsd/archimate/3.1/"
SCHEMA_FILES = ("archimate3_Model.xsd", "archimate3_View.xsd", "archimate3_Diagram.xsd")
XML_XSD_URL = "https://www.w3.org/2001/xml.xsd"
SCHEMA_DIR = Path.home() / ".cache" / "archdiag" / "schemas" / "archimate-3.1"

NODE_W, NODE_H, GAP, PAD, PER_ROW = 120, 55, 40, 20, 6

ELEMENT_TYPE = {
    "component": "ApplicationComponent",
    "external_system": "ApplicationComponent",
    "interface": "ApplicationInterface",
    "layer": "Grouping",
    "container": "Artifact",
    "external": "SystemSoftware",
    "device": "Device",
    "bus": "CommunicationNetwork",
}

# Allowed (source, relationship, target) for the element types this exporter emits, taken
# from the ArchiMate 3.1/3.2 relationship tables. Association is always allowed, and a Grouping
# may aggregate or compose any concept.
VALID = frozenset({
    ("ApplicationComponent", "Serving", "ApplicationComponent"),
    ("ApplicationComponent", "Flow", "ApplicationComponent"),
    ("ApplicationComponent", "Composition", "ApplicationInterface"),
    ("ApplicationInterface", "Serving", "ApplicationComponent"),
    ("Artifact", "Realization", "ApplicationComponent"),
    ("SystemSoftware", "Serving", "ApplicationComponent"),
    ("Device", "Serving", "ApplicationComponent"),
    ("Device", "Assignment", "SystemSoftware"),
})


def is_valid(source: str, relationship: str, target: str) -> bool:
    if relationship == "Association":
        return True
    if source == "Grouping" and relationship in ("Aggregation", "Composition"):
        return True
    return (source, relationship, target) in VALID


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _lang(parent: ET.Element, tag: str, text: str) -> ET.Element:
    node = ET.SubElement(parent, _q(tag))
    node.set(f"{{{XML_NS}}}lang", "en")
    node.text = text
    return node


def _xid(value: str) -> str:
    return "id-" + re.sub(r"[^A-Za-z0-9_.-]", "_", value)


@dataclass
class _Rel:
    id: str
    type: str
    source: str
    target: str
    name: str = ""
    directed: bool = False


@dataclass
class _Model:
    elements: dict[str, tuple[str, Element]] = field(default_factory=dict)  # xid → (type, el)
    relations: list[_Rel] = field(default_factory=list)

    def add_relation(self, kind: str, source: str, target: str, name: str = "",
                     directed: bool = False) -> None:
        rel_id = _xid(f"r_{kind}_{source}_{target}")
        if any(r.id == rel_id for r in self.relations):
            return
        self.relations.append(_Rel(rel_id, kind, _xid(source), _xid(target), name, directed))


def _map(ad: AD, result: ExportResult) -> _Model:
    model = _Model()
    for element in ad.elements.values():
        archimate_type = ELEMENT_TYPE.get(element.type)
        if archimate_type is None:
            continue
        model.elements[_xid(element.id)] = (archimate_type, element)
        if element.type == "component":
            result.covered.add(element.id)

    def has(element_id: str) -> bool:
        return _xid(element_id) in model.elements

    for relation in ad.relations:
        source, target = relation.source, relation.target
        if not (has(source) and has(target)):
            continue
        source_type = ad.elements[source].type
        target_type = ad.elements[target].type
        if "container" in (source_type, target_type) and relation.type != "contains":
            continue  # container-level relations are lifted views of component relations
        if relation.type == "depends":
            model.add_relation("Serving", target, source, relation.description)
        elif relation.type == "flows":
            if "device" in (source_type, target_type):
                model.add_relation("Association", source, target, relation.description, True)
            else:
                model.add_relation("Flow", source, target, relation.description)
        elif relation.type == "contains" and source_type == "layer":
            model.add_relation("Aggregation", source, target)
        elif relation.type == "contains" and source_type == "container":
            model.add_relation("Realization", source, target)
        elif relation.type == "interacts":
            model.add_relation("Association", source, target, relation.description, True)

    # Interfaces: owned (Composition) by their component, serving the components that use them.
    for interface in ad.of_type("interface"):
        owner = interface.parent
        if owner and has(owner):
            model.add_relation("Composition", owner, interface.id)
        for relation in ad.relations_of("composes", "realizes"):
            if relation.target != interface.id:
                continue
            user = ad.elements.get(relation.source)
            user_component = user.parent if user else None
            if user_component and has(user_component) and user_component != owner:
                model.add_relation("Serving", interface.id, user_component, relation.type)
    return model


def _properties(parent: ET.Element, element: Element, definitions: dict[str, str]) -> None:
    values = {"path": ", ".join(element.paths[:5]),
              **{k: v for k, v in element.properties.items() if v}}
    values = {k: v for k, v in values.items() if v}
    if not values:
        return
    container = ET.SubElement(parent, _q("properties"))
    for key, value in sorted(values.items()):
        definition = definitions.setdefault(key, f"pd-{len(definitions) + 1}")
        prop = ET.SubElement(container, _q("property"), {"propertyDefinitionRef": definition})
        _lang(prop, "value", value[:500])


class _Layout:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix  # identifiers are xs:ID, unique across the whole document
        self.counter = 0
        self.placed: dict[str, str] = {}  # element xid → node id

    def node_id(self) -> str:
        self.counter += 1
        return f"{self.prefix}-n-{self.counter}"


def _row(parent: ET.Element, layout: _Layout, xids: list[str], x: int, y: int) -> tuple[int, int]:
    """Place nodes in rows of PER_ROW; returns the (width, height) used."""
    width = height = 0
    for index, xid in enumerate(xids):
        col, row = index % PER_ROW, index // PER_ROW
        nx, ny = x + col * (NODE_W + GAP), y + row * (NODE_H + GAP)
        node_id = layout.node_id()
        ET.SubElement(parent, _q("node"), {
            "identifier": node_id, "elementRef": xid, f"{{{XSI}}}type": "Element",
            "x": str(nx), "y": str(ny), "w": str(NODE_W), "h": str(NODE_H),
        })
        layout.placed[xid] = node_id
        width = max(width, nx - x + NODE_W)
        height = max(height, ny - y + NODE_H)
    return width, height


def _diagram(views: ET.Element, view_id: str, name: str, rows: list[tuple[str | None, list[str]]],
             model: _Model) -> ET.Element:
    view = ET.SubElement(views, _q("view"), {"identifier": view_id,
                                             f"{{{XSI}}}type": "Diagram"})
    _lang(view, "name", name)
    layout = _Layout(view_id)
    y = PAD
    for group_xid, members in rows:
        if not members:
            continue
        if group_xid:
            group_id = layout.node_id()
            group = ET.SubElement(view, _q("node"), {
                "identifier": group_id, "elementRef": group_xid, f"{{{XSI}}}type": "Element",
                "x": str(PAD), "y": str(y), "w": "1", "h": "1",
            })
            layout.placed[group_xid] = group_id
            width, height = _row(group, layout, members, PAD * 2, y + PAD * 2)
            group.set("w", str(width + PAD * 2))
            group.set("h", str(height + PAD * 3))
            y += height + PAD * 3 + GAP
        else:
            _, height = _row(view, layout, members, PAD, y)
            y += height + GAP
    counter = 0
    for relation in model.relations:
        if relation.type == "Aggregation" and relation.source in layout.placed and \
                model.elements.get(relation.source, ("",))[0] == "Grouping":
            continue  # nesting already shows the aggregation
        if relation.source in layout.placed and relation.target in layout.placed:
            counter += 1
            ET.SubElement(view, _q("connection"), {
                "identifier": f"{view_id}-c-{counter}", "relationshipRef": relation.id,
                f"{{{XSI}}}type": "Relationship",
                "source": layout.placed[relation.source],
                "target": layout.placed[relation.target],
            })
    return view


def export_archimate(ad: AD) -> ExportResult:
    result = ExportResult("archimate")
    model = _map(ad, result)
    for relation in model.relations:
        source_type = model.elements[relation.source][0]
        target_type = model.elements[relation.target][0]
        if not is_valid(source_type, relation.type, target_type):
            result.error(f"{relation.type} from {source_type} to {target_type} is not allowed by "
                         "the ArchiMate relationship table.", relation.id)

    ET.register_namespace("", NS)
    ET.register_namespace("xsi", XSI)
    root = ET.Element(_q("model"), {
        "identifier": _xid(f"model_{ad.system_name}"),
        f"{{{XSI}}}schemaLocation": f"{NS} {SCHEMA_BASE}archimate3_Diagram.xsd",
    })
    _lang(root, "name", ad.system_name)
    _lang(root, "documentation",
          (ad.overview or "Generated by archdiag from static analysis.")[:4000])
    definitions: dict[str, str] = {}

    elements = ET.SubElement(root, _q("elements"))
    for xid, (archimate_type, element) in sorted(model.elements.items()):
        node = ET.SubElement(elements, _q("element"), {"identifier": xid,
                                                       f"{{{XSI}}}type": archimate_type})
        _lang(node, "name", element.name)
        if element.description:
            _lang(node, "documentation", element.description[:2000])
        _properties(node, element, definitions)

    relationships = ET.SubElement(root, _q("relationships"))
    for relation in model.relations:
        attributes = {"identifier": relation.id, "source": relation.source,
                      "target": relation.target, f"{{{XSI}}}type": relation.type}
        if relation.type == "Association" and relation.directed:
            attributes["isDirected"] = "true"
        node = ET.SubElement(relationships, _q("relationship"), attributes)
        if relation.name:
            _lang(node, "name", relation.name[:200])

    organizations = ET.SubElement(root, _q("organizations"))
    by_type: dict[str, list[str]] = defaultdict(list)
    for xid, (archimate_type, _) in sorted(model.elements.items()):
        by_type[archimate_type].append(xid)
    for archimate_type, xids in sorted(by_type.items()):
        folder = ET.SubElement(organizations, _q("item"))
        _lang(folder, "label", archimate_type)
        for xid in xids:
            ET.SubElement(folder, _q("item"), {"identifierRef": xid})

    if definitions:
        defs = ET.SubElement(root, _q("propertyDefinitions"))
        for key, identifier in definitions.items():
            definition = ET.SubElement(defs, _q("propertyDefinition"),
                                       {"identifier": identifier, "type": "string"})
            _lang(definition, "name", f"archdiag:{key}")

    views = ET.SubElement(ET.SubElement(root, _q("views")), _q("diagrams"))
    components = [e for e in ad.of_type("component") if _xid(e.id) in model.elements]
    rows: list[tuple[str | None, list[str]]] = []
    for layer in ad.of_type("layer"):
        members = [_xid(c.id) for c in components if any(
            r.type == "contains" and r.source == layer.id and r.target == c.id
            for r in ad.relations)]
        rows.append((_xid(layer.id), members))
    grouped = {x for _, members in rows for x in members}
    rows.append((None, [_xid(c.id) for c in components if _xid(c.id) not in grouped]))
    rows.append((None, [_xid(e.id) for e in ad.of_type("interface")]))
    rows.append((None, [_xid(e.id) for e in ad.of_type("external_system")]))
    _diagram(views, "id-v-application", "Application cooperation", rows, model)

    realized = {r.target for r in model.relations if r.type == "Realization"}
    tech_rows: list[tuple[str | None, list[str]]] = [
        (None, [_xid(e.id) for e in ad.of_type("external")]),
        (None, [_xid(e.id) for e in ad.of_type("container")]),
        (None, sorted(realized)),
        (None, [_xid(e.id) for e in ad.of_type("device", "bus")]),
    ]
    _diagram(views, "id-v-technology", "Technology", tech_rows, model)

    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="unicode", xml_declaration=False)
    result.files["archimate/model.xml"] = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml + "\n"
    result.views.append(ADView("archimate-application", "archimate-application",
                               "ArchiMate: application cooperation",
                               "Application components grouped by layer, their serving and flow "
                               "relationships and the interfaces they offer.",
                               element_ids=[e.id for e in components],
                               files=["archimate/model.xml"]))
    result.views.append(ADView("archimate-technology", "archimate-technology",
                               "ArchiMate: technology",
                               "Artifacts (build targets) realising the application components, "
                               "the system software they use and the devices they reach.",
                               element_ids=[e.id for e in ad.of_type("container", "external",
                                                                    "device", "bus")],
                               files=["archimate/model.xml"]))
    validate_xsd(result)
    return result


def validate_xsd(result: ExportResult, schema_dir: Path = SCHEMA_DIR) -> None:
    xml = result.files.get("archimate/model.xml")
    if xml is None:
        return
    main = schema_dir / "archimate3_Diagram.xsd"
    if not main.is_file():
        result.note("XSD validation skipped: run `archdiag fetch-schemas` once to enable it.")
        return
    try:
        from lxml import etree
    except ImportError:
        result.note("XSD validation skipped: install lxml (pip install 'archdiag[validate]').")
        return
    try:
        schema = etree.XMLSchema(etree.parse(str(main)))
        document = etree.fromstring(xml.encode("utf-8"))
    except (etree.XMLSchemaParseError, etree.XMLSyntaxError, OSError) as exc:
        result.error(f"XSD validation could not run: {exc}")
        return
    if not schema.validate(document):
        for entry in list(schema.error_log)[:20]:
            result.error(f"XSD: line {entry.line}: {entry.message}")
    else:
        result.note("model.xml is valid against the ArchiMate 3.1 exchange-format XSD.")


def fetch_schemas(force: bool = False, target: Path = SCHEMA_DIR) -> list[str]:
    """Download the exchange-format XSDs once, with imports rewritten to local copies."""
    target.mkdir(parents=True, exist_ok=True)
    lines = []
    downloads = [(SCHEMA_BASE + name, name) for name in SCHEMA_FILES]
    downloads.append((XML_XSD_URL, "xml.xsd"))
    for url, name in downloads:
        path = target / name
        if path.is_file() and not force:
            lines.append(f"{name}: already present")
            continue
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (fixed https/http URLs)
            data = response.read().decode("utf-8")
        data = data.replace('schemaLocation="http://www.w3.org/2001/xml.xsd"',
                            'schemaLocation="xml.xsd"')
        path.write_text(data, encoding="utf-8")
        lines.append(f"{name}: downloaded from {url}")
    lines.append(f"schemas stored in {target}")
    return lines
