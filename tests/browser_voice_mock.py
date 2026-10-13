"""UI smoke without real networking (local browser restrictions); not a live peer test."""
import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
BASE=Path(__file__).resolve().parents[1]
ME={'id':1,'username':'Mr_H','display_name':'Mr_H','bio':'','avatar':None,'role':'owner','theme':{}}
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--autoplay-policy=no-user-gesture-required'])
    page=browser.new_page(viewport={'width':1320,'height':820})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    html=(BASE/'static/index.html').read_text()
    html=html.replace('<link rel="stylesheet" href="/static/styles.css" />','<style>'+ (BASE/'static/styles.css').read_text() + '</style>')
    html=html.replace('<script src="/static/app.js" defer></script>','')
    page.set_content(html)
    page.evaluate("""
      window.WebSocket=class FakeSocket {
        static OPEN=1;constructor(url){this.url=url;this.readyState=0;setTimeout(()=>{this.readyState=1;this.onopen?.();
          if(url.includes('/ws/voice?'))setTimeout(()=>this.onmessage?.({data:JSON.stringify({type:'voice-roster',self_id:1,members:[{id:1,username:'Mr_H',display_name:'Mr_H',role:'owner',muted:false,sharing:false}]})}),1);
          else this.onmessage?.({data:JSON.stringify({type:'presence',online:[1]})});
        },5)}
        send(raw){if(!this.url.includes('/ws/voice?'))return;let v=JSON.parse(raw);
          if(v.type==='voice-status')setTimeout(()=>this.onmessage?.({data:JSON.stringify({type:'voice-status',user_id:1,muted:v.muted,sharing:v.sharing})}),1);
        }close(){this.readyState=3}
      };
      Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{
        getUserMedia:async()=>{const ctx=new AudioContext();return ctx.createMediaStreamDestination().stream},
        getDisplayMedia:async()=>{const canvas=document.createElement('canvas');canvas.width=800;canvas.height=450;const ctx=canvas.getContext('2d');ctx.fillStyle='#4f4299';ctx.fillRect(0,0,800,450);return canvas.captureStream(10)}
      }});
      window.fetch=async(url)=>{
        let values={'/api/me':{id:1,username:'Mr_H',display_name:'Mr_H',bio:'',avatar:null,role:'owner',theme:{}},
         '/api/channels':[{slug:'general',name:'general',topic:'everyone is welcome'}],
         '/api/members':[{id:1,username:'Mr_H',display_name:'Mr_H',role:'owner'}],'/api/dms':[],'/api/notifications':[],
         '/api/channels/general/messages':[],
         '/api/voice/channels':[{slug:'lounge',name:'Lounge',members:[]},{slug:'gaming',name:'Gaming',members:[]}],
         '/api/voice/ice-config':{iceServers:[]}};
        return {ok:true,status:200,json:async()=>values[url]||[]};
      }
    """)
    page.add_script_tag(content=(BASE/'static/app.js').read_text())
    page.wait_for_selector('#app-screen:not([hidden])',timeout=15000)
    page.wait_for_selector('[data-join-voice="lounge"]')
    page.locator('[data-join-voice="lounge"]').click()
    page.wait_for_selector('#voice-dock:not([hidden])')
    page.wait_for_function('voice.people.size===1')
    assert page.locator('#voice-stage').is_visible()
    page.locator('#voice-share').click()
    page.wait_for_function('voice.sharing===true')
    assert page.locator('.voice-stream video').count()==1
    page.screenshot(path='/mnt/data/mr-h-media-v6-voice-preview.png')
    page.locator('#voice-share').click()
    page.wait_for_function('voice.sharing===false')
    page.locator('#voice-leave').click()
    assert not page.locator('#voice-dock').is_visible()
    assert not errors,errors
    print('Browser UI mock PASS')
    browser.close()
