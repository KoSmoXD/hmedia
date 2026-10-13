"""Mr H Media add-on: moderation-safe comments, mentions, themes and spam controls."""
import asyncio
import hashlib
import json
import re
import sqlite3
import time
from collections import defaultdict
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

def install(app):
    from app import db, require_user, auth_user, user_dict, connections
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS mh_comments(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
          author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          body TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE INDEX IF NOT EXISTS mh_comments_post ON mh_comments(post_id,id);
        CREATE TABLE IF NOT EXISTS mh_themes(
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mh_spam(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          category TEXT NOT NULL, fingerprint TEXT NOT NULL, at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS mh_spam_window ON mh_spam(user_id,category,at);
        CREATE TABLE IF NOT EXISTS mh_timeouts(
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          until_at REAL NOT NULL DEFAULT 0, strikes INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS mh_pings(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, context TEXT NOT NULL, preview TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          read_at TEXT
        );
        CREATE INDEX IF NOT EXISTS mh_pings_user ON mh_pings(user_id,id DESC);
        """)
    DEFAULT={'accent':'#8a7eff','sidebar':'#151620','chat':'#1a1b26','text':'#e8e9f2'}
    class Theme(BaseModel):
        accent: str='#8a7eff'
        sidebar: str='#151620'
        chat: str='#1a1b26'
        text: str='#e8e9f2'
    class Comment(BaseModel):
        body: str=Field(min_length=1,max_length=1500)

    @app.get('/api/me/theme')
    def get_theme(request:Request):
        u=require_user(request)
        with db() as c:
            r=c.execute('SELECT data FROM mh_themes WHERE user_id=?',(u['id'],)).fetchone()
        return json.loads(r['data']) if r else DEFAULT

    @app.patch('/api/me/theme')
    def save_theme(data:Theme,request:Request):
        u=require_user(request)
        colors=data.model_dump()
        if any(not re.fullmatch(r'#[0-9a-fA-F]{6}',v) for v in colors.values()):
            raise HTTPException(400,'Use six-digit hex colors.')
        with db() as c:
            c.execute('INSERT INTO mh_themes(user_id,data) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data',(u['id'],json.dumps(colors)))
        return colors

    def valid_post(c,pid):
        if not c.execute('SELECT 1 FROM posts WHERE id=?',(pid,)).fetchone():
            raise HTTPException(404,'Post not found.')

    def serialize(row):
        return {'id':row['id'],'post_id':row['post_id'],'body':row['body'],'created_at':row['created_at'],
                'user':{'id':row['user_id'],'username':row['username'],'display_name':row['display_name'],
                        'avatar':row['avatar'],'role':row['role']}}

    QUERY='''SELECT m.id,m.post_id,m.body,m.created_at,u.id user_id,u.username,u.display_name,u.avatar,u.role
             FROM mh_comments m JOIN users u ON u.id=m.author_id'''

    @app.get('/api/posts/{post_id}/comments')
    def comments(post_id:int,request:Request):
        require_user(request)
        with db() as c:
            valid_post(c,post_id)
            rows=c.execute(QUERY+' WHERE m.post_id=? ORDER BY m.id DESC LIMIT 200',(post_id,)).fetchall()
        return [serialize(r) for r in reversed(rows)]

    @app.post('/api/posts/{post_id}/comments',status_code=201)
    def add_comment(post_id:int,data:Comment,request:Request):
        u=require_user(request)
        body=data.body.strip()
        if not body:
            raise HTTPException(400,'Comment cannot be empty.')
        with db() as c:
            valid_post(c,post_id)
            cur=c.execute('INSERT INTO mh_comments(post_id,author_id,body) VALUES (?,?,?)',(post_id,u['id'],body))
            return serialize(c.execute(QUERY+' WHERE m.id=?',(cur.lastrowid,)).fetchone())

    @app.delete('/api/posts/{post_id}/comments/{comment_id}')
    def remove_comment(post_id:int,comment_id:int,request:Request):
        u=require_user(request)
        with db() as c:
            r=c.execute('SELECT author_id FROM mh_comments WHERE id=? AND post_id=?',(comment_id,post_id)).fetchone()
            if not r: raise HTTPException(404,'Comment not found.')
            if r['author_id']!=u['id'] and u['role']!='owner':
                raise HTTPException(403,'Only the author or owner can delete this comment.')
            c.execute('DELETE FROM mh_comments WHERE id=?',(comment_id,))
        return {'ok':True}

    @app.get('/api/notifications')
    def notifications(request:Request):
        u=require_user(request)
        with db() as c:
            rows=c.execute('''SELECT n.*,u.username sender,u.display_name sender_name FROM mh_pings n
                              JOIN users u ON u.id=n.sender_id WHERE n.user_id=? ORDER BY n.id DESC LIMIT 60''',(u['id'],)).fetchall()
        return [{'id':r['id'],'sender':r['sender'],'sender_name':r['sender_name'],'kind':r['kind'],
                 'context':r['context'],'preview':r['preview'],'read':bool(r['read_at']),
                 'created_at':r['created_at']} for r in rows]

    @app.post('/api/notifications/read')
    def mark_read(request:Request):
        u=require_user(request)
        with db() as c:
            c.execute("UPDATE mh_pings SET read_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE user_id=? AND read_at IS NULL",(u['id'],))
        return {'ok':True}

    limits={'message':(6,10,35,60),'upload':(3,60,10,3600),'post':(3,60,9,3600),
            'comment':(8,30,35,3600),'avatar':(5,3600,5,3600)}
    locks=defaultdict(asyncio.Lock)
    mention=re.compile(r'(?<![\\w@])@([a-zA-Z0-9_]{3,24})\\b')
    async def store_mentions(actor,body,kind,context):
        names=list(dict.fromkeys(x.casefold() for x in mention.findall(body)))[:5]
        if not names: return
        with db() as c:
            for name in names:
                target=c.execute('SELECT id FROM users WHERE username=? COLLATE NOCASE AND banned_at IS NULL',(name,)).fetchone()
                if not target or target['id']==actor['id']: continue
                c.execute('INSERT INTO mh_pings(user_id,sender_id,kind,context,preview) VALUES (?,?,?,?,?)',
                          (target['id'],actor['id'],kind,context,body[:200]))
                for ws,(_,uid) in list(connections.items()):
                    if uid==target['id']:
                        try: await ws.send_json({'type':'ping-alert'})
                        except Exception: connections.pop(ws,None)

    def route_class(path):
        if re.fullmatch(r'/api/channels/[a-z0-9-]+/messages',path) or re.fullmatch(r'/api/dms/\\d+/messages',path):
            return 'message'
        if re.fullmatch(r'/api/channels/[a-z0-9-]+/attachments',path) or re.fullmatch(r'/api/dms/\\d+/attachments',path):
            return 'upload'
        if path in ('/api/posts','/api/posts/with-file'): return 'post'
        if re.fullmatch(r'/api/posts/\\d+/comments',path): return 'comment'
        if path=='/api/me/avatar': return 'avatar'
        return None

    @app.middleware('http')
    async def anti_spam_and_pings(request:Request,call_next):
        category=route_class(request.url.path) if request.method=='POST' else None
        if not category: return await call_next(request)
        actor=auth_user(request.cookies.get('mrh_session'))
        if not actor: return await call_next(request)
        if actor['role']=='owner': return await call_next(request)
        # Cache the submitted content so the underlying endpoint can read it too.
        raw=await request.body()
        if len(raw)>10*1024*1024:
            return await call_next(request)
        body=''
        if 'application/json' in request.headers.get('content-type',''):
            try:
                item=json.loads(raw)
                body=' '.join(str(item.get(x,'')) for x in ('title','body'))
            except (ValueError,TypeError): pass
        else:
            # Multipart: the filename and the text body make a stable fingerprint.
            try:
                form=await request.form()
                body=' '.join(str(form.get(x,'')) for x in ('title','body'))
                up=form.get('file')
                if up is not None: body+=' '+str(getattr(up,'filename',''))
            except Exception: pass
        now=time.time()
        fingerprint=hashlib.sha256(' '.join(body.casefold().split()).encode()).hexdigest()
        limit,window,large,large_window=limits[category]
        async with locks[actor['id']]:
            with db() as c:
                state=c.execute('SELECT until_at,strikes FROM mh_timeouts WHERE user_id=?',(actor['id'],)).fetchone()
                if state and state['until_at']>now:
                    seconds=int(state['until_at']-now)+1
                    return JSONResponse({'detail':f'Anti-spam timeout. Try again in {seconds} seconds.'},429,headers={'Retry-After':str(seconds)})
                recent=c.execute('SELECT at,fingerprint FROM mh_spam WHERE user_id=? AND category=? AND at>? ORDER BY at DESC',
                                 (actor['id'],category,now-large_window)).fetchall()
                duplicate=bool(body.strip()) and any(x['fingerprint']==fingerprint and x['at']>now-45 for x in recent)
                too_fast=sum(x['at']>now-window for x in recent)>=limit or len(recent)>=large
                if duplicate or too_fast:
                    strike=min(8,(state['strikes'] if state else 0)+1)
                    timeout=min(300,10*(2**(strike-1)))
                    c.execute('INSERT INTO mh_timeouts(user_id,until_at,strikes) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET until_at=excluded.until_at,strikes=excluded.strikes',
                              (actor['id'],now+timeout,strike))
                    return JSONResponse({'detail':('Duplicate content. ' if duplicate else 'Too many actions. ')+f'Try again in {timeout} seconds.'},429,headers={'Retry-After':str(timeout)})
            response=await call_next(request)
            if response.status_code>=400: return response
            with db() as c:
                c.execute('INSERT INTO mh_spam(user_id,category,fingerprint,at) VALUES (?,?,?,?)',
                          (actor['id'],category,fingerprint,now))
                c.execute('DELETE FROM mh_spam WHERE user_id=? AND at<?',(actor['id'],now-3600))
        if body.strip():
            path=request.url.path
            kind='post' if '/posts' in path else 'channel' if '/channels/' in path else 'dm'
            context=(re.findall(r'/channels/([^/]+)',path) or re.findall(r'/posts/(\\d+)',path) or re.findall(r'/dms/(\\d+)',path) or ['posts'])[0]
            await store_mentions(actor,body,kind,context)
        return response
