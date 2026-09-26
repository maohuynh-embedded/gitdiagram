import pytest

from archdiag.facts.model import INCLUDE, TREESITTER, Confidence, Fact
from archdiag.facts.resolve import IncludeResolver, external_group, resolve_includes

FILES = {
    "src/app/main.c", "src/app/app.h", "src/drivers/can/can.h", "src/hal/gpio.h",
    "lib/a/config.h", "lib/b/config.h", "include/mylib/api.h",
}
RESOLVER = IncludeResolver(FILES, ["src/hal", "include"])


def test_same_directory_first() -> None:
    r = RESOLVER.resolve("app.h", "src/app/main.c", system=False)
    assert (r.target, r.confidence, r.ambiguous) == ("src/app/app.h", Confidence.MEDIUM, False)


def test_include_directories() -> None:
    assert RESOLVER.resolve("gpio.h", "src/app/main.c", system=False).target == "src/hal/gpio.h"
    system = RESOLVER.resolve("mylib/api.h", "src/app/main.c", system=True)
    assert system.target == "include/mylib/api.h"
    assert system.confidence == Confidence.MEDIUM


def test_unique_suffix_is_low_confidence() -> None:
    r = RESOLVER.resolve("can/can.h", "src/app/main.c", system=False)
    assert (r.target, r.confidence) == ("src/drivers/can/can.h", Confidence.LOW)


def test_ambiguous_suffix_picks_the_closest() -> None:
    r = RESOLVER.resolve("config.h", "lib/b/sub/impl.c", system=False)
    assert (r.target, r.confidence, r.ambiguous) == ("lib/b/config.h", Confidence.LOW, True)


def test_unresolved_becomes_external() -> None:
    r = RESOLVER.resolve("QObject", "src/app/main.c", system=True)
    assert (r.target, r.external) == (None, "Qt")


@pytest.mark.parametrize(
    ("spelled", "group"),
    [
        ("QObject", "Qt"), ("QtCore/QTimer", "Qt"), ("qqmlengine.h", "Qt"),
        ("vector", "C/C++ standard library"), ("stdint.h", "C/C++ standard library"),
        ("cstdio", "C/C++ standard library"), ("sys/ioctl.h", "Operating system"),
        ("windows.h", "Operating system"), ("IOKit/IOKitLib.h", "Operating system"),
        ("boost/asio.hpp", "boost"), ("libusb.h", "libusb"),
    ],
)
def test_external_groups(spelled: str, group: str) -> None:
    assert external_group(spelled) == group


def test_resolve_includes_updates_facts_and_confidence() -> None:
    facts = [
        Fact(INCLUDE, "src/app/main.c", 1, TREESITTER, Confidence.MEDIUM,
             {"spelled": "app.h", "system": False, "conditional": False}),
        Fact(INCLUDE, "src/app/main.c", 2, TREESITTER, Confidence.LOW,
             {"spelled": "gpio.h", "system": False, "conditional": True}),
        Fact(INCLUDE, "src/app/main.c", 3, TREESITTER, Confidence.MEDIUM,
             {"spelled": "stdio.h", "system": True, "conditional": False}),
    ]
    resolved = resolve_includes(facts, RESOLVER)
    assert [f.data.get("target") for f in resolved] == ["src/app/app.h", "src/hal/gpio.h", None]
    assert resolved[1].confidence == Confidence.LOW
    assert resolved[2].data["external"] == "C/C++ standard library"
