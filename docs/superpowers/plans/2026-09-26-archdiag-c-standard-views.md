# archdiag C: standard views — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Checkbox steps.
> TDD per task: write the listed tests, see them fail, implement, see them pass, commit.

**Goal:** Turn the static ArchModel v2 into an ISO/IEC/IEEE 42010 architecture description
with C4, ArchiMate, SysML v2, AUTOSAR-style and ISO 26262 / ASPICE views, each validated.

**Architecture:** `ad/model.py` holds a neutral 42010 metamodel built from ArchModel v2 plus the
`[c4] [safety] [autosar] [requirements] [[stakeholders]] [[concerns]] [[decisions]]` tables.
One exporter per notation turns it into files and reports issues. Correspondence rules compare
the element sets that each exporter covered.

**Spec:** `docs/superpowers/specs/2026-09-25-archdiag-standard-views-design.md`

## Global constraints

- Everything from A and B still holds: local only, confidential reference project, no invented
  structure (people, decisions and ASIL only come from declarations or code tags).
- Outputs go under `<out>/ad/`, and validation results go to `<out>/ad/validation.json`.
- Every generated identifier and string is escaped for its notation.
- `archdiag ad --strict` exits 3 when a validator reports an error.

## Deviations from spec C

1. **No extra LLM calls in C.** The AD overview reuses the explanation that B's annotation
   already wrote. View descriptions come from templates, so C is deterministic and fast.
2. **System nodes carry their member components** (`NodeV2.members`), so C4 containers can list
   their components without guessing.
3. **XSD validation** of ArchiMate uses `lxml`, an optional `[validate]` extra. Without it, only
   the built-in relationship table is checked, and this is recorded as a note.
4. **Code tags** (`@req`, `@asil`, `@safety_goal`) are collected by B's tree-sitter pass as new
   `tag` facts. The `autosar-rte` protocol preset recognises `Rte_*` calls.

## Tasks

### C1: metamodel, AD document, C4

- `model/schema_v2.py`: add `NodeV2.members`. `arch/views.py` fills it for system nodes.
- `ad/config.py`: loaders for the tables, with ConfigError on bad input.
- `ad/model.py`: AD, Stakeholder, Concern, Viewpoint, ADView, Element, Relation, Correspondence,
  Decision.
- `ad/build.py`: ArchModelV2 + snapshot + config → AD. Also adds technology per container and
  default stakeholders and concerns.
- `ad/decisions.py`: `[[decisions]]` plus `docs/adr/*.md` (the title and status lines).
- `ad/viewpoints.py`: the library from spec §5.
- `ad/export/c4.py`: Mermaid C4 (context, container, one component view per container, code as
  a classDiagram), Structurizr DSL, and C4-PlantUML using the stdlib include.
- `ad/validate.py`: the Issue type and the C4 checks.
- `ad/document.py`: `architecture-description.md` with sections 1–8.
- `ad/pipeline.py`: `generate_ad(...)` writes files and `validation.json`.
- `cli.py`: `archdiag ad`. `web/server.py`: `/api/ad`, `/api/ad/files`, `/api/ad/file`.
- **Tests:** escaping, element mapping (containers, components, externals, device, people only
  when declared), DSL and PlantUML golden checks, C4 validation, document sections, CLI.

### C2: ArchiMate

- `ad/export/archimate.py`: element and relationship mapping, organizations, a layered diagram
  with coordinates, and the relationship validity table.
- `archdiag fetch-schemas`, plus optional XSD validation with lxml.
- **Tests:** mapping, XML well-formedness and namespaces, NCName identifiers, the validity table
  rejecting a bad relationship, and XSD validation when the schemas are present (skipped
  otherwise).

### C3: SysML v2

- `ad/export/sysml.py`: package, part defs and parts, item defs for messages, port defs with
  `out` items and conjugated receivers, `connect`, and `dependency`.
- The internal checker, plus an optional `sysml_validator` command.
- **Tests:** name quoting, a missing def is detected, connect direction, and golden output on the
  mini-repo.

### C4: AUTOSAR and safety

- B: add the `tag` facts and the `autosar-rte` preset.
- `ad/importers/arxml.py`, `reqif.py`, `csv_req.py`.
- `ad/export/autosar.py`: the derived SWC view (Mermaid and PlantUML), ARXML ↔ code
  differences, and port/interface checks.
- `ad/export/safety.py`: merged ASIL (highest wins, conflicts warned), the checks
  `ffi-dependency`, `asil-missing`, `req-unallocated`, `component-untraced` and
  `req-unverified`, `safety.mmd`, and `trace-matrix.csv/.md`.
- **Tests:** a small hand-written ARXML fixture with `Rte_*` code, a ReqIF fixture, a CSV
  fixture, ASIL conflict, and each check.

### Verification

- Run `archdiag ad` on the mini-repo and on the reference project (output outside the project).
- Check the web page. Lint, types, all tests.
