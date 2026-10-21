"""_resolve_pdf_source: a page's recorded PDF path from the other operating
system still resolves to <project>/sources/<basename> (the fallback used to
take Path(...).name, which on Linux leaves a Windows path whole)."""
from pathlib import Path

from app.server import _resolve_pdf_source


def test_windows_absolute_path_resolves_to_sources_on_any_os(tmp_path):
    proj = tmp_path / "proj"; ann = proj / "annotations"; src = proj / "sources"
    ann.mkdir(parents=True); src.mkdir()
    pdf = src / "Volume__pages1-50.pdf"; pdf.write_bytes(b"%PDF-1.4")
    win = r"C:\Users\someone\Dropbox\econai-dedust\projects\proj\sources\Volume__pages1-50.pdf"
    assert _resolve_pdf_source(ann, win) == pdf
    posix = "/home/someone/dedust/projects/proj/sources/Volume__pages1-50.pdf"
    assert _resolve_pdf_source(ann, posix) == pdf
    assert _resolve_pdf_source(ann, "sources/Volume__pages1-50.pdf") == pdf
    assert _resolve_pdf_source(ann, r"C:\elsewhere\missing.pdf") is None
