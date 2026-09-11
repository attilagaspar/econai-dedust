# 11. Learning diagnostics — knowing where the next hour is best spent

*Written 2026-09-11, prompted by the Központi Értesítő question: "200 annotated
pages and the model still needs considerable manual post-processing — how do I
know whether more training data will help?" With tens of thousands of pages
ahead across several sources, guessing wrong about this is expensive in months,
not hours.*

Everything in this chapter answers one question:

> **Is my next hour better spent (a) annotating more pages, (b) fixing the
> annotations I already have, (c) annotating specific unusual pages, or
> (d) improving the automatic post-processing?**

A "learning diagnostic" is any measurement that answers this *before* the hour
is spent. None of it requires new theory — it requires three ingredients and
the discipline to keep them.

---

## The three ingredients

### 1. A frozen test set

A set of pages — hand-verified, correct beyond doubt — that the model is
**never allowed to train on**, and that **never changes**. After every
training, the model is scored on these same pages.

Why frozen: if two models are scored on different pages, you are comparing two
students who took two different exams — the difference could be the students
or the exams, and you cannot tell which. Today dedust re-rolls the 80/20
train/test split on every training run, so scores from different runs are
exactly this kind of incomparable. That is the first thing to fix.

Why the model must never see them: a model scored on pages it trained on gets
a flattering, meaningless number — it is being asked to recall, not to
generalize.

How many: for a layout as uniform as the KE, 30–50 pages give stable numbers.
A source whose layout varies (the Compass) needs the test set to contain each
variant it should be judged on.

### 2. A score you actually care about

The machine-learning standard score is **AP** ("average precision"): roughly,
of the boxes the model drew, how many were right, and of the boxes that should
exist, how many it found — averaged over how strictly "right" is defined.
It's fine for comparing runs, but it is not your cost.

Your cost is **corrections per page**: how many boxes a human must *add*
(model missed a cell), *delete* (model invented one), or *move/resize* (model
drew it sloppily) to turn the model's output into accepted truth. This can be
computed automatically: match the model's boxes to the verified boxes on a
test page (same label, large overlap = a match), and count the leftovers on
each side plus the matches whose edges are off by more than a tolerance.

Report both, but decide based on corrections per page. The split into
added / deleted / moved is itself diagnostic — see "reading the residue"
below.

### 3. A ledger

One line per training run: date, which model, how many training pages and of
what review status, and the scores on the frozen test set. Without the ledger
there is no curve — just anecdotes. This should be automatic: every training
appends its own line.

---

## The learning curve

With those three in place, the plateau question becomes a two-afternoon
measurement instead of a feeling:

Train the same way on **nested subsets** of the training data — say 50, 100,
and all 200 pages, where the 100 contains the 50 and so on (nested, so the
only difference between runs is *quantity*, not *which* pages). Score each on
the frozen test set. Plot score against training size. Three shapes:

- **Still improving at your current size** → more data helps; keep annotating,
  random pages are fine.
- **Flat between the last two points, and the flat level is acceptable** →
  stop annotating this source. Done.
- **Flat, but the flat level still costs too much correction** → more of the
  same data will NOT fix it. The ceiling is caused by something else — go to
  the error audit below. This is the case to expect for the KE: one page
  holds ~100 cells, so 200 pages is on the order of 20,000 training examples
  of essentially the same two tables. Layouts this uniform saturate early.

Practical note for dedust: the GPU trains one job at a time and a run takes
a while, so the curve is 2–3 deliberate runs, not a casual button press.
That is fine — it needs doing roughly once per source, not weekly.

---

## Reading the residue (the error audit)

When the curve is flat but corrections are still expensive, the *type* of
correction says what to do next. Take one freshly inferred batch and classify
what you fix:

| What you keep fixing | What it means | What actually helps |
|---|---|---|
| Boxes exist but edges are off; you nudge and snap | Detectors of this kind are never pixel-precise. No amount of data fixes it. | Deterministic post-processing — lattice correction, snap, overlap trim. This is already dedust's strength; invest here, not in annotation. |
| Missing or invented boxes, spread evenly over ordinary pages | A quality floor in the training data itself (see the two poisons below), or genuine visual ambiguity. | Clean the training data; do NOT add more of it. |
| Errors clustered on unusual pages (multi-table pages, section transitions, damaged scans) | The common case is saturated; the rare case is under-represented. Random extra pages barely contain it. | Annotate exactly those pages and fine-tune from the current model. Ten targeted pages beat two hundred random ones. |

## The two poisons (quality beats quantity)

Both put a ceiling on the curve that more data cannot break — and both are
about what goes *into* training, not how much:

1. **Training on the model's own uncorrected output.** The COCO export
   currently takes every shape on every page. Any page still in status
   "predicted" feeds the model its own previous mistakes labeled as truth.
   The model then confidently reproduces them — and the more such pages
   accumulate, the *lower* the ceiling. Defense: export only pages a human
   has actually touched (status corrected/verified). This is the single most
   likely cause of a premature plateau in the current setup.

2. **Inconsistent annotation conventions.** If cell edges were drawn tight in
   one session and generous in another, or headers handled two ways, the
   model learns the average and hedges — which surfaces as exactly the
   edge-sloppiness you then correct by hand forever. Worth one deliberate
   pass: pick the convention, spot-check old pages against it.

---

## Transfer: a new year / volume / source

Before annotating a new year of an existing source (Compass 1875 after 1874)
or a new source entirely, measure instead of guessing:

1. Hand-verify a *small* sample of the new material (10–20 pages) — this
   becomes its frozen test set.
2. Score the existing model on it, same corrections-per-page metric.
3. Read the number: close to the home score → run inference at scale and just
   review. Much worse → correct a few dozen pages of the new material and
   **fine-tune from** the existing model (warm start) rather than training
   from scratch; sibling layouts usually converge with a fraction of the data.

The same measurement, repeated after the fine-tune, tells you when the new
source has caught up.

---

## Rules of thumb

- No number without a frozen test set — everything else is anecdote.
- Decide on corrections-per-page; report AP alongside for comparability.
- Uniform layout → expect early saturation; variation, not volume, is what
  the model is short of.
- A flat curve is not a failure — it is the signal to redirect effort:
  geometry residue → post-processing; even residue → data cleaning;
  clustered residue → targeted annotation + fine-tune.
- Never let unreviewed predictions into a training export.
- New source: measure transfer on 10–20 verified pages before committing to
  an annotation campaign.

The dedust build items that make all of this one-click live in
[07_improvement_roadmap.md](07_improvement_roadmap.md), section P10.
