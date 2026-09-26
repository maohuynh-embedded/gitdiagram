# Bench results

Aggregate numbers only. The reference project is confidential: its name, structure and answer
file (`benchmarks/private/`) are never committed.

Machine: Apple M2 Pro, 32 GB. Model: `qwen3-coder:30b` (Ollama 0.34.4, Q4_K_M).

## Reference project (C++/QML, ~350 analysable files, 13 expected components, 9 relations)

| Date | Mode | Component recall | Relation recall | Path validity | Junk nodes | Time |
|---|---|---|---|---|---|---|
| 2026-09-26 | A (LLM only, 22 of 241 files in context) | 92.3% | 44.4% | 100% (2 invented paths removed) | 0 | 5 min 50 s |

## mini-repo fixture

| Date | Mode | Component recall | Relation recall | Path validity | Junk nodes | Time |
|---|---|---|---|---|---|---|
| 2026-09-26 | A | 100% | 100% | 100% | 0 | 39 s (cold model) |
