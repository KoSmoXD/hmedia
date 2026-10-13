"""Quoted channel/DM replies and attachment replies, with strict room isolation."""
from fastapi.testclient import TestClient
from app import app, db, rate_events


def test_reply_to_channel_messages_and_files():
    rate_events.clear()
    owner = TestClient(app)
    alice = TestClient(app)
    assert owner.post('/api/login', json={'username':'Mr_H','password':'a-strong-owner-secret'}).status_code==200
    assert alice.post('/api/signup', json={'username':'reply_alice','password':'reply-password-123'}).status_code==201
    initial = owner.post('/api/channels/general/messages',json={'body':'Original text and a link'})
    assert initial.status_code==201
    first=initial.json()
    assert first['reply'] is None
    reply = alice.post('/api/channels/general/messages',json={'body':'That makes sense','reply_to_id':first['id']})
    assert reply.status_code==201, reply.text
    assert reply.json()['reply']['id']==first['id']
    assert reply.json()['reply']['username']=='Mr_H'
    assert reply.json()['reply']['preview']=='Original text and a link'
    messages=owner.get('/api/channels/general/messages').json()
    assert messages[-1]['reply']['id']==first['id']
    assert alice.post('/api/channels/help/messages',json={'body':'Cross-channel reply','reply_to_id':first['id']}).status_code==403
    assert alice.post('/api/channels/general/messages',json={'body':'missing','reply_to_id':999999999}).status_code==404
    assert alice.post('/api/channels/general/messages',json={'body':'invalid','reply_to_id':-5}).status_code==422

    attached = owner.post('/api/channels/general/attachments', files={'file':('notes.txt',b'hello','text/plain')})
    assert attached.status_code==201
    # Member file replies preserve their own file and quoted text on re-fetch.
    file_reply = alice.post('/api/channels/general/attachments', data={'body':'Attached answer','reply_to_id':str(first['id'])},files={'file':('answer.txt',b'answer','text/plain')})
    assert file_reply.status_code==201,file_reply.text
    assert file_reply.json()['reply']['id']==first['id']
    assert file_reply.json()['attachment']['name']=='answer.txt'
    file_to_file=owner.post('/api/channels/general/messages',json={'body':'Thanks for your document','reply_to_id':attached.json()['id']})
    assert file_to_file.status_code==201,file_to_file.text
    assert file_to_file.json()['reply']['file']=='notes.txt'


def test_reply_dm_room_isolation_and_ws():
    rate_events.clear()
    owner=TestClient(app)
    alice=TestClient(app)
    third=TestClient(app)
    assert owner.post('/api/login',json={'username':'Mr_H','password':'a-strong-owner-secret'}).status_code==200
    assert alice.post('/api/login',json={'username':'reply_alice','password':'reply-password-123'}).status_code==200
    assert third.post('/api/signup',json={'username':'reply_third','password':'reply-password-123'}).status_code==201
    aid=alice.get('/api/me').json()['id']
    oid=owner.get('/api/me').json()['id']
    tid=third.get('/api/me').json()['id']
    first=owner.post(f'/api/dms/{aid}/messages',json={'body':'Private original'}).json()
    assert alice.post(f'/api/dms/{tid}/messages',json={'body':'Wrong recipient','reply_to_id':first['id']}).status_code==403
    assert third.post(f'/api/dms/{oid}/messages',json={'body':'Foreign DM','reply_to_id':first['id']}).status_code==403
    assert alice.post('/api/channels/general/messages',json={'body':'Leaked reference','reply_to_id':first['id']}).status_code==403
    with owner.websocket_connect(f'/ws?dm={aid}') as ws:
        ws.receive_json()
        result=alice.post(f'/api/dms/{oid}/messages',json={'body':'Actual response','reply_to_id':first['id']})
        assert result.status_code==201,result.text
        streamed=ws.receive_json()
        assert streamed['type']=='message'
        assert streamed['message']['reply']['preview']=='Private original'
    assert owner.get(f'/api/dms/{aid}/messages').json()[-1]['reply']['id']==first['id']
    # Replying to unrelated channel content via DM must be blocked.
    other_channel=owner.post('/api/channels/help/messages',json={'body':'Hello help'}).json()
    assert alice.post(f'/api/dms/{oid}/messages',json={'body':'No cross-room','reply_to_id':other_channel['id']}).status_code==403


def test_schema_keeps_legacy_message_data():
    from app import init_db
    with db() as c:
        before=c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    init_db()
    with db() as c:
        columns={r['name'] for r in c.execute('PRAGMA table_info(messages)')}
        after=c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    assert 'reply_to_id' in columns and before==after
