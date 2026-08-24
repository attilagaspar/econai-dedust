# The dataset layer: from "pages of cells" to a declared dataset

*Plan drafted 2026-07-24 in the debug session, from Attila's request. Status:
proposal — nothing implemented yet.*

## Motivation

Analysis keeps finding large outliers in exported data, and there is no way
to tell data errors from true values without going back to the scans one by
one. All existing quality machinery is **horizontal** (row rules like
`1+2=4` within a page) or **layer-wise** (OCR vs LLM vs Human disagreement).
Outlier detection is fundamentally **vertical / project-wise**: "the value in
the tractors column on page 31 is 10,000× the next largest value of that
variable anywhere" is only expressible once all pages' column-6 cells are
understood as *one variable of one dataset*.

Today the export is a spatial Excel dump; variable identity lives in a Stata
do-file as `rename W n_tractors` spaghetti, maintained by hand, broken by any
layout change (see the 2026-07-23 slot-alignment incident). The editor knows
everything needed to do better — it just has no place to write it down.

## Core idea

A per-project, human-authored **dataset declaration**: which lattice objects
constitute a dataset, how physical positions map to **named variables**, and
which column is the **record key**. Everything else — validation, outlier
reports, tidy export — is derived from that one file.

Three principles, debated and settled below:

1. **The page JSONs remain the single source of truth.** The dataset is a
   *view*, built on demand — not a second store that can drift. No live
   SQLite to keep in sync.
2. **Schema first, statistics second.** Most "outliers" in this data are
   mechanical (row misalignment, digit-glued-to-neighbor OCR, wrong column) —
   type/parse/range violations catch them before any distribution test.
3. **The value of doing this in the editor (not Stata) is the feedback
   loop**: outlier → click → see the crop → fix Human → the dataset heals.
   Stata can flag, but cannot show the scan or write the correction back.

## The declaration (`projects/<p>/datasets/<name>.dataset.json`)

```jsonc
{
  "name": "foldbirtok_main",
  "version": 1,

  // Which lattice objects belong. LATTICE ONLY by construction: selection is
  // via super_row/super_column + labels; free annotations and region shapes
  // (Compass) can never enter a dataset.
  "scope": {
    "labels": ["numerical_cell", "text_cell"],
    "pattern": "1,1,0,0",          // the page cycle; printed pages = slots 1..k
    "pages": "",                   // optional page-range restriction
    "tables": [0]
  },

  // What one record is and how multi-slot pages join.
  "record": {
    "unit": "internal_row",        // "internal_row" | "lattice_row"
    // records of slot 1 and slot 2 pages join positionally:
    // same (cycle, lattice_row, internal_row_n) = same record
    "key": { "slot": 1, "column": 2, "dtype": "entity",
             "authority": "places_hu" }
  },

  // Position → variable. This REPLACES the do-file renames.
  "variables": [
    { "name": "settlement",  "slot": 1, "column": 2,  "dtype": "entity" },
    { "name": "area_total",  "slot": 1, "column": 3,  "dtype": "number",
      "label": "Összes terület (kat. hold)", "min": 0 },
    { "name": "n_tractors",  "slot": 2, "column": 6,  "dtype": "int",
      "label": "Traktorok száma", "min": 0, "max_hint": 500 },
    ...
  ],

  // Parsing conventions, per dataset with per-variable overrides:
  "parse": {
    "missing": ["-", "—", "·", ""],     // dash conventions of the source
    "thousands": [" ", "."],            // 1 234 / 1.234
    "decimal": ","
  }
}
```

Notes:

- **Slots, not stems**: the mapping is per pattern-slot, so it survives page
  insertion/renumbering, and one declaration covers the whole book. Pages
  whose lattice disagrees with the declaration (missing/extra columns) are
  *findable* — that is itself the first diagnostic (would have caught the
  page_39 missing column and the page_29 stale row bands before export).
- **`dtype: "entity"`** ties a variable to authority resolution; the record
  key being an entity gives every record a stable ID (`places_hu` id) — this
  is what makes the dataset joinable across projects/years later
  (foldbirtok1935 × machines1935 × census by settlement id).
- Several datasets per project are allowed (multi-table books); a lattice
  object can only be claimed via scope filters, so datasets can coexist.

## The engine (server-side)

`GET /api/dataset/<name>/build` — assembles records from the page JSONs:
per cell take the best layer (Human > LLM > OCR), split by internal rows,
join slots positionally, parse by the declaration. Returns records with full
**provenance** per value: `(stem, shape idx, row_i, layer_used)` — that is
what makes every diagnostic clickable.

`POST /api/dataset/<name>/diagnose` — check ladder, cheap to expensive:

1. **Structure**: pages whose slot has missing/extra columns vs the
   declaration; rows that fail to join across slots; duplicate/missing keys.
2. **Parse**: values that fail their dtype (non-numeric in a numeric
   variable) — in practice a large share of true errors live here.
3. **Hard constraints**: min/max violations, entity column unresolved.
4. **Distribution** (per variable, robust): log-scale median/MAD z-scores,
   top-gap ratio (max / second-max — Attila's 10,000× case), share of zeros,
   Benford-ish leading-digit skew as a soft signal. Thresholds default,
   overridable per variable. **Flag, never auto-fix**: agrarian data has true
   heavy tails (Budapest exists).
5. Later: dataset-level rules (today's row rules generalized: cross-variable
   identities, cross-record sums against printed totals rows).

## The UI

Reuse the **report chassis** (duplicate/unresolved/lookup reports): a
"Dataset diagnostics" report grouped by check → variable, each finding one
row with crop, layers, editable Human, click-to-jump, minimizable pill.
Findings are fixed in place; re-run to converge. No new interaction concepts.

Declaration authoring: start with the JSON file edited by hand (Claude can
draft it from an existing do-file's renames — the mapping already exists in
Stata spaghetti and can be translated once). A small UI (column header row →
variable names) can come later; it is not on the critical path.

## The export

`dataset export`: one **tidy file** (CSV + optionally .dta) with variable
names as headers, the entity key as `<key>_id`/`<key>_name`, and provenance
columns (`page`, `lattice_row`, `row_n`). This *replaces* the column-letter
renames entirely — the do-file shrinks to `import delimited + labels`. The
spatial Excel export stays for visual checking; the tidy export is for
analysis.

## Compass safety (explicit non-goal guard)

The declaration's scope selects **only** shapes with lattice coordinates and
listed labels. Region shapes, free annotations, firm-record structures etc.
never qualify. Mixed projects: a dataset simply doesn't see the non-lattice
parts. Structured extraction (`shape.structured`) remains its own separate
path for non-tabular records; if a future project needs both, they coexist
without interaction.

## Phasing (each phase ships value alone)

1. **Declaration + builder + structure/parse checks** — the file format, the
   build endpoint, findings for structural mismatch and unparseable values,
   shown in the report chassis. (Biggest immediate value: catches the layout
   and OCR breakage that today surfaces as Stata outliers.)
2. **Distribution diagnostics** — robust outlier flags per variable, the
   10,000× case; review/fix loop in the report.
3. **Tidy export** — CSV/dta from the declaration; retire the renames.
4. **Later**: dataset-level rules; a declaration-authoring UI; cross-project
   joins by entity id; optional materialized SQLite/parquet if analysis ever
   needs SQL directly.

## Debated and rejected

- **A real database (SQLite as store)**: rejected as the primary form —
  two sources of truth, sync bugs, and the editor's whole model is
  file-per-page. Materialized *caches* are fine later; never authoritative.
- **Declaring datasets inside page flags**: rejected — the declaration is
  project-level, versioned, and diff-able; it belongs in its own file like
  schemas and rules.
- **Auto-inferring the schema from column headers**: useful as a *drafting*
  aid, rejected as the source of truth — header cells are OCR'd text on
  exactly the pages we distrust.
- **Doing outlier detection purely in Stata**: it already happens and is
  exactly the pain: no crops, no writeback, findings die in a log file.
