"""Real Chromium smoke test against mocked account and chat APIs."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright
BASE=Path(__file__).resolve().parents[1]
html=(BASE/'static/index.html').read_text()
html=re.sub(r'<link rel="stylesheet" href="/static/styles.css\?v=[^"]+"\s*/>',lambda m:'<style>'+(BASE/'static/styles.css').read_text()+'</style>',html)
html=re.sub(r'<script src="/static/(?:app|v8).js\?v=[^"]+" defer></script>','',html)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1280,'height':800})
    errors=[]
    page.on('pageerror',lambda x:errors.append(str(x)))
    page.set_content(html)
    page.evaluate('''
        window.__emojiCatalog=[{name:'mrh',url:'/api/emojis/file/emoji_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png'}];
        window.WebSocket=class MockSocket{static OPEN=1;constructor(){this.readyState=1;setTimeout(()=>{this.onopen?.();this.onmessage?.({data:JSON.stringify({type:'presence',online:[1]})})},25)}send(){}close(){this.readyState=3}};
        window.fetch=async(url,options={})=>{
          const msg={id:42,body:'hello :mrh: and @Mr_H',created_at:'2026-10-13T12:00:00Z',channel_slug:'general',reply:null,attachment:null,recipient_id:null,reactions:[{emoji:':mrh:',count:2}],user:{id:1,username:'Mr_H',display_name:'Mr_H',role:'owner'}};
          let responses={
           '/api/me':{id:1,username:'Mr_H',display_name:'Mr_H',role:'owner',avatar:null,bio:'',notification_settings:{desktop:false}},
           '/api/channels':[{slug:'general',name:'general',topic:'greetings'}],'/api/members':[msg.user],'/api/dms':[],
           '/api/channels/general/messages':[msg],'/api/groups':[],'/api/voice/channels':[],
           '/api/v8/meta':{permissions:['*']},'/api/unread':{},'/api/emojis':window.__emojiCatalog,
           '/api/notifications':[], '/api/friends':[],'/api/blocks':[], '/api/moderation/logs':[]};
          return {ok:true,status:200,json:async()=>responses[String(url).split('?')[0]]??{}};
        };
    ''')
    page.add_script_tag(content=(BASE/'static/app.js').read_text()+'\n'+(BASE/'static/v8.js').read_text())
    print("waiting login",flush=True);page.wait_for_selector('#app-screen:not([hidden])',timeout=5000);print("login good",flush=True)
    print("waiting emoji",flush=True);print('body text:',page.locator('#messages').inner_text()[:800],flush=True);print('img count:',page.locator('.message-text img').count(),flush=True);print('errors:',errors,flush=True);print('state:',page.evaluate('({mode:state.mode,permissions:state.v8Permissions,emojis:v8CustomEmojis})'),flush=True);page.wait_for_selector('.message-text .v8-inline-emoji',timeout=5000);print('emoji good',flush=True)
    assert page.locator('.message-text .v8-inline-emoji').count()==1
    assert page.locator('.v8-reactions .v8-reaction-emoji').count()==1
    assert ':mrh:' in page.locator('.v8-inline-emoji').get_attribute('alt')
    page.locator('#emoji-button').click()
    print("waiting picker",flush=True);page.wait_for_selector('.v8-custom button',timeout=5000);print("picker good",flush=True)
    page.locator('.v8-custom button').first.click()
    assert page.locator('#message-input').input_value().count(':mrh:')==1
    assert page.locator('#emoji-popover').is_hidden()
    page.evaluate('''window.__emojiCatalog=[{name:'mrh',url:'/api/emojis/file/emoji_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png'},{name:'newone',url:'/api/emojis/file/emoji_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png'}]''')
    page.locator('#emoji-button').click()
    print('waiting new emoji',flush=True);page.wait_for_selector('[data-v8-emoji=":newone:"]',timeout=5000);print('new emoji good',flush=True)
    page.locator('[data-v8-emoji=":newone:"]').click()
    assert page.locator('#message-input').input_value().count(':newone:')==1
    page.locator('#dock-settings').click()
    page.wait_for_selector('#v8-emoji-settings')
    page.locator('#v8-emoji-settings').click()
    page.wait_for_selector('.v8-emoji-catalog-item')
    assert page.locator('.v8-emoji-catalog-item').count()==2
    assert not errors,errors
    print('PASS: custom emoji rendered in messages/reactions, single-insert picker, refresh, settings catalog')
    browser.close()
