"""Actual local server + two Chromium clients: UI, voice signals, sound preferences."""
from playwright.sync_api import sync_playwright
BASE='http://127.0.0.1:8765'
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--autoplay-policy=no-user-gesture-required'])
    a=browser.new_page(viewport={'width':1320,'height':820},permissions=['microphone'])
    b=browser.new_page(viewport={'width':1120,'height':800},permissions=['microphone'])
    errors=[]
    for page,name in [(a,'owner'),(b,'member')]:
        page.on('pageerror',lambda e, name=name:errors.append(f'{name}: {e}'))
    b.add_init_script('''Object.defineProperty(navigator.mediaDevices,'getDisplayMedia',{configurable:true,value:async()=>{const c=document.createElement('canvas');c.width=800;c.height=450;const x=c.getContext('2d');x.fillStyle='#3c2d74';x.fillRect(0,0,c.width,c.height);x.fillStyle='white';x.font='32px Arial';x.fillText('Mr H Live!',270,230);return c.captureStream(10)}});''')
    a.goto(BASE)
    a.locator('#auth-username').fill('Mr_H');a.locator('#auth-password').fill('TestVoiceCall777');a.locator('#auth-submit').click()
    a.wait_for_selector('#app-screen:not([hidden])')
    b.goto(BASE)
    b.locator('#tab-signup').click();b.locator('#auth-username').fill('member_voice_777')
    b.locator('#auth-password').fill('VoiceMember98765');b.locator('#auth-submit').click()
    b.wait_for_selector('#app-screen:not([hidden])')
    a.wait_for_selector('[data-join-voice="lounge"]');b.wait_for_selector('[data-join-voice="lounge"]')
    a.locator('[data-join-voice="lounge"]').click()
    a.wait_for_function('voice.connected && voice.people.size===1',timeout=20000)
    assert a.locator('#voice-stage').is_visible()
    assert a.locator('.voice-member-tile').count()==1
    assert not a.locator('#chat-scroller').is_visible()
    assert a.locator('#voice-call-mute').is_visible()
    assert a.evaluate('voiceCueContext?.state')=='running'
    b.locator('[data-join-voice="lounge"]').click()
    a.wait_for_function('voice.people.size===2',timeout=15000)
    b.wait_for_function('voice.people.size===2',timeout=15000)
    a.wait_for_function("[...voice.peers.values()].every(p=>p.pc.connectionState==='connected')",timeout=20000)
    b.wait_for_function("[...voice.peers.values()].every(p=>p.pc.connectionState==='connected')",timeout=20000)
    assert a.locator('.voice-member-tile').count()==2
    a.screenshot(path='/mnt/data/mr-h-media-v7-voice-call-preview.png',full_page=False)
    a.locator('#voice-call-mute').click()
    assert a.evaluate('voice.muted')
    assert a.locator('#voice-call-mute').inner_text()=='Unmute'
    a.locator('#voice-sounds').click()
    assert a.evaluate('voiceSoundsEnabled') is False
    assert a.evaluate("localStorage.getItem('mrh-voice-sounds')")=='off'
    a.locator('#voice-sounds').click()
    assert a.evaluate('voiceSoundsEnabled') is True
    b.locator('#voice-call-share').click()
    b.wait_for_function('voice.sharing && voice.screen?.readyState==="live"')
    a.wait_for_function("[...voice.people.values()].some(u=>u.sharing&&u.username==='member_voice_777')")
    a.wait_for_function("[...voice.peers.values()].some(p=>p.videoTrack&&!p.videoTrack.muted)")
    assert a.locator('.voice-stream video').count()==1
    a.screenshot(path='/mnt/data/mr-h-media-v7-stream-preview.png',full_page=False)
    a.locator('#voice-back-chat').click()
    assert a.locator('#chat-scroller').is_visible()
    assert a.evaluate('voice.room')=='lounge'
    a.locator('#voice-show').click()
    assert a.locator('#voice-stage').is_visible()
    a.locator('#voice-call-leave').click()
    assert not a.locator('#voice-stage').is_visible()
    assert not a.evaluate('voice.ws')
    b.locator('#voice-call-leave').click()
    assert not errors,errors
    m=browser.new_page(viewport={'width':390,'height':844},permissions=['microphone'])
    m.goto(BASE)
    m.locator('#auth-username').fill('Mr_H');m.locator('#auth-password').fill('TestVoiceCall777');m.locator('#auth-submit').click()
    m.wait_for_selector('#app-screen:not([hidden])')
    m.locator('#mobile-menu').click()
    m.locator('[data-join-voice="lounge"]').click()
    m.wait_for_function('voice.connected')
    assert m.locator('#voice-call-leave').is_visible()
    assert m.evaluate('document.documentElement.scrollWidth <= innerWidth')
    m.screenshot(path='/mnt/data/mr-h-media-v7-mobile-preview.png',full_page=False)
    print('PASS: 2 clients WebRTC, call screen, mute, sound preferences, screen share, return to chat, leave, mobile layout')
    browser.close()
