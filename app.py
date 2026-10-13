"""Mr H Media — persistent realtime chat, single-instance FastAPI app."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', str(BASE / 'data'))).resolve()
UPLOADS = DATA / 'uploads'
UPLOADS.mkdir(parents=True, exist_ok=True)
DBFILE = DATA / 'media.sqlite3'
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', '').lower() in ('1', 'true', 'yes')
SESSION_SECONDS = 60 * 60 * 24 * 30
NAME_RE = re.compile(r'^[a-zA-Z0-9_]{3,24}$')
CHANNEL_RE = re.compile(r'^[a-z0-9-]{2,28}$')

@contextmanager
def db():
    conn = sqlite3.connect(DBFILE, timeout=8)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=8000')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL,
          display_name TEXT NOT NULL,
          bio TEXT NOT NULL DEFAULT '',
          avatar TEXT,
          role TEXT NOT NULL DEFAULT 'member',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
          id_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS channels (
          slug TEXT PRIMARY KEY, name TEXT NOT NULL, topic TEXT NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          channel_slug TEXT REFERENCES channels(slug) ON DELETE CASCADE,
          recipient_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
          body TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          CHECK ((channel_slug IS NOT NULL AND recipient_id IS NULL) OR (channel_slug IS NULL AND recipient_id IS NOT NULL))
        );
        CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_slug,id);
        CREATE INDEX IF NOT EXISTS idx_messages_dm ON messages(sender_id,recipient_id,id);
        ''')
        for i, (slug, name, topic) in enumerate([
            ('general', 'general', 'the place for everything. say hello.'),
            ('introductions', 'introductions', 'new here? introduce yourself.'),
            ('creations', 'creations', 'share what you have been working on.'),
            ('help', 'help', 'questions, answers, and good people.'),
        ]):
            c.execute('INSERT OR IGNORE INTO channels (slug,name,topic,sort_order) VALUES (?,?,?,?)', (slug, name, topic, i))
        owner = os.environ.get('OWNER_USERNAME', '')
        password = os.environ.get('OWNER_PASSWORD', '')
        if owner and password and NAME_RE.fullmatch(owner) and len(password) >= 8:
            existing = c.execute('SELECT id FROM users WHERE username=? COLLATE NOCASE', (owner,)).fetchone()
            if existing:
                # Privileges are NOT granted to a preexisting user via an env variable.
                pass
            else:
                c.execute('INSERT INTO users(username,password_hash,display_name,role) VALUES (?,?,?,?)',
                          (owner, hash_password(password), owner, 'owner'))

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1)
    return f'scrypt${salt.hex()}${derived.hex()}'

def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split('$')
        actual = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False

def user_dict(r):
    return {'id': r['id'], 'username': r['username'], 'display_name': r['display_name'],
            'bio': r['bio'], 'avatar': r['avatar'], 'role': r['role']}

def auth_user(token: str | None):
    if not token:
        return None
    with db() as c:
        return c.execute('''SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.id_hash=? AND s.expires_at>?''',
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()

def require_user(request: Request):
    user = auth_user(request.cookies.get('mrh_session'))
    if user is None:
        raise HTTPException(status_code=401, detail='Log in to continue.')
    return user

def set_session(response: Response, user_id: int):
    raw = secrets.token_urlsafe(42)
    with db() as c:
        c.execute('DELETE FROM sessions WHERE expires_at <= ?', (int(time.time()),))
        c.execute('INSERT INTO sessions(id_hash,user_id,expires_at) VALUES (?,?,?)',
                  (hashlib.sha256(raw.encode()).hexdigest(), user_id, int(time.time()) + SESSION_SECONDS))
    response.set_cookie('mrh_session', raw, max_age=SESSION_SECONDS, httponly=True,
                        secure=COOKIE_SECURE, samesite='lax', path='/')

rate_events = defaultdict(deque)
def throttle(request: Request, category: str, max_attempts: int, period: int):
    # IP-based lightweight throttling, sufficient for a single-instance starter.
    key = (request.client.host if request.client else '?', category)
    events = rate_events[key]
    now = time.monotonic()
    while events and events[0] < now - period:
        events.popleft()
    if len(events) >= max_attempts:
        raise HTTPException(429, 'Too many attempts. Try again later.')
    events.append(now)

class Credentials(BaseModel):
    username: str
    password: str

class ProfileUpdate(BaseModel):
    display_name: str = Field(max_length=40)
    username: str
    bio: str = Field(default='', max_length=240)

class ChangePassword(BaseModel):
    current_password: str
    new_password: str

class NewMessage(BaseModel):
    body: str = Field(min_length=1, max_length=2000)

class NewChannel(BaseModel):
    name: str
    topic: str = Field(default='', max_length=120)

app = FastAPI(title='Mr H Media', docs_url=None, redoc_url=None)
init_db()
app.mount('/static', StaticFiles(directory=str(BASE / 'static')), name='static')

@app.middleware('http')
async def security_headers(request: Request, call_next):
    # Refuse cross-origin state changes, even with ambient cookies.
    if request.method in ('POST', 'PATCH', 'PUT', 'DELETE'):
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc.lower() != request.headers.get('host', '').lower():
            return Response('Cross-origin request denied', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    return response

@app.get('/')
def index():
    return FileResponse(BASE / 'static' / 'index.html')

@app.get('/health')
def health():
    return {'ok': True, 'service': 'mr-h-media'}

@app.post('/api/signup', status_code=201)
def signup(data: Credentials, response: Response, request: Request):
    throttle(request, 'signup', 6, 3600)
    if not NAME_RE.fullmatch(data.username):
        raise HTTPException(400, 'Username must be 3–24 letters, numbers or underscores.')
    if len(data.password) < 8 or len(data.password) > 128:
        raise HTTPException(400, 'Password must be 8–128 characters.')
    try:
        with db() as c:
            cur = c.execute('INSERT INTO users(username,password_hash,display_name) VALUES (?,?,?)',
                            (data.username, hash_password(data.password), data.username))
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'That username is already taken.')
    set_session(response, user_id)
    with db() as c:
        return user_dict(c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())

@app.post('/api/login')
def login(data: Credentials, response: Response, request: Request):
    throttle(request, 'login', 20, 900)
    with db() as c:
        u = c.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE', (data.username,)).fetchone()
    if not u or not check_password(data.password, u['password_hash']):
        raise HTTPException(401, 'Wrong username or password.')
    set_session(response, u['id'])
    return user_dict(u)

@app.post('/api/logout')
def logout(request: Request, response: Response):
    token = request.cookies.get('mrh_session')
    if token:
        with db() as c:
            c.execute('DELETE FROM sessions WHERE id_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie('mrh_session', path='/')
    return {'ok': True}

@app.get('/api/me')
def me(request: Request):
    return user_dict(require_user(request))

@app.patch('/api/me')
def update_profile(data: ProfileUpdate, request: Request):
    u = require_user(request)
    display = data.display_name.strip()
    if not 1 <= len(display) <= 40:
        raise HTTPException(400, 'Display name must be 1–40 characters.')
    if not NAME_RE.fullmatch(data.username):
        raise HTTPException(400, 'Username must be 3–24 letters, numbers or underscores.')
    try:
        with db() as c:
            c.execute('UPDATE users SET username=?,display_name=?,bio=? WHERE id=?',
                      (data.username, display, data.bio.strip(), u['id']))
            result = user_dict(c.execute('SELECT * FROM users WHERE id=?', (u['id'],)).fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'That username is already taken.')
    return result

@app.post('/api/me/password')
def update_password(data: ChangePassword, request: Request):
    u = require_user(request)
    if not check_password(data.current_password, u['password_hash']):
        raise HTTPException(403, 'Current password is incorrect.')
    if not 8 <= len(data.new_password) <= 128:
        raise HTTPException(400, 'New password must be 8–128 characters.')
    with db() as c:
        c.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(data.new_password), u['id']))
        # Other sessions become invalid after password rotation.
        token = request.cookies.get('mrh_session')
        c.execute('DELETE FROM sessions WHERE user_id=? AND id_hash<>?',
                  (u['id'], hashlib.sha256(token.encode()).hexdigest()))
    return {'ok': True}

@app.post('/api/me/avatar')
async def upload_avatar(request: Request, file: UploadFile = File(...)):
    u = require_user(request)
    if file.content_type not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'):
        raise HTTPException(400, 'Upload a PNG, JPEG, WEBP, or GIF image.')
    raw = await file.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise HTTPException(413, 'Maximum image size is 4 MB.')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.seek(0)
            im.thumbnail((256, 256))
            if im.mode not in ('RGB', 'RGBA'):
                im = im.convert('RGBA')
            out = io.BytesIO()
            im.save(out, format='PNG', optimize=True)
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, 'That file is not a valid image.')
    filename = f'{u["id"]}_{secrets.token_hex(12)}.png'
    (UPLOADS / filename).write_bytes(out.getvalue())
    old = u['avatar']
    with db() as c:
        c.execute('UPDATE users SET avatar=? WHERE id=?', ('/uploads/' + filename, u['id']))
        result = user_dict(c.execute('SELECT * FROM users WHERE id=?', (u['id'],)).fetchone())
    if old and re.fullmatch(r'/uploads/[0-9]+_[a-f0-9]{24}\.png', old):
        (UPLOADS / old.split('/')[-1]).unlink(missing_ok=True)
    return result

@app.get('/uploads/{filename}')
def get_avatar(filename: str):
    if not re.fullmatch(r'[0-9]+_[a-f0-9]{24}\.png', filename):
        raise HTTPException(404)
    path = UPLOADS / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'public, max-age=31536000, immutable'})

@app.get('/api/channels')
def channels(request: Request):
    require_user(request)
    with db() as c:
        return [dict(r) for r in c.execute('SELECT slug,name,topic FROM channels ORDER BY sort_order,slug')]

@app.post('/api/channels', status_code=201)
def add_channel(data: NewChannel, request: Request):
    u = require_user(request)
    if u['role'] != 'owner':
        raise HTTPException(403, 'Only the owner can create channels.')
    slug = data.name.strip().lower().replace(' ', '-')
    if not CHANNEL_RE.fullmatch(slug):
        raise HTTPException(400, 'Channel name needs 2–28 lowercase letters, numbers or dashes.')
    try:
        with db() as c:
            order = c.execute('SELECT COALESCE(MAX(sort_order),0)+1 FROM channels').fetchone()[0]
            c.execute('INSERT INTO channels(slug,name,topic,sort_order) VALUES (?,?,?,?)',
                      (slug, slug, data.topic.strip(), order))
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'Channel already exists.')
    return {'slug': slug, 'name': slug, 'topic': data.topic.strip()}

@app.get('/api/members')
def members(request: Request, q: str = ''):
    require_user(request)
    with db() as c:
        if q:
            return [user_dict(r) for r in c.execute('SELECT * FROM users WHERE username LIKE ? OR display_name LIKE ? ORDER BY CASE role WHEN "owner" THEN 0 ELSE 1 END, display_name LIMIT 70', ('%' + q[:40] + '%', '%' + q[:40] + '%'))]
        return [user_dict(r) for r in c.execute('SELECT * FROM users ORDER BY CASE role WHEN "owner" THEN 0 ELSE 1 END, display_name LIMIT 200')]

MESSAGE_COLS = '''m.id, m.body, m.created_at, m.channel_slug, m.recipient_id,
       u.id as user_id, u.username, u.display_name, u.avatar, u.role'''
def message_dict(r):
    return {'id': r['id'], 'body': r['body'], 'created_at': r['created_at'],
            'channel_slug': r['channel_slug'], 'recipient_id': r['recipient_id'],
            'user': {'id': r['user_id'], 'username': r['username'], 'display_name': r['display_name'],
                     'avatar': r['avatar'], 'role': r['role']}}

def get_messages(where: str, params: tuple, before: int | None):
    if before is not None:
        where += ' AND m.id < ?'
        params += (before,)
    with db() as c:
        rows = c.execute(f'''SELECT {MESSAGE_COLS} FROM messages m JOIN users u ON u.id=m.sender_id
            WHERE {where} ORDER BY m.id DESC LIMIT 80''', params).fetchall()
    return [message_dict(r) for r in reversed(rows)]

def valid_channel(slug: str):
    with db() as c:
        room = c.execute('SELECT * FROM channels WHERE slug=?', (slug,)).fetchone()
    if not room:
        raise HTTPException(404, 'Channel not found.')
    return room

def valid_recipient(current_id, other_id):
    if current_id == other_id:
        raise HTTPException(400, 'You cannot message yourself.')
    with db() as c:
        row = c.execute('SELECT id FROM users WHERE id=?', (other_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'User not found.')

@app.get('/api/channels/{slug}/messages')
def channel_messages(slug: str, request: Request, before: int | None = None):
    require_user(request)
    valid_channel(slug)
    return get_messages('m.channel_slug=?', (slug,), before)

@app.get('/api/dms/{other_id}/messages')
def dm_messages(other_id: int, request: Request, before: int | None = None):
    u = require_user(request)
    valid_recipient(u['id'], other_id)
    return get_messages('m.channel_slug IS NULL AND ((m.sender_id=? AND m.recipient_id=?) OR (m.sender_id=? AND m.recipient_id=?))',
                        (u['id'], other_id, other_id, u['id']), before)

@app.get('/api/dms')
def dm_list(request: Request):
    u = require_user(request)
    with db() as c:
        ids = c.execute('''SELECT CASE WHEN sender_id=? THEN recipient_id ELSE sender_id END AS other_id,
            MAX(id) AS last_message FROM messages WHERE channel_slug IS NULL
            AND (sender_id=? OR recipient_id=?) GROUP BY other_id ORDER BY last_message DESC LIMIT 30''',
            (u['id'], u['id'], u['id'])).fetchall()
        return [user_dict(c.execute('SELECT * FROM users WHERE id=?', (r['other_id'],)).fetchone()) for r in ids]

# Websocket clients are process-local; run with a single worker.
connections: dict[WebSocket, tuple[str, int]] = {}
def dm_key(a, b):
    return f'dm:{min(a,b)}:{max(a,b)}'

async def emit(room: str, payload: dict):
    stale = []
    for ws, (key, _) in list(connections.items()):
        if key == room:
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
    for ws in stale:
        connections.pop(ws, None)

async def presence():
    users = list({uid for _, uid in connections.values()})
    for ws in list(connections):
        try:
            await ws.send_json({'type': 'presence', 'online': users})
        except Exception:
            connections.pop(ws, None)

@app.post('/api/channels/{slug}/messages', status_code=201)
async def send_channel(slug: str, data: NewMessage, request: Request):
    u = require_user(request)
    valid_channel(slug)
    body = data.body.strip()
    if not body:
        raise HTTPException(400, 'Message cannot be empty.')
    with db() as c:
        cur = c.execute('INSERT INTO messages(sender_id,channel_slug,body) VALUES (?,?,?)', (u['id'], slug, body))
        msg = message_dict(c.execute(f'SELECT {MESSAGE_COLS} FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.id=?', (cur.lastrowid,)).fetchone())
    await emit('channel:' + slug, {'type': 'message', 'message': msg})
    return msg

@app.post('/api/dms/{other_id}/messages', status_code=201)
async def send_dm(other_id: int, data: NewMessage, request: Request):
    u = require_user(request)
    valid_recipient(u['id'], other_id)
    body = data.body.strip()
    if not body:
        raise HTTPException(400, 'Message cannot be empty.')
    with db() as c:
        cur = c.execute('INSERT INTO messages(sender_id,recipient_id,body) VALUES (?,?,?)', (u['id'], other_id, body))
        msg = message_dict(c.execute(f'SELECT {MESSAGE_COLS} FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.id=?', (cur.lastrowid,)).fetchone())
    room = dm_key(u['id'], other_id)
    await emit(room, {'type': 'message', 'message': msg})
    # Notify the recipient even if they're viewing a public channel or another DM.
    for ws, (key, uid) in list(connections.items()):
        if uid == other_id and key != room:
            try:
                await ws.send_json({'type': 'dm-alert', 'from_user_id': u['id']})
            except Exception:
                connections.pop(ws, None)
    return msg

@app.websocket('/ws')
async def websocket(ws: WebSocket):
    origin = ws.headers.get('origin')
    if origin and urlparse(origin).netloc.lower() != ws.headers.get('host', '').lower():
        await ws.close(code=1008)
        return
    user = auth_user(ws.cookies.get('mrh_session'))
    if not user:
        await ws.close(code=1008)
        return
    channel = ws.query_params.get('channel')
    other = ws.query_params.get('dm')
    if bool(channel) == bool(other):
        await ws.close(code=1008)
        return
    if channel:
        with db() as c:
            if not c.execute('SELECT 1 FROM channels WHERE slug=?', (channel,)).fetchone():
                await ws.close(code=1008)
                return
        key = 'channel:' + channel
    else:
        try:
            other_id = int(other)
            valid_recipient(user['id'], other_id)
        except (ValueError, HTTPException):
            await ws.close(code=1008)
            return
        key = dm_key(user['id'], other_id)
    await ws.accept()
    connections[ws] = (key, user['id'])
    await presence()
    try:
        while True:
            item = await ws.receive_json()
            if isinstance(item, dict) and item.get('type') == 'typing':
                await emit(key, {'type': 'typing', 'user_id': user['id'], 'name': user['display_name']})
    except (WebSocketDisconnect, ValueError, RuntimeError):
        pass
    finally:
        connections.pop(ws, None)
        await presence()
