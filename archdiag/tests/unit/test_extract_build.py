from typing import Any

from archdiag.facts.extract_build import extract_build
from archdiag.facts.model import BUILD_TARGET, INCLUDE_DIR

FILES = {
    "CMakeLists.txt", "src/main.cpp", "src/app/app.cpp", "src/app/app.h",
    "src/drivers/can.c", "src/drivers/can.h", "src/drivers/gpio.c", "lib/util/util.cpp",
    "lib/util/CMakeLists.txt", "qt/app.pro", "qt/common.pri", "qt/main.cpp", "qt/view.cpp",
    "qt/view.h", "qt/win_only.cpp", "qt/sub/sub.pro", "qt/sub/a.cpp",
}

CMAKE_ROOT = """
cmake_minimum_required(VERSION 3.20)
project(demo CXX C)
set(APP_SOURCES
    src/app/app.cpp  # the application
    src/app/app.h)
file(GLOB DRIVER_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/src/drivers/*.c)
include_directories(src)
add_executable(demo WIN32 MACOSX_BUNDLE src/main.cpp ${APP_SOURCES} $<$<CONFIG:Debug>:debug.cpp>)
target_sources(demo PRIVATE ${DRIVER_SOURCES} missing.cpp)
target_include_directories(demo PRIVATE
  "${PROJECT_SOURCE_DIR}/src/drivers"
  ${CMAKE_CURRENT_SOURCE_DIR}/src/app)
target_link_libraries(demo PRIVATE util Qt5::Core)
add_library(Qt5::Imported SHARED IMPORTED)
add_subdirectory(lib/util)
"""

CMAKE_LIB = """
add_library(util STATIC util.cpp)
target_include_directories(util PUBLIC ${CMAKE_CURRENT_LIST_DIR})
"""

PRO = """
TEMPLATE = app
QT += core gui
include(common.pri)
SOURCES += main.cpp \\
    view.cpp
HEADERS = view.h
win32 {
    SOURCES += win_only.cpp
}
unix:!macx: LIBS += -lusb -L/opt/lib
INCLUDEPATH += $$PWD/../src $${EXTRA_DIR}
"""

PRI = """
EXTRA_DIR = $$PWD/../lib
DEFINES += USE_X
"""

SUB_PRO = """
TEMPLATE = lib
TARGET = subcore
SOURCES = a.cpp
"""

TEXTS = {
    "CMakeLists.txt": CMAKE_ROOT,
    "lib/util/CMakeLists.txt": CMAKE_LIB,
    "qt/app.pro": PRO,
    "qt/common.pri": PRI,
    "qt/sub/sub.pro": SUB_PRO,
}


def run() -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    build_files = ["CMakeLists.txt", "lib/util/CMakeLists.txt", "qt/app.pro", "qt/common.pri",
                   "qt/sub/sub.pro"]
    facts, warnings = extract_build(FILES, build_files, TEXTS.__getitem__)
    targets = {f.data["name"]: f.data | {"file": f.file, "line": f.line}
               for f in facts if f.kind == BUILD_TARGET}
    dirs = sorted(f.data["dir"] for f in facts if f.kind == INCLUDE_DIR)
    return targets, dirs, warnings


def test_cmake_targets_sources_include_dirs_and_links() -> None:
    targets, _, _ = run()
    demo = targets["demo"]
    assert demo["kind"] == "executable"
    assert demo["system"] == "cmake"
    assert demo["file"] == "CMakeLists.txt"
    assert demo["line"] == 9
    assert demo["sources"] == [
        "src/app/app.cpp", "src/app/app.h", "src/drivers/can.c", "src/drivers/gpio.c",
        "src/main.cpp",
    ]
    assert demo["include_dirs"] == ["src", "src/app", "src/drivers"]
    assert demo["links"] == ["Qt5::Core", "util"]
    util = targets["util"]
    assert util["kind"] == "library"
    assert util["sources"] == ["lib/util/util.cpp"]
    assert util["include_dirs"] == ["lib/util"]
    assert "Qt5::Imported" not in targets


def test_generator_expressions_are_reported() -> None:
    _, _, warnings = run()
    assert any("generator expression" in w for w in warnings)


def test_qmake_targets_with_pri_scopes_and_variables() -> None:
    targets, _, _ = run()
    app = targets["app"]
    assert app["system"] == "qmake"
    assert app["kind"] == "executable"
    assert app["sources"] == ["qt/main.cpp", "qt/view.cpp", "qt/view.h", "qt/win_only.cpp"]
    assert app["include_dirs"] == ["lib", "src"]
    assert app["links"] == ["usb"]
    sub = targets["subcore"]
    assert sub["kind"] == "library"
    assert sub["sources"] == ["qt/sub/a.cpp"]
    assert "common" not in targets


def test_global_include_directories() -> None:
    _, dirs, _ = run()
    assert dirs == ["src"]


def test_unreadable_build_file_is_a_warning() -> None:
    def read(path: str) -> str:
        raise OSError("boom")

    facts, warnings = extract_build(FILES, ["CMakeLists.txt"], read)
    assert facts == []
    assert any("CMakeLists.txt" in w for w in warnings)
