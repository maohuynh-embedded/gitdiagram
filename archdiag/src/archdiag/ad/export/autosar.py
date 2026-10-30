"""AUTOSAR: a derived SWC view for any code base, and ARXML ↔ code differences when the
project is a real AUTOSAR project. The derived view is labelled as such everywhere: it is an
AUTOSAR-style reading of non-AUTOSAR code, not an AUTOSAR model.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from archdiag.ad.context import ExportContext
from archdiag.ad.importers.arxml import ArxmlModel, ArxmlSwc, parse_arxml
from archdiag.ad.model import AD, ADView, Element
from archdiag.ad.validate import ExportResult, plain_text
from archdiag.arch.components import snake_id
from archdiag.facts.model import INCLUDE, MESSAGE_IO
from archdiag.render.mermaid import escape_label

DERIVED_TITLE = "AUTOSAR-style (derived), not an AUTOSAR model"
LAYERS = {
    "application": ("Application layer", "Application SWC"),
    "services": ("BSW: services", "Service SWC"),
    "ecu_abstraction": ("BSW: ECU abstraction", "ECU abstraction SWC"),
    "complex_drivers": ("Complex device drivers", "Complex device driver SWC"),
    "mcal": ("BSW: MCAL", "MCAL module"),
    "rte": ("RTE", "RTE"),
}
_SERVICE_WORDS = ("service", "config", "diag", "nvm", "memory", "storage", "log", "os")
_ABSTRACTION_WORDS = ("hal", "driver", "io", "port", "adapter", "infrastructure")
ARXML_LAYER = {
    "Application": "application",
    "SensorActuator": "application",
    "Service": "services",
    "EcuAbstraction": "ecu_abstraction",
    "ComplexDeviceDriver": "complex_drivers",
}
_RTE_CALL = re.compile(r"^Rte_(Write|IWrite|Send|Call|Read|IRead|Receive|Result)_(.+)$")


@dataclass
class SwcPort:
    name: str
    kind: str  # P | R
    interface: str
    interface_kind: str  # SR | CS


@dataclass
class DerivedSwc:
    element: Element
    layer: str
    ports: list[SwcPort] = field(default_factory=list)

    def port(self, name: str, kind: str, interface: str, interface_kind: str) -> str:
        if not any(p.name == name for p in self.ports):
            self.ports.append(SwcPort(name, kind, interface, interface_kind))
        return name


def _layer(component: Element, ad: AD, ctx: ExportContext, touches_device: set[str]) -> str:
    declared = ctx.config.autosar_layers
    for key in (component.name, component.id, *component.paths):
        if key in declared:
            return declared[key]
    if component.id in touches_device:
        return "complex_drivers"
    words = f"{component.name} {' '.join(component.paths)}".lower()
    if any(w in words for w in _SERVICE_WORDS):
        return "services"
    if any(w in words for w in _ABSTRACTION_WORDS):
        return "ecu_abstraction"
    return "application"


def _port_id(prefix: str, interface: str) -> str:
    return f"{prefix}_{re.sub(r'[^A-Za-z0-9]+', '_', interface).strip('_') or 'x'}"


def derive(
    ad: AD,
    ctx: ExportContext,
    arxml: ArxmlModel | None = None,
    mapping: dict[str, str] | None = None,
) -> tuple[dict[str, DerivedSwc], list[tuple[str, str, str, str, str]]]:
    components = [e for e in ad.of_type("component")]
    device_ids = {e.id for e in ad.of_type("device")}
    touches_device = {
        r.source if r.target in device_ids else r.target
        for r in ad.relations_of("flows")
        if device_ids & {r.source, r.target}
    }
    swcs = {c.id: DerivedSwc(c, _layer(c, ad, ctx, touches_device)) for c in components}
    ends = set(swcs) | device_ids
    connections: list[tuple[str, str, str, str, str]] = []
    if arxml is not None and mapping:
        by_component = {component: swc for swc, component in mapping.items()}
        for component, swc_name in by_component.items():
            declared = arxml.swcs.get(swc_name)
            if component in swcs and declared and declared.type in ARXML_LAYER:
                swcs[component].layer = ARXML_LAYER[declared.type]
                for port in declared.ports:
                    kind = arxml.interfaces.get(port.interface)
                    swcs[component].port(
                        port.name,
                        "R" if port.kind == "R" else "P",
                        port.interface,
                        kind.kind if kind else "SR",
                    )
        for connector in arxml.connectors:
            source = mapping.get(connector.provider_swc)
            target = mapping.get(connector.requester_swc)
            if source in swcs and target in swcs:
                interface = next(
                    (p.interface for p in swcs[source].ports if p.name == connector.provider_port),
                    connector.provider_port,
                )
                kind = arxml.interfaces.get(interface)
                connections.append(
                    (
                        source,
                        connector.provider_port,
                        target,
                        connector.requester_port,
                        f"{kind.kind if kind else 'SR'}: {interface}",
                    )
                )
    for relation in ad.relations_of("flows"):
        if relation.source not in ends or relation.target not in ends:
            continue  # container-level relations are lifted copies of these
        interface = relation.description or "data"
        sender, receiver = swcs.get(relation.source), swcs.get(relation.target)
        p_port = sender.port(_port_id("Pp", interface), "P", interface, "SR") if sender else ""
        r_port = receiver.port(_port_id("Rp", interface), "R", interface, "SR") if receiver else ""
        connections.append((relation.source, p_port, relation.target, r_port, f"SR: {interface}"))
    for iface in ad.of_type("interface"):
        providers = {
            ad.elements[r.source].parent
            for r in ad.relations_of("realizes")
            if r.target == iface.id and r.source in ad.elements
        }
        users = {
            ad.elements[r.source].parent
            for r in ad.relations_of("composes")
            if r.target == iface.id and r.source in ad.elements
        }
        for provider in sorted(p for p in providers if p in swcs):
            p_port = swcs[provider].port(_port_id("Pp", iface.name), "P", iface.name, "CS")
            for user in sorted(u for u in users if u in swcs and u != provider):
                r_port = swcs[user].port(_port_id("Rp", iface.name), "R", iface.name, "CS")
                connections.append((provider, p_port, user, r_port, f"CS: {iface.name}"))
    return swcs, connections


def _derived_views(
    ad: AD,
    swcs: dict[str, DerivedSwc],
    connections: list[tuple[str, str, str, str, str]],
    result: ExportResult,
) -> None:
    by_layer: dict[str, list[DerivedSwc]] = defaultdict(list)
    for swc in swcs.values():
        by_layer[swc.layer].append(swc)
    mermaid = ["---", f'title: "{DERIVED_TITLE}"', "---", "flowchart LR"]
    puml = ["@startuml", f"title {DERIVED_TITLE}", "skinparam componentStyle rectangle"]
    for layer, (title, kind) in LAYERS.items():
        members = by_layer.get(layer, [])
        if not members:
            continue
        mermaid.append(f'  subgraph layer_{layer}["{escape_label(title)}"]')
        puml.append(f'package "{plain_text(title)}" {{')
        for swc in members:
            label = escape_label(f"{swc.element.name} · {kind}")
            mermaid.append(f'    swc_{swc.element.id}["{label}"]')
            puml.append(
                f'  component "{plain_text(swc.element.name, 60)}" as '
                f"swc_{swc.element.id} <<{kind}>>"
            )
        mermaid.append("  end")
        puml.append("}")
    for device in ad.of_type("device"):
        mermaid.append(f'  swc_{device.id}{{{{"{escape_label(device.name)}"}}}}')
        puml.append(f'node "{plain_text(device.name, 60)}" as swc_{device.id}')
    for source, _, target, _, label in connections:
        mermaid.append(f'  swc_{source} -->|"{escape_label(label, 40)}"| swc_{target}')
        puml.append(f"swc_{source} --> swc_{target} : {plain_text(label, 60)}")
    puml.append("@enduml")
    text = "\n".join(mermaid) + "\n"
    result.files["autosar/swc.mmd"] = text
    result.files["autosar/swc.puml"] = "\n".join(puml) + "\n"
    result.views.append(
        ADView(
            "autosar-swc",
            "autosar-swc",
            "AUTOSAR-style software components (derived)",
            f"{DERIVED_TITLE}. Components are read as SWCs in AUTOSAR layers; message flows become "
            "sender-receiver ports and pure virtual interfaces client-server ports.",
            element_ids=list(swcs),
            files=["autosar/swc.mmd", "autosar/swc.puml"],
            mermaid=text,
        )
    )


def _check_derived(swcs: dict[str, DerivedSwc], result: ExportResult) -> None:
    kinds: dict[str, set[str]] = defaultdict(set)
    for swc in swcs.values():
        for port in swc.ports:
            kinds[port.interface].add(port.interface_kind)
        if not swc.ports:
            result.note(f"{swc.element.name} has no ports (isolated SWC).", swc.element.id)
    for interface, used in kinds.items():
        if len(used) > 1:
            result.error(f"Interface '{interface}' is used both sender-receiver and client-server.")


# --- ARXML comparison ------------------------------------------------------------------------


def _load_arxml(ctx: ExportContext, result: ExportResult) -> ArxmlModel | None:
    paths = list(ctx.config.arxml) or [f.path for f in ctx.snapshot.files if f.language == "arxml"]
    if not paths:
        return None
    merged = ArxmlModel()
    for path in paths:
        try:
            text = (ctx.repo / path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result.warning(f"{path}: cannot read ({exc})")
            continue
        model = parse_arxml(text, path)
        merged.swcs.update(model.swcs)
        merged.interfaces.update(model.interfaces)
        merged.connectors.extend(model.connectors)
        for warning in model.warnings:
            result.warning(warning)
    return merged


def _map_swcs(model: ArxmlModel, ad: AD, ctx: ExportContext) -> dict[str, str]:
    mapping: dict[str, str] = {}
    by_name = {e.name: e.id for e in ad.of_type("component")}
    for swc, component in ctx.config.autosar_mapping.items():
        target = by_name.get(component) or (component if component in ad.elements else None)
        if target:
            mapping[swc] = target
    for fact in ctx.facts:
        if fact.kind != INCLUDE:
            continue
        spelled = str(fact.data.get("spelled", ""))
        match = re.match(r"^Rte_(\w+)\.h$", spelled.rsplit("/", 1)[-1])
        owner = ctx.component_of.get(fact.file)
        if match and owner and match.group(1) in model.swcs:
            mapping.setdefault(match.group(1), owner)
    snake = {snake_id(e.name): e.id for e in ad.of_type("component")}
    for swc in model.swcs:
        candidate = snake.get(snake_id(swc))
        if candidate:
            mapping.setdefault(swc, candidate)
    return mapping


def _rte_calls(ctx: ExportContext) -> dict[str, list[tuple[str, str, str, int]]]:
    """component → [(operation, rest of the name, file, line)]"""
    calls: dict[str, list[tuple[str, str, str, int]]] = defaultdict(list)
    for fact in ctx.facts:
        if fact.kind != MESSAGE_IO or fact.data.get("protocol") != "autosar-rte":
            continue
        match = _RTE_CALL.match(str(fact.data.get("function", "")))
        component = ctx.component_of.get(fact.file)
        if match and component:
            calls[component].append((match.group(1), match.group(2), fact.file, fact.line))
    return calls


def _arxml_diff(model: ArxmlModel, ad: AD, ctx: ExportContext, result: ExportResult) -> None:
    mapping = _map_swcs(model, ad, ctx)
    calls = _rte_calls(ctx)
    lines = [
        "# ARXML ↔ code",
        "",
        f"{len(model.swcs)} SWC types, {len(model.interfaces)} interfaces, "
        f"{len(model.connectors)} assembly connectors read from ARXML.",
        "",
    ]
    findings: list[tuple[str, str]] = []
    used_ports: set[tuple[str, str]] = set()
    for name, swc in sorted(model.swcs.items()):
        if swc.type == "Composition":
            continue
        component = mapping.get(name)
        if component is None:
            findings.append(("swc-unmapped", f"SWC {name} matches no component in the code."))
            continue
        port_names = sorted((p.name for p in swc.ports), key=len, reverse=True)
        for operation, rest, file, line in calls.get(component, []):
            port_name = next((p for p in port_names if rest == p or rest.startswith(f"{p}_")), None)
            if port_name is None:
                findings.append(
                    (
                        "call-undeclared",
                        f"`Rte_{operation}_{rest}` in {file}:{line} uses a port that "
                        f"SWC {name} does not declare.",
                    )
                )
            else:
                used_ports.add((name, port_name))
        for port in swc.ports:
            if (name, port.name) not in used_ports:
                findings.append(
                    (
                        "port-unused",
                        f"Port {name}.{port.name} ({port.kind}, "
                        f"{port.interface}) has no RTE call in "
                        f"{ad.elements[component].name}.",
                    )
                )
    for connector in model.connectors:
        provided = (connector.provider_swc, connector.provider_port) in used_ports
        required = (connector.requester_swc, connector.requester_port) in used_ports
        if not (provided and required):
            findings.append(
                (
                    "connector-without-flow",
                    f"Connector {connector.provider_swc}.{connector.provider_port} → "
                    f"{connector.requester_swc}.{connector.requester_port} has no "
                    "matching RTE calls on both sides.",
                )
            )
    lines += ["| SWC | Type | Component in code |", "|---|---|---|"]
    for name, swc in sorted(model.swcs.items()):
        component = mapping.get(name)
        lines.append(
            f"| {name} | {swc.type} | {ad.elements[component].name if component else '—'} |"
        )
    lines += ["", "## Differences", ""]
    lines += [f"- **{kind}** — {text}" for kind, text in findings] or ["None found."]
    result.files["autosar/arxml-diff.md"] = "\n".join(lines) + "\n"
    for kind, text in findings:
        result.warning(f"ARXML {kind}: {text}")

    mermaid = ["flowchart LR"]
    for name, swc in sorted(model.swcs.items()):
        if swc.type != "Composition":
            mermaid.append(f'  a_{snake_id(name)}["{escape_label(name + " · " + swc.type)}"]')
    for connector in model.connectors:
        interface = next(
            (
                p.interface
                for p in model.swcs.get(connector.provider_swc, ArxmlSwc("", "")).ports
                if p.name == connector.provider_port
            ),
            "",
        )
        mermaid.append(
            f"  a_{snake_id(connector.provider_swc)} -->|"
            f'"{escape_label(interface or connector.provider_port, 40)}"| '
            f"a_{snake_id(connector.requester_swc)}"
        )
    text = "\n".join(mermaid) + "\n"
    result.files["autosar/arxml.mmd"] = text
    result.views.append(
        ADView(
            "autosar-arxml",
            "autosar-swc",
            "AUTOSAR composition from ARXML",
            "Software components and assembly connectors as declared in "
            "ARXML, compared with the RTE calls found in the code.",
            files=["autosar/arxml.mmd", "autosar/arxml-diff.md"],
            mermaid=text,
        )
    )


def export_autosar(ad: AD, ctx: ExportContext) -> ExportResult:
    result = ExportResult("autosar")
    model = _load_arxml(ctx, result)
    mapping = _map_swcs(model, ad, ctx) if model is not None else None
    swcs, connections = derive(ad, ctx, model, mapping)
    _derived_views(ad, swcs, connections, result)
    _check_derived(swcs, result)
    result.covered.update(swcs)
    if model is not None:
        _arxml_diff(model, ad, ctx, result)
    return result
