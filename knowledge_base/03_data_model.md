# Data model

Everything lives in one LabelMe-style JSON per page (`projects/<p>/annotations/<batch>/<stem>.json`) next to the page image. The server injects `_idx` per shape when serving. Below, the fields that matter.

## Shape (one annotation box)

```jsonc
{
  "label": "table",                  // project-defined label
  "points": [[x1,y1],[x2,y2]],       // rectangle
  "super_row": 3, "super_col": 2,    // lattice coordinates (null/absent = free annotation)
  "table": 0,                        // which lattice on the page (multi-table support)
  "pdf_text": "...",                 // PDF text layer clip
  "ocr_output": {...},               // OCR result (flat text)
  "openai_output": {"response": "..."}, // LLM-cleaned flat text
  "human_correction": "...",         // flat Human layer
  "row_struct": {...},               // internal row structure, see below
  "authority": {...},                // whole-cell entity resolution, see below
  "structured": {...},               // schema-conforming JSON record, see below
  "clip": {"id": 4},                 // cross-page data-unit flag
  "rule_flags": {...},               // rule-fix bookkeeping (llm_fixed etc.)
  "continuation": true,              // record chaining: continues the previous box of
                                     //   the record label in reading order (§H5)
  "group_id": 17,                    // record id, numbered through the volume by the sweep
  "group_locked": true,              // hand-assigned record id — the sweep keeps it
  "crop_origin": {"project": "x_crops", "stem": "p12__crop3"},
                                     // written back from that crop page (crop round
                                     //   trip); a repeat write-back of the same crop
                                     //   replaces exactly the shapes with this origin
  "derived_from": {"kind": "record_roles", "bbox": [x1,y1,x2,y2],
                   "continuation": false, "start_label": "name",
                   "start_idx": 4, "column": 0}
                                     // a record-label fragment built from start / stop
                                     //   roles (app/fragments.py); a re-run replaces it
                                     //   only while bbox == points, the continuation
                                     //   mark is unchanged, it is not locked and holds
                                     //   no content — otherwise it counts as hand work
}
```

Crop round trip (`app/crops.py`): a crop page (an image page of another
project cut from one box of this page) carries the provenance in its page
flags: `flags.crop_source = {project, stem, shape_idx, bbox, crop_rect,
label, margin, source_status, group_id, continuation, ts[, refreshed]}`.
The box is identified by label + bbox (the index is a hint that may
shift); a moved or vanished box makes the provenance stale. `crop_origin`
on a source-page shape is the inverse pointer; both fields live on the
shape / page, so the rows PATCH whitelist does not touch them.

Record chaining (`app/records.py`): a project's `config.record_label` names
the label records are built from; `layout_columns` (`auto` | 1 | 2) steers the
per-page reading order; `record_roles` (`{label: start | stop | ignore}`,
`app/fragments.py`) names the small objects the record-label boxes are
derived from — a label without a role is neither, the record label itself
carries none. The three fields above live on the shape (not on
rows), so the rows PATCH whitelist does not touch them. The derived registry
`intermediate/records.json` (`{meta, records: {id: {fragments: [{stem, idx,
bbox}], page_span, first_stem, n_pages, orphan, name}}}`) is rebuilt by every
sweep and is never the source of truth.

Content layer priority everywhere: **Human > LLM > OCR > PDF** ("best text").

## Internal row structure (`row_struct`)

Created by line-by-line / anchored OCR or LLM runs, or ⊞ Convert. One structure per cell, shared by all layers.

```jsonc
{
  "rows": [
    { "y0": 0.12, "y1": 0.19,        // band, relative to cell height
      "pdf": "...", "ocr": "...", "llm": "...", "human": "...",
      "authority": {...},            // per-row entity resolution
      "llm_fixed": true }            // set when rule-fix wrote the llm value
  ]
}
```

Server-side `PATCH /api/page/shape/rows` rebuilds rows keeping only whitelisted fields — **new per-row fields must be whitelisted** or they are wiped on the next row edit.

## Lattice

The printed grid: shapes get `super_row`/`super_col`/`table`. Detected from box geometry ("Lattice" button), editable (col/row separators, snap, split ✂, delete ✕). Multi-table pages supported. Excel export's spatial layout is driven by these coordinates; free annotations (no lattice coords) are interleaved as their own rows.

## Authority resolution (`shape.authority` / `row.authority`)

```jsonc
{ "id": "M22...", "name": "Szombathely", "type": "settlement",
  "parent": "...", "county_name": "Vas", "district_name": null,
  "lat": 47.2, "lon": 16.6, "score": 100, "via": "name",
  "source": "places_hu", "ts": "..." }
```

Authority *files* (`authorities/*.authority.json`): `{authority, version, entity_types, query_strip[], entities:[{id, name, aliases[], xref, attrs, slices:[{as_of, source, type, parent, name, attrs}]}]}`. Temporal facts (type/parent/name) live in per-source-year `slices`; timeless facts (coords, modern name) at top level. Current authorities: `places_hu` (GIStA 1910: 64 counties / 439 districts / 12,542 settlements / ~19k aliases), `industries_hu` (1900 census, 159 industries).

Page flags steering resolution:
- `flags.authority_file` — page-default authority
- `flags.column_authority["table:super_col"]` — per-column override
- `flags.authority_context[tableId]` — county/district context (a soft ranking boost, not a hard filter; accept floor `_AUTH_MIN_ACCEPT=70`)

## Structured extraction (`shape.structured`)

```jsonc
{ "schema_name": "product_catalog", "llm": {...}, "data": {...},
  "edited": true, "model": "gpt-...", "ts": "..." }
```
`data` is the human-authoritative copy; kept across LLM re-runs when `edited`. Schemas in `projects/<p>/schemas/*.json`.

## Page-level flags

`flags` on the page JSON: authority settings (above), plus misc. Rules and clips are stored per project / per page respectively.

## Exports

- **Excel**: layout sheet (one Excel row per internal row; page-pattern tiling; column filter; free annotations interleaved), companion **Resolved** sheet (original text + resolved name + id + score...), **Structured** sheet (dotted-key flattened records).
- **JSON export**: ordered records from structured annotations; per-label modes export / ignore / **propagate** (e.g. a country header carried into all subsequent records); single array file or zip of per-record files.
