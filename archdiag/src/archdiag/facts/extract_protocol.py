"""Protocol description files: CAN DBC and CANopen EDS/DCF."""

from __future__ import annotations

import re
from typing import Any

from archdiag.facts.model import MESSAGE, PROTOCOL_FILE, Confidence, Fact

_DBC_MESSAGE = re.compile(r"^BO_\s+(\d+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\d+\s+(\S+)")
_DBC_SIGNAL = re.compile(r"^SG_\s+.*\s(\S+)\s*$")
_EDS_SECTION = re.compile(r"^\[([0-9A-Fa-f]{4})(?:sub([0-9A-Fa-f]+))?\]$")
_DBC_NO_NODE = "Vector__XXX"
_EXTENDED_FLAG = 0x80000000


def extract_dbc(path: str, text: str) -> list[Fact]:
    facts: list[Fact] = []
    current: dict[str, Any] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        message = _DBC_MESSAGE.match(line)
        if message:
            raw_id = int(message.group(1))
            data: dict[str, Any] = {
                "protocol": "can",
                "id": raw_id & ~_EXTENDED_FLAG,
                "name": message.group(2),
                "senders": [] if message.group(3) == _DBC_NO_NODE else [message.group(3)],
                "receivers": [],
            }
            if raw_id & _EXTENDED_FLAG:
                data["extended"] = True
            current = data
            facts.append(Fact(MESSAGE, path, number, PROTOCOL_FILE, Confidence.HIGH, data))
            continue
        signal = _DBC_SIGNAL.match(line)
        if signal and current is not None:
            for node in signal.group(1).split(","):
                node = node.strip()
                if node and node != _DBC_NO_NODE and node not in current["receivers"]:
                    current["receivers"].append(node)
        elif line and not line.startswith("SG_"):
            current = None
    return facts


def extract_eds(path: str, text: str) -> list[Fact]:
    facts: list[Fact] = []
    section: tuple[int, int | None, int] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith("["):
            match = _EDS_SECTION.match(line)
            section = None
            if match:
                sub = int(match.group(2), 16) if match.group(2) else None
                section = (int(match.group(1), 16), sub, number)
            continue
        if section is None or not line.lower().startswith("parametername"):
            continue
        _, _, value = line.partition("=")
        index, sub, where = section
        data: dict[str, Any] = {"protocol": "canopen", "id": index, "name": value.strip()}
        if sub is not None:
            data["subindex"] = sub
        facts.append(Fact(MESSAGE, path, where, PROTOCOL_FILE, Confidence.HIGH, data))
        section = None
    return facts
