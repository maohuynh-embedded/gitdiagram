"""The viewpoint library and the default stakeholders and concerns (spec C §4, §5)."""

from __future__ import annotations

from archdiag.ad.model import Concern, Stakeholder, Viewpoint

DEFAULT_CONCERNS = [
    Concern("structure", "How is the software divided into parts, and what depends on what?"),
    Concern("interfaces", "Which interfaces do the parts offer and use?"),
    Concern("layering", "Does the code respect the intended layers and rules?"),
    Concern("decisions", "Which architecture decisions were made, and why?"),
    Concern("deployment", "Which build targets exist, and which libraries do they use?"),
    Concern("communication", "What does the software exchange with devices and buses?"),
    Concern("testability", "Which parts are covered by tests, and where are integration seams?"),
    Concern("safety", "Where are ASIL levels allocated, and is freedom from interference argued?"),
    Concern("traceability", "Which requirements are implemented and verified where?"),
]

DEFAULT_STAKEHOLDERS = [
    Stakeholder("developer", "Developer", ["structure", "interfaces"]),
    Stakeholder("architect", "Architect", ["structure", "layering", "decisions"]),
    Stakeholder("integrator", "Integrator", ["deployment", "communication"]),
    Stakeholder("tester", "Tester", ["testability", "interfaces"]),
    Stakeholder("safety_engineer", "Safety engineer", ["safety", "traceability"]),
]

VIEWPOINTS = [
    Viewpoint(
        "c4-context",
        "C4 system context",
        "C1",
        "C4",
        ["deployment", "communication"],
        ["C4 context diagram"],
        ["system", "person", "external_system", "device"],
        ["interacts", "flows"],
        ["c4-elements-traceable"],
    ),
    Viewpoint(
        "c4-container",
        "C4 containers",
        "C1",
        "C4",
        ["deployment", "structure"],
        ["C4 container diagram"],
        ["container", "external", "device", "person", "external_system"],
        ["depends", "flows", "interacts"],
        ["c4-elements-traceable"],
    ),
    Viewpoint(
        "c4-component",
        "C4 components",
        "C1",
        "C4",
        ["structure", "interfaces", "layering"],
        ["C4 component diagram"],
        ["component"],
        ["depends", "flows"],
        ["c4-archimate", "c4-sysml"],
    ),
    Viewpoint(
        "c4-code",
        "C4 code",
        "C1",
        "C4 / UML class",
        ["interfaces"],
        ["UML class diagram"],
        ["class", "interface"],
        ["specializes", "realizes", "composes"],
        [],
    ),
    Viewpoint(
        "archimate-application",
        "ArchiMate application cooperation",
        "C2",
        "ArchiMate",
        ["structure", "interfaces", "communication"],
        ["ArchiMate view"],
        ["component", "interface", "layer"],
        ["depends", "flows", "contains"],
        ["c4-archimate"],
    ),
    Viewpoint(
        "archimate-technology",
        "ArchiMate technology",
        "C2",
        "ArchiMate",
        ["deployment", "communication"],
        ["ArchiMate view"],
        ["container", "device", "bus", "external"],
        ["realizes", "interacts"],
        [],
    ),
    Viewpoint(
        "sysml-structure",
        "SysML v2 structure and interfaces",
        "C3",
        "SysML v2",
        ["structure", "interfaces", "communication"],
        ["SysML v2 textual model"],
        ["component", "message"],
        ["composes", "flows", "depends"],
        ["c4-sysml"],
    ),
    Viewpoint(
        "autosar-swc",
        "AUTOSAR-style software components",
        "C4",
        "AUTOSAR (derived)",
        ["interfaces", "communication", "layering"],
        ["SWC composition"],
        ["component", "message", "interface"],
        ["flows", "realizes"],
        ["autosar-mapping"],
    ),
    Viewpoint(
        "safety-architecture",
        "Safety architecture (ISO 26262 / ASPICE)",
        "C4",
        "ISO 26262",
        ["safety", "traceability"],
        ["ASIL allocation", "trace matrix"],
        ["component", "requirement", "test"],
        ["satisfies", "verifies", "depends"],
        ["safety-traceable"],
    ),
]

NOTATION_OF_STAGE = {
    "C1": "c4",
    "C2": "archimate",
    "C3": "sysml",
    "C4": "autosar,safety",
}
