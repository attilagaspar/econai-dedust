# Critique — what is not right

An honest assessment, from big-picture to nitty-gritty. Ordered roughly by how much each costs in *human hours per clean data row* — the metric that matters.

## A. Big picture

**A1. The human is the scheduler.** The tool has powerful primitives (layers, rules, authorities, batch ops) but no notion of *what needs attention next*. The operator decides which page, which cell, which column to look at — by scanning visually. Most human time is spent *finding* problems, not fixing them. There is no ranked queue of "these 340 cells are suspect (layers disagree / rule fails / low authority score / OCR confidence low), press Enter to accept, type to fix." This is the single largest speed lever available.

**A2. No definition of "done", no progress accounting.** `pipeline.json` stages are vestigial — nothing enforces or tracks them, and there is no *per-page* status (unreviewed / needs-fix / verified). With multiple RAs and thousands of pages, "how far along is foldbirtok1935, and which pages can I trust?" has no answer inside the tool. QA is vibes-based: there is no sampled audit to estimate the residual error rate of a "finished" export, which a referee will eventually ask about.

**A3. The final data product is an Excel file that mimics the printed page.** Export reproduces layout (tiling patterns, interleaving, column filters) rather than producing an analysis-ready dataset. The layout sheet then gets re-parsed in Stata — undoing work the tool already did. The tool *knows* row entities (authority IDs), column meanings, page provenance, layer origin — and throws most of that away or scatters it across companion sheets keyed by position. The growing option-jungle of the export dialog (patterns, filters, interleave exceptions like the July 2026 non-lattice/column-filter fix) is a symptom: layout replication is the wrong target abstraction. What analysis needs is a *tidy long table*: one row per (page, table, row-entity, column-variable) with value, layer, confidence, resolved ID, plus a codebook.

**A4. Corrections don't feed back into the models.** Human-corrected layouts are exactly new training data, but retraining is a manual, occasional act, and there is no "prediction vs corrected" diff to measure whether retraining would pay. Similarly, accepted LLM fixes and human OCR corrections are a goldmine for few-shot prompts / fine-tuning, unused.

**A5. Single-machine, single-user, Dropbox-synced mutable JSONs.** Dropbox is the multi-user story; two people editing the same page = silent conflict files. There is no audit trail of who changed what when (layers partially capture this, but not per-edit). The write-race bug (~15% lost saves) was fixed client-side (`_serializeWrite`), but the server still does unlocked read-modify-write of whole files — a second browser tab can still lose data.

**A6. Underused modern-model path.** The pipeline's shape (Detectron2 → cell OCR → per-cell LLM) predates current vision-LLMs. The "magic wand" whole-page LLM layout detection exists but is experimental; nobody has benchmarked "GPT-5-class vision model reads the whole page into structured JSON directly" against the multi-stage pipeline on a real table page. Even if it doesn't win on dense tables, it likely wins on Type-B pages and could pre-fill everything cheaply. Per-cell LLM calls are serial-ish, uncached, and don't use the OpenAI Batch API (50% cost, overnight throughput).

## B. UX / editor

**B1. Discoverability.** Features hide behind unlabeled emoji buttons (🏛 ⚖ 🛠 🚩 ⊞ ✂ ⟳ 📋), modes, and modals. The README documents them, but in-app there are no tooltips-with-shortcuts, no command palette, no "what can I do with this selection?" affordance. New RA onboarding cost is high.

**B2. The Batch modal is a junk drawer.** ~8 operations × many filter fields in one modal. Correct per user instruction ("don't add new buttons"), but it now needs internal structure: presets/recipes ("resolve column 2 of all odd pages with places_hu") that can be saved, named, and re-run, instead of re-entering filters each time.

**B3. Click-heavy micro-corrections.** Fixing one cell = click box → find field in a long right panel → click into value → type → save. The right panel (Cell inspector) has grown into a tall stack of collapsible groups; frequently-used things (Human field, per-row table) share space with rarely-used ones. Keyboard coverage is good for boxes but weak for *content*: no "jump to next disagreeing row and focus its Human field" key.

**B4. Stale-cache whack-a-mole.** ~~Hard-reload + build-marker console checks~~ — **fixed 2026-07-05**: `/static` is served with `Cache-Control: no-cache`, so a plain reload always revalidates (cheap 304 when unchanged). Build markers can be retired.

**B5. Trust indicators are binary.** Border colors show *presence* of layers, not *agreement* or confidence. A page can look "all green" and still be wrong. Rule violations show per-line, which is great — but only rules that were manually authored; there is no generic anomaly shading (column type violations, outlier magnitudes, sum-check candidates auto-suggested from headers).

**B6. Undo is per-page and pre-save only**; no cross-page undo of a batch op. A misconfigured batch (wrong overwrite flag, wrong authority) can trample thousands of cells with no rollback other than Dropbox file history.

## C. Engineering

**C1. Two monoliths.** ~~`index.html` ~10k lines~~ — **split 2026-07-05** into an HTML skeleton + 9 ordered JS files + external CSS (identical behavior; verified live). `server.py` (~5.4k lines) remains one module. Tests now exist (see C2) but coverage is thin — the regression risk is reduced, not gone.

**C2. No CI, no pinned deps.** **Partly fixed 2026-07-05**: `requirements.txt` pinned (`~=` ranges), `tests/` added (17 pytest tests: boot smoke, shape CRUD, rows whitelist, Excel export incl. col-filter exemption, authority matcher on real places_hu). Still no CI runner / pre-commit hook — run `python -m pytest tests -q` before committing backend changes.

**C3. Secrets & clutter.** `config.json` holds SSH key paths per project and syncs via Dropbox; OpenAI/Azure keys entered in the UI end up in local config too. Repo root is accumulating debris (`server.log`, `server.err`, `server_test.log`, stray PNG/JPG, `model_final_census.pth` — a weight file! — all untracked but sitting there); `.gitignore` doesn't cover them.

**C4. Whole-file rewrite per save + Dropbox.** ~~Corruption window~~ — saves were already crash-atomic (`_write_json`: temp file + `os.replace` with Dropbox/antivirus retry, global write lock). The remaining gap was the read-modify-write race between concurrent *requests* (second tab, batch overlapping a click) — **fixed 2026-07-05** by a middleware serializing mutating `/api/page*` requests per (folder, stem). Long SSE runs stay unserialized by design. Sync churn on multi-MB JSONs remains.

**C5. Performance cliffs.** Authority index rebuilds per file mtime (fine), but pages with hundreds of shapes re-render the whole SVG overlay on each save; batch LLM has no concurrency control surfaced; Excel export loads everything in memory.

## D. Data / methodology

**D1. Authority coverage gaps are invisible.** Below-floor matches are skipped silently in batches; there is no worklist of "unresolved cells, grouped by frequency" (fix the string once, apply everywhere). Frequent OCR variants of the same settlement are re-resolved page by page instead of learned ("memory" of accepted corrections / alias promotion back into the authority file).

**D2. The 1935 problem is still open.** Trianon redrew boundaries; GIStA stops at 1910. The 1933 Helységnévtár slice (project exists: `helysegnevtar_1933`) is the missing spine layer for the flagship Techxtremism datasets — arguably higher value than more editor features.

**D3. No inter-annotator agreement / double-entry option** for critical columns, the standard historical-data QA practice.

**D4. LLM cleaning is unversioned.** `structured.model` and `ts` are stored, but prompts aren't versioned; you cannot reconstruct which prompt produced which layer values across a project.
