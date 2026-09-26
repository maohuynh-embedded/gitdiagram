from typing import Any

from archdiag.arch.aggregate import ACTOR_ID, aggregate
from archdiag.arch.components import Component
from archdiag.facts.model import (
    FIELD_TYPE,
    INCLUDE,
    INHERITS,
    INTERFACE,
    MESSAGE,
    MESSAGE_IO,
    PATTERN,
    SYMBOL,
    TREESITTER,
    Confidence,
    Fact,
)

COMPONENTS = [
    Component("app", "app", ("app",), ("app/main.cpp", "app/app.h"), "app", "module", "inferred"),
    Component("ports", "ports", ("ports",), ("ports/can_port.h",), "ports", "module",
              "inferred"),
    Component("can", "infra/can", ("infra/can",), ("infra/can/driver.h", "infra/can/driver.cpp"),
              "infra", "module", "inferred"),
    Component("tests", "tests", ("tests",), ("tests/t.cpp",), "tests", "test", "inferred"),
]


def f(kind: str, file: str, line: int, data: dict[str, Any],
      confidence: Confidence = Confidence.MEDIUM, source: str = TREESITTER) -> Fact:
    return Fact(kind, file, line, source, confidence, data)


FACTS = [
    f(INCLUDE, "app/main.cpp", 1, {"spelled": "can_port.h", "target": "ports/can_port.h"}),
    f(INCLUDE, "app/main.cpp", 2, {"spelled": "driver.h", "target": "infra/can/driver.h"},
      Confidence.LOW),
    f(INCLUDE, "app/main.cpp", 3, {"spelled": "app.h", "target": "app/app.h"}),
    f(INCLUDE, "app/main.cpp", 4, {"spelled": "QObject", "system": True, "target": None,
                                   "external": "Qt"}),
    f(INCLUDE, "infra/can/driver.h", 1, {"spelled": "can_port.h", "target": "ports/can_port.h"}),
    f(INCLUDE, "tests/t.cpp", 1, {"spelled": "driver.h", "target": "infra/can/driver.h"}),
    f(INCLUDE, "tests/t.cpp", 2, {"spelled": "gtest.h", "target": None, "external": "gtest"}),
    f(SYMBOL, "ports/can_port.h", 3, {"kind": "class", "name": "CanPort"}),
    f(INTERFACE, "ports/can_port.h", 3, {"class": "CanPort"}),
    f(SYMBOL, "infra/can/driver.h", 5, {"kind": "class", "name": "infra::Driver"}),
    f(INHERITS, "infra/can/driver.h", 5, {"child": "infra::Driver", "base": "CanPort",
                                         "access": "public"}),
    f(SYMBOL, "app/app.h", 2, {"kind": "class", "name": "App"}),
    f(FIELD_TYPE, "app/app.h", 4, {"owner": "App", "types": ["CanPort", "Unknown"]}),
    f(SYMBOL, "infra/can/driver.cpp", 9, {"kind": "function", "name": "Driver::sendFrame"}),
    f(MESSAGE, "ports/can_port.h", 1, {"protocol": "canopen", "name": "SDO_TX", "id": 0x600},
      source=PATTERN),
    f(MESSAGE_IO, "app/main.cpp", 10, {"protocol": "can", "direction": "send",
                                      "function": "sendFrame", "role": "call",
                                      "args": ["SDO_TX"]}, Confidence.LOW, PATTERN),
    f(MESSAGE_IO, "infra/can/driver.cpp", 9, {"protocol": "can", "direction": "send",
                                             "function": "sendFrame", "role": "definition",
                                             "args": []}, Confidence.LOW, PATTERN),
    f(MESSAGE_IO, "tests/t.cpp", 5, {"protocol": "can", "direction": "send",
                                    "function": "sendFrame", "role": "call", "args": []},
      Confidence.LOW, PATTERN),
]


def run() -> Any:
    return aggregate(COMPONENTS, FACTS, {"app/main.cpp": 20, "app/app.h": 5}, "demo")


def edge(result: Any, source: str, target: str, kind: str) -> Any:
    return next(e for e in result.edges if (e.source, e.target, e.kind) == (source, target, kind))


def test_component_edges_carry_weight_confidence_and_evidence() -> None:
    result = run()
    app_ports = edge(result, "app", "ports", "depends")
    # one include + one composition (App has CanPort)
    assert app_ports.weight == 2
    assert app_ports.confidence == "medium"
    assert [(e.file, e.line, e.fact) for e in app_ports.evidence] == [
        ("app/app.h", 4, "field_type"), ("app/main.cpp", 1, "include"),
    ]
    assert edge(result, "app", "can", "depends").confidence == "low"
    can_ports = edge(result, "can", "ports", "depends")
    assert can_ports.weight == 2  # include + inheritance
    assert not any(e.source == "app" and e.target == "app" for e in result.edges)


def test_tests_get_test_edges_and_no_externals() -> None:
    result = run()
    assert edge(result, "tests", "can", "test").weight == 1
    assert not any(e.source == "tests" and e.kind in ("depends", "uses") for e in result.edges)
    assert not any(n.id == "ext_gtest" for n in result.nodes)


def test_external_nodes_and_edges() -> None:
    result = run()
    qt = next(n for n in result.nodes if n.id == "ext_qt")
    assert (qt.level, qt.label) == ("external", "Qt")
    assert edge(result, "app", "ext_qt", "uses").weight == 1


def test_classes_interfaces_and_class_edges() -> None:
    result = run()
    port = next(n for n in result.nodes if n.id == "cls_canport")
    assert (port.kind, port.level, port.group) == ("interface", "class", "ports")
    assert port.metrics["references"] == 2
    assert edge(result, "cls_infra_driver", "cls_canport", "implements").weight == 1
    assert edge(result, "cls_app", "cls_canport", "composes").weight == 1


def test_messages_link_callers_definers_and_the_device() -> None:
    result = run()
    call = edge(result, "app", "can", "message")
    assert call.label == "SDO_TX"
    assert call.confidence == "medium"
    assert edge(result, "can", ACTOR_ID, "message").confidence == "low"
    assert any(n.id == ACTOR_ID for n in result.nodes)
    message = next(m for m in result.messages if m.name == "SDO_TX")
    assert (message.message_id, message.senders) == (0x600, ["app"])
    assert not any(e.source == "tests" and e.kind == "message" for e in result.edges)


def test_nodes_have_layers_groups_and_metrics() -> None:
    result = run()
    app = next(n for n in result.nodes if n.id == "app")
    assert (app.group, app.layer, app.paths) == ("layer_app", "app", ["app"])
    assert app.metrics == {"files": 2, "lines": 25}
    assert [g.id for g in result.groups] == ["layer_app", "layer_ports", "layer_infra",
                                             "layer_tests"]
