# Subsystem deep dives

## 1. Authority / gazetteer resolver

**Goal**: resolve OCR'd strings to canonical entity IDs at annotation time, so sources join on stable IDs (no more fuzzy matching in Stata).

- **Files**: `authorities/places_hu.authority.json` (spine = GIStA `IDTel1910`), `authorities/industries_hu.authority.json` (spine = 1900 census code `I1900-<code>`). Builders live outside the repo (GIStA Access DB via pyodbc; industry CSV). Git-tracked.
- **Matcher** (`server.py`): lazy mtime-cached index; exact normalized match O(1), else rapidfuzz WRatio over accent-folded name+alias pools; parent context is a **soft boost** (`_AUTH_PARENT_BOOST=12`), never a hard filter; accept floor `_AUTH_MIN_ACCEPT=70`; tie-break prefers period name over modern/alias; `query_strip` tokens (e.g. `rtv`, `tjv`) are **authority-defined**, stripped before matching.
- **Ditto marks** (`"`, dashes, `do.`, `u.a.` …) inherit the entity from the row above; inheritance never crosses lattice-cell boundaries; a dash in a cell's top internal row is not a ditto.
- **UI**: 🏛 Authority group in the cell inspector — Source (authority file) picker, entity-type select, per-table county/district context; 🔎 Resolve, 📋 Resolve column; per-internal-row "Auth" column with batch header button, click-dropdown with live search (from 3rd char), double-click = inherit from above.
- **Scoping**: per-column authority (`flags.column_authority`) overrides per-page default (`flags.authority_file`).
- **Batch**: `resolve_authority` op in the ⚙ Batch modal → `POST /api/authority/batch` (in-process, fast), with page pattern, column filter, overwrite policy (human picks always kept), context usage.
- **Export**: Excel companion sheet **Resolved** (original + resolved_name + resolved_id + type/score/source/coords).
- **Worklist (built 2026-07-05)**: "📋 Unresolved worklist…" in the ⚙ Batch resolve panel → `POST /api/authority/worklist` groups every resolvable-but-unresolved string (same page/column scope as the batch op) by folded form, sorted by frequency, with sample crops + prefilled candidates; picking an entity and hitting Apply (`POST /api/authority/apply_string`) resolves ALL occurrences at once (written source=human, so batches keep it). A 🤖 button per string sends the cell crop + candidates to the LLM (`POST /api/authority/llm_pick`) to disambiguate near-ties.
- **Alias learning (built 2026-07-05)**: "➕ Alias suggestions…" → `POST /api/authority/alias_candidates` lists human-confirmed strings the authority file doesn't know yet; `POST /api/authority/promote_aliases` appends them as `{"source": "econai_confirmed"}` aliases to the git-tracked file (review the diff before committing). The matcher picks them up automatically (mtime cache).
- **Canvas badges (built 2026-07-05)**: each shape gets a corner dot — green = fully resolved (shape or all text-bearing rows), amber = partially resolved rows (`_authBadgeState` in authority.js, drawn in `drawOverlay`).
- **Planned**: 1933/1935 administrative overlay as new temporal slices on the same IDs; HS-heading authority for products; firm authority.

## 2. Structured (JSON) extraction

**Goal**: Type-B documents — one annotation = one record (e.g. a firm in the 1900 Paris Exhibition catalog).

- Per-project JSON Schemas in `projects/<p>/schemas/*.json` (CRUD via `/api/schemas`); strict-mode-friendly (all props required, `additionalProperties:false`, optionals nullable).
- LLM JSON toggle in the LLM panel and in Batch LLM; `_llm_complete_json` degrades strict→non-strict→json_object for local backends.
- Result in `shape.structured`; human edits via a live-validated text editor (JSON validity + schema conformance + tree view); `edited:true` protects `data` from re-runs.
- **JSON export** batch op: reading-order records; per-label modes export / ignore / **propagate forward** (title-type annotations like country headers become a field on subsequent records); single file or zip.
- Paris catalog schema captures serial_number, name, kind, address, products[] with `original`/`english`/`hs4_code`/`hs4_label` (HS 4-digit chosen over CPV/CN/PRODCOM), exhibit_location, awards[], notes. LLM-assigned HS codes are approximate → needs an HS authority + resolve step.
- **Deferred**: schema-driven form UI (instead of raw JSON text), multiple records per annotation.

## 2b. Row structure vs content (the separation, built 2026-07-08)

Row **geometry** and **content** are separate concerns, decided in separate steps:
- **Structure** — the ⚙ Batch op **"⊞ Build internal row structure (detect / anchor-project)"** → `POST /api/rows/build` (free, local, no API). Row count comes from the image (`source=image`, auto-detect) or a content layer's line count (`best`/`human`/`llm`/`ocr`/`pdf` → `_split_into_n_rows`). Blank `anchor_pattern` = detect every cell independently; a cyclic pattern of anchor `super_column`s (e.g. `8,,2,1`, empty slot skips the page) detects the anchor cell's rows once per lattice row and **projects** them onto the row's other columns (`_project_abs_bands`). `overwrite` gates cells that already have a structure; structural blanks are skipped. This is the ONE place row bands are set.
- **Content** — OCR/LLM with **Scope = "Internal rows (keep structure)"** reads into those bands (paid; overnight-able).

This **replaced the old ⚓ Anchored OCR / ⚓ Anchored LLM batch ops**, which conflated structure + content in one paid pass. The single-cell interactive "Anchored" scope in the LLM panel still uses the `/api/page/shape/{ocr,llm}/anchored` endpoints (unchanged). `_set_row_struct` redistributes each flat layer into rows when line counts match; `_sync_flat_from_rows` only writes a flat layer back when a row has text, so a mismatched rebuild never destroys flat human text.

## 3. Row rules & rule fix

- Rules: arithmetic constraints between lattice columns (`1+2=4`), per-rule zero-characters and page pattern; evaluated **per internal row** on the best layer; violations shade the exact line red; "all rules" union.
- 🛠 Fix rule: lists violating lines with per-line image snippets; ▶ Ask LLM proposes corrections (chunk size configurable, 1 = per line), showing diffs + whether the proposal satisfies the rule (✓/⚠); per-line checkboxes; Apply writes to the **LLM layer** flagged `llm_fixed` (green highlight for changed values); the proposal field is editable — an edited value routes to the **Human layer**; H badge marks already-human rows. Writes are serialized and gated on save success.

## 4. Batch operations & export filters

Shared filter vocabulary across Batch modal, authority batch and Excel export: ordered stems, cyclic `1/0` page pattern, 1-indexed column ranges ("2-4,7"), conditions (has row_struct, has clip). Excel tiling: page pattern controls stacking vs side-by-side (rank-aligned on printed lattice rows). Free (non-lattice) annotations are interleaved as own rows and (since 2026-07) must bypass the column filter.

## 5. Clips

Numbered colored flags marking that annotations on different pages belong to one data unit (record split across a page break). Tray with dangling flags; export as a column; can filter export to clipped rows.
