# Phase H — hierarchical layout & record grouping (Compass-class sources)

*Status (2026-10-18): H1 data model built (`app/regions.py`, region
vocabulary, table_region derivation). **H8 detection scope — first slice
built** (crop export, `--parents` crop inference, merge-apply, dashboard
selector; see §H8 below). H2–H3 are annotation + GPU work (user). H4 v1
clip-to-regions, H5 grouping sweep, H6 records export: not built.*

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
  → **Built generically as the detection scope, §H8** (a model trained
  and run `within:table_region` is exactly v2).
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

## H8. Detection scope — nested detection within parent shapes

**Status: first slice built 2026-10-18** (crop export, crop inference with
coordinate mapping, merge-apply, dashboard selector, ledger field, unit
tests without GPU). **Second slice, same day**: the cascade needs no cloned
label sets — infer-from accepts a source whose labels are a *subset* of the
target's, and merge-apply has a page-scope mode keyed by label set (see
below). Not yet exercised on a real GPU run.

### Why

A single flat page-level detector has to learn every object scale at once
(firm headers, paragraphs, whole balance sheets, individual cells) and the
page-to-page variability of their arrangement. Hierarchical sources are
better served by a cascade: one model finds the large **parent** shapes,
another model is trained and run **only on crops of those parents**, where
the children are large relative to the input and the context is uniform.
This is a general mechanism of the engine — any label can be a parent —
and two instances motivate it today:

- `firm_area → parts` (firm name line, address block, capital line,
  balance-sheet table …) in company directories;
- `table_region → cells` in any tabular source (the H4 "crop variant").

### The scope concept

A **detection scope** is a per-project string, `detection_scope` in
`config.json`:

| value | meaning |
|---|---|
| `page` (default, or absent) | today's behaviour — whole-page training and inference |
| `within:<parent_label>` | the model is trained on crops of every `<parent_label>` shape and run on crops of the current `<parent_label>` shapes |

The scope is a property of the **model**: a model trained `within:X` only
makes sense when run `within:X`. That is why it lives in project config
(the project's own model) and travels in every request body
(`scope`) for cross-project operations (infer-from, fine-tune-from), where
the request value wins and the config value is the default. The training
ledger records `scope` on every row.

**Class set.** A scoped model's classes are the project's labels **minus
the parent label** (`scope.scope_labels(labels, scope)`): the parent is the
input, never a prediction. NUM_CLASSES in the patched yaml, the COCO
categories and `--labels` on the inference command all use this reduced
list, so the three stay consistent by construction.

### Parent–child containment: geometric, computed on the fly

There is **no stored parent field** on shapes. A shape is a child of a
parent when its **centre lies inside the parent box**; if several parents
contain the centre, the smallest wins. The relation is recomputed from
geometry whenever it is needed (export, merge-apply), so moving or
resizing a parent in the editor silently re-assigns its children, nothing
can go stale, and the data model (03) is untouched. The parent label itself
is never a child label.

### Crop export (training) — `coco_convert.prepare_training_data(scope=…)`

Page selection, status filtering, the frozen test set and the train
fraction all happen **at page level exactly as before**. Then, when the
scope is `within:X`, each selected page is expanded into one **crop page
per parent shape** (`app/scope.py::export_crops`):

- crop rectangle = parent box + a small margin (default 12 px), clamped to
  the image; written as `intermediate/crops/<stem>__crop<k>.jpg` (k =
  parent index in top-to-bottom, left-to-right order; the directory is
  cleared of stale crops first);
- children (centre inside the parent) are **translated** into crop
  coordinates and **clipped** to the crop; degenerate results are dropped;
- a parent with **no children is a negative example** (a crop with zero
  annotations; `DATALOADER.FILTER_EMPTY_ANNOTATIONS False` is set, as for
  verified-empty pages);
- crops **inherit their page's split**: all crops of a frozen test page
  go to `test.json`, the rest to `train.json`. With no frozen test set the
  legacy cocosplit re-roll runs over crops, so crops of one page may land
  on both sides — freeze a test set before scoped training.

`pages_to_coco` is unchanged: a crop page is just a page dict with its own
`imagePath`, `imageWidth/Height` and translated shapes. The generated
`train.sh` / fine-tune script point `--image_path_*` at
`/workspace/<project>/crops`, and the push step uploads
`intermediate/crops/*.jpg` **instead of** the page images.
`train_summary.json` records `scope`, `n_parents`, `n_crop_negatives`.

### Crop inference — `infer_layout.py --parents`

The inference tool (runs inside the GPU container) gains an optional
`--parents <dir> --parent-label X --margin M` mode. For each page image it
reads `<dir>/<stem>.json` — a slim per-page file holding only the parent
shapes, exported from the current annotations at push time
(`intermediate/parents/`, uploaded to `/workspace/<project>/parents/`; no
text layers leave the machine) — crops each parent with the same
`crop_rect` geometry used at training time, predicts on the crop, and
**maps boxes back to page coordinates** (`map_boxes_to_page`: add the crop
offset, clip to the parent box, drop degenerate boxes). One LabelMe per
page is written to `predictions/`, holding only predicted children, each
with its `score`. Pages without parents get an empty prediction file. The
geometry functions are pure (no detectron2 import at module level) and are
unit-tested on the laptop.

Both the infer and infer-from push steps regenerate the inference script
from (labels, scope, threshold) at run time, so the script always matches
the current scope; the yaml and weights are the model's own.

### Cross-project labels: the subset rule

The parent detector naturally lives in its own project with a single label
(`firm_area`, `table_region`, …) while the target project holds that label
*plus* the part labels. Infer-from therefore checks that **every source
label exists in the target palette** (`scope.missing_labels`), not that the
two sets are identical; the source's label order defines the model's class
ids and is passed unchanged to the inference script. The SSE log names the
labels the model predicts and the target labels it leaves alone. The gate
runs before the GPU-server gate, so a bad pairing is a plain 400. The
reverse (a richer source onto a poorer target) is refused with the missing
labels named.

**Fine-tune-from stays strict** (same label set): the warm start copies the
source's class head, whose rows are the source's class ids. With a
different NUM_CLASSES detectron2 silently drops the mismatched head and
re-initialises it, so the "fine-tune" would degrade to a backbone-only warm
start without anyone noticing. (Known soft spot, unchanged: the check is on
the *set*, so a same-set, different-*order* pairing warm-starts with
permuted class rows and relearns them — a strict order check is a candidate
tightening once no live workflow depends on reordering.)

### Merge-apply — `POST /api/project/{name}/apply-predictions-merge`

The existing *apply* fills only empty pages. Once pages carry shapes a
**merge** is needed. The endpoint has two modes, chosen by the scope; both
replace only *predicted* shapes (ones carrying a `score`), never touch
unscored (human) shapes or skip pages, write a page only when something
changes, and return `applied` / `skipped_unchanged` / `skipped_clutter` /
`inserted` / `removed` counts.

**Page scope + `labels`** (`app/scope.py::merge_labels`,
`merge_apply_labels`): the predictions carry a label set — typically the
labels of a source model run via infer-from (the parent detector's single
label). Per page with a prediction file: remove earlier predicted shapes
whose label is in the set, insert the new predictions of those labels (the
new shapes take the place of the first removed one, so page order stays
stable for the editor), leave every other label alone. `changed` compares
shape multisets, so re-running with the same predictions writes nothing —
idempotent in the strict sense. `labels` is required and must exist in the
project (400 otherwise); without it the endpoint points at *Apply to empty
pages* or a `within:` scope.

**Nested scope** (`within:<parent>`, `app/scope.py::merge_children`). Per
page with a prediction file:

1. skip `skip` (clutter) pages and pages with **no parent shapes**;
2. **remove** earlier *predicted* children: shapes with a `score`, a child
   label, and centre inside a parent — the scoped model owns parent
   interiors, and only what a model wrote;
3. **never touch** shapes without a score (human work), parents, or scored
   shapes outside every parent (another model's output);
4. **insert** the new predicted children (page coordinates, with score);
5. write only when something changed; report `applied`,
   `skipped_unchanged`, `skipped_no_parents`, `skipped_clutter`,
   `inserted`, `removed`.

Re-running merge-apply is idempotent in effect (the same children are
removed and re-inserted; the page is rewritten with identical content).
Human corrections survive any number of re-runs in both modes because only
scored shapes are replaced.

### UI

Dashboard, *Train & Infer* card: a **Detection scope** selector
(`whole page` | `within: <label>` for every project label) bound to
`config.detection_scope` (PATCH on change). Its value rides along in the
prepare/train, infer, infer-from and fine-tune-from requests, and a
**⤵ Merge into parents** button (shown only when a scope is set) calls
merge-apply. After a whole-page **Infer from** run the dashboard remembers
the source and its labels for the target project (localStorage) and shows
**⤵ Merge model labels**; its confirm lists the labels that will be
written, and *Apply to empty pages* points at it when pages were skipped
for already having shapes. The training-ledger table shows a `⊂label`
badge on scoped rows.

### Workflow instance (Compass)

1. Target project labels: `firm_area`, `firm_name`, `address`, `capital`,
   `balance_table`, …; annotate ~60 pages fully.
2. Parent model: a separate one-label project (`firm_area`, scope `page`),
   trained on its own pages. **Infer from** it onto the target (its label
   is a subset of the target's — no cloned label set needed), Pull, then
   *Apply to empty pages* for untouched pages and **⤵ Merge model labels**
   for pages that already carry parts or earlier parents.
3. Switch the target's scope to `within:firm_area`, Train (crops are
   exported automatically), Run inference, Pull, **Merge into parents**.
4. Correct in the editor; fine-tune-from (scope rides along) feeds
   corrections back. For cells inside tables, repeat with
   `within:balance_table` — same mechanism, different instance.

### Open items / next slices

- Scopes with **several parent labels** (`within:a|b`) — trivial
  extension of `parse_scope`; not needed yet.
- A parent-level **upscale** option (resize small crops to a fixed short
  side before prediction) for very small parents — detectron2's resize
  does most of this already; measure first.
- Containment by **area fraction** as an alternative to the centre rule
  for children that straddle two parents (rare in practice).
- A real GPU round trip (train `within:` on a smoke project) has not been
  run yet; the push paths are covered by code review and the pure
  functions by tests.

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
