# What's new: rules, LLM fixing & clips (update of 2026-06-16)

This guide is for everyone who already used the **internal rows** version (see
[WHATS_NEW_INTERNAL_ROWS.md](WHATS_NEW_INTERNAL_ROWS.md)). Everything you knew
still works; this layer adds **validation, automated correction, and
cross-page linking** on top of the internal-row structure. Old projects open
unchanged.

The headline: you can now **define arithmetic rules between columns, see where
they're violated, let an LLM propose fixes you approve, and link the same
real-world record across pages** — and the Excel export got more flexible.

---

## 1. Row rules — arithmetic checks between columns

New **⚖ Rules** button in the toolbar opens a rule editor. A rule is an
expression between **lattice column numbers**, e.g. `1+2=4` with a name like
*"male + female = all workers"*.

Each rule row has four boxes:

1. **Expression** — column numbers (1-indexed), operators `+ − * / ( )`, one `=`.
2. **Name** — shown in the Diagnose dropdown.
3. **Zero characters** — a cell consisting only of these counts as **0**
   (default covers every dash/underscore/dot variant). Empty cells are always 0.
4. **Page pattern** — optional `1`/`0`s (e.g. `1,0,0`) so the rule only applies
   to some pages, cycling over pages in order. Blank = every page.

Saved rules appear in the **Diagnose** dropdown as `ROW RULE: <name>`, plus an
**⚖ all rules** entry that checks every rule at once. Selecting one:

- evaluates it **per internal row**, using the best layer per row
  (Human > LLM > OCR > PDF);
- treats anything that isn't a clean number as a **failure** (so OCR junk like
  `5O` flags itself);
- **shades the violating internal rows red** on the page — you see exactly
  which line of which cell breaks the rule, not just the cell.

Rules are saved per project and survive restarts.

**Note:** some violations are real source errors (publication mistakes,
rounding) that *can't* be fixed — those just stay flagged. That's expected.

---

## 2. Fix rule — let the LLM propose corrections, you approve

New **🛠 Fix rule** button. With a rule (or *all rules*) selected in Diagnose,
it lists every violation on the page. **▶ Ask LLM** sends, for each violating
line, the **cell image snippets + the current readings** to the LLM and shows
proposed corrections as green diffs.

- A **✓ / ⚠ indicator** tells you whether the proposal actually makes the rule
  hold — so a confident-but-wrong fix is visible before you accept it.
- **Editable prompt** (remembered between sessions) and its **own model
  dropdown**.
- **Max lines / request** chunk size — set it to **1** for strict
  line-by-line, higher to give the model cross-line context.
- **Per-line checkboxes** (default checked): **✓ Apply checked** writes only
  the checked lines, removes them from the list, and you can **Ask LLM** again
  for whatever's left. Untick the lines the LLM got wrong.

**Where corrections are stored:** accepted fixes go to the **LLM layer**,
flagged 🛠 as rule-fixed — *not* to Human. This keeps the Human layer's "a
person actually verified this" meaning intact (important once batch
auto-accept arrives), and an existing Human value still wins by layer
priority. The 🛠 marker shows on rule-fixed cells in the table view.

Each violation snippet is sent with its own rule, so **all rules** works too:
one pass fixes violations across every rule on the page.

---

## 3. Clips — link the same data unit across pages

New **🚩 Clips** toggle. A clip is a numbered, colored flag you stamp on **any**
annotation (not just lattice cells) to say "this is the same underlying record
as that annotation over there" — e.g. a settlement row that continues at the
top of the next page, or a firm description tied to its balance table.

How to use it:

1. Turn on **🚩 Clips**. A flag tray appears across the top of the page.
2. Drag the **➕** flag onto an annotation (or click ➕, then click the
   annotation) — it gets that flag and the flag is now "dangling".
3. Turn the page. The dangling flag is waiting in the tray. Drag it onto the
   continuation annotation — now both carry the same clip.

Clipped annotations show a colored numbered badge. **Click a badge (in clip
mode) to remove it** (with confirm). "Show all" in the tray reveals every clip
for records spanning 3+ pages.

Clips export too: the Excel modal has **Add a Clip column** and **Only rows
that have a clip**.

---

## 4. Excel export changes

The old **odd / even** checkboxes and the **single / dual page** radio are
**gone**, replaced by a single **page pattern** box of `1`/`0`s:

- blank → every selected page, stacked vertically (old "single")
- `1,1` → pairs side by side (old "dual")
- `1,0` → odd pages only · `0,1` → even pages
- `1,1,0,0` → print two side by side, skip two · any 1/0 cycle works

Side-by-side pages are now aligned **rank by rank** on their printed lattice
rows (the k-th printed row of each page starts on the same Excel row, shorter
ones red-padded), so paired pages no longer drift apart after a height
mismatch.

Also new in the export modal: **Add a Clip column**, **Only rows that have a
clip**, and (from the internal-rows release) **Only cells with internal row
structure**.

---

## 5. Smaller things

- **LLM models:** the model dropdowns now include the **GPT-5 family**
  (GPT-5 / mini / nano) and the **o-series** reasoning models. Calls adapt
  automatically (these models need different parameters), so they actually
  work now — including the previously-broken `gpt-5-nano`. Each modal that
  runs the LLM has its own model picker.
- **Anchoring onto a human-corrected cell** now pulls the existing Human text
  into the new rows (top-aligned, truncated if longer) instead of leaving it
  invisible behind the table.
- **Toolbar wrapping:** crowded toolbar rows now wrap to a new line instead of
  being clipped under the right panel.

---

## Suggested workflow with the new tools

1. Finish the lattice + internal rows as before (OCR/LLM/anchor, fix dividers).
2. Define your **column rules** (⚖ Rules) — the identities the table must obey.
3. Pick a rule (or *all rules*) in **Diagnose** → red bands show every
   violation.
4. **🛠 Fix rule** → Ask LLM → untick wrong proposals → **Apply checked** →
   Ask again for the rest. The ⚠ ones that won't close are likely real source
   errors — note and move on.
5. Use **🚩 Clips** to link records split across page breaks.
6. **Export** with the page pattern that matches your layout, plus the Clip
   column if you're using clips.
