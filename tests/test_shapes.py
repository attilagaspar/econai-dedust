"""Shape CRUD round-trips and the rows-PATCH whitelist.

The whitelist test guards a real past bug: PATCH /api/page/shape/rows
rebuilds each row keeping only known fields — when per-row `authority`
was added, it was silently wiped on every row edit until whitelisted.
"""
import json


def _read(page_folder):
    return json.loads((page_folder / "p1.json").read_text(encoding="utf-8"))


def test_patch_label_persists(client, page_folder):
    r = client.patch("/api/page/shape",
                     params={"folder": str(page_folder), "stem": "p1", "idx": 0},
                     json={"label": "renamed"})
    assert r.status_code == 200
    assert _read(page_folder)["shapes"][0]["label"] == "renamed"


def test_add_and_delete_shape(client, page_folder):
    n0 = len(_read(page_folder)["shapes"])
    r = client.post("/api/page/shape",
                    params={"folder": str(page_folder), "stem": "p1"},
                    json={"label": "x", "points": [[0, 0], [5, 5]]})
    assert r.status_code == 200
    idx = r.json()["idx"]
    assert len(_read(page_folder)["shapes"]) == n0 + 1
    r = client.delete("/api/page/shape",
                      params={"folder": str(page_folder), "stem": "p1", "idx": idx})
    assert r.status_code == 200
    assert len(_read(page_folder)["shapes"]) == n0


def test_replace_shapes_stores_verbatim(client, page_folder):
    """PUT /api/page/shapes must keep unknown per-shape fields (authority,
    structured, …) — bulk client ops rely on this."""
    shapes = _read(page_folder)["shapes"]
    shapes[1]["authority"] = {"id": "TEST1", "name": "Testville"}
    r = client.put("/api/page/shapes",
                   params={"folder": str(page_folder), "stem": "p1"},
                   json={"shapes": shapes})
    assert r.status_code == 200
    assert _read(page_folder)["shapes"][1]["authority"]["id"] == "TEST1"


def test_rows_patch_whitelist_keeps_authority_and_llm_fixed(client, page_folder):
    rows = [
        {"y0": 100.0, "y1": 150.0, "ocr": "foo", "llm": "foo", "human": "",
         "authority": {"id": "TEST1", "name": "Testville"}, "llm_fixed": True},
        {"y0": 150.0, "y1": 200.0, "ocr": "bar", "llm": "", "human": "bar"},
    ]
    r = client.patch("/api/page/shape/rows",
                     params={"folder": str(page_folder), "stem": "p1", "idx": 1},
                     json={"rows": rows, "origin": "manual"})
    assert r.status_code == 200
    saved = _read(page_folder)["shapes"][1]["row_struct"]["rows"]
    assert saved[0]["authority"]["id"] == "TEST1"
    assert saved[0]["llm_fixed"] is True
    assert saved[1]["human"] == "bar"
    # numbering assigned by y-order
    assert [r["n"] for r in saved] == [1, 2]


def test_empty_rows_deletes_struct(client, page_folder):
    client.patch("/api/page/shape/rows",
                 params={"folder": str(page_folder), "stem": "p1", "idx": 1},
                 json={"rows": [{"y0": 100.0, "y1": 200.0, "ocr": "x"}]})
    r = client.patch("/api/page/shape/rows",
                     params={"folder": str(page_folder), "stem": "p1", "idx": 1},
                     json={"rows": []})
    assert r.status_code == 200
    assert "row_struct" not in _read(page_folder)["shapes"][1]
