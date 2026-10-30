# Bench results

Aggregate numbers only. The reference project is confidential: its name, structure and answer
file (`benchmarks/private/`) are never committed.

Machine: Apple M2 Pro, 32 GB. Model: `qwen3-coder:30b` (Ollama 0.34.4, Q4_K_M).

Scoring compares **file sets**: an expected component counts as found when one node holds at
least half of its files (a coarser split) or when nodes that sit inside it cover at least half
of it (a finer split). A relation counts when an edge joins two different nodes that belong to
the two components. Rows before 2026-09-26 16:00 were rescored with this method.

## Reference project (C++/QML, ~350 analysable files, 13 expected components, 9 relations)

| Date | Mode | Component recall | Relation recall | Path validity | Junk nodes | Time |
|---|---|---|---|---|---|---|
| 2026-09-26 | A: LLM only, 22 of 241 files in context | 84.6% | 44.4% | 100% (2 invented paths removed) | 0 | 5 min 50 s |
| 2026-09-26 | B: static, tree-sitter, `--no-llm` | 100% | 100% | 100% | 0 | 0.4 s cold, 0.07 s warm |
| 2026-09-26 | B: static + annotation (18 components, 19 LLM calls) | 100% | 100% | 100% | 0 | 175 s first run, cached after |

## mini-repo fixture

| Date | Mode | Component recall | Relation recall | Path validity | Junk nodes | Time |
|---|---|---|---|---|---|---|
| 2026-09-26 | A | 100% | 100% | 100% | 0 | 39 s (cold model) |

A also produced edges the code does not contain (for example a driver "controlling" the
application). B only emits relations backed by `file:line` evidence.
