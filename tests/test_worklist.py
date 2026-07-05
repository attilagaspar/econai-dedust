"""Authority worklist / apply-by-string / alias promotion round-trip.

Uses a throwaway authority file in authorities/ (removed after the test) so
the real gazetteers are never modified.
"""
import json
from pathlib import Path

import pytest

_AUTH_DIR = Path(__file__).resolve().parents[1] / "authorities"
_TEST_AUTH = "_test_wl.authority.json"


@pytest.fixture()
def test_authority():
    doc = {
        "authority": "_test_wl", "version": "0",
        "entity_types": ["thing"], "query_strip": ["rtv"],
        "counts": {"aliases": 1},
        "entities": [
            {"id": "T1", "name": "Alfa", "aliases": [], "attrs": {},
             "slices": [{"as_of": 1900, "source": "t", "type": "thing",
                         "parent": None, "name": "Alfa"}]},
            {"id": "T2", "name": "Beta", "aliases": [{"name": "Betaa", "source": "x"}],
             "attrs": {},
             "slices": [{"as_of": 1900, "source": "t", "type": "thing",
                         "parent": None, "name": "Beta"}]},
        ],
    }
    p = _AUTH_DIR / _TEST_AUTH
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    yield _TEST_AUTH
    p.unlink(missing_ok=True)


@pytest.fixture()
def wl_folder(tmp_path):
    """Page with a 3-row lattice column: two unresolved 'Alfa' variants and one
    human-resolved cell whose text 'Betta' is not yet an alias of its entity."""
    def cell(text, row, auth=None):
        sh = {"label": "cell", "points": [[0, row * 100], [200, row * 100 + 90]],
              "group_id": None, "shape_type": "rectangle", "flags": {},
              "super_row": row, "super_column": 1, "table": 0,
              "human_output": {"human_corrected_text": text}}
        if auth:
            sh["authority"] = auth
        return sh
    shapes = [
        cell("Alfa", 1),
        cell("Alfa rtv", 2),          # query_strip → same folded group
        cell("Betta", 3, auth={"id": "T2", "name": "Beta", "type": "thing",
                               "score": 90, "via": "name", "source": "human",
                               "ts": "2026-01-01T00:00:00Z"}),
    ]
    (tmp_path / "w1.json").write_text(
        json.dumps({"shapes": shapes}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _read(folder):
    return json.loads((folder / "w1.json").read_text(encoding="utf-8"))


def test_worklist_groups_and_apply(client, wl_folder, test_authority):
    body = {"name": test_authority, "layer": "human"}
    r = client.post("/api/authority/worklist",
                    params={"folder": str(wl_folder)}, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    # both 'Alfa' and 'Alfa rtv' fold into one group of 2; resolved cell excluded
    assert d["total_unresolved"] == 2 and d["distinct"] == 1
    g = d["groups"][0]
    assert g["count"] == 2 and g["fold"] == "alfa"
    assert any(c["id"] == "T1" for c in g["candidates"])
    assert len(g["locations"]) == 2

    # apply the group's fix to all occurrences
    r = client.post("/api/authority/apply_string",
                    params={"folder": str(wl_folder)},
                    json={**body, "fold": "alfa", "entity_id": "T1"})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 2
    shapes = _read(wl_folder)["shapes"]
    assert shapes[0]["authority"]["id"] == "T1"
    assert shapes[1]["authority"]["id"] == "T1"
    assert shapes[0]["authority"]["source"] == "human"   # kept by future batches

    # worklist is now empty
    r = client.post("/api/authority/worklist",
                    params={"folder": str(wl_folder)}, json=body)
    assert r.json()["total_unresolved"] == 0


def test_alias_candidates_and_promote(client, wl_folder, test_authority):
    body = {"name": test_authority, "layer": "human"}
    r = client.post("/api/authority/alias_candidates",
                    params={"folder": str(wl_folder)}, json=body)
    assert r.status_code == 200, r.text
    cands = r.json()["candidates"]
    assert cands and cands[0]["id"] == "T2" and cands[0]["alias"] == "Betta"

    r = client.post("/api/authority/promote_aliases",
                    json={"name": test_authority,
                          "aliases": [{"id": "T2", "alias": "Betta"}]})
    assert r.status_code == 200 and r.json()["added"] == 1
    doc = json.loads((_AUTH_DIR / _TEST_AUTH).read_text(encoding="utf-8"))
    e2 = next(e for e in doc["entities"] if e["id"] == "T2")
    assert {"name": "Betta", "source": "econai_confirmed"} in e2["aliases"]
    assert doc["counts"]["aliases"] == 2

    # matcher picks the new alias up (mtime cache invalidation) → exact hit
    r = client.get("/api/authority/resolve",
                   params={"q": "Betta", "name": test_authority})
    top = r.json()["candidates"][0]
    assert top["id"] == "T2" and top["score"] >= 99

    # promoting the same alias again is a no-op
    r = client.post("/api/authority/promote_aliases",
                    json={"name": test_authority,
                          "aliases": [{"id": "T2", "alias": "Betta"}]})
    assert r.json()["added"] == 0
