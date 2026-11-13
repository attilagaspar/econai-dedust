"""Notes (dashboard → Notes): topics of timestamped notes behind
/api/notes, stored in one JSON file (monkeypatched to a temp path)."""
import json

import pytest

from app import notes


@pytest.fixture()
def notes_path(tmp_path, monkeypatch):
    p = tmp_path / "notes.json"
    monkeypatch.setattr(notes, "NOTES_PATH", p)
    return p


def test_topics_and_notes_round_trip(client, notes_path):
    assert client.get("/api/notes").json()["topics"] == []
    assert client.post("/api/notes", json={"title": "   "}).status_code == 400
    t = client.post("/api/notes", json={"title": "Data sources"}).json()["topic"]
    assert t["title"] == "Data sources" and t["n_notes"] == 0 and t["created"]
    tid = t["id"]
    n1 = client.post(f"/api/notes/{tid}/notes", json={"text": "https://adt.arcanum.com/ Compass volumes, needs a login"}).json()["note"]
    n2 = client.post(f"/api/notes/{tid}/notes", json={"text": "KSH library catalogue"}).json()["note"]
    assert n1["created"] == n1["updated"] and n1["id"] != n2["id"]
    assert client.post(f"/api/notes/{tid}/notes", json={"text": ""}).status_code == 400
    got = client.get(f"/api/notes/{tid}").json()["topic"]
    assert got["n_notes"] == 2 and [n["id"] for n in got["notes"]][0] in (n1["id"], n2["id"])   # newest first (same second: either)
    # edit keeps created, bumps updated; rename; listing shows counts
    e = client.patch(f"/api/notes/{tid}/notes/{n2['id']}", json={"text": "KSH library catalogue (ask Judit)"}).json()["note"]
    assert e["created"] == n2["created"] and e["updated"] >= n2["updated"] and e["text"].endswith("(ask Judit)")
    assert client.patch(f"/api/notes/{tid}", json={"title": "Sources"}).json()["topic"]["title"] == "Sources"
    lst = client.get("/api/notes").json()
    assert lst["topics"][0]["n_notes"] == 2 and lst["path"] == str(notes_path)
    # the file is the store
    raw = json.loads(notes_path.read_text(encoding="utf-8"))
    assert raw["topics"][0]["title"] == "Sources" and len(raw["topics"][0]["notes"]) == 2
    # delete a note, then the topic
    assert client.delete(f"/api/notes/{tid}/notes/{n1['id']}").json()["ok"] is True
    assert client.delete(f"/api/notes/{tid}/notes/{n1['id']}").status_code == 404
    assert client.get(f"/api/notes/{tid}").json()["topic"]["n_notes"] == 1
    assert client.delete(f"/api/notes/{tid}").json()["notes_removed"] == 1
    assert client.get(f"/api/notes/{tid}").status_code == 404
    assert client.get("/api/notes").json()["topics"] == []


def test_a_damaged_or_missing_file_reads_as_empty(client, notes_path):
    notes_path.write_text("{not json", encoding="utf-8")
    assert client.get("/api/notes").json()["topics"] == []
    client.post("/api/notes", json={"title": "fresh"})
    assert json.loads(notes_path.read_text(encoding="utf-8"))["topics"][0]["title"] == "fresh"
