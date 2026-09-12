# Improvement roadmap

Goal metric: **human minutes per 1,000 verified data cells**, and **days from raw PDF to analysis-ready dataset**. Plans are ordered by expected return on that metric. Each is scoped to be buildable incrementally inside the current architecture (no rewrite required unless stated).

---

## STATUS: P1 + P3 built 2026-07-07

Review queue and page-status scoreboard shipped together. Editor: **⚡ Review** button → setup modal (signals: OCR≠LLM disagreement, numeric column outliers, unverified; page/column scope; skip-verified) → `POST /api/review/queue` returns a severity-ranked flat list of suspect units → a docked **review strip** walks them (Enter=accept best guess into Human, type to correct, ↓ skip, U undo, Esc quit; canvas navigates + highlights each item; row-band crop snippet). Page status in `flags.status` (predicted/corrected/verified/problem) via the status dropdown in the nav bar and the **V** hotkey (verify + jump to next unverified). Dashboard shows a per-project **Review progress** bar from `GET /api/project/status`. Structural-blank cells are excluded from the queue. Signals are JSON-only (no API). Known gaps: rule-violation and unresolved-authority signals not yet wired in; multi-line whole cells (no row_struct) flatten awkwardly in the single-line strip input (skip them). Tests in test_review.py.

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
1. ~~Concurrency + response cache~~ — **DONE 2026-07-06**: ⚙ Batch LLM runs N requests in flight ("Parallel requests" knob, default 6); identical requests answered from a local sqlite cache (`.llm_cache.sqlite`); LLM endpoints use per-shape merge writes so same-page parallelism is safe.
2. ~~Batch API overnight lane~~ — **DONE 2026-07-06**: op "🌙 LLM — overnight batch (half price)" in the ⚙ modal: server packages requests (incl. line-by-line row slicing) into a JSONL, submits via OpenAI/Azure Batch API, tracks jobs per project (`intermediate/llm_batch_jobs.json`), Apply writes results like the live path, Cancel supported. Azure needs a Global Batch deployment of the model.
3. Benchmark harness: for N gold pages, compare (a) current pipeline, (b) vision model whole-page → structured JSON (Type B) or → per-cell values keyed by lattice coordinates (Type A), on accuracy and cost. If (b) is close, use it to *pre-fill* layers and let P1's queue absorb the errors. **(Remaining.)**
4. Promote the "magic wand" from experimental using the benchmark results — possibly replacing GPU layout training for new projects with few pages (where annotating 30 pages + training costs more than vision-LLM inference on 300). **(Remaining.)**

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

## P8. UX polish backlog — **DONE 2026-07-06** (except panel-group pinning)

- ~~Ctrl+K command palette~~: searchable list of every visible button; `?` opens a shortcut cheatsheet; `H` focuses the Human field (flat textarea or first rows-table cell).
- ~~Batch presets~~: 💾 recipes in the ⚙ modal (all settings incl. label checkboxes, per project, localStorage).
- ~~Batch dry-run + undo~~: 👁 Preview counts affected cells without writing; every writing batch first snapshots the selected pages to a zip (`intermediate/batch_undo.zip`, one generation); ↩ Undo last batch restores them.
- ~~Agreement borders~~: amber border when OCR and LLM disagree and no human has looked (legend updated); presence colors otherwise unchanged.
- Remaining (minor): pin frequently-used panel groups to the top.

---

## P9. Minor build list (small items, grab one on a build day)

**HIGH PRIORITY (added 2026-09-10):**

0. ~~**Multi-lattice-per-page option in batch lattice correction.**~~ ✅ Built
   2026-09-10: `_latticeSegmentStacked` + `_latticeDetectMulti` in
   `lattice.js`, checkbox `batch-multi-lattice` in the shared
   `batch-ol-opts` panel (covers both batch modes), persisted in
   localStorage. Stacked tables only; splits at coverage gaps ≥
   max(3×median cell height, 60px) or at a non-selected annotation
   lying mostly inside a gap and x-overlapping the tables.
   Original spec: Both batch
   modes (`overlaps_lattice` and `overlaps_lattice_snap_trim` in `batch.js`)
   run `_latticeDetect(selectedLabels)` over ALL selected annotation types on
   the page — so when a page holds two (or more) tables, e.g. text_cell +
   numerical_cell + cell header from both, they are merged into one monster
   lattice. In reality separate tables are almost always divided by something:
   an annotation of a *different* (non-selected) type, or a significant band
   of empty space. Add a checkbox to each batch lattice-correction mode which,
   when checked, pre-segments the selected shapes into groups by those
   dividers — a non-selected annotation type lying between them, or a large
   gap — and runs lattice detection per group, producing two or more separate
   lattices. Implementation hook: `_latticeDetect` in `lattice.js` already
   accepts `opts.subset` + `opts.table` and shapes carry a `table` id, so the
   segmentation pass just needs to partition shapes and call it once per
   partition with distinct table ids.

*Training-loop items added 2026-09-08 from the compass_1874 fine-tuning session:*

1. **Include empty pages in training data (checkbox + flag).** Today empties are
   dropped twice: `cocosplit --having-annotations` in BOTH generated train
   scripts (Train and finetune-from), and Detectron2's
   `DATALOADER.FILTER_EMPTY_ANNOTATIONS` default (True). The naive fix is
   removing both, but that would turn every *not-yet-annotated* page into a
   poison negative ("this page contains nothing"). Safe design: empty pages
   enter the COCO export only when explicitly marked — either
   `flags.status == "verified"` with zero shapes, or a dedicated
   `empty_verified` flag — plus a dashboard checkbox to enable inclusion.
   "An empty page is an annotation, not an absence."
   *Update 2026-09-11: the inference half is covered by the EXISTING `skip`
   status — apply-predictions never populates skip pages (now
   regression-tested; UI relabeled "no annotations" so it's discoverable).
   Note for the training-export build: skip conflates "deliberately
   unannotated" with genuine clutter, and a skipped TABLE page must not
   train as a negative — so the verified-empty negative marker introduced
   here must be distinct from `skip`.*
2. **Self-fine-tune: allow source == target in finetune-from.** The dashboard
   guard (`Source and target project must be different`) exists because the
   training script wipes `outputs/<target>/*.pth` before training — with
   source == target it would delete the weights it is about to warm-start
   from. Fix: copy the source weights aside (e.g. `bootstrap_weights.pth`)
   before the checkpoint cleanup, point `MODEL.WEIGHTS` at the copy, then
   lift the JS guard. Until then the workaround is a full Train.
3. **Training export honors page status (contamination guard).** The COCO
   export takes every shape on every page — uncorrected predictions train as
   ground truth, indistinguishable from human work. P3 status flags already
   exist (`predicted / corrected / verified`): filter the training export to
   corrected+verified pages (with an override checkbox), and the
   active-learning loop can no longer poison itself when an unreviewed batch
   sits in the project at train time. Composes with item 1: a *verified*
   empty page is a negative example; a *predicted* empty page is nothing.
   *→ promoted to P10.2 (2026-09-11).*
4. **Persistent train/test split.** `cocosplit` re-rolls the 80/20 split every
   run, so eval metrics are not comparable across training cycles. Seed it
   per project (or store `test.json` once and reuse) so the loop's progress
   is measurable run over run. *→ promoted to P10.1 (2026-09-11).*
5. *(carried from P8)* Pin frequently-used panel groups to the top.
6. **Trash page in the dashboard (added 2026-09-12).** One view over both
   trash locations — whole projects in `projects/_trash`, per-project pages
   in `<project>/_trash_pages` — showing stem/file count/size/mtime, with
   Restore (move back; REFUSE if a live page with the same stem exists —
   never clobber live work) and Delete-forever (confirm states the bytes
   freed; per item + "empty this project's trash"). Kills the last reason
   to SSH for file management (root-owned container files made `rm` painful).
   Endpoints: list / restore / purge + collision-rule tests.

---

## P10. Learning diagnostics (added 2026-09-11)

*Background and the reasoning in plain language:
[11_learning_diagnostics.md](11_learning_diagnostics.md). Motivation: several
sources with tens of thousands of pages are coming; the "does more annotation
still help?" question must be a measurement, not a feeling. Items 1–2 absorb
P9 items 4 and 3 — they are the prerequisites for everything below.*

1. **Frozen test set (absorbs P9.4).** Per project: a hand-picked (or
   stratified-random) list of verified pages stored as
   `intermediate/test_stems.json`; the training export always EXCLUDES them
   from training data and always evaluates on exactly them. Dashboard UI:
   create/inspect the set ("freeze N verified pages"), warn when a frozen
   page's annotations change afterwards. Without this, no two training runs
   are comparable.
2. **Status-filtered training export (absorbs P9.3).** Only corrected +
   verified pages enter the training COCO (override checkbox). Also the
   verified-empty negative marker from P9.1 — distinct from `skip`, which
   must never train as a negative. This removes the most likely artificial
   ceiling (the model training on its own uncorrected output).
3. **Training ledger.** Every Train / finetune-from appends one row to a
   project-level `training_log.json`: timestamp, mode, training-page counts
   by status, config essentials (iterations, base weights), and — once item 4
   exists — the evaluation scores. Dashboard table, newest first. Turns every
   training into a data point instead of an anecdote.
4. **Auto-evaluation after training (corrections-per-page).** After training,
   run inference on the frozen test set and score it TWO ways: standard AP,
   and the human-cost metric — match predicted boxes to verified boxes (same
   label + IoU threshold), then count ADDED (missed by model), DELETED
   (invented), MOVED (matched but edges off beyond tolerance), per page and
   per label. Store in the ledger row. The added/deleted/moved split is the
   error-audit input (see the explainer's residue table).
5. **Learning-curve run.** A "train on subset" option (fraction + fixed seed,
   nested so 50% ⊂ 100%) so 2–3 runs produce the score-vs-quantity curve from
   ledger rows alone; the dashboard renders the curve when a project has
   ledger entries at multiple training sizes. GPU is exclusive-use, so this
   stays a deliberate sequence of runs, not a parallel batch.
6. **Correction telemetry (the real curve, for free).** When a page
   transitions predicted → corrected, snapshot-diff its shapes (the batch-undo
   zip machinery already snapshots pages) and log actual human
   corrections-per-page over time. This measures the true objective
   continuously on real work, without dedicating GPU time — the learning
   curve's cheap everyday complement.
7. **Error audit view.** Per-page visual diff of predicted vs corrected/
   verified (added = green, deleted = red, moved = amber) plus per-label and
   per-page aggregates. Turns "considerable manual post-processing" into
   numbers that point at one of: post-processing work, data cleaning, or
   targeted annotation.
8. **Transfer check.** Evaluate a chosen model on the frozen test set of a
   DIFFERENT project (new year / volume / source) with the same
   corrections-per-page report — the "annotate at scale or fine-tune first?"
   decision for every new source, measured on 10–20 pages before committing
   to thousands.

*Sequencing within P10: 1 + 2 first (small, and every later number is
meaningless without them); 3 + 4 next (the ledger makes each future training
a free data point); 5–8 as the sources arrive.*

---

## Suggested sequencing

1. **P7.1/2/5** (cache, atomic writes, hygiene) — one short session, removes recurring pain.
2. **P1 Review Queue** + **P3 status flags** — the throughput transformation.
3. **P2 Dataset spec/tidy export** — the product transformation; retires export-dialog growth.
4. **P5 LLM speed/cost** and **P6 authority worklist** — in parallel with data entry actually happening.
5. **P4 audit** before the first paper-grade export; **P8** continuously in slack time.
