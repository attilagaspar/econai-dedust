# Architecture

## Code layout

```
econai.py                    CLI entry (serve / new-project / list / status / advance)
app/
  server.py        (~5.4k)   FastAPI backend — ALL API routes, OCR, LLM calls,
                             authority matcher, Excel/JSON export, SSH/Docker orchestration glue
  ssh_ops.py                 paramiko SSH/SFTP helpers (streaming logs, detached jobs)
  coco_convert.py            LabelMe → COCO; generates train.sh (Detectron2 solver params)
  pipeline.py                pipeline stage machine (largely vestigial — see critique)
  page_import.py             PDF/image import, page splitting
  infer_layout.py            runs ON the GPU server inside Docker
  docker_config.py           container-name config
  static/
    dashboard.html (~1.9k)   project dashboard: import, GPU config, train/infer, params
    index.html     (~1.2k)   the annotation/data editor — HTML skeleton only (split 2026-07-05)
    css/editor.css           editor styles (extracted from index.html)
    js/*.js                  editor logic in ordered classic scripts sharing ONE global
                             scope — load order in index.html is load-bearing:
                             core.js → rules_clips.js → lattice.js → rows_panel.js →
                             batch.js → export_ocr.js → llm_structured.js →
                             authority.js → main.js (init/page loading last)
    validator.html (~2.4k)   older "data lab" batch-cleaning view
authorities/                 *.authority.json gazetteer files (git-tracked) + README
projects/<name>/
  config.json                type, labels, per-project server + LLM settings (incl. key paths)
  pipeline.json              nominal stage
  annotations/               page images + one LabelMe-style JSON per page  [gitignored]
  schemas/                   JSON schemas for structured extraction
  intermediate/  predictions/  output/                                      [gitignored]
knowledge_base/              these docs
```

## Backend (`app/server.py`)

Single FastAPI module. Functional areas, roughly in file order:
- **Page/shape CRUD**: `GET /api/pages`, `GET/PATCH/POST/DELETE /api/page/shape`, `PUT /api/page/shapes` (verbatim replace, used by undo and bulk ops), `PATCH /api/page/shape/rows` (row_struct; rebuilds rows from a **field whitelist**), `PATCH /api/page/flags` (page-level flags merge).
- **Imaging**: `GET /api/image`, `GET /api/cell` (PIL crop), PDF text-layer clipping.
- **OCR**: Tesseract + EasyOCR paths, line-by-line and anchored modes writing `row_struct`.
- **LLM**: `_llm_complete` (OpenAI / Azure OpenAI / local "TK GPU" backends; GPT-5/o-series aware), `_llm_complete_json` (strict json_schema → non-strict → json_object fallback), per-cell endpoint `POST /api/page/shape/llm`.
- **Authority**: mtime-cached index build, `_authority_match` (rapidfuzz), `/api/authorities`, `/api/authority/resolve|children|entity`, `POST /api/page/shape/authority`, `POST /api/authority/batch` (server-side batch resolve).
- **Schemas & structured**: `GET/PUT/DELETE /api/schemas`, `PATCH /api/page/shape/structured`.
- **Export**: Excel (`shapes_to_cells` + page-pattern tiling + column filter + Resolved + Structured sheets), `POST /api/export/json` (records with propagate-forward labels).
- **GPU orchestration**: prepare-training-data, train/infer over SSH+Docker, detached with nohup, log streaming, container auto-stop, pull predictions.

Persistence = the LabelMe JSON files themselves; every save rewrites the whole page JSON. No database, no locking beyond client-side write serialization.

## Frontend

**`index.html` + `js/*.js`** — OpenSeadragon viewer + SVG overlay + right-side "Cell inspector" panel + toolbar. The logic lives in nine ordered classic scripts (see file layout above); they share one global scope, so any function can call any other, but a *load-time* (top-level) call must not target a later file. Contains edit/review modes, lattice tools, internal-rows table, authority panel, structured-JSON editor, rules/rule-fix modals, clips, the ⚙ Batch modal (junk-drawer of batch ops: OCR, LLM, convert, resolve_authority, json_export, Excel-style filters), toasts, undo stack, `_serializeWrite` write queue, `--panel-w` resizable panel, localStorage persistence for many knobs.

**`dashboard.html`** — projects list, import, GPU server + Docker cards, training params (persisted to localStorage `trainParams`), live log modal.

## Cross-cutting mechanisms & gotchas

- **Stale cache**: fixed — `/static` is served with `Cache-Control: no-cache`, so a plain reload always revalidates. The `[econai] build-N` console marker still exists but is no longer needed as a ritual.
- **Write races**: all shape/row writes on the client go through a global promise chain (`_serializeWrite`); `saveRowStruct`/`replaceAllShapes` return success booleans and callers must gate on them.
- **Whitelist trap**: any new per-row field must be added to the rows-PATCH whitelist in server.py, and any new per-shape field survives `PUT /api/page/shapes` only because that route stores shapes verbatim.
- **Server restarts**: backend edits require restarting `python econai.py serve`; the CLI kills whatever holds the port first.
