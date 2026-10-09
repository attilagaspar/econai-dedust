"""
Dataset diagnostics, Phase 2 (knowledge_base/10_dataset_layer.md, "Phase 2
specification"). Pure functions over the records the dataset builder
assembles; the server turns the returned findings into report items.

Ground rules this module implements:
  * flags route values to a human — nothing here edits data;
  * every finding carries its method + parameters;
  * skewed positive variables are tested on a log scale;
  * zero-inflated variables are tested on their non-zero values;
  * group tests report an effect size next to the (Bonferroni-adjusted) p.

Checks:
  A1 identities   — declared row identities (total column = sum of parts)
  A2 totals       — printed total rows vs the records they sum (best-suffix
                    anchoring: the segment a total covers is found from the
                    numbers themselves, not from header rows, which are often
                    lost as separator cells)
  B  extremes     — top/bottom N per variable (dashboard panels, not findings)
  C  iqr          — Tukey fences, 1.5× = mild, 3× = extreme
  D  mad          — robust z (Iglewicz–Hoaglin), threshold 3.5
  E  digits       — value has ≥k more integer digits than the variable's median
  F  trailing1    — excess of values ending in "1" per physical column per
                    page (column rules read as a digit) + cell candidates
  G  esd          — Generalized ESD (Rosner), optional
"""
from __future__ import annotations

import math
import re

DEFAULTS = {
    "top_n": 10,
    "iqr_mild": 1.5,
    "iqr_extreme": 3.0,
    "mad_z": 3.5,
    "zero_share": 0.5,          # above this share of zeros → test non-zeros only
    "min_n": 12,                # fewer usable values → no distribution tests
    "report_mild": False,       # mild-only IQR flags: counted, not listed
    "digit_excess": 2,
    # calibrated on foldbirtok1935 (2026-10-07): last digits of values with
    # ≥2 digits are uniform over 1–9 (shares 0.104–0.119), so 2-digit values
    # can be tested — that is where "21 → 2" artifacts live
    "trailing_min_digits": 2,
    "trailing_min_n": 12,       # values per (variable, page) group
    "trailing_alpha": 0.01,     # family-wise, Bonferroni over groups
    "trailing_min_ratio": 2.0,  # share of 1s must be ≥ this × expected (1/9)
    "esd": False,
    "esd_max_frac": 0.05,
    "esd_alpha": 0.05,
    "totals_min_match": 0.5,    # share of variables that must match to anchor
    "tol": 0.51,                # numeric tolerance for sums (integer printing)
    "hist_bins": 24,
}


def params(decl_diag: dict | None) -> dict:
    p = dict(DEFAULTS)
    for k, v in (decl_diag or {}).items():
        if k in p and v is not None:
            p[k] = v
    return p


# ── small numeric helpers (no scipy dependency in the server image) ─────────

def _quantile(sorted_xs, q):
    n = len(sorted_xs)
    if n == 0:
        return None
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def _median(xs):
    return _quantile(sorted(xs), 0.5)


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X ≥ k) for X ~ Binomial(n, p), exact (n is small: one page column)."""
    if k <= 0:
        return 1.0
    total = 0.0
    for i in range(k, n + 1):
        total += math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
    return min(1.0, total)


def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 300, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > FPMIN else FPMIN)
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > FPMIN else FPMIN)
        c = 1.0 + aa / c
        c = c if abs(c) > FPMIN else FPMIN
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > FPMIN else FPMIN)
        c = 1.0 + aa / c
        c = c if abs(c) > FPMIN else FPMIN
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def _betainc(a, b, x):
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log(1 - x))
    bt = math.exp(lbt)
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1 - x) / b


def t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    tail = 0.5 * _betainc(df / 2, 0.5, x)
    return 1 - tail if t > 0 else tail


def t_ppf(q: float, df: float) -> float:
    """Inverse Student-t CDF by bisection (accuracy ~1e-9 — plenty here)."""
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ── value helpers ────────────────────────────────────────────────────────────

def int_digits(x) -> int:
    return len(str(int(abs(x)))) if x is not None else 0


def last_digit(text: str):
    """Last printed digit of a cell's text (trailing punctuation ignored)."""
    m = re.search(r"(\d)\D*$", text or "")
    return int(m.group(1)) if m else None


def one_char_apart(a: str, b: str) -> bool:
    """True when two digit strings differ by one substitution, insertion or
    deletion — the footprint of a single OCR slip."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    if abs(la - lb) != 1:
        return False
    s, l = (a, b) if la < lb else (b, a)
    for i in range(len(l)):
        if l[:i] + l[i + 1:] == s:
            return True
    return False


def _num_str(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and x == int(x):
        x = int(x)
    return str(x)


def slip_note(current, wanted) -> str:
    """Name the slip when the fix is 'drop a trailing 1' (column rule read
    as a digit) — the arithmetic-proven form of the trailing-1 check."""
    c, w = _num_str(current), _num_str(wanted)
    if len(c) == len(w) + 1 and c.endswith("1") and c[:-1] == w:
        return " — a column rule read as a trailing '1'"
    return ""


def diff_hint(printed, computed) -> str:
    """A short human hint for why two numbers that should agree don't."""
    if printed is None or computed is None:
        return ""
    if printed == 0 or computed == 0:
        return ""
    d = abs(printed - computed)
    r = printed / computed
    for k in (10, 100, 1000):
        if abs(r - k) < 1e-9 or abs(r - 1 / k) < 1e-9:
            return f"factor {k} — a digit added or dropped"
    if one_char_apart(_num_str(printed), _num_str(computed)):
        return "one character apart — a single OCR slip"
    if d == int(d) and int(d) % 9 == 0:
        return "difference divisible by 9 — possibly transposed digits"
    return ""


# ── A1: declared row identities ──────────────────────────────────────────────

def check_identities(records, identities, p):
    """identities: [{"total": var, "parts": [var...], "label": str?}].
    Missing parts (dash) count as 0; a structurally absent or unparseable
    part makes the identity untestable for that record (reported elsewhere).
    Returns (findings, stats_by_identity)."""
    findings, stats = [], []
    for ident in identities or []:
        tot, parts = ident["total"], list(ident["parts"])
        lbl = ident.get("label") or f"{tot} = " + " + ".join(parts)
        st = {"identity": lbl, "total": tot, "ok": 0, "mismatch": 0, "untestable": 0}
        for rec in records:
            vals = rec["values"]
            tv = vals.get(tot)
            if not tv or tv["status"] != "ok":
                st["untestable"] += 1
                continue
            if any(vals.get(q, {}).get("status") in (None, "error", "absent") for q in parts):
                st["untestable"] += 1
                continue
            s = sum((vals[q]["value"] or 0) for q in parts)
            if abs(s - tv["value"]) <= p["tol"]:
                st["ok"] += 1
                continue
            st["mismatch"] += 1
            # which single cell, changed by one OCR slip, would make it hold?
            suggest = None
            cands = []
            need_total = s
            if one_char_apart(_num_str(tv["value"]), _num_str(need_total)):
                cands.append((tot, tv, need_total))
            for q in parts:
                qv = vals[q]
                need = tv["value"] - (s - (qv["value"] or 0))
                if need >= 0 and one_char_apart(_num_str(qv["value"] or 0), _num_str(need)):
                    cands.append((q, qv, need))
            note = ""
            if len(cands) == 1:
                var, vd, need = cands[0]
                suggest = {"variable": var, "value": _num_str(need),
                           "stem": vd.get("stem"), "idx": vd.get("idx"),
                           "row_i": vd.get("row_i")}
                note = slip_note(vd["value"] or 0, need)
            hint = diff_hint(tv["value"], s)
            detail = (f"{tot} = {_num_str(tv['value'])} but its parts sum to "
                      f"{_num_str(s)} (difference {_num_str(round(tv['value'] - s, 2))})")
            if hint:
                detail += f" — {hint}"
            if suggest:
                detail += (f". Likely culprit: {suggest['variable']} reads "
                           f"{_num_str(vals[suggest['variable']]['value'] or 0)}, "
                           f"should be {suggest['value']}{note}")
            findings.append({"check": "identity", "variable": tot, "severity": "error",
                             "ref": tv, "rec": rec, "detail": detail,
                             "group_title": f"Row identity broken: {lbl}",
                             "method": "identity", "params": {"tol": p["tol"]},
                             "suggest": suggest if suggest and suggest["variable"] == tot
                             else None,
                             "culprit": suggest})
        stats.append(st)
    return findings, stats


# ── A2: printed total rows (best-suffix anchoring) ───────────────────────────

def check_totals(rows, totals_spec, variables, p):
    """rows: ALL builder rows in reading order, excluded ones flagged
    rec['excluded']=True. A row whose key matches totals_spec['row_pattern']
    is a printed total; it is compared with the sum of the K data rows above
    it (since the previous total row), K chosen as the suffix length that
    makes the most variables agree. A total anchors when ≥ totals_min_match of
    its comparable variables agree exactly — at ~10+ variables a chance
    majority is practically impossible, so the remaining disagreements are
    real errors (in the total cell or in one of the summed values)."""
    if not totals_spec or not totals_spec.get("row_pattern"):
        return [], {"anchored": 0, "unanchored": 0, "cells_ok": 0, "cells_bad": 0}
    rx = re.compile(totals_spec["row_pattern"], re.IGNORECASE)
    min_frac = totals_spec.get("min_match")
    if min_frac is None:
        min_frac = p["totals_min_match"]
    numvars = [v for v in variables
               if v.dtype in ("int", "number") and getattr(v, "stats", True)]
    findings = []
    st = {"anchored": 0, "unanchored": 0, "cells_ok": 0, "cells_bad": 0}
    since = []
    for rec in rows:
        ktext = rec["key"].get("text") or ""
        kfold = rec["key"].get("fold") or ktext.lower()
        if rec.get("excluded") and (rx.search(ktext) or rx.search(kfold)):
            tv = {v.name: rec["values"][v.name] for v in numvars
                  if rec["values"].get(v.name, {}).get("status") == "ok"}
            best_m, best_k, best_sums = 0, 0, None
            sums = {vn: 0.0 for vn in tv}
            for k in range(1, len(since) + 1):
                r2 = since[-k]
                for vn in tv:
                    x = r2["values"][vn]["value"]
                    if isinstance(x, (int, float)):
                        sums[vn] += x
                m = sum(1 for vn in tv if abs(sums[vn] - tv[vn]["value"]) <= p["tol"])
                if m > best_m:
                    best_m, best_k, best_sums = m, k, dict(sums)
            need = max(3, math.ceil(min_frac * len(tv))) if tv else 1
            if tv and best_k and best_m >= need:
                st["anchored"] += 1
                seg = since[-best_k:]
                for vn, tval in tv.items():
                    s = best_sums[vn]
                    if abs(s - tval["value"]) <= p["tol"]:
                        st["cells_ok"] += 1
                        continue
                    st["cells_bad"] += 1
                    delta = tval["value"] - s
                    culprit = None
                    cands = []
                    if one_char_apart(_num_str(tval["value"]), _num_str(s)):
                        cands.append(("the total itself", tval, s))
                    for r2 in seg:
                        vd = r2["values"][vn]
                        if vd["status"] != "ok":
                            continue
                        want = (vd["value"] or 0) + delta
                        if want >= 0 and one_char_apart(_num_str(vd["value"]), _num_str(want)):
                            cands.append((r2["key"].get("text") or "?", vd, want))
                    note = ""
                    if len(cands) == 1:
                        who, vd, want = cands[0]
                        culprit = {"variable": vn, "value": _num_str(want), "who": who,
                                   "stem": vd.get("stem"), "idx": vd.get("idx"),
                                   "row_i": vd.get("row_i")}
                        note = slip_note(vd["value"], want)
                        culprit_now = _num_str(vd["value"])
                    hint = diff_hint(tval["value"], s)
                    detail = (f"printed total {_num_str(tval['value'])} ≠ sum of the "
                              f"{best_k} record(s) above it = {_num_str(round(s, 2))} "
                              f"(difference {_num_str(round(delta, 2))})")
                    if hint:
                        detail += f" — {hint}"
                    if culprit:
                        detail += (f". Likely culprit: {culprit['who']} reads "
                                   f"{culprit_now}, should be {culprit['value']}{note}")
                    findings.append({
                        "check": "totals", "variable": vn, "severity": "error",
                        "ref": tval, "rec": rec, "detail": detail,
                        "group_title": f"{vn}: printed total row disagrees with its records",
                        "method": "totals_best_suffix",
                        "params": {"min_match": min_frac, "tol": p["tol"],
                                   "anchored_k": best_k,
                                   "matched": f"{best_m}/{len(tv)}"},
                        "culprit": culprit,
                        "suggest": ({"value": culprit["value"]}
                                    if culprit and culprit["who"] == "the total itself"
                                    else None)})
            else:
                st["unanchored"] += 1
                findings.append({
                    "check": "totals_unanchored", "variable": None, "severity": "info",
                    "ref": rec["key"], "rec": rec,
                    "detail": (f"could not tell which records this total covers "
                               f"(best match {best_m}/{len(tv)} variables) — missing "
                               f"rows, a merged district, or many errors"),
                    "group_title": "Printed total rows that could not be checked",
                    "method": "totals_best_suffix",
                    "params": {"min_match": min_frac}})
            since = []
        elif rec.get("excluded"):
            continue                       # header rows: neither data nor boundary
        else:
            since.append(rec)
    return findings, st


# ── A3: declared ratio rules (operands joinable across datasets) ─────────────

def check_ratios(records, ratios, lookup, p):
    """ratios: [{"num", "den", "min", "max", "min_den", "label"}]. num/den
    are variable names, or references into another dataset ("dataset:var")
    or project ("project/dataset:var") — those are resolved by `lookup(rec,
    spec) → (value-dict | None, anchor_is_local)`, joining on the resolved
    entity key id (the server supplies it; this module stays IO-free). A
    record where either side is missing, unparsed or unjoined is untestable —
    those states have their own findings already. The finding anchors at
    whichever operand's cell is in the current project so the crop and the
    jump work. Returns (findings, stats_by_ratio)."""
    findings, stats = [], []
    for rt in ratios or []:
        lbl = rt.get("label") or f"{rt['num']} / {rt['den']}"
        lo, hi = rt.get("min"), rt.get("max")
        min_den = rt.get("min_den") or 0
        st = {"ratio": lbl, "ok": 0, "violation": 0, "untestable": 0}
        for rec in records:
            nvd, nloc = lookup(rec, rt["num"])
            dvd, dloc = lookup(rec, rt["den"])
            if (not nvd or nvd.get("status") != "ok"
                    or not dvd or dvd.get("status") != "ok"
                    or dvd["value"] <= 0 or dvd["value"] < min_den):
                st["untestable"] += 1
                continue
            r = nvd["value"] / dvd["value"]
            if (hi is not None and r > hi) or (lo is not None and r < lo):
                st["violation"] += 1
                anchor = nvd if nloc else (dvd if dloc else nvd)
                bounds = f"[{_num_str(lo) if lo is not None else '…'}, " \
                         f"{_num_str(hi) if hi is not None else '…'}]"
                findings.append({
                    "check": "ratio", "variable": rt["num"], "severity": "extreme",
                    "ref": anchor, "rec": rec,
                    "detail": f"{rt['num']} = {_num_str(nvd['value'])} over "
                              f"{rt['den']} = {_num_str(dvd['value'])} → "
                              f"{r:.3g}, outside {bounds}",
                    "group_title": f"Ratio outside bounds: {lbl}",
                    "method": "ratio",
                    "params": {"min": lo, "max": hi, "min_den": min_den}})
            else:
                st["ok"] += 1
        stats.append(st)
    return findings, stats


# ── B–E, G: per-variable distribution checks ─────────────────────────────────

def _scale_of(var, values):
    s = getattr(var, "scale", None)
    if s in ("log", "raw"):
        return s
    return "log" if values and min(values) >= 0 else "raw"


def _tf(x, scale):
    return math.log1p(x) if scale == "log" else x


def _histogram(tvals, bins):
    if not tvals:
        return {"edges": [], "counts": []}
    lo, hi = min(tvals), max(tvals)
    if hi == lo:
        return {"edges": [lo, hi], "counts": [len(tvals)]}
    w = (hi - lo) / bins
    counts = [0] * bins
    for x in tvals:
        i = min(bins - 1, int((x - lo) / w))
        counts[i] += 1
    return {"edges": [lo + i * w for i in range(bins + 1)], "counts": counts}


def generalized_esd(tvals, max_out, alpha):
    """Rosner's generalized ESD. Returns indices (into tvals) of outliers."""
    n = len(tvals)
    if n < 15 or max_out < 1:
        return []
    idx = list(range(n))
    xs = list(tvals)
    removed, n_out = [], 0
    for i in range(1, max_out + 1):
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0
        if sd == 0:
            break
        j = max(range(len(xs)), key=lambda k: abs(xs[k] - m))
        R = abs(xs[j] - m) / sd
        nn = n - i + 1
        pp = 1 - alpha / (2 * nn)
        t = t_ppf(pp, nn - 2)
        lam = (nn - 1) * t / math.sqrt((nn - 2 + t * t) * nn)
        removed.append(idx[j])
        if R > lam:
            n_out = i
        del xs[j]
        del idx[j]
    return removed[:n_out]


def check_distributions(records, variables, p):
    """C/D/E/G findings + per-variable dashboard stats (incl. B extremes and
    histograms). Returns (findings, var_stats)."""
    findings, var_stats = [], {}
    for var in variables:
        if var.dtype not in ("int", "number") or not getattr(var, "stats", True):
            continue
        pts = [(rec["values"][var.name]["value"], rec["values"][var.name], rec)
               for rec in records
               if rec["values"].get(var.name, {}).get("status") == "ok"
               and isinstance(rec["values"][var.name]["value"], (int, float))]
        n_missing = sum(1 for rec in records
                        if rec["values"].get(var.name, {}).get("status") == "missing")
        vals = [x for x, _, _ in pts]
        vs = {"variable": var.name, "label": var.label, "n": len(vals),
              "n_missing": n_missing,
              "n_zero": sum(1 for x in vals if x == 0)}
        vs["zero_share"] = round(vs["n_zero"] / len(vals), 3) if vals else None
        scale = _scale_of(var, vals)
        vs["scale"] = scale
        # B — extremes (dashboard panel)
        order = sorted(pts, key=lambda t: t[0])
        tn = int(p["top_n"])
        vs["top"] = [_ref(v, r, x) for x, v, r in reversed(order[-tn:])]
        vs["bottom"] = [_ref(v, r, x) for x, v, r in order[:tn]]
        if len(order) >= 2 and order[-2][0] > 0:
            vs["top_gap"] = round(order[-1][0] / order[-2][0], 2)
        # test population: non-zeros when zero-inflated
        zero_inflated = vs["zero_share"] is not None and vs["zero_share"] > p["zero_share"]
        vs["zero_inflated"] = zero_inflated
        test = [(x, v, r) for x, v, r in pts if not (zero_inflated and x == 0)]
        tvals = [_tf(x, scale) for x, _, _ in test]
        vs["hist"] = _histogram(tvals, int(p["hist_bins"]))
        vs["tests"] = {"iqr_mild": 0, "iqr_extreme": 0, "mad": 0, "digits": 0, "esd": 0}
        if len(test) < p["min_n"]:
            vs["skipped"] = f"only {len(test)} usable values (min {p['min_n']})"
            var_stats[var.name] = vs
            continue
        st = sorted(tvals)
        q1, q3 = _quantile(st, 0.25), _quantile(st, 0.75)
        iqr = q3 - q1
        med = _quantile(st, 0.5)
        mad = _median([abs(x - med) for x in tvals])
        vs.update({"q1": q1, "q3": q3, "median_t": med, "mad_t": mad,
                   "fences": {"mild": [q1 - p["iqr_mild"] * iqr, q3 + p["iqr_mild"] * iqr],
                              "extreme": [q1 - p["iqr_extreme"] * iqr,
                                          q3 + p["iqr_extreme"] * iqr]}})
        vs["mad_zero"] = mad == 0
        nz = [x for x, _, _ in test if x != 0]
        med_digits = _median([int_digits(x) for x in nz]) if nz else None
        vs["median_digits"] = med_digits
        esd_set = set()
        if p.get("esd") or getattr(var, "esd", False):
            mx = max(1, int(p["esd_max_frac"] * len(tvals)))
            esd_set = set(generalized_esd(tvals, mx, p["esd_alpha"]))
        flagged_t = []
        for i, ((x, v, rec), t) in enumerate(zip(test, tvals)):
            fired, sev = [], None
            if iqr > 0:
                if t < vs["fences"]["extreme"][0] or t > vs["fences"]["extreme"][1]:
                    fired.append(f"IQR {p['iqr_extreme']}× (extreme)")
                    sev = "extreme"
                    vs["tests"]["iqr_extreme"] += 1
                elif t < vs["fences"]["mild"][0] or t > vs["fences"]["mild"][1]:
                    fired.append(f"IQR {p['iqr_mild']}× (mild)")
                    sev = "mild"
                    vs["tests"]["iqr_mild"] += 1
            if mad > 0:
                z = 0.6745 * (t - med) / mad
                if abs(z) > p["mad_z"]:
                    fired.append(f"MAD z={z:+.1f}")
                    sev = sev or "mild"
                    vs["tests"]["mad"] += 1
            if i in esd_set:
                fired.append(f"Rosner ESD (α={p['esd_alpha']})")
                sev = sev or "mild"
                vs["tests"]["esd"] += 1
            # heavy-tailed data puts ~2% of every variable past the mild fence;
            # listing those buries the real errors. Mild-only hits are counted
            # (dashboard) and listed only on request.
            listable = (sev == "extreme" or len(fired) > 1
                        or not fired[0].startswith("IQR") if fired else False)
            if fired and (listable or p.get("report_mild")):
                flagged_t.append(t)
                findings.append({
                    "check": "outlier", "variable": var.name, "severity": sev,
                    "ref": v, "rec": rec, "value": x, "t": t,
                    "detail": (f"{_num_str(x)} — " + "; ".join(fired)
                               + (" (log scale)" if scale == "log" else "")
                               + (" (non-zero values only)" if zero_inflated else "")),
                    "group_title": f"{var.name}: statistical outliers",
                    "method": "iqr+mad" + ("+esd" if esd_set else ""),
                    "params": {"scale": scale, "iqr_mild": p["iqr_mild"],
                               "iqr_extreme": p["iqr_extreme"], "mad_z": p["mad_z"],
                               "zero_inflated": zero_inflated}})
            # E — digit length
            if med_digits and x != 0 and int_digits(x) >= med_digits + p["digit_excess"]:
                vs["tests"]["digits"] += 1
                findings.append({
                    "check": "digits", "variable": var.name, "severity": "mild",
                    "ref": v, "rec": rec, "value": x,
                    "detail": (f"{_num_str(x)} has {int_digits(x)} digits; this "
                               f"variable typically has {med_digits:g} — glued cells "
                               f"or a column shift?"),
                    "group_title": f"{var.name}: values much longer than usual",
                    "method": "digit_length",
                    "params": {"digit_excess": p["digit_excess"],
                               "median_digits": med_digits}})
        vs["flagged_t"] = flagged_t
        var_stats[var.name] = vs
    return findings, var_stats


def _ref(v, rec, x):
    return {"value": x, "text": v.get("text"), "stem": v.get("stem"),
            "idx": v.get("idx"), "row_i": v.get("row_i"), "row_n": v.get("row_n"),
            "y0": v.get("y0"), "y1": v.get("y1"),
            "key": (rec.get("key") or {}).get("text")}


# ── F: trailing "1" (column rule read as a digit) ────────────────────────────

def check_trailing_one(records, variables, var_stats, p):
    """Group by physical column per page = (variable, stem). Test the share of
    values ending in 1 among values ending in 1–9 (zeros excluded: rounding
    piles values on 0) against 1/9, values with ≥ trailing_min_digits digits
    only. Groups: Bonferroni over all tested groups + an effect-size floor.

    Cell candidates are listed ONLY inside a flagged page column: there a
    value that ends in 1 and becomes typical once the 1 is dropped (outlier →
    in-fence, or too long → typical length) is very likely the artifact.
    Outside flagged columns the same pattern is mostly genuine large values
    (~1 in 9 of them end in 1), and the exact checks (identities, printed
    totals) already name a trailing-1 culprit when the arithmetic proves it.

    An image-based "rule inside the box edge" signal was tried and dropped:
    on foldbirtok1935 it fired on ~40% of ALL cells (ends-in-1 or not), so
    it carries no evidence (2026-10-07 calibration).
    Returns (findings, heat) where heat[var][stem] = {k, n, share, ...}."""
    groups: dict = {}
    for rec in records:
        for var in variables:
            if var.dtype not in ("int", "number") or not getattr(var, "stats", True):
                continue
            v = rec["values"].get(var.name)
            if not v or v["status"] != "ok" or not isinstance(v["value"], (int, float)):
                continue
            if int_digits(v["value"]) < p["trailing_min_digits"]:
                continue
            d = last_digit(v.get("text") or "")
            if d is None or d == 0:
                continue
            g = groups.setdefault((var.name, v.get("stem")), {"k": 0, "n": 0})
            g["n"] += 1
            if d == 1:
                g["k"] += 1
    tested = [(key, g) for key, g in groups.items() if g["n"] >= p["trailing_min_n"]]
    m = max(1, len(tested))
    expected = 1 / 9
    findings, heat = [], {}
    for (vn, stem), g in groups.items():
        heat.setdefault(vn, {})[stem] = {"k": g["k"], "n": g["n"],
                                        "share": round(g["k"] / g["n"], 3) if g["n"] else None}
    hot = set()
    for (vn, stem), g in tested:
        share = g["k"] / g["n"]
        pval = binom_sf(g["k"], g["n"], expected)
        padj = min(1.0, pval * m)
        heat[vn][stem]["p_adj"] = padj
        if padj < p["trailing_alpha"] and share >= p["trailing_min_ratio"] * expected:
            heat[vn][stem]["flagged"] = True
            hot.add((vn, stem))
            findings.append({
                "check": "trailing1", "variable": vn, "severity": "mild",
                "ref": {"stem": stem, "idx": None}, "rec": None,
                "detail": (f"{g['k']}/{g['n']} values end in 1 ({share:.0%}; expected "
                           f"~11%) — a column rule is probably being read as a '1' on "
                           f"this page (Bonferroni p={padj:.2g} over {m} columns)"),
                "group_title": f"{vn}: too many values ending in 1 on a page",
                "method": "trailing_digit_binomial",
                "params": {"min_digits": p["trailing_min_digits"], "alpha": p["trailing_alpha"],
                           "groups_tested": m, "min_ratio": p["trailing_min_ratio"]}})
    if not hot:
        return findings, heat

    for var in variables:
        vs = var_stats.get(var.name)
        if not vs or "fences" not in vs:
            continue
        scale = vs["scale"]
        lo, hi = vs["fences"]["mild"]
        med_d = vs.get("median_digits")
        for rec in records:
            v = rec["values"].get(var.name)
            if not v or (var.name, v.get("stem")) not in hot:
                continue
            if v["status"] != "ok" or not isinstance(v["value"], (int, float)):
                continue
            x = v["value"]
            if x < 10 or x != int(x) or last_digit(v.get("text") or "") != 1:
                continue
            sx = int(x) // 10
            t, ts = _tf(x, scale), _tf(sx, scale)
            becomes_typical = ((t < lo or t > hi) and lo <= ts <= hi) or \
                (bool(med_d) and int_digits(x) > med_d and int_digits(sx) <= med_d)
            if not becomes_typical:
                continue
            findings.append({
                "check": "trailing1_cell", "variable": var.name, "severity": "mild",
                "ref": v, "rec": rec, "value": x,
                "detail": (f"{v.get('text')} → probably {sx}: this page column has an "
                           f"excess of values ending in 1, and dropping the trailing 1 "
                           f"makes this value typical for {var.name}"),
                "group_title": f"{var.name}: a column rule read as a trailing '1'?",
                "method": "trailing1_candidate",
                "params": {"gated_by": "page-column excess"},
                "suggest": {"value": str(sx)}})
    return findings, heat


# ── flag identity + adjudication bookkeeping ─────────────────────────────────

def flag_id(f: dict) -> str:
    r = f.get("ref") or {}
    return "|".join(str(x) for x in (f["check"], f.get("variable") or "",
                                     r.get("stem") or "", r.get("idx"),
                                     r.get("row_i")))


def value_text_of(f: dict) -> str:
    r = f.get("ref") or {}
    return str(r.get("text") or "")


def apply_confirmations(findings, confirmed: dict):
    """Split findings into (open, confirmed). A confirmation only holds while
    the cell's text is unchanged — edit the value and the flag comes back."""
    open_, conf = [], []
    for f in findings:
        fid = flag_id(f)
        c = confirmed.get(fid)
        if c is not None and c.get("value_text", "") == value_text_of(f):
            conf.append(f)
        else:
            open_.append(f)
    return open_, conf


def update_history(history: dict, open_findings, confirmed_findings, ts: str,
                   summary: dict) -> dict:
    """Adjudication across runs (full-dataset runs only — a page-restricted
    run must not mark everything outside its range as corrected).
    ever:     every flag id ever raised → its check
    resolved: ids that were open before and are no longer flagged nor
              confirmed → corrected (the value changed and passed)."""
    h = dict(history or {})
    ever = dict(h.get("ever") or {})
    resolved = dict(h.get("resolved") or {})
    prev_open = set(h.get("last_open") or [])
    now_open = {flag_id(f): f["check"] for f in open_findings}
    now_conf = {flag_id(f) for f in confirmed_findings}
    for fid, chk in now_open.items():
        ever[fid] = chk
        resolved.pop(fid, None)              # reopened
    for f in confirmed_findings:
        ever[flag_id(f)] = f["check"]
    for fid in prev_open - set(now_open) - now_conf:
        resolved[fid] = {"check": ever.get(fid, fid.split("|", 1)[0]), "ts": ts}
    runs = list(h.get("runs") or [])
    runs.append({"ts": ts, **summary})
    h.update({"ever": ever, "resolved": resolved, "last_open": sorted(now_open),
              "runs": runs[-50:]})
    return h


def adjudication(history: dict, open_findings, confirmed_findings) -> list:
    """Per check: flagged ever / corrected / confirmed genuine / still open."""
    ever = history.get("ever") or {}
    resolved = history.get("resolved") or {}
    rows: dict = {}

    def row(chk):
        return rows.setdefault(chk, {"check": chk, "flagged": 0, "corrected": 0,
                                     "confirmed": 0, "open": 0})
    for fid, chk in ever.items():
        row(chk)["flagged"] += 1
    for fid, r in resolved.items():
        row(r["check"])["corrected"] += 1
    for f in confirmed_findings:
        row(f["check"])["confirmed"] += 1
    for f in open_findings:
        row(f["check"])["open"] += 1
    return sorted(rows.values(), key=lambda r: r["check"])
