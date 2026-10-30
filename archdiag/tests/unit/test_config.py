from pathlib import Path

import pytest

from archdiag.config import ConfigError, is_loopback, load_settings


def test_defaults_point_at_local_ollama(tmp_path: Path) -> None:
    settings = load_settings(tmp_path, user_config=tmp_path / "missing.toml")
    assert settings.api == "ollama"
    assert settings.num_ctx == 32768
    assert is_loopback(settings.endpoint)


def test_precedence_user_then_repo_then_cli(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    user.write_text('model = "user-model"\nnum_ctx = 8192\n')
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "archdiag.toml").write_text('model = "repo-model"\nexclude = ["docs/"]\n')

    settings = load_settings(repo, {"model": "cli-model", "endpoint": None}, user_config=user)

    assert settings.model == "cli-model"
    assert settings.num_ctx == 8192
    assert settings.exclude == ("docs/",)
    assert settings.endpoint == "http://127.0.0.1:11434"


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "archdiag.toml").write_text('modle = "typo"\n')
    with pytest.raises(ConfigError, match="unknown setting 'modle'"):
        load_settings(tmp_path, user_config=tmp_path / "none.toml")


def test_wrong_type_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "archdiag.toml").write_text('num_ctx = "big"\n')
    with pytest.raises(ConfigError, match="num_ctx"):
        load_settings(tmp_path, user_config=tmp_path / "none.toml")


def test_invalid_toml_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "archdiag.toml").write_text("model = \n")
    with pytest.raises(ConfigError):
        load_settings(tmp_path, user_config=tmp_path / "none.toml")


def test_unknown_api_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="api"):
        load_settings(tmp_path, {"api": "cloud"}, user_config=tmp_path / "none.toml")


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://127.0.0.1:11434", True),
        ("http://localhost:1234/v1", True),
        ("http://[::1]:11434", True),
        ("http://192.168.1.5:11434", False),
        ("https://api.example.com/v1", False),
        ("not a url", False),
    ],
)
def test_is_loopback(endpoint: str, expected: bool) -> None:
    assert is_loopback(endpoint) is expected


def test_sections_are_left_to_their_own_loaders(tmp_path: Path) -> None:
    from archdiag.config import load_sections

    (tmp_path / "archdiag.toml").write_text(
        'model = "m"\n[architecture]\nlayers = ["ui", "core"]\n'
        '[[architecture.components]]\nname = "x"\npaths = ["x/**"]\n'
    )
    settings = load_settings(tmp_path, user_config=tmp_path / "none.toml")
    assert settings.model == "m"
    sections = load_sections(tmp_path)
    assert sections["architecture"]["layers"] == ["ui", "core"]
    assert load_sections(tmp_path / "missing") == {}
