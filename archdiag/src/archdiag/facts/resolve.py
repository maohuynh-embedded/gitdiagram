"""Resolve #include spellings to repository files, or to a named external library."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace

from archdiag.facts.model import INCLUDE, Confidence, Fact

STD_HEADERS = frozenset({
    # C
    "assert.h", "complex.h", "ctype.h", "errno.h", "fenv.h", "float.h", "inttypes.h",
    "iso646.h", "limits.h", "locale.h", "math.h", "setjmp.h", "signal.h", "stdalign.h",
    "stdarg.h", "stdatomic.h", "stdbool.h", "stddef.h", "stdint.h", "stdio.h", "stdlib.h",
    "stdnoreturn.h", "string.h", "tgmath.h", "threads.h", "time.h", "uchar.h", "wchar.h",
    "wctype.h",
    # C++
    "algorithm", "any", "array", "atomic", "bitset", "cassert", "cctype", "cerrno", "cfloat",
    "chrono", "cinttypes", "climits", "cmath", "codecvt", "complex", "concepts",
    "condition_variable", "cstdarg", "cstddef", "cstdint", "cstdio", "cstdlib", "cstring",
    "ctime", "deque", "exception", "execution", "filesystem", "format", "forward_list",
    "fstream", "functional", "future", "initializer_list", "iomanip", "ios", "iosfwd",
    "iostream", "istream", "iterator", "limits", "list", "locale", "map", "memory", "mutex",
    "new", "numeric", "optional", "ostream", "queue", "random", "ranges", "ratio", "regex",
    "scoped_allocator", "set", "shared_mutex", "span", "sstream", "stack", "stdexcept",
    "streambuf", "string", "string_view", "system_error", "thread", "tuple", "type_traits",
    "typeindex", "typeinfo", "unordered_map", "unordered_set", "utility", "valarray",
    "variant", "vector", "version", "bit", "compare", "coroutine", "numbers", "source_location",
})
_OS_PREFIXES = ("sys/", "linux/", "asm/", "net/", "netinet/", "arpa/", "IOKit/",
                "CoreFoundation/", "mach/", "winapi/")
_OS_HEADERS = frozenset({
    "windows.h", "winsock2.h", "unistd.h", "pthread.h", "fcntl.h", "termios.h", "dlfcn.h",
    "poll.h", "dirent.h", "setupapi.h", "devguid.h", "initguid.h",
})
_QT_CLASS = re.compile(r"^Q[A-Z][A-Za-z0-9]*$")
_QT_HEADER = re.compile(r"^q[a-z0-9]+\.h$")
_QT_MODULE = re.compile(r"^Qt[A-Z][A-Za-z0-9]*$")


def external_group(spelled: str) -> str:
    head = spelled.split("/")[0]
    if spelled in STD_HEADERS:
        return "C/C++ standard library"
    if _QT_CLASS.match(head) or _QT_HEADER.match(head) or _QT_MODULE.match(head):
        return "Qt"
    if spelled.startswith(_OS_PREFIXES) or spelled in _OS_HEADERS:
        return "Operating system"
    return head if "/" in spelled else posixpath.splitext(head)[0]


@dataclass(frozen=True)
class Resolution:
    target: str | None
    confidence: Confidence
    external: str | None = None
    ambiguous: bool = False


def _common_prefix(a: str, b: str) -> int:
    return len(posixpath.commonpath([posixpath.dirname(a) or ".", posixpath.dirname(b) or "."]))


class IncludeResolver:
    def __init__(self, files: Collection[str], include_dirs: Iterable[str]) -> None:
        self.files = set(files)
        self.include_dirs = [d for d in dict.fromkeys(include_dirs)]
        self.by_basename: dict[str, list[str]] = defaultdict(list)
        for path in sorted(self.files):
            self.by_basename[posixpath.basename(path)].append(path)

    def resolve(self, spelled: str, from_file: str, system: bool) -> Resolution:
        spelled = spelled.strip().replace("\\", "/")
        while spelled.startswith("./"):
            spelled = spelled[2:]
        candidates = [] if system else [posixpath.join(posixpath.dirname(from_file), spelled)]
        candidates += [posixpath.join(d, spelled) if d else spelled for d in self.include_dirs]
        candidates.append(spelled)
        for candidate in candidates:
            normalized = posixpath.normpath(candidate)
            if normalized in self.files:
                return Resolution(normalized, Confidence.MEDIUM)
        matches = [
            path for path in self.by_basename.get(posixpath.basename(spelled), [])
            if path == spelled or path.endswith(f"/{spelled}")
        ]
        if len(matches) == 1:
            return Resolution(matches[0], Confidence.LOW)
        if matches:
            best = max(matches, key=lambda path: (_common_prefix(path, from_file), path))
            return Resolution(best, Confidence.LOW, ambiguous=True)
        return Resolution(None, Confidence.MEDIUM, external=external_group(spelled))


def resolve_includes(facts: Iterable[Fact], resolver: IncludeResolver) -> list[Fact]:
    """Attach a target (or external group) to tree-sitter include facts."""
    out: list[Fact] = []
    for fact in facts:
        if fact.kind != INCLUDE or "target" in fact.data or "external" in fact.data:
            out.append(fact)
            continue
        result = resolver.resolve(fact.data["spelled"], fact.file, bool(fact.data.get("system")))
        confidence = min(fact.confidence, result.confidence)
        data = dict(fact.data)
        data["target"] = result.target
        if result.external:
            data["external"] = result.external
        if result.ambiguous:
            data["ambiguous"] = True
        out.append(replace(fact, confidence=Confidence(confidence), data=data))
    return out
