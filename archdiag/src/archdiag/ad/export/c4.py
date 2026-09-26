"""C4 model: Mermaid C4, Structurizr DSL and C4-PlantUML for context, container, component,
plus a UML class diagram for the code level.

Libraries are not C4 elements; they appear in the technology of the containers that use them.
"""

from __future__ import annotations

from collections import Counter

from archdiag.ad.build import SYSTEM_ID
from archdiag.ad.model import AD, ADView, Element, Relation
from archdiag.ad.validate import ExportResult, plain_text

MAX_COMPONENTS = 60
_EXTERNAL_TYPES = ("device", "external_system")


def _t(text: str, limit: int = 160) -> str:
    return plain_text(text, limit)


class _C4:
    def __init__(self, ad: AD) -> None:
        self.ad = ad
        self.containers = ad.of_type("container")
        self.parent = {e.id: e.parent for e in ad.elements.values()}
        self.components = [e for e in ad.of_type("component") if e.parent]
        self.externals = [e for e in ad.elements.values() if e.type in _EXTERNAL_TYPES]
        self.people = ad.of_type("person")

    def container_of(self, element_id: str) -> str | None:
        current: str | None = element_id
        while current is not None:
            element = self.ad.elements.get(current)
            if element is None:
                return None
            if element.type == "container":
                return element.id
            current = element.parent
        return None

    def lifted(self, level: str) -> list[tuple[str, str, str, str]]:
        """(source, target, description, technology) between elements of the given level."""
        pairs: dict[tuple[str, str], tuple[Counter[str], Counter[str]]] = {}
        for relation in self.ad.relations_of("depends", "flows", "interacts"):
            source, target = self.lift(relation.source, level), self.lift(relation.target, level)
            if not source or not target or source == target:
                continue
            labels, techs = pairs.setdefault((source, target), (Counter(), Counter()))
            labels[relation.description or relation.type] += relation.weight
            if relation.technology:
                techs[relation.technology] += relation.weight
        out = []
        for (source, target), (labels, techs) in sorted(pairs.items()):
            description = ", ".join(label for label, _ in labels.most_common(2))
            technology = ", ".join(t for t, _ in techs.most_common(2))
            out.append((source, target, description or "uses", technology))
        return out

    def lift(self, element_id: str, level: str) -> str | None:
        element = self.ad.elements.get(element_id)
        if element is None:
            return None
        if element.type in (*_EXTERNAL_TYPES, "person"):
            return element.id
        if level == "system":
            return SYSTEM_ID if self.container_of(element_id) or element.type == "system" else None
        if level == "container":
            return self.container_of(element_id)
        return element.id if element.type == "component" else None


def _mermaid_element(kind: str, element: Element, technology: bool = False) -> str:
    tech = f', "{_t(element.properties.get("technology", ""), 60)}"' if technology else ""
    return f'{kind}({element.id}, "{_t(element.name, 60)}"{tech}, "{_t(element.description)}")'


def _external_kind(element: Element) -> str:
    return "System_Ext"


def _context(c4: _C4, result: ExportResult) -> None:
    ad = c4.ad
    system = ad.elements[SYSTEM_ID]
    relations = c4.lifted("system")
    lines = ["C4Context", f"  title System context: {_t(system.name, 80)}"]
    puml = ["@startuml", "!include <C4/C4_Context>", f"title System context: {_t(system.name)}"]
    for person in c4.people:
        lines.append(f"  {_mermaid_element('Person', person)}")
        puml.append(_mermaid_element("Person", person))
    lines.append(f"  {_mermaid_element('System', system)}")
    puml.append(_mermaid_element("System", system))
    for external in c4.externals:
        lines.append(f"  {_mermaid_element(_external_kind(external), external)}")
        puml.append(_mermaid_element(_external_kind(external), external))
    for source, target, description, technology in relations:
        rel = f'Rel({source}, {target}, "{_t(description, 60)}", "{_t(technology, 40)}")'
        lines.append(f"  {rel}")
        puml.append(rel)
    puml += ["SHOW_LEGEND()", "@enduml"]
    ids = [SYSTEM_ID, *(p.id for p in c4.people), *(e.id for e in c4.externals)]
    mermaid = "\n".join(lines) + "\n"
    result.files["c4/context.mmd"] = mermaid
    result.files["c4/context.puml"] = "\n".join(puml) + "\n"
    result.views.append(
        ADView(
            "c4-context",
            "c4-context",
            "System context",
            "The system, the people and the external systems around it.",
            element_ids=ids,
            files=["c4/context.mmd", "c4/context.puml"],
            mermaid=mermaid,
        )
    )
    result.covered.update(ids)


def _container(c4: _C4, result: ExportResult) -> None:
    ad = c4.ad
    system = ad.elements[SYSTEM_ID]
    if not c4.containers:
        result.error("The model has no container (build target); the container view is empty.")
    relations = c4.lifted("container")
    lines = ["C4Container", f"  title Containers: {_t(system.name, 80)}"]
    puml = ["@startuml", "!include <C4/C4_Container>", f"title Containers: {_t(system.name)}"]
    for person in c4.people:
        lines.append(f"  {_mermaid_element('Person', person)}")
        puml.append(_mermaid_element("Person", person))
    lines.append(f'  System_Boundary({SYSTEM_ID}, "{_t(system.name, 60)}") {{')
    puml.append(f'System_Boundary({SYSTEM_ID}, "{_t(system.name, 60)}") {{')
    for container in c4.containers:
        lines.append(f"    {_mermaid_element('Container', container, technology=True)}")
        puml.append(f"  {_mermaid_element('Container', container, technology=True)}")
    lines.append("  }")
    puml.append("}")
    for external in c4.externals:
        lines.append(f"  {_mermaid_element(_external_kind(external), external)}")
        puml.append(_mermaid_element(_external_kind(external), external))
    for source, target, description, technology in relations:
        rel = f'Rel({source}, {target}, "{_t(description, 60)}", "{_t(technology, 40)}")'
        lines.append(f"  {rel}")
        puml.append(rel)
    puml += ["SHOW_LEGEND()", "@enduml"]
    ids = [c.id for c in c4.containers] + [p.id for p in c4.people] + [e.id for e in c4.externals]
    mermaid = "\n".join(lines) + "\n"
    result.files["c4/container.mmd"] = mermaid
    result.files["c4/container.puml"] = "\n".join(puml) + "\n"
    result.views.append(
        ADView(
            "c4-container",
            "c4-container",
            "Containers",
            "Build targets (containers), their technology and what they talk to.",
            element_ids=ids,
            files=["c4/container.mmd", "c4/container.puml"],
            mermaid=mermaid,
        )
    )
    result.covered.update(ids)


def _components(c4: _C4, result: ExportResult) -> None:
    relations = c4.lifted("component")
    for container in c4.containers:
        members = [c for c in c4.components if c.parent == container.id]
        if not members:
            continue
        if len(members) > MAX_COMPONENTS:
            result.warning(
                f"{container.name} has {len(members)} components; the view shows "
                f"the first {MAX_COMPONENTS}.",
                container.id,
            )
            members = members[:MAX_COMPONENTS]
        inside = {m.id for m in members}
        outside: set[str] = set()
        rels = []
        for source, target, description, technology in relations:
            if source in inside or target in inside:
                rels.append((source, target, description, technology))
                outside.update(x for x in (source, target) if x not in inside)
        lines = ["C4Component", f"  title Components: {_t(container.name, 80)}"]
        puml = [
            "@startuml",
            "!include <C4/C4_Component>",
            f"title Components: {_t(container.name)}",
        ]
        lines.append(f'  Container_Boundary({container.id}, "{_t(container.name, 60)}") {{')
        puml.append(f'Container_Boundary({container.id}, "{_t(container.name, 60)}") {{')
        for member in members:
            lines.append(f"    {_mermaid_element('Component', member, technology=True)}")
            puml.append(f"  {_mermaid_element('Component', member, technology=True)}")
        lines.append("  }")
        puml.append("}")
        for other_id in sorted(outside):
            other = c4.ad.elements[other_id]
            if other.type == "component":
                kind = "Component_Ext"
                entry = _mermaid_element(kind, other, technology=True)
            elif other.type == "person":
                entry = _mermaid_element("Person", other)
            else:
                entry = _mermaid_element("System_Ext", other)
            lines.append(f"  {entry}")
            puml.append(entry)
        for source, target, description, technology in rels:
            rel = f'Rel({source}, {target}, "{_t(description, 60)}", "{_t(technology, 40)}")'
            lines.append(f"  {rel}")
            puml.append(rel)
        puml += ["SHOW_LEGEND()", "@enduml"]
        name = f"c4/component-{container.id}"
        mermaid = "\n".join(lines) + "\n"
        result.files[f"{name}.mmd"] = mermaid
        result.files[f"{name}.puml"] = "\n".join(puml) + "\n"
        result.views.append(
            ADView(
                f"c4-component-{container.id}",
                "c4-component",
                f"Components of {container.name}",
                "Components inside one container and their static dependencies.",
                element_ids=sorted(inside | outside),
                files=[f"{name}.mmd", f"{name}.puml"],
                mermaid=mermaid,
            )
        )
        result.covered.update(inside)


def _code(c4: _C4, result: ExportResult) -> None:
    ad = c4.ad
    arrows = {"specializes": "--|>", "realizes": "..|>", "composes": "*--"}
    for component in ad.of_type("component"):
        classes = ad.children(component.id, "class", "interface")
        ids = {c.id for c in classes}
        relations = [r for r in ad.relations_of(*arrows) if r.source in ids and r.target in ids]
        if len(classes) < 2 or not relations:
            continue
        lines = ["classDiagram", "  direction BT"]
        for element in classes:
            lines.append(f'  class {element.id}["{_t(element.name, 60).replace("`", "")}"]')
            if element.type == "interface":
                lines.append(f"  <<interface>> {element.id}")
        for relation in relations:
            if relation.type == "composes":
                lines.append(f"  {relation.target} {arrows['composes']} {relation.source}")
            else:
                lines.append(f"  {relation.source} {arrows[relation.type]} {relation.target}")
        mermaid = "\n".join(lines) + "\n"
        path = f"c4/code-{component.id}.mmd"
        result.files[path] = mermaid
        result.views.append(
            ADView(
                f"c4-code-{component.id}",
                "c4-code",
                f"Code: {component.name}",
                "Classes and interfaces inside one component.",
                element_ids=sorted(ids),
                relation_ids=[r.id for r in relations],
                files=[path],
                mermaid=mermaid,
            )
        )


def _structurizr(c4: _C4, result: ExportResult) -> None:
    ad = c4.ad
    system = ad.elements[SYSTEM_ID]

    def ident(element_id: str) -> str:
        return f"e_{element_id}"

    lines = [f'workspace "{_t(system.name, 80)}" "{_t(system.description)}" {{', "", "    model {"]
    for person in c4.people:
        lines.append(
            f'        {ident(person.id)} = person "{_t(person.name, 60)}" '
            f'"{_t(person.description)}"'
        )
    lines.append(
        f'        {ident(SYSTEM_ID)} = softwareSystem "{_t(system.name, 60)}" '
        f'"{_t(system.description)}" {{'
    )
    for container in c4.containers:
        lines.append(
            f'            {ident(container.id)} = container "{_t(container.name, 60)}" '
            f'"{_t(container.description)}" '
            f'"{_t(container.properties.get("technology", ""), 60)}" {{'
        )
        for member in (c for c in c4.components if c.parent == container.id):
            lines.append(
                f"                {ident(member.id)} = component "
                f'"{_t(member.name, 60)}" "{_t(member.description)}" '
                f'"{_t(member.properties.get("technology", ""), 60)}"'
            )
        lines.append("            }")
    lines.append("        }")
    for external in c4.externals:
        lines.append(
            f'        {ident(external.id)} = softwareSystem "{_t(external.name, 60)}" '
            f'"{_t(external.description)}" "External"'
        )
    lines.append("")
    known = {
        SYSTEM_ID,
        *(e.id for e in c4.people),
        *(e.id for e in c4.externals),
        *(c.id for c in c4.containers),
        *(c.id for c in c4.components),
    }
    for source, target, description, technology in c4.lifted("component"):
        if source in known and target in known:
            lines.append(
                f'        {ident(source)} -> {ident(target)} "{_t(description, 60)}" '
                f'"{_t(technology, 40)}"'
            )
    for relation in ad.relations_of("interacts"):
        if (
            relation.source in known
            and relation.target in known
            and (ad.elements[relation.source].type == "person")
        ):
            lines.append(
                f"        {ident(relation.source)} -> {ident(relation.target)} "
                f'"{_t(relation.description or "uses", 60)}"'
            )
    lines += [
        "    }",
        "",
        "    views {",
        f'        systemContext {ident(SYSTEM_ID)} "SystemContext" {{',
        "            include *",
        "            autolayout lr",
        "        }",
        f'        container {ident(SYSTEM_ID)} "Containers" {{',
        "            include *",
        "            autolayout lr",
        "        }",
    ]
    for container in c4.containers:
        if any(c.parent == container.id for c in c4.components):
            lines += [
                f'        component {ident(container.id)} "Components_{container.id}" {{',
                "            include *",
                "            autolayout tb",
                "        }",
            ]
    lines += [
        "        styles {",
        '            element "Person" {',
        "                shape person",
        "            }",
        '            element "External" {',
        "                background #8a8a8a",
        "                color #ffffff",
        "            }",
        "        }",
        "    }",
        "}",
    ]
    result.files["c4/workspace.dsl"] = "\n".join(lines) + "\n"


def validate_c4(ad: AD, result: ExportResult) -> None:
    for view in result.views:
        if view.viewpoint_id == "c4-context":
            wrong = [
                i
                for i in view.element_ids
                if ad.elements[i].type not in ("system", "person", *_EXTERNAL_TYPES)
            ]
            for element_id in wrong:
                result.error("A context view may only show people and systems.", element_id)
        names = Counter(ad.elements[i].name for i in view.element_ids if i in ad.elements)
        for name, count in names.items():
            if count > 1:
                result.warning(f"{view.title}: {count} elements are named '{name}'.")
    for relation in ad.relations_of("depends", "flows", "interacts"):
        if not relation.description.strip():
            result.error("Every C4 relationship needs a description.", relation.id)
    for container in ad.of_type("container"):
        if not container.properties.get("technology"):
            result.warning(
                "The container has no technology (no recognised source language).", container.id
            )


def export_c4(ad: AD) -> ExportResult:
    result = ExportResult("c4")
    c4 = _C4(ad)
    _context(c4, result)
    _container(c4, result)
    _components(c4, result)
    _code(c4, result)
    _structurizr(c4, result)
    validate_c4(ad, result)
    return result


def relations_between(ad: AD, ids: set[str]) -> list[Relation]:
    return [r for r in ad.relations if r.source in ids and r.target in ids]
