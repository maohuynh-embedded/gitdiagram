# archdiag

Local, standards-aware architecture diagrams for embedded C/C++ repositories. Everything runs
on your machine: no cloud service, no paid API key, no code leaves the computer.

- **Static analysis first (B).** Components, layers and dependencies come from the code
  (tree-sitter, plus libclang when a `compile_commands.json` exists), and every edge carries
  `file:line` evidence. A local LLM only names and explains.
- **Standard notations (C).** An ISO/IEC/IEEE 42010 architecture description with C4,
  ArchiMate (Open Group exchange format, XSD-validated), SysML v2, an AUTOSAR-style SWC view
  (plus ARXML ↔ code differences) and an ISO 26262 / ASPICE safety view with a trace matrix.
- **LLM-only fallback (A)** for repositories without C/C++.

Specs and plans: `docs/superpowers/specs/` and `docs/superpowers/plans/`. Measured results:
[`benchmarks/RESULTS.md`](benchmarks/RESULTS.md).

## Install

```bash
brew install ollama && brew services start ollama
ollama pull qwen3-coder:30b          # about 19 GB; any local model works
cd archdiag
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,clang,validate]'   # clang and validate are optional
.venv/bin/archdiag fetch-schemas                    # once, for ArchiMate XSD validation
```

## Use

```bash
archdiag doctor                               # Ollama, model, RAM, libclang
archdiag analyze ~/work/fw                    # static analysis + LLM annotation → ~/work/fw/.archdiag/
archdiag analyze ~/work/fw --no-llm           # static only, a second or less
archdiag check   ~/work/fw                    # architecture rules; exit 3 on violations (CI)
archdiag ad      ~/work/fw                    # 42010 description + C4/ArchiMate/SysML/AUTOSAR/safety
archdiag ad      ~/work/fw --notation c4,archimate --strict
archdiag serve   ~/work/fw                    # viewer at http://127.0.0.1:8765
archdiag bench   ~/work/fw --expected answers.toml [--compare]
```

`--mode auto|static|llm` picks the path (auto: static when there is C/C++). `--compile-db`
points at a `compile_commands.json`; one in the root or `build*/` is found automatically.
`--rebuild-facts` drops the per-file cache.

Exit codes: `0` success, `1` analysis failed, `2` configuration or environment problem,
`3` architecture violations (`check`) or validation errors (`ad --strict`).

## Outputs (`<repo>/.archdiag/`, or `--out`)

| Path | Content |
|---|---|
| `model.json` | ArchModel v2: components, edges with evidence, messages, violations, views |
| `views/*.mmd`, `diagram.mmd` | Mermaid per view: system, dependencies (collapsing to layers), interfaces, classes |
| `explanation.md` | overview, component table, violations, messages |
| `facts.db` | per-file facts cache (SQLite) and cached LLM annotations |
| `ad/architecture-description.md` | the 42010 document (stakeholders, viewpoints, views, correspondences, decisions, inconsistencies, traceability) |
| `ad/c4/` | Mermaid C4, `workspace.dsl` (Structurizr), C4-PlantUML, class diagrams |
| `ad/archimate/model.xml` | Open Group exchange format (Archi, Sparx EA, BiZZdesign) |
| `ad/sysml/model.sysml` | SysML v2 textual model |
| `ad/autosar/` | derived SWC view (`swc.mmd/.puml`), `arxml-diff.md` when ARXML exists |
| `ad/safety/` | ASIL view, `trace-matrix.csv/.md` |
| `ad/validation.json` | issues per notation and correspondence results |

The AD is a **draft work product**: archdiag is not a qualified tool under ISO 26262-8 §11.

## Configure (`<repo>/archdiag.toml`)

```toml
model = "qwen3-coder:30b"                 # LLM settings: see also ~/.config/archdiag/config.toml
language = "English"
exclude = ["docs/"]

[architecture]                            # optional intended architecture
layers = ["presentation", "application", "domain", "infrastructure"]   # top → bottom
[[architecture.components]]
name = "can_driver"
layer = "infrastructure"
paths = ["drivers/can/**"]
[[architecture.rules]]
from = "domain"
deny = ["infrastructure"]

[protocol]                                # presets: can, canopen, autosar-rte (all on by default)
presets = ["can", "canopen", "autosar-rte"]
[[protocol.code]]
name = "myproto"
message_name_patterns = ["MSG_*"]
send_calls = ["proto_send*"]
recv_calls = ["proto_on*"]

[[c4.people]]                             # people and external systems are never guessed
name = "Service technician"
uses = ["main_app"]

[[decisions]]                             # plus ADRs under docs/adr/*.md
id = "ADR-1"
title = "CAN is the only device link"

[[safety.components]]                     # plus @asil / @req / @safety_goal tags in comments
name = "drivers/can"
asil = "B"
requirements = ["SW-REQ-042"]

[requirements.reqif]                      # or [requirements.csv]
path = "req/export.reqif"

[autosar]
arxml = ["arxml/system.arxml"]            # found automatically when omitted
mapping = { SpeedSensor = "swc/speed" }   # SWC → component (Rte_<Swc>.h is used otherwise)

[sysml]
validator = "sysml-check {file}"          # optional external SysML v2 validator
```

An LLM endpoint that is not on this machine is refused unless you pass `--allow-remote-llm`,
including when the endpoint comes from a repository's own `archdiag.toml`.

## Develop

```bash
.venv/bin/python -m pytest              # 250+ tests, no LLM needed
.venv/bin/python -m pytest -m live      # end to end with the local model
.venv/bin/ruff check . && .venv/bin/mypy
```

Keep answer files for private projects in `benchmarks/private/` (gitignored).
