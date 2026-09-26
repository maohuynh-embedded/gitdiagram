"""tree-sitter extractor for C and C++: runs without any build configuration."""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import cache
from typing import Any

import tree_sitter_c
import tree_sitter_cpp
from tree_sitter import Language, Node, Parser

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
from archdiag.facts.protocol_config import ProtocolConfig

EXTRACTOR_VERSION = "ts-1"

C_LANGUAGES = frozenset({"c"})
CPP_LANGUAGES = frozenset({"c-header", "cpp", "cpp-header"})
EXTRACTABLE = C_LANGUAGES | CPP_LANGUAGES

_QT_LINE_MACRO = re.compile(rb"^[ \t]*Q_[A-Z_]+[ \t]*(?:\([^\n]*\))?[ \t]*;?[ \t]*$", re.M)
_QT_TOKEN = re.compile(rb"\b(?:Q_INVOKABLE|Q_SIGNAL|Q_SLOT|Q_EMIT|emit)\b")
_QT_SLOTS = re.compile(rb"\b(public|protected|private)([ \t]+(?:slots|Q_SLOTS))([ \t]*:)")
_QT_SIGNALS = re.compile(rb"\b(?:signals|Q_SIGNALS)[ \t]*:")
_EXPORT_MACRO = re.compile(rb"\b(class|struct)([ \t]+)([A-Z][A-Z0-9_]*_(?:EXPORT|API|DLL)\w*)")
_INT_SUFFIX = re.compile(r"[uUlL]+$")
_CLASS_TYPES = {
    "class_specifier": "class",
    "struct_specifier": "struct",
    "union_specifier": "union",
}
_CONDITIONAL = {"preproc_if", "preproc_ifdef", "preproc_elif", "preproc_else", "preproc_elifdef"}
_TYPE_NAME_NODES = {"type_identifier"}
_MAX_ARG_CHARS = 60


def strip_qt_macros(src: bytes) -> bytes:
    """Blank Qt macros that confuse the C++ grammar, keeping every byte offset and line."""

    def blank(match: re.Match[bytes]) -> bytes:
        return b" " * len(match.group(0))

    src = _QT_LINE_MACRO.sub(blank, src)
    src = _QT_TOKEN.sub(blank, src)
    src = _QT_SLOTS.sub(lambda m: m.group(1) + b" " * len(m.group(2)) + m.group(3), src)
    src = _QT_SIGNALS.sub(lambda m: b"public:".ljust(len(m.group(0))), src)
    src = _EXPORT_MACRO.sub(lambda m: m.group(1) + m.group(2) + b" " * len(m.group(3)), src)
    return src


@cache
def _parser(kind: str) -> Parser:
    grammar = tree_sitter_c.language() if kind == "c" else tree_sitter_cpp.language()
    return Parser(Language(grammar))


def _parse_int(text: str) -> int | None:
    cleaned = _INT_SUFFIX.sub("", text.strip())
    try:
        return int(cleaned, 0)
    except ValueError:
        return None


class _Extractor:
    def __init__(self, path: str, src: bytes, protocols: ProtocolConfig) -> None:
        self.path = path
        self.src = src
        self.protocols = protocols
        self.facts: list[Fact] = []

    def text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self.src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def add(
        self,
        kind: str,
        node: Node,
        data: dict[str, Any],
        confidence: Confidence = Confidence.MEDIUM,
        source: str = TREESITTER,
    ) -> None:
        self.facts.append(Fact(kind, self.path, node.start_point.row + 1, source, confidence, data))

    # --- traversal -------------------------------------------------------------------------

    def visit(self, node: Node, ns: tuple[str, ...], owner: str | None, cond: bool) -> None:
        kind = node.type
        if kind == "preproc_include":
            self.include(node, cond)
            return
        if kind in _CONDITIONAL:
            nested = cond or not self.is_include_guard(node)
            for child in node.children:
                self.visit(child, ns, owner, nested)
            return
        if kind == "namespace_definition":
            name = self.text(node.child_by_field_name("name"))
            body = node.child_by_field_name("body")
            if body is not None:
                inner = (*ns, *[part for part in name.split("::") if part])
                for child in body.children:
                    self.visit(child, inner, owner, cond)
            return
        if kind in _CLASS_TYPES and node.child_by_field_name("body") is not None:
            self.class_definition(node, ns, owner, cond, None)
            return
        if kind == "type_definition":
            self.typedef(node, ns, owner, cond)
            return
        if kind == "enum_specifier":
            self.enum(node, ns, owner)
            return
        if kind == "preproc_def":
            self.define(node)
            return
        if kind == "declaration":
            self.constant(node)
        if kind == "function_definition":
            self.function_definition(node, ns, owner)
        if kind == "call_expression":
            self.call(node)
        for child in node.children:
            self.visit(child, ns, owner, cond)

    def is_include_guard(self, node: Node) -> bool:
        if node.type != "preproc_ifdef" or not self.text(node).startswith("#ifndef"):
            return False
        named = node.named_children
        return (
            len(named) >= 2
            and named[1].type == "preproc_def"
            and self.text(named[1].child_by_field_name("name")) == self.text(named[0])
        )

    # --- facts -----------------------------------------------------------------------------

    def include(self, node: Node, cond: bool) -> None:
        path_node = node.child_by_field_name("path")
        if path_node is None:
            return
        raw = self.text(path_node).strip()
        system = path_node.type == "system_lib_string"
        spelled = raw[1:-1] if len(raw) >= 2 and raw[0] in '<"' else raw
        if not spelled:
            return
        confidence = Confidence.MEDIUM.lowered() if cond else Confidence.MEDIUM
        self.add(
            INCLUDE, node, {"spelled": spelled, "system": system, "conditional": cond}, confidence
        )

    def qualify(self, ns: tuple[str, ...], owner: str | None, name: str) -> str:
        if owner:
            return f"{owner}::{name}"
        return "::".join((*ns, name))

    def class_definition(
        self, node: Node, ns: tuple[str, ...], owner: str | None, cond: bool, alias: str | None
    ) -> None:
        name_node = node.child_by_field_name("name")
        name = self.text(name_node) or alias
        if not name:
            return
        qualified = self.qualify(ns, owner, name)
        class_kind = _CLASS_TYPES[node.type]
        self.add(SYMBOL, node, {"kind": class_kind, "name": qualified})
        default_access = "private" if class_kind == "class" else "public"
        for child in node.children:
            if child.type == "base_class_clause":
                self.bases(child, qualified, default_access)
        body = node.child_by_field_name("body")
        pure_virtual = False
        if body is not None:
            for member in body.children:
                if member.type == "field_declaration":
                    pure_virtual |= self.field(member, qualified)
                elif member.type in _CLASS_TYPES and member.child_by_field_name("body"):
                    self.class_definition(member, ns, qualified, cond, None)
                elif member.type in (
                    "function_definition",
                    "declaration",
                    "template_declaration",
                    "enum_specifier",
                    "type_definition",
                ):
                    self.visit(member, ns, qualified, cond)
        if pure_virtual:
            self.add(INTERFACE, node, {"class": qualified})

    def bases(self, clause: Node, child: str, default_access: str) -> None:
        access = default_access
        for part in clause.children:
            if part.type == "access_specifier":
                access = self.text(part)
            elif part.type in ("type_identifier", "qualified_identifier", "template_type"):
                base = self.type_name(part)
                if base:
                    self.add(INHERITS, part, {"child": child, "base": base, "access": access})
                access = default_access

    def type_name(self, node: Node) -> str:
        if node.type == "template_type":
            return self.text(node.child_by_field_name("name"))
        if node.type == "qualified_identifier":
            scope = self.text(node.child_by_field_name("scope"))
            name_node = node.child_by_field_name("name")
            inner = self.type_name(name_node) if name_node is not None else ""
            return f"{scope}::{inner}" if scope else inner
        return self.text(node)

    def field(self, node: Node, owner: str) -> bool:
        declarator = node.child_by_field_name("declarator")
        if declarator is not None and declarator.type == "function_declarator":
            is_virtual = any(self.text(c) == "virtual" for c in node.children)
            is_pure = any(
                c.type == "number_literal" and self.text(c) == "0" for c in node.named_children
            )
            return is_virtual and is_pure
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return False
        names = list(dict.fromkeys(self.type_identifiers(type_node)))
        if names:
            self.add(FIELD_TYPE, node, {"owner": owner, "types": names})
        return False

    def type_identifiers(self, node: Node) -> Iterator[str]:
        if node.type in _TYPE_NAME_NODES:
            yield self.text(node)
            return
        if node.type == "qualified_identifier":
            scope = self.text(node.child_by_field_name("scope"))
            if scope == "std":
                name = node.child_by_field_name("name")
                if name is not None and name.type == "template_type":
                    args = name.child_by_field_name("arguments")
                    if args is not None:
                        yield from self.type_identifiers(args)
                return
        for child in node.named_children:
            if child.type != "namespace_identifier":
                yield from self.type_identifiers(child)

    def typedef(self, node: Node, ns: tuple[str, ...], owner: str | None, cond: bool) -> None:
        type_node = node.child_by_field_name("type")
        alias = self.text(node.child_by_field_name("declarator")).strip("* ")
        if (
            type_node is not None
            and type_node.type in _CLASS_TYPES
            and type_node.child_by_field_name("body") is not None
        ):
            self.class_definition(type_node, ns, owner, cond, alias or None)

    def enum(self, node: Node, ns: tuple[str, ...], owner: str | None) -> None:
        name = self.text(node.child_by_field_name("name"))
        if name:
            self.add(SYMBOL, node, {"kind": "enum", "name": self.qualify(ns, owner, name)})
        body = node.child_by_field_name("body")
        if body is None:
            return
        for enumerator in body.named_children:
            if enumerator.type != "enumerator":
                continue
            value = enumerator.child_by_field_name("value")
            self.message(
                enumerator,
                self.text(enumerator.child_by_field_name("name")),
                self.text(value) if value is not None else "",
            )

    def define(self, node: Node) -> None:
        value = node.child_by_field_name("value")
        self.message(
            node,
            self.text(node.child_by_field_name("name")),
            self.text(value) if value is not None else "",
        )

    def constant(self, node: Node) -> None:
        for child in node.named_children:
            if child.type == "init_declarator":
                value = child.child_by_field_name("value")
                if value is not None and value.type == "number_literal":
                    self.message(
                        child, self.text(child.child_by_field_name("declarator")), self.text(value)
                    )

    def message(self, node: Node, name: str, value: str) -> None:
        number = _parse_int(value) if value else None
        if not name or number is None:
            return
        for pattern in self.protocols.patterns:
            if pattern.is_message_name(name):
                self.add(
                    MESSAGE,
                    node,
                    {"protocol": pattern.name, "name": name, "id": number},
                    Confidence.MEDIUM,
                    PATTERN,
                )
                return

    def declarator_name(self, node: Node) -> str:
        declarator = node.child_by_field_name("declarator")
        while declarator is not None and declarator.type != "function_declarator":
            if declarator.type not in (
                "pointer_declarator",
                "reference_declarator",
                "parenthesized_declarator",
                "attributed_declarator",
            ):
                return ""
            declarator = declarator.child_by_field_name("declarator")
        if declarator is None:
            return ""
        return self.text(declarator.child_by_field_name("declarator"))

    def function_definition(self, node: Node, ns: tuple[str, ...], owner: str | None) -> None:
        name = self.declarator_name(node)
        if not name:
            return
        qualified = name if "::" in name else self.qualify(ns, owner, name)
        self.add(SYMBOL, node, {"kind": "function", "name": qualified})
        self.io(node, name.rsplit("::", 1)[-1], "definition", [])

    def call(self, node: Node) -> None:
        function = node.child_by_field_name("function")
        if function is None:
            return
        if function.type == "field_expression":
            name = self.text(function.child_by_field_name("field"))
        elif function.type == "qualified_identifier":
            name = self.text(function.child_by_field_name("name"))
        elif function.type == "identifier":
            name = self.text(function)
        else:
            return
        arguments = node.child_by_field_name("arguments")
        args = [
            self.text(arg)[:_MAX_ARG_CHARS]
            for arg in (arguments.named_children if arguments is not None else [])[:2]
        ]
        self.io(node, name, "call", args)

    def io(self, node: Node, name: str, role: str, args: list[str]) -> None:
        for pattern in self.protocols.patterns:
            direction = (
                "send" if pattern.is_send(name) else "recv" if pattern.is_recv(name) else None
            )
            if direction:
                self.add(
                    MESSAGE_IO,
                    node,
                    {
                        "protocol": pattern.name,
                        "direction": direction,
                        "function": name,
                        "role": role,
                        "args": args,
                    },
                    Confidence.LOW,
                    PATTERN,
                )
                return


def extract_file(path: str, src: bytes, language: str, protocols: ProtocolConfig) -> list[Fact]:
    parser = _parser("c" if language in C_LANGUAGES else "cpp")
    cleaned = strip_qt_macros(src) if language in CPP_LANGUAGES else src
    tree = parser.parse(cleaned)
    extractor = _Extractor(path, cleaned, protocols)
    extractor.visit(tree.root_node, (), None, False)
    return extractor.facts
