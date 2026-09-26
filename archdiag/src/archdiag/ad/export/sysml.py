"""SysML v2 textual notation: structure (part defs, parts) and interfaces (items, ports, connect).

Mapping (spec C §5.3): component → part def + part in the system, message → item def, sending
component → port with an `out` item, receiving component → conjugated port, sender/receiver
pair → connect, static dependency → dependency.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from archdiag.ad.model import AD, ADView, Element
from archdiag.ad.validate import ExportResult

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
KEYWORDS = frozenset(
    {
        "about",
        "abstract",
        "accept",
        "action",
        "actor",
        "after",
        "alias",
        "all",
        "allocate",
        "allocation",
        "analysis",
        "and",
        "as",
        "assert",
        "assign",
        "assume",
        "at",
        "attribute",
        "bind",
        "binding",
        "by",
        "calc",
        "case",
        "comment",
        "concern",
        "connect",
        "connection",
        "constraint",
        "decide",
        "def",
        "default",
        "defined",
        "dependency",
        "derived",
        "do",
        "doc",
        "else",
        "end",
        "entry",
        "enum",
        "event",
        "exhibit",
        "exit",
        "expose",
        "filter",
        "first",
        "flow",
        "for",
        "fork",
        "frame",
        "from",
        "hastype",
        "if",
        "implies",
        "import",
        "in",
        "include",
        "individual",
        "inout",
        "interface",
        "istype",
        "item",
        "join",
        "language",
        "library",
        "locale",
        "loop",
        "merge",
        "message",
        "meta",
        "metadata",
        "nonunique",
        "not",
        "objective",
        "occurrence",
        "of",
        "or",
        "ordered",
        "out",
        "package",
        "parallel",
        "part",
        "perform",
        "port",
        "private",
        "protected",
        "public",
        "readonly",
        "redefines",
        "ref",
        "references",
        "render",
        "rendering",
        "rep",
        "require",
        "requirement",
        "return",
        "satisfy",
        "send",
        "snapshot",
        "specializes",
        "stakeholder",
        "standard",
        "state",
        "subject",
        "subsets",
        "succession",
        "terminate",
        "then",
        "timeslice",
        "to",
        "transition",
        "until",
        "use",
        "variant",
        "variation",
        "verification",
        "verify",
        "via",
        "view",
        "viewpoint",
        "when",
        "while",
        "xor",
        "true",
        "false",
        "null",
    }
)


def name(text: str) -> str:
    """A SysML v2 name: a basic identifier, or an unrestricted name in single quotes."""
    if _IDENT.match(text) and text not in KEYWORDS:
        return text
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    escaped = "".join(ch if ch.isprintable() else " " for ch in escaped)
    return f"'{escaped}'"


def doc(text: str) -> str:
    cleaned = " ".join(text.replace("*/", "* /").split())
    return f"doc /* {cleaned} */"


@dataclass
class Port:
    name: str
    type: str  # port def name
    conjugated: bool


@dataclass
class PartDef:
    name: str
    element_id: str
    documentation: str = ""
    ports: list[Port] = field(default_factory=list)


@dataclass
class SysmlModel:
    package: str
    documentation: str = ""
    items: dict[str, str] = field(default_factory=dict)  # item def name → doc
    port_defs: dict[str, str] = field(default_factory=dict)  # port def name → item def name
    part_defs: dict[str, PartDef] = field(default_factory=dict)  # element id → def
    usages: dict[str, str] = field(default_factory=dict)  # usage name → part def element id
    connects: list[tuple[str, str, str, str]] = field(default_factory=list)
    dependencies: list[tuple[str, str]] = field(default_factory=list)  # (element id, element id)


def _unique_def_names(elements: list[Element]) -> dict[str, str]:
    counts: dict[str, int] = {}
    out = {}
    for element in elements:
        base = element.name
        counts[base] = counts.get(base, 0) + 1
        out[element.id] = base if counts[base] == 1 else f"{base} ({element.id})"
    return out


def build_sysml(ad: AD) -> SysmlModel:
    model = SysmlModel(ad.system_name, ad.overview.split(". ")[0] if ad.overview else "")
    parts = [e for e in ad.of_type("component", "device")]
    names = _unique_def_names(parts)
    for element in parts:
        docs = element.description or ""
        if element.paths:
            docs = f"{docs} Code: {', '.join(element.paths[:3])}".strip()
        model.part_defs[element.id] = PartDef(names[element.id], element.id, docs)
        model.usages[element.id] = element.id
    messages = {e.name: e for e in ad.of_type("message")}
    for relation in ad.relations_of("flows"):
        if relation.source not in model.part_defs or relation.target not in model.part_defs:
            continue
        label = relation.description or "message"
        message = messages.get(label)
        item = message.name if message else f"{label} message"
        if item not in model.items:
            details = ""
            if message:
                details = " ".join(f"{k}={v}" for k, v in sorted(message.properties.items()))
            else:
                details = f"inferred from {relation.technology or 'code'}"
            model.items[item] = details
        port_def = f"{item} port"
        model.port_defs.setdefault(port_def, item)
        port_id = re.sub(r"[^A-Za-z0-9_]", "_", item.lower()).strip("_") or "msg"
        out_port = f"{port_id}_out"
        in_port = f"{port_id}_in"
        sender = model.part_defs[relation.source]
        receiver = model.part_defs[relation.target]
        if not any(p.name == out_port for p in sender.ports):
            sender.ports.append(Port(out_port, port_def, False))
        if not any(p.name == in_port for p in receiver.ports):
            receiver.ports.append(Port(in_port, port_def, True))
        connect = (relation.source, out_port, relation.target, in_port)
        if connect not in model.connects:
            model.connects.append(connect)
    for relation in ad.relations_of("depends"):
        if relation.source in model.part_defs and relation.target in model.part_defs:
            model.dependencies.append((relation.source, relation.target))
    return model


def render_sysml(model: SysmlModel) -> str:
    lines = [
        "// Generated by archdiag. Edit the code or archdiag.toml, not this file.",
        f"package {name(model.package)} {{",
    ]
    if model.documentation:
        lines.append(f"    {doc(model.documentation)}")
    if model.items:
        lines += ["", "    // Messages"]
        for item, details in sorted(model.items.items()):
            body = f" {{ {doc(details)} }}" if details else ";"
            lines.append(f"    item def {name(item)}{body}")
    if model.port_defs:
        lines += ["", "    // Interfaces"]
        for port_def, item in sorted(model.port_defs.items()):
            lines.append(f"    port def {name(port_def)} {{ out item payload : {name(item)}; }}")
    lines += ["", "    // Components"]
    for part in model.part_defs.values():
        lines.append(f"    part def {name(part.name)} {{")
        if part.documentation:
            lines.append(f"        {doc(part.documentation)}")
        for port in part.ports:
            conj = "~" if port.conjugated else ""
            lines.append(f"        port {name(port.name)} : {conj}{name(port.type)};")
        lines.append("    }")
    system = "System"
    taken = {p.name for p in model.part_defs.values()}
    while system in taken:
        system = f"{system}_"
    lines += ["", f"    part def {name(system)} {{"]
    for usage, element_id in model.usages.items():
        lines.append(f"        part {name(usage)} : {name(model.part_defs[element_id].name)};")
    for source, out_port, target, in_port in model.connects:
        lines.append(
            f"        connect {name(source)}.{name(out_port)} to {name(target)}.{name(in_port)};"
        )
    lines += ["    }", f"    part system : {name(system)};"]
    if model.dependencies:
        lines += ["", "    // Static dependencies (from #include and type use)"]
        for source, target in model.dependencies:
            lines.append(
                f"    dependency from {name(model.part_defs[source].name)} to "
                f"{name(model.part_defs[target].name)};"
            )
    lines.append("}")
    return "\n".join(lines) + "\n"


def check_sysml(model: SysmlModel, result: ExportResult) -> None:
    for port_def, item in model.port_defs.items():
        if item not in model.items:
            result.error(f"port def '{port_def}' carries undefined item def '{item}'.")
    for part in model.part_defs.values():
        for port in part.ports:
            if port.type not in model.port_defs:
                result.error(
                    f"part def '{part.name}' uses undefined port def '{port.type}'.",
                    part.element_id,
                )
    for usage, element_id in model.usages.items():
        if element_id not in model.part_defs:
            result.error(f"part '{usage}' is typed by an undefined part def.", element_id)
    for source, out_port, target, in_port in model.connects:
        sender = model.part_defs.get(model.usages.get(source, ""))
        receiver = model.part_defs.get(model.usages.get(target, ""))
        if sender is None or receiver is None:
            result.error(
                f"connect {source}.{out_port} to {target}.{in_port} refers to an unknown part."
            )
            continue
        a = next((p for p in sender.ports if p.name == out_port), None)
        b = next((p for p in receiver.ports if p.name == in_port), None)
        if a is None or b is None:
            result.error(
                f"connect {source}.{out_port} to {target}.{in_port} refers to an unknown port."
            )
        elif a.conjugated or not b.conjugated or a.type != b.type:
            result.error(
                f"connect {source}.{out_port} to {target}.{in_port} must join an out "
                "port to a conjugated port of the same port def."
            )
    for source, target in model.dependencies:
        if source not in model.part_defs or target not in model.part_defs:
            result.error(f"dependency {source} → {target} refers to an undefined part def.")


def _external_check(text: str, command: str, result: ExportResult) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.sysml"
        path.write_text(text, encoding="utf-8")
        argv = [part.replace("{file}", str(path)) for part in shlex.split(command)]
        if not any(str(path) in a for a in argv):
            argv.append(str(path))
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            result.note(f"The SysML validator could not run ({exc}); internal checks only.")
            return
    if completed.returncode != 0:
        output = (completed.stdout + completed.stderr).strip().splitlines()[-10:]
        result.error("The external SysML validator rejected model.sysml: " + " | ".join(output))
    else:
        result.note("model.sysml passed the external SysML v2 validator.")


def export_sysml(ad: AD, validator: str | None = None) -> ExportResult:
    result = ExportResult("sysml")
    model = build_sysml(ad)
    text = render_sysml(model)
    check_sysml(model, result)
    if validator:
        _external_check(text, validator, result)
    else:
        result.note(
            "No external SysML v2 validator configured ([sysml] validator); internal checks only."
        )
    result.files["sysml/model.sysml"] = text
    result.covered.update(e for e in model.part_defs if ad.elements[e].type == "component")
    result.views.append(
        ADView(
            "sysml-structure",
            "sysml-structure",
            "SysML v2: structure and interfaces",
            "Part definitions for the components, item and port definitions for the messages they "
            "exchange, and the connections between their ports.",
            element_ids=list(model.part_defs),
            files=["sysml/model.sysml"],
        )
    )
    return result
