"""Excel export: lattice layout, non-lattice interleaving, and the column
filter exemption for free annotations.

Guards two real past bugs:
- free annotations (title/footer) on lattice pages were dropped entirely;
- once interleaved at column 0, they were then dropped by any Columns
  filter that excluded column 1 (e.g. "2").
"""
import io

import openpyxl


def _export(client, page_folder, **params):
    p = {"folder": str(page_folder), "scope": "page", "stem": "p1",
         "layer": "human"}
    p.update(params)
    r = client.get("/api/export/excel", params=p)
    assert r.status_code == 200, r.text
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    return wb


def _all_values(ws):
    return {str(c.value) for row in ws.iter_rows() for c in row
            if c.value not in (None, "")}


def test_lattice_and_free_annotations_both_exported(client, page_folder):
    wb = _export(client, page_folder)
    vals = _all_values(wb.worksheets[0])
    for expected in ("A1", "B1", "A2", "B2", "TITLE ROW", "FOOTER ROW"):
        assert expected in vals, f"missing {expected!r} in export"


def test_free_annotations_ordered_around_lattice(client, page_folder):
    ws = _export(client, page_folder).worksheets[0]
    order = [str(c.value) for row in ws.iter_rows() for c in row
             if str(c.value) in ("TITLE ROW", "A1", "A2", "FOOTER ROW")]
    assert order == ["TITLE ROW", "A1", "A2", "FOOTER ROW"]


def test_col_filter_keeps_free_annotations(client, page_folder):
    """Columns filter '2' → only lattice column 2 survives, but the free
    title/footer rows must still be exported."""
    vals = _all_values(_export(client, page_folder, col_filter="2").worksheets[0])
    assert "B1" in vals and "B2" in vals          # column 2 kept
    assert "A1" not in vals and "A2" not in vals  # column 1 filtered out
    assert "TITLE ROW" in vals and "FOOTER ROW" in vals   # free cells exempt


def test_col_filter_open_range(client, page_folder):
    vals = _all_values(_export(client, page_folder, col_filter="2-").worksheets[0])
    assert "B1" in vals and "A1" not in vals
    assert "TITLE ROW" in vals


def test_types_filter(client, page_folder):
    vals = _all_values(_export(client, page_folder, types="cell").worksheets[0])
    assert "A1" in vals
    assert "TITLE ROW" not in vals
