"""Collect facts for a snapshot: cached per file, parallel when there is enough work."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path

from archdiag.facts import extract_clang, extract_ts
from archdiag.facts.extract_build import extract_build
from archdiag.facts.extract_protocol import extract_dbc, extract_eds
from archdiag.facts.model import BUILD_TARGET, INCLUDE_DIR, STRUCTURAL_KINDS, Fact
from archdiag.facts.protocol_config import ProtocolConfig
from archdiag.facts.resolve import IncludeResolver, resolve_includes
from archdiag.facts.store import FactStore
from archdiag.scan.snapshot import RepoSnapshot

Progress = Callable[[str, str], None]

# Process start-up costs more than parsing a few hundred files serially (~2 ms each).
_PARALLEL_THRESHOLD = 600
_CLANG_PREFIX = "clang::"


@dataclass
class FactsResult:
    facts: list[Fact]
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    compile_db: str | None = None


def _ts_job(job: tuple[str, str, str, ProtocolConfig]) -> list[Fact]:
    root, path, language, protocols = job
    source = (Path(root) / path).read_bytes()
    return extract_ts.extract_file(path, source, language, protocols)


def _clang_job(job: tuple[str, extract_clang.CompileEntry, frozenset[str]]
               ) -> tuple[list[Fact], list[str], str | None]:
    root, entry, files = job
    return extract_clang.extract_tu(root, entry, files)


def _noop(step: str, message: str) -> None:
    return None


def _workers(requested: int | None) -> int:
    return max(1, requested if requested else (os.cpu_count() or 2) - 1)


def collect_facts(
    snapshot: RepoSnapshot,
    protocols: ProtocolConfig,
    store: FactStore,
    *,
    compile_db: Path | None = None,
    use_clang: bool = True,
    rebuild: bool = False,
    workers: int | None = None,
    progress: Progress | None = None,
) -> FactsResult:
    report = progress or _noop
    result = FactsResult([], list(store.warnings))
    root = str(snapshot.root)
    files = {entry.path for entry in snapshot.files}
    if rebuild:
        store.clear()

    def read(path: str) -> str:
        return (snapshot.root / path).read_text(encoding="utf-8", errors="replace")

    build_facts, build_warnings = extract_build(files, snapshot.build_files, read)
    result.warnings.extend(build_warnings)

    # tree-sitter, cached per file content + extractor version + protocol patterns
    fingerprint = protocols.fingerprint()
    per_file: dict[str, list[Fact]] = {}
    jobs: list[tuple[str, str, str, ProtocolConfig]] = []
    keys: dict[str, str] = {}
    for entry in snapshot.files:
        if entry.language not in extract_ts.EXTRACTABLE:
            continue
        key = f"{extract_ts.EXTRACTOR_VERSION}:{fingerprint}:{snapshot.hashes.get(entry.path)}"
        keys[entry.path] = key
        cached = store.cached(entry.path, key)
        if cached is None:
            jobs.append((root, entry.path, entry.language or "cpp", protocols))
        else:
            per_file[entry.path] = cached
    report("facts", f"{len(keys)} C/C++ files, {len(jobs)} to parse, {len(per_file)} cached")
    outputs: list[list[Fact]] | None = None
    if len(jobs) >= _PARALLEL_THRESHOLD and _workers(workers) > 1:
        try:
            with ProcessPoolExecutor(max_workers=_workers(workers)) as pool:
                outputs = list(pool.map(_ts_job, jobs, chunksize=16))
        except (BrokenProcessPool, OSError) as exc:
            result.warnings.append(f"Parallel parsing failed ({exc}); parsed serially instead.")
    if outputs is None:
        outputs = [_ts_job(job) for job in jobs]
    for job, facts in zip(jobs, outputs, strict=True):
        path = job[1]
        per_file[path] = facts
        store.replace(path, keys[path], facts)
    result.stats.update(c_files=len(keys), parsed=len(jobs), cached=len(keys) - len(jobs))

    # protocol description files
    protocol_facts: list[Fact] = []
    for entry in snapshot.files:
        if entry.language == "dbc":
            protocol_facts.extend(extract_dbc(entry.path, read(entry.path)))
        elif entry.language == "eds":
            protocol_facts.extend(extract_eds(entry.path, read(entry.path)))

    # libclang, replacing structural tree-sitter facts for every file it covers
    keep = set(keys)
    db = extract_clang.find_compile_db(snapshot.root, compile_db) if use_clang else None
    if compile_db is not None and db is None:
        result.warnings.append(f"Compile database {compile_db} was not found.")
    clang_facts: dict[tuple[str, str, int, str], Fact] = {}
    covered: set[str] = set()
    if db is not None:
        result.compile_db = str(db)
        entries = extract_clang.load_compile_db(db, snapshot.root, files)
        setup = extract_clang.clang_setup() if entries else None
        if entries and setup is None:
            result.warnings.append(
                "compile_commands.json found but libclang is not installed; using tree-sitter "
                "only. Install it with: pip install 'archdiag[clang]'"
            )
        elif entries:
            headers = hashlib.sha256("".join(
                snapshot.hashes.get(p, "") for p in sorted(files)
                if p.endswith((".h", ".hh", ".hpp", ".hxx", ".inl"))
            ).encode()).hexdigest()[:16]
            todo = []
            for entry in entries:
                key = hashlib.sha256(
                    f"{extract_clang.EXTRACTOR_VERSION}:{snapshot.hashes.get(entry.file)}:"
                    f"{headers}:{' '.join(entry.args)}".encode()
                ).hexdigest()
                cache_path = f"{_CLANG_PREFIX}{entry.file}"
                keep.add(cache_path)
                cached = store.cached(cache_path, key)
                if cached is not None:
                    for fact in cached:
                        clang_facts[(fact.kind, fact.file, fact.line, repr(sorted(fact.data)))] = fact
                        covered.add(fact.file)
                    covered.add(entry.file)
                else:
                    todo.append((entry, key, cache_path))
            report("facts", f"libclang: {len(entries)} translation units, {len(todo)} to parse")
            frozen = frozenset(files)
            jobs_clang = [(root, entry, frozen) for entry, _, _ in todo]
            clang_out: list[tuple[list[Fact], list[str], str | None]] | None = None
            if len(jobs_clang) > 1 and _workers(workers) > 1:
                try:
                    with ProcessPoolExecutor(max_workers=_workers(workers)) as pool:
                        clang_out = list(pool.map(_clang_job, jobs_clang))
                except (BrokenProcessPool, OSError) as exc:
                    result.warnings.append(f"Parallel libclang failed ({exc}); ran serially.")
            if clang_out is None:
                clang_out = [_clang_job(job) for job in jobs_clang]
            failed = 0
            for (entry, key, cache_path), (facts, files_covered, error) in zip(
                todo, clang_out, strict=True
            ):
                if error:
                    failed += 1
                    result.warnings.append(f"libclang could not parse {error}; using tree-sitter")
                    continue
                store.replace(cache_path, key, facts)
                covered.update(files_covered)
                for fact in facts:
                    clang_facts[(fact.kind, fact.file, fact.line, repr(sorted(fact.data)))] = fact
            result.stats.update(clang_units=len(entries), clang_failed=failed)
    store.prune(keep)

    facts: list[Fact] = []
    for path in sorted(per_file):
        facts.extend(
            f for f in per_file[path] if not (path in covered and f.kind in STRUCTURAL_KINDS)
        )
    facts.extend(clang_facts.values())
    facts.extend(protocol_facts)
    facts.extend(build_facts)

    include_dirs = [f.data["dir"] for f in build_facts if f.kind == INCLUDE_DIR]
    for fact in build_facts:
        if fact.kind == BUILD_TARGET:
            include_dirs.extend(fact.data["include_dirs"])
    result.facts = resolve_includes(facts, IncludeResolver(files, include_dirs))
    result.stats["facts"] = len(result.facts)
    return result
