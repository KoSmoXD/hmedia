"""v8 account permission, privacy, group, reactions, moderation and media regressions."""
import io
import os
import tempfile
os.environ.setdefault('DATA_DIR',tempfile.mkdtemp(prefix='mrh-v8-tests-'))
os.environ.setdefault('OWNER_USERNAME','Mr_H')
os.environ.setdefault('OWNER_PASSWORD','a-strong-owner-secret')
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app import app,db,rate_events


def account(username,owner=False):
    rate_events.clear()
    c=TestClient(app)
    r=c.post('/api/login' if owner else '/api/signup',json={'username':'Mr_H' if owner else username,'password':'a-strong-owner-secret' if owner else 'member-password-123'})
    assert r.status_code==200 if owner else r.status_code==201,r.text
    return c,c.get('/api/me').json()['id']

def test_roles_profile_preferences_and_animated_avatar():
    owner,_=account(None,True);a,aid=account('v8_role_alice')
    role=owner.post('/api/roles',json={'slug':'helper','name':'Helper','color':'#31a3c2','permissions':['manage_channels','moderate']})
    assert role.status_code==201,role.text
    assert a.post('/api/roles/assign/'+str(aid),json={'role':'owner'}).status_code==403
    assert owner.post('/api/roles/assign/'+str(aid),json={'role':'helper'}).status_code==200
    assert a.get('/api/me').json()['role_color']=='#31a3c2'
    assert a.post('/api/channels',json={'name':'helper-room'}).status_code==201
    assert a.patch('/api/me/profile-more',json={'status':'🎮 playing Roblox','profile_color':'#224466'}).status_code==200
    assert a.patch('/api/me/profile-more',json={'status':'bad','profile_color':'red'}).status_code==400
    image=io.BytesIO();Image.new('RGB',(100,40),'blue').save(image,format='PNG')
    banner=a.post('/api/me/banner',files={'file':('banner.png',image.getvalue(),'image/png')})
    assert banner.status_code==200,banner.text
    assert a.get(banner.json()['banner']).status_code==200
    settings={'desktop':False,'mentions':False,'dms':True,'friends':True}
    assert a.patch('/api/me/notifications',json=settings).json()==settings
    assert a.get('/api/me').json()['notification_settings']==settings
    gifs=[Image.new('RGB',(80,80),color) for color in ('red','blue')];buf=io.BytesIO()
    gifs[0].save(buf,format='GIF',save_all=True,append_images=gifs[1:],duration=[120,120],loop=0)
    avatar=a.post('/api/me/avatar',files={'file':('loop.gif',buf.getvalue(),'image/gif')})
    assert avatar.status_code==200,avatar.text
    assert avatar.json()['avatar'].endswith('.gif')
    assert a.get(avatar.json()['avatar']).headers['content-type'].startswith('image/gif')


def test_friends_block_and_private_group_call():
    a,aid=account('v8_friends_a');b,bid=account('v8_friends_b');outsider,_=account('v8_outsider')
    assert a.post('/api/friends/request',json={'user_id':bid}).status_code==200
    assert b.get('/api/friends').json()[0]['state']=='pending'
    assert a.post(f'/api/friends/{bid}/accept').status_code==403
    assert b.post(f'/api/friends/{aid}/accept').status_code==200
    assert a.get('/api/friends').json()[0]['state']=='accepted'
    group=a.post('/api/groups',json={'name':'Test party','members':[bid]})
    assert group.status_code==201,group.text
    gid=group.json()['id']
    assert outsider.get(f'/api/groups/{gid}/messages').status_code==403
    assert outsider.post(f'/api/groups/{gid}/messages',json={'body':'stolen'}).status_code==403
    with pytest.raises(WebSocketDisconnect):
        with outsider.websocket_connect(f'/ws/voice?room=group-{gid}') as ws:ws.receive_json()
    with a.websocket_connect(f'/ws/voice?room=group-{gid}') as ws:assert ws.receive_json()['type']=='voice-roster'
    assert a.post(f'/api/groups/{gid}/messages',json={'body':'hello group'}).status_code==201
    assert b.get(f'/api/groups/{gid}/messages').json()[0]['body']=='hello group'
    assert b.get('/api/unread').json()['group:'+str(gid)] is True
    assert b.post(f'/api/read/group/{gid}').status_code==200
    assert b.get('/api/unread').json()['group:'+str(gid)] is False
    assert a.post(f'/api/blocks/{bid}').status_code==200
    assert a.get('/api/friends').json()==[]
    assert b.post(f'/api/dms/{aid}/messages',json={'body':'forbidden'}).status_code==403
    assert a.delete(f'/api/blocks/{bid}').status_code==200


def test_reactions_thread_search_moderation_and_slowmode():
    owner,_=account(None,True);moderator,mid=account('v8_mod');member,uid=account('v8_general_user');another,_=account('v8_reactor')
    assert owner.post(f'/api/roles/assign/{mid}',json={'role':'moderator'}).status_code==200
    msg=member.post('/api/channels/general/messages',json={'body':'v8 thread root'})
    assert msg.status_code==201,msg.text
    root=msg.json()['id']
    react=another.post(f'/api/messages/{root}/reactions',json={'emoji':'🔥'})
    assert react.status_code==200,react.text
    assert react.json()['reactions']==[{'emoji':'🔥','count':1}]
    assert member.get('/api/channels/general/messages').json()[-1]['reactions'][0]['emoji']=='🔥'
    assert another.post(f'/api/messages/{root}/reactions',json={'emoji':'🔥'}).json()['reactions']==[]
    reply=member.post('/api/channels/general/messages',json={'body':'v8 child reply','reply_to_id':root})
    assert reply.status_code==201,reply.text
    thread=moderator.get(f'/api/messages/{root}/thread').json();assert len(thread)==2
    assert moderator.post(f'/api/messages/{root}/pin').json()['pinned']
    assert moderator.get('/api/channels/general/pins').json()[0]['id']==root
    assert any(r['id']==root for r in moderator.get('/api/search/messages?q=thread').json())
    assert moderator.patch('/api/channels/general/slowmode',json={'seconds':60}).status_code==200
    assert owner.post('/api/channels/general/messages',json={'body':'owner bypass'}).status_code==201
    assert moderator.post(f'/api/moderation/users/{uid}/timeout',json={'seconds':600,'reason':'test'}).status_code==200
    assert member.post(f'/api/dms/{mid}/messages',json={'body':'should fail'}).status_code==403
    assert moderator.post(f'/api/moderation/users/{uid}/timeout',json={'seconds':0,'reason':'cleared'}).status_code==200
    assert moderator.delete(f'/api/moderation/messages/{root}').status_code==200
    assert moderator.get('/api/moderation/logs').status_code==200
    assert member.get('/api/moderation/logs').status_code==403
    assert member.get(f'/api/messages/{root}/thread').status_code==404


def test_custom_emoji_and_private_message_cannot_be_moderated():
    owner,_=account(None,True);alice,aid=account('v8_emoji_alice');bob,bid=account('v8_emoji_bob')
    png=io.BytesIO();Image.new('RGB',(32,32),'green').save(png,format='PNG')
    assert alice.post('/api/emojis?name=mrh8',files={'file':('emoji.png',png.getvalue(),'image/png')}).status_code==403
    custom=owner.post('/api/emojis?name=mrh8',files={'file':('emoji.png',png.getvalue(),'image/png')})
    assert custom.status_code==201,custom.text
    assert alice.get(custom.json()['url']).status_code==200
    dm=alice.post(f'/api/dms/{bid}/messages',json={'body':'Private secret from Alice'})
    assert dm.status_code==201
    id=dm.json()['id']
    assert owner.get(f'/api/messages/{id}/reactions').status_code==403
    assert owner.delete(f'/api/moderation/messages/{id}').status_code==403
    assert bob.post(f'/api/messages/{id}/reactions',json={'emoji':':mrh8:'}).status_code==200
    assert alice.get(f'/api/messages/{id}/reactions').json()[0]['count']==1


def test_group_file_privacy_and_member_removal_disconnects():
    a,aid=account('v8_files_a');b,bid=account('v8_files_b');outsider,_=account('v8_files_out')
    group=a.post('/api/groups',json={'name':'File party','members':[bid]})
    assert group.status_code==201
    gid=group.json()['id']
    f=a.post(f'/api/groups/{gid}/attachments',files={'file':('notes.txt',b'private group notes','text/plain')},data={'body':'Group file'})
    assert f.status_code==201,f.text
    url=f.json()['attachment']['url']
    assert b.get(url).content==b'private group notes'
    assert outsider.get(url).status_code==403
    with b.websocket_connect(f'/ws?group={gid}') as ws:
        assert ws.receive_json()['type']=='presence'
        assert a.delete(f'/api/groups/{gid}/members/{bid}').status_code==200
        with pytest.raises(WebSocketDisconnect):ws.receive_json()
    assert b.get(f'/api/groups/{gid}/messages').status_code==403
    assert b.get(url).status_code==403
