"""End-to-end API and websocket checks with an isolated temporary SQLite DB."""
import io
import os
import tempfile

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='mrh-tests-')
os.environ['OWNER_USERNAME'] = 'Mr_H'
os.environ['OWNER_PASSWORD'] = 'a-strong-owner-secret'

from fastapi.testclient import TestClient
from app import app


def test_full_flow():
    owner = TestClient(app)
    alice = TestClient(app)
    bob = TestClient(app)
    assert owner.post('/api/login', json={'username':'Mr_H','password':'a-strong-owner-secret'}).status_code == 200
    assert owner.get('/api/me').json()['role'] == 'owner'
    assert alice.get('/api/me').status_code == 401
    assert alice.post('/api/signup', json={'username':'alice_12','password':'alice-secret-42'}).status_code == 201
    assert bob.post('/api/signup', json={'username':'bob_12','password':'bob-secret-42'}).status_code == 201
    assert bob.post('/api/signup', json={'username':'ALICE_12','password':'test123456'}).status_code == 409
    assert alice.post('/api/login', json={'username':'alice_12','password':'wrong-password'}).status_code == 401
    assert alice.get('/api/channels').status_code == 200
    assert alice.post('/api/channels',json={'name':'secret-chat'}).status_code == 403
    assert owner.post('/api/channels',json={'name':'secret-chat','topic':'test room'}).status_code == 201
    assert owner.post('/api/channels',json={'name':'secret-chat'}).status_code == 409
    assert alice.get('/api/channels/secret-chat/messages').json() == []

    alice_info = alice.get('/api/me').json()
    bob_id = bob.get('/api/me').json()['id']
    with alice.websocket_connect('/ws?channel=general') as alice_ws:
        with bob.websocket_connect('/ws?channel=general') as bob_ws:
            assert alice_ws.receive_json()['type'] == 'presence'
            assert bob_ws.receive_json()['type'] == 'presence'
            # presence update sent to alice after bob connects
            assert alice_ws.receive_json()['type'] == 'presence'
            result = alice.post('/api/channels/general/messages', json={'body':'Hello, world!'})
            assert result.status_code == 201
            assert result.json()['body'] == 'Hello, world!'
            assert bob_ws.receive_json()['message']['body'] == 'Hello, world!'
            assert alice_ws.receive_json()['message']['body'] == 'Hello, world!'
            bob_ws.send_json({'type':'typing'})
            assert alice_ws.receive_json()['type'] == 'typing'
    assert bob.get('/api/channels/general/messages').json()[0]['body'] == 'Hello, world!'

    with alice.websocket_connect(f'/ws?dm={bob_id}') as alice_dm:
        with bob.websocket_connect(f'/ws?dm={alice_info["id"]}') as bob_dm:
            alice_dm.receive_json();bob_dm.receive_json();alice_dm.receive_json()
            r=alice.post(f'/api/dms/{bob_id}/messages', json={'body':'private ping'})
            assert r.status_code==201
            assert bob_dm.receive_json()['message']['body']=='private ping'
    assert bob.get(f'/api/dms/{alice_info["id"]}/messages').json()[0]['body']=='private ping'
    with bob.websocket_connect('/ws?channel=help') as bob_elsewhere:
        bob_elsewhere.receive_json()
        r = alice.post(f'/api/dms/{bob_id}/messages', json={'body':'second private ping'})
        assert r.status_code == 201
        notice = bob_elsewhere.receive_json()
        assert notice['type'] == 'dm-alert'
        assert notice['from_user_id'] == alice_info['id']
    assert bob.get('/api/dms').json()[0]['username']=='alice_12'
    assert bob.get('/api/dms/900000/messages').status_code==404
    assert alice.post(f'/api/dms/{alice_info["id"]}/messages',json={'body':'self'}).status_code == 400

    profile=alice.patch('/api/me',json={'display_name':'Alice !!','username':'alice_new','bio':'i like coding'})
    assert profile.status_code==200
    assert profile.json()['display_name']=='Alice !!'
    assert alice.get('/api/members?q=alice').json()[0]['bio']=='i like coding'
    assert alice.patch('/api/me',json={'display_name':'x','username':'Mr_H','bio':''}).status_code==409
    image = io.BytesIO()
    from PIL import Image
    Image.new('RGB',(800,500),(120,90,200)).save(image,format='PNG')
    r=alice.post('/api/me/avatar',files={'file':('avatar.png',image.getvalue(),'image/png')})
    assert r.status_code==200
    assert alice.get(r.json()['avatar']).status_code==200
    assert alice.post('/api/me/avatar',files={'file':('bad.png',b'<script>hi</script>','image/png')}).status_code==400
    assert alice.post('/api/me/password',json={'current_password':'bad','new_password':'more-secure-44'}).status_code==403
    assert alice.post('/api/me/password',json={'current_password':'alice-secret-42','new_password':'more-secure-44'}).status_code==200
    assert alice.post('/api/logout').status_code==200
    assert alice.get('/api/me').status_code==401
    assert alice.post('/api/login',json={'username':'alice_new','password':'alice-secret-42'}).status_code==401
    assert alice.post('/api/login',json={'username':'alice_new','password':'more-secure-44'}).status_code==200
    assert alice.get('/health').json()['ok'] is True
