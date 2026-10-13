"""Browser interaction smoke test with mocked API and websocket, no server required."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright
BASE=Path(__file__).resolve().parents[1]
html=(BASE/'static/index.html').read_text()
html=re.sub(r'<link rel="stylesheet" href="/static/styles.css\?v=\d+" />','<style>'+(BASE/'static/styles.css').read_text()+'</style>',html)
html=re.sub(r'<script src="/static/(?:app|v8).js\?v=\d+" defer></script>','',html)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1280,'height':820})
    errors=[]
    page.on('pageerror',lambda x:errors.append(str(x)))
    page.set_content(html)
    page.evaluate('''window.WebSocket=class MockSocket {static OPEN=1;constructor(url){this.url=url;this.readyState=0;setTimeout(()=>{this.readyState=1;this.onopen?.(); if(url.includes('/ws?'))this.onmessage?.({data:JSON.stringify({type:'presence',online:[1]})});},10)}send(){}close(){this.readyState=3}};
      window.fetch=async(url,opts={})=>{let values={
        '/api/me':{id:1,username:'Mr_H',display_name:'Mr_H',bio:'Welcome',avatar:null,role:'owner',role_color:'#ffcf67',theme:{},notification_settings:{desktop:false,mentions:true,dms:true,friends:true}},
        '/api/channels':[{slug:'general',name:'general',topic:'main channel'}],
        '/api/members':[{id:1,username:'Mr_H',display_name:'Mr_H',role:'owner'},{id:2,username:'Guest',display_name:'Guest',role:'member'}],
        '/api/dms':[],'/api/notifications':[], '/api/voice/channels':[{slug:'lounge',name:'Lounge',members:[]}],
        '/api/channels/general/messages':[], '/api/v8/meta':{version:8,permissions:['*']},'/api/groups':[{id:1,name:'Gaming squad',members:[{id:1,username:'Mr_H',display_name:'Mr_H',role:'owner'},{id:2,username:'Guest',display_name:'Guest',role:'member'}]}],
        '/api/emojis':[{name:'mrh',url:'/api/emojis/file/emoji_abc.png'}],'/api/unread':{'channel:general':true},
        '/api/friends':[{user:{id:2,username:'Guest',display_name:'Guest',role:'member'},state:'accepted',requested_by:1}],
        '/api/blocks':[],'/api/groups/1/messages':[{id:19,body:'Testing group',created_at:'2026-10-13T10:00:00Z',group_id:1,user:{id:2,username:'Guest',display_name:'Guest',role:'member'},attachment:null,reply:null}],
        '/api/roles':[{slug:'owner',name:'Owner',color:'#ffcf67',permissions:['*']}],'/api/moderation/logs':[],
        '/api/messages/19/thread':[{id:19,body:'Thread!',username:'Guest',created_at:'2026-10-13T10:00:00Z'}]};
        const key=String(url).split('?')[0];return {ok:true,status:200,json:async()=>values[url]??values[key]??{ok:true}};
      };''')
    page.add_script_tag(content=(BASE/'static/app.js').read_text()+'\n'+(BASE/'static/v8.js').read_text())
    page.wait_for_selector('#app-screen:not([hidden])',timeout=12000)
    page.wait_for_selector('[data-open-group="1"]',timeout=12000)
    assert page.locator('#rail-friends').count()==1
    page.locator('#rail-friends').click()
    page.wait_for_selector('.v8-modal')
    assert 'Guest' in page.locator('.v8-modal').inner_text()
    page.locator('.modal-close').click()
    page.locator('[data-open-group="1"]').click()
    page.wait_for_function("state.mode==='group' && state.group===1")
    assert page.locator('#messages .message').count()==1
    page.locator('#dock-settings').click()
    page.wait_for_selector('#v8-custom-profile')
    page.locator('#v8-custom-profile').click()
    page.wait_for_selector('#v8-profile-form')
    page.screenshot(path='/mnt/data/mr-h-media-v8-profile-preview.png')
    page.locator('.modal-close').click()
    page.locator('#rail-friends').click()
    page.screenshot(path='/mnt/data/mr-h-media-v8-friends-preview.png')
    assert not errors,errors
    print('PASS: v8 initialization, friends, group chat, settings profile, no runtime errors')
    browser.close()