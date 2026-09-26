# archdiag

Local architecture diagrams for embedded C/C++ repositories. Everything runs on your machine
with a local LLM: no cloud service, no paid API key, and no code leaves the computer.

This is sub-project **A** of the roadmap in `docs/superpowers/specs/` (A: local CLI and web,
B: static analysis, C: standard notations).

## Install

```bash
brew install ollama && brew services start ollama
ollama pull qwen3-coder:30b          # about 19 GB; any local model works
cd archdiag
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Use

```bash
archdiag doctor                        # checks Ollama, the model and RAM
archdiag analyze ~/work/my-firmware    # writes ~/work/my-firmware/.archdiag/
archdiag serve   ~/work/my-firmware    # viewer at http://127.0.0.1:8765
archdiag bench   ~/work/my-firmware --expected answers.toml
```

`analyze` writes:

| File | Content |
|---|---|
| `model.json` | the validated architecture model (the source of truth) |
| `diagram.mmd` | Mermaid flowchart compiled from the model |
| `explanation.md` | written explanation and component list |
| `run.json` | model, timings, attempts, warnings, cache key |
| `debug/` | raw model replies, only when a reply was rejected |

Re-running on unchanged code reuses the previous result. Pass `--no-cache` to force a new one.

Exit codes: `0` success, `1` analysis failed, `2` configuration or environment problem.

## Configure

Settings come from, in increasing priority: built-in defaults, `~/.config/archdiag/config.toml`,
`<repo>/archdiag.toml`, and command-line flags.

```toml
api = "ollama"                       # or "openai" for LM Studio / llama.cpp (endpoint …/v1)
endpoint = "http://127.0.0.1:11434"
model = "qwen3-coder:30b"
num_ctx = 32768                      # context window sent to Ollama
context_budget_tokens = 20000        # how much of the repo the model sees
language = "English"                 # language of the explanation
exclude = ["docs/", "tools/legacy/"] # gitignore syntax, added to the built-in excludes
include = ["3rdparty/our_stack/"]    # re-include something excluded by default
```

An endpoint that is not on this machine is refused unless you pass `--allow-remote-llm`. This
also applies when the endpoint comes from a repository's own `archdiag.toml`.

## Bench files

```toml
forbidden = ["build/**", "3rdparty/**"]

[[components]]
name = "can"
paths = ["src/drivers/can/**"]

[[relations]]
from = "app"
to = "can"
```

Keep answer files for private projects in `benchmarks/private/`, which is gitignored.

## Develop

```bash
.venv/bin/python -m pytest          # unit and pipeline tests, no LLM needed
.venv/bin/python -m pytest -m live  # end to end with the local model
.venv/bin/ruff check . && .venv/bin/mypy
```
