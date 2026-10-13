"""Mr H Media — persistent realtime chat, single-instance FastAPI app."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
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

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', str(BASE / 'data'))).resolve()
UPLOADS = DATA / 'uploads'
UPLOADS.mkdir(parents=True, exist_ok=True)
ATTACHMENTS_DIR = UPLOADS / 'attachments'
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
ALLOWED_EXTS = {'.png','.jpg','.jpeg','.webp','.gif','.pdf','.txt','.zip','.mp3','.wav','.ogg','.mp4','.webm','.doc','.docx','.ppt','.pptx','.xls','.xlsx','.json','.lua','.rbxm','.rbxmx','.js','.py','.csv'}
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
          reply_to_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          CHECK ((channel_slug IS NOT NULL AND recipient_id IS NULL) OR (channel_slug IS NULL AND recipient_id IS NOT NULL))
        );
        CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_slug,id);
        CREATE INDEX IF NOT EXISTS idx_messages_dm ON messages(sender_id,recipient_id,id);
        ''')
        # Additive schema migrations preserve accounts, channels, and message history.
        if 'reply_to_id' not in {r['name'] for r in c.execute('PRAGMA table_info(messages)')}:
            c.execute('ALTER TABLE messages ADD COLUMN reply_to_id INTEGER REFERENCES messages(id) ON DELETE SET NULL')
        c.execute('CREATE INDEX IF NOT EXISTS idx_message_replies ON messages(reply_to_id)')
        columns = {r['name'] for r in c.execute('PRAGMA table_info(users)')}
        if 'banned_at' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN banned_at TEXT')
        if 'ban_reason' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN ban_reason TEXT NOT NULL DEFAULT ''")
        if 'theme' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT '{}' ")
        if 'spam_timeout_until' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN spam_timeout_until REAL NOT NULL DEFAULT 0')
        if 'spam_strikes' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN spam_strikes INTEGER NOT NULL DEFAULT 0')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS comments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
          author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          body TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id,id);
        CREATE TABLE IF NOT EXISTS spam_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          category TEXT NOT NULL, content_hash TEXT NOT NULL, at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_spam_window ON spam_events(user_id,category,at);
        CREATE TABLE IF NOT EXISTS pings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, target_id INTEGER NOT NULL, context TEXT NOT NULL,
          preview TEXT NOT NULL, read_at TEXT,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_pings_recipient ON pings(recipient_id,id DESC);
        CREATE TABLE IF NOT EXISTS posts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          title TEXT NOT NULL, body TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TABLE IF NOT EXISTS attachments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          message_id INTEGER UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
          post_id INTEGER UNIQUE REFERENCES posts(id) ON DELETE CASCADE,
          filename TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
          media_type TEXT NOT NULL, size INTEGER NOT NULL,
          CHECK ((message_id IS NOT NULL AND post_id IS NULL) OR (message_id IS NULL AND post_id IS NOT NULL))
        );
        CREATE INDEX IF NOT EXISTS idx_posts_newest ON posts(id DESC);
        ''')
        c.executescript("""
        CREATE TABLE IF NOT EXISTS voice_channels (
          slug TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0
        );
        """)
        c.execute('INSERT OR IGNORE INTO voice_channels(slug,name,sort_order) VALUES (?,?,?)', ('lounge','Lounge',0))
        c.execute('INSERT OR IGNORE INTO voice_channels(slug,name,sort_order) VALUES (?,?,?)', ('gaming','Gaming',1))
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
            'bio': r['bio'], 'avatar': r['avatar'], 'role': r['role'],
            'theme': json.loads(r['theme'] or '{}')}

def auth_user(token: str | None):
    if not token:
        return None
    with db() as c:
        return c.execute('''SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.id_hash=? AND s.expires_at>? AND u.banned_at IS NULL''',
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

# Persistent, user-scoped spam controls (multiple browser sessions cannot bypass them).
SPAM_LIMITS = {'message': (6, 10, 35, 60), 'upload': (3, 60, 8, 3600),
               'post': (3, 60, 9, 3600), 'comment': (8, 30, 35, 3600),
               'avatar': (5, 3600, 5, 3600)}

def check_spam(user, category: str, text: str):
    if user['role'] == 'owner':
        return
    limit, window, long_limit, long_window = SPAM_LIMITS[category]
    now = time.time()
    fingerprint = hashlib.sha256(' '.join(text.casefold().split()).encode()).hexdigest() if text.strip() else ''
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        u = c.execute('SELECT spam_timeout_until,spam_strikes FROM users WHERE id=?', (user['id'],)).fetchone()
        if u['spam_timeout_until'] > now:
            seconds = max(1, int(u['spam_timeout_until'] - now) + 1)
            raise HTTPException(429, f'Slow down. Try again in {seconds} seconds.', headers={'Retry-After':str(seconds)})
        recent = c.execute('SELECT at,content_hash FROM spam_events WHERE user_id=? AND category=? AND at>? ORDER BY at DESC',
                           (user['id'],category,now-long_window)).fetchall()
        too_fast = sum(r['at'] > now-window for r in recent) >= limit or len(recent)>=long_limit
        duplicate = bool(fingerprint and any(r['content_hash']==fingerprint and r['at']>now-45 for r in recent))
        if too_fast or duplicate:
            strikes = min(int(u['spam_strikes']) + 1, 8)
            seconds = min(300, 10 * (2 ** (strikes-1)))
            c.execute('UPDATE users SET spam_timeout_until=?,spam_strikes=? WHERE id=?', (now+seconds,strikes,user['id']))
            raise_after_commit = (seconds, duplicate)
        else:
            raise_after_commit = None
            c.execute('INSERT INTO spam_events(user_id,category,content_hash,at) VALUES (?,?,?,?)', (user['id'],category,fingerprint,now))
            c.execute('DELETE FROM spam_events WHERE user_id=? AND at<?', (user['id'],now-3600))
    if raise_after_commit:
        seconds, duplicate = raise_after_commit
        raise HTTPException(429, ('Duplicate content. ' if duplicate else 'Too many actions. ') + f'Try again in {seconds} seconds.', headers={'Retry-After':str(seconds)})

MENTION_PATTERN = re.compile(r'(?<![\w@])@([A-Za-z0-9_]{3,24})\b')
def record_pings(c, actor_id: int, body: str, kind: str, target_id: int, context: str):
    names = list(dict.fromkeys(n.casefold() for n in MENTION_PATTERN.findall(body)))[:5]
    notified = []
    for username in names:
        user = c.execute('SELECT id FROM users WHERE username=? COLLATE NOCASE AND banned_at IS NULL',(username,)).fetchone()
        if user and user['id'] != actor_id:
            c.execute('INSERT INTO pings(recipient_id,sender_id,kind,target_id,context,preview) VALUES (?,?,?,?,?,?)',
                      (user['id'],actor_id,kind,target_id,context,body[:180]))
            notified.append(user['id'])
    return notified

async def notify_pings(ids):
    for ws, (_, uid) in list(connections.items()):
        if uid in ids:
            try:
                await ws.send_json({'type':'ping-alert'})
            except Exception:
                connections.pop(ws,None)

class ThemePrefs(BaseModel):
    accent: str = '#8a7eff'
    sidebar: str = '#151620'
    chat: str = '#1a1b26'
    text: str = '#e8e9f2'

class NewComment(BaseModel):
    body: str = Field(min_length=1, max_length=1500)

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
    reply_to_id: int | None = Field(default=None, gt=0)

class NewChannel(BaseModel):
    name: str
    topic: str = Field(default='', max_length=120)

class NewPost(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default='', max_length=5000)

class BanRequest(BaseModel):
    reason: str = Field(default='', max_length=240)

def require_owner(request: Request):
    u = require_user(request)
    if u['role'] != 'owner':
        raise HTTPException(403, 'Only the owner can do that.')
    return u

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
    if u['banned_at']:
        raise HTTPException(403, 'This account has been banned.')
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

@app.patch('/api/me/theme')
def update_theme(data: ThemePrefs, request: Request):
    user = require_user(request)
    colors = data.model_dump()
    if any(not re.fullmatch(r'#[a-fA-F0-9]{6}',value) for value in colors.values()):
        raise HTTPException(400,'Colors must use six-digit hexadecimal format.')
    with db() as c:
        c.execute('UPDATE users SET theme=? WHERE id=?', (json.dumps(colors),user['id']))
        return user_dict(c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone())

@app.post('/api/me/avatar')
async def upload_avatar(request: Request, file: UploadFile = File(...)):
    u = require_user(request)
    check_spam(u,'avatar','avatar:'+str(time.time()))
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
    require_owner(request)
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
            return [user_dict(r) for r in c.execute('SELECT * FROM users WHERE banned_at IS NULL AND (username LIKE ? OR display_name LIKE ?) ORDER BY CASE role WHEN "owner" THEN 0 ELSE 1 END, display_name LIMIT 70', ('%' + q[:40] + '%', '%' + q[:40] + '%'))]
        return [user_dict(r) for r in c.execute('SELECT * FROM users WHERE banned_at IS NULL ORDER BY CASE role WHEN "owner" THEN 0 ELSE 1 END, display_name LIMIT 200')]

MESSAGE_COLS = '''m.id, m.body, m.created_at, m.channel_slug, m.recipient_id, m.reply_to_id,
       (SELECT substr(q.body,1,180) FROM messages q WHERE q.id=m.reply_to_id) AS reply_preview,
       (SELECT ru.username FROM messages q JOIN users ru ON ru.id=q.sender_id WHERE q.id=m.reply_to_id) AS reply_username,
       (SELECT ru.display_name FROM messages q JOIN users ru ON ru.id=q.sender_id WHERE q.id=m.reply_to_id) AS reply_display_name,
       (SELECT a2.filename FROM attachments a2 WHERE a2.message_id=m.reply_to_id) AS reply_file,
       u.id as user_id, u.username, u.display_name, u.avatar, u.role,
       a.id AS attachment_id, a.filename AS attachment_filename,
       a.media_type AS attachment_mime, a.size AS attachment_size'''
def attachment_dict(r):
    return {'id': r['attachment_id'], 'name': r['attachment_filename'],
            'mime': r['attachment_mime'], 'size': r['attachment_size'],
            'url': '/api/attachments/' + str(r['attachment_id'])}

def message_dict(r):
    return {'id': r['id'], 'body': r['body'], 'created_at': r['created_at'],
            'channel_slug': r['channel_slug'], 'recipient_id': r['recipient_id'],
            'user': {'id': r['user_id'], 'username': r['username'], 'display_name': r['display_name'],
                     'avatar': r['avatar'], 'role': r['role']},
            'attachment': attachment_dict(r) if r['attachment_id'] is not None else None,
            'reply': {'id': r['reply_to_id'], 'username': r['reply_username'],
                      'display_name': r['reply_display_name'], 'preview': r['reply_preview'],
                      'file': r['reply_file']} if r['reply_to_id'] else None}

def get_messages(where: str, params: tuple, before: int | None):
    if before is not None:
        where += ' AND m.id < ?'
        params += (before,)
    with db() as c:
        rows = c.execute(f'''SELECT {MESSAGE_COLS} FROM messages m JOIN users u ON u.id=m.sender_id LEFT JOIN attachments a ON a.message_id=m.id
            WHERE {where} ORDER BY m.id DESC LIMIT 80''', params).fetchall()
    return [message_dict(r) for r in reversed(rows)]

def valid_channel(slug: str):
    with db() as c:
        room = c.execute('SELECT * FROM channels WHERE slug=?', (slug,)).fetchone()
    if not room:
        raise HTTPException(404, 'Channel not found.')
    return room

def validate_reply(c, reply_id: int | None, sender_id: int, slug: str | None, other_id: int | None):
    if reply_id is None:
        return
    reply = c.execute('SELECT sender_id, recipient_id, channel_slug FROM messages WHERE id=?', (reply_id,)).fetchone()
    if not reply:
        raise HTTPException(404, 'Message to reply to was not found.')
    if slug is not None:
        allowed = reply['channel_slug'] == slug
    else:
        allowed = (reply['channel_slug'] is None and
                   ((reply['sender_id'] == sender_id and reply['recipient_id'] == other_id) or
                    (reply['sender_id'] == other_id and reply['recipient_id'] == sender_id)))
    if not allowed:
        raise HTTPException(403, 'Cannot reply to a message outside this conversation.')

def valid_recipient(current_id, other_id):
    if current_id == other_id:
        raise HTTPException(400, 'You cannot message yourself.')
    with db() as c:
        row = c.execute('SELECT id FROM users WHERE id=? AND banned_at IS NULL', (other_id,)).fetchone()
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

# Owner moderation, with existing sessions revoked immediately.
@app.get('/api/admin/users')
def admin_users(request: Request):
    require_owner(request)
    with db() as c:
        return [{**user_dict(r), 'banned': bool(r['banned_at']), 'ban_reason': r['ban_reason']}
                for r in c.execute('SELECT * FROM users ORDER BY id DESC LIMIT 500')]

@app.post('/api/admin/users/{user_id}/ban')
async def ban_user(user_id: int, data: BanRequest, request: Request):
    actor = require_owner(request)
    with db() as c:
        target = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, 'User not found.')
        if user_id == actor['id'] or target['role'] == 'owner':
            raise HTTPException(403, 'You cannot ban the owner.')
        c.execute("UPDATE users SET banned_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),ban_reason=? WHERE id=?",
                  (data.reason.strip(), user_id))
        c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
    for ws, (_, uid) in list(connections.items()):
        if uid == user_id:
            connections.pop(ws, None)
            await ws.close(code=1008, reason='Account banned')
    await presence()
    for voice_ws, (_, uid) in list(voice_connections.items()):
        if uid == user_id:
            try:
                await voice_ws.close(code=1008, reason='Account banned')
            except RuntimeError:
                pass
    return {'ok': True}

@app.post('/api/admin/users/{user_id}/unban')
def unban_user(user_id: int, request: Request):
    require_owner(request)
    with db() as c:
        target = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, 'User not found.')
        if target['role'] == 'owner':
            raise HTTPException(403, 'The owner is not bannable.')
        c.execute("UPDATE users SET banned_at=NULL,ban_reason='' WHERE id=?", (user_id,))
    return {'ok': True}

@app.delete('/api/channels/{slug}')
async def delete_channel(slug: str, request: Request):
    require_owner(request)
    if slug == 'general':
        raise HTTPException(403, 'The general channel cannot be removed.')
    with db() as c:
        if not c.execute('SELECT 1 FROM channels WHERE slug=?', (slug,)).fetchone():
            raise HTTPException(404, 'Channel not found.')
        # Capture attachments before ON DELETE CASCADE so their bytes can be removed.
        files = [r['stored_name'] for r in c.execute('''SELECT a.stored_name FROM attachments a
          JOIN messages m ON m.id=a.message_id WHERE m.channel_slug=?''', (slug,))]
        c.execute('DELETE FROM channels WHERE slug=?', (slug,))
    for name in files:
        (ATTACHMENTS_DIR / name).unlink(missing_ok=True)
    await emit('channel:' + slug, {'type':'channel-deleted', 'slug':slug})
    return {'ok': True}

# File attachments belong to either a message or post. Access checks use the linked record.
def validate_file_name(name: str):
    name = name.replace('\\', '/').split('/')[-1].strip().replace('\x00', '')
    if not name or len(name) > 150 or Path(name).suffix.lower() not in ALLOWED_EXTS:
        raise HTTPException(400, 'Unsupported filename or file type.')
    return name

async def store_attachment(file: UploadFile, request: Request):
    throttle(request, 'upload', 35, 3600)
    name = validate_file_name(file.filename or '')
    data = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, 'File must be between 1 byte and 8 MB.')
    mime = 'application/octet-stream'
    if Path(name).suffix.lower() in {'.png','.jpg','.jpeg','.webp','.gif'}:
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                im.thumbnail((1600, 1600))
                output = io.BytesIO()
                im.convert('RGB').save(output, format='PNG', optimize=True)
                data = output.getvalue()
            mime = 'image/png'
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(400, 'Invalid image.')
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, 'Processed file exceeds the 8 MB limit.')
    stored = secrets.token_hex(24)
    (ATTACHMENTS_DIR / stored).write_bytes(data)
    return name, stored, mime, len(data)

@app.get('/api/attachments/{attachment_id}')
def download_attachment(attachment_id: int, request: Request):
    u = require_user(request)
    with db() as c:
        attachment = c.execute('''SELECT a.*, m.channel_slug, m.sender_id AS message_sender,
           m.recipient_id, p.author_id AS post_author FROM attachments a
           LEFT JOIN messages m ON m.id=a.message_id
           LEFT JOIN posts p ON p.id=a.post_id WHERE a.id=?''', (attachment_id,)).fetchone()
    if not attachment:
        raise HTTPException(404, 'Attachment not found.')
    if attachment['message_id'] and attachment['channel_slug'] is None and u['id'] not in (attachment['message_sender'], attachment['recipient_id']):
        raise HTTPException(403, 'This file belongs to a private conversation.')
    file = ATTACHMENTS_DIR / attachment['stored_name']
    if not file.is_file():
        raise HTTPException(404, 'File not available.')
    is_image = attachment['media_type'] == 'image/png'
    return FileResponse(file, media_type='image/png' if is_image else 'application/octet-stream',
                        filename=attachment['filename'], content_disposition_type='inline' if is_image else 'attachment',
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

async def send_file_message(file: UploadFile, request: Request, body: str, slug: str | None, recipient_id: int | None, reply_to_id: int | None = None):
    u = require_user(request)
    if len(body) > 2000:
        raise HTTPException(400, 'Message is too long.')
    if slug:
        valid_channel(slug)
    else:
        valid_recipient(u['id'], recipient_id)
    with db() as c:
        validate_reply(c, reply_to_id, u['id'], slug, recipient_id)
    name, stored, mime, size = await store_attachment(file, request)
    try:
        check_spam(u,'upload',body.strip() or 'file:'+str(time.time()))
    except HTTPException:
        (ATTACHMENTS_DIR / stored).unlink(missing_ok=True)
        raise
    try:
        with db() as c:
            validate_reply(c, reply_to_id, u['id'], slug, recipient_id)
            cur = c.execute('INSERT INTO messages(sender_id,channel_slug,recipient_id,body,reply_to_id) VALUES (?,?,?,?,?)',
                            (u['id'], slug, recipient_id, body.strip(), reply_to_id))
            c.execute('INSERT INTO attachments(sender_id,message_id,filename,stored_name,media_type,size) VALUES (?,?,?,?,?,?)',
                      (u['id'],cur.lastrowid,name,stored,mime,size))
            msg = message_dict(c.execute(f'''SELECT {MESSAGE_COLS} FROM messages m JOIN users u ON u.id=m.sender_id
                  LEFT JOIN attachments a ON a.message_id=m.id WHERE m.id=?''', (cur.lastrowid,)).fetchone())
            mentions=record_pings(c,u['id'],body,'channel' if slug else 'dm',cur.lastrowid,slug or str(u['id']))
    except Exception:
        (ATTACHMENTS_DIR / stored).unlink(missing_ok=True)
        raise
    await notify_pings(mentions)
    if slug:
        await emit('channel:' + slug, {'type':'message','message':msg})
    else:
        await emit(dm_key(u['id'], recipient_id), {'type':'message','message':msg})
        for ws, (key, uid) in list(connections.items()):
            if uid == recipient_id and key != dm_key(u['id'],recipient_id):
                try:
                    await ws.send_json({'type':'dm-alert','from_user_id':u['id']})
                except Exception:
                    connections.pop(ws,None)
    return msg

@app.post('/api/channels/{slug}/attachments', status_code=201)
async def upload_channel_file(slug: str, request: Request, file: UploadFile = File(...), body: str = Form(default=''), reply_to_id: int | None = Form(default=None)):
    return await send_file_message(file, request, body, slug, None, reply_to_id)

@app.post('/api/dms/{other_id}/attachments', status_code=201)
async def upload_dm_file(other_id: int, request: Request, file: UploadFile = File(...), body: str = Form(default=''), reply_to_id: int | None = Form(default=None)):
    return await send_file_message(file, request, body, None, other_id, reply_to_id)

POST_COLS = '''p.id,p.title,p.body,p.created_at,
                (SELECT COUNT(*) FROM comments cc WHERE cc.post_id=p.id) AS comment_count, u.id AS user_id,u.username,u.display_name,u.avatar,u.role,
                a.id AS attachment_id,a.filename AS attachment_filename,
                a.media_type AS attachment_mime,a.size AS attachment_size'''
def post_dict(r):
    return {'id':r['id'], 'title':r['title'], 'body':r['body'], 'created_at':r['created_at'],
            'user': {'id':r['user_id'], 'username':r['username'], 'display_name':r['display_name'],
                     'avatar':r['avatar'], 'role':r['role']},
            'attachment': attachment_dict(r) if r['attachment_id'] is not None else None,
            'comment_count':r['comment_count']}

def get_post(c, post_id):
    return post_dict(c.execute(f'''SELECT {POST_COLS} FROM posts p JOIN users u ON u.id=p.author_id
         LEFT JOIN attachments a ON a.post_id=p.id WHERE p.id=?''', (post_id,)).fetchone())

@app.get('/api/posts')
def list_posts(request: Request, before: int | None = None):
    require_user(request)
    with db() as c:
        where = 'WHERE p.id < ?' if before is not None else ''
        rows = c.execute(f'''SELECT {POST_COLS} FROM posts p JOIN users u ON u.id=p.author_id
                 LEFT JOIN attachments a ON a.post_id=p.id {where} ORDER BY p.id DESC LIMIT 40''',
                 (before,) if before is not None else ()).fetchall()
    return [post_dict(r) for r in rows]

@app.post('/api/posts', status_code=201)
async def publish_post(data: NewPost, request: Request):
    u = require_user(request)
    title = data.title.strip()
    if not title:
        raise HTTPException(400, 'Post title cannot be empty.')
    check_spam(u,'post',title+' '+data.body)
    with db() as c:
        cur=c.execute('INSERT INTO posts(author_id,title,body) VALUES (?,?,?)', (u['id'],title,data.body.strip()))
        mentions=record_pings(c,u['id'],title+' '+data.body,'post',cur.lastrowid,'posts')
        result=get_post(c,cur.lastrowid)
    await notify_pings(mentions)
    return result

@app.post('/api/posts/with-file', status_code=201)
async def publish_post_with_file(request: Request, file: UploadFile=File(...), title: str=Form(...), body: str=Form(default='')):
    u=require_user(request)
    title=title.strip()
    if not title or len(title)>120 or len(body)>5000:
        raise HTTPException(400,'Invalid post title or body.')
    check_spam(u,'post',title+' '+body)
    name,stored,mime,size=await store_attachment(file,request)
    try:
        with db() as c:
            cur=c.execute('INSERT INTO posts(author_id,title,body) VALUES (?,?,?)',(u['id'],title,body.strip()))
            c.execute('INSERT INTO attachments(sender_id,post_id,filename,stored_name,media_type,size) VALUES (?,?,?,?,?,?)',
                      (u['id'],cur.lastrowid,name,stored,mime,size))
            mentions=record_pings(c,u['id'],title+' '+body,'post',cur.lastrowid,'posts')
            result=get_post(c,cur.lastrowid)
    except Exception:
        (ATTACHMENTS_DIR/stored).unlink(missing_ok=True)
        raise
    await notify_pings(mentions)
    return result

COMMENT_COLS = """c.id,c.post_id,c.body,c.created_at,u.id AS user_id,u.username,u.display_name,u.avatar,u.role"""
def comment_dict(row):
    return {'id':row['id'],'post_id':row['post_id'],'body':row['body'],'created_at':row['created_at'],
            'user': {'id':row['user_id'],'username':row['username'],'display_name':row['display_name'],
                     'avatar':row['avatar'],'role':row['role']}}

@app.get('/api/posts/{post_id}/comments')
def list_comments(post_id: int, request: Request):
    require_user(request)
    with db() as c:
        if not c.execute('SELECT 1 FROM posts WHERE id=?',(post_id,)).fetchone():
            raise HTTPException(404,'Post not found.')
        return [comment_dict(r) for r in c.execute(f"""SELECT {COMMENT_COLS} FROM comments c
                  JOIN users u ON u.id=c.author_id WHERE c.post_id=? ORDER BY c.id DESC LIMIT 200""",(post_id,))][::-1]

@app.post('/api/posts/{post_id}/comments', status_code=201)
async def add_comment(post_id: int, data: NewComment, request: Request):
    u=require_user(request)
    body=data.body.strip()
    if not body:
        raise HTTPException(400,'Comment cannot be empty.')
    with db() as c:
        if not c.execute('SELECT 1 FROM posts WHERE id=?',(post_id,)).fetchone():
            raise HTTPException(404,'Post not found.')
    check_spam(u,'comment',body)
    with db() as c:
        row=c.execute('INSERT INTO comments(post_id,author_id,body) VALUES (?,?,?)',(post_id,u['id'],body))
        mentions=record_pings(c,u['id'],body,'post',post_id,'comments')
        result=comment_dict(c.execute(f"""SELECT {COMMENT_COLS} FROM comments c JOIN users u ON u.id=c.author_id WHERE c.id=?""",(row.lastrowid,)).fetchone())
    await notify_pings(mentions)
    return result

@app.delete('/api/posts/{post_id}/comments/{comment_id}')
def delete_comment(post_id: int, comment_id: int, request: Request):
    u=require_user(request)
    with db() as c:
        row=c.execute('SELECT * FROM comments WHERE id=? AND post_id=?',(comment_id,post_id)).fetchone()
        if not row:
            raise HTTPException(404,'Comment not found.')
        if u['id']!=row['author_id'] and u['role']!='owner':
            raise HTTPException(403,'Cannot delete this comment.')
        c.execute('DELETE FROM comments WHERE id=?',(comment_id,))
    return {'ok':True}

@app.get('/api/notifications')
def notifications(request: Request):
    u=require_user(request)
    with db() as c:
        return [{'id':r['id'],'sender':r['username'],'sender_name':r['display_name'],
                 'kind':r['kind'],'target_id':r['target_id'],'context':r['context'],
                 'preview':r['preview'],'read':bool(r['read_at']),'created_at':r['created_at']}
                for r in c.execute("""SELECT p.*, u.username,u.display_name FROM pings p JOIN users u ON u.id=p.sender_id
                                  WHERE p.recipient_id=? ORDER BY p.id DESC LIMIT 60""",(u['id'],))]

@app.post('/api/notifications/read')
def mark_notifications_read(request: Request):
    u=require_user(request)
    with db() as c:
        c.execute("UPDATE pings SET read_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE recipient_id=? AND read_at IS NULL",(u['id'],))
    return {'ok':True}

@app.delete('/api/posts/{post_id}')
def delete_post(post_id: int, request: Request):
    u=require_user(request)
    with db() as c:
        post=c.execute('SELECT * FROM posts WHERE id=?',(post_id,)).fetchone()
        if not post:
            raise HTTPException(404,'Post not found.')
        if post['author_id'] != u['id'] and u['role'] != 'owner':
            raise HTTPException(403,'You cannot delete this post.')
        files=[r['stored_name'] for r in c.execute('SELECT stored_name FROM attachments WHERE post_id=?',(post_id,))]
        c.execute('DELETE FROM posts WHERE id=?',(post_id,))
    for name in files:
        (ATTACHMENTS_DIR/name).unlink(missing_ok=True)
    return {'ok':True}

# Voice rooms are signaling-only: audio/screen pixels move peer-to-peer via WebRTC.
# One Railway instance is required because room membership is in process memory.
voice_connections: dict[WebSocket, tuple[str,int]] = {}
voice_status: dict[WebSocket, dict] = {}
VOICE_CAPACITY = 8   # Mesh calls: each participant sends media to every other participant.

class VoiceChannelCreate(BaseModel):
    name: str = Field(min_length=2, max_length=28)


def voice_room_exists(slug: str):
    with db() as c:
        if not c.execute('SELECT 1 FROM voice_channels WHERE slug=?', (slug,)).fetchone():
            raise HTTPException(404, 'Voice room not found.')


def voice_members(slug: str):
    ids = list(dict.fromkeys(uid for room, uid in voice_connections.values() if room == slug))
    if not ids:
        return []
    with db() as c:
        rows = c.execute('SELECT * FROM users WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
    return [{**user_dict(row), 'muted': next((voice_status.get(w, {}).get('muted', True) for w, (r, u) in voice_connections.items() if r==slug and u==row['id']), True),
             'sharing': next((voice_status.get(w, {}).get('sharing', False) for w, (r, u) in voice_connections.items() if r==slug and u==row['id']), False)} for row in rows]


@app.get('/api/voice/channels')
def get_voice_channels(request: Request):
    require_user(request)
    with db() as c:
        rooms = [dict(r) for r in c.execute('SELECT slug,name FROM voice_channels ORDER BY sort_order,slug')]
    return [{**room, 'members': voice_members(room['slug'])} for room in rooms]


@app.post('/api/voice/channels', status_code=201)
def create_voice_channel(data: VoiceChannelCreate, request: Request):
    require_owner(request)
    name = data.name.strip()
    slug = re.sub(r'[^a-z0-9-]', '-', name.lower()).strip('-')
    slug = re.sub(r'-+', '-', slug)
    if not CHANNEL_RE.fullmatch(slug):
        raise HTTPException(400, 'Use 2–28 letters, numbers or hyphens for the room name.')
    with db() as c:
        if c.execute('SELECT 1 FROM voice_channels WHERE slug=?', (slug,)).fetchone():
            raise HTTPException(409, 'A voice room with this name already exists.')
        c.execute('INSERT INTO voice_channels(slug,name,sort_order) VALUES (?,?,(SELECT COUNT(*) FROM voice_channels))', (slug,name))
    return {'slug':slug,'name':name,'members':[]}


@app.delete('/api/voice/channels/{slug}')
async def delete_voice_channel(slug: str, request: Request):
    require_owner(request)
    if slug == 'lounge':
        raise HTTPException(403, 'The Lounge room cannot be deleted.')
    voice_room_exists(slug)
    with db() as c:
        c.execute('DELETE FROM voice_channels WHERE slug=?', (slug,))
    for ws, (room, _) in list(voice_connections.items()):
        if room == slug:
            try:
                await ws.close(code=1008, reason='Voice room deleted')
            except RuntimeError:
                pass
    return {'ok':True}


@app.get('/api/voice/ice-config')
def ice_config(request: Request):
    require_user(request)
    # Optional TURN support: supply a JSON array via Railway WEBRTC_ICE_SERVERS.
    default = [{'urls':['stun:stun.l.google.com:19302','stun:stun1.l.google.com:19302']}]
    try:
        configured = json.loads(os.environ.get('WEBRTC_ICE_SERVERS', 'null'))
    except json.JSONDecodeError:
        configured = None
    if not isinstance(configured, list) or len(configured)>8 or any(not isinstance(i,dict) or not isinstance(i.get('urls'),(str,list)) for i in configured):
        configured = default
    return {'iceServers':configured}


async def voice_emit(slug: str, data: dict, exclude: WebSocket | None = None):
    for ws, (room, _) in list(voice_connections.items()):
        if room == slug and ws is not exclude:
            try:
                await ws.send_json(data)
            except (RuntimeError, WebSocketDisconnect):
                pass


@app.websocket('/ws/voice')
async def voice_socket(ws: WebSocket):
    origin = ws.headers.get('origin')
    if origin and urlparse(origin).netloc.lower() != ws.headers.get('host','').lower():
        await ws.close(code=1008)
        return
    user = auth_user(ws.cookies.get('mrh_session'))
    slug = ws.query_params.get('room','')
    if not user or not CHANNEL_RE.fullmatch(slug):
        await ws.close(code=1008)
        return
    with db() as c:
        valid = c.execute('SELECT 1 FROM voice_channels WHERE slug=?', (slug,)).fetchone()
    if not valid:
        await ws.close(code=1008)
        return
    if len([1 for room, _ in voice_connections.values() if room==slug]) >= VOICE_CAPACITY:
        await ws.close(code=1008,reason='Voice room full')
        return
    # One voice room per logged-in user, even across multiple tabs.
    for older_ws, (_, uid) in list(voice_connections.items()):
        if uid == user['id']:
            try:
                await older_ws.close(code=1008,reason='Joined from another tab')
            except RuntimeError:
                pass
            voice_connections.pop(older_ws,None)
            voice_status.pop(older_ws,None)
    await ws.accept()
    voice_connections[ws] = (slug, user['id'])
    voice_status[ws] = {'muted':True,'sharing':False}
    await ws.send_json({'type':'voice-roster','room':slug,'members':voice_members(slug),'self_id':user['id']})
    await voice_emit(slug, {'type':'voice-joined','user':{**user_dict(user),'muted':True,'sharing':False}}, exclude=ws)
    signaling_times = deque(maxlen=100)
    try:
        while True:
            packet = await ws.receive_json()
            if not isinstance(packet,dict) or voice_connections.get(ws)!=(slug,user['id']):
                break
            kind = packet.get('type')
            if kind=='voice-status':
                # The client cannot set status for other members.
                st=voice_status[ws]
                st['muted'] = packet.get('muted') is True
                st['sharing'] = packet.get('sharing') is True
                await voice_emit(slug,{'type':'voice-status','user_id':user['id'],**st})
            elif kind in ('offer','answer','ice'):
                now = time.monotonic()
                signaling_times.append(now)
                if sum(1 for n in signaling_times if n>now-10)>70:
                    await ws.close(code=1008,reason='Too much signaling')
                    break
                target_id = packet.get('to')
                payload = packet.get('data')
                if type(target_id) is not int or target_id==user['id'] or not isinstance(payload,dict):
                    continue
                if kind in ('offer','answer'):
                    if payload.get('type')!=kind or not isinstance(payload.get('sdp'),str) or len(payload['sdp'])>24000:
                        continue
                    sanitized={'type':kind,'sdp':payload['sdp']}
                else:
                    if len(json.dumps(payload))>4500 or 'candidate' not in payload:
                        continue
                    sanitized={'candidate':payload.get('candidate'),'sdpMid':payload.get('sdpMid'),
                               'sdpMLineIndex':payload.get('sdpMLineIndex')}
                for peer_ws,(peer_room,peer_uid) in list(voice_connections.items()):
                    if peer_uid==target_id and peer_room==slug:
                        await peer_ws.send_json({'type':'voice-signal','from':user['id'],'signal':kind,'data':sanitized})
                        break
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        if voice_connections.get(ws)==(slug,user['id']):
            voice_connections.pop(ws,None)
            voice_status.pop(ws,None)
            await voice_emit(slug,{'type':'voice-left','user_id':user['id']})


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
        validate_reply(c, data.reply_to_id, u['id'], slug, None)
    check_spam(u,'message',body)
    with db() as c:
        validate_reply(c, data.reply_to_id, u['id'], slug, None)
        cur = c.execute('INSERT INTO messages(sender_id,channel_slug,body,reply_to_id) VALUES (?,?,?,?)', (u['id'], slug, body, data.reply_to_id))
        msg = message_dict(c.execute(f'SELECT {MESSAGE_COLS} FROM messages m JOIN users u ON u.id=m.sender_id LEFT JOIN attachments a ON a.message_id=m.id WHERE m.id=?', (cur.lastrowid,)).fetchone())
        mentions=record_pings(c,u['id'],body,'channel',cur.lastrowid,slug)
    await notify_pings(mentions)
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
        validate_reply(c, data.reply_to_id, u['id'], None, other_id)
    check_spam(u,'message',body)
    with db() as c:
        validate_reply(c, data.reply_to_id, u['id'], None, other_id)
        cur = c.execute('INSERT INTO messages(sender_id,recipient_id,body,reply_to_id) VALUES (?,?,?,?)', (u['id'], other_id, body, data.reply_to_id))
        msg = message_dict(c.execute(f'SELECT {MESSAGE_COLS} FROM messages m JOIN users u ON u.id=m.sender_id LEFT JOIN attachments a ON a.message_id=m.id WHERE m.id=?', (cur.lastrowid,)).fetchone())
        mentions=record_pings(c,u['id'],body,'dm',cur.lastrowid,str(other_id))
    await notify_pings(mentions)
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
