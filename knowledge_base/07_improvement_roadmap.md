# Improvement roadmap

Goal metric: **human minutes per 1,000 verified data cells**, and **days from raw PDF to analysis-ready dataset**. Plans are ordered by expected return on that metric. Each is scoped to be buildable incrementally inside the current architecture (no rewrite required unless stated).

---

## P1. Review Queue — triage-driven human attention *(highest leverage)*

**Problem** (critique A1, B3, B5): humans spend most time finding suspect cells, not fixing them.

**Plan**
1. Server endpoint `GET /api/review/queue?folder=&filters…` that scores every cell/internal row across pages and returns a ranked worklist. Suspicion signals (all already in the data or cheap to add):
   - layer disagreement (OCR vs LLM vs PDF normalized mismatch),
   - rule violations (already computed),
   - authority score < floor or unresolved in a resolved column,
   - empty Human on a page marked "needs verification",
   - numeric outliers within a column (z-score on parsed values).
2. Editor "Review mode 2.0": a queue panel; **Enter = accept best guess into Human, Esc/↓ = skip, typing = correct**; auto-advances and auto-navigates pages; shows the cell crop + neighbors for context. Target: 1–2 seconds per confirmed cell.
3. Queue filters: by signal type, column, page range — so a session can be "verify all name-column cells with authority score < 85".
4. Log accept/fix/skip events (per session) → this becomes the QA + progress data for P3/P4.

**Effort**: ~2–4 sessions. **Payoff**: order-of-magnitude on verification throughput.

## P2. Dataset spec + tidy export — define the *product*, not the layout

**Problem** (A3): export mimics the page; analysis needs a tidy table; the export dialog's option jungle keeps growing.

**Plan**
1. Per-project `dataset.json` spec: for each (table, column) → variable name, type (int/str/entity), authority, unit; row-identity column(s); propagate-labels; page range.
2. One-click **Build dataset**: emits long-format CSV/Parquet — one row per (source_page, table, row_entity_id, variable) with `value`, `layer` (which layer supplied it), `authority_id/score`, `clip_id` — plus a wide pivot for convenience and an auto-generated codebook (variable ↔ column header crop image!).
3. Excel export stays for eyeballing; the tidy build becomes the deliverable Stata reads. Column-mapping UI can be seeded from header-row OCR via one LLM call ("map these headers to variables").
4. Re-running the build is idempotent → the dataset becomes reproducible from annotations at any time (version the spec in git).

**Effort**: ~3–5 sessions. **Payoff**: removes the whole Excel→Stata re-parsing stage; makes exports self-documenting.

## P3. Page status & progress board

**Problem** (A2): no per-page state, no project progress, no RA coordination.

**Plan**
1. Page flag `status`: `predicted → corrected → verified` (+ `problem` with a note), set manually (hotkey) or automatically (all queue items on the page cleared → verified).
2. Dashboard per project: progress bars by status, per-batch; click → jump to next unfinished page. Editor hotkey "next page needing work".
3. Optional `assignee` flag for RA division of labor (works even over Dropbox since it's per-page).
4. Export refuses (or warns) when unverified pages are included — an explicit quality gate.

**Effort**: ~1–2 sessions. Do together with P1 (shared signals).

## P4. Measured quality — sampled audit + agreement stats

**Problem** (A2, D3): no residual-error estimate; referees will ask.

**Plan**
1. "Audit sample" mode: random sample of already-verified cells re-presented blind (crop only, no layers); compare to stored Human → estimated error rate with CI, per column/project.
2. Layer-agreement dashboard: % cells where OCR==LLM, where Human overrode LLM, per column — identifies columns where automation is trustworthy enough to skip review entirely (huge time saver: *stop reviewing what's already accurate*).
3. Store per-cell provenance of who verified (RA initials from a local setting) for D3-style double-entry on designated critical columns.

**Effort**: ~2 sessions, mostly reuses P1 UI.

## P5. Faster automation: batch/parallel LLM + whole-page vision benchmark

**Problem** (A6): serial, uncached, full-price LLM usage; multi-stage pipeline never benchmarked against one-shot vision extraction.

**Plan**
1. Concurrency (asyncio + semaphore, e.g. 8-way) in Batch LLM; response cache keyed on (image hash, prompt hash, model) so re-runs are free.
2. OpenAI **Batch API** path for overnight jobs (50% cost) — fits the workflow: queue in the evening, review queue in the morning.
3. Benchmark harness: for N gold pages, compare (a) current pipeline, (b) vision model whole-page → structured JSON (Type B) or → per-cell values keyed by lattice coordinates (Type A), on accuracy and cost. If (b) is close, use it to *pre-fill* layers and let P1's queue absorb the errors.
4. Promote the "magic wand" from experimental using the benchmark results — possibly replacing GPU layout training for new projects with few pages (where annotating 30 pages + training costs more than vision-LLM inference on 300).

**Effort**: 1 session for concurrency+cache; 2–3 for benchmark. **Payoff**: cost ↓, wall-clock ↓, and possibly deleting a whole pipeline stage for small projects.

## P6. Authority: unresolved worklist + alias learning

**Problem** (D1): silent skips; same fix repeated; authority files never learn.

**Plan** (items 1–3 **DONE 2026-07-05** — see 05_subsystems.md for the endpoints/UI)
1. ~~"Unresolved" report~~: worklist modal in the ⚙ Batch resolve panel — distinct unresolved strings by frequency with crops + candidates; Apply resolves all occurrences.
2. ~~Alias promotion~~: alias-suggestions modal → `econai_confirmed` aliases appended to the git-tracked authority file.
3. ~~LLM disambiguation + canvas tint~~: 🤖 per-string LLM pick (crop + candidates); green/amber corner dots on resolved/partially-resolved cells.
4. Build the **1933/1935 place slice** (`helysegnevtar_1933` project feeds it) and the **HS-heading authority** for product codes — both unblock the actual research datasets. **(Remaining — mostly data work.)**

## P7. Engineering hardening (enables everything above)

**Problem** (C1–C4, B4): monoliths, no tests, stale cache, unsafe writes, secrets/clutter.

**Plan — deliberately minimal, no rewrite:**
1. **Cache-busting now**: serve `index.html` with `Cache-Control: no-cache` and/or hashed static URLs; delete the build-marker ritual. (30 minutes; has burned hours.)
2. **Atomic saves**: write temp file + `os.replace`, add a server-side per-file lock; removes the Dropbox corruption window and second-tab races.
3. **Committed test suite**: pytest over the export/matcher/rows-whitelist logic using fixture pages (they already exist ad hoc); a smoke test booting the app. Run pre-commit.
4. Split `index.html` into `index.html` + a handful of JS modules (editor core, panel, batch, authority, structured) — mechanical, no framework; do it *before* P1 adds more code.
5. Housekeeping: `.gitignore` for `server*.log`, `*.pth`, stray images; move secrets to a git-ignored `secrets.json` or env vars; pin `requirements.txt`.

**Effort**: ~2–3 sessions spread out; items 1, 2, 5 immediately.

## P8. UX polish backlog (cheap, cumulative)

- Tooltips with names + shortcuts on every emoji button; a `?` overlay listing shortcuts; a Ctrl+K command palette (fuzzy list of all actions — solves discoverability outright).
- Saved **batch presets** ("recipes") in the ⚙ modal; one click re-runs "OCR + LLM + resolve places col 2, odd pages".
- Batch **dry-run** ("would change 412 cells on 37 pages") + one-shot batch undo (snapshot the affected JSONs to a `.undo/` folder before running).
- Right panel: pin frequently used groups, collapse state persisted (partially exists), "focus Human field" hotkey.
- Border shading by *agreement* not just presence (green only when layers agree or Human set).

---

## Suggested sequencing

1. **P7.1/2/5** (cache, atomic writes, hygiene) — one short session, removes recurring pain.
2. **P1 Review Queue** + **P3 status flags** — the throughput transformation.
3. **P2 Dataset spec/tidy export** — the product transformation; retires export-dialog growth.
4. **P5 LLM speed/cost** and **P6 authority worklist** — in parallel with data entry actually happening.
5. **P4 audit** before the first paper-grade export; **P8** continuously in slack time.
