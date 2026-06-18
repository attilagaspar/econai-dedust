# Notes: detecting table structure with the layout model

Idea / question: can the detectron2 layout model find **table areas** and their
**internal row/column structure** (not just cells), alongside separating
text blocks / tables / figures?

Short answer: yes. It splits into an easy part and a harder part.

## 1. Page-level class separation — easy, basically free

`text_block` vs `table` vs `figure` is plain **multi-class object detection** —
exactly what the current detectron2 (Faster R-CNN) pipeline already does. Just
add the classes and annotate examples (PubLayNet-style layout analysis). No new
technique.

## 2. Internal table structure (rows / columns / separators) — TSR

This is a distinct, studied problem: **Table Structure Recognition**. Three ways
to cast it as detection:

1. **Rows & columns as objects (recommended).** Train classes `table_row`
   (full-width thin boxes) and `table_column` (full-height thin boxes), then
   reconstruct the grid by **intersecting rows × columns** — i.e. what our
   lattice tool already does from cell boxes, but learned. NMS-friendly: rows
   don't overlap rows, columns don't overlap columns. Separators = the
   boundaries between adjacent row/column regions (more robust than detecting
   the thin separator lines directly).
2. **Cells as objects** — detect every cell box (we already annotate these).
   Fine for regular grids, messy when boxes touch.
3. **Separators as a signal** — SPLERGE "split-then-merge": predict row/column
   separator positions, then a merge step handles spanning cells.

## Recommended approach for THIS pipeline

- **Two models, two scales** (what the field converged on): a page-level
  detector for `{text_block, table, figure}`, then a **structure model run on
  each cropped table region** that detects `row`, `column` (+ optionally
  `column_header`, `spanning_cell`). Page-scale + fine structure in one detector
  is much harder than structure on a zoomed-in crop.
- **Auto-generate the row/column training labels from existing annotations.**
  Our annotations already carry `super_row` / `super_column` per cell, so we can
  derive full-width row boxes and full-height column boxes programmatically from
  each lattice — little/no new manual annotation to teach internal structure.
- **Reconstruct + correct in the editor.** If the model predicts row/column
  boxes, the existing band/grid logic can consume them and the human-in-the-loop
  editor fixes misses — fits what we've built (incl. multi-lattice).

## The catch: spanning / merged cells

Pure rows × columns intersection breaks on **spanning cells** — exactly our
gazetteer case (a settlement name spanning several data rows; a `Járás összesen`
row spanning columns). Needs either a merge step (SPLERGE) or a model that
detects spanning cells explicitly. If tables are *mostly* regular grids,
intersection gets ~90% and the editor fixes the rest.

## Off-the-shelf options

- **CascadeTabNet** — Cascade Mask R-CNN (detectron2 family); detects tables +
  cells. Closest to our stack.
- **Table Transformer / TATR** (Microsoft, DETR-based, pretrained on
  PubTables-1M; open weights on HuggingFace) — detects table, rows, columns,
  column headers, and **spanning cells** as objects. Current go-to; fine-tuning
  it would likely beat training row/column detection from scratch and it already
  handles spanning cells.

## Plan of attack (if/when we build it)

1. Try **row + column detection on cropped table regions**, with labels
   **auto-derived from existing lattice annotations** (cheap, reuses our data).
2. Reconstruct the grid via intersection; feed into the existing lattice/grid
   logic; correct in the editor.
3. If spanning cells become the bottleneck, **fine-tune TATR** instead.
