"""Mr H Media v8 social, moderation and conversation extensions.
All migrations are additive and preserve existing state.
"""
import json, re, time, secrets, io, hashlib
from pathlib import Path
from fastapi import HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError, ImageSequence

DEFAULT_ROLES={
 'owner': ('Owner','#ffcf67',['*']),
 'admin': ('Admin','#ef8b91',['manage_channels','moderate','ban_users','manage_emoji','manage_roles','manage_posts']),
 'moderator': ('Moderator','#74b9fe',['moderate','manage_posts']),
 'vip': ('VIP','#c8a6ff',[]),
 'member': ('Member','#acafba',[]),
}
ROLE_ID=re.compile(r'^[a-z][a-z0-9_-]{1,23}$')
HEX=re.compile(r'^#[0-9a-fA-F]{6}$')
EMOJI_NAME=re.compile(r'^[a-zA-Z0-9_]{2,24}$')
ALLOWED_PERMS={'manage_channels','moderate','ban_users','manage_emoji','manage_roles','manage_posts'}

def install(ns):
    app=ns['app']; db=ns['db']; require_user=ns['require_user']; emit=ns['emit']; user_dict=ns['user_dict']
    check_spam=ns['check_spam']; UPLOADS=ns['UPLOADS']; attachments_dir=ns['ATTACHMENTS_DIR']; store_attachment=ns['store_attachment']
    def setup():
        with db() as c:
            for table,col,sql in [
              ('users','status',"TEXT NOT NULL DEFAULT ''"),('users','banner',"TEXT"),
              ('users','profile_color',"TEXT NOT NULL DEFAULT '#8a7eff'"),
              ('users','notification_settings',"TEXT NOT NULL DEFAULT '{\"desktop\":false,\"mentions\":true,\"dms\":true,\"friends\":true}'"),
              ('users','mod_timeout_until','REAL NOT NULL DEFAULT 0'),
              ('channels','slowmode_seconds','INTEGER NOT NULL DEFAULT 0')]:
                if col not in {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {sql}')
            c.executescript('''
            CREATE TABLE IF NOT EXISTS roles(slug TEXT PRIMARY KEY,name TEXT NOT NULL,color TEXT NOT NULL,permissions TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS friends(a INTEGER NOT NULL REFERENCES users(id),b INTEGER NOT NULL REFERENCES users(id),
               requested_by INTEGER NOT NULL REFERENCES users(id),state TEXT NOT NULL CHECK(state IN ('pending','accepted')),
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(a,b),CHECK(a<b));
            CREATE TABLE IF NOT EXISTS blocks(blocker INTEGER NOT NULL REFERENCES users(id),blocked INTEGER NOT NULL REFERENCES users(id),
               PRIMARY KEY(blocker,blocked),CHECK(blocker<>blocked));
            CREATE TABLE IF NOT EXISTS group_chats(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,creator_id INTEGER NOT NULL REFERENCES users(id),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
              user_id INTEGER NOT NULL REFERENCES users(id),PRIMARY KEY(group_id,user_id));
            CREATE TABLE IF NOT EXISTS group_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
              group_id INTEGER NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
              sender_id INTEGER NOT NULL REFERENCES users(id),body TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
            CREATE INDEX IF NOT EXISTS group_messages_idx ON group_messages(group_id,id);
            CREATE TABLE IF NOT EXISTS group_attachments(
              id INTEGER PRIMARY KEY AUTOINCREMENT,group_id INTEGER NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
              message_id INTEGER UNIQUE NOT NULL REFERENCES group_messages(id) ON DELETE CASCADE,
              sender_id INTEGER NOT NULL REFERENCES users(id), filename TEXT NOT NULL,stored_name TEXT NOT NULL UNIQUE,
              media_type TEXT NOT NULL,size INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS message_reactions(message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
              user_id INTEGER NOT NULL REFERENCES users(id),emoji TEXT NOT NULL,PRIMARY KEY(message_id,user_id,emoji));
            CREATE TABLE IF NOT EXISTS custom_emojis(name TEXT PRIMARY KEY,url TEXT NOT NULL,creator_id INTEGER NOT NULL REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER NOT NULL REFERENCES users(id),room TEXT NOT NULL,last_id INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(user_id,room));
            CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
              pinned_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER NOT NULL REFERENCES users(id),
              action TEXT NOT NULL,target TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS raids(ip_hash TEXT NOT NULL,created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS raids_window ON raids(ip_hash,created_at);
            ''')
            for slug,(name,color,perms) in DEFAULT_ROLES.items():
                c.execute('INSERT OR IGNORE INTO roles(slug,name,color,permissions) VALUES (?,?,?,?)',(slug,name,color,json.dumps(perms)))
    setup()
    def decorate(row):
        with db() as c: role=c.execute('SELECT name,color,permissions FROM roles WHERE slug=?',(row['role'],)).fetchone()
        return {'status':row['status'],'banner':row['banner'],'profile_color':row['profile_color'],
                'role_name':role['name'] if role else row['role'],
                'role_color':role['color'] if role else '#aaa',
                'notification_settings':json.loads(row['notification_settings'] or '{}')}
    ns['v8_decorate_user']=decorate
    def permissions(u):
        if u['role']=='owner':return {'*'}
        with db() as c:
            r=c.execute('SELECT permissions FROM roles WHERE slug=?',(u['role'],)).fetchone()
        return set(json.loads(r['permissions'])) if r else set()
    def allowed(u,permission): return '*' in permissions(u) or permission in permissions(u)
    def must(u,perm):
        if not allowed(u,perm):raise HTTPException(403,'Insufficient role permissions.')
    ns['v8_allowed']=allowed
    def audit(c,u,action,target,detail=''):
        c.execute('INSERT INTO audit_logs(actor_id,action,target,detail) VALUES (?,?,?,?)',(u['id'],action,str(target),detail[:500]))
    ns['v8_audit']=audit
    def blocked(c,a,b):
        return bool(c.execute('SELECT 1 FROM blocks WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)',(a,b,b,a)).fetchone())
    ns['v8_blocked']=blocked
    def summary_reactions(message_id):
        with db() as c:
            return [{'emoji':r['emoji'],'count':r['count']} for r in c.execute('SELECT emoji,COUNT(*) AS count FROM message_reactions WHERE message_id=? GROUP BY emoji',(message_id,))]
    ns['v8_message_reactions']=summary_reactions
    def friend_key(a,b): return (min(a,b),max(a,b))
    def user_exists(c,user_id):
        row=c.execute('SELECT * FROM users WHERE id=? AND banned_at IS NULL',(user_id,)).fetchone()
        if row is None:raise HTTPException(404,'User not found.')
        return row
    def no_self(u,id):
        if u['id']==id:raise HTTPException(400,'Cannot perform this action on yourself.')
    def valid_group(c,u,group_id):
        if not c.execute('SELECT 1 FROM group_members WHERE user_id=? AND group_id=?',(u['id'],group_id)).fetchone():
            raise HTTPException(403,'Not a member of this group.')
    ns['v8_valid_group']=valid_group
    def can_voice(u,slug):
        if not slug.startswith('group-'): return True
        if not slug[6:].isdigit():return False
        with db() as c: return bool(c.execute('SELECT 1 FROM group_members WHERE group_id=? AND user_id=?',(int(slug[6:]),u['id'])).fetchone())
    ns['v8_can_voice']=can_voice

    class RoleCreate(BaseModel):
        slug:str; name:str=Field(max_length=30); color:str='#8a7eff'; permissions:list[str]=Field(default_factory=list)
    class AssignRole(BaseModel):role:str
    class FriendAction(BaseModel):user_id:int
    class CreateGroup(BaseModel):name:str=Field(min_length=1,max_length=55); members:list[int]=Field(min_length=1,max_length=7)
    class AddGroupMember(BaseModel):user_id:int
    class GroupMessage(BaseModel):body:str=Field(min_length=1,max_length=2000)
    class Reaction(BaseModel):emoji:str=Field(min_length=1,max_length=32)
    class ProfileMore(BaseModel):status:str=Field(default='',max_length=100); profile_color:str='#8a7eff'
    class NotificationSettings(BaseModel):desktop:bool=False; mentions:bool=True; dms:bool=True; friends:bool=True
    class TimeoutBody(BaseModel):seconds:int=Field(ge=0,le=604800);reason:str=Field(default='',max_length=240)
    class SlowMode(BaseModel):seconds:int=Field(ge=0,le=3600)
    class SearchQuery(BaseModel):q:str

    @app.get('/api/roles')
    def role_list(request:Request):
        require_user(request)
        with db() as c:return [{**dict(r),'permissions':json.loads(r['permissions'])} for r in c.execute('SELECT * FROM roles ORDER BY CASE slug WHEN "owner" THEN 0 WHEN "admin" THEN 1 WHEN "moderator" THEN 2 WHEN "vip" THEN 3 ELSE 4 END,name')]
    @app.post('/api/roles',status_code=201)
    def role_create(data:RoleCreate,request:Request):
        u=require_user(request);must(u,'manage_roles')
        if not ROLE_ID.fullmatch(data.slug) or not HEX.fullmatch(data.color) or not data.name.strip() or set(data.permissions)-ALLOWED_PERMS:raise HTTPException(400,'Invalid role definition.')
        if len(data.permissions)!=len(set(data.permissions)):raise HTTPException(400,'Duplicate permissions.')
        with db() as c:
            if c.execute('SELECT 1 FROM roles WHERE slug=?',(data.slug,)).fetchone():raise HTTPException(409,'Role exists.')
            c.execute('INSERT INTO roles VALUES (?,?,?,?)',(data.slug,data.name.strip(),data.color,json.dumps(data.permissions)))
            audit(c,u,'role.create',data.slug)
        return data.model_dump()
    @app.patch('/api/roles/{slug}')
    def role_update(slug:str,data:RoleCreate,request:Request):
        u=require_user(request);must(u,'manage_roles')
        if slug=='owner' or slug!=data.slug or not HEX.fullmatch(data.color) or set(data.permissions)-ALLOWED_PERMS:raise HTTPException(403,'Cannot edit this role.')
        with db() as c:
            if not c.execute('SELECT 1 FROM roles WHERE slug=?',(slug,)).fetchone():raise HTTPException(404,'Role not found.')
            c.execute('UPDATE roles SET name=?,color=?,permissions=? WHERE slug=?',(data.name.strip(),data.color,json.dumps(data.permissions),slug));audit(c,u,'role.update',slug)
        return {'ok':True}
    @app.post('/api/roles/assign/{user_id}')
    def role_assign(user_id:int,data:AssignRole,request:Request):
        u=require_user(request);must(u,'manage_roles')
        with db() as c:
            target=user_exists(c,user_id)
            if target['role']=='owner' or data.role=='owner':raise HTTPException(403,'The owner role cannot be reassigned.')
            if not c.execute('SELECT 1 FROM roles WHERE slug=?',(data.role,)).fetchone():raise HTTPException(404,'Role does not exist.')
            c.execute('UPDATE users SET role=? WHERE id=?',(data.role,user_id));audit(c,u,'role.assign',user_id,data.role)
        return {'ok':True}

    @app.get('/api/friends')
    def friend_list(request:Request):
        u=require_user(request)
        with db() as c:
            return [{'state':r['state'],'requested_by':r['requested_by'], 'user':user_dict(user_exists(c,r['other']))} for r in c.execute('''SELECT state,requested_by,CASE WHEN a=? THEN b ELSE a END AS other
                FROM friends WHERE a=? OR b=? ORDER BY created_at DESC''',(u['id'],u['id'],u['id']))]
    @app.post('/api/friends/request')
    def friend_request(data:FriendAction,request:Request):
        u=require_user(request);no_self(u,data.user_id);check_spam(u,'comment',f'friend:{data.user_id}')
        with db() as c:
            user_exists(c,data.user_id)
            if blocked(c,u['id'],data.user_id):raise HTTPException(403,'This friend request is not allowed.')
            a,b=friend_key(u['id'],data.user_id)
            if c.execute('SELECT 1 FROM friends WHERE a=? AND b=?',(a,b)).fetchone():raise HTTPException(409,'Request already exists.')
            c.execute('INSERT INTO friends(a,b,requested_by,state) VALUES (?,?,?,?)',(a,b,u['id'],'pending'))
            pref=json.loads(c.execute('SELECT notification_settings FROM users WHERE id=?',(data.user_id,)).fetchone()[0])
            if pref.get('friends',True):c.execute('INSERT INTO pings(recipient_id,sender_id,kind,target_id,context,preview) VALUES (?,?,?,?,?,?)',(data.user_id,u['id'],'friend',u['id'],'friends','Sent you a friend request'))
        return {'ok':True}
    @app.post('/api/friends/{other_id}/accept')
    def friend_accept(other_id:int,request:Request):
        u=require_user(request);a,b=friend_key(u['id'],other_id)
        with db() as c:
            r=c.execute('SELECT * FROM friends WHERE a=? AND b=?',(a,b)).fetchone()
            if not r or r['state']!='pending' or r['requested_by']==u['id'] or blocked(c,u['id'],other_id):raise HTTPException(403,'No incoming request.')
            c.execute('UPDATE friends SET state="accepted" WHERE a=? AND b=?',(a,b))
        return {'ok':True}
    @app.delete('/api/friends/{other_id}')
    def friend_remove(other_id:int,request:Request):
        u=require_user(request);a,b=friend_key(u['id'],other_id)
        with db() as c:c.execute('DELETE FROM friends WHERE a=? AND b=?',(a,b))
        return {'ok':True}
    @app.post('/api/blocks/{other_id}')
    def block_user(other_id:int,request:Request):
        u=require_user(request);no_self(u,other_id)
        with db() as c:
            user_exists(c,other_id);c.execute('INSERT OR IGNORE INTO blocks VALUES (?,?)',(u['id'],other_id))
            a,b=friend_key(u['id'],other_id);c.execute('DELETE FROM friends WHERE a=? AND b=?',(a,b))
        return {'ok':True}
    @app.delete('/api/blocks/{other_id}')
    def unblock_user(other_id:int,request:Request):
        u=require_user(request)
        with db() as c:c.execute('DELETE FROM blocks WHERE blocker=? AND blocked=?',(u['id'],other_id))
        return {'ok':True}
    @app.get('/api/blocks')
    def list_blocks(request:Request):
        u=require_user(request)
        with db() as c:return [r['blocked'] for r in c.execute('SELECT blocked FROM blocks WHERE blocker=?',(u['id'],))]

    def group_result(c,group_id):
        g=c.execute('SELECT * FROM group_chats WHERE id=?',(group_id,)).fetchone()
        if not g:raise HTTPException(404,'Group not found.')
        return {'id':g['id'],'name':g['name'],'creator_id':g['creator_id'],
            'members':[user_dict(r) for r in c.execute('SELECT u.* FROM group_members gm JOIN users u ON u.id=gm.user_id WHERE gm.group_id=?',(group_id,))]}
    @app.post('/api/groups',status_code=201)
    def group_create(data:CreateGroup,request:Request):
        u=require_user(request)
        ids=set(data.members)-{u['id']}
        if not ids or len(ids)>7 or not data.name.strip():raise HTTPException(400,'Choose 1–7 other members.')
        with db() as c:
            for uid in ids:
                user_exists(c,uid)
                if blocked(c,u['id'],uid):raise HTTPException(403,'Cannot create a group with a blocked user.')
            gid=c.execute('INSERT INTO group_chats(name,creator_id) VALUES (?,?)',(data.name.strip(),u['id'])).lastrowid
            for uid in ids|{u['id']}:c.execute('INSERT INTO group_members VALUES (?,?)',(gid,uid))
            c.execute('INSERT INTO voice_channels(slug,name,sort_order) VALUES (?,?,0)',(f'group-{gid}',f'Group {gid} call'))
            return group_result(c,gid)
    @app.get('/api/groups')
    def group_list(request:Request):
        u=require_user(request)
        with db() as c:
            return [group_result(c,r['group_id']) for r in c.execute('SELECT group_id FROM group_members WHERE user_id=? ORDER BY group_id DESC',(u['id'],)).fetchall()]
    @app.post('/api/groups/{group_id}/members')
    def group_add(group_id:int,data:AddGroupMember,request:Request):
        u=require_user(request)
        with db() as c:
            valid_group(c,u,group_id);user_exists(c,data.user_id)
            if blocked(c,u['id'],data.user_id):raise HTTPException(403,'Cannot invite a blocked user.')
            if c.execute('SELECT COUNT(*) FROM group_members WHERE group_id=?',(group_id,)).fetchone()[0]>=8:raise HTTPException(409,'Group is full.')
            c.execute('INSERT OR IGNORE INTO group_members VALUES (?,?)',(group_id,data.user_id))
            return group_result(c,group_id)
    @app.delete('/api/groups/{group_id}/members/{user_id}')
    async def group_remove(group_id:int,user_id:int,request:Request):
        u=require_user(request)
        with db() as c:
            valid_group(c,u,group_id)
            owner=c.execute('SELECT creator_id FROM group_chats WHERE id=?',(group_id,)).fetchone()['creator_id']
            if u['id']!=user_id and u['id']!=owner:raise HTTPException(403,'Cannot remove that member.')
            c.execute('DELETE FROM group_members WHERE group_id=? AND user_id=?',(group_id,user_id))
            if c.execute('SELECT COUNT(*) FROM group_members WHERE group_id=?',(group_id,)).fetchone()[0]==0:
                c.execute('DELETE FROM voice_channels WHERE slug=?',(f'group-{group_id}',))
                c.execute('DELETE FROM group_chats WHERE id=?',(group_id,))
        for ws,(key,uid) in list(ns['connections'].items()):
            if uid==user_id and key==f'group:{group_id}':
                try:await ws.close(code=1008,reason='Removed from group')
                except RuntimeError:pass
                ns['connections'].pop(ws,None)
        for ws,(room,uid) in list(ns['voice_connections'].items()):
            if uid==user_id and room==f'group-{group_id}':
                try:await ws.close(code=1008,reason='Removed from group')
                except RuntimeError:pass
        return {'ok':True}
    def group_message_dict(c,r):
        a=c.execute('SELECT id,filename,media_type,size FROM group_attachments WHERE message_id=?',(r['id'],)).fetchone()
        attachment={'id':a['id'],'name':a['filename'],'mime':a['media_type'],'size':a['size'],'url':'/api/groups/files/'+str(a['id'])} if a else None
        return {'id':r['id'],'body':r['body'],'created_at':r['created_at'], 'user':user_dict(c.execute('SELECT * FROM users WHERE id=?',(r['sender_id'],)).fetchone()),
                'group_id':r['group_id'],'attachment':attachment,'reply':None}
    @app.get('/api/groups/{group_id}/messages')
    def group_messages(group_id:int,request:Request,before:int|None=None):
        u=require_user(request)
        with db() as c:
            valid_group(c,u,group_id)
            if before:return [group_message_dict(c,r) for r in reversed(c.execute('SELECT * FROM group_messages WHERE group_id=? AND id<? ORDER BY id DESC LIMIT 80',(group_id,before)).fetchall())]
            return [group_message_dict(c,r) for r in reversed(c.execute('SELECT * FROM group_messages WHERE group_id=? ORDER BY id DESC LIMIT 80',(group_id,)).fetchall())]
    @app.post('/api/groups/{group_id}/messages',status_code=201)
    async def group_send(group_id:int,data:GroupMessage,request:Request):
        u=require_user(request);body=data.body.strip()
        if not body:raise HTTPException(400,'Empty message.')
        with db() as c:valid_group(c,u,group_id)
        check_spam(u,'message',body)
        with db() as c:
            valid_group(c,u,group_id)
            mid=c.execute('INSERT INTO group_messages(group_id,sender_id,body) VALUES (?,?,?)',(group_id,u['id'],body)).lastrowid
            result=group_message_dict(c,c.execute('SELECT * FROM group_messages WHERE id=?',(mid,)).fetchone())
            alert_ids=[]
            for member in c.execute('SELECT gm.user_id,us.notification_settings FROM group_members gm JOIN users us ON us.id=gm.user_id WHERE gm.group_id=?',(group_id,)):
                if member['user_id']==u['id'] or not json.loads(member['notification_settings']).get('dms',True):continue
                c.execute('INSERT INTO pings(recipient_id,sender_id,kind,target_id,context,preview) VALUES (?,?,?,?,?,?)',(member['user_id'],u['id'],'group',mid,str(group_id),body[:180]))
                alert_ids.append(member['user_id'])
        await emit(f'group:{group_id}',{'type':'message','message':result})
        await ns['notify_pings'](alert_ids)
        return result

    @app.post('/api/groups/{group_id}/attachments',status_code=201)
    async def group_upload(group_id:int,request:Request,file:UploadFile=File(...),body:str=Form(default='')):
        u=require_user(request)
        if len(body)>2000:raise HTTPException(400,'Message too long.')
        with db() as c:valid_group(c,u,group_id)
        name,stored,mime,size=await store_attachment(file,request)
        try:
            check_spam(u,'upload',body or 'group-file:'+str(time.time()))
            with db() as c:
                valid_group(c,u,group_id)
                mid=c.execute('INSERT INTO group_messages(group_id,sender_id,body) VALUES (?,?,?)',(group_id,u['id'],body.strip())).lastrowid
                c.execute('INSERT INTO group_attachments(group_id,message_id,sender_id,filename,stored_name,media_type,size) VALUES (?,?,?,?,?,?,?)',
                          (group_id,mid,u['id'],name,stored,mime,size))
                result=group_message_dict(c,c.execute('SELECT * FROM group_messages WHERE id=?',(mid,)).fetchone())
        except Exception:
            (attachments_dir/stored).unlink(missing_ok=True)
            raise
        await emit(f'group:{group_id}',{'type':'message','message':result})
        return result
    @app.get('/api/groups/files/{attachment_id}')
    def group_file(attachment_id:int,request:Request):
        u=require_user(request)
        with db() as c:
            a=c.execute('SELECT * FROM group_attachments WHERE id=?',(attachment_id,)).fetchone()
            if not a:raise HTTPException(404,'File unavailable.')
            valid_group(c,u,a['group_id'])
        path=attachments_dir/a['stored_name']
        if not path.is_file():raise HTTPException(404,'File missing.')
        inline=a['media_type'] in ('image/png','image/gif')
        return FileResponse(path,media_type=a['media_type'] if inline else 'application/octet-stream',
          filename=a['filename'],content_disposition_type='inline' if inline else 'attachment',
          headers={'Cache-Control':'private,no-store','X-Content-Type-Options':'nosniff'})

    @app.post('/api/messages/{message_id}/reactions')
    async def reaction_toggle(message_id:int,data:Reaction,request:Request):
        u=require_user(request)
        with db() as c:
            m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
            if not m:raise HTTPException(404,'Message not found.')
            if m['recipient_id'] is not None:
                if u['id'] not in (m['sender_id'],m['recipient_id']):raise HTTPException(403,'Private message.')
            if data.emoji.startswith(':') and data.emoji.endswith(':'):
                if not c.execute('SELECT 1 FROM custom_emojis WHERE name=?',(data.emoji[1:-1],)).fetchone():raise HTTPException(400,'Unknown custom emoji.')
            r=c.execute('SELECT 1 FROM message_reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,u['id'],data.emoji)).fetchone()
            if r:c.execute('DELETE FROM message_reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,u['id'],data.emoji))
            else:c.execute('INSERT INTO message_reactions VALUES (?,?,?)',(message_id,u['id'],data.emoji))
            rows=c.execute('SELECT emoji,COUNT(*) AS count FROM message_reactions WHERE message_id=? GROUP BY emoji',(message_id,)).fetchall()
            room=f'channel:{m["channel_slug"]}' if m['channel_slug'] else f'dm:{min(m["sender_id"],m["recipient_id"])}:{max(m["sender_id"],m["recipient_id"])}'
            result=[dict(x) for x in rows]
        await emit(room,{'type':'reaction','message_id':message_id,'reactions':result})
        return {'reactions':result}
    @app.get('/api/messages/{message_id}/reactions')
    def reactions(message_id:int,request:Request):
        u=require_user(request)
        with db() as c:
            m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
            if not m:raise HTTPException(404,'Message not found.')
            if m['recipient_id'] is not None and u['id'] not in (m['sender_id'],m['recipient_id']):raise HTTPException(403,'Private message.')
            return [{'emoji':r['emoji'],'count':r['count'],'mine':bool(c.execute('SELECT 1 FROM message_reactions WHERE message_id=? AND emoji=? AND user_id=?',(message_id,r['emoji'],u['id'])).fetchone())} for r in c.execute('SELECT emoji,COUNT(*) AS count FROM message_reactions WHERE message_id=? GROUP BY emoji',(message_id,))]
    @app.get('/api/emojis')
    def emojis(request:Request):
        require_user(request)
        with db() as c:return [dict(r) for r in c.execute('SELECT name,url FROM custom_emojis ORDER BY name')]
    @app.post('/api/emojis',status_code=201)
    async def emoji_upload(request:Request,name:str, file:UploadFile=File(...)):
        u=require_user(request);must(u,'manage_emoji')
        if not EMOJI_NAME.fullmatch(name):raise HTTPException(400,'Use 2–24 letters, numbers or underscores.')
        raw=await file.read(1024*1024+1)
        if len(raw)>1024*1024:raise HTTPException(413,'Emoji images must be under 1 MB.')
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ('PNG','WEBP','GIF'):raise ValueError('Unsupported format')
                if image.width*image.height>4_000_000:raise ValueError('Image dimensions too large')
                is_animated=image.format=='GIF' and getattr(image,'n_frames',1)>1
                buffer=io.BytesIO()
                if is_animated:
                    if image.n_frames>100:raise ValueError('Too many animation frames')
                    frames=[];durations=[]
                    for frame in ImageSequence.Iterator(image):
                        resized=frame.convert('RGBA')
                        resized.thumbnail((128,128),Image.Resampling.LANCZOS)
                        frames.append(resized)
                        durations.append(max(20,int(frame.info.get('duration',100))))
                    frames[0].save(buffer,format='GIF',save_all=True,append_images=frames[1:],duration=durations,loop=image.info.get('loop',0),disposal=2,optimize=True)
                    ext='gif'
                else:
                    image.thumbnail((128,128),Image.Resampling.LANCZOS)
                    image.convert('RGBA').save(buffer,format='PNG')
                    ext='png'
                if buffer.tell()>1024*1024:raise ValueError('Processed emoji exceeds 1 MB')
        except (OSError,ValueError,UnidentifiedImageError) as e:raise HTTPException(400,'Invalid or oversized emoji image.') from e
        stored='emoji_'+secrets.token_hex(16)+'.'+ext
        (UPLOADS/stored).write_bytes(buffer.getvalue())
        try:
            with db() as c:
                c.execute('INSERT INTO custom_emojis VALUES (?,?,?)',(name,'/api/emojis/file/'+stored,u['id']))
                audit(c,u,'emoji.create',name)
        except Exception:
            (UPLOADS/stored).unlink(missing_ok=True)
            raise HTTPException(409,'Emoji name is taken.')
        return {'name':name,'url':'/api/emojis/file/'+stored}
    @app.get('/api/emojis/file/{filename}')
    def emoji_file(filename:str,request:Request):
        require_user(request)
        if not re.fullmatch(r'emoji_[0-9a-f]{32}\.(png|gif)',filename):raise HTTPException(404)
        path=UPLOADS/filename
        if not path.is_file():raise HTTPException(404)
        return FileResponse(path,media_type='image/gif' if filename.endswith('.gif') else 'image/png',headers={'Cache-Control':'private,max-age=86400','X-Content-Type-Options':'nosniff'})

    @app.patch('/api/me/notifications')
    def notification_prefs(data:NotificationSettings,request:Request):
        u=require_user(request)
        with db() as c:c.execute('UPDATE users SET notification_settings=? WHERE id=?',(data.model_dump_json(),u['id']))
        return data.model_dump()
    @app.get('/api/unread')
    def unread(request:Request):
        u=require_user(request)
        with db() as c:
            rows=c.execute('''SELECT 'channel:'||slug AS room,(SELECT COALESCE(MAX(id),0) FROM messages WHERE channel_slug=slug) AS last_id FROM channels
              UNION ALL SELECT 'dm:'||(CASE WHEN sender_id<? THEN sender_id ELSE ? END)||':'||(CASE WHEN sender_id>? THEN sender_id ELSE ? END) AS room,MAX(id) AS last_id
              FROM messages WHERE recipient_id=? GROUP BY sender_id
              UNION ALL SELECT 'group:'||gm.group_id AS room,COALESCE((SELECT MAX(id) FROM group_messages WHERE group_id=gm.group_id),0) AS last_id FROM group_members gm WHERE gm.user_id=?''',(u['id'],u['id'],u['id'],u['id'],u['id'],u['id'])).fetchall()
            return {r['room']:int(r['last_id'])>int((c.execute('SELECT last_id FROM read_state WHERE user_id=? AND room=?',(u['id'],r['room'])).fetchone() or {'last_id':0})['last_id']) for r in rows}
    @app.post('/api/read/{kind}/{room}')
    def set_read(kind:str,room:str,request:Request):
        u=require_user(request)
        with db() as c:
            if kind=='channel':
                if not c.execute('SELECT 1 FROM channels WHERE slug=?',(room,)).fetchone():raise HTTPException(404,'Channel missing.')
                last=c.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE channel_slug=?',(room,)).fetchone()[0]
                key='channel:'+room
            elif kind=='dm':
                try:other=int(room)
                except ValueError:raise HTTPException(400,'Bad DM ID.')
                user_exists(c,other)
                key=f'dm:{min(u["id"],other)}:{max(u["id"],other)}'
                last=c.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE (sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?)',(u['id'],other,other,u['id'])).fetchone()[0]
            elif kind=='group':
                try:group_id=int(room)
                except ValueError:raise HTTPException(400,'Bad group ID.')
                valid_group(c,u,group_id)
                key=f'group:{group_id}'
                last=c.execute('SELECT COALESCE(MAX(id),0) FROM group_messages WHERE group_id=?',(group_id,)).fetchone()[0]
            else:raise HTTPException(400,'Unknown room type.')
            c.execute('INSERT INTO read_state VALUES (?,?,?) ON CONFLICT(user_id,room) DO UPDATE SET last_id=MAX(last_id,excluded.last_id)',(u['id'],key,last))
            return {'ok':True,'last_id':last}

    @app.patch('/api/me/profile-more')
    def profile_more(data:ProfileMore,request:Request):
        u=require_user(request)
        if not HEX.fullmatch(data.profile_color):raise HTTPException(400,'Invalid profile color.')
        with db() as c:c.execute('UPDATE users SET status=?,profile_color=? WHERE id=?',(data.status.strip(),data.profile_color,u['id']))
        return {'status':data.status,'profile_color':data.profile_color}
    @app.post('/api/me/banner')
    async def upload_banner(request:Request,file:UploadFile=File(...)):
        u=require_user(request)
        raw=await file.read(3*1024*1024+1)
        if len(raw)>3*1024*1024:raise HTTPException(413,'Maximum banner size: 3 MB.')
        try:
            with Image.open(io.BytesIO(raw)) as im:
                im.thumbnail((1200,400));image=im.convert('RGB');buffer=io.BytesIO();image.save(buffer,format='JPEG',quality=84)
        except (OSError,ValueError,UnidentifiedImageError):raise HTTPException(400,'Invalid image.')
        stored=f'{u["id"]}_{secrets.token_hex(12)}.jpg';(UPLOADS/stored).write_bytes(buffer.getvalue())
        with db() as c:c.execute('UPDATE users SET banner=? WHERE id=?',('/uploads/'+stored,u['id']))
        return {'banner':'/uploads/'+stored}
    @app.get('/uploads/{filename}.jpg')
    def banner_file(filename:str):
        if not re.fullmatch(r'[0-9]+_[0-9a-f]{24}',filename):raise HTTPException(404)
        path=UPLOADS/(filename+'.jpg')
        if not path.is_file():raise HTTPException(404)
        return FileResponse(path,media_type='image/jpeg',headers={'Cache-Control':'public,max-age=86400'})

    def moderation_target(c,u,user_id):
        target=user_exists(c,user_id)
        if target['role']=='owner' or target['id']==u['id']:raise HTTPException(403,'Cannot moderate the owner or yourself.')
        ranks={'owner':100,'admin':75,'moderator':50,'vip':20,'member':10}
        if ranks.get(u['role'],15)<=ranks.get(target['role'],15):raise HTTPException(403,'Cannot moderate same or higher role.')
        return target
    @app.post('/api/moderation/users/{user_id}/timeout')
    def timeout(user_id:int,data:TimeoutBody,request:Request):
        u=require_user(request);must(u,'moderate')
        with db() as c:
            moderation_target(c,u,user_id)
            c.execute('UPDATE users SET mod_timeout_until=? WHERE id=?',(time.time()+data.seconds if data.seconds else 0,user_id))
            audit(c,u,'timeout',user_id,json.dumps({'seconds':data.seconds,'reason':data.reason}))
        return {'ok':True}
    @app.delete('/api/moderation/messages/{message_id}')
    async def remove_message(message_id:int,request:Request):
        u=require_user(request)
        with db() as c:
            m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
            if not m:raise HTTPException(404,'Message missing.')
            if u['id']!=m['sender_id']:
                must(u,'moderate')
                if m['recipient_id'] is not None:raise HTTPException(403,'Moderators cannot read or delete private messages.')
            c.execute('DELETE FROM messages WHERE id=?',(message_id,))
            audit(c,u,'message.delete',message_id)
            room=f'channel:{m["channel_slug"]}' if m['channel_slug'] else f'dm:{min(m["sender_id"],m["recipient_id"])}:{max(m["sender_id"],m["recipient_id"])}'
        await emit(room,{'type':'message-deleted','message_id':message_id})
        return {'ok':True}
    @app.patch('/api/channels/{slug}/slowmode')
    def set_slowmode(slug:str,data:SlowMode,request:Request):
        u=require_user(request);must(u,'moderate')
        with db() as c:
            if not c.execute('SELECT 1 FROM channels WHERE slug=?',(slug,)).fetchone():raise HTTPException(404,'Channel missing.')
            c.execute('UPDATE channels SET slowmode_seconds=? WHERE slug=?',(data.seconds,slug))
            audit(c,u,'channel.slowmode',slug,str(data.seconds))
        return {'ok':True}
    @app.get('/api/moderation/logs')
    def audit_list(request:Request):
        u=require_user(request);must(u,'moderate')
        with db() as c:return [dict(r) for r in c.execute('SELECT a.*,u.username AS actor FROM audit_logs a JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 100')]

    @app.post('/api/messages/{message_id}/pin')
    def pin_message(message_id:int,request:Request):
        u=require_user(request);must(u,'moderate')
        with db() as c:
            m=c.execute('SELECT channel_slug FROM messages WHERE id=?',(message_id,)).fetchone()
            if not m or not m['channel_slug']:raise HTTPException(404,'Public channel message not found.')
            if c.execute('SELECT 1 FROM pins WHERE message_id=?',(message_id,)).fetchone():c.execute('DELETE FROM pins WHERE message_id=?',(message_id,));pinned=False
            else:c.execute('INSERT INTO pins(message_id,pinned_by) VALUES (?,?)',(message_id,u['id']));pinned=True
            audit(c,u,'message.pin' if pinned else 'message.unpin',message_id)
        return {'pinned':pinned}
    @app.get('/api/channels/{slug}/pins')
    def channel_pins(slug:str,request:Request):
        require_user(request)
        with db() as c:return [{'id':r['id'],'body':r['body'],'username':r['username']} for r in c.execute('''SELECT m.id,m.body,u.username FROM pins p JOIN messages m ON m.id=p.message_id
                JOIN users u ON u.id=m.sender_id WHERE m.channel_slug=? ORDER BY p.created_at DESC LIMIT 100''',(slug,))]
    @app.get('/api/messages/{message_id}/thread')
    def message_thread(message_id:int,request:Request):
        u=require_user(request)
        with db() as c:
            m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
            if not m:raise HTTPException(404,'Message not found.')
            if m['recipient_id'] and u['id'] not in (m['sender_id'],m['recipient_id']):raise HTTPException(403,'Private thread.')
            rows=c.execute('''WITH RECURSIVE branch(id) AS (SELECT ? UNION SELECT m.id FROM messages m JOIN branch b ON m.reply_to_id=b.id)
              SELECT m.id,m.body,m.created_at,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.sender_id
              WHERE m.id IN (SELECT id FROM branch) ORDER BY m.id LIMIT 100''',(message_id,)).fetchall()
            return [dict(r) for r in rows]
    @app.get('/api/search/messages')
    def search_messages(request:Request,q:str='',channel:str=''):
        u=require_user(request)
        if len(q.strip())<2:raise HTTPException(400,'Search term must contain at least 2 characters.')
        with db() as c:
            if channel:
                rows=c.execute('''SELECT m.id,m.body,m.channel_slug,m.created_at,u.username FROM messages m JOIN users u ON u.id=m.sender_id
                    WHERE m.channel_slug=? AND m.body LIKE ? ORDER BY m.id DESC LIMIT 50''',(channel,'%'+q[:80]+'%')).fetchall()
            else:
                rows=c.execute('''SELECT m.id,m.body,m.channel_slug,m.created_at,u.username FROM messages m JOIN users u ON u.id=m.sender_id
                    WHERE m.channel_slug IS NOT NULL AND m.body LIKE ? ORDER BY m.id DESC LIMIT 50''',('%'+q[:80]+'%',)).fetchall()
            return [dict(r) for r in rows]
    @app.get('/api/v8/meta')
    def metadata(request:Request):
        u=require_user(request)
        return {'permissions':sorted(permissions(u)),'version':8}
