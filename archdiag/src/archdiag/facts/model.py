"""Facts: the smallest deterministic observations about the code, each traceable to file:line."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

# Fact kinds
INCLUDE = "include"
SYMBOL = "symbol"
INHERITS = "inherits"
INTERFACE = "interface"
FIELD_TYPE = "field_type"
BUILD_TARGET = "build_target"
INCLUDE_DIR = "include_dir"
MESSAGE = "message"
MESSAGE_IO = "message_io"
CALL = "call"

# Sources
TREESITTER = "treesitter"
CLANG = "clang"
BUILD = "build"
PROTOCOL_FILE = "protocol-file"
PATTERN = "pattern"

# Kinds that libclang reproduces more precisely than tree-sitter.
STRUCTURAL_KINDS = frozenset({INCLUDE, SYMBOL, INHERITS, INTERFACE, FIELD_TYPE})


class Confidence(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @property
    def label(self) -> str:
        return self.name.lower()

    def lowered(self) -> Confidence:
        return Confidence(max(Confidence.LOW, self - 1))


@dataclass(frozen=True)
class Fact:
    kind: str
    file: str
    line: int
    source: str
    confidence: Confidence
    data: dict[str, Any] = field(default_factory=dict, hash=False, compare=True)

    def to_row(self) -> tuple[str, str, int, str, int, str]:
        return (self.file, self.kind, self.line, self.source, int(self.confidence),
                json.dumps(self.data, sort_keys=True))

    @classmethod
    def from_row(cls, row: tuple[str, str, int, str, int, str]) -> Fact:
        file, kind, line, source, confidence, data = row
        return cls(kind, file, line, source, Confidence(confidence), json.loads(data))
