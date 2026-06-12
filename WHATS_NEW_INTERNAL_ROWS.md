# What's new: Internal rows (update of 2026-06-12)

This guide is for everyone who already knows the editor and worked with the
previous version. The update introduces one big concept — **internal rows** —
and a set of tools around it. Everything you knew still works; old projects
open exactly as before.

---

## The idea in one paragraph

Until now a lattice cell stored only three blobs of text (OCR, LLM, Human),
and the line breaks inside them were the only hint of where one row of the
original table ended and the next began. From now on the app can store, inside
each annotation, the actual **row boxes**: where each internal row sits on the
image (its top and bottom), numbered 1…N, with the OCR, LLM and Human readings
attached **per row, side by side**. Each annotation has exactly **one** row
structure — the layers share it, so row 3 of the OCR is by definition the same
physical row as row 3 of the LLM.

You will notice cells with a structure immediately: thin dashed cyan divider
lines appear inside them on the page, and blue bands on the cell crop.

---

## How a cell gets its internal rows

1. **Automatically** — every *line-by-line* or *anchored* OCR/LLM run now
   saves the rows it detected. Re-running a layer on a cell that already has
   a structure **reuses** the existing rows (it does not re-detect), so your
   hand-tuned dividers survive.
2. **⊞ Convert** — in the right panel there is a new **Internal rows** group.
   For old cells that only have flat text, press **⊞ Convert**: the app takes
   the best text layer (Human > LLM > OCR), counts its lines, and finds that
   many row boundaries on the image. Layers whose line count doesn't match
   stay empty in the table (their flat text is kept, nothing is lost).
3. **In bulk** — Batch panel → operation **"⊞ Convert annotations to internal
   rows"**. Respects the condition and column filters; already-converted
   cells are skipped.

**PDF is not one of the row layers** (it is often messy) — but it is shown
next to them read-only, see below.

---

## The table view (replaces the three text boxes)

When you select a cell that has internal rows, the three stacked text boxes
are replaced by a table:

```
#  [crop]  PDF   OCR   LLM   Human
```

Columns are ordered worst → best data quality. What you can do there:

- **Green row** = OCR and LLM agree (and Human, if filled, agrees too).
  **Red row** = they conflict. Red rows are what you need to look at.
- **[crop]** — a thumbnail of that row's slice of the cell image, so you can
  check against the original without leaving the table.
- **Hover a row** → its band lights up on the cell crop.
- **Human column is editable** — type and tab away, it saves instantly.
- **Click any PDF/OCR/LLM value** → copies it into that row's Human field.
- **⤓H** in a column header → copies the whole column into Human.
- **⚖** in the Human header → **majority vote**: for each row it looks at the
  non-empty PDF/OCR/LLM values; one source → copy it; two → copy only if they
  agree; three → copy if at least two agree. Rows without consensus are left
  untouched (it never overwrites your Human values with a guess).
- **⟳** in each column header re-pulls that column **using the current row
  structure** (no re-detection):
  - **PDF ⟳** — re-extracts the PDF text layer *per row band* (much more
    accurate than the old top-aligned page extract);
  - **OCR ⟳** — EasyOCR row by row over the current bands;
  - **LLM ⟳** — the LLM row by row with the prompt currently in the LLM panel.
    While running, this button becomes **■** — click it to stop.
- **✕ Rows** removes the structure (the flat text layers are kept).

**If a flat layer disagrees with the table** (e.g. you ran whole-cell LLM and
it returned 26 lines for 25 rows), that layer's old text box reappears below
with a yellow ⚠ warning showing the counts, plus an **⤓ import anyway**
button that force-fills the rows top-aligned. Tip: whole-cell LLM only lands
in the table when its line count happens to match — use **LLM line×line** or
**anchored** (or the LLM ⟳ button) for guaranteed per-row results.

The PDF column is capped at 60 lines for huge extracts (an input appears to
raise the cap); PDF lines beyond the row structure show as dimmed ghost rows.

---

## Editing the row boundaries

Switch to **Edit mode** and work on the **cell crop** (middle panel):

- Hover near a divider → it turns **red** and the cursor becomes ↕.
  **Red line visible = you are about to act on that divider.**
  - **Drag** — move the divider.
  - **Double-click** — merge the two rows around it (texts joined by a space).
- Away from any divider the cursor is ＋:
  - **Double-click** — split the band at that point (texts stay in the upper
    half, the new lower row starts empty).

So: *red line → removing a row; no red line → adding one.*

Moving or resizing an annotation (including lattice separator drags)
**rescales the internal rows proportionally**. After a drastic resize, fix
the dividers by hand or re-convert.

---

## Anchoring: new "Row structure" source

The **Anchor at** dropdowns (OCR anchored, LLM anchored, and the batch panel)
have a new option: **Row structure**. Instead of taking just the row *count*
from the reference cell, it projects the reference's **exact divider
positions** onto every target cell in the lattice row. The projected bands
also become the targets' stored structure — what was read is what you see.

This enables the high-precision workflow:

> Convert / hand-tune the anchor column's rows → anchor at **Row structure**
> → every other column inherits the exact same dividers.

In batch mode, lattice rows whose anchor cell has no structure are skipped.

**Anchor column pattern — skip slots.** The pattern now supports empty
positions: `8,,2,1` means page 1 of the cycle anchors column 8, page 2 is
*skipped entirely*, page 3 anchors column 2, page 4 column 1, repeat. Note
the pattern cycles over the **selected** pages only (so with "even pages
only", the slots are consumed by even pages alone), and `9,,` is *three*
slots (anchor, skip, skip) — "every other page" is `9,`.

---

## Excel export

- A cell **with** internal rows exports one Excel row per internal row, taken
  straight from the structure. Cells without one behave as before.
- The layer choice (e.g. Human > LLM > OCR > PDF) now applies **per internal
  row**, not per cell: a half-corrected cell exports your Human values on the
  rows you fixed and the LLM's on the rest. (Previously one layer won the
  whole cell.)
- **Dual-page layout**: paired pages are now aligned **row by row** from the
  lattice start onwards — lattice row N on the left page sits on exactly the
  same Excel rows as lattice row N on the right page, with red padding where
  one side is shorter. (Previously only the lattice start was aligned and
  rows could drift apart.)

---

## Reliability fix you should know about

Page JSONs are now written **atomically** (temp file + swap, serialized,
with retries that outlast Dropbox/antivirus file locks). Previously, two
simultaneous writes — e.g. a batch OCR run finishing while you edited a cell —
could corrupt a page file ("Extra data" error on load). If you ever saw that,
it cannot happen anymore. If a page write is delayed by a file lock you'll
see a `[write_json]` line in the server console instead of a lost result.

---

## Suggested daily workflow with the new tools

1. Run lattice detection / corrections as usual.
2. Pick the most reliable column on the page, run line×line OCR (or convert
   from existing text), and **fix its dividers by hand** on the crop.
3. Anchor at **Row structure** to project those dividers across the row
   (single page from the inspector, or in bulk via Batch with a column
   pattern).
4. In the table view: **PDF ⟳**, then **⚖ majority vote**.
5. Go through the **red rows**, checking the crop thumbnails, and fill the
   Human cells.
6. Export — your Human corrections win per row, dual pages stay aligned.
