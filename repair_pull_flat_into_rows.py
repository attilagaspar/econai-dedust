"""One-off repair: pull flat LLM / Human / OCR back into internal rows.

Row-structure rebuilds (anchored OCR/LLM with a different row count) dropped
the other layers from the rows while their flat originals survived — so a
perfectly good whole-cell LLM output showed an empty LLM column and exports
fell back to OCR. app/server.py now pulls flat layers back automatically on
every rebuild; this script applies the same rule once to EXISTING data.

Rule (deliberately conservative): a layer is filled only when its row values
are ALL empty and the flat text's line count matches the row count EXACTLY
(a positional fit). Nothing is ever overwritten.

Usage:
    python repair_pull_flat_into_rows.py <project>            # dry run
    python repair_pull_flat_into_rows.py <project> --apply    # write changes
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.server import _split_lines                       # noqa: E402


def flat_of(sh, lay):
    if lay == "llm":
        return (sh.get("openai_output") or {}).get("response") or ""
    if lay == "human":
        return (sh.get("human_output") or {}).get("human_corrected_text") or ""
    return ((sh.get("tesseract_output") or {}).get("ocr_text")
            or (sh.get("easyocr_output") or {}).get("ocr_text") or "")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    project = sys.argv[1]
    apply   = "--apply" in sys.argv
    ann = Path(__file__).parent / "projects" / project / "annotations"
    if not ann.exists():
        print(f"No annotations folder: {ann}")
        sys.exit(1)

    filled = {"llm": 0, "human": 0, "ocr": 0}
    count_mismatch = {"llm": 0, "human": 0, "ocr": 0}
    pages_changed = skipped_busy = 0

    def process_page(data):
        """Fill layers in place; return per-page (fills, mismatches)."""
        f = {"llm": 0, "human": 0, "ocr": 0}
        m = {"llm": 0, "human": 0, "ocr": 0}
        for sh in data.get("shapes", []):
            rows = (sh.get("row_struct") or {}).get("rows") or []
            if not rows:
                continue
            for lay in ("llm", "human", "ocr"):
                if any((r.get(lay) or "").strip() for r in rows):
                    continue                       # rows already have values
                lines = _split_lines(flat_of(sh, lay))
                if not lines or not any(l.strip() for l in lines):
                    continue                       # no flat content either
                if len(lines) != len(rows):
                    m[lay] += 1                    # needs manual import-anyway
                    continue
                for r, t in zip(rows, lines):
                    r[lay] = t
                f[lay] += 1
        return f, m

    for jf in sorted(ann.glob("*.json")):
        # Live editors (the review server) may save this page concurrently —
        # re-check the mtime just before writing and redo the page from
        # scratch if it changed under us; give up (loudly) after 3 attempts.
        for attempt in range(3):
            try:
                mtime = jf.stat().st_mtime_ns
                data = json.loads(jf.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  ! unreadable, skipped: {jf.name} ({e})")
                break
            f, m = process_page(data)
            if apply and any(f.values()):
                if jf.stat().st_mtime_ns != mtime:
                    continue                       # edited meanwhile → redo page
                jf.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                              encoding="utf-8")
            for lay in filled:
                filled[lay] += f[lay]
                count_mismatch[lay] += m[lay]
            if any(f.values()):
                pages_changed += 1
            break
        else:
            skipped_busy += 1
            print(f"  ! page kept changing under us, SKIPPED: {jf.name} — re-run later")

    mode = "APPLIED" if apply else "DRY RUN (nothing written — add --apply)"
    print(f"{mode} on {project}:")
    print(f"  pages touched:               {pages_changed}")
    for lay in ("llm", "human", "ocr"):
        print(f"  {lay:5s} cells filled: {filled[lay]:6d}   "
              f"(count mismatch, left for manual review: {count_mismatch[lay]})")


if __name__ == "__main__":
    main()
