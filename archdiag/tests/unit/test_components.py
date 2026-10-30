import pytest

from archdiag.arch.components import (
    build_components,
    infer_components,
    is_test_path,
    snake_id,
    target_dirs,
    unique_ids,
)
from archdiag.arch.config import ArchitectureConfig, load_architecture
from archdiag.config import ConfigError


def names(components: list) -> dict[str, int]:  # type: ignore[type-arg]
    return {c.name: len(c.files) for c in components}


def test_small_top_level_directories_become_components() -> None:
    files = [
        "main.cpp",
        "app/a.cpp",
        "app/a.h",
        "hal/gpio.c",
        "hal/gpio.h",
        "hal/x/y.c",
        "hal/x/z.c",
    ]
    comps = infer_components(files, set(), max_files=40)
    assert names(comps) == {"root": 1, "app": 2, "hal": 4}
    assert comps[0].kind == "root"


def test_large_directories_split_and_tiny_ones_merge_into_parent() -> None:
    files = [f"infra/can/f{i}.cpp" for i in range(5)]
    files += [f"infra/serial/f{i}.cpp" for i in range(4)]
    files += ["infra/one/lonely.cpp", "infra/readme_helpers.h"]
    comps = infer_components(files, set(), max_files=6)
    assert names(comps) == {"infra": 2, "infra/can": 5, "infra/serial": 4}


def test_build_target_directories_are_not_split() -> None:
    files = [f"lib/core/sub{i % 3}/f{i}.c" for i in range(12)]
    comps = infer_components(files, {"lib/core"}, max_files=4)
    assert names(comps) == {"lib/core": 12}
    assert target_dirs(
        [{"kind": "library", "sources": files}, {"kind": "executable", "sources": ["main.c"]}], 13
    ) == {"lib/core"}
    # the main executable spanning most of the tree is not a boundary
    assert target_dirs([{"kind": "executable", "sources": files}], 13) == set()
    assert target_dirs([{"kind": "executable", "sources": files[:3]}], 13) == {"lib/core"}


def test_test_directories_are_marked() -> None:
    comps = infer_components(
        ["tests/UT/a.cpp", "tests/UT/b.cpp", "src/a.c", "src/b.c"], set(), max_files=40
    )
    assert {c.name: c.kind for c in comps} == {"src": "module", "tests": "test"}
    assert is_test_path("tests/UT/tst_x/a.cpp")
    assert is_test_path("module_tests/a.c")
    assert not is_test_path("src/testing_util.c")


def test_layers_without_declaration_follow_top_directory() -> None:
    comps = build_components(
        ["main.c", "infra/can/a.c", "infra/can/b.c"], set(), ArchitectureConfig()
    )
    assert {c.name: c.layer for c in comps} == {"root": "root", "infra": "infra"}


def test_declared_layers_map_top_directories() -> None:
    config = load_architecture({"layers": ["ui", "core"]})
    comps = build_components(
        ["ui/a.c", "ui/b.c", "core/a.c", "core/b.c", "misc/a.c", "misc/b.c"], set(), config
    )
    assert {c.name: c.layer for c in comps} == {"core": "core", "misc": "unlayered", "ui": "ui"}


def test_declared_components_first_match_wins_and_unassigned() -> None:
    config = load_architecture(
        {
            "layers": ["app", "drivers"],
            "components": [
                {"name": "can driver", "layer": "drivers", "paths": ["drivers/can/**"]},
                {"name": "drivers", "layer": "drivers", "paths": ["drivers/"]},
                {"name": "app", "layer": "app", "paths": ["src/app/**", "main.c"]},
            ],
        }
    )
    comps = build_components(
        ["main.c", "src/app/a.c", "drivers/can/c.c", "drivers/spi/s.c", "tools/x.py"],
        set(),
        config,
    )
    by_name = {c.name: c for c in comps}
    assert by_name["can driver"].files == ("drivers/can/c.c",)
    assert by_name["can driver"].id == "can_driver"
    assert by_name["drivers"].files == ("drivers/spi/s.c",)
    assert by_name["app"].files == ("main.c", "src/app/a.c")
    assert by_name["unassigned"].files == ("tools/x.py",)
    assert by_name["unassigned"].kind == "unassigned"
    assert by_name["app"].origin == "declared"


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"layers": ["a", "a"]}, "duplicates"),
        (
            {"layers": ["a"], "components": [{"name": "x", "layer": "b", "paths": ["x"]}]},
            "not one of",
        ),
        ({"components": [{"name": "x", "paths": []}]}, "must not be empty"),
        (
            {"components": [{"name": "x", "paths": ["a"]}, {"name": "x", "paths": ["b"]}]},
            "duplicate names",
        ),
        ({"layers": ["a"], "rules": [{"from": "a", "deny": ["zzz"]}]}, "neither"),
        ({"max_component_files": 1}, "max_component_files"),
    ],
)
def test_invalid_architecture_declarations(data: dict, fragment: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(ConfigError, match=fragment):
        load_architecture(data)


def test_ids() -> None:
    assert snake_id("infrastructure/can") == "infrastructure_can"
    assert snake_id("3rdparty/x") == "c_3rdparty_x"
    assert len(snake_id("a" * 80)) == 48
    assert unique_ids(["a-b", "a b", "a_b"]) == ["a_b", "a_b_2", "a_b_3"]


def test_container_directories_split_into_their_packages() -> None:
    files = [
        "infra/can/a.c",
        "infra/can/b.c",
        "infra/serial/a.c",
        "infra/serial/b.c",
        "infra/config/a.c",
        "infra/common.h",
    ]
    comps = infer_components(files, set(), max_files=40)
    # infra/config has a single file, so it folds back into infra.
    assert names(comps) == {"infra": 2, "infra/can": 2, "infra/serial": 2}
