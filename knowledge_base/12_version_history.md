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
- **Oct 18 — detection scope (nested detection, §H8)**: `app/scope.py`
  + `prepare_training_data(scope=)` crop export (per-parent crops under
  `intermediate/crops/`, translated + clipped children, childless parents
  as negatives, split inheritance), `infer_layout.py --parents` crop
  inference with page-coordinate mapping (pure geometry shared by export
  and inference; module now imports without detectron2),
  `POST …/apply-predictions-merge`, `detection_scope` in project config +
  dashboard selector and ⤵ Merge button, scope on every train / infer /
  infer-from / fine-tune request and ledger row. 20 tests, no GPU; design
  in 09 §H8. Config PATCH now uses the atomic writer.
- **Oct 18 — §H8 second slice: cascade without cloned label sets**:
  infer-from accepts a source whose labels are a *subset* of the target's
  (`scope.missing_labels`; source order = class ids; gate runs before the
  GPU-server gate; SSE log names predicted vs untouched labels),
  fine-tune-from stays strict (class head is copied — documented),
  `apply-predictions-merge` gains a page-scope mode keyed by `labels`
  (`scope.merge_labels` / `merge_apply_labels`: scored shapes of the set
  replaced in place, other labels + unscored shapes + skip pages untouched,
  multiset change detection → strictly idempotent), dashboard remembers the
  last whole-page infer-from per project and offers **⤵ Merge model labels**
  with the label set in the confirm. 8 tests on `_smoke_` fixtures incl. the
  full parent → merge → within-scope cascade; suite 255 green.
- **Oct 18 — record chaining (§H5 for one parent label)**: `app/records.py`
  (reading order with band/column logic, sweep with `continuation` /
  `group_id` / `group_locked`, forward-resweep seed, registry, page-order
  check), config `record_label` + `layout_columns`, shape PATCH for the
  marks, `POST …/sweep-records` (dry run, from_stem) + `GET …/records` +
  `GET /api/records/context`; editor `records.js` (C hotkey, inspector
  button, tint + ⛓ glyph + `r<id>` badge in drawOverlay, batch ops
  ⛓ sweep / browser on the report chassis), dashboard selectors. 18 tests
  on `_smoke_` fixtures (column ordering, cross-page chaining, locked ids,
  idempotence, orphans, forward resweep, rows-PATCH survival); suite 273
  green; verified in a browser on :8001. Live server not restarted.
- **Oct 18 — crop round trip (crops as pages + write-back, §H8)**:
  `app/crops.py` (`derive_crops`: every box of a label → image page
  `<stem>__crop<k>` of a target project with `flags.crop_source`
  provenance incl. `group_id` / `continuation`; idempotent, provenance
  refresh, stale detection with policies keep / replace / force, human /
  reviewed crop pages protected, vanished boxes reported, optional carried
  children stamped `crop_origin` on the source; `write_back`: shapes
  translated to page coordinates incl. absolute row bands with all content
  kept, `crop_origin` stamp + record id, same-origin replacement only,
  table-id renumbering on collision, stale skipped, multiset change
  detection, dry run), endpoints `POST …/derive-crops` (creates the target
  project when missing) and `POST …/write-back`, `/api/records/context`
  now returns `projects_root`; editor `crops.js` (batch ops ✂ cut / ⤴ write
  back with dry-run previews and source-page undo snapshots, origin pill
  with source deep link, ⤴ badge on written-back shapes). 15 tests on
  `_smoke_` fixtures; docs 00 / 03 / 09. Live server not restarted.
- **Oct 18 — derive record fragments from start / stop / ignore roles
  (§H5/§H8: the detector learns starts and stops, fragments are
  derived)**: `app/fragments.py` (pure: `normalize_roles`, `InkMap` row /
  column ink projections with ignore boxes masked out, `derive_page` —
  bands at wide boxes, columns from the sweep's gutter logic, leading
  continuation while a record is open, start → next start/stop, last
  start → column bottom, stop closes, ink trim + margin, empty dropped,
  `carry_open` threaded page to page; `is_replaceable` replacement rule,
  `merge_fragments` keeping identical fragments / protecting hand and
  edited boxes / suppressing overlaps, `derive_project`), config
  `record_roles` (`PATCH …/config`, validated against the label set, the
  record label carries no role; `/api/records/context` returns it),
  `POST /api/project/{name}/derive-fragments` (statuses, pattern, margin,
  threshold, dry run, chained sweep, per-page counts + `empty_pages`),
  editor batch op **⛓ Records: derive fragments** (role table that PATCHes
  the config, statuses, margin, ink threshold, then-sweep, dry-run preview
  with per-page rows and the zero-starts-zero-stops list, confirm, undo
  snapshot), ⚙ in the record badge for derived shapes, dashboard roles row.
  13 tests on `_smoke_` fixtures with synthetic page images; docs 00 / 03 /
  09. Live server not restarted, not pushed.
- **Oct 19 — fragment derivation: no banding in a single column** (bug
  found by the first real dry run on compass_1877: every full-width firm
  heading became a band, so one start yielded heading + continuation
  instead of one fragment). `fragments._bands`: `layout_columns: 1` → one
  band; `auto` → bands only when the narrow role boxes reveal a gutter;
  `2` unchanged. Regression test; the wide-start test now states both
  behaviours; doc 09 §H5. Pushed and redeployed to Azure.
- **Oct 19 — record browser: readable crops**: fragment thumbnails 150 px
  high (were 60) and click-to-enlarge overlay (`_recShowCrop`, Esc/click
  closes) — a whole firm entry was unjudgeable at thumbnail size.
- **Oct 19 — record browser strip view**: 📖 toggle (remembered) renders
  every fragment as a full-width crop one under the other in record order
  (record head / ⛓ continuation edge colour, jump / toggle / resweep per
  fragment), so a volume's records can be read continuously; list view
  kept.
- **Oct 19 — fragment ink trim: scan edges + furniture lines** (second
  real-run bug, compass_1877 p70: the dark scanner edge pinned the leading
  continuation to the page top and the page number outside the running
  head's ignore box kept the head inside the crop). `InkMap.from_image`
  clears a 2 % frame (`clear_edges`); an ignore box in the top/bottom 20 %
  of the page masks its whole line. Regression test; doc 09.
- **Oct 19 — render scale + re-render pages** (`app/rerender.py`): the
  PDF import scale is a project setting (`config.render_scale`: number or
  `native` = embedded-scan resolution; `render_longest_px`) used by upload /
  path / inbox imports (dashboard select in the Import card, config PATCH
  validation). `POST /api/project/{name}/rerender` re-rasterizes the
  PDF-imported pages at a new scale and multiplies every coordinate (shape
  points, absolute row bands, `raw` / `mask`, `derived_from.bbox`,
  predictions, records registry) by the exact factor; dims + `pdf_scale` +
  a `rerender` provenance note updated, stems unchanged; dry run (counts,
  factor, refused pages: no source, PDF missing, image missing, altered
  since import); apply = background job with status polling and a zip
  backup under `backups/`; same-scale re-run is a no-op. Dashboard modal
  🔍 Re-render pages… (dry run → confirm → progress). 10 tests on a
  `_smoke_` project built from a generated PDF; docs 00 / 03 (the row-band
  unit in 03 was wrong: bands are absolute page pixels). Live server not
  restarted, not pushed.
- **Oct 20 — fragment ink trim: text bands, not first ink** (third
  real-run bug, compass_1877 p38 after the native re-render: a stain just
  below the edge frame started the leading fragment, so the running head
  sat inside it). `InkMap.text_bands`: a run of inked rows is text only
  when its ink spans ≥ 6 % of the slot width; `_trim` uses the first/last
  band. Regression test extended (stain, short last line kept, blot
  ignored); doc 09.
- **Oct 21 — fragment width: one text extent per column slot** (user
  report: a speck in the margin pulled single fragments out to the page
  edge). `InkMap.slot_text_extent`: the page-level per-column ink profile
  (full-width bands and stop boxes left out), kept at ≥ 5 % of its peak in
  runs ≥ 1 % of the slot wide; `_slot_extent` falls back to the median
  edges of the role boxes, then to the rows' own extent. Every fragment of
  a slot shares that x extent. Test; doc 09.
