"""Static-browser UI check with mocked login/WebSockets because Chromium network policy blocks localhost."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright
BASE=Path(__file__).resolve().parents[1]
html=(BASE/'static/index.html').read_text()
html=re.sub(r'<link rel="stylesheet" href="/static/styles.css\?v=\d+" />','<style>'+(BASE/'static/styles.css').read_text()+'</style>',html)
html=re.sub(r'<script src="/static/app.js\?v=\d+" defer></script>','',html)
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--autoplay-policy=no-user-gesture-required'])
 page=browser.new_page(viewport={'width':1300,'height':820})
 errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content(html)
 page.evaluate('''const prefs=new Map();Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem:k=>prefs.get(k)||null,setItem:(k,v)=>prefs.set(k,String(v))}});
 window.WebSocket=class FakeSocket {
   static OPEN=1;constructor(url){this.url=url;this.readyState=0;setTimeout(()=>{this.readyState=1;this.onopen?.();
   if(url.includes('/ws/voice?'))setTimeout(()=>this.onmessage?.({data:JSON.stringify({type:'voice-roster',self_id:1,members:[{id:1,username:'Mr_H',display_name:'Mr_H',role:'owner',muted:false,sharing:false}]})}),10)
   else this.onmessage?.({data:JSON.stringify({type:'presence',online:[1]})});},10)}
   send(raw){}close(){this.readyState=3}
 };
 Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{
   getUserMedia:async()=>{const ctx=new AudioContext();return ctx.createMediaStreamDestination().stream},
   getDisplayMedia:async()=>{const canvas=document.createElement('canvas');canvas.width=800;canvas.height=450;
     const ctx=canvas.getContext('2d');ctx.fillStyle='#342a69';ctx.fillRect(0,0,800,450);ctx.fillStyle='white';ctx.font='40px Arial';ctx.fillText('Mr H LIVE',250,225);return canvas.captureStream(10)}
 }});
 window.fetch=async(url)=>{let values={
 '/api/me':{id:1,username:'Mr_H',display_name:'Mr_H',bio:'',avatar:null,role:'owner',theme:{}},
 '/api/channels':[{slug:'general',name:'general',topic:'everyone is welcome'},{slug:'help',name:'help',topic:'ask questions'}],
 '/api/members':[{id:1,username:'Mr_H',display_name:'Mr_H',role:'owner'}],'/api/dms':[],'/api/notifications':[],
 '/api/channels/general/messages':[],'/api/channels/help/messages':[],
 '/api/voice/channels':[{slug:'lounge',name:'Lounge',members:[]},{slug:'gaming',name:'Gaming',members:[]}],
 '/api/voice/ice-config':{iceServers:[]}};
 return {ok:true,status:200,json:async()=>values[url]||[]};};''')
 page.add_script_tag(content=(BASE/'static/app.js').read_text())
 page.wait_for_selector('#app-screen:not([hidden])',timeout=12000)
 page.locator('[data-join-voice="lounge"]').click()
 page.wait_for_function('voice.connected===true',timeout=10000)
 assert page.locator('#voice-stage').is_visible()
 assert page.locator('.voice-member-tile').count()==1
 assert page.locator('#voice-call-mute').is_visible()
 assert not page.locator('#chat-scroller').is_visible()
 assert page.evaluate('voiceCueContext?.state')=='running'
 # Simulate second participant joining, then sharing.
 page.evaluate("handleVoicePacket({type:'voice-joined',user:{id:2,username:'Guest_2',display_name:'Guest two',muted:false,sharing:false}})")
 assert page.locator('.voice-member-tile').count()==2
 page.screenshot(path='/mnt/data/mr-h-media-v7-voice-call-preview.png')
 page.locator('#voice-call-mute').click()
 assert page.evaluate('voice.muted')
 assert 'Unmute' in page.locator('#voice-call-mute').inner_text()
 page.locator('#voice-sounds').click()
 assert page.evaluate("localStorage.getItem('mrh-voice-sounds')")=='off'
 page.locator('#voice-sounds').click()
 assert page.evaluate("localStorage.getItem('mrh-voice-sounds')")=='on'
 page.locator('#voice-call-share').click()
 page.wait_for_function('voice.sharing===true')
 assert page.locator('.voice-stream video').count()==1
 page.screenshot(path='/mnt/data/mr-h-media-v7-stream-preview.png')
 page.locator('#voice-back-chat').click()
 assert page.locator('#chat-scroller').is_visible()
 assert page.evaluate('voice.room')=='lounge'
 page.locator('[data-channel="help"]').click()
 assert not page.locator('#voice-stage').is_visible()
 assert page.evaluate('voice.room')=='lounge'
 page.locator('#voice-show').click()
 assert page.locator('#voice-stage').is_visible()
 page.locator('#voice-call-leave').click()
 assert not page.evaluate('voice.ws')
 assert not page.locator('#voice-stage').is_visible()
 assert not errors,errors
 page.set_viewport_size({'width':390,'height':844})
 page.locator('#mobile-menu').click()
 page.locator('[data-join-voice="gaming"]').click()
 page.wait_for_function('voice.connected===true',timeout=10000)
 assert page.locator('#voice-call-leave').is_visible()
 assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'),page.evaluate('document.documentElement.scrollWidth')
 page.wait_for_function("document.querySelector('.sidebar').getBoundingClientRect().right < 61")
 page.screenshot(path='/mnt/data/mr-h-media-v7-mobile-preview.png')
 print('PASS: join/leave call screen, avatar tiles, locally generated sounds, sound preference, mute, screen share, return to chat, switch channel, reenter voice, mobile.')
 browser.close()
