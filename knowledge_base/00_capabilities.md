# Dedust — what it can do (capabilities reference)

*The complete feature inventory, kept current: **every commit that adds,
changes or removes a capability must update this file** (and append to
[12_version_history.md](12_version_history.md)) in the same commit.
Last full audit: 2026-10-09, at commit `dfa77e6`.*

Dedust (repo `econai-dedust`) is a browser-based, human-in-the-loop pipeline
for digitizing historical economic documents — Hungarian statistical
yearbooks, censuses, land registers, company registers, newspapers,
exhibition catalogs (~1890–1949). It turns scanned pages into structured,
entity-ID-linked datasets. Success metric: **human hours per thousand clean
data rows**. One FastAPI backend (`app/server.py`), static-HTML frontends,
one LabelMe-style JSON per page as the single source of truth.

---

## 1. Projects & dashboard (`dashboard.html`)

- **Project lifecycle**: create (type A tables / B structured text, custom
  labels), clone, rename, soft-delete; active project remembered.
- **Import**: PDF/image upload split into page JPEGs; source filenames
  preserved (`xyz.pdf → xyz_1.jpg…`); **Inbox bulk import** (server-side
  staging folder → project, can copy the label set from an existing project).
- **Label palette**: add/rename/delete project labels from dashboard or
  editor.
- **Scoreboard**: per-project review progress by page status
  (predicted / corrected / verified / problem / skip), % verified computed
  against working (non-clutter) pages.
- **Model Quality card** (P10): frozen test set management, training ledger,
  score-predictions-on-test-set, learning-curve view, corrections telemetry.
- **📈 Data quality card** (P11): opens `quality.html` per dataset (see §10).
- **🗑 Trash page**: one view over soft-deleted projects and pages; restore
  (refuses live-stem collisions) or purge.
- **GPU cards**: server profiles, Docker build/status, training parameters
  (persisted), live log modal. See §12.

## 2. Annotation editor (`index.html` + `app/static/js/*.js`)

- **Viewer**: OpenSeadragon deep-zoom + SVG overlay; resizable right-side
  Cell inspector; toasts; per-page undo stack; `Ctrl+K` command palette;
  `?` shortcut cheatsheet.
- **Box editing**: draw, move, resize, copy-paste, drag-to-copy, Ctrl+arrow
  clone, clone-to-page (N/P), rubber-band + Ctrl-click multi-select, bulk
  ops, stamping (M/O/P), autofill, score display, delete-below-score batch.
- **Perspective correction**: 4 corner tokens → full-image warp (zoomable
  stage, safety margin, auto corner detection).
- **Modes**: edit (`E`) / review; mode survives page navigation.
- **Page status**: dropdown + hotkeys (`V` verify-and-next, `S` mark skip);
  `skip` = clutter — excluded from review totals, never receives applied
  predictions, never trains. Page-selector entries colored by status.
- **Page deletion**: soft-delete page (image + JSON) to the project trash;
  also as a batch op.
- **Search bar**: folder-wide full-text search with type/layer filters;
  results jump-and-zoom (`_searchJump`); the editor accepts `?stem=&idx=`
  deep links.
- **Diagnostic highlight dropdown**: presence/agreement coloring, rule
  violations, row-count mismatches; amber disagreement borders (OCR≠LLM,
  no human); authority corner dots (green resolved / amber partial).

## 3. Lattice (printed-grid superstructure) — `lattice.js`

- **Detect** from box geometry; **completion** fills grid holes; multi-pass
  hole fix; overlap removal; snap-to-grid; trim overlaps.
- **Multi-table pages**: independent lattices per page (`table` id), carve /
  ✂ split / ✕ delete per table; **multi-lattice segmentation** option
  (stacked tables split at coverage gaps or divider annotations) on both the
  workspace button and the batch ops, with selectable split rules and fill
  inheritance direction.
- **Separator tools**: add/delete column & row separators, double-click
  deletes an internal separator, drag boundaries to resize whole rows/columns,
  row/col fill buttons.

## 4. Content layers & text extraction

Four layers per cell, fixed priority **Human > LLM > OCR > PDF**; Human is
only ever written by a person.

- **PDF text layer**: per-cell clipping from the source PDF (stored under
  `sources/`, portable path resolution).
- **OCR**: Tesseract + EasyOCR (GPU-warmed), whole-cell or line-by-line;
  shadow-page preprocessing (line-erased full page, tunable), digit
  whitelist, output-stage preprocessing (blur/binarize/opening); batch OCR
  as a **server-side background job** (survives browser close).
- **Internal row structure** (`row_struct`): per-cell row bands shared by
  all layers. Structure and content are separate: **⊞ Build internal row
  structure** (free, local) detects bands from the image or a layer's line
  count, or **anchor-projects** a reference column's bands across the row
  (cyclic per-page anchor pattern); OCR/LLM then read into the bands
  (Scope = internal rows). Per-row table in the inspector: copy-to-Human,
  majority vote, per-column refresh, crop thumbnails, divider editing,
  flat↔rows redistribution (`⟳`); `_sync_flat_from_rows` never destroys
  flat human text.
- **Structural blanks**: free ink scan marks blank cells/rows (`blank`),
  manual `∅`/B toggle incl. multi-select; batches and review skip them;
  export as missing.

## 5. LLM integration

- **Backends**: OpenAI, two Azure OpenAI resources (`azure:`, `azure-us:`),
  local Qwen via ollama, "TK GPU" server; GPT-5/o-series reasoning-aware
  calls (incl. JSON reasoning headroom); temperature 0.
- **Per-cell panel**: Send (image / OCR / both) × Scope (whole cell /
  internal rows keep / re-detect); anchored single-cell mode; prompt
  palette (save/load named prompts); prompt transparency (full prompt
  shown/logged).
- **Batch LLM**: parallel requests (configurable, default 6) + local sqlite
  response cache; conditions (PDF-content filters, rows-without-LLM, …);
  **🌙 overnight lane** via OpenAI/Azure Batch API at half price (JSONL
  packaging, auto-split at size limits, job tracking per project, apply /
  cancel / remove, failure reasons surfaced).
- **JSON / structured mode**: schema-constrained extraction (see §8).
- **LLM consistency test modal**; **magic wand** (experimental whole-page
  vision layout detection); vision benchmark harness (whole-page vision LLM
  vs the pipeline on gold pages, strip-wise reading).

## 6. Rules, clips, review

- **Row rules** (`⚖`): arithmetic constraints between lattice columns
  (`1+2=4`), per-rule zero characters and page pattern, evaluated per
  internal row on the best layer; violations shade the exact line red;
  "all rules" union view.
- **🛠 Rule fix**: violating lines with per-line crop snippets → LLM
  proposals in configurable chunks, diff display, satisfies-rule check,
  per-line checkboxes; apply writes the LLM layer flagged `llm_fixed`;
  edited proposals route to Human; H badge marks human rows; writes
  serialized and gated on success.
- **Clips** (`🚩`): numbered colored flags linking annotations across pages
  into one data unit; tray of dangling flags; export column / filter.
- **⚡ Review queue** (P1): server-scored suspect units (layer disagreement,
  numeric outliers, unverified) with page/column scope → docked review strip:
  Enter=accept best guess into Human, type to correct, ↓ skip, U undo;
  auto-navigation, band-crop snippet, optional 🎯 pinpoint arrow; accepting
  an empty value marks the unit blank; accepts serialized. Single decision
  endpoint `POST /api/review/accept`.
- **Mobile review PWA** (`review.html`): phone card loop over the same
  queue (zoomable snippet, thumb buttons, column/page filters), installable,
  online-only.

## 7. Authority / gazetteer (entity resolution) — `authority.js`

- **Authority files** (`authorities/*.authority.json`, git-tracked):
  `places_hu` (GIStA 1910 spine: 64 counties / 439 districts / 12,542
  settlements / ~19k aliases + the rescraped 1930 census layer, 3,555
  settlements, orthography-twin duplicates healed), `industries_hu`
  (1900 census, 159 industries). Temporal facts live in per-source-year
  `slices` on stable IDs.
- **Matcher**: mtime-cached index, exact normalized hit O(1) else rapidfuzz
  over accent-folded names+aliases; parent context is a soft boost, accept
  floor 70; authority-defined `query_strip` suffixes; ditto marks (`"`,
  `do.`, dashes…) inherit from the row above, never across cells.
- **UI**: 🏛 inspector group (source picker, type, per-table county/district
  context), per-row Auth column with live-search dropdown (double-click =
  inherit), per-column authority override, resolve cell / column.
- **Batch resolve** (`POST /api/authority/batch`): page pattern, column
  filter, overwrite policy (human picks kept), context toggle.
- **📋 Unresolved worklist**: distinct unresolved strings by frequency with
  crops + candidates; Apply resolves all occurrences; 🤖 LLM disambiguation
  per string.
- **➕ Alias learning**: human-confirmed unknown strings promoted into the
  authority file as `econai_confirmed` aliases.
- **Cross-page reports** (report chassis, minimizable pill, click-to-jump,
  editable Human + authority per row): **duplicate** (same entity resolved
  in 2+ places), **unresolved** (grouped by folded text), **lookup** (by
  ID/name list); bulk clear via checkboxes.

## 8. Structured (JSON) extraction — Type B

- Per-project JSON Schemas (`projects/<p>/schemas/`, CRUD API),
  strict-mode-friendly; LLM JSON mode per cell and in batch
  (strict → non-strict → json_object fallback for local backends).
- Result in `shape.structured`; live-validated editor (JSON validity +
  schema conformance + tree view); `edited:true` protects human data from
  re-runs.
- **JSON export** batch op: reading-order records; per-label modes
  export / ignore / **propagate forward** (e.g. country headers become a
  field on subsequent records); on newsflow pages propagation follows
  `flow_order`; single file or zip.

## 9. Newspaper flow reconstruction (`newsflow.js`)

- Rebuilds a newspaper page's reading structure from noisy region
  detections: column skeleton, horizontal bands (page-wide separators +
  whitespace cuts + fold detection), merge same-type text boxes per
  band×column, split at breakers; reading order (band → column → y);
  consecutive elements between titles share a `group_id` (= one article).
- Per-label **roles** (text / title / breaker / furniture / ignore,
  remembered); horror-vacui gap filling; column extension; furniture
  widened to column extent. Replaces the source boxes (undoable);
  run before OCR.
- **Flow grid**: lattice-style view/edit grid over the reconstruction,
  adjustable outer grid, panning preserved; editable flow fields in the
  Cell inspector + output label option.
- **Markdown export** of the reconstructed flow with page/article markers
  (batch op).

## 10. The dataset layer (declarations, diagnostics, quality)

The bridge from "pages of cells" to a declared dataset. Page JSONs stay the
single source of truth; a dataset is a **view** built on demand. Full design:
[10_dataset_layer.md](10_dataset_layer.md).

- **Declarations** (`projects/<p>/datasets/<name>.dataset.json`): scope
  (labels + cyclic page pattern → slots + page range + tables — lattice
  cells only by construction; several datasets per project coexist), record
  spec (internal_row / lattice_row; **join**: positional by page-level
  column sequence, or keyed by the identifier column; `exclude_keys` regexes
  for printed total/header rows), position → named **variables**
  (dtype int/number/text/entity, min/max, per-variable parse/stats/scale),
  Hungarian print conventions (dash = missing, space/dot thousands, comma
  decimal), declared **identities**, printed-**totals** pattern, **ratios**
  (see below), diagnostics parameter overrides.
- **📋 Dataset window** in the editor: declaration authoring UI — slot map
  from the page order, "read columns from this page", hover-highlighting,
  exclude-pattern defaults, "find sums in the data" (identity discovery),
  import from ⚖ Rules, ▶ Test preview (records, problems, per-variable
  read ✓/✗, identity hold rates), save with `.prev` backup, delete.
- **Builder** (`GET /api/dataset/<name>/build`): best-layer records joined
  across slots with full per-value provenance (stem, shape idx, row_i,
  layer); flat cells are separator bands in internal_row mode; every join
  mismatch is a loud structure finding, never a silent shift.
- **Diagnostics** (`POST /api/dataset/<name>/diagnose`,
  `app/diagnostics.py`) — the check ladder: structure (columns vs
  declaration, join), parse (dtype), hard min/max, unresolved entities,
  duplicate/missing keys; then Phase 2: **identities** (total = Σ parts,
  with single-OCR-slip culprit suggestion → one-key ✓ fix), **printed total
  rows** (best-suffix anchoring), **ratio rules** (num/den bounds where an
  operand may live in another dataset `dataset:var` or project
  `project/dataset:var`, joined on the entity key ID — tractors vs
  acreage), **IQR fences** (mild/extreme, log scale), **MAD robust z**,
  **digit-length excess**, **trailing-"1" excess** per physical page column
  (column rules OCR'd as a digit; binomial, Bonferroni, effect sizes) with
  cell-level candidates, optional **Generalized ESD**. Log scale by default,
  zero-inflation fallback, every flag carries method + parameters.
- **Confirmations & adjudication**: per-flag persistent "confirmed genuine"
  / "book is wrong" state (invalidated when the cell text changes); run
  history; flagged/corrected/confirmed/open bookkeeping — the paper-appendix
  table.
- **Findings report** in the editor (📊 batch op, report chassis: grouped
  findings, severity badges, crops, editable Human, ✓ fix for proven
  culprits, click-to-jump) and the **data-quality dashboard**
  (`quality.html`): summary tiles, run trend, adjudication table + CSV
  export, exact-check tables, per-variable log-histograms with fences,
  top/bottom-10 with crops, trailing-1 page strips, findings browser.

## 11. Exports

- **Excel**: spatial layout sheet (one Excel row per internal row; 1/0 page
  pattern tiling with cycle-aligned slots; column filter; free annotations
  interleaved and exempt from the column filter; row_struct authoritative;
  per-row layer picks; authority name+ID inline columns), companion
  **Resolved** and **Structured** sheets.
- **JSON records** (structured annotations, propagate-forward; §8).
- **Newsflow Markdown** (§9).
- **Tidy dataset export (CSV/.dta from the declaration): NOT BUILT YET** —
  dataset-layer Phase 3, the next unbuilt roadmap item.

## 12. GPU training & model management

- **Pipeline**: LabelMe→COCO prepare (always re-run before train), train /
  infer Detectron2 in Docker over SSH (paramiko), detached (nohup) with live
  SSE logs, re-attachable, containers self-stop after jobs, per-workspace
  container names (multi-user GPU hosts), self-healing rebuild, root-owned
  leftover files survived via force uploads.
- **Model reuse**: **infer-from** (cross-project inference, threshold
  configurable), **fine-tune-from** (warm-start from another project's
  model — the per-decade chaining strategy), **self-fine-tune**
  (source = target, bootstrap weights copied aside), apply-to-empty-pages
  (never touches skip pages).
- **Learning diagnostics (P10)**: frozen test set per project (training
  always excludes it), status-filtered training export (only corrected +
  verified; verified-empty pages train as negatives), **training ledger**
  (one row per run with config + scores; stale-row reconciliation),
  auto-evaluation with the human-cost metric (added/deleted/moved boxes per
  page), nested train-fraction subsets for learning curves, correction
  telemetry (predicted→corrected diffs logged on status change), transfer
  check (score any model on any project's frozen set).
- **GPU server profiles** (`app/gpu_servers.json`, per instance): named
  backends with optional stored passphrase; per-project `server_profile`;
  password-auth SSH supported; `gpu_scaffold/` bootstraps any fresh GPU
  server from the repo alone.

## 13. Remote access & multi-user

- **Token guard** (`ECONAI_TOKEN` + `/api/login`): inert locally, armed for
  remote requests; `folder` resolution caged to `projects/` for remote
  sessions; `serve --host` refuses non-local bind without a token.
- **Deployments**: home PC behind Cloudflare Tunnel
  (dedust.gasparattila.hu); Azure VM (`Dockerfile.web` +
  `docker-compose.yml`, webapp + cloudflared, azure.gasparattila.hu);
  Azure T4 GPU VM (start/stop around jobs) coexists with Koren's GPU by
  per-project profile.
- **Project sync**: `econai.py push-project` / `pull-project` (SFTP, live
  progress, additive pull with `--overwrite`); datasets/ (declarations +
  confirmations + run history) travels along. **Rule: a project the RA
  edits in the cloud is never push-projected over — single source of
  truth per project.**
- Phase B (identity stamping, advisory page locks) is planned, not built.

## 14. CLI (`econai.py`)

`serve` (kills the port's old process, `--no-browser`, `--no-reload`,
`--host`), `new-project`, `list`, `status`, `advance` / `set-stage`
(vestigial stages), `push-project`, `pull-project`.

## 15. Known not-built / planned (pointers)

- **Tidy dataset export** (Phase 3) — next on the dataset layer.
- **P12 entity index** — cross-project linkage browser on authority IDs;
  Hidiroglou–Berthelot year-to-year checks ride on it.
- **Phase H grouping** (Compass records): region layer foundation exists
  (H1); the grouping sweep, group browser and records export do not.
- **Phase B multi-user** (verified_by, page locks).
- **1933/1935 temporal place authority** (helysegnevtar_1933 feeds it).
- Full roadmap: [07_improvement_roadmap.md](07_improvement_roadmap.md);
  honest weaknesses: [06_critique.md](06_critique.md).
