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


def test_posts_files_channels_and_moderation():
    """Owner permissions, uploaded files, private-file boundaries, ban revocation and post deletes."""
    owner = TestClient(app)
    alice = TestClient(app)
    bob = TestClient(app)
    third = TestClient(app)
    assert owner.post('/api/login',json={'username':'Mr_H','password':'a-strong-owner-secret'}).status_code==200
    assert alice.post('/api/signup',json={'username':'alice_extra','password':'my-test-pass-123'}).status_code==201
    assert bob.post('/api/signup',json={'username':'bob_extra','password':'my-test-pass-123'}).status_code==201
    assert third.post('/api/signup',json={'username':'third_extra','password':'my-test-pass-123'}).status_code==201
    alice_id=alice.get('/api/me').json()['id']
    bob_id=bob.get('/api/me').json()['id']
    assert alice.get('/api/admin/users').status_code==403
    assert alice.post(f'/api/admin/users/{bob_id}/ban',json={'reason':'test'}).status_code==403
    assert bob.delete('/api/channels/general').status_code==403
    assert owner.delete('/api/channels/general').status_code==403
    assert owner.post('/api/channels',json={'name':'media-files','topic':'files and discussion'}).status_code==201

    channel_file=alice.post('/api/channels/media-files/attachments', data={'body':'A PDF file'},files={'file':('test.pdf',b'%PDF-1.4\nhello','application/pdf')})
    assert channel_file.status_code==201,channel_file.text
    message=channel_file.json()
    assert message['body']=='A PDF file'
    assert message['attachment']['name']=='test.pdf'
    assert message['attachment']['mime']=='application/octet-stream'
    attachment=message['attachment']['url']
    assert bob.get(attachment).content.startswith(b'%PDF')
    assert third.get(attachment).status_code==200
    assert TestClient(app).get(attachment).status_code==401
    assert alice.post('/api/channels/media-files/attachments',files={'file':('big.zip',b'x'*(8*1024*1024+1))}).status_code==413
    assert alice.post('/api/channels/media-files/attachments',files={'file':('script.html',b'<script>alert(1)</script>')}).status_code==400
    assert alice.post('/api/channels/media-files/attachments',files={'file':('spoof.png',b'not an image')}).status_code==400
    assert alice.get('/api/channels/media-files/messages').json()[-1]['attachment']['id']==message['attachment']['id']

    private=alice.post(f'/api/dms/{bob_id}/attachments',files={'file':('test.txt',b'PRIVATE FILE','text/plain')})
    assert private.status_code==201,private.text
    private_link=private.json()['attachment']['url']
    assert alice.get(private_link).status_code==200
    assert bob.get(private_link).status_code==200
    assert third.get(private_link).status_code==403
    assert private.json()['attachment']['name']=='test.txt'

    public_post=alice.post('/api/posts',json={'title':'My first post','body':'Hello everyone'})
    assert public_post.status_code==201,public_post.text
    post_id=public_post.json()['id']
    assert any(p['id']==post_id for p in bob.get('/api/posts').json())
    assert bob.delete(f'/api/posts/{post_id}').status_code==403
    assert alice.delete(f'/api/posts/{post_id}').status_code==200
    file_post=alice.post('/api/posts/with-file',data={'title':'Attached','body':'new attachment'}, files={'file':('notes.txt',b'hello posts')})
    assert file_post.status_code==201,file_post.text
    post_file_link=file_post.json()['attachment']['url']
    assert bob.get(post_file_link).status_code==200
    assert owner.delete('/api/posts/'+str(file_post.json()['id'])).status_code==200
    assert bob.get(post_file_link).status_code==404

    assert owner.post(f'/api/admin/users/{owner.get("/api/me").json()["id"]}/ban',json={'reason':'oops'}).status_code==403
    assert owner.post(f'/api/admin/users/{alice_id}/ban',json={'reason':'spam'}).status_code==200
    assert alice.get('/api/me').status_code==401
    assert alice.get('/api/posts').status_code==401
    assert alice.get(private_link).status_code==401
    assert alice.post('/api/login',json={'username':'alice_extra','password':'my-test-pass-123'}).status_code==403
    assert any(u['username']=='alice_extra' and u['banned'] for u in owner.get('/api/admin/users').json())
    assert owner.post(f'/api/admin/users/{alice_id}/unban').status_code==200
    assert alice.post('/api/login',json={'username':'alice_extra','password':'my-test-pass-123'}).status_code==200
    assert owner.delete('/api/channels/media-files').status_code==200
    assert bob.get('/api/channels/media-files/messages').status_code==404
    assert bob.get(attachment).status_code==404
