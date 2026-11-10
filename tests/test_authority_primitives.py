"""Authority write primitives (knowledge_base/13 §2.2, E1): mint an entity,
attach a slice, add a typed edge — plus the schema additions they rely on:
`id_scheme` (opaque sequential ids), `normalize.strip_tokens` (phrases dropped
anywhere, e.g. legal forms), alias `lang`, `edges[]` with duplicate_of
collapsing to the surviving entity in the resolver.

Runs against a throwaway authority directory (never the git-tracked files).
"""
import json

import pytest

from app import server as srv

FILE = "_smoke_firms.authority.json"


@pytest.fixture()
def auth_dir(tmp_path, monkeypatch):
    d = tmp_path / "authorities"
    d.mkdir()
    (d / FILE).write_text(json.dumps({
        "authority": "_smoke_firms", "version": "0.3",
        "entity_types": ["firm"],
        "id_scheme": {"prefix": "F", "width": 6},
        "normalize": {"strip_tokens": ["részvénytársaság", "r.-t.", "Actiengesellschaft", "A.-G."]},
        "slices_present": [], "counts": {}, "edges": [], "entities": [],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(srv, "AUTHORITIES_DIR", d)
    srv._AUTH_CACHE.clear()
    srv._AUTH_META_CACHE.clear()
    return d


def _read(auth_dir):
    return json.loads((auth_dir / FILE).read_text(encoding="utf-8"))


def _mint(client, name, **kw):
    r = client.post("/api/authority/mint", json={
        "name": FILE,
        "entity": {"name": name, **kw.pop("entity", {})},
        "slice": kw.pop("slice", {"as_of": 1877, "source": "compass_1877",
                                  "provenance": {"project": "compass_1877", "stem": "p1", "group_id": 3},
                                  "seq": 3}),
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_mint_assigns_sequential_opaque_ids_and_first_slice(client, auth_dir):
    a = _mint(client, "Pesti Hengermalom Részvénytársaság",
              entity={"aliases": [{"name": "Pester Walzmühle A.-G.", "lang": "de"}]})
    b = _mint(client, "Első Magyar Általános Biztosító Társaság")
    assert a["entity"]["id"] == "F000001" and b["entity"]["id"] == "F000002"
    assert a["existing_exact"] == [] and b["existing_exact"] == []
    data = _read(auth_dir)
    e = data["entities"][0]
    assert e["type"] == "firm"
    assert e["aliases"] == [{"name": "Pester Walzmühle A.-G.", "source": "econai", "lang": "de"}]
    assert e["slices"][0]["as_of"] == 1877 and e["slices"][0]["seq"] == 3
    assert e["slices"][0]["provenance"]["group_id"] == 3
    assert data["counts"] == {"entities": 2, "aliases": 1, "edges": 0}
    assert data["slices_present"] == [1877]


def test_mint_warns_about_an_existing_exact_name_but_still_mints(client, auth_dir):
    _mint(client, "Pesti Hengermalom Részvénytársaság")
    again = _mint(client, "pesti hengermalom részvénytársaság")
    assert again["entity"]["id"] == "F000002"
    assert again["existing_exact"] == ["F000001"]


def test_mint_refuses_without_an_id_scheme_or_a_known_type(client, auth_dir):
    r = client.post("/api/authority/mint", json={"name": FILE, "entity": {"name": "X", "type": "bank"}})
    assert r.status_code == 400 and "entity.type" in r.text
    data = _read(auth_dir); data.pop("id_scheme")
    (auth_dir / FILE).write_text(json.dumps(data), encoding="utf-8")
    r = client.post("/api/authority/mint", json={"name": FILE, "entity": {"name": "X"}})
    assert r.status_code == 400 and "id_scheme" in r.text


def test_legal_form_phrases_are_stripped_on_both_sides(client, auth_dir):
    _mint(client, "Pesti Hengermalom Részvénytársaság",
          entity={"aliases": [{"name": "Pester Walzmühle Actiengesellschaft", "lang": "de"}]})
    for q in ("Pesti Hengermalom", "Pesti Hengermalom r.-t.", "Pester Walzmühle A.-G.",
              "pesti hengermalom reszvenytarsasag"):
        r = client.get("/api/authority/resolve", params={"q": q, "name": FILE})
        assert r.status_code == 200, r.text
        c = r.json()["candidates"]
        assert c and c[0]["id"] == "F000001" and c[0]["score"] >= 99, (q, c)


def test_slice_is_idempotent_on_provenance_and_adds_the_printed_name_as_alias(client, auth_dir):
    _mint(client, "Pesti Hengermalom Részvénytársaság")
    body = {"name": FILE, "id": "F000001",
            "slice": {"as_of": 1882, "source": "compass_1882", "name": "Pesti Hengermalom R.-T.",
                      "provenance": {"project": "compass_1882", "stem": "p9", "group_id": 11}, "seq": 11,
                      "attrs": {"seat": "Budapest"}},
            "alias": {"name": "Pesti Hengermalom R.-T.", "lang": "hu", "source": "compass_1882"}}
    r1 = client.post("/api/authority/slice", json=body); assert r1.status_code == 200, r1.text
    assert r1.json()["replaced"] is False and r1.json()["added_alias"] is True and r1.json()["n_slices"] == 2
    body["slice"]["attrs"] = {"seat": "Budapest", "capital": "2 000 000"}
    r2 = client.post("/api/authority/slice", json=body); assert r2.status_code == 200
    assert r2.json()["replaced"] is True and r2.json()["added_alias"] is False and r2.json()["n_slices"] == 2
    e = _read(auth_dir)["entities"][0]
    assert e["slices"][1]["attrs"]["capital"] == "2 000 000"
    assert [a["name"] for a in e["aliases"]] == ["Pesti Hengermalom R.-T."]
    assert _read(auth_dir)["slices_present"] == [1877, 1882]
    r = client.post("/api/authority/slice", json={**body, "id": "F000099"})
    assert r.status_code == 404


def test_edges_are_typed_validated_and_not_stacked(client, auth_dir):
    _mint(client, "Pesti Hengermalom Részvénytársaság")
    _mint(client, "Budapesti Hengermalom Részvénytársaság")
    r = client.post("/api/authority/edge", json={"name": FILE, "from": "F000001", "to": "F000002",
                                                 "type": "rename", "as_of": 1887, "note": "moved seat"})
    assert r.status_code == 200 and r.json()["added"] is True, r.text
    r = client.post("/api/authority/edge", json={"name": FILE, "from": "F000001", "to": "F000002",
                                                 "type": "rename", "as_of": 1887})
    assert r.json()["added"] is False
    assert len(_read(auth_dir)["edges"]) == 1
    for bad, code in (({"from": "F000001", "to": "F000001", "type": "rename"}, 400),
                      ({"from": "F000001", "to": "F000002", "type": "friends"}, 400),
                      ({"from": "F000001", "to": "F000077", "type": "merger"}, 404)):
        r = client.post("/api/authority/edge", json={"name": FILE, **bad})
        assert r.status_code == code, (bad, r.text)
    r = client.get("/api/authority/edges", params={"name": FILE, "id": "F000002"})
    assert r.status_code == 200
    assert r.json()["canonical_id"] == "F000002" and r.json()["edges"][0]["type"] == "rename"
    r = client.get("/api/authority/entity", params={"name": FILE, "id": "F000001"})
    assert r.json()["edges"][0]["to"] == "F000002" and r.json()["canonical_id"] == "F000001"


def test_duplicate_of_collapses_in_the_resolver_and_refuses_cycles(client, auth_dir):
    _mint(client, "Pesti Magyar Kereskedelmi Bank")              # F000001, the survivor
    _mint(client, "Pester Ungarische Commercialbank")            # F000002, minted by mistake
    r = client.post("/api/authority/edge", json={"name": FILE, "from": "F000002", "to": "F000001",
                                                 "type": "duplicate_of"})
    assert r.status_code == 200, r.text
    r = client.get("/api/authority/resolve", params={"q": "Pester Ungarische Commercialbank", "name": FILE})
    c = r.json()["candidates"]
    assert c[0]["id"] == "F000001" and c[0]["matched"] == "Pester Ungarische Commercialbank"
    assert [x["id"] for x in c].count("F000002") == 0
    r = client.get("/api/authority/edges", params={"name": FILE, "id": "F000002"})
    assert r.json()["canonical_id"] == "F000001"
    r = client.post("/api/authority/edge", json={"name": FILE, "from": "F000001", "to": "F000002",
                                                 "type": "duplicate_of"})
    assert r.status_code == 400 and "cycle" in r.text


def test_listing_exposes_the_new_header_fields(client, auth_dir):
    r = client.get("/api/authorities")
    me = next(a for a in r.json()["authorities"] if a["file"] == FILE)
    assert me["id_scheme"] == {"prefix": "F", "width": 6}
    assert "részvénytársaság" in me["normalize"]["strip_tokens"] and me["n_edges"] == 0


def test_places_strip_is_unchanged_by_the_phrase_rule():
    # `query_strip` keeps its trailing-only semantics; phrases are additive.
    assert srv._auth_strip_status("szombathely rtv", {"rtv"}) == "szombathely"
    assert srv._auth_strip_status("rtv", {"rtv"}) == "rtv"
    ph = (("részvénytársaság",), ("r", "t"))
    assert srv._auth_strip_status("pesti hengermalom részvénytársaság", set(), ph) == "pesti hengermalom"
    assert srv._auth_strip_status("pesti r t hengermalom", set(), ph) == "pesti hengermalom"
    assert srv._auth_strip_status("részvénytársaság", set(), ph) == "részvénytársaság"   # one token survives
