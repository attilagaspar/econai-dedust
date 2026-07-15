"""Flat-text ↔ row_struct alignment: empty lines are ROW POSITIONS.

Bug: _split_lines stripped leading/trailing empty lines, so a flat layer that
was the join of rows with empty leading/middle rows lost its positions — the
values were re-imported into the FIRST N rows instead of their own rows, and
the editor showed a bogus size-mismatch warning for correctly placed edits.
"""
from app.server import _apply_layer_rows, _distribute_flat_to_rows, _split_lines


def test_split_lines_preserves_positions():
    # leading + interior empties kept, trailing blank lines / CR dropped
    assert _split_lines("\n\nA\r\nB\n\n") == ["", "", "A", "B"]
    assert _split_lines("A\nB") == ["A", "B"]
    assert _split_lines("") == []
    assert _split_lines(None) == []


def _shape_with_rows(n, human_flat=None):
    rows = [{"n": i + 1, "y0": 10.0 * i, "y1": 10.0 * i + 10,
             "ocr": "", "llm": "", "human": ""} for i in range(n)]
    sh = {"points": [[0, 0], [100, 10 * n]],
          "row_struct": {"version": 1, "origin": "test", "rows": rows}}
    if human_flat is not None:
        sh["human_output"] = {"human_corrected_text": human_flat}
    return sh


def test_distribute_flat_keeps_empty_rows_in_place():
    # join of 5 rows where only rows 3-4 have text: 5 positional lines
    sh = _shape_with_rows(5)
    _distribute_flat_to_rows(sh, "human", "\n\nA\nB\n")
    got = [r["human"] for r in sh["row_struct"]["rows"]]
    assert got == ["", "", "A", "B", ""]


def test_apply_layer_rows_pulls_flat_human_positionally():
    # a line-by-line LLM run on a cell whose rows have no human yet must pull
    # the flat Human in at its own row positions, not packed to the top
    sh = _shape_with_rows(5, human_flat="\n\nA\nB\n")
    bands = [(r["y0"], r["y1"]) for r in sh["row_struct"]["rows"]]
    _apply_layer_rows(sh, bands, "llm", ["l1", "l2", "l3", "l4", "l5"], "linebyline")
    got = [r["human"] for r in sh["row_struct"]["rows"]]
    assert got == ["", "", "A", "B", ""]
    assert [r["llm"] for r in sh["row_struct"]["rows"]] == ["l1", "l2", "l3", "l4", "l5"]
