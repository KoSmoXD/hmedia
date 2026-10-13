from fastapi.testclient import TestClient
from app import app, db, rate_events


def test_mentions_comments_theme_and_antispam():
    rate_events.clear()
    owner, a, b = TestClient(app),TestClient(app),TestClient(app)
    assert owner.post('/api/login', json={'username':'Mr_H','password':'a-strong-owner-secret'}).status_code==200
    assert a.post('/api/signup',json={'username':'arthur_v4','password':'long_secret_123'}).status_code==201
    assert b.post('/api/signup',json={'username':'bella_v4','password':'long_secret_456'}).status_code==201
    aid=a.get('/api/me').json()['id']
    bid=b.get('/api/me').json()['id']
    theme={'accent':'#34ffaa','sidebar':'#182934','chat':'#101518','text':'#eeeeee'}
    updated=a.patch('/api/me/theme',json=theme)
    assert updated.status_code==200,updated.text
    assert updated.json()['theme']==theme
    assert a.get('/api/me').json()['theme']==theme
    assert a.patch('/api/me/theme',json={**theme,'chat':'red'}).status_code==400
    with a.websocket_connect('/ws?channel=general') as ws_a:
        ws_a.receive_json()
        with b.websocket_connect('/ws?channel=general') as ws_b:
            ws_b.receive_json();ws_a.receive_json()
            ping=a.post('/api/channels/general/messages',json={'body':'hello @bella_v4 can you check?'})
            assert ping.status_code==201,ping.text
            assert ws_b.receive_json()['type']=='ping-alert'
            assert ws_b.receive_json()['type']=='message'
            assert ws_a.receive_json()['type']=='message'
    unread=b.get('/api/notifications').json()
    assert len(unread)==1 and unread[0]['kind']=='channel' and not unread[0]['read']
    assert b.post('/api/notifications/read').status_code==200
    assert b.get('/api/notifications').json()[0]['read']
    post=a.post('/api/posts',json={'title':'Gallery update','body':'check this @bella_v4'})
    assert post.status_code==201,post.text
    pid=post.json()['id']
    assert post.json()['comment_count']==0
    assert b.get('/api/notifications').json()[0]['kind']=='post'
    comment=b.post(f'/api/posts/{pid}/comments',json={'body':'Looks great @arthur_v4'})
    assert comment.status_code==201,comment.text
    cid=comment.json()['id']
    assert a.get('/api/notifications').json()[0]['context']=='comments'
    assert a.get('/api/posts').json()[0]['comment_count']==1
    assert a.get(f'/api/posts/{pid}/comments').json()[0]['body']=='Looks great @arthur_v4'
    assert a.delete(f'/api/posts/{pid}/comments/{cid}').status_code==403
    assert b.delete(f'/api/posts/{pid}/comments/{cid}').status_code==200
    assert b.get(f'/api/posts/{pid}/comments').json()==[]
    assert a.post('/api/channels/general/messages',json={'body':'hello @bella_v4 can you check?'}).status_code==429
    assert a.post('/api/channels/general/messages',json={'body':'just another text'}).status_code==429
    with db() as conn:
        row=conn.execute('SELECT spam_timeout_until,spam_strikes FROM users WHERE id=?',(aid,)).fetchone()
        assert row['spam_timeout_until']>0 and row['spam_strikes']==1
        conn.execute('UPDATE users SET spam_timeout_until=0 WHERE id=?',(aid,))
    for i in range(5):
        assert a.post('/api/dms/'+str(bid)+'/messages',json={'body':f'Unique msg #{i}'}).status_code==201
    # sixth action in the same 10s interval blocked, timeout escalated
    assert a.post('/api/dms/'+str(bid)+'/messages',json={'body':'Another unique msg'}).status_code==429
    assert a.get('/api/me').status_code==200
