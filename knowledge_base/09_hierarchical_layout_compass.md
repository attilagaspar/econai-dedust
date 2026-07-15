# Phase H — hierarchical layout & record grouping (Compass-class sources)

Goal: handle sources where tables are NOT the dominant object — pages that mix
firm descriptions (paragraphs), balance-sheet tables, and occasional figures.
Three capabilities, layered: (H-A) detect **regions** (firm_header /
text_block / table / figure); (H-B) detect structure **inside** table regions;
(H-C) **group** regions into records ("this description + this balance sheet
= one firm"), across page boundaries.

Corpus facts that shaped this design (user, 2026-07-15):
- Compass layout varies BY DECADE but is consistent within a decade →
  one region model per decade, each **fine-tuned from the previous decade's**
  (the existing fine-tune-from machinery is the model-management strategy).
- Balance sheets are NOT standardized (line items vary) → no fixed-schema
  shortcut; real structure detection + per-row transcription required.
- Records span pages (description ends page N, table on N+1, more prose
  after) → grouping = a PROJECT-LEVEL sweep that carries the open record
  across page boundaries. User-endorsed heuristic: **everything between two
  firm headers (in reading order) belongs to the earlier header**. Header
  detection quality is therefore the single linchpin metric.

---

## H1. Data model (foundation, no behavior change)

- **Region shapes live in the same per-page `shapes` list**, distinguished by
  label vocabulary (`firm_header`, `text_block`, `table_region`, `figure`,
  `page_header`). No new file format — the editor, LLM-per-shape, review
  queue, and batch ops all work on shapes already. A shape is "a region"
  purely by its label; a per-project config list `region_labels` declares
  which labels are regions (drives UI tinting + grouping).
- **`group_id`** (LabelMe-native field, currently unused) = record id,
  **globally sequential across the project** (cross-page). Assigned by the
  grouping sweep (H5), editable in the editor. Derived registry
  `intermediate/groups.json`: `{gid: {header_stem, header_idx, header_text,
  page_span}}` — rebuildable, never the source of truth.
- **Reading order**: per page, regions sorted column-major. Column assignment:
  per-project setting `layout_columns: auto | 1 | 2` (Compass is 2-col;
  `auto` = split at the x-histogram valley). Within a column: by y0.
  Page order = existing natural stem sort (VERIFY scan filenames sort
  correctly per volume before any sweep — cheap sanity script).
- Non-record labels (`page_header`, running footers) are declared in config
  (`group_ignore_labels`) and skipped by the sweep.

## H2. Annotation protocol (per decade)

- Sample **60–100 pages** spanning the decade (varied sections). Annotate
  region boxes only: `firm_header` = the firm-name heading line, TIGHT box;
  `text_block` = each paragraph block; `table_region` = full balance sheet
  incl. its internal headers; `figure`; `page_header`.
- Effort: regions are ~5–10 boxes/page → an annotated page costs ~1/10 of a
  cell-annotated page. 100 pages ≈ one focused day.
- **Free training data**: a script derives `table_region` boxes from every
  EXISTING cell-annotated project (bbox of each lattice + margin) → thousands
  of table examples from the yearbooks for the `table_region` class
  (domain-different but regularizing).
- Editor needs (small): per-label colors already exist; add a "regions"
  visibility toggle so region boxes and cell boxes don't visually drown each
  other (one checkbox in the toolbar, filter in drawOverlay).

## H3. Region model training

- Plain detectron2 multi-class detection — the existing Train/fine-tune
  pipeline UNCHANGED, in a dedicated project per decade
  (`compass_regions_1900s`, `compass_regions_1910s`, …), labels = region set.
- Decade chaining: train the first decade from scratch (or warm);
  every later decade = **Fine-tune from** the previous decade's model
  (existing button). Expect steep data-efficiency gains after the first.
- OPTIONAL experiment (cheap, do once): warm-start decade #1 from a
  PubLayNet/DocLayNet-pretrained Faster R-CNN — drop the downloaded weights
  into `outputs/<pseudo-project>/fast_rcnn_R_50_FPN_3x/model_final.pth` on the
  GPU host and use Fine-tune-from as-is (detectron2's checkpointer skips the
  mismatched class head automatically). If it doesn't obviously help, drop it.
- **Acceptance metric is NOT mAP**: it is `firm_header` **recall** (missed
  header = two firms silently merged — the worst, least visible error) with
  precision as secondary (spurious header = split record — visible in the
  group browser, easy to fix). Target: recall ≥ 0.99 on a held-out 20 pages
  before grouping is trusted. The standard eval printout already gives
  per-class AP; add a tiny script for header P/R at a fixed threshold.

## H4. Hierarchical inference

- **Step 1 — regions**: run the decade's region model over the volume
  (existing infer / infer-from). NEW: "apply predictions (regions)" — the
  current apply fills only EMPTY pages; region apply must MERGE region shapes
  into pages that may already have cell shapes (insert shapes whose labels are
  region labels; never touch others).
- **Step 2 — cells inside tables**, v1 = **clip**: run the cell model
  full-page exactly as today, then drop/clip detections outside any
  `table_region` (server-side post-filter; one function + a checkbox on the
  infer op). v2 = **crop** (only if v1 accuracy disappoints): run the cell
  model on table-region crops, map coordinates back — better signal-to-noise
  for small tables, ~1 session in `infer_layout.py`.
- **Step 3 — prose**: `text_block` regions are ordinary shapes → the existing
  whole-cell LLM transcription, rules, review queue all apply UNCHANGED. This
  is where the architecture pays for itself: firm descriptions need zero new
  transcription code.
- (H-B structure detection inside tables — rows/columns per the earlier
  NOTES_table_structure_detection.md plan (auto-derived row/col labels or
  fine-tuned TATR) — is deliberately DECOUPLED: Compass balance sheets can
  start with the existing per-cell annotation/detection within regions, and
  the learned-structure upgrade slots in later without touching H4's shape.)

## H5. Grouping sweep (the record builder)

- `POST /api/project/{name}/group-records` (+ batch-op UI): iterate ALL pages
  in order → reading-order the region shapes → sweep with a running `gid`
  that **persists across page boundaries**; each `firm_header` starts a new
  gid; every region (and, transitively, cell shapes inside a `table_region`)
  gets `group_id = gid`. Front-matter before the first header: gid = null.
  Writes shapes + rebuilds `groups.json` (header text = best layer of the
  header shape, so records have names).
- Determinism: re-running the sweep is idempotent given the same regions;
  manual `group_id` overrides are respected via a `group_locked: true` flag
  on hand-assigned shapes (sweep skips them).
- **Editor UI v1**: (a) tint shapes by gid (cycling palette, region layer
  only); (b) a group strip in the right panel when a region is selected:
  gid, header text, "⇐ merge into previous group" / "start new group here"
  (toggles a header's role, resweeps forward); (c) project-level **group
  browser** modal: list of records (name, page span, #blocks, #tables), click
  → jump to header. Cross-page repair happens through (b) — the sweep does
  the propagation, the human only corrects headers.
- Column/reading-order errors are the main failure mode after header recall —
  the tint makes them INSTANTLY visible (interleaved colors), which is why
  tinting ships in v1, not later.

## H6. Records export

- v1: `records.json` — one object per gid: `{gid, name, pages: [...],
  description: <text_blocks' best text concatenated in reading order>,
  tables: [<existing lattice export structure per table_region>],
  figures: [page+bbox]}`. This is the analysis-ready artifact.
- v2 (after seeing real analysis needs): Excel — "Records" sheet (one row per
  firm: name, page span, description) + "Record tables" long sheet
  (gid × table × row × col × value). Balance-sheet line items stay AS PRINTED
  (not standardized) — harmonization is a downstream research step, possibly
  a future authority ("balance-sheet items") once enough variants are seen.

## H7. Phasing, effort, order

| Step | What | Who/effort |
|---|---|---|
| P1 | H1 data model + region label config + editor region toggle + table-region auto-derive script | 1 session |
| P2 | Annotate decade #1 (60–100 pages) | user, ~1 day |
| P3 | Train region model, header-P/R eval script, (optional warm-start test) | 1 session + GPU |
| P4 | H4 inference: region apply-merge + clip-to-regions + verify prose-LLM flow | 1–2 sessions |
| P5 | H5 sweep + tint + group strip + group browser | 2 sessions |
| P6 | H6 records.json export | 1 session |
| P7+ | Next decades: annotate ~30 pages, fine-tune-from, resweep | recurring, cheap |

Order rationale: P1→P3 gives measurable header recall BEFORE any grouping
code exists (kill criterion: if recall on the worst decade can't reach ~0.99
even after fine-tuning, the sweep design must add a repair-first UI pass,
so we'd learn that as early as possible). P4 is independently useful even if
grouping never ships (better cell precision on mixed pages). P5–P6 deliver
the actual research artifact.

## Risks / open items

- **Page-order integrity**: stem sort must equal physical order (two-page
  spreads, plate pages). Sanity script in P1.
- **Tables split across a page break** = two table_regions, same gid; export
  concatenates and FLAGS (`"split_table": true`) for manual review.
- **Two-column reading order** on pages where a region spans both columns
  (full-width headers are common!) — treat width > ~70% page as column-break
  resetting elements; handle in the reading-order function, test early.
- **Exclusive GPU on Koren** — region + cell double inference doubles GPU
  passes; still minutes per volume, fine.
- Review-queue integration for text_blocks works today; a "record
  completeness" signal (record with 0 tables, or table without record) is a
  cheap, high-value queue addition in P6.
