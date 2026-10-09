"""Dataset diagnostics Phase 2 (app/diagnostics.py): exact checks, robust
statistics, trailing-1 artifacts, confirmations and adjudication."""
import json
import math
import random
import shutil
import uuid
from types import SimpleNamespace as NS

import pytest

from app import diagnostics as dg
from app.pipeline import create_project, PROJECTS_ROOT

P = dg.params({})


def V(name, dtype="int", stats=True, scale=None, label=None):
    return NS(name=name, dtype=dtype, stats=stats, scale=scale, label=label, esd=False)


def val(x, stem="p1", idx=0, row_i=0, text=None, status="ok"):
    return {"status": status, "value": x, "text": text if text is not None else str(x),
            "stem": stem, "idx": idx, "row_i": row_i, "row_n": row_i + 1,
            "y0": None, "y1": None, "layers": None}


def rec(key, excluded=False, **vals):
    r = {"key": {"text": key, "fold": key.lower()}, "values": vals}
    if excluded:
        r["excluded"] = True
    return r


# ── helpers ─────────────────────────────────────────────────────────────────

def test_one_char_apart():
    assert dg.one_char_apart("2081", "208")      # trailing 1 deleted
    assert dg.one_char_apart("157", "156")       # substitution
    assert dg.one_char_apart("12", "112")        # insertion
    assert not dg.one_char_apart("157", "157")
    assert not dg.one_char_apart("157", "175")   # transposition = 2 subs


def test_slip_note_names_trailing_one():
    assert "trailing '1'" in dg.slip_note(21, 2)
    assert dg.slip_note(23, 2) == ""


def test_binom_and_t_quantile_against_known_values():
    assert abs(dg.binom_sf(0, 10, 0.3) - 1.0) < 1e-12
    assert abs(dg.binom_sf(10, 10, 0.5) - 0.5 ** 10) < 1e-12
    # Student t: t_{0.975, 10} = 2.2281, t_{0.95, 30} = 1.6973
    assert abs(dg.t_ppf(0.975, 10) - 2.2281) < 1e-3
    assert abs(dg.t_ppf(0.95, 30) - 1.6973) < 1e-3


# ── A1: identities ──────────────────────────────────────────────────────────

def test_identity_mismatch_names_trailing_one_culprit():
    recs = [rec("A", tot=val(30, idx=1), a=val(10, idx=2), b=val(20, idx=3)),
            rec("B", tot=val(30, idx=4), a=val(101, idx=5), b=val(20, idx=6))]
    f, st = dg.check_identities(recs, [{"total": "tot", "parts": ["a", "b"]}], P)
    assert st[0]["ok"] == 1 and st[0]["mismatch"] == 1
    assert len(f) == 1
    c = f[0]["culprit"]
    assert c["variable"] == "a" and c["value"] == "10" and c["idx"] == 5
    assert "trailing '1'" in f[0]["detail"]


def test_identity_dash_counts_as_zero_and_absent_is_untestable():
    recs = [rec("A", tot=val(10), a=val(10), b=val(None, status="missing")),
            rec("B", tot=val(10), a=val(10), b=val(None, status="absent"))]
    f, st = dg.check_identities(recs, [{"total": "tot", "parts": ["a", "b"]}], P)
    assert st[0]["ok"] == 1 and st[0]["untestable"] == 1 and not f


# ── A2: printed totals ──────────────────────────────────────────────────────

def _district(names_vals, start_idx=0):
    out = []
    for i, (n, xs) in enumerate(names_vals):
        out.append(rec(n, **{f"v{j}": val(x, idx=start_idx + i * 10 + j)
                             for j, x in enumerate(xs)}))
    return out


def test_totals_anchor_on_the_right_segment_and_find_culprit():
    variables = [V(f"v{j}") for j in range(5)]
    d1 = _district([("a", [1, 2, 3, 4, 5]), ("b", [10, 20, 30, 40, 50])])
    tot1 = rec("Összesen", excluded=True,
               **{f"v{j}": val(x, idx=900 + j) for j, x in enumerate([11, 22, 33, 44, 55])})
    # second district: one record's v2 reads 301 where the total proves 30
    d2 = _district([("c", [5, 5, 5, 5, 5]), ("d", [7, 7, 301, 7, 7])], start_idx=100)
    hdr = rec("2. Valami járás", excluded=True)
    tot2 = rec("Összesen", excluded=True,
               **{f"v{j}": val(x, idx=950 + j) for j, x in enumerate([12, 12, 35, 12, 12])})
    rows = d1 + [tot1, hdr] + d2 + [tot2]
    f, st = dg.check_totals(rows, {"row_pattern": "osszesen|összesen"}, variables, P)
    assert st["anchored"] == 2 and st["unanchored"] == 0
    assert st["cells_bad"] == 1
    assert len(f) == 1 and f[0]["variable"] == "v2"
    assert f[0]["culprit"]["who"] == "d" and f[0]["culprit"]["value"] == "30"
    assert "trailing '1'" in f[0]["detail"]


def test_totals_unanchored_when_nothing_adds_up():
    variables = [V(f"v{j}") for j in range(4)]
    rows = _district([("a", [1, 1, 1, 1])]) + [
        rec("Összesen", excluded=True, **{f"v{j}": val(999 + j) for j in range(4)})]
    f, st = dg.check_totals(rows, {"row_pattern": "osszesen|összesen"}, variables, P)
    assert st["unanchored"] == 1
    assert f[0]["check"] == "totals_unanchored" and f[0]["severity"] == "info"


# ── C/D/E: distributions ────────────────────────────────────────────────────

def _lognormal_records(n=400, seed=3, var="x"):
    rnd = random.Random(seed)
    return [rec(f"k{i}", **{var: val(int(math.exp(rnd.gauss(5, 1))), idx=i)})
            for i in range(n)]


def test_log_scale_keeps_heavy_tail_quiet_but_catches_glued_value():
    recs = _lognormal_records()
    recs.append(rec("glued", x=val(14853128, idx=999)))      # two cells glued
    f, vs = dg.check_distributions(recs, [V("x")], P)
    assert vs["x"]["scale"] == "log"
    flagged = {ff["ref"]["idx"] for ff in f if ff["check"] == "outlier"}
    assert 999 in flagged
    # log scale: the lognormal tail itself is mostly NOT listed
    assert len(flagged) < 0.02 * len(recs)
    assert any(ff["check"] == "digits" and ff["ref"]["idx"] == 999 for ff in f)
    # extremes panel
    assert vs["x"]["top"][0]["value"] == 14853128


def test_zero_inflated_variable_tests_non_zeros_only():
    recs = [rec(f"z{i}", x=val(0, idx=i)) for i in range(300)]
    recs += [rec(f"n{i}", x=val(3 + (i % 4), idx=500 + i)) for i in range(60)]
    recs.append(rec("big", x=val(4000, idx=999)))
    f, vs = dg.check_distributions(recs, [V("x")], P)
    assert vs["x"]["zero_inflated"] is True
    assert any(ff["ref"]["idx"] == 999 for ff in f if ff["check"] == "outlier")


def test_stats_false_and_small_n_are_skipped():
    recs = _lognormal_records(n=8)
    f, vs = dg.check_distributions(recs, [V("x")], P)
    assert "skipped" in vs["x"] and not f
    f, vs = dg.check_distributions(_lognormal_records(), [V("x", stats=False)], P)
    assert vs == {} and f == []


def test_generalized_esd_finds_planted_outliers():
    rnd = random.Random(7)
    xs = [rnd.gauss(0, 1) for _ in range(200)] + [9.0, -8.5]
    out = dg.generalized_esd(xs, 10, 0.05)
    assert set(out) >= {200, 201}
    assert len(out) <= 4


# ── F: trailing 1 ───────────────────────────────────────────────────────────

def _page_col(stem, values, var="x", start=0):
    return [rec(f"{stem}-{i}", **{var: val(x, stem=stem, idx=start + i)})
            for i, x in enumerate(values)]


def test_trailing_one_quiet_on_uniform_last_digits():
    rnd = random.Random(5)
    recs = []
    for s in range(6):
        recs += _page_col(f"pg{s}", [rnd.randint(20, 900) for _ in range(40)], start=s * 100)
    _, vs = dg.check_distributions(recs, [V("x")], P)
    f, heat = dg.check_trailing_one(recs, [V("x")], vs, P)
    assert not [x for x in f if x["check"] == "trailing1"]


def test_trailing_one_flags_page_with_excess_and_lists_candidates():
    rnd = random.Random(9)
    recs = []
    for s in range(5):
        recs += _page_col(f"pg{s}", [rnd.randint(20, 300) for _ in range(40)], start=s * 100)
    # bad page: the rule glued a '1' onto most values
    bad = [rnd.randint(20, 300) * 10 + 1 for _ in range(30)]
    recs += _page_col("bad", bad, start=900)
    _, vs = dg.check_distributions(recs, [V("x")], P)
    f, heat = dg.check_trailing_one(recs, [V("x")], vs, P)
    groups = [x for x in f if x["check"] == "trailing1"]
    assert len(groups) == 1 and groups[0]["ref"]["stem"] == "bad"
    cells = [x for x in f if x["check"] == "trailing1_cell"]
    assert cells and all(c["ref"]["stem"] == "bad" for c in cells)
    c = cells[0]
    assert c["suggest"]["value"] == str(int(c["value"]) // 10)


# ── confirmations + adjudication ────────────────────────────────────────────

def test_confirmation_holds_only_while_text_unchanged():
    f = {"check": "outlier", "variable": "x", "ref": val(298597, idx=7)}
    fid = dg.flag_id(f)
    open_, conf = dg.apply_confirmations([f], {fid: {"value_text": "298597"}})
    assert conf == [f] and open_ == []
    f2 = dict(f, ref=val(298591, idx=7))                       # value edited
    open_, conf = dg.apply_confirmations([f2], {fid: {"value_text": "298597"}})
    assert open_ == [f2] and conf == []


def test_history_counts_corrected_confirmed_open():
    a = {"check": "outlier", "variable": "x", "ref": val(1, idx=1)}
    b = {"check": "identity", "variable": "t", "ref": val(2, idx=2)}
    c = {"check": "outlier", "variable": "x", "ref": val(3, idx=3)}
    h = dg.update_history({}, [a, b, c], [], "t1", {"open": 3})
    # run 2: a was fixed (gone), c confirmed genuine, b still open
    h = dg.update_history(h, [b], [c], "t2", {"open": 1})
    rows = {r["check"]: r for r in dg.adjudication(h, [b], [c])}
    assert rows["outlier"] == {"check": "outlier", "flagged": 2, "corrected": 1,
                               "confirmed": 1, "open": 0}
    assert rows["identity"]["open"] == 1 and rows["identity"]["corrected"] == 0
    assert len(h["runs"]) == 2


# ── endpoint: end to end on a tiny project ──────────────────────────────────

def _cell(label, x1, y1, x2, y2, sr, sc, rows_text):
    rows = [{"n": i + 1, "y0": y1 + i * 20, "y1": y1 + (i + 1) * 20, "human": t}
            for i, t in enumerate(rows_text)]
    return {"label": label, "points": [[x1, y1], [x2, y2]], "shape_type": "rectangle",
            "flags": {}, "super_row": sr, "super_column": sc, "table": 0,
            "row_struct": {"rows": rows}}


@pytest.fixture()
def ds_proj(client):
    name = f"_pytest_diag_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", ["text_cell", "numerical_cell"])
    pdir = PROJECTS_ROOT / name
    names = ["Alpha", "Beta", "Gamma", "Delta", "Összesen"]
    # Delta: 401+5 ≠ 45 — only a=40 (drop the trailing 1) repairs it; a
    # one-digit difference like 41 vs 40 would be ambiguous (3 cells could
    # each be the slip) and correctly gets no culprit
    a = ["10", "20", "30", "401", "100"]
    b = ["5", "5", "5", "5", "20"]
    t = ["15", "25", "35", "45", "120"]
    shapes = [_cell("text_cell", 0, 0, 100, 100, 1, 1, names),
              _cell("numerical_cell", 100, 0, 200, 100, 1, 2, a),
              _cell("numerical_cell", 200, 0, 300, 100, 1, 3, b),
              _cell("numerical_cell", 300, 0, 400, 100, 1, 4, t)]
    (pdir / "annotations" / "p1.json").write_text(json.dumps(
        {"imagePath": "p1.jpg", "imageWidth": 400, "imageHeight": 120,
         "flags": {}, "shapes": shapes}), encoding="utf-8")
    decl = {"name": "main", "scope": {"labels": ["text_cell", "numerical_cell"]},
            "record": {"unit": "internal_row", "exclude_keys": ["osszesen"],
                       "key": {"slot": 1, "column": 1, "dtype": "text"}},
            "variables": [{"name": "place", "column": 1, "dtype": "text"},
                          {"name": "a", "column": 2, "dtype": "int"},
                          {"name": "b", "column": 3, "dtype": "int"},
                          {"name": "t", "column": 4, "dtype": "int"}],
            "identities": [{"total": "t", "parts": ["a", "b"]}],
            "totals": {"row_pattern": "osszesen"}}
    (pdir / "datasets").mkdir()
    (pdir / "datasets" / "main.dataset.json").write_text(json.dumps(decl), encoding="utf-8")
    try:
        yield name, str(pdir / "annotations")
    finally:
        shutil.rmtree(pdir, ignore_errors=True)


def test_diagnose_endpoint_phase2_confirm_and_history(client, ds_proj):
    name, folder = ds_proj
    r = client.post(f"/api/dataset/main/diagnose?folder={folder}", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    groups = {g["check"]: g for g in d["groups"]}
    ident = groups["identity"]["items"][0]
    assert ident["fix"]["value"] == "40" and ident["fix"]["row_i"] == 3
    assert "trailing '1'" in ident["detail"]
    q = d["quality"]
    assert q["identities"][0]["mismatch"] == 1
    # 3 numeric variables, one broken → 2/3 match < the 3-match floor: a
    # total over so few variables is reported as uncheckable, never guessed
    assert q["totals"]["unanchored"] == 1 and q["totals"]["anchored"] == 0
    assert any(row["check"] == "identity" for row in q["adjudication"])

    # parse errors are never confirmable
    r = client.post(f"/api/dataset/main/confirm?folder={folder}",
                    json={"flag_id": "parse|a|p1|1|0"})
    assert r.status_code == 400
    # confirm the identity flag as the book's own error → leaves the open list
    r = client.post(f"/api/dataset/main/confirm?folder={folder}",
                    json={"flag_id": ident["flag_id"], "value_text": ident["value_text"]})
    assert r.status_code == 200
    d2 = client.post(f"/api/dataset/main/diagnose?folder={folder}", json={}).json()
    assert "identity" not in {g["check"] for g in d2["groups"]}
    assert d2["quality"]["confirmed_n"] >= 1
    assert len(d2["quality"]["runs"]) == 2


# ── A3: ratio rules ──────────────────────────────────────────────────────────

def _local_lookup(rec, spec):
    assert ":" not in spec, "unit tests use local operands"
    return rec["values"].get(spec), True


def test_ratio_bounds_and_untestable():
    ratios = [{"num": "a", "den": "b", "max": 2.0, "min": 0.5,
               "min_den": 5, "label": "a per b"}]
    records = [
        rec("ok",       a=val(10), b=val(10)),            # 1.0 in bounds
        rec("high",     a=val(100), b=val(10)),           # 10 > max
        rec("low",      a=val(1), b=val(10)),             # 0.1 < min
        rec("tiny_den", a=val(10), b=val(2)),             # den < min_den
        rec("missing",  a=val(None, status="missing"), b=val(10)),
        rec("zero_den", a=val(10), b=val(0)),
    ]
    findings, stats = dg.check_ratios(records, ratios, _local_lookup, P)
    assert [f["rec"]["key"]["text"] for f in findings] == ["high", "low"]
    assert all(f["check"] == "ratio" and f["severity"] == "extreme"
               for f in findings)
    assert "10" in findings[0]["detail"] and "a per b" in findings[0]["group_title"]
    st = stats[0]
    assert (st["ok"], st["violation"], st["untestable"]) == (1, 2, 3)


def test_ratio_anchor_prefers_local_side():
    # numerator remote (not local), denominator local → anchor at denominator
    def lookup(r, spec):
        if spec == "remote_num":
            return val(100, stem="OTHER"), False
        return r["values"].get(spec), True
    records = [rec("k", b=val(10))]
    findings, _ = dg.check_ratios(
        records, [{"num": "remote_num", "den": "b", "max": 2}], lookup, P)
    assert findings and findings[0]["ref"]["stem"] == "p1"   # the local cell


def test_ratio_unjoined_remote_is_untestable():
    def lookup(r, spec):
        if ":" in spec:
            return None, False                    # no row with this key there
        return r["values"].get(spec), True
    records = [rec("k", b=val(10))]
    findings, stats = dg.check_ratios(
        records, [{"num": "other:x", "den": "b", "max": 1}], lookup, P)
    assert not findings and stats[0]["untestable"] == 1
