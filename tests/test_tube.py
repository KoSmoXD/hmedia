import importlib
import io
import sqlite3

from fastapi.testclient import TestClient
from PIL import Image


def test_account_library_comment_and_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COOKIE_SECURE", "0")
    tube = importlib.import_module("tube")
    client = TestClient(tube.app)

    assert client.get("/health").json() == {"ok": True}
    assert client.get("/api/me").json()["user"] is None
    assert client.post("/api/library/likes/example").status_code == 401

    created = client.post("/api/signup", json={"username": "tester_1", "password": "secure-password-123"})
    assert created.status_code == 200
    assert created.json()["user"]["username"] == "tester_1"
    assert client.post("/api/signup", json={"username": "TESTER_1", "password": "secure-password-123"}).status_code == 409
    assert client.get("/api/me").json()["user"]["username"] == "tester_1"

    assert client.post("/api/library/likes/example").json() == {"active": True}
    assert client.post("/api/library/saved/example").json() == {"active": True}
    assert client.post("/api/history/example").status_code == 200
    assert client.post("/api/comments/example", json={"text": "Nice video"}).status_code == 200
    assert client.get("/api/comments/example").json()[0]["text"] == "Nice video"
    assert client.get("/api/me").json()["history"] == ["example"]

    poster = io.BytesIO()
    Image.new("RGB", (32, 18), "yellow").save(poster, "PNG")
    uploaded = client.post("/api/upload", data={"title": "My clip", "description": "Test", "category": "Other"}, files={
        "video": ("clip.mp4", b"\x00\x00\x00\x18ftypisom0000", "video/mp4"),
        "thumbnail": ("thumb.png", poster.getvalue(), "image/png"),
    })
    assert uploaded.status_code == 200, uploaded.text
    video_id = uploaded.json()["id"]
    assert client.get("/api/videos").json()[0]["id"] == video_id
    assert client.get(f"/api/media/{video_id}").status_code == 200
    assert client.get(f"/api/thumbnail/{video_id}").status_code == 200

    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").json()["user"] is None
    assert client.post("/api/login", json={"username": "tester_1", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/login", json={"username": "tester_1", "password": "secure-password-123"}).status_code == 200
    assert client.get("/api/me").json()["likes"] == ["example"]


def test_existing_account_is_imported(tmp_path, monkeypatch):
    tube = importlib.import_module("tube")
    legacy = sqlite3.connect(tmp_path / "media.sqlite3")
    legacy.execute("CREATE TABLE users(username TEXT, password_hash TEXT, banned_at TEXT)")
    legacy.execute("INSERT INTO users VALUES(?,?,NULL)", ("old_member", tube.password_hash("old-password-123")))
    legacy.execute("INSERT INTO users VALUES(?,?,?)", ("banned_member", tube.password_hash("old-password-123"), "2026-01-01"))
    legacy.commit()
    legacy.close()

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COOKIE_SECURE", "0")
    tube = importlib.reload(tube)
    client = TestClient(tube.app)
    assert client.post("/api/login", json={"username": "old_member", "password": "old-password-123"}).status_code == 200
    assert client.post("/api/login", json={"username": "banned_member", "password": "old-password-123"}).status_code == 401
