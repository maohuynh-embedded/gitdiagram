"""Parse XML from the repository without entity expansion (billion-laughs) or external entities."""

from __future__ import annotations

import xml.etree.ElementTree as ET

MAX_BYTES = 50 * 1024 * 1024


class UnsafeXML(ValueError):
    pass


def parse_xml(text: str) -> ET.Element:
    if len(text) > MAX_BYTES:
        raise UnsafeXML("the file is larger than 50 MB")
    head = text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in text.upper():
        raise UnsafeXML("DTDs and entities are not accepted")
    return ET.fromstring(text)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(element: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in element if local(c.tag) == name), None)


def child_text(element: ET.Element, name: str) -> str:
    found = child(element, name)
    return (found.text or "").strip() if found is not None else ""


def descendants(element: ET.Element, name: str) -> list[ET.Element]:
    return [e for e in element.iter() if local(e.tag) == name]
