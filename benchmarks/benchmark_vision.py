# -*- coding: utf-8 -*-
"""Vision benchmark: can a vision LLM read a WHOLE page into a table, and how
does that compare to the current pipeline (layout model + OCR + per-cell LLM)?

Scored against gold pages — pages whose Human layer is verified. For every
verified line the gold answer is known; the same units are scored for:
  * the vision model's whole-page transcription (aligned row-by-row), and
  * the pipeline's pre-human layers (LLM layer, OCR layer) as the baseline.

AZURE ONLY by design (user decision): non-azure models are refused.
Requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in the environment.

Standalone: touches nothing in the app; reads page JSONs, writes reports to
benchmarks/. Reuses the app's LLM client + response cache (so re-runs are free).

Usage:
  python benchmarks/benchmark_vision.py                     # default gold set
  python benchmarks/benchmark_vision.py --models azure:gpt-5-mini
  python benchmarks/benchmark_vision.py --selftest          # no API calls:
        scorer verified against a synthetic 'model' output derived from gold
  python benchmarks/benchmark_vision.py --dry-run           # show gold stats only
"""
import argparse
import base64
import datetime
import difflib
import io
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

# Read-only reuse of the app's LLM plumbing (client factory, cache-aware call)
from app.server import _make_llm_client, _llm_complete, _find_image  # noqa: E402

# ── Default gold set: pages with high human-verified coverage ────────────────
DEFAULT_GOLD = [
    ("projects/foldbirtok1935/annotations",
     "MagyarStatisztikaiKozlemenyek_US_099_1936-1718919823__pages102-151_page_1"),
    ("projects/machines1935/annotations", "pages17-66_page_5"),
    ("projects/machines1935/annotations", "pages67-116_page_45"),
]

# Approximate USD per 1M tokens (input, output) — for the cost column only.
PRICES = {"gpt-5-mini": (0.25, 2.00), "gpt-5-nano": (0.05, 0.40),
          "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00),
          "gpt-5": (1.25, 10.00)}

PROMPT = (
    "The image is a full page from a 1930s Hungarian statistical publication "
    "containing a printed table. Transcribe the table body.\n"
    "Return ONLY a JSON object: {\"rows\": [[...], [...], ...]} — one entry per "
    "printed text line of the table body, each entry a list of strings, one "
    "per table column, left to right. Rules:\n"
    "- skip the column-header rows; transcribe data lines only\n"
    "- copy numbers and names EXACTLY as printed (Hungarian diacritics too)\n"
    "- an empty cell is \"\" — never drop or merge columns\n"
    "- dashes/ditto marks: copy the character you see (e.g. \"-\", \"—\")\n"
    "- do not summarize, do not skip lines, transcribe every data line"
)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^\w]", "", s)


def _lev1(a: str, b: str) -> bool:
    """Within one edit of each other."""
    try:
        from rapidfuzz.distance import Levenshtein
        return Levenshtein.distance(a, b) <= 1
    except ImportError:
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


# ── Gold extraction ──────────────────────────────────────────────────────────

def gold_tables(jf: Path):
    """Flatten a lattice page into data rows.

    Returns a list of tables; each table = list of rows; each row =
    {gold: [str per col], llm: [...], ocr: [...], mask: [bool per col]} where
    mask marks the human-verified cells (the only ones scored)."""
    data = json.loads(jf.read_text(encoding="utf-8"))
    from collections import defaultdict
    cells = defaultdict(dict)                       # (table, sr) -> {sc: shape}
    for sh in data.get("shapes", []):
        sr, sc = sh.get("super_row"), sh.get("super_column")
        if sr is None or sc is None:
            continue
        cells[(sh.get("table") or 0, int(sr))][int(sc)] = sh

    def layers(sh):
        rows = (sh.get("row_struct") or {}).get("rows") or []
        if rows:
            return ([(r.get("human") or "").strip() for r in rows],
                    [(r.get("llm") or "").strip() for r in rows],
                    [(r.get("ocr") or "").strip() for r in rows])
        hum = ((sh.get("human_output") or {}).get("human_corrected_text") or "").strip()
        llm = ((sh.get("openai_output") or {}).get("response") or "").strip()
        ocr = ((sh.get("tesseract_output") or {}).get("ocr_text") or
               (sh.get("easyocr_output") or {}).get("ocr_text") or "").strip()
        n = max(len(hum.splitlines()), 1)
        pad = lambda t: (t.splitlines() + [""] * n)[:n]
        return pad(hum), pad(llm), pad(ocr)

    tables = defaultdict(list)
    for (tbl, sr) in sorted(cells):
        row_cells = cells[(tbl, sr)]
        cols = sorted(row_cells)
        per_col = {c: layers(row_cells[c]) for c in cols}
        n = max(len(per_col[c][0]) for c in cols)
        for i in range(n):
            g, l, o, m = [], [], [], []
            for c in cols:
                hum, llm, ocr = per_col[c]
                g.append(hum[i] if i < len(hum) else "")
                l.append(llm[i] if i < len(llm) else "")
                o.append(ocr[i] if i < len(ocr) else "")
                m.append(bool((hum[i] if i < len(hum) else "").strip()))
            if any(m):                              # keep rows with ≥1 verified cell
                tables[tbl].append({"gold": g, "llm": l, "ocr": o, "mask": m})
    return [tables[t] for t in sorted(tables)]


# ── Vision call ──────────────────────────────────────────────────────────────

def page_b64(folder: Path, stem: str, max_side: int) -> str:
    from PIL import Image
    img_path = _find_image(folder, stem)
    if img_path is None:
        raise SystemExit(f"image not found for {stem}")
    img = Image.open(str(img_path)).convert("RGB")
    if max(img.size) > max_side:
        sc = max_side / max(img.size)
        img = img.resize((int(img.width * sc), int(img.height * sc)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def run_vision(model: str, b64: str):
    client = _make_llm_client(model)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
    ]}]
    resp = _llm_complete(client, model, messages, 16000, temperature=0,
                         response_format={"type": "json_object"}, use_cache=True)
    raw = (resp.choices[0].message.content or "").strip()
    u = getattr(resp, "usage", None)
    tokens = (getattr(u, "prompt_tokens", 0) if u else 0,
              getattr(u, "completion_tokens", 0) if u else 0)
    a, b = raw.find("{"), raw.rfind("}")
    rows = []
    if 0 <= a < b:
        try:
            rows = json.loads(raw[a:b + 1]).get("rows") or []
        except Exception:
            pass
    rows = [[str(c) for c in r] for r in rows if isinstance(r, list)]
    return rows, raw, tokens, bool(getattr(resp, "cached", False))


# ── Alignment + scoring ──────────────────────────────────────────────────────

def row_key(cells):
    """Alignment key: the first non-empty cell (usually the name column)."""
    for c in cells:
        if fold(c):
            return fold(c)
    return ""


def align(gold_rows_, got_rows):
    """Match model rows to gold rows by first-column similarity + order."""
    gk = [row_key(r["gold"]) for r in gold_rows_]
    mk = [row_key(r) for r in got_rows]
    sm = difflib.SequenceMatcher(None, gk, mk, autojunk=False)
    pairs, used_g, used_m = [], set(), set()
    for bl in sm.get_matching_blocks():
        for off in range(bl.size):
            pairs.append((bl.a + off, bl.b + off))
            used_g.add(bl.a + off); used_m.add(bl.b + off)
    # second pass: fuzzy-pair leftovers in order
    free_g = [i for i in range(len(gk)) if i not in used_g]
    free_m = [i for i in range(len(mk)) if i not in used_m]
    gi = mi = 0
    while gi < len(free_g) and mi < len(free_m):
        if _lev1(gk[free_g[gi]], mk[free_m[mi]]) or True:   # positional fallback
            pairs.append((free_g[gi], free_m[mi]))
            used_g.add(free_g[gi]); used_m.add(free_m[mi])
            gi += 1; mi += 1
    dropped = len(gk) - len({p[0] for p in pairs})
    invented = len(mk) - len({p[1] for p in pairs})
    return sorted(pairs), dropped, invented


_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―−"})


def _punct_norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").translate(_DASHES).strip())


def score_rows(gold_rows_, value_of):
    """Score masked cells with value_of(row_index, col_index) -> str|None.
    Punctuation-only cells (ditto dashes etc.) are compared on normalized raw
    text — folding would empty them and miscount correct dashes as missing."""
    s = dict(exact=0, near=0, wrong=0, missing=0, total=0)
    for ri, row in enumerate(gold_rows_):
        for ci, ok in enumerate(row["mask"]):
            if not ok:
                continue
            s["total"] += 1
            v = value_of(ri, ci)
            if v is None or not str(v).strip():
                s["missing"] += 1
                continue
            vf, gf = fold(v), fold(row["gold"][ci])
            if vf == gf:
                if vf or _punct_norm(v) == _punct_norm(row["gold"][ci]):
                    s["exact"] += 1
                else:
                    s["near"] += 1        # both punctuation-only, different glyphs
            elif _lev1(vf, gf):
                s["near"] += 1
            else:
                s["wrong"] += 1
    return s


def pct(part, total):
    return f"{100 * part / total:.1f}%" if total else "–"


def fmt_score(s, extra=""):
    t = s["total"]
    return (f"exact {pct(s['exact'], t)} · near {pct(s['near'], t)} · "
            f"wrong {pct(s['wrong'], t)} · missing {pct(s['missing'], t)}"
            f" (n={t}){extra}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="azure:gpt-5-mini",
                    help="comma-separated azure: models")
    ap.add_argument("--pages", default=None,
                    help="folder::stem,folder::stem (default: built-in gold set)")
    ap.add_argument("--max-side", type=int, default=2048)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="verify the scorer with a synthetic model output (no API)")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    for m in models:
        if not m.startswith("azure:") and not args.selftest:
            raise SystemExit(f"Refusing non-Azure model '{m}' (this benchmark is "
                             "Azure-only by decision — use azure:<deployment>)")

    gold_set = DEFAULT_GOLD
    if args.pages:
        gold_set = [tuple(p.split("::", 1)) for p in args.pages.split(",")]

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    outdir = ROOT / "benchmarks"
    report = [f"# Vision benchmark — {ts}", "",
              "Whole-page vision transcription vs the pipeline's pre-human layers, "
              "scored on human-verified cells only. `near` = within one character "
              "after accent folding. `missing` counts verified lines the approach "
              "never produced — the dangerous error class.", ""]

    for folder_s, stem in gold_set:
        folder = ROOT / folder_s
        jf = folder / f"{stem}.json"
        if not jf.exists():
            print(f"!! missing gold page {stem}"); continue
        tables = gold_tables(jf)
        rows = [r for t in tables for r in t]
        n_units = sum(sum(r["mask"]) for r in rows)
        ncols = max((len(r["gold"]) for r in rows), default=0)
        print(f"\n=== {stem}: {len(rows)} gold data rows × ≤{ncols} cols, "
              f"{n_units} verified cells")
        report += [f"\n## {stem}", "",
                   f"{len(rows)} data rows, {n_units} human-verified cells "
                   f"({len(tables)} table(s), ≤{ncols} columns)", ""]

        # Baseline: the pipeline's own pre-human layers on the same cells.
        # 'missing' for a layer usually means it was never run on that cell,
        # so also report accuracy restricted to cells where the layer exists.
        base_llm = score_rows(rows, lambda ri, ci: rows[ri]["llm"][ci])
        base_ocr = score_rows(rows, lambda ri, ci: rows[ri]["ocr"][ci])
        def when_present(layer):
            covered = base = score_rows(
                rows, lambda ri, ci: (rows[ri][layer][ci] or None))
            n_cov = base["total"] - base["missing"]
            return pct(base["exact"], n_cov), n_cov
        llm_wp, llm_n = when_present("llm")
        ocr_wp, ocr_n = when_present("ocr")
        print(f"  pipeline LLM layer : {fmt_score(base_llm)} · exact-where-present {llm_wp} (n={llm_n})")
        print(f"  pipeline OCR layer : {fmt_score(base_ocr)} · exact-where-present {ocr_wp} (n={ocr_n})")
        report += ["| approach | exact | near | wrong | missing | n | cost |",
                   "|---|---|---|---|---|---|---|",
                   f"| pipeline LLM layer | {pct(base_llm['exact'], base_llm['total'])} | {pct(base_llm['near'], base_llm['total'])} | {pct(base_llm['wrong'], base_llm['total'])} | {pct(base_llm['missing'], base_llm['total'])} | {base_llm['total']} | (already spent) |",
                   f"| pipeline OCR layer | {pct(base_ocr['exact'], base_ocr['total'])} | {pct(base_ocr['near'], base_ocr['total'])} | {pct(base_ocr['wrong'], base_ocr['total'])} | {pct(base_ocr['missing'], base_ocr['total'])} | {base_ocr['total']} | – |"]

        if args.dry_run:
            continue

        for model in models:
            if args.selftest:
                # synthesize a model output from gold: drop 5% of rows, corrupt 10% of cells
                import random
                rnd = random.Random(42)
                got = []
                for r in rows:
                    if rnd.random() < 0.05:
                        continue
                    got.append([(c[:-1] + "X" if c and rnd.random() < 0.10 else c)
                                for c in r["gold"]])
                tokens, cached, raw = (0, 0), True, ""
            else:
                print(f"  → {model} reading the page…", flush=True)
                b64 = page_b64(folder, stem, args.max_side)
                try:
                    got, raw, tokens, cached = run_vision(model, b64)
                except Exception as e:
                    msg = f"  ✕ {model}: {e}"
                    print(msg); report.append(f"| {model} | ✕ {e} | | | | | |")
                    continue
                (outdir / f"raw_{stem}_{model.replace(':', '_').replace('/', '_')}.json"
                 ).write_text(json.dumps({"rows": got, "raw": raw}, ensure_ascii=False,
                                         indent=1), encoding="utf-8")

            pairs, dropped, invented = align(rows, got)
            pair_map = dict(pairs)
            def value_of(ri, ci, _pm=pair_map, _got=got):
                mi = _pm.get(ri)
                if mi is None:
                    return None
                mr = _got[mi]
                return mr[ci] if ci < len(mr) else None
            sc = score_rows(rows, value_of)
            tin, tout = tokens
            price = PRICES.get(model.split(":", 1)[-1])
            cost = (f"${tin / 1e6 * price[0] + tout / 1e6 * price[1]:.4f}"
                    if price and (tin or tout) else
                    ("cache" if cached else f"{tin}+{tout} tok"))
            extra = f" · rows: {dropped} dropped, {invented} invented · {cost}"
            print(f"  vision {model:24s}: {fmt_score(sc, extra)}")
            report.append(
                f"| vision {model} | {pct(sc['exact'], sc['total'])} | "
                f"{pct(sc['near'], sc['total'])} | {pct(sc['wrong'], sc['total'])} | "
                f"{pct(sc['missing'], sc['total'])} | {sc['total']} | {cost} |")
            report.append(f"|   ↳ rows dropped: {dropped}, invented: {invented} | | | | | | |")

    out = outdir / f"vision_benchmark_{ts}.md"
    out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\nreport: {out}")


if __name__ == "__main__":
    main()
