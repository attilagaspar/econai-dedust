"""Table type by header (knowledge_base/10, scope.header): a declaration
whose scope names header patterns covers, on every page, the lattice tables
whose header row matches — the table id is a position, not a type — and a
first table without header cells inherits the previous page's verdict. The
coverage report lists the tables no declaration covers and those two cover."""
import json

import pytest

from app import server as srv


def _cell(sr, sc, text, table=0, label="table_cell"):
    sh = {"label": label, "points": [[sc * 100, sr * 60 + table * 1000], [sc * 100 + 90, sr * 60 + 50 + table * 1000]],
          "shape_type": "rectangle", "flags": {}, "super_row": sr, "super_column": sc, "table": table}
    if text:
        sh["pdf_text"] = text
    return sh


def _table(table, headers, rows):
    """headers: [text per column]; rows: [[text per column], ...]; header row 1."""
    out = [_cell(1, c + 1, h, table, "table_header") for c, h in enumerate(headers)]
    for r, vals in enumerate(rows, start=2):
        out += [_cell(r, c + 1, v, table) for c, v in enumerate(vals)]
    return out


H_EGYENI = ["Királyi törvényszék", "A bejegyzést rendelő végzés kelte", "A czég szószerinti szövege",
            "A főtelep", "A c z é g  b i r t o k o s a", "Czégvezetők", "Jegyzet"]
H_TARSAS = ["Királyi törvényszék", "A bejegyzést rendelő végzés kelte", "A czég szószerinti szövege",
            "A főtelep", "Czégvezetők és felszámoló czégvezetők", "A társaság jogviszonyai", "Jegyzet"]
H_CSOD = ["Neve, állása és lakása a bukónak", "Csődbiróság", "Perügyelő és tömeggondnok", "Bejelentési határidő"]


def _decl(name, header, key_col, variables, tables=None):
    d = {"name": name, "version": 1,
         "scope": {"labels": ["table_cell"], "pattern": "1", "tables": tables or [0]},
         "record": {"unit": "lattice_row", "key": {"slot": 1, "column": key_col, "dtype": "text"}},
         "variables": [{"name": n, "column": c} for n, c in variables]}
    if header is not None:
        d["scope"]["header"] = header
    return d


@pytest.fixture()
def book(tmp_path):
    ann = tmp_path / "proj" / "annotations"; ann.mkdir(parents=True)
    dsd = tmp_path / "proj" / "datasets"; dsd.mkdir()
    pages = {
        # p1: sole traders (table 0) over companies (table 1)
        "p1": _table(0, H_EGYENI, [["Pesti tvsz", "1876. jun. 1.", "Zinner Móritz", "Pest", "Zinner M. kereskedő", "", ""],
                                   ["„", "1876. jun. 2.", "Glück Sámuel", "Tokaj", "Glück S.", "", ""]])
            + _table(1, H_TARSAS, [["Pesti tvsz", "1876. jun. 3.", "Első pesti gőzmalom", "Pest", "", "Részvénytársaság", ""]]),
        # p2: bankruptcies first (table 0), then companies (table 1) — the SAME type as p1's table 1 sits at another id
        "p2": _table(0, H_CSOD, [["Kis János, Pest", "Pesti tvsz", "Nagy Pál", "1876. aug. 1."]])
            + _table(1, H_TARSAS, [["Szegedi tvsz", "1876. jun. 9.", "Szegedi takarékpénztár", "Szeged", "", "Alapszabály", ""]]),
        # p3: a table continued over the page break: no header cells at all
        "p3": [_cell(2, c + 1, v, 0) for c, v in enumerate(["Szegedi tvsz", "1876. jun. 10.", "Tisza malom", "Szeged", "", "Közkereseti", ""])],
        # p4: a table of a type no declaration knows
        "p4": _table(0, ["Védjegy", "Tulajdonos", "Bejelentve"], [["csillag", "X. Y.", "1876"]]),
    }
    for stem, shapes in pages.items():
        (ann / f"{stem}.json").write_text(json.dumps({"version": "5.0.1", "flags": {}, "shapes": shapes,
                                                      "imagePath": f"{stem}.jpg", "imageWidth": 900, "imageHeight": 2200},
                                                     ensure_ascii=False), encoding="utf-8")
    decls = [
        _decl("egyeni", {"5": "birtokos"}, 3, [("court", 1), ("name", 3), ("owner", 5)]),
        _decl("tarsas", {"6": "jogviszony"}, 3, [("court", 1), ("name", 3), ("legal", 6)]),
        _decl("csod", {"2": "csod"}, 1, [("debtor", 1), ("court", 2)]),
    ]
    for d in decls:
        (dsd / f"{d['name']}.dataset.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return ann


def _names(client, ann, name, var="name"):
    d = client.get(f"/api/dataset/{name}/build", params={"folder": str(ann)}).json()
    return [(r["key"]["stem"], r["table"], r["values"][var]["text"]) for r in d["records"]]


def test_tables_are_chosen_by_header_not_by_id(client, book):
    assert _names(client, book, "egyeni") == [("p1", 0, "Zinner Móritz"), ("p1", 0, "Glück Sámuel")]
    # companies: p1 table 1, p2 table 1, and p3 table 0 by inheritance (no header, previous page ended in a company table)
    assert _names(client, book, "tarsas") == [("p1", 1, "Első pesti gőzmalom"), ("p2", 1, "Szegedi takarékpénztár"), ("p3", 0, "Tisza malom")]
    assert _names(client, book, "csod", "debtor") == [("p2", 0, "Kis János, Pest")]
    # the inheritance is a finding, pages without a matching table are not
    decl = srv._ds_load_decl(str(book), "tarsas")
    _recs, findings, stats = srv._ds_build(book, decl)
    assert [f["stem"] for f in findings if "inherited" in f["detail"]] == ["p3"]
    assert not [f for f in findings if "has no lattice cells" in f["detail"]]
    decl = srv._ds_load_decl(str(book), "egyeni")
    _recs, findings, _ = srv._ds_build(book, decl)
    assert not [f for f in findings if "inherited" in f["detail"]]   # p3 follows a company table, not a sole-trader one


def test_coverage_lists_unmatched_and_doubly_covered_tables(client, book):
    c = client.get("/api/datasets/coverage", params={"folder": str(book)}).json()
    assert c["pages"] == 4 and c["tables"] == 6
    assert c["per_declaration"] == {"egyeni": 1, "tarsas": 3, "csod": 1}
    assert c["n_multi"] == 0 and c["n_unmatched"] == 1
    u = c["unmatched"][0]
    assert (u["stem"], u["table"], u["columns"], u["rows"]) == ("p4", 0, 3, 2)
    assert u["header"][str(1)].startswith("vedjegy") or u["header"][1].startswith("vedjegy")
    # a declaration that matches both firm registers shows up as double coverage, inheritance is starred
    (book.parent / "datasets" / "all7.dataset.json").write_text(json.dumps(
        _decl("all7", {"1": "torvenyszek"}, 3, [("court", 1), ("name", 3)])), encoding="utf-8")
    c = client.get("/api/datasets/coverage", params={"folder": str(book)}).json()
    assert c["per_declaration"]["all7"] == 4 and c["n_multi"] == 4
    p3 = next(m for m in c["multi"] if m["stem"] == "p3")
    assert sorted(p3["declarations"]) == ["all7*", "tarsas*"]


def test_header_scope_is_validated_and_table_ids_still_work_without_it(client, book):
    bad = srv.DatasetDecl(**_decl("bad", {"x": "a", "5": "("}, 3, [("name", 3)]))
    probs = srv._ds_decl_problems(bad)
    assert any("'x'" in p for p in probs) and any("bad pattern" in p for p in probs)
    assert srv._ds_decl_problems(srv.DatasetDecl(**_decl("ok", {"5": "birtokos"}, 3, [("name", 3)]))) == []
    # no header: scope.tables as before — table 0 on every page, whatever it is
    (book.parent / "datasets" / "byid.dataset.json").write_text(json.dumps(
        _decl("byid", None, 3, [("name", 3)])), encoding="utf-8")
    assert [x[0] for x in _names(client, book, "byid")] == ["p1", "p1", "p2", "p3", "p4"]
