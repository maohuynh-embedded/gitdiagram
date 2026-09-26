from typing import Any

from archdiag.facts.extract_ts import extract_file, strip_qt_macros
from archdiag.facts.model import (
    FIELD_TYPE,
    INCLUDE,
    INHERITS,
    INTERFACE,
    MESSAGE,
    MESSAGE_IO,
    SYMBOL,
    Confidence,
    Fact,
)
from archdiag.facts.protocol_config import load_protocol_config

PROTOCOLS = load_protocol_config(None)

HEADER = b"""#ifndef IMPL_H
#define IMPL_H
#include "ports/can_port.h"
#include <QObject>
#ifdef USE_TRACE
#include "trace.h"
#endif
#define NODE_COB_ID 0x180
namespace app { namespace can {
class CanPort {
 public:
  virtual ~CanPort() = default;
  virtual bool sendFrame(int id) = 0;
};
class Driver : public QObject, public CanPort, private detail::Base<int> {
  Q_OBJECT
  Q_PROPERTY(int speed READ speed NOTIFY speedChanged)
 public:
  Q_INVOKABLE bool sendFrame(int id) override;
 signals:
  void speedChanged();
 public slots:
  void onFrameReceived();
 private:
  Codec* m_codec;
  std::vector<Frame> m_frames;
  int m_count = 0;
};
enum Ids { SDO_TX = 0x600, SDO_RX = 0x580, OTHER = 3 };
}}
#endif
"""

SOURCE = b"""#include "impl.h"
namespace app { namespace can {
static constexpr int OD_SPEED = 0x6081;
bool Driver::sendFrame(int id) { return transmit(id); }
void Driver::onFrameReceived() {}
void run(Driver& d) {
  d.sendFrame(SDO_TX);
  sendSdo(0x601, 1);
  helper();
}
}}
"""

C_SOURCE = b"""#include "can_driver.h"
typedef struct { unsigned id; } can_frame_t;
struct gpio_bank { int pins; };
#define MOTOR_CAN_ID 0x201u
bool can_send(const can_frame_t *f) { return f != 0; }
int main(void) { can_frame_t f; can_send(&f); can_receive(&f); return 0; }
"""


def of(facts: list[Fact], kind: str) -> list[Fact]:
    return [f for f in facts if f.kind == kind]


def data(facts: list[Fact], kind: str) -> list[dict[str, Any]]:
    return [f.data for f in of(facts, kind)]


def test_qt_macros_are_blanked_without_moving_lines() -> None:
    cleaned = strip_qt_macros(HEADER)
    assert len(cleaned) == len(HEADER)
    assert cleaned.count(b"\n") == HEADER.count(b"\n")
    assert b"Q_OBJECT" not in cleaned
    assert b"Q_PROPERTY" not in cleaned
    assert b"public slots" not in cleaned
    assert b"signals:" not in cleaned


def test_includes_and_conditional_flag() -> None:
    facts = extract_file("src/impl.h", HEADER, "cpp-header", PROTOCOLS)
    includes = data(facts, INCLUDE)
    assert includes == [
        {"spelled": "ports/can_port.h", "system": False, "conditional": False},
        {"spelled": "QObject", "system": True, "conditional": False},
        {"spelled": "trace.h", "system": False, "conditional": True},
    ]
    assert [f.line for f in of(facts, INCLUDE)] == [3, 4, 6]
    assert [f.confidence for f in of(facts, INCLUDE)] == [
        Confidence.MEDIUM,
        Confidence.MEDIUM,
        Confidence.LOW,
    ]


def test_classes_bases_interfaces_and_fields() -> None:
    facts = extract_file("src/impl.h", HEADER, "cpp-header", PROTOCOLS)
    symbols = {d["name"]: d["kind"] for d in data(facts, SYMBOL)}
    assert symbols["app::can::CanPort"] == "class"
    assert symbols["app::can::Driver"] == "class"
    assert symbols["app::can::Ids"] == "enum"
    assert data(facts, INHERITS) == [
        {"child": "app::can::Driver", "base": "QObject", "access": "public"},
        {"child": "app::can::Driver", "base": "CanPort", "access": "public"},
        {"child": "app::can::Driver", "base": "detail::Base", "access": "private"},
    ]
    assert data(facts, INTERFACE) == [{"class": "app::can::CanPort"}]
    fields = data(facts, FIELD_TYPE)
    assert {"owner": "app::can::Driver", "types": ["Codec"]} in fields
    assert {"owner": "app::can::Driver", "types": ["Frame"]} in fields
    assert all(f["types"] for f in fields)


def test_messages_from_defines_enums_and_constants() -> None:
    header = extract_file("src/impl.h", HEADER, "cpp-header", PROTOCOLS)
    source = extract_file("src/impl.cpp", SOURCE, "cpp", PROTOCOLS)
    messages = {(d["protocol"], d["name"], d["id"]) for d in data(header + source, MESSAGE)}
    assert ("canopen", "NODE_COB_ID", 0x180) in messages
    assert ("canopen", "SDO_TX", 0x600) in messages
    assert ("canopen", "SDO_RX", 0x580) in messages
    assert ("canopen", "OD_SPEED", 0x6081) in messages
    assert not any(name == "OTHER" for _, name, _ in messages)


def test_message_io_from_calls_and_definitions() -> None:
    facts = extract_file("src/impl.cpp", SOURCE, "cpp", PROTOCOLS)
    io = data(facts, MESSAGE_IO)
    assert {
        "protocol": "can",
        "direction": "send",
        "function": "sendFrame",
        "role": "definition",
        "args": [],
    } in io
    assert {
        "protocol": "can",
        "direction": "send",
        "function": "transmit",
        "role": "call",
        "args": ["id"],
    } in io
    assert {
        "protocol": "can",
        "direction": "send",
        "function": "sendFrame",
        "role": "call",
        "args": ["SDO_TX"],
    } in io
    assert {
        "protocol": "canopen",
        "direction": "send",
        "function": "sendSdo",
        "role": "call",
        "args": ["0x601", "1"],
    } in io
    assert {
        "protocol": "can",
        "direction": "recv",
        "function": "onFrameReceived",
        "role": "definition",
        "args": [],
    } in io
    assert not any(d["function"] == "helper" for d in io)
    assert all(f.confidence == Confidence.LOW for f in of(facts, MESSAGE_IO))


def test_function_symbols_record_methods() -> None:
    facts = extract_file("src/impl.cpp", SOURCE, "cpp", PROTOCOLS)
    functions = {d["name"] for d in data(facts, SYMBOL) if d["kind"] == "function"}
    assert {"Driver::sendFrame", "Driver::onFrameReceived", "app::can::run"} <= functions


def test_c_sources() -> None:
    facts = extract_file("src/main.c", C_SOURCE, "c", PROTOCOLS)
    assert data(facts, INCLUDE)[0]["spelled"] == "can_driver.h"
    kinds = {d["name"]: d["kind"] for d in data(facts, SYMBOL)}
    assert kinds["can_frame_t"] == "struct"
    assert kinds["gpio_bank"] == "struct"
    assert kinds["can_send"] == "function"
    assert ("can", "MOTOR_CAN_ID", 0x201) in {
        (d["protocol"], d["name"], d["id"]) for d in data(facts, MESSAGE)
    }
    io = {(d["function"], d["role"], d["direction"]) for d in data(facts, MESSAGE_IO)}
    assert ("can_send", "definition", "send") in io
    assert ("can_send", "call", "send") in io
    assert ("can_receive", "call", "recv") in io


def test_broken_code_does_not_raise() -> None:
    facts = extract_file("x.cpp", b"class { ;;; #include <", "cpp", PROTOCOLS)
    assert isinstance(facts, list)
