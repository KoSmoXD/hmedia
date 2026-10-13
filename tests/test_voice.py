"""Voice rooms use authorized WebSockets; no voice/screen payload passes through API."""
import pytest
from starlette.websockets import WebSocketDisconnect
from fastapi.testclient import TestClient
from app import app, db, voice_connections


def test_voice_room_owner_permissions_roster_and_signaling():
    owner, alice, bob = TestClient(app), TestClient(app), TestClient(app)
    assert owner.post('/api/login', json={'username':'Mr_H','password':'a-strong-owner-secret'}).status_code == 200
    assert alice.post('/api/signup',json={'username':'voice_alice','password':'safe-password-123'}).status_code == 201
    assert bob.post('/api/signup',json={'username':'voice_bob','password':'safe-password-123'}).status_code == 201
    uid_a=alice.get('/api/me').json()['id'];uid_b=bob.get('/api/me').json()['id']
    assert alice.get('/api/voice/channels').status_code==200
    assert any(ch['slug']=='lounge' for ch in alice.get('/api/voice/channels').json())
    assert alice.post('/api/voice/channels',json={'name':'Private Party'}).status_code==403
    created=owner.post('/api/voice/channels',json={'name':'Voice Test'})
    assert created.status_code==201,created.text
    assert created.json()['slug']=='voice-test'
    assert owner.post('/api/voice/channels',json={'name':'Voice Test'}).status_code==409
    assert alice.delete('/api/voice/channels/voice-test').status_code==403
    assert owner.delete('/api/voice/channels/lounge').status_code==403
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect('/ws/voice?room=voice-test') as ws:
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with alice.websocket_connect('/ws/voice?room=nonexistent') as ws:
            ws.receive_json()
    with alice.websocket_connect('/ws/voice?room=voice-test') as a_ws:
        init_a=a_ws.receive_json()
        assert init_a['type']=='voice-roster' and len(init_a['members'])==1
        assert init_a['self_id']==uid_a
        with bob.websocket_connect('/ws/voice?room=voice-test') as b_ws:
            init_b=b_ws.receive_json()
            assert init_b['type']=='voice-roster'
            assert {u['id'] for u in init_b['members']}=={uid_a,uid_b}
            joined=a_ws.receive_json()
            assert joined['type']=='voice-joined' and joined['user']['id']==uid_b
            rooms=alice.get('/api/voice/channels').json()
            assert next(r for r in rooms if r['slug']=='voice-test')['members']
            b_ws.send_json({'type':'voice-status','muted':False,'sharing':True,'user_id':uid_a})
            status=a_ws.receive_json()
            assert status=={'type':'voice-status','user_id':uid_b,'muted':False,'sharing':True}
            sample={'type':'offer','sdp':'v=0\r\n'+'a=mock\r\n'}
            a_ws.send_json({'type':'offer','to':uid_b,'data':sample})
            forwarded=b_ws.receive_json()  # receives its own broadcast voice-status first
            if forwarded['type']=='voice-status': forwarded=b_ws.receive_json()
            assert forwarded['type']=='voice-signal' and forwarded['from']==uid_a
            assert forwarded['signal']=='offer' and forwarded['data']==sample
        left=a_ws.receive_json()
        assert left=={'type':'voice-left','user_id':uid_b}
    assert owner.delete('/api/voice/channels/voice-test').status_code==200
    assert 'voice-test' not in [r['slug'] for r in owner.get('/api/voice/channels').json()]
    assert voice_connections=={}


def test_voice_owner_ban_and_config():
    owner, bob=TestClient(app), TestClient(app)
    assert owner.post('/api/login',json={'username':'Mr_H','password':'a-strong-owner-secret'}).status_code==200
    assert bob.post('/api/login',json={'username':'voice_bob','password':'safe-password-123'}).status_code==200
    cfg=bob.get('/api/voice/ice-config')
    assert cfg.status_code==200 and isinstance(cfg.json()['iceServers'],list)
    uid=bob.get('/api/me').json()['id']
    with bob.websocket_connect('/ws/voice?room=lounge') as ws:
        assert ws.receive_json()['type']=='voice-roster'
        assert owner.post(f'/api/admin/users/{uid}/ban',json={'reason':'moderation test'}).status_code==200
        with pytest.raises(WebSocketDisconnect): ws.receive_json()
    assert bob.get('/api/voice/channels').status_code==401
    assert owner.post(f'/api/admin/users/{uid}/unban').status_code==200
