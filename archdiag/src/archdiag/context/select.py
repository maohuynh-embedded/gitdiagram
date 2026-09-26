"""Pick what the LLM sees: a filtered tree plus excerpts, within a token budget.

Sub-project B replaces this stage with static analysis; A keeps it as the LLM-only path.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass

from archdiag.scan.snapshot import RepoSnapshot
from archdiag.scan.walker import HEADER_LANGUAGES, SOURCE_LANGUAGES, FileEntry

CHARS_PER_TOKEN = 4
README_CHARS = 6000
BUILD_CHARS = 4000
EXCERPT_CHARS = 3000
MAX_BUILD_FILES = 6
MAX_FILES_LISTED_PER_DIR = 15
_EXCERPT_OVERHEAD_TOKENS = 8


@dataclass(frozen=True)
class Excerpt:
    path: str
    text: str


@dataclass(frozen=True)
class Evidence:
    tree_summary: str
    excerpts: tuple[Excerpt, ...]
    budget_used: int
    budget_limit: int
    truncated: bool
    candidates_total: int


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else "."


def summarize_tree(snapshot: RepoSnapshot, counts_only: bool = False) -> str:
    by_dir: dict[str, list[FileEntry]] = defaultdict(list)
    for entry in snapshot.files:
        by_dir[_parent(entry.path)].append(entry)
    lines: list[str] = []
    for directory in sorted(by_dir):
        entries = by_dir[directory]
        languages = Counter(entry.language or "other" for entry in entries)
        summary = ", ".join(
            f"{count} {language}"
            for language, count in sorted(languages.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        noun = "file" if len(entries) == 1 else "files"
        lines.append(f"{directory}/ ({len(entries)} {noun}: {summary})")
        if counts_only:
            continue
        names = [entry.path.rsplit("/", 1)[-1] for entry in entries]
        lines.extend(f"  {name}" for name in names[:MAX_FILES_LISTED_PER_DIR])
        if len(names) > MAX_FILES_LISTED_PER_DIR:
            lines.append(f"  … {len(names) - MAX_FILES_LISTED_PER_DIR} more")
    return "\n".join(lines)


def _candidates(snapshot: RepoSnapshot) -> list[tuple[str, int]]:
    ordered: list[tuple[str, int]] = []
    seen: set[str] = set()

    def add(path: str, limit: int) -> None:
        if path not in seen:
            seen.add(path)
            ordered.append((path, limit))

    if snapshot.readme:
        add(snapshot.readme, README_CHARS)
    build_files = sorted(snapshot.build_files, key=lambda p: (p.count("/"), p))
    for path in build_files[:MAX_BUILD_FILES]:
        add(path, BUILD_CHARS)

    sources = [entry for entry in snapshot.files if entry.language in SOURCE_LANGUAGES]
    for entry in sources:
        if entry.path.rsplit("/", 1)[-1].split(".")[0].lower() == "main":
            add(entry.path, EXCERPT_CHARS)

    queues: dict[str, deque[FileEntry]] = {}
    for entry in sorted(
        sources, key=lambda e: (e.language not in HEADER_LANGUAGES, -e.size, e.path)
    ):
        queues.setdefault(_parent(entry.path), deque()).append(entry)
    order = sorted(queues, key=lambda d: (d.count("/") if d != "." else -1, d))
    while any(queues[d] for d in order):
        for directory in order:
            if queues[directory]:
                add(queues[directory].popleft().path, EXCERPT_CHARS)
    return ordered


def select_evidence(snapshot: RepoSnapshot, budget_tokens: int) -> Evidence:
    truncated = False
    tree = summarize_tree(snapshot)
    if estimate_tokens(tree) > budget_tokens // 3:
        tree = summarize_tree(snapshot, counts_only=True)
        truncated = True
    max_tree_chars = (budget_tokens // 2) * CHARS_PER_TOKEN
    if len(tree) > max_tree_chars:
        tree = tree[:max_tree_chars] + "\n… (tree cut)"
        truncated = True

    used = estimate_tokens(tree)
    excerpts: list[Excerpt] = []
    candidates = _candidates(snapshot)
    for path, limit in candidates:
        text = snapshot.read_text(path, limit)
        if len(text) >= limit:
            text += "\n… (excerpt cut)"
        cost = estimate_tokens(text) + estimate_tokens(path) + _EXCERPT_OVERHEAD_TOKENS
        if used + cost > budget_tokens:
            truncated = True
            continue
        excerpts.append(Excerpt(path, text))
        used += cost
    return Evidence(tree, tuple(excerpts), used, budget_tokens, truncated, len(candidates))
