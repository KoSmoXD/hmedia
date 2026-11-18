"""MrHTube: a small video site with persistent local accounts and uploads."""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import hmac
import io
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "tube_static"
DATA = Path(os.environ.get("DATA_DIR", str(ROOT / "tube_data"))).resolve()
MEDIA = DATA / "tube_videos"
POSTERS = DATA / "tube_posters"
for folder in (DATA, MEDIA, POSTERS):
    folder.mkdir(parents=True, exist_ok=True)
DB = DATA / "tube.sqlite3"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1").lower() in {"1", "true", "yes"}
SESSION_AGE = 60 * 60 * 24 * 30
MAX_VIDEO = 100 * 1024 * 1024
MAX_POSTER = 3 * 1024 * 1024
USERNAME = re.compile(r"^[A-Za-z0-9_]{3,24}$")
CATEGORIES = {"Music", "Learning", "Science", "Animation", "Gaming", "Other", "Shorts"}
AUTH_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)

app = FastAPI(title="MrHTube")


@contextmanager
def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS profiles (
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          display_name TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '',
          avatar BLOB, avatar_version TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS videos (
          id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          title TEXT NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL,
          filename TEXT NOT NULL, poster_filename TEXT, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS likes (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          video_id TEXT NOT NULL, PRIMARY KEY(user_id, video_id)
        );
        CREATE TABLE IF NOT EXISTS saved (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          video_id TEXT NOT NULL, PRIMARY KEY(user_id, video_id)
        );
        CREATE TABLE IF NOT EXISTS history (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          video_id TEXT NOT NULL, visited_at INTEGER NOT NULL,
          PRIMARY KEY(user_id, video_id)
        );
        CREATE TABLE IF NOT EXISTS comments (
          id INTEGER PRIMARY KEY, video_id TEXT NOT NULL,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          body TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS comments_video ON comments(video_id,id DESC);
        """)
        # Reuse existing chat account credentials without changing its database.
        legacy_db = DATA / "media.sqlite3"
        if legacy_db.is_file():
            with sqlite3.connect(f"file:{legacy_db.as_posix()}?mode=ro", uri=True) as legacy:
                columns = {row[1] for row in legacy.execute("PRAGMA table_info(users)")}
                where = " WHERE banned_at IS NULL" if "banned_at" in columns else ""
                for username, stored_hash in legacy.execute("SELECT username,password_hash FROM users" + where):
                    if stored_hash.startswith("scrypt$"):
                        conn.execute("INSERT OR IGNORE INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                                     (username, stored_hash, int(time.time())))


init_db()


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        method, salt, digest = stored.split("$")
        if method != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def current_user(request: Request):
    token = request.cookies.get("tube_session")
    if not token:
        return None
    with db() as conn:
        return conn.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
          WHERE s.token_hash=? AND s.expires_at>?""",
          (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()


def require_user(request: Request):
    user = current_user(request)
    if user is not None:
        return user
    raise HTTPException(401, "Log in to continue.")


def limit_auth(request: Request, action: str, max_attempts: int):
    key = f"{action}:{request.client.host if request.client else 'unknown'}"
    recent = AUTH_ATTEMPTS[key]
    now = time.monotonic()
    while recent and now - recent[0] > 900:
        recent.popleft()
    if len(recent) >= max_attempts:
        raise HTTPException(429, "Too many attempts. Try again later.")
    recent.append(now)


def new_session(response: Response, user_id: int):
    token = secrets.token_urlsafe(40)
    now = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                     (hashlib.sha256(token.encode()).hexdigest(), user_id, now + SESSION_AGE))
    response.set_cookie("tube_session", token, max_age=SESSION_AGE, httponly=True,
                        secure=COOKIE_SECURE, samesite="lax", path="/")


def library(user_id: int):
    with db() as conn:
        return {
            "likes": [r[0] for r in conn.execute("SELECT video_id FROM likes WHERE user_id=?", (user_id,))],
            "saved": [r[0] for r in conn.execute("SELECT video_id FROM saved WHERE user_id=?", (user_id,))],
            "history": [r[0] for r in conn.execute("SELECT video_id FROM history WHERE user_id=? ORDER BY visited_at DESC", (user_id,))],
        }


def public_user(user):
    with db() as conn:
        profile = conn.execute("SELECT display_name,bio,avatar_version FROM profiles WHERE user_id=?", (user["id"],)).fetchone()
    return {
        "id": user["id"], "username": user["username"],
        "display_name": profile["display_name"] if profile else "",
        "bio": profile["bio"] if profile else "",
        "avatar_url": avatar_url(user["id"], profile["avatar_version"]) if profile else None,
    }


def avatar_url(user_id, version):
    return f"/api/avatars/{user_id}?v={version}" if version else None


@app.get("/api/profiles/{username}")
def get_profile(username: str):
    with db() as conn:
        user = conn.execute("SELECT id,username FROM users WHERE username=?", (username,)).fetchone()
    if user is None:
        raise HTTPException(404, "Profile not found.")
    return public_user(user)


@app.get("/api/avatars/{user_id}")
def get_avatar(user_id: int):
    with db() as conn:
        row = conn.execute("SELECT avatar FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if not row or row["avatar"] is None:
        raise HTTPException(404, "Profile picture not found.")
    return Response(row["avatar"], media_type="image/png", headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


@app.put("/api/me/profile")
def update_profile(request: Request, display_name: str = Form(""), bio: str = Form(""),
                   avatar: UploadFile | None = File(None), remove_avatar: bool = Form(False)):
    user = require_user(request)
    display_name, bio = display_name.strip(), bio.strip()
    if len(display_name) > 40 or len(bio) > 300:
        raise HTTPException(400, "Use up to 40 characters for your name and 300 for your bio.")
    new_avatar = None
    if avatar is not None:
        raw = avatar.file.read(MAX_POSTER + 1)
        if len(raw) > MAX_POSTER:
            raise HTTPException(400, "Profile pictures must be 3 MB or smaller.")
        try:
            with Image.open(io.BytesIO(raw), formats=["JPEG", "PNG", "WEBP"]) as image:
                if image.width * image.height > 16_000_000:
                    raise ValueError("Image dimensions too large")
                image.thumbnail((256, 256))
                output = io.BytesIO()
                image.convert("RGBA").save(output, format="PNG")
                new_avatar = output.getvalue()
        except (OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(400, "Choose a valid JPEG, PNG, or WebP profile picture.") from None
    version = hashlib.sha256(new_avatar).hexdigest() if new_avatar is not None else None
    with db() as conn:
        conn.execute("""INSERT INTO profiles(user_id,display_name,bio,avatar,avatar_version)
            VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            display_name=excluded.display_name, bio=excluded.bio,
            avatar=CASE WHEN ? THEN excluded.avatar ELSE profiles.avatar END,
            avatar_version=CASE WHEN ? THEN excluded.avatar_version ELSE profiles.avatar_version END
            """, (user["id"], display_name, bio, new_avatar, version,
                  remove_avatar or new_avatar is not None, remove_avatar or new_avatar is not None))
    return public_user(user)



class Credentials(BaseModel):
    username: str
    password: str


class Comment(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@app.middleware("http")
async def check_origin(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlparse
            if urlparse(origin).netloc.lower() != request.headers.get("host", "").lower():
                return Response("Invalid origin", status_code=403)
    return await call_next(request)


@app.get("/health")
def health():
    with db() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    user = current_user(request)
    return {"user": public_user(user) if user else None, **(library(user["id"]) if user else {"likes": [], "saved": [], "history": []})}


@app.post("/api/signup")
def signup(data: Credentials, response: Response, request: Request):
    limit_auth(request, "signup", 10)
    username = data.username.strip()
    if not USERNAME.fullmatch(username):
        raise HTTPException(400, "Username must be 3–24 letters, numbers or underscores.")
    if not 8 <= len(data.password) <= 128:
        raise HTTPException(400, "Password must be 8–128 characters.")
    try:
        with db() as conn:
            cursor = conn.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                                  (username, password_hash(data.password), int(time.time())))
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That username is taken.") from None
    new_session(response, user_id)
    return {"user": public_user({"id": user_id, "username": username}), "likes": [], "saved": [], "history": []}


@app.post("/api/login")
def login(data: Credentials, response: Response, request: Request):
    limit_auth(request, "login", 30)
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (data.username.strip(),)).fetchone()
    if not user or not password_matches(data.password, user["password_hash"]):
        raise HTTPException(401, "Invalid username or password.")
    new_session(response, user["id"])
    return {"user": public_user(user), **library(user["id"])}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("tube_session")
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie("tube_session", path="/")
    return {"ok": True}


@app.get("/api/videos")
def list_videos(channel: str | None = None):
    with db() as conn:
        rows = conn.execute("""SELECT v.*, u.username, p.display_name, p.avatar_version, (SELECT COUNT(*) FROM likes l WHERE l.video_id=v.id) AS like_count
          FROM videos v JOIN users u ON u.id=v.owner_id LEFT JOIN profiles p ON p.user_id=u.id
          WHERE (? IS NULL OR u.username=?) ORDER BY v.created_at DESC LIMIT 200""", (channel, channel)).fetchall()
    return [{"id": r["id"], "title": r["title"], "description": r["description"],
             "channel": r["username"], "channel_name": r["display_name"] or r["username"],
             "channel_avatar": avatar_url(r["owner_id"], r["avatar_version"]), "views": f'{r["like_count"]} likes', "age": "Community upload",
             "duration": "VIDEO", "category": r["category"], "color": "#ba9b35",
             "video_url": f'/api/media/{r["id"]}',
             "thumbnail_url": f'/api/thumbnail/{r["id"]}' if r["poster_filename"] else None}
            for r in rows]


@app.post("/api/upload")
async def upload(request: Request, title: str = Form(...), description: str = Form(""),
                 category: str = Form("Other"), video: UploadFile = File(...),
                 thumbnail: UploadFile | None = File(None)):
    user = require_user(request)
    title, description = title.strip(), description.strip()
    if not 1 <= len(title) <= 120 or len(description) > 2000 or category not in CATEGORIES:
        raise HTTPException(400, "Check the title, description and category.")
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".webm"} or video.content_type not in {"video/mp4", "video/webm"}:
        raise HTTPException(400, "Upload an MP4 or WebM video.")
    video_id = secrets.token_urlsafe(12)
    filename = video_id + suffix
    destination = MEDIA / filename
    size = 0
    try:
        with destination.open("wb") as out:
            while chunk := await video.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_VIDEO:
                    raise HTTPException(413, "Video must be 100 MB or smaller.")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "Choose a video file.")
        with destination.open("rb") as uploaded:
            signature = uploaded.read(12)
        if not ((suffix == ".mp4" and signature[4:8] == b"ftyp") or
                (suffix == ".webm" and signature[:4] == b"\x1aE\xdf\xa3")):
            raise HTTPException(400, "The video file does not match its type.")
        poster_name = None
        if thumbnail and thumbnail.filename:
            data = await thumbnail.read(MAX_POSTER + 1)
            if len(data) > MAX_POSTER:
                raise HTTPException(413, "Thumbnail must be 3 MB or smaller.")
            try:
                image = Image.open(io.BytesIO(data))
                image.verify()
                image = Image.open(io.BytesIO(data)).convert("RGB")
                image.thumbnail((1280, 720))
                poster_name = video_id + ".jpg"
                image.save(POSTERS / poster_name, "JPEG", quality=85)
            except (UnidentifiedImageError, OSError, ValueError):
                raise HTTPException(400, "Invalid thumbnail image.") from None
        with db() as conn:
            conn.execute("""INSERT INTO videos(id,owner_id,title,description,category,filename,poster_filename,created_at)
              VALUES(?,?,?,?,?,?,?,?)""", (video_id, user["id"], title, description, category, filename, poster_name, int(time.time())))
        return {"id": video_id}
    except Exception:
        destination.unlink(missing_ok=True)
        (POSTERS / (video_id + ".jpg")).unlink(missing_ok=True)
        raise


def video_row(video_id: str):
    with db() as conn:
        return conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()


@app.get("/api/media/{video_id}")
def media(video_id: str):
    row = video_row(video_id)
    if not row:
        raise HTTPException(404)
    path = MEDIA / row["filename"]
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4" if path.suffix == ".mp4" else "video/webm")


@app.get("/api/thumbnail/{video_id}")
def thumbnail(video_id: str):
    row = video_row(video_id)
    if not row or not row["poster_filename"]:
        raise HTTPException(404)
    path = POSTERS / row["poster_filename"]
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@app.post("/api/library/{kind}/{video_id}")
def toggle_library(kind: str, video_id: str, request: Request):
    user = require_user(request)
    if kind not in {"likes", "saved"} or len(video_id) > 100:
        raise HTTPException(400)
    with db() as conn:
        exists = conn.execute(f"SELECT 1 FROM {kind} WHERE user_id=? AND video_id=?", (user["id"], video_id)).fetchone()
        if exists:
            conn.execute(f"DELETE FROM {kind} WHERE user_id=? AND video_id=?", (user["id"], video_id))
        else:
            conn.execute(f"INSERT INTO {kind}(user_id,video_id) VALUES(?,?)", (user["id"], video_id))
    return {"active": not bool(exists)}


@app.post("/api/history/{video_id}")
def add_history(video_id: str, request: Request):
    user = require_user(request)
    if len(video_id) > 100:
        raise HTTPException(400)
    with db() as conn:
        conn.execute("""INSERT INTO history(user_id,video_id,visited_at) VALUES(?,?,?)
          ON CONFLICT(user_id,video_id) DO UPDATE SET visited_at=excluded.visited_at""",
          (user["id"], video_id, int(time.time())))
    return {"ok": True}


@app.get("/api/comments/{video_id}")
def get_comments(video_id: str):
    with db() as conn:
        rows = conn.execute("""SELECT c.body,c.created_at,u.username FROM comments c
          JOIN users u ON u.id=c.user_id WHERE c.video_id=? ORDER BY c.id DESC LIMIT 100""", (video_id,)).fetchall()
    return [{"text": r["body"], "time": r["created_at"] * 1000, "username": r["username"]} for r in rows]


@app.post("/api/comments/{video_id}")
def add_comment(video_id: str, data: Comment, request: Request):
    user = require_user(request)
    body = data.text.strip()
    if not body or len(video_id) > 100:
        raise HTTPException(400)
    with db() as conn:
        conn.execute("INSERT INTO comments(video_id,user_id,body,created_at) VALUES(?,?,?,?)",
                     (video_id, user["id"], body, int(time.time())))
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="site")
