# The dataset layer: from "pages of cells" to a declared dataset

*Plan drafted 2026-07-24 in the debug session, from Attila's request. Status:
Phase 1 (declaration + builder + structure/parse checks + findings report)
shipped 2026-09. Phase 2 (diagnostics + the P11 evaluation dashboard)
BUILT 2026-10-07 — see "Phase 2 specification" and "Phase 2 as built" below.*

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
    // Cross-slot row matching is a property of the DECLARATION, not the
    // engine (clarified 2026-08-15):
    //   "positional" (default) — same (cycle, lattice_row, internal_row_n)
    //                = same record; for books that print one logical table
    //                across multiple pattern slots (foldbirtok1935 style)
    //   "keyed"      — match by the key column's value, for books where
    //                both slots repeat the identifier column
    // Single-slot datasets (probably most future projects) omit "join"
    // entirely — the question never arises.
    "join": "positional",
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

- **General infrastructure, not a foldbirtok1935 feature**: the declaration
  format is editor infrastructure like the authority mechanism —
  foldbirtok1935 is only the first declaration instance, the way `places_hu`
  is one instance of an authority. Nothing in the schema may assume the
  two-slot layout of that one book; join semantics live in the declaration
  precisely so the engine stays project-agnostic.
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
join slots per the declaration's join mode, parse by the declaration.
Returns records with full
**provenance** per value: `(stem, shape idx, row_i, layer_used)` — that is
what makes every diagnostic clickable.

`POST /api/dataset/<name>/diagnose` — check ladder, cheap to expensive:

1. **Structure**: pages whose slot has missing/extra columns vs the
   declaration; join mismatches — differing row counts across slots under
   a positional join, key disagreement under a keyed join — surfaced as
   **loud findings, never a silent wrong join**; duplicate/missing keys.
2. **Parse**: values that fail their dtype (non-numeric in a numeric
   variable) — in practice a large share of true errors live here.
3. **Hard constraints**: min/max violations, entity column unresolved.
4. **Printed totals** (moved up from "later", 2026-10-07): sums of records
   against the book's own printed total rows/columns. Exact, not
   probabilistic — see Phase 2 specification, check A.
5. **Distribution** (per variable, robust) — fully specified in the Phase 2
   specification below (checks B–F). **Flag, never auto-fix**: agrarian data
   has true heavy tails (Budapest exists).
6. Later: other dataset-level rules (cross-variable identities).

## The UI

Reuse the **report chassis** (duplicate/unresolved/lookup reports): a
"Dataset diagnostics" report grouped by check → variable, each finding one
row with crop, layers, editable Human, click-to-jump, minimizable pill.
Findings are fixed in place; re-run to converge. No new interaction concepts.

Declaration authoring: **the editor's 📋 Dataset window** (built 2026-10-08,
`app/static/js/dataset_decl.js`; toolbar button next to ⚖ Rules). A side
panel, so the page stays visible:

1. *Which pages* — page cycle + which positions belong (the pattern), with
   the slot map shown in the builder's own page order
   (`POST /api/dataset/page-map`) and "this page is slot k"; page range;
   which cell labels are read; advanced: record unit, join mode, tables.
2. *Columns → variables* — "⬇ Read columns from this page" lists every
   lattice column of the current page as a variable of its slot (do it once
   per slot), with sample values; name / type / printed header / min / max /
   stats; a radio marks the key (the first text column is pre-selected as
   an entity key). Hovering a row highlights that column's cells on the page.
   No header cells are annotated in foldbirtok1935, so names are typed, not
   read.
3. *Rows that are not records* — exclude patterns (+ one-click Hungarian
   statistics defaults incl. the "… j." district rows) and the printed-total
   pattern.
4. *Sums the book guarantees* — declared identities as chips; "🔍 Find sums
   in the data" (`POST /api/dataset/suggest-identities`: runs of consecutive
   same-type columns, ≥80% hold, top-2 disjoint runs per total, end columns
   that are almost always a dash rejected — on foldbirtok it rediscovers
   every hand-found identity and adds area_total = cultivation branches);
   "⇩ Import from ⚖ Rules" maps column rules (1+2=4) onto named variables.
5. *How numbers are printed* — thousands separators, decimal mark incl.
   "none", no-value marks.

▶ Test builds the dataset without saving (`POST /api/dataset/preview`:
records, layout problems with page links, read ✓/✗ per variable with
examples, identity hold rates, printed-total anchoring). 💾 Save validates
server-side and keeps the previous version as `<name>.dataset.json.prev`;
🗑 renames to `.deleted-<ts>`. Unknown fields of a loaded declaration are
preserved. Verified 2026-10-08: built from scratch in the window,
foldbirtok_main reproduces the hand-written declaration (33 variables,
3,413 records). Finding from the test: with decimal mark "none" and "," as a
thousands separator, foldbirtok has 94 fewer unreadable values and 91 more
matching printed-total cells — left as Attila's call (only right if the book
prints no decimals).

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

## Phase 2 specification: diagnostics (2026-10-07)

*Source: a test proposal from Attila's research assistant, reviewed and
amended in session. What was adopted, what was corrected, and why.*

### Ground rules (apply to every check)

1. **The goal is routing, not removal.** A flag sends a value to a human
   who looks at the scan crop. Transcription errors get corrected in Human;
   genuine values get marked confirmed. Nothing is deleted or auto-fixed.
2. **"Confirmed genuine" is a persistent state per flag.** Stored outside
   the page JSONs (e.g. `datasets/<name>.confirmed.json`, keyed by
   variable + record key + value), survives rebuilds, and is invalidated
   automatically if the underlying value changes. Without it Budapest is
   re-flagged on every run and review never converges.
3. **Log scale by default for positive count/size variables.** Historical
   economic data is heavily right-skewed (farm sizes, machine counts,
   population — roughly lognormal / power-law). On raw values IQR, MAD and
   ESD flag every large town and drown the real errors. Use log1p; the
   declaration may override per variable (`"scale": "raw" | "log"`).
4. **Zero-inflated variables need a fallback.** When most records are 0
   (machinery in villages), MAD = 0 and the z-score is undefined. Rule: if
   the share of zeros exceeds a threshold (default 50%), run the
   distribution tests on the non-zero values only, and report the zero share
   itself as a fact on the dashboard.
5. **Every flag records its method and parameters** (test, transform,
   threshold, group) — visible in the UI and exported, so the appendix can
   state exactly what was checked.
6. **Effect sizes, not only p-values.** Many variables × many columns ×
   many volumes = multiple testing; some p < 0.05 by chance is guaranteed.
   Report the magnitude (e.g. "34% end in 1, expected ~11%") alongside any
   p, and either Bonferroni-adjust or rank by effect size.

### Checks, in build order

**A. Printed totals (exact).** Statistical yearbooks print row and column
sums. Recompute them from the transcribed values and compare. A mismatch
means *something in that row/column is definitely wrong* — the strongest
check available, stronger than any distributional test. Phase 1 already
identifies total rows (`record.exclude_keys`, so they are not counted as
records); Phase 2 turns them into a check: the declaration names which
key values are totals and what they sum over (e.g. district total = sum of
its settlements). Mismatch finding shows both numbers and the difference;
a difference that is a power of ten or a single-digit transposition is
labeled as such (likely OCR digit error).

**B. Extremes per variable: top/bottom N.** The 10 largest and 10 smallest
values per variable (N adjustable), each clickable to its crop. Trivial,
and it is what people actually look at first. Includes the existing
top-gap ratio (max / second-max) as a one-number summary.

**C. IQR fences, two tiers.** Tukey fences on the (log-scaled) values:
1.5× IQR = **mild**, 3× IQR = **extreme**. Two-tier severity maps directly
onto the dashboard's colors and lets the reviewer start with extremes.

**D. Robust z-score (MAD).** Modified z = 0.6745·(x − median)/MAD
(Iglewicz–Hoaglin), threshold 3.5, on the log scale, with the zero-inflation
fallback from rule 4. Partly redundant with C; kept because the two
disagree in useful ways on small or lumpy samples.

**E. Digit length vs. the column's typical value.** Flag values with ≥2
more digits than the variable's median digit count. Statistically this is
close to a log-scale outlier, but it is kept as its own named check because
it states the *cause* in readable terms: "3 digits longer than usual" =
two cells glued together / column shift ("összecsúszás"). Readable causes
make review faster than a bare z-score.

**F. Trailing-digit excess — column separators read as "1".** The most
valuable proposal, specific to these scans: vertical rule lines next to a
number get OCR'd as a trailing "1" (or "l", "|", sometimes "7"). Design:

- **Group by layout, not by variable.** The artifact comes from where a
  cell sits on the page. Test per physical column per page (and per
  volume), not per variable pooled over the dataset — pooling dilutes a
  local artifact. Dedust knows each cell's position, so this is natural.
- **Restrict to values with ≥3 digits** (last digits are only approximately
  uniform for larger numbers; small counts are not uniform) and **compare
  the share of 1s against the other non-zero digits** (rounded figures pile
  up on 0, which would distort a plain uniformity test). Chi-squared or a
  binomial test on "ends in 1 vs ends in 2–9", reported with the effect
  size.
- **Leading-digit variant** (separator on the left → spurious leading 1):
  the expected distribution there is Benford's (~30% leading 1s), NOT
  uniform. Lower priority; soft signal only.
- **Cell-level pinpointing — the Dedust-only part.** Combine the
  statistical signal with geometry: a value is a high-confidence candidate
  when (1) it ends in 1, (2) its box's edge touches or overlaps a detected
  vertical rule, and (3) dropping the trailing 1 moves it from outlier to
  normal range for its variable. When all three hold, the finding carries a
  **suggested correction** (the value without the trailing 1) that the
  reviewer accepts with one key — still a human decision, never automatic.
  No outside tool can do this; it needs the box geometry.

**G. Generalized ESD (Rosner) — optional, "appendix-grade".** Gives
p-values for "which values are significantly outlying". Assumes
approximate normality after removing outliers → run on the log scale only.
Needs an upper bound on the number of outliers per variable (default 5% of
n). Selectable as an alternative method, not the default. Framing note for
the paper: the defensible appendix sentence is not "values with p < 0.05
were removed" but "every flagged value was checked against the source
scan; transcription errors were corrected, genuine values were confirmed"
— the human adjudication is the evidence; the test only chose what to look
at. The dashboard's counts (flagged / corrected / confirmed per check)
are the appendix table.

**H. Histograms.** Per variable, log-scale histogram with flagged values
marked; shown in the dashboard (P11, built in the same push) rather than
in the findings list.

### Deferred to the entity index (P12) — need cross-project linkage

- **Year-to-year change outliers: Hidiroglou–Berthelot.** The
  official-statistics standard for period-to-period ratio editing; needs the
  same settlement linked across volumes/years (temporal authority + P12).
- **Ratio outliers against another source** (e.g. machines or land per
  capita using a census population variable from a different project), with
  the same IQR treatment and a histogram. Needs P12 to fetch the
  denominator.
- **k-NN distance / isolation forest** (a 2025 comparison found them best
  among non-parametric longitudinal methods): **held back**. Harder to
  explain to a referee, and a flag that says "anomalous in 12 dimensions"
  is much slower to review against a crop than "this ratio jumped 40×".
  Revisit only if HB demonstrably misses error classes.

## Phase 2 as built (2026-10-07)

**Code**: `app/diagnostics.py` (pure checks; no scipy — binomial tail and the
Student-t quantile for ESD are implemented in-module), wired in
`_ds_run_diagnostics` in server.py; `POST /api/dataset/<name>/diagnose`
returns the report groups plus a `quality` payload; `POST
/api/dataset/<name>/confirm` records "genuine" / "book is wrong". UI:
`app/static/quality.html` (opened from the dashboard's 📈 Data quality card;
summary tiles, run trend, adjudication table + CSV appendix export, exact-
check tables, per-variable histograms with fences and top/bottom 10 with
crops, trailing-1 page strips, findings browser with open / ✓ fix /
✓ genuine), and the editor report (`dataset_report.js`) gained the same
badges and buttons. The editor accepts `?stem=&idx=` deep links. Tests:
tests/test_diagnostics.py.

**Declaration additions** (all optional):

```jsonc
"identities": [ {"total": "n_total", "parts": ["n_1_5", "n_5_50", …],
                 "label": "number of holdings = sum of the size classes"} ],
"totals":     { "row_pattern": "osszesen" },   // rows must ALSO match exclude_keys
"diagnostics": { "report_mild": false, "trailing_min_digits": 2, … },  // DEFAULTS overrides
// per variable:
{ "name": "serial", …, "stats": false }          // no distribution tests
{ "name": "x", …, "scale": "raw", "esd": true }
```

**Side files** next to the declaration: `<name>.confirmed.json`
(confirmations, keyed by check|variable|stem|idx|row_i, valid only while the
cell text is unchanged) and `<name>.diag_history.json` (run history + the
flagged/corrected/confirmed/open bookkeeping; only full-dataset runs update
it). Both travel with `push-project` / `pull-project` (datasets/ is pushed).

**Design decisions taken during the build** (calibrated on foldbirtok1935,
3,413 + 3,464 records):

- **Exact checks name their culprit.** For a broken identity or printed
  total, every cell is tested for "one OCR slip away" (one substitution,
  insertion or deletion) from the value that would repair the arithmetic;
  when exactly one cell qualifies, the finding carries a ✓ fix. Ambiguous
  cases (e.g. 45 vs 41+5: three cells are each one digit off) get no
  culprit — never a guess. When the fix is "drop a trailing 1", the finding
  says "a column rule read as a trailing '1'" — the arithmetic-proven form
  of check F.
- **Printed totals use best-suffix anchoring, not header rows.** District
  header rows are often stored as flat separator cells and vanish from the
  record sequence, so header-based segmentation matched only 52 of 130
  total rows. Instead, for each total row the run of K records above it
  whose sums agree with the most variables is chosen; a total anchors only
  when ≥3 and ≥50% of its variables match exactly (a chance majority over
  10+ variables is practically impossible). Result: 108/130 anchored, and
  the disagreements dropped from 474 cells to 93 real ones.
- **The image-based "rule inside the box edge" probe was built, measured
  and removed**: it fired on ~40% of ALL cells, ends-in-1 or not — no
  evidence. Cell-level trailing-1 candidates are now listed only inside a
  page column that shows a statistically significant excess.
- **Last digits of ≥2-digit values are uniform** in this book (shares of
  1–9: 0.104–0.119), so the trailing-1 test includes 2-digit values (where
  "21 → 2" lives); default `trailing_min_digits` = 2. foldbirtok_main
  shows no page-column excess (overall 11.3% ending in 1) — the test is
  quiet when there is nothing, which is the point.
- **Mild-only IQR hits are counted, not listed** (`report_mild` to list
  them): heavy tails put ~2% of every variable past 1.5× IQR even on the
  log scale; listing them buried the real errors (1,329 → ~820 listed).
- **Dashes count as 0 in identities** (the books print "-" for zero); in
  these books zeros are almost always dashes, so `zero_share` is ~0% and
  the zero-inflation fallback rarely triggers here — it exists for sources
  that print 0.
- **First findings on real data**: decimal-comma misreads (`1,871` parsed
  as 1.871 instead of 1871 — caught by identities as "factor 1000" with the
  fix), district subtotal rows abbreviated "… j." entering as records
  (exclude pattern `\sj\.?\s*$` added), and the `serial2 = serial` identity
  as a direct detector of misaligned page pairs (47 / 64 mismatches).

## Phasing (each phase ships value alone)

1. **Declaration + builder + structure/parse checks** — the file format, the
   build endpoint, findings for structural mismatch and unparseable values,
   shown in the report chassis. (Biggest immediate value: catches the layout
   and OCR breakage that today surfaces as Stata outliers.)
2. **Diagnostics + evaluation dashboard (one build, decided 2026-10-07)** —
   checks A–G of the Phase 2 specification (printed totals first, then
   extremes, IQR/MAD on log scale, digit length, trailing-1 with geometry
   cross-check, optional ESD), the persistent "confirmed genuine" state, the
   review/fix loop in the findings report, AND the P11 dashboard on top
   (per-variable panels with histograms, mild/extreme counts, trailing-1 per
   column, build-over-build trend, adjudication table). Built together
   because the checks alone are a long findings list that doesn't show where
   problems concentrate, and the dashboard is mostly aggregation of numbers
   the checks produce anyway.
3. **Tidy export** — CSV/dta from the declaration; retire the renames.
4. **Later**: dataset-level rules; a declaration-authoring UI; optional
   materialized SQLite/parquet if analysis ever needs SQL directly.
   Cross-project joins by entity id have grown into their own roadmap entry:
   **P12 (entity index)** in 07_improvement_roadmap.md — records stay in
   their projects, the authority ID is the join key, the index is computed
   and rebuildable. The evaluation summary over builds is **P11** there.

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
