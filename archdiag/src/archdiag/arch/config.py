"""The optional intended architecture declared in [architecture] of archdiag.toml."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from archdiag.config import ConfigError

DEFAULT_MAX_COMPONENT_FILES = 40


@dataclass(frozen=True)
class DeclaredComponent:
    name: str
    paths: tuple[str, ...]
    layer: str | None = None


@dataclass(frozen=True)
class DenyRule:
    source: str
    deny: tuple[str, ...]


@dataclass(frozen=True)
class ArchitectureConfig:
    layers: tuple[str, ...] = ()
    allow_upward: bool = False
    max_component_files: int = DEFAULT_MAX_COMPONENT_FILES
    components: tuple[DeclaredComponent, ...] = ()
    rules: tuple[DenyRule, ...] = ()

    @property
    def declared(self) -> bool:
        return bool(self.layers or self.components or self.rules)


def _strings(value: object, where: str, allow_empty: bool = True) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{where} must be a list of non-empty strings")
    if not value and not allow_empty:
        raise ConfigError(f"{where} must not be empty")
    return tuple(value)


def load_architecture(data: dict[str, Any] | None, source: str = "archdiag.toml"
                      ) -> ArchitectureConfig:
    section = data or {}
    if not isinstance(section, dict):
        raise ConfigError(f"{source}: [architecture] must be a table")
    layers = _strings(section.get("layers"), f"{source}: architecture.layers")
    if len(set(layers)) != len(layers):
        raise ConfigError(f"{source}: architecture.layers has duplicates")
    allow_upward = section.get("allow_upward", False)
    if not isinstance(allow_upward, bool):
        raise ConfigError(f"{source}: architecture.allow_upward must be true or false")
    max_files = section.get("max_component_files", DEFAULT_MAX_COMPONENT_FILES)
    if not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 2:
        raise ConfigError(f"{source}: architecture.max_component_files must be an integer >= 2")

    components: list[DeclaredComponent] = []
    for index, item in enumerate(section.get("components", [])):
        where = f"{source}: architecture.components[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
            raise ConfigError(f"{where} needs a name")
        layer = item.get("layer")
        if layer is not None and (not isinstance(layer, str) or layer not in layers):
            raise ConfigError(f"{where}: layer '{layer}' is not one of architecture.layers")
        components.append(DeclaredComponent(
            item["name"], _strings(item.get("paths"), f"{where}.paths", allow_empty=False), layer
        ))
    names = [c.name for c in components]
    if len(set(names)) != len(names):
        raise ConfigError(f"{source}: architecture.components has duplicate names")

    known = set(layers) | set(names)
    rules: list[DenyRule] = []
    for index, item in enumerate(section.get("rules", [])):
        where = f"{source}: architecture.rules[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("from"), str):
            raise ConfigError(f"{where} needs 'from'")
        deny = _strings(item.get("deny"), f"{where}.deny", allow_empty=False)
        for name in (item["from"], *deny):
            if name not in known:
                raise ConfigError(f"{where}: '{name}' is neither a declared layer nor component")
        rules.append(DenyRule(item["from"], deny))
    return ArchitectureConfig(layers, allow_upward, max_files, tuple(components), tuple(rules))
