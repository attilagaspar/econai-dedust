"""Authority matcher against the real, git-tracked places_hu authority.

Guards real past bugs: the district-context hard filter that excluded
district-seat towns ("Szombathely rtv" resolved to a random village), and
hardcoded admin-suffix stripping (now authority-defined query_strip).
"""
import pytest
from pathlib import Path

_PLACES = Path(__file__).resolve().parents[1] / "authorities" / "places_hu.authority.json"

pytestmark = pytest.mark.skipif(not _PLACES.exists(),
                                reason="places_hu authority not present")


def _resolve(client, q, **params):
    r = client.get("/api/authority/resolve",
                   params={"q": q, "name": "places_hu.authority.json", **params})
    assert r.status_code == 200, r.text
    return r.json()["candidates"]


def test_exact_settlement(client):
    cands = _resolve(client, "Szombathely")
    assert cands and cands[0]["name"] == "Szombathely"
    assert cands[0]["score"] >= 99


def test_query_strip_admin_suffix(client):
    """'rtv' (rendezett tanácsú város) is declared in the authority's
    query_strip and must not hurt the match."""
    cands = _resolve(client, "Szombathely rtv")
    assert cands and cands[0]["name"] == "Szombathely"
    assert cands[0]["score"] >= 99


def test_accent_folding(client):
    cands = _resolve(client, "Szekesfehervar")   # accents dropped by OCR
    assert cands and cands[0]["name"] == "Székesfehérvár"


def test_type_filter(client):
    cands = _resolve(client, "Vas", type="county")
    assert cands and cands[0]["type"] == "county"


def test_authorities_listing(client):
    r = client.get("/api/authorities")
    assert r.status_code == 200
    names = str(r.json())
    assert "places_hu" in names
