# archdiag A: local CLI + web — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking. TDD: write the listed tests first, watch them fail, implement, watch them pass, commit.

**Goal:** A Python CLI (`archdiag analyze | serve | doctor | bench`) plus a local web viewer. It turns a local repository into a validated `ArchModel` and a Mermaid diagram, using only a local LLM.

**Architecture:** Pure pipeline stages: scan → context → llm → model (validate) → render. `pipeline.py` and `cli.py` are the only modules that touch the file system or the network. The web server only reads `<out>/`, except for its "analyze again" endpoint.

**Tech stack:** Python ≥ 3.12 (machine: 3.14), pydantic 2, httpx, pathspec, FastAPI + uvicorn, pytest, ruff, mypy. Frontend: vanilla JS, with mermaid 11 and DOMPurify vendored.

**Spec:** `docs/superpowers/specs/2026-09-25-archdiag-local-cli-design.md`

## Global constraints

- Everything runs locally. The default LLM endpoint is loopback. A non-loopback endpoint needs `--allow-remote-llm`.
- Never commit anything that identifies the confidential reference project. Its bench answers go in `archdiag/benchmarks/private/`, which is gitignored.
- id pattern `^[a-z][a-z0-9_]{0,47}$`. At most 60 nodes, 120 edges and 20 groups.
- Exit codes: `0` ok, `1` analysis failure, `2` configuration or environment error.
- Default context budget is 32K tokens `num_ctx`, and the evidence budget is 20K tokens.
- The web server binds to 127.0.0.1 only and rejects other Host headers.

## Deviations from spec A (fold them back into the spec in Task 1)

1. **LLM API.** The default is the native Ollama API (`http://127.0.0.1:11434`, `/api/chat`), because only it accepts `options.num_ctx`. Without it, Ollama silently truncates the prompt to its small default context. OpenAI-compatible servers are supported with `api = "openai"`.
2. **No ELK in A.** Mermaid's built-in layout is enough for ≤ 60 nodes. ELK is reconsidered in C.
3. **No `click`/`href` directives in the generated Mermaid.** The web UI attaches node clicks itself. The link allowlist becomes "strip every `<a>`" and "build `vscode://file/` links only from validated model paths".

## File map

```
archdiag/
  pyproject.toml  README.md  .gitignore
  benchmarks/mini-repo.expected.toml
  src/archdiag/
    __init__.py  cli.py  config.py  pipeline.py  bench.py
    scan/{__init__,walker,snapshot}.py
    context/{__init__,select}.py
    llm/{__init__,client,prompts}.py
    model/{__init__,schema,validate}.py
    render/{__init__,mermaid,explanation}.py
    web/{__init__,server}.py  web/static/{index.html,app.js,app.css,vendor/*}
  tests/
    conftest.py
    fixtures/mini-repo/…
    unit/test_config.py test_walker.py test_snapshot.py test_select.py test_schema_validate.py
         test_mermaid.py test_prompts.py test_client.py test_bench.py test_server.py test_cli.py
    pipeline/test_pipeline.py
    live/test_live.py
```

## Tasks

### Task 1: Package scaffold + config

**Files:** `pyproject.toml`, `.gitignore`, `src/archdiag/__init__.py`, `config.py`, `tests/unit/test_config.py`, plus the spec A update for the deviations above.

**Produces:**
- `Settings` (frozen dataclass): `api`, `endpoint`, `model`, `num_ctx`, `context_budget_tokens`, `temperature`, `seed`, `timeout_s`, `max_attempts`, `language`, `include`, `exclude`.
- `load_settings(repo: Path | None, overrides: dict | None, user_config: Path) -> Settings`
- `apply_settings(base, data, source) -> Settings`
- `is_loopback(endpoint) -> bool`
- `ConfigError`

**Tests:**
- defaults point at local Ollama
- precedence: user < repo < CLI, and a `None` override is ignored
- unknown key is rejected
- wrong type is rejected
- invalid TOML is rejected
- bad `api` is rejected
- `is_loopback` parametrised: 127.0.0.1, localhost, [::1], LAN IP, public host, garbage

### Task 2: Scanner

**Files:** `scan/walker.py`, `scan/snapshot.py`, `tests/conftest.py` (`make_tree` fixture), `tests/fixtures/mini-repo/**`, `test_walker.py`, `test_snapshot.py`.

**Produces:**
- `FileEntry(path, size, language)`
- `walk(root, include, exclude) -> list[FileEntry]`
- `detect_language(name) -> str | None`
- `SOURCE_LANGUAGES`, `HEADER_LANGUAGES`
- `RepoSnapshot(root, files, dirs, readme, build_files, content_hash)` with `.name`, `.known_paths()` and `.read_text(path, max_chars)`
- `build_snapshot(root, include, exclude) -> RepoSnapshot`
- `ScanError`

**Tests:**
- skips build outputs, vendored code, generated Qt files, images, archives and binaries (NUL sniff)
- respects the root `.gitignore`
- config `exclude` and `include` (negation) work
- skips symlinks
- language detection covers `CMakeLists.txt`, `.S`, upper-case extensions and unknown extensions
- snapshot collects dirs, readme and build files
- hash changes with content
- empty repo → `ScanError`
- `read_text` refuses paths outside the snapshot

### Task 3: ArchModel schema + validation

**Files:** `model/schema.py`, `model/validate.py`, `test_schema_validate.py`.

**Produces:**
- `Group`, `Node`, `Edge` (aliases `from`/`to`), `ArchModel` (`schema_version = 1`, `title`, `groups`, `nodes`, `edges`, `explanation`, `to_json()`)
- `llm_json_schema()`, with `$ref`s inlined
- `ValidationResult(model, errors, warnings)`
- `normalize_path`
- `parse_model(raw)`
- `check_structure(model)`
- `validate_raw(raw, known_paths)`

**Tests:**
- a valid model passes
- markdown fences are stripped
- invalid JSON reports the position
- schema errors carry the location
- bad id, duplicate id, unknown group, unknown edge endpoint and self-loop are each reported
- group parent cycle is reported
- limits are enforced
- only-unknown-paths → repaired with a warning
- structural error plus unknown path → errors only
- duplicate edges are removed with a warning
- `./` and trailing `/` are normalised
- the schema has no `$ref`

### Task 4: Mermaid + explanation renderers

**Files:** `render/mermaid.py`, `render/explanation.py`, `test_mermaid.py`.

**Produces:**
- `escape_label(text, max_len=80)`
- `compile_mermaid(model) -> str`
- `render_explanation(model) -> str`

**Tests:**
- quotes, brackets, `#`, `%%`, `;`, `|`, `<script>` and newlines are escaped to `#NN;` entities
- Vietnamese letters are kept
- labels are capped
- nested subgraphs are emitted
- empty groups are skipped
- ungrouped nodes come after groups
- edge labels are quoted
- output is deterministic
- the output never contains `click`, `href` or `javascript:`
- the explanation lists components with their paths

### Task 5: Evidence selection

**Files:** `context/select.py`, `test_select.py`.

**Produces:**
- `Excerpt(path, text)`
- `Evidence(tree_summary, excerpts, budget_used, budget_limit, truncated, candidates_total)`
- `estimate_tokens`
- `summarize_tree(snapshot, counts_only=False)`
- `select_evidence(snapshot, budget_tokens) -> Evidence`

**Tests:**
- a small repo fits with `truncated` false
- the budget is respected and truncation is flagged
- README then build files come first
- round robin across directories, with headers first
- the tree falls back to counts-only when large

### Task 6: LLM clients + prompts

**Files:** `llm/client.py`, `llm/prompts.py`, `test_client.py`, `test_prompts.py`.

**Produces:**
- `ChatClient` protocol: `chat(messages, schema) -> str` and `list_models() -> dict[str, int]`
- `OllamaClient(settings, transport=None)`
- `OpenAICompatClient(settings, transport=None)`
- `make_client(settings)`
- `LLMError`, `LLMUnavailable`, `LLMTimeout`
- `PROMPT_VERSION`
- `build_messages(repo_name, evidence, language)`
- `retry_message(errors)`

**Tests:** `httpx.MockTransport` checks:
- the Ollama body carries `format` = schema and `options.num_ctx`, `temperature` and `seed`
- connection error → `LLMUnavailable`
- 404 "model not found" → `LLMUnavailable` with an `ollama pull` hint
- timeout → `LLMTimeout`
- the OpenAI client sends `response_format` of type `json_schema`
- `list_models` parses both APIs
- the prompts contain the tree, the delimited excerpts and the language
- the retry message lists the errors

### Task 7: Pipeline

**Files:** `pipeline.py`, `tests/pipeline/test_pipeline.py`.

**Produces:**
- `AnalysisError`
- `AnalysisResult(model, mermaid, warnings, attempts, cached, out_dir)`
- `cache_key(snapshot, settings)`
- `analyze(repo, out, settings, client, *, use_cache=True, progress=None) -> AnalysisResult`
- It writes `model.json`, `diagram.mmd`, `explanation.md`, `run.json` and `debug/attempt-N.txt`.

**Tests (FakeClient):**
- the first valid reply writes all outputs
- invalid JSON leads to a retry with feedback and a debug file
- unknown paths are stripped without a retry
- the run gives up after `max_attempts` with an `AnalysisError`
- a cache hit skips the LLM
- one timeout is retried
- two timeouts → `AnalysisError`
- an output dir inside the repo is not scanned

### Task 8: Bench

**Files:** `bench.py`, `benchmarks/mini-repo.expected.toml`, `test_bench.py`.

**Produces:**
- `Expected`
- `load_expected(path)`
- `BenchReport`
- `score(model, expected, known_paths)`
- `format_report(report)`

**Tests:**
- a perfect model scores 1.0
- a missing component gives partial recall
- a junk node is detected
- path validity is measured
- an invalid expected file → `ConfigError`

### Task 9: CLI

**Files:** `cli.py`, `test_cli.py`.

**Produces:** `main(argv=None) -> int`, with the subcommands `analyze`, `serve`, `doctor` and `bench`.

**Tests (monkeypatched `make_client`):**
- missing repo → 2
- remote endpoint without the flag → 2
- analyze succeeds → 0 and the files exist
- a validation failure → 1
- doctor: model present → 0, model missing → 2, unreachable → 2
- bench → 0 and `bench.json` is written

### Task 10: Web server

**Files:** `web/server.py`, `test_server.py`.

**Produces:**
- `create_app(repo, out, settings, client_factory, port) -> FastAPI`
- `serve(repo, out, settings, client_factory, port)`

**Tests (TestClient):**
- `/api/model` 200, and 404 before any analysis
- `/api/diagram` returns text
- `/api/file` returns text, 403 on traversal, absolute paths and symlinks leaving the repo, 404 on missing files, 415 on binaries and 413 on large files
- a wrong Host → 400
- `POST /api/analyze` without an Origin → 403
- `POST /api/analyze` with a valid Origin streams `event: done`
- `/` carries a CSP header

### Task 11: Web frontend

**Files:** `web/static/index.html`, `app.js`, `app.css`, `vendor/mermaid.min.js`, `vendor/purify.min.js`, `vendor/VERSIONS.txt`.

**Behaviour:**
- mermaid `securityLevel: "antiscript"` and `htmlLabels: false`
- DOMPurify SVG profile, then unwrap every `<a>`
- click a node → details (paths, open in VS Code, preview)
- pan and zoom, fit
- export SVG and PNG, copy the Mermaid source
- re-analyse over SSE
- dark mode

**Verification:** manual, in the browser pane against the mini-repo output.

### Task 12: Run locally

- Install the package in `archdiag/.venv`.
- `ollama pull` the default model.
- `archdiag doctor`
- `archdiag analyze` on the mini-repo and on the reference project (output stays inside that project).
- `archdiag serve`
- `archdiag bench` with the private expected file.
- Record aggregate baseline numbers only.
- Live test: `pytest -m live`.
