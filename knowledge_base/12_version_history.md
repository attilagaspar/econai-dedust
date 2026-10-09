# Version history

*Maintenance rule (set by Attila, 2026-10-09): **every commit appends one
line to the current period's section below**, in the same commit — a date, a
short plain-language description, and the commit's subject if it differs.
When a commit changes what Dedust can do, update
[00_capabilities.md](00_capabilities.md) too. New months get a new heading
with a one-line theme. The pre-2026-10-09 history below was reconstructed
from all 451 commits and the knowledge-base status notes.*

---

## 2026-04 — Birth: the unified pipeline app (Apr 28–30)

The initial commit establishes the shape that still holds: FastAPI backend +
static HTML, projects with LabelMe-style page JSONs, a project dashboard with
GPU-server SSH integration, and the annotation editor. Within three days:
page import (PDF→JPEGs, source filenames preserved), COCO conversion,
train/infer with live SSE logs, and a fast box-editing vocabulary —
copy-paste, drag-to-copy, Ctrl+arrow clone, clone-to-page, multi-select,
rubber-band, stamping, autofill. The parallel-write corruption bug was found
and fixed in week one. Also: table drawer with equal split, perspective
correction v1.

## 2026-05 — OCR, LLM, lattice, Excel export, Data Lab

- **Lattice** (May 1–7): detect, completion (predict missing cells), resize
  by dragging boundaries, row/col fill, snap-to-grid, separator add/delete
  tools, overlap handling.
- **OCR** (May 2–4): per-cell Tesseract, then EasyOCR (GPU warm-up),
  line-by-line modes with comb-filter row detection, shadow-page line
  removal with tunable preprocessing, OCR input preview.
- **LLM** (May 2–6): OpenAI cleaner panel, line-by-line mode, batch over
  same-label shapes, local Qwen via ollama, consistency test modal,
  temperature 0, prompt transparency, empty-cell guard (later removed).
- **Batch modal** born (May 4) replacing "Clear pages": OCR, LLM, score
  threshold delete, conditions; diagnostic highlight dropdown.
- **Excel export** (May 12–13): spatial layout preservation, one Excel row
  per text line, dual-page alignment, collision rescue.
- **Data Lab / validator** (May 12–28): standalone batch-cleaning view —
  constraint rules, crawl (row-shift) detection through several algorithm
  iterations, per-row violation solver with digit-level edit model,
  hierarchy levels, inequality constraints, history tab with revert, batch
  clean. (Still exists as `validator.html`; superseded in spirit by the
  in-editor rules + dataset layer.)
- **Cross-project inference** ("infer-from", May 22) after a week of Docker
  path/mount debugging; detached training that survives webapp restarts.
- Perspective correction rebuilt on cv2.warpPerspective with auto corner
  detection.

## 2026-06 — Internal rows, rules & rule fix, clips, authority resolver

- **PDF text layer** extraction (May 29) matured; anchored OCR/LLM modes
  (Jun 8–9): project a reference column's structure across the row.
- **Internal row structure** (`row_struct`, Jun 12) — the month's big one:
  per-cell row bands shared by all layers, rows table with copy-to-Human /
  majority vote / per-column refresh / crop thumbnails, atomic JSON writes
  (temp + `os.replace` + Dropbox-lock backoff), band projection as
  authoritative anchor source, Excel export made row_struct-authoritative
  with the 1/0 page-pattern tiling and rank-based row sync.
- **Row rules + rule fix** (Jun 12–23): per-internal-row arithmetic rules,
  red shading, LLM-assisted fix modal with crops, diffs, chunking, per-line
  checkboxes, `llm_fixed` flag, edits routing to Human, H badges.
- **Clips** (Jun 14): cross-page data-unit flags.
- **Authority resolver Phase 1** (Jun 19–26): places_hu + industries_hu
  files git-tracked, rapidfuzz matcher, per-row resolution column,
  per-column mapping, Resolve-column, Excel Resolved sheet, ditto-mark
  inheritance, batch resolution with soft context, authority-defined
  query_strip. The "~15% lost saves" rule-fix bug fixed by `_serializeWrite`.
- **Structured JSON extraction** (Jun 28–30): schema-constrained LLM mode +
  JSON export with propagate-forward. GPT-5/o-series support; Azure OpenAI +
  TK GPU backends (Jul 1).

## 2026-07 — Hardening, split, remote access, reports; the knowledge base

The most intense month (≈100 commits).

- **Jul 5 — the hardening day**: knowledge_base docs created; no-cache
  static serving (ends the stale-cache ritual); per-page mutation lock;
  pinned deps; first pytest suite; `index.html` split into 9 ordered JS
  modules + CSS; authority worklist + alias promotion + LLM disambiguation +
  resolved badges.
- **Jul 6 — P5/P8**: parallel batch LLM + sqlite response cache; overnight
  Batch-API lane (half price); vision benchmark harness; batch recipes,
  preview, one-step batch undo, Ctrl+K palette, disagreement borders.
- **Jul 7 — P1+P3**: review queue + review strip + page-status scoreboard;
  structural blanks (free ink scan).
- **Jul 8 — remote Phase A**: token guard, folder caging, login, mobile
  review PWA; "Dedust" rebrand; structure/content separation (⊞ Build
  internal row structure replaces anchored batch ops); unified batch OCR op.
- **Jul 9–14 — deployment**: per-workspace Docker names, self-healing
  containers, fine-tune-from (active learning), webapp dockerized (C1),
  Azure VM live at azure.gasparattila.hu (C2), push-project / pull-project,
  GPU server profiles, SSH password auth.
- **Jul 15–23**: Phase H region-layer foundation (inert); folder-wide
  search; batch 1/0 page pattern; **authority duplicate / unresolved /
  lookup reports** (the report chassis); project rename/soft-delete; 1930
  authority layer v2 (full census rescrape); rows-rebuild pulling flat
  layers back in + repair script; gpu_scaffold (bootstrap any GPU server
  from the repo); dedust-gpu T4 VM live (C3a); inbox bulk import; editable
  label palette.
- **Jul 24**: page skip/clutter status + batch set-status. *(Jul 23: the
  slot-alignment export incident that motivated the dataset layer.)*

## 2026-08 — The dataset layer, Phase 1 (Aug 24)

Plan written (declared schema, project-wise diagnostics, tidy export;
join mode as a declaration property), then built in one day-long session:
declaration files, the builder, the diagnostics ladder
(structure/parse/range/keys), the 📊 findings report in the report chassis —
plus two reality-forced engine decisions from the first run on foldbirtok1935:
the join unit is the **page-level column sequence** (not the lattice band),
and **flat cells are separator bands**, with `exclude_keys` declaring printed
totals/headers as non-records. Scoreboard counts clutter correctly.

## 2026-09 — Lattice upgrades, newsflow, learning-diagnostics plan

- Multi-lattice batch correction (stacked tables), lattice-completion hole
  fixes, selectable split rules (Sep 10–11).
- P10 learning-diagnostics explainer + roadmap (Sep 11), page soft-delete +
  status-colored page selector (Sep 12).
- **Newsflow** (Sep 15–17): newspaper flow reconstruction — columns, bands,
  fold detection, horror vacui, reading order, article group_ids; Markdown
  export of the reconstructed flow.
- Perspective correction rebuilt again (token stage, zoomable); batch OCR
  became a server-side background job (Sep 19).
- Dataset Phase 1 marked shipped in the plan doc.

## 2026-10 — Learning diagnostics built; dataset Phase 2 + quality dashboard; declaration UI

- **Oct 1 — P10 built**: frozen test set, status-filtered training export
  (verified-empty negatives), training ledger, auto-evaluation with the
  corrections-per-page metric, nested learning-curve subsets, correction
  telemetry; P9.6 trash page; S-key skip; inbox label-set copy.
- **Oct 5–7 — newsflow round 2**: editable flow fields, flow_order-aware
  JSON propagation, the flow grid (view + edit).
- **Oct 7–8 — dataset Phase 2 + P11, one push**: `app/diagnostics.py`
  (identities with one-OCR-slip culprit suggestion, printed totals with
  best-suffix anchoring, log-scale IQR/MAD, digit excess, trailing-"1" per
  page column, optional ESD; confirmations + adjudication + run history),
  the `quality.html` data-quality dashboard, badges + ✓ fix / ✓ genuine in
  the editor report. Then the **dataset declaration API + 📋 editor window**
  (test build, page map, sum discovery — rebuilt foldbirtok_main from
  scratch, 33 variables / 3,413 records). Training-ledger robustness; GPU
  push survives root-owned leftovers; self-fine-tune exposed in the
  dashboard.
- **Oct 9 — ratio rules (A3)** (`dfa77e6`): declared num/den bounds whose
  operands may come from another dataset (`dataset:var`) or project
  (`project/dataset:var`), joined on the resolved entity key ID — the
  tractors-per-acre class of checks; findings anchor at the local cell and
  are confirmable. *Incident note: an out-of-date session first committed a
  duplicate "Phase 2" over the real one (`c03ccee`) and reverted it minutes
  later (`4210835`); nothing was lost, production never ran the bad code —
  the lesson (pull + read status lines before building) is now codified in
  CLAUDE.md, along with this file's maintenance rule.*
- Oct 9: capabilities reference (00) + this version history (12) added;
  documentation upkeep becomes part of every commit.
- Oct 9: docs — temporal authority reframed as general infrastructure
  (slices for attribute change + succession edges for entity
  transformations; firms are the motivating future case), per Attila; not
  a Techxtremism-specific unblocker.
- Oct 9: design principle made explicit (Attila): **Dedust is software, not
  a per-project tool** — the engine stays generic, projects are instances
  via data/config files; no feature is designed around one research project.
  Stated in 01_project_overview, README conventions, CLAUDE.md.
