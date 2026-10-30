"""AUTOSAR 4.x ARXML: software component types, ports, interfaces and assembly connectors."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from archdiag.ad.importers.xmlsafe import (
    UnsafeXML,
    child,
    child_text,
    descendants,
    local,
    parse_xml,
)

SWC_TYPES = {
    "APPLICATION-SW-COMPONENT-TYPE": "Application",
    "SENSOR-ACTUATOR-SW-COMPONENT-TYPE": "SensorActuator",
    "COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE": "ComplexDeviceDriver",
    "SERVICE-SW-COMPONENT-TYPE": "Service",
    "ECU-ABSTRACTION-SW-COMPONENT-TYPE": "EcuAbstraction",
    "COMPOSITION-SW-COMPONENT-TYPE": "Composition",
}
PORT_KINDS = {"P-PORT-PROTOTYPE": "P", "R-PORT-PROTOTYPE": "R", "PR-PORT-PROTOTYPE": "PR"}


@dataclass
class ArxmlPort:
    name: str
    kind: str  # P | R | PR
    interface: str


@dataclass
class ArxmlSwc:
    name: str
    type: str
    ports: list[ArxmlPort] = field(default_factory=list)


@dataclass
class ArxmlInterface:
    name: str
    kind: str  # SR | CS
    members: list[str] = field(default_factory=list)


@dataclass
class ArxmlConnector:
    provider_swc: str
    provider_port: str
    requester_swc: str
    requester_port: str


@dataclass
class ArxmlModel:
    swcs: dict[str, ArxmlSwc] = field(default_factory=dict)
    interfaces: dict[str, ArxmlInterface] = field(default_factory=dict)
    connectors: list[ArxmlConnector] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _last(ref: str) -> str:
    return ref.strip().rstrip("/").rsplit("/", 1)[-1]


def _ref(element: ET.Element, name: str) -> str:
    return _last(child_text(element, name))


def parse_arxml(text: str, source: str = "arxml") -> ArxmlModel:
    model = ArxmlModel()
    try:
        root = parse_xml(text)
    except (ET.ParseError, UnsafeXML) as exc:
        model.warnings.append(f"{source}: not read ({exc})")
        return model

    for element in root.iter():
        tag = local(element.tag)
        if tag in SWC_TYPES:
            swc = ArxmlSwc(child_text(element, "SHORT-NAME"), SWC_TYPES[tag])
            ports = child(element, "PORTS")
            for port_element in ports if ports is not None else []:
                kind = PORT_KINDS.get(local(port_element.tag))
                if kind is None:
                    continue
                interface = _ref(port_element, "PROVIDED-INTERFACE-TREF") or _ref(
                    port_element, "REQUIRED-INTERFACE-TREF"
                )
                swc.ports.append(ArxmlPort(child_text(port_element, "SHORT-NAME"), kind, interface))
            if swc.name:
                model.swcs[swc.name] = swc
        elif tag == "SENDER-RECEIVER-INTERFACE":
            name = child_text(element, "SHORT-NAME")
            members = [
                child_text(d, "SHORT-NAME") for d in descendants(element, "VARIABLE-DATA-PROTOTYPE")
            ]
            model.interfaces[name] = ArxmlInterface(name, "SR", members)
        elif tag == "CLIENT-SERVER-INTERFACE":
            name = child_text(element, "SHORT-NAME")
            members = [
                child_text(d, "SHORT-NAME") for d in descendants(element, "CLIENT-SERVER-OPERATION")
            ]
            model.interfaces[name] = ArxmlInterface(name, "CS", members)

    prototypes: dict[str, str] = {}
    for prototype in descendants(root, "SW-COMPONENT-PROTOTYPE"):
        prototypes[child_text(prototype, "SHORT-NAME")] = _ref(prototype, "TYPE-TREF")
    for connector in descendants(root, "ASSEMBLY-SW-CONNECTOR"):
        provider = child(connector, "PROVIDER-IREF")
        requester = child(connector, "REQUESTER-IREF")
        if provider is None or requester is None:
            continue
        p_proto = _ref(provider, "CONTEXT-COMPONENT-REF")
        r_proto = _ref(requester, "CONTEXT-COMPONENT-REF")
        model.connectors.append(
            ArxmlConnector(
                prototypes.get(p_proto, p_proto),
                _ref(provider, "TARGET-P-PORT-REF"),
                prototypes.get(r_proto, r_proto),
                _ref(requester, "TARGET-R-PORT-REF"),
            )
        )

    for swc in model.swcs.values():
        for port in swc.ports:
            if port.interface and port.interface not in model.interfaces:
                model.warnings.append(
                    f"{source}: port {swc.name}.{port.name} refers to unknown "
                    f"interface {port.interface}"
                )
    return model
