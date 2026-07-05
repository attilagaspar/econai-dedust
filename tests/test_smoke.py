"""App boots; static files are served fresh (no stale-cache ritual)."""


def test_static_index_served_no_cache(client):
    r = client.get("/static/index.html")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"


def test_pages_listing(client, page_folder):
    r = client.get("/api/pages", params={"folder": str(page_folder)})
    assert r.status_code == 200
    assert "p1" in str(r.json())
