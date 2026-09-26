"""Settings, read in order: defaults < user config < <repo>/archdiag.toml < CLI flags."""

from __future__ import annotations

import ipaddress
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

API_KINDS = ("ollama", "openai")
USER_CONFIG = Path.home() / ".config" / "archdiag" / "config.toml"
REPO_CONFIG_NAME = "archdiag.toml"
# Tables owned by other loaders (architecture, protocols, standard views); Settings skips them.
SECTION_KEYS = frozenset({
    "architecture", "protocol", "c4", "archimate", "sysml", "autosar", "safety",
    "requirements", "stakeholders", "concerns", "decisions",
})


class ConfigError(Exception):
    """Invalid configuration or environment (CLI exit code 2)."""


@dataclass(frozen=True)
class Settings:
    api: str = "ollama"
    endpoint: str = "http://127.0.0.1:11434"
    model: str = "qwen3-coder:30b"
    num_ctx: int = 32768
    context_budget_tokens: int = 20000
    temperature: float = 0.2
    seed: int = 7
    timeout_s: float = 900.0
    max_attempts: int = 3
    language: str = "English"
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


def _coerce_value(key: str, default: object, value: object, source: str) -> object:
    if isinstance(default, tuple):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{source}: '{key}' must be a list of strings")
        return tuple(value)
    if isinstance(value, bool):
        raise ConfigError(f"{source}: '{key}' must not be a boolean")
    if isinstance(default, float):
        if not isinstance(value, int | float):
            raise ConfigError(f"{source}: '{key}' must be a number")
        return float(value)
    if isinstance(default, int):
        if not isinstance(value, int):
            raise ConfigError(f"{source}: '{key}' must be an integer")
        return value
    if not isinstance(value, str):
        raise ConfigError(f"{source}: '{key}' must be a string")
    return value


def apply_settings(base: Settings, data: dict[str, Any], source: str) -> Settings:
    known = {f.name for f in fields(Settings)}
    updates: dict[str, Any] = {}
    for key, value in data.items():
        if key in SECTION_KEYS:
            continue
        if key not in known:
            raise ConfigError(f"{source}: unknown setting '{key}'")
        updates[key] = _coerce_value(key, getattr(base, key), value, source)
    settings = replace(base, **updates)
    if settings.api not in API_KINDS:
        raise ConfigError(f"{source}: 'api' must be one of {', '.join(API_KINDS)}")
    if settings.max_attempts < 1 or settings.context_budget_tokens < 1000:
        raise ConfigError(f"{source}: max_attempts must be >= 1 and context_budget_tokens >= 1000")
    return settings


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_settings(
    repo: Path | None,
    overrides: dict[str, Any] | None = None,
    user_config: Path = USER_CONFIG,
) -> Settings:
    settings = Settings()
    if user_config.is_file():
        settings = apply_settings(settings, _read_toml(user_config), str(user_config))
    if repo is not None:
        repo_config = repo / REPO_CONFIG_NAME
        if repo_config.is_file():
            settings = apply_settings(settings, _read_toml(repo_config), str(repo_config))
    if overrides:
        given = {key: value for key, value in overrides.items() if value is not None}
        settings = apply_settings(settings, given, "command line")
    return settings


def load_sections(repo: Path | None) -> dict[str, Any]:
    """The raw tables of <repo>/archdiag.toml that Settings does not own."""
    if repo is None:
        return {}
    path = repo / REPO_CONFIG_NAME
    if not path.is_file():
        return {}
    return {k: v for k, v in _read_toml(path).items() if k in SECTION_KEYS}


def is_loopback(endpoint: str) -> bool:
    host = urlparse(endpoint).hostname
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
