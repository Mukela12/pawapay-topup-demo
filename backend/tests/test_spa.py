"""Serving the built React app from Flask."""
import pytest


@pytest.fixture
def dist(app, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "icons").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "assets" / "index-abc123.css").write_text("body{}")
    (tmp_path / "assets" / "plex-400.woff2").write_bytes(b"wOF2")
    (tmp_path / "icons" / "check.json").write_text("{}")
    app.config["FRONTEND_DIST"] = str(tmp_path)
    return tmp_path


def test_client_routes_get_index(client, dist):
    for path in ("/", "/wallet", "/console", "/console/topups/123"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert b"id=root" in resp.data
        assert resp.headers["Cache-Control"] == "no-cache"


def test_assets_have_correct_mime_types_and_caching(client, dist):
    js = client.get("/assets/index-abc123.js")
    assert js.status_code == 200 and js.mimetype == "text/javascript"
    assert "immutable" in js.headers["Cache-Control"]
    assert client.get("/assets/index-abc123.css").mimetype == "text/css"
    assert client.get("/assets/plex-400.woff2").mimetype == "font/woff2"
    assert client.get("/icons/check.json").mimetype == "application/json"
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/missing.png").status_code == 404


def test_unknown_api_paths_are_json_404(client, dist):
    for method, path in (("get", "/api/nope"), ("post", "/api/nope"), ("get", "/api"), ("delete", "/webhooks/x")):
        resp = getattr(client, method)(path)
        assert resp.status_code == 404, (method, path)
        assert resp.is_json and resp.json["error"]["code"] == "not_found"


def test_no_dist_still_answers(app, client, tmp_path):
    app.config["FRONTEND_DIST"] = str(tmp_path / "missing")
    resp = client.get("/")
    assert resp.status_code == 200 and resp.json["service"] == "ringwise-api"


def test_path_traversal_is_blocked(client, dist):
    assert client.get("/../../etc/passwd").status_code == 404
