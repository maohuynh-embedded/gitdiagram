"""Name patterns that turn code into protocol facts. Pattern facts are inferences, never proof."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from typing import Any

from archdiag.config import ConfigError


@dataclass(frozen=True)
class ProtocolPattern:
    name: str
    message_name_patterns: tuple[str, ...] = ()
    send_calls: tuple[str, ...] = ()
    recv_calls: tuple[str, ...] = ()

    def is_message_name(self, name: str) -> bool:
        return any(fnmatchcase(name, p) for p in self.message_name_patterns)

    def is_send(self, name: str) -> bool:
        return any(fnmatchcase(name, p) for p in self.send_calls)

    def is_recv(self, name: str) -> bool:
        return any(fnmatchcase(name, p) for p in self.recv_calls)


PRESETS: dict[str, ProtocolPattern] = {
    "can": ProtocolPattern(
        "can",
        message_name_patterns=("*_CAN_ID", "CAN_ID_*", "*_MSG_ID", "MSG_ID_*"),
        send_calls=("can_send*", "can_write*", "*CanSend*", "send*Frame*", "write*Frame*",
                    "transmit*", "*Transmit*"),
        recv_calls=("can_receive*", "can_read*", "*CanReceive*", "on*Frame*", "receive*Frame*",
                    "read*Frame*", "handle*Frame*"),
    ),
    "canopen": ProtocolPattern(
        "canopen",
        message_name_patterns=("*_COB_ID", "COB_ID_*", "OD_*", "SDO_*", "PDO_*", "NMT_*",
                               "*_SDO_*", "*_PDO_*"),
        send_calls=("send*Sdo*", "write*Sdo*", "*SdoWrite*", "*SdoDownload*", "send*Pdo*",
                    "send*Nmt*", "*sendSdo*", "*writeSdo*"),
        recv_calls=("on*Sdo*", "handle*Sdo*", "*SdoRead*", "*SdoUpload*", "on*Pdo*",
                    "handle*Pdo*", "dispatch*"),
    ),
}
DEFAULT_PRESETS = ("can", "canopen")


@dataclass(frozen=True)
class ProtocolConfig:
    patterns: tuple[ProtocolPattern, ...]

    def fingerprint(self) -> str:
        blob = json.dumps([asdict(p) for p in self.patterns], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _strings(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of strings")
    return tuple(value)


def load_protocol_config(data: dict[str, Any] | None, source: str = "archdiag.toml") -> ProtocolConfig:
    section = data or {}
    if not isinstance(section, dict):
        raise ConfigError(f"{source}: [protocol] must be a table")
    presets = _strings(section.get("presets", list(DEFAULT_PRESETS)), f"{source}: protocol.presets")
    patterns: list[ProtocolPattern] = []
    for name in presets:
        if name not in PRESETS:
            raise ConfigError(
                f"{source}: unknown protocol preset '{name}' (known: {', '.join(PRESETS)})"
            )
        patterns.append(PRESETS[name])
    for index, item in enumerate(section.get("code", [])):
        where = f"{source}: protocol.code[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ConfigError(f"{where} needs a name")
        patterns.append(ProtocolPattern(
            item["name"],
            _strings(item.get("message_name_patterns"), f"{where}.message_name_patterns"),
            _strings(item.get("send_calls"), f"{where}.send_calls"),
            _strings(item.get("recv_calls"), f"{where}.recv_calls"),
        ))
    return ProtocolConfig(tuple(patterns))
