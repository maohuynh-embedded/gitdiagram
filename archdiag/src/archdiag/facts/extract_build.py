"""Read CMake and qmake files statically: targets, their sources, include dirs and links.

Nothing is executed. Anything that needs evaluation (generator expressions, functions,
conditions) is skipped and reported, so the result is a safe under-approximation.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from archdiag.facts.model import BUILD, BUILD_TARGET, INCLUDE_DIR, Confidence, Fact

_ROOT = "@ROOT@"
_CMAKE_KEYWORDS = frozenset(
    {
        "WIN32",
        "MACOSX_BUNDLE",
        "EXCLUDE_FROM_ALL",
        "STATIC",
        "SHARED",
        "MODULE",
        "OBJECT",
        "INTERFACE",
        "PRIVATE",
        "PUBLIC",
        "SYSTEM",
        "BEFORE",
        "AFTER",
        "MANUAL_FINALIZATION",
        "CONFIGURE_DEPENDS",
        "LINK_PRIVATE",
        "LINK_PUBLIC",
        "LINK_INTERFACE_LIBRARIES",
    }
)
_EXECUTABLE_COMMANDS = frozenset(
    {
        "add_executable",
        "qt_add_executable",
        "qt5_add_executable",
        "qt6_add_executable",
    }
)
_LIBRARY_COMMANDS = frozenset({"add_library", "qt_add_library", "qt6_add_library"})
_VAR_REF = re.compile(r"\$\{([A-Za-z0-9_.+-]+)\}")
_QMAKE_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*)\s*(\+=|-=|\*=|=)\s*(.*)$")
_QMAKE_VAR = re.compile(r"\$\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
_QMAKE_SCOPE_PREFIX = re.compile(
    r"^[!A-Za-z0-9_.|*-]+(?:\([^)]*\))?(?::[!A-Za-z0-9_.|*-]+)*\s*:\s*"
)


@dataclass
class _Target:
    name: str
    kind: str
    system: str
    file: str
    line: int
    sources: set[str] = field(default_factory=set)
    include_dirs: set[str] = field(default_factory=set)
    links: set[str] = field(default_factory=set)


def _normalize(token: str, base: str) -> str | None:
    if token.startswith(f"{_ROOT}/"):
        path = token[len(_ROOT) + 1 :]
    elif token == _ROOT:
        path = ""
    elif token.startswith("/"):
        return None
    else:
        path = posixpath.join(base, token) if base else token
    path = posixpath.normpath(path) if path else "."
    if path.startswith(".."):
        return None
    return "" if path == "." else path


# --- CMake ---------------------------------------------------------------------------------


def _strip_cmake_comments(text: str) -> str:
    text = re.sub(r"#\[(=*)\[.*?\]\1\]", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    out: list[str] = []
    for line in text.split("\n"):
        in_quote = False
        cut = len(line)
        for i, ch in enumerate(line):
            if ch == '"' and (i == 0 or line[i - 1] != "\\"):
                in_quote = not in_quote
            elif ch == "#" and not in_quote:
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


def _cmake_commands(text: str) -> Iterable[tuple[str, list[str], int]]:
    text = _strip_cmake_comments(text)
    pattern = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    pos = 0
    while True:
        match = pattern.search(text, pos)
        if match is None:
            return
        depth, i = 1, match.end()
        while i < len(text) and depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        body = text[match.end() : i - 1]
        line = text.count("\n", 0, match.start()) + 1
        args = [
            a[1:-1] if a.startswith('"') and a.endswith('"') and len(a) > 1 else a
            for a in re.findall(r'"(?:[^"\\]|\\.)*"|[^\s"]+', body)
        ]
        yield match.group(1).lower(), args, line
        pos = i


class _CMakeReader:
    def __init__(
        self,
        files: Collection[str],
        targets: dict[str, _Target],
        global_dirs: set[str],
        warnings: list[str],
    ) -> None:
        self.files = files
        self.targets = targets
        self.global_dirs = global_dirs
        self.warnings = warnings

    def read(self, path: str, text: str) -> None:
        base = posixpath.dirname(path)
        variables: dict[str, list[str]] = {
            "CMAKE_SOURCE_DIR": [_ROOT],
            "PROJECT_SOURCE_DIR": [_ROOT],
            "CMAKE_CURRENT_SOURCE_DIR": [f"{_ROOT}/{base}" if base else _ROOT],
            "CMAKE_CURRENT_LIST_DIR": [f"{_ROOT}/{base}" if base else _ROOT],
        }
        skipped = 0
        self.directory_dirs: set[str] = set()
        for command, raw_args, line in _cmake_commands(text):
            args: list[str] = []
            for arg in raw_args:
                expanded = self.expand(arg, variables)
                if any("$<" in a or "${" in a for a in expanded):
                    skipped += 1
                    continue
                args.extend(expanded)
            self.apply(command, args, line, path, base, variables)
        if skipped:
            self.warnings.append(
                f"{path}: skipped {skipped} argument(s) with generator expressions or "
                "unknown variables"
            )

    def expand(self, arg: str, variables: dict[str, list[str]]) -> list[str]:
        if "$<" in arg:
            return [arg]
        whole = _VAR_REF.fullmatch(arg)
        if whole:
            return list(variables.get(whole.group(1), [arg]))
        for _ in range(5):
            replaced = _VAR_REF.sub(
                lambda m: " ".join(variables.get(m.group(1), [m.group(0)])), arg
            )
            if replaced == arg:
                break
            arg = replaced
        return arg.split(";") if ";" in arg else [arg]

    def paths(self, values: Iterable[str], base: str) -> list[str]:
        out = []
        for value in values:
            if value in _CMAKE_KEYWORDS:
                continue
            normalized = _normalize(value, base)
            if normalized is not None:
                out.append(normalized)
        return out

    def apply(
        self,
        command: str,
        args: list[str],
        line: int,
        path: str,
        base: str,
        variables: dict[str, list[str]],
    ) -> None:
        if not args:
            return
        if command == "set":
            values = args[1:]
            for stop in ("CACHE", "PARENT_SCOPE"):
                if stop in values:
                    values = values[: values.index(stop)]
            variables[args[0]] = values
        elif command == "list" and len(args) >= 2 and args[0] == "APPEND":
            variables.setdefault(args[1], []).extend(args[2:])
        elif command == "file" and len(args) >= 3 and args[0] in ("GLOB", "GLOB_RECURSE"):
            patterns = [p for p in self.paths(args[2:], base) if p]
            recursive = args[0] == "GLOB_RECURSE"
            variables[args[1]] = [
                f"{_ROOT}/{f}"
                for f in sorted(self.files)
                if any(self.glob_match(f, p, recursive) for p in patterns)
            ]
        elif command in _EXECUTABLE_COMMANDS or command in _LIBRARY_COMMANDS:
            if "IMPORTED" in args or "ALIAS" in args:
                return
            kind = "executable" if command in _EXECUTABLE_COMMANDS else "library"
            target = self.targets.setdefault(args[0], _Target(args[0], kind, "cmake", path, line))
            # include_directories() seeds every target created after it in this directory.
            target.include_dirs.update(self.directory_dirs)
            target.sources.update(p for p in self.paths(args[1:], base) if p in self.files)
        elif command == "target_sources" and args[0] in self.targets:
            self.targets[args[0]].sources.update(
                p for p in self.paths(args[1:], base) if p in self.files
            )
        elif command == "target_include_directories" and args[0] in self.targets:
            self.targets[args[0]].include_dirs.update(self.paths(args[1:], base))
        elif command == "include_directories":
            dirs = self.paths(args, base)
            self.directory_dirs.update(dirs)
            self.global_dirs.update(dirs)
        elif command == "target_link_libraries" and args[0] in self.targets:
            self.targets[args[0]].links.update(a for a in args[1:] if a not in _CMAKE_KEYWORDS)

    @staticmethod
    def glob_match(path: str, pattern: str, recursive: bool) -> bool:
        if recursive:
            directory, name = posixpath.split(pattern)
            prefix = f"{directory}/" if directory else ""
            return path.startswith(prefix) and fnmatchcase(posixpath.basename(path), name)
        return fnmatchcase(path, pattern) and path.count("/") == pattern.count("/")


# --- qmake ---------------------------------------------------------------------------------


class _QmakeReader:
    def __init__(
        self,
        files: Collection[str],
        read: Callable[[str], str],
        targets: dict[str, _Target],
        warnings: list[str],
    ) -> None:
        self.files = files
        self.read_text = read
        self.targets = targets
        self.warnings = warnings

    def read(self, path: str) -> None:
        variables: dict[str, list[str]] = {}
        self.parse(path, variables, depth=0)
        template = (variables.get("TEMPLATE") or ["app"])[0]
        if template == "subdirs":
            return
        base = posixpath.dirname(path)
        name = (variables.get("TARGET") or [posixpath.splitext(posixpath.basename(path))[0]])[0]
        target = _Target(name, "library" if template == "lib" else "executable", "qmake", path, 1)
        for key in ("SOURCES", "HEADERS"):
            target.sources.update(
                p
                for p in (_normalize(v, base) for v in variables.get(key, []))
                if p is not None and p in self.files
            )
        target.include_dirs.update(
            p
            for p in (_normalize(v, base) for v in variables.get("INCLUDEPATH", []))
            if p is not None
        )
        target.links.update(v[2:] for v in variables.get("LIBS", []) if v.startswith("-l"))
        self.targets[name] = target

    def parse(self, path: str, variables: dict[str, list[str]], depth: int) -> None:
        if depth > 8:
            return
        try:
            text = self.read_text(path)
        except OSError as exc:
            self.warnings.append(f"{path}: cannot read ({exc})")
            return
        base = posixpath.dirname(path)
        root_relative = f"{_ROOT}/{base}" if base else _ROOT
        joined = re.sub(r"\\\s*\n", " ", text)
        for raw in joined.split("\n"):
            line = raw.split("#", 1)[0].strip()
            if not line or line == "}":
                continue
            line = line.rstrip("{").strip().lstrip("}").strip()
            line = _QMAKE_SCOPE_PREFIX.sub("", line) if "=" in line or "(" in line else line
            include = re.match(r"^include\s*\(\s*([^)]+?)\s*\)$", line)
            if include:
                target = self.substitute(include.group(1), variables, root_relative)
                normalized = _normalize(target, base) if target else None
                if normalized and normalized in self.files:
                    self.parse(normalized, variables, depth + 1)
                continue
            assign = _QMAKE_ASSIGN.match(line)
            if not assign:
                continue
            name, op, value = assign.groups()
            values = [
                v
                for v in (self.substitute(tok, variables, root_relative) for tok in value.split())
                if v
            ]
            if op == "=":
                variables[name] = values
            elif op == "+=":
                variables.setdefault(name, []).extend(values)
            elif op == "*=":
                current = variables.setdefault(name, [])
                current.extend(v for v in values if v not in current)
            elif op == "-=":
                variables[name] = [v for v in variables.get(name, []) if v not in values]

    @staticmethod
    def substitute(token: str, variables: dict[str, list[str]], root_relative: str) -> str:
        token = token.replace("$$PWD", root_relative).replace("$${PWD}", root_relative)
        token = token.replace("$$_PRO_FILE_PWD_", root_relative)
        token = _QMAKE_VAR.sub(lambda m: " ".join(variables.get(m.group(1), [])), token).strip()
        return "" if "$$" in token or " " in token else token


# --- entry point ---------------------------------------------------------------------------


def extract_build(
    files: Collection[str], build_files: Sequence[str], read: Callable[[str], str]
) -> tuple[list[Fact], list[str]]:
    targets: dict[str, _Target] = {}
    global_dirs: set[str] = set()
    warnings: list[str] = []
    cmake = _CMakeReader(files, targets, global_dirs, warnings)
    qmake = _QmakeReader(files, read, targets, warnings)
    for path in sorted(build_files):
        name = posixpath.basename(path)
        try:
            if name == "CMakeLists.txt" or name.endswith(".cmake"):
                cmake.read(path, read(path))
            elif name.endswith(".pro"):
                qmake.read(path)
        except OSError as exc:
            warnings.append(f"{path}: cannot read ({exc})")
        except (ValueError, RecursionError) as exc:
            warnings.append(f"{path}: could not be parsed ({exc})")
    facts = [
        Fact(
            BUILD_TARGET,
            t.file,
            t.line,
            BUILD,
            Confidence.HIGH,
            {
                "name": t.name,
                "kind": t.kind,
                "system": t.system,
                "sources": sorted(t.sources),
                "include_dirs": sorted(t.include_dirs),
                "links": sorted(t.links),
            },
        )
        for t in sorted(targets.values(), key=lambda t: t.name)
    ]
    facts.extend(
        Fact(INCLUDE_DIR, "", 0, BUILD, Confidence.HIGH, {"dir": d}) for d in sorted(global_dirs)
    )
    return facts, warnings
