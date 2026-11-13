"""Batch OCR fast path: tesseract over many crops in one process per worker
(a list file + TSV), parsed back into the per-crop dicts the line grouper
reads. The parser is tested on a hand-written TSV; the real tesseract run
only where tesseract is installed."""
import os
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from app import server as srv

TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "1\t1\t0\t0\t0\t0\t0\t0\t300\t60\t-1\t",
    "5\t1\t1\t1\t1\t1\t10\t12\t80\t20\t96.5\tZinner",
    "5\t1\t1\t1\t1\t2\t100\t12\t80\t20\t91.2\tMóritz",
    "5\t1\t1\t1\t2\t1\t10\t36\t80\t20\t88.0\tPozsony",
    "1\t2\t0\t0\t0\t0\t0\t0\t300\t60\t-1\t",
    "5\t2\t1\t1\t1\t1\t10\t12\t80\t20\t0\tjunk",
    "1\t3\t0\t0\t0\t0\t0\t0\t300\t60\t-1\t",
    "5\t3\t1\t1\t1\t1\t10\t12\t80\t20\t95\t1876.",
])


def test_tsv_parser_and_line_grouper_agree_with_the_per_cell_path():
    per = srv._tess_parse_tsv(TSV, 3)
    assert [len(d["text"]) for d in per] == [4, 2, 2]
    assert srv._tess_group_lines(per[0]) == ("Zinner Móritz\nPozsony", 91.7)   # confidences truncated to ints, as always
    assert srv._tess_group_lines(per[1]) == ("", 0.0)           # a zero-confidence word is dropped
    assert srv._tess_group_lines(per[2]) == ("1876.", 95.0)
    assert srv._tess_parse_tsv("", 2) == [dict(text=[], conf=[], block_num=[], par_num=[], line_num=[], top=[])] * 2
    assert srv._tess_parse_tsv("garbage header\n1\t2\t3", 1)[0]["text"] == []


def _tesseract_here():
    return bool(shutil.which("tesseract") or os.path.exists(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))


@pytest.mark.skipif(not _tesseract_here(), reason="tesseract not installed")
def test_batch_tesseract_reads_many_crops_in_order():
    font = None
    for cand in (r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(cand):
            font = ImageFont.truetype(cand, 36); break
    if font is None:
        pytest.skip("no TrueType font for a legible crop")
    crops = []
    for txt in ("1876", "Pozsony 1882", "Adony"):
        im = Image.new("L", (420, 70), 255)
        ImageDraw.Draw(im).text((10, 10), txt, fill=0, font=font)
        crops.append(im)
    res = srv._tess_batch_data(crops, "eng", "--psm 7", workers=2)
    assert len(res) == 3
    texts = [srv._tess_group_lines(d)[0] for d in res]
    assert "1876" in texts[0] and "1882" in texts[1] and "Adony" in texts[2]
