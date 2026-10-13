"""End-to-end API regression for custom emoji media, reactions and saved emoji catalog."""
import io, os, tempfile
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='mrh-emoji-fix-')
os.environ['OWNER_USERNAME'] = 'Mr_H'
os.environ['OWNER_PASSWORD'] = 'strong-emojitest-secret'
from PIL import Image
from fastapi.testclient import TestClient
from app import app, rate_events

def signup(name, password='user-password-123'):
    rate_events.clear()
    client=TestClient(app)
    r=client.post('/api/login' if name=='Mr_H' else '/api/signup',json={'username':name,'password':'strong-emojitest-secret' if name=='Mr_H' else password})
    assert r.status_code in (200,201),r.text
    return client

def test_emoji_static_and_animated():
    owner=signup('Mr_H'); member=signup('emoji_user')
    png=io.BytesIO();Image.new('RGBA',(256,64),(10,180,160,255)).save(png,format='PNG')
    forbidden=member.post('/api/emojis?name=nice',files={'file':('nice.png',png.getvalue(),'image/png')})
    assert forbidden.status_code==403
    r=owner.post('/api/emojis?name=nice',files={'file':('nice.png',png.getvalue(),'image/png')})
    assert r.status_code==201,r.text
    media=member.get(r.json()['url'])
    assert media.status_code==200 and media.headers['content-type'].startswith('image/png')
    with Image.open(io.BytesIO(media.content)) as im: assert max(im.size)<=128
    frames=[Image.new('RGB',(140,90),c) for c in ('red','blue','green')]
    animated=io.BytesIO();frames[0].save(animated,format='GIF',save_all=True,append_images=frames[1:],duration=[150,150,150],loop=0)
    r=owner.post('/api/emojis?name=dance',files={'file':('dance.gif',animated.getvalue(),'image/gif')})
    assert r.status_code==201,r.text
    assert r.json()['url'].endswith('.gif')
    media=member.get(r.json()['url'])
    assert media.status_code==200 and media.headers['content-type'].startswith('image/gif')
    with Image.open(io.BytesIO(media.content)) as im:
        assert im.n_frames>=2
        assert max(im.size)<=128
    names={e['name'] for e in member.get('/api/emojis').json()}
    assert {'nice','dance'}.issubset(names)
    assert member.get('/api/emojis/file/../app.py').status_code==404
    message=member.post('/api/channels/general/messages',json={'body':'hello :dance: :nice:'})
    assert message.status_code==201,message.text
    mid=message.json()['id']
    reaction=owner.post(f'/api/messages/{mid}/reactions',json={'emoji':':dance:'})
    assert reaction.status_code==200,reaction.text
    reactions=member.get(f'/api/messages/{mid}/reactions').json()
    assert reactions[0]['emoji']==':dance:'
    assert owner.post('/api/emojis?name=nice',files={'file':('nice.png',png.getvalue(),'image/png')}).status_code==409
    assert owner.post('/api/emojis?name=x',files={'file':('nice.png',png.getvalue(),'image/png')}).status_code==400
