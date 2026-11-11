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
- **Oct 21 — text bands: density and height conditions** (p82 scattered
  dust spread wide but held ~2 px per row; p446 a 4-row scanner-edge line
  at the frame's edge — both passed the width-only band test). A band's
  rows must hold ≥ 1 % of the slot width in ink on average, and a band
  must be ≥ 40 % of the page's median band height (when ≥ 3 bands). These
  are properties of print, not of the two pages; regression test extended.
- **Oct 21 — batch confirmations in the panel, not browser dialogs**: the
  derive-fragments, resweep-all, cut and write-back ops asked `confirm()`;
  a browser set to suppress a page's dialogs answers "no" invisibly, so a
  real run silently ended after its preview (two such runs on
  compass_1877). `_batchConfirmInline` (batch.js) renders Apply / Cancel
  inside the batch progress area instead.
- **Oct 21 — ⤵ Merge model labels for a project's own model + label
  picker**: the page-scope merge-apply was only offered after an
  infer-from; it now shows in page scope always, with an in-panel label
  picker (preset = the last infer-from source's labels, else all project
  labels) and no `confirm()`. Needed in a crop project whose pages already
  carry the parent's start boxes: merge `table` alone, leave the carried
  (still scored) start boxes untouched.
- **Oct 21 — merge-apply skips corrected / verified pages by default**
  (incident: a page-scope merge wrote over verified firm images — a box a
  person moved or checked still carries its detector score, so "scored =
  predicted" is not a safe guard on a human-touched page). Both merge
  modes take `statuses` (default `["predicted"]`, status-less pages count
  as predicted; `skip` refused); the dashboard picker has page-status
  checkboxes with corrected and verified off. Test.
- **Oct 21 — model reuse checks read the trained class list, not the
  palette** (compass_1877's palette gained `firm` after its start/stop/
  ignore model was trained, so fine-tuning 1882 from it was refused).
  `_trained_labels(project)` = train_summary.json `model_labels` (else the
  exported COCO categories); fine-tune-from compares the target's classes
  with it in order (same set / other order → reorder hint), infer-from
  takes it as the model's label set; the project detail exposes
  `trained_labels`; ledger rows record `model_labels`. Test.
- **Oct 21 — dashboard: notes, tags instead of the pipeline stage,
  collapsed project groups.** Notes card on top of the project detail
  (`config.notes`, autosaved, debounced); the Pipeline card and Advance
  Stage button are gone, a Tags card (`config.tags`: chips, add box with
  cross-project datalist suggestions) takes its place and the list rows
  show tags as chips that filter the list; the config PATCH accepts
  `notes` (string) and `tags` (list of unique non-empty strings, trimmed,
  400 on anything else), `GET /api/projects` returns `tags`; the project
  list folds `<prefix>_<digits>` siblings under one bare-prefix row with a
  ▸ toggle (state in localStorage, active project's group always open).
  `pipeline.json` untouched; CLI `advance` / `set-stage` marked deprecated.
  Test (`tests/test_dashboard_config.py`).
- **Oct 21 — source-PDF fallback works across operating systems**
  (foldbirtok1935 on Azure: page JSONs carry the Windows path baked in at
  import; on Linux `Path(...).name` left it whole, so the sources/ basename
  fallback never matched and "PDF refresh" / text-layer clipping failed).
  `_resolve_pdf_source` splits the basename on both separators. Test.
- **Nov 2 — `gpu_scaffold/wait_for_vm.ps1`**: retries starting the Azure GPU VM
  every 5 minutes while Azure reports no capacity (`AllocationFailed`), notifies
  on start, stops on any other error, gives up after 12 hours.
- **Nov 2 — ignore boxes are hard cuts in fragment derivation** (user:
  running heads flagged `ignore` still ended up inside the leading
  continuation when anything band-like sat above them). `_carve` splits
  every fragment's y-range at the ignore boxes overlapping it and trims
  the pieces separately; later pieces are ⛓ continuations. A fragment can
  no longer overlap an ignore box. Test.
- **Nov 6 — model classes as a subset of the palette (roadmap P13, phase 1)**:
  `train_labels` in the config (dashboard checklist), used by Prepare /
  Train / Fine-tune; negatives counted after the class filter; own
  inference and the infer-from gate read the trained classes; test-set
  scoring counts only the model's classes (it counted every label, so
  derived record boxes inflated corrections-per-page). Plan: roadmap P13,
  doc 11 "Pooling". Tests.
- **Nov 6 — training on several projects (P13, phase 2)**: `training_sources`
  / `eval_projects` in the config (validated: existing, not itself, not
  both); Prepare / Fine-tune export every source's pages with its own test
  set (400 when a source lacks a model class), multi-project COCO names
  under `/workspace`, source images uploaded only where missing, per-source
  counts in the summary + ledger. Single-project exports unchanged. Tests.
- **Nov 6 — training data panel + per-project scoring (P13, phase 3)**:
  "Training data → Choose projects…" in the dashboard (train / held-out per
  usable project, counts, label gaps; `GET training-candidates`);
  "📏 Score per project" (`POST score-projects`: model on each project's
  frozen test pages, scored separately, saved as `score_by_project` on the
  ledger row and shown in the history). Smoke-tested locally; the GPU path
  is untested on a real run. Tests.
- **Nov 6 — editor ⊞ Merge overlaps**: merges overlapping boxes of the same
  label into their union box (chains and several groups per page; never
  across labels; lattice cells untouched; best score kept, stale text
  dropped; undoable) — for verifying model predictions that stack several
  boxes on one object. Checked in the browser on a smoke page.
- **Nov 10 — plan: cross-source entity linkage** (`13_entity_linkage.md`).
  Records resolve to a shared authority at annotation time with three
  outcomes (same / new / successor), succession edges beside slices,
  authority-declared normalisation + blocking boosts + a sequence prior,
  entity view (P12 item 2), coverage flag, entity × source-year export.
  First instance: firms across Compass volumes. Docs only, nothing built.
- **Nov 10 — entity linkage: the four open questions decided** (opaque IDs,
  overrides on the record, duplicates via `duplicate_of` edge, same-vs-
  successor rule by printed name). `13_entity_linkage.md` §5.
- **Nov 10 — authority write primitives (entity linkage E1)**: `POST
  /api/authority/mint | slice | edge`, `GET /api/authority/edges`; schema
  additions `id_scheme`, `normalize.strip_tokens` (phrases stripped anywhere,
  both sides of the match), alias `lang`, `edges[]` with `duplicate_of`
  collapsing to the survivor in the resolver; `firms_hu.authority.json`
  (empty). 9 tests on a throwaway authority dir. Not deployed.
- **Nov 10 — record-level structured extraction (entity linkage E1b)**:
  `app/record_extract.py`, `POST /api/project/{name}/extract-records`, batch
  op 🧩 Records: extract with a schema (schema / model / prompt / overwrite /
  attach text / max images; dry run; chunked with progress); one call per
  record with all fragments' crops + attached text in reading order; object
  on the head fragment as `structured` + `record`; record browser 🧩 chip +
  JSON popup; `/records` returns the summary. 6 tests, LLM stubbed. Not
  deployed.
- **Nov 10 — sample identity schema** for directory records
  (`samples/schemas/directory_record_identity.json`), drafted from Compass
  1877/1882 crops: 14 nullable fields, strict-mode friendly. 13 §1b points to it.
- **Nov 10 — record-level entity resolution (entity linkage E3)**:
  `app/record_resolve.py`; config `record_authority` + `source_year`;
  `GET …/records/candidates` (identity-object queries, seat / founded /
  county / legal-form boosts, candidates' facts + last slice + crop),
  `POST …/records/resolve` (same / new / successor / clear → authority file
  + head-fragment mark), `POST …/resolve-records` (floor + margin auto-same,
  mint-unmatched bootstrap, human marks kept); batch op 🏛 Records: resolve
  to entities; record browser 🏛 chip, resolve panel, unresolved filter. 6
  tests on a throwaway authority + `_smoke_` project. Not deployed.
- **Nov 10 — record extraction through the batch service (E1c)**: `POST
  …/extract-records/submit` (🌙 checkbox in the extract panel) packages one
  request per record into the provider's batch service; the overnight job
  list shows it as a 🧩 records job and ⬇ Apply writes the answers onto the
  head fragments. The chunk / upload / manifest generator is now shared by
  the cell and record lanes (`_batch_submit_stream`); the overnight apply
  confirm is in-panel. 2 tests with a fake provider.
- **Nov 10 — batch jobs listed in the extract panel**: the 🧩 Records: extract
  panel shows the project's batch jobs with ⟳ / ⬇ Apply / cancel, so a record
  job is applied where it was submitted; the renderer fills both lists.
- **Nov 10 — batch UI fixes**: the in-panel confirm bar floats above the modal
  (it was drawn under the pinned footer buttons, unclickable); applying a
  finished batch job no longer asks for confirmation.
- **Nov 10 — record resolution: same-source exclusion + whole-name score**
  (after the real 1877 bootstrap merged 298 records into just-minted
  entities). A record never matches an entity holding a slice from its own
  source; candidates are re-scored on the whole folded, legal-form-stripped
  name (token-sort ratio). Dry run now equals the real run. 2 tests.
- **Nov 11 — record resolution: a record named only in a third language**
  (other_names, e.g. Romanian) can be minted; its first other name is the
  display name. Found on the real 1877 bootstrap (1 of 805).
