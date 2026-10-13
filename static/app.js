'use strict';

const $ = (id) => document.getElementById(id);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"></use></svg>`;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (x) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const state = {group:null,groups:[],unreadRooms:{},v8Permissions:[],me:null,channels:[],members:[],dms:[],online:new Set(),mode:'channel',room:'general',peer:null,ws:null,msgIds:new Set(),msgById:new Map(),replyTo:null,earliest:null,hasOlder:false,loading:false,lastTyping:0,typingTimeout:null,toastTimer:null,sidebarOpen:innerWidth>630,showMembers:false,unreadDms:new Set(),upload:null,authMode:'login',settingsTab:'account',notifications:[],openedPost:null,themeDraft:null};
const palette = ['#7967e7','#408fad','#c77e9e','#b48b55','#54a18a','#a37bcc','#a47b64'];
const colorFor = (value) => {let h=0;for(const c of String(value))h=((h<<5)-h+c.charCodeAt(0))|0;return palette[Math.abs(h)%palette.length]};
function avatar(user,cls=''){if(!user)return `<div class="avatar ${cls}">?</div>`;
  const name = user.display_name||user.username||'?';
  if(user.avatar && /^\/uploads\/[0-9]+_[a-f0-9]{24}\.(png|gif)$/.test(user.avatar)) return `<img class="avatar ${cls}" src="${user.avatar}" alt="${escapeHtml(name)}" loading="lazy">`;
  return `<div class="avatar ${cls}" style="background:${colorFor(user.username)}">${escapeHtml(name.slice(0,1).toUpperCase())}</div>`;
}
const DEFAULT_THEME={accent:'#8a7eff',sidebar:'#151620',chat:'#1a1b26',text:'#e8e9f2'};
function applyTheme(theme){const cfg={...DEFAULT_THEME,...(theme||{})};for(const [name,value] of Object.entries(cfg)){if(/^#[a-fA-F0-9]{6}$/.test(value))document.documentElement.style.setProperty('--theme-'+name,value)}document.documentElement.dataset.customTheme='1';}
function pingify(text){return escapeHtml(text).replace(/(^|[^\w@])@([A-Za-z0-9_]{3,24})\b/g,(match,pre,name)=>pre+'<span class="ping-name">@'+name+'</span>')}
const roleBadge = (user) => user.role==='owner' ? '<span class="role-badge">✦ OWNER</span>' : (user.role && user.role!=='member'?'<span class="role-badge" style="color:'+escapeHtml(user.role_color||'#bbaaff')+';border-color:currentColor">'+escapeHtml(user.role_name||user.role)+'</span>':'');
async function api(path,options={}){
  let response;
  try{response=await fetch(path,{credentials:'same-origin',...options,headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(options.headers||{})}})}catch(e){throw new Error('Connection failed. Check your internet and try again.')}
  const result=await response.json().catch(()=>({}));
  if(!response.ok){if(response.status===401&&state.me&&path!=='/api/login')logoutLocal();throw new Error(typeof result.detail==='string'?result.detail:'Something went wrong.')}
  return result;
}
function showToast(message){const el=$('toast');el.textContent=message;el.hidden=false;clearTimeout(state.toastTimer);state.toastTimer=setTimeout(()=>el.hidden=true,3600)}
function authError(message){const el=$('auth-error');el.textContent=message;el.hidden=!message}
function switchAuth(mode){state.authMode=mode;const signup=mode==='signup';$('tab-login').classList.toggle('active',!signup);$('tab-signup').classList.toggle('active',signup);$('auth-heading').innerHTML=signup?'join the party<span>.</span>':'welcome back<span>.</span>';$('auth-subheading').textContent=signup?'create your account and get chatting.':'log in to pick up where you left off.';$('auth-submit').innerHTML=(signup?'Create account':'Log in')+' <span>→</span>';$('auth-password').autocomplete=signup?'new-password':'current-password';$('signup-hint').hidden=!signup;authError('')}
$('tab-login').addEventListener('click',()=>switchAuth('login'));
$('tab-signup').addEventListener('click',()=>switchAuth('signup'));
$('show-password').addEventListener('click',()=>{const p=$('auth-password');p.type=p.type==='password'?'text':'password'});
$('auth-form').addEventListener('submit',async(e)=>{
  e.preventDefault();const button=$('auth-submit');button.disabled=true;authError('');
  try{const data={username:$('auth-username').value.trim(),password:$('auth-password').value};
    state.me=await api('/api/'+(state.authMode==='signup'?'signup':'login'),{method:'POST',body:JSON.stringify(data)});
    $('auth-password').value='';await enterApp();
  }catch(error){authError(error.message)}finally{button.disabled=false}
});

async function enterApp(){
  $('auth-screen').hidden=true;$('app-screen').hidden=false;applyTheme(state.me.theme);loadNotifications();
  try{
    const [channels,members,dms]=await Promise.all([api('/api/channels'),api('/api/members'),api('/api/dms')]);
    state.channels=channels;state.members=members;state.dms=dms;
    $('new-channel').hidden=state.me.role!=='owner';$('owner-moderation').hidden=state.me.role!=='owner';updateDock();renderChannels();renderDms();renderMembers();
    await openChannel('general');
    await v8Initialize();
    if(innerWidth<=630)toggleSidebar(false);
  }catch(e){showToast(e.message)}
}
function updateDock(){if(!state.me)return;$('dock-avatar').innerHTML=avatar(state.me,'dock-avatar-img');$('dock-name').textContent=state.me.display_name;$('dock-handle').textContent='@'+state.me.username}
function renderChannels(){
  $('channel-list').innerHTML=state.channels.map(c=>`<div class="channel-line"><button class="channel-item ${state.mode==='channel'&&state.room===c.slug?'active':''}" data-channel="${escapeHtml(c.slug)}">${icon('hash')}<span>${escapeHtml(c.name)}</span></button>${state.me?.role==='owner'&&c.slug!=='general'?`<button type="button" class="delete-channel" data-del-channel="${escapeHtml(c.slug)}" title="Delete channel">×</button>`:''}</div>`).join('');
}
function renderDms(){
  $('dm-list').innerHTML=state.dms.length?state.dms.map(u=>`<button class="channel-item ${state.mode==='dm'&&state.peer===u.id?'active':''}" data-dm="${u.id}">${avatar(u,'dm-channel-avatar')}<span>${escapeHtml(u.display_name)}</span>${state.unreadDms.has(u.id)?'<span class="unread-dot"></span>':''}</button>`).join(''):'<div class="dm-empty">no conversations yet</div>';
}
function renderMembers(){
  const online=state.members.filter(u=>state.online.has(u.id));const offline=state.members.filter(u=>!state.online.has(u.id));
  $('members-total').textContent=`${state.members.length} MEMBER${state.members.length!==1?'S':''}`;
  $('online-count').textContent=online.length;$('offline-count').textContent=offline.length;
  const html=(list,isOffline)=>list.map(u=>`<button class="member-item ${isOffline?'offline':''}" data-user="${u.id}"><div class="member-avatar-wrap">${avatar(u,'member-avatar')}<span class="presence-dot"></span></div><span class="member-text">${escapeHtml(u.display_name)}<small>@${escapeHtml(u.username)}</small></span>${u.role==='owner'?'<span class="member-verified" title="Verified owner">✦</span>':''}</button>`).join('');
  $('online-list').innerHTML=html(online,false);$('offline-list').innerHTML=html(offline,true);
}
function channelBySlug(slug){return state.channels.find(c=>c.slug===slug)}
function peerById(id){return state.members.find(m=>m.id===id)||state.dms.find(m=>m.id===id)}
async function openChannel(slug){const ch=channelBySlug(slug);if(!ch)return;
  state.mode='channel';state.room=slug;state.peer=null;state.group=null;$('composer-area').hidden=false;clearSelectedFile();clearReply();
  $('header-symbol').innerHTML=icon('hash');$('room-name').textContent=ch.name;$('room-topic').textContent=ch.topic;$('message-input').placeholder='Message #'+ch.name;renderChannels();renderDms();await loadConversation();connectWs();toggleSidebar(false)
}
async function openDm(id){const peer=peerById(id);if(!peer)return;
  state.mode='dm';state.group=null;state.peer=id;$('composer-area').hidden=false;clearSelectedFile();clearReply();state.unreadDms.delete(id);if(!state.dms.some(u=>u.id===id))state.dms.unshift(peer);
  $('header-symbol').innerHTML=icon('message');$('room-name').textContent=peer.display_name;$('room-topic').textContent='Direct messages · @'+peer.username;$('message-input').placeholder='Message @'+peer.username;renderChannels();renderDms();await loadConversation();connectWs();toggleSidebar(false)
}
function urlForMessages(){return state.mode==='group'?`/api/groups/${state.group}/messages`:state.mode==='channel'?`/api/channels/${encodeURIComponent(state.room)}/messages`:`/api/dms/${state.peer}/messages`}
async function loadConversation(){
  $('messages').replaceChildren();$('older-holder').replaceChildren();$('typing-indicator').textContent='';
  state.msgIds=new Set();state.msgById=new Map();state.earliest=null;state.hasOlder=false;state.loading=true;
  const current=state.mode==='group'?`group:${state.group}`:state.mode==='channel'?`channel:${state.room}`:`dm:${state.peer}`;
  $('messages').innerHTML=welcomeMarkup();
  try{const list=await api(urlForMessages());if((state.mode==='group'?`group:${state.group}`:state.mode==='channel'?`channel:${state.room}`:`dm:${state.peer}`)!==current)return;
    state.hasOlder=list.length===80;if(list.length)state.earliest=list[0].id;
    appendMessages(list,true);updateOlder();scrollBottom();
  }catch(err){showToast(err.message)}finally{state.loading=false}
}
function welcomeMarkup(){const isChannel=state.mode==='channel';const n=state.mode==='group'?('👥 '+(state.groups.find(g=>g.id===state.group)?.name||'Group chat')):isChannel?('#'+channelBySlug(state.room)?.name):('@'+peerById(state.peer)?.username);
  return `<div class="chat-welcome"><div class="chat-welcome-icon">${icon(isChannel?'hash':'message')}</div><h2>${isChannel?'Welcome to '+escapeHtml(n)+'!':'Your conversation with '+escapeHtml(n)}</h2><p>${isChannel?'This is the beginning of the <strong>'+escapeHtml(n)+'</strong> channel. Pull up a chair and join the conversation.':'This is the start of your private conversation. Only you two can see these messages.'}</p><div class="welcome-line"><span>THE CONVERSATION STARTS HERE</span></div></div>`;
}
function formatTime(s){const t=new Date(s.endsWith('Z')||s.includes('+')?s:s.replace(' ','T')+'Z');return Number.isNaN(t.getTime())?'':t.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}
function replyExcerpt(reply){return String(reply?.preview||reply?.file||'(attachment)').slice(0,180)}
function replyReference(reply){return reply?`<button class="message-reply" type="button" data-jump-message="${reply.id}" title="Jump to referenced message"><span class="reply-connector">↳</span><strong>@${escapeHtml(reply.username||'unknown')}</strong><span class="reply-excerpt">${escapeHtml(replyExcerpt(reply))}</span></button>`:''}
function messageHtml(m){return `<article class="message" data-msg="${m.id}">${avatar(m.user,'message-avatar')}<div class="message-body">${replyReference(m.reply)}<div class="message-header"><button class="message-name" data-user="${m.user.id}">${escapeHtml(m.user.display_name)}</button>${roleBadge(m.user)}<span class="message-time">${formatTime(m.created_at)}</span>${state.mode==='group'?'':`<button type="button" class="reply-action" data-reply-message="${m.id}">↩ Reply</button><button type="button" class="reply-action" data-thread="${m.id}">Thread</button><button type="button" class="reply-action" data-reaction="${m.id}">☺ +</button>${v8HasPermission('moderate')?`<button type="button" class="reply-action" data-pin="${m.id}">Pin</button>`:''}`}<button type="button" class="reply-action" data-delete-message="${m.id}">✕</button></div><div class="message-text">${pingify(m.body)}</div>${attachmentMarkup(m.attachment)}${state.mode==='group'?'':v8ReactionHtml(m)}</div></article>`}
function clearReply(){state.replyTo=null;$('reply-composer').hidden=true;$('reply-composer').replaceChildren()}
function selectReply(id){const m=state.msgById.get(id);if(!m)return;state.replyTo={id:m.id,username:m.user.username,preview:(m.body||m.attachment?.name||'Attachment').slice(0,180)};$('reply-composer').hidden=false;$('reply-composer').innerHTML=`<div class="reply-selected"><span class="reply-connector">↳</span><div class="reply-selection-text"><strong>Replying to @${escapeHtml(state.replyTo.username)}</strong><span>${escapeHtml(state.replyTo.preview)}</span></div><button type="button" class="reply-cancel" id="reply-cancel" title="Cancel reply" aria-label="Cancel reply">×</button></div>`;$('message-input').focus()}
$('messages').addEventListener('click',e=>{const reply=e.target.closest('[data-reply-message]');if(reply){selectReply(Number(reply.dataset.replyMessage));return}const jump=e.target.closest('[data-jump-message]');if(jump){const original=$('messages').querySelector(`[data-msg="${Number(jump.dataset.jumpMessage)}"]`);if(original){original.scrollIntoView({block:'center',behavior:'smooth'});original.classList.remove('reply-highlight');void original.offsetWidth;original.classList.add('reply-highlight')}else showToast('Original message is older. Load older messages to view it.')}});
$('reply-composer').addEventListener('click',e=>{if(e.target.closest('.reply-cancel'))clearReply()});
function appendMessages(list,initial=false){const root=$('messages');
  for(const msg of list){if(state.msgIds.has(msg.id))continue;state.msgIds.add(msg.id);state.msgById.set(msg.id,msg);root.insertAdjacentHTML('beforeend',messageHtml(msg))}
  if(!initial){const scroll=$('chat-scroller');const nearBottom=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<165;if(nearBottom||list.some(m=>m.user.id===state.me.id))scrollBottom()}
}
function updateOlder(){$('older-holder').innerHTML=state.hasOlder?'<button id="load-older" class="load-older">Load older messages ↑</button>':''}
$('older-holder').addEventListener('click',async(e)=>{if(e.target.id!=='load-older'||state.loading||!state.earliest)return;
  state.loading=true;const btn=e.target;btn.disabled=true;btn.textContent='Loading…';const scroller=$('chat-scroller');const prevHeight=scroller.scrollHeight;
  try{const list=await api(urlForMessages()+`?before=${state.earliest}`);state.hasOlder=list.length===80;
    if(list.length)state.earliest=list[0].id;
    // insert older messages after welcome, before existing messages
    const root=$('messages');const firstMessage=root.querySelector('.message');
    for(const m of list){if(state.msgIds.has(m.id))continue;state.msgIds.add(m.id);state.msgById.set(m.id,m);if(firstMessage)firstMessage.insertAdjacentHTML('beforebegin',messageHtml(m));else root.insertAdjacentHTML('beforeend',messageHtml(m))}
    updateOlder();scroller.scrollTop+=scroller.scrollHeight-prevHeight;
  }catch(err){showToast(err.message);btn.disabled=false}finally{state.loading=false}
});
function scrollBottom(){$('chat-scroller').scrollTop=$('chat-scroller').scrollHeight}
function connectWs(){if(state.ws){state.ws.onclose=null;state.ws.close();state.ws=null}
  const query=state.mode==='group'?`group=${state.group}`:state.mode==='channel'?`channel=${encodeURIComponent(state.room)}`:`dm=${state.peer}`;
  const proto=location.protocol==='https:'?'wss':'ws';const ws=new WebSocket(`${proto}://${location.host}/ws?${query}`);state.ws=ws;
  ws.onmessage=(event)=>{if(state.ws!==ws)return;let packet;try{packet=JSON.parse(event.data)}catch{return}
    if(packet.type==='message'){
      // Ignore messages for a different conversation during navigation.
      if(state.mode==='channel'&&packet.message.channel_slug!==state.room)return;
      if(state.mode==='dm'&&packet.message.channel_slug!==null)return;
      if(state.mode==='group'&&packet.message.group_id!==state.group)return;
      const scroll=$('chat-scroller');const near=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<175;
      appendMessages([packet.message]);if(near||packet.message.user.id===state.me.id)scrollBottom();
      if(state.mode==='dm'&&packet.message.user.id!==state.me.id)refreshDms();
    }else if(packet.type==='reaction'){const m=state.msgById.get(packet.message_id);if(m){m.reactions=packet.reactions;const tile=document.querySelector(`[data-msg="${packet.message_id}"]`);if(tile){const old=tile.querySelector('.v8-reactions');if(old)old.outerHTML=v8ReactionHtml(m)}}}
    else if(packet.type==='message-deleted'){const el=document.querySelector(`[data-msg="${packet.message_id}"]`);el?.remove();state.msgIds.delete(packet.message_id);state.msgById.delete(packet.message_id)}
    else if(packet.type==='channel-deleted'){showToast('Channel was deleted.');state.channels=state.channels.filter(c=>c.slug!==packet.slug);openChannel('general')}else if(packet.type==='presence'){state.online=new Set(packet.online);renderMembers();if(packet.online.some(id=>id!==state.me.id&&!state.members.some(u=>u.id===id)))refreshMembers()}
    else if(packet.type==='ping-alert'){loadNotifications();showToast('Someone mentioned you.')}
    else if(packet.type==='dm-alert'){state.unreadDms.add(packet.from_user_id);refreshDms();if(state.me?.notification_settings?.dms!==false)showToast('New private message.')}
    else if(packet.type==='typing'&&packet.user_id!==state.me.id){$('typing-indicator').textContent=packet.name+' is typing…';clearTimeout(state.typingTimeout);state.typingTimeout=setTimeout(()=>$('typing-indicator').textContent='',1500)}
  };
  ws.onclose=(event)=>{if(state.ws!==ws)return;state.ws=null;if(event.code===1008){logoutLocal();showToast('Session ended or account banned.');return}setTimeout(()=>{if(state.me&&!state.ws)connectWs()},2800)};
}
async function refreshDms(){try{state.dms=await api('/api/dms');renderDms()}catch{}}
$('message-form').addEventListener('submit',async(e)=>{e.preventDefault();const input=$('message-input'),body=input.value.trim();if((!body&&!state.upload)||!state.me)return;const button=$('send-button');button.disabled=true;
  const destination=urlForMessages();const attachment=state.upload;const reply=state.mode==='group'?null:state.replyTo;if(!body&&!attachment){button.disabled=false;return}input.value='';input.style.height='auto';
  try{let msg;if(attachment){const form=new FormData();form.append('file',attachment);form.append('body',body);if(reply)form.append('reply_to_id',String(reply.id));msg=await api(destination.replace(/\/messages$/, '/attachments'),{method:'POST',body:form});clearSelectedFile()}else{msg=await api(destination,{method:'POST',body:JSON.stringify({body,reply_to_id:reply?.id??null})})}if(destination===urlForMessages()){if(!state.msgIds.has(msg.id)){appendMessages([msg]);scrollBottom()}if(state.replyTo?.id===reply?.id)clearReply()}if(state.mode==='dm')refreshDms()}
  catch(err){input.value=body;showToast(err.message)}finally{button.disabled=false;input.focus()}
});
$('message-input').addEventListener('keydown',(e)=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('message-form').requestSubmit()}});
$('message-input').addEventListener('input',()=>{const el=$('message-input');el.style.height='auto';el.style.height=Math.min(el.scrollHeight,130)+'px';const now=Date.now();if(now-state.lastTyping>1500&&state.ws?.readyState===WebSocket.OPEN){state.ws.send(JSON.stringify({type:'typing'}));state.lastTyping=now}});
const emojis='😀 😎 😂 ❤️ 🔥 ✨ 👍 🎉 👀 💜 🙌 💀 🫶 🤝 🚀 😭 👋 🌟 🎮 💯 🥳 😁 🤔 ☕ 🎵 💬 ✳️'.split(' ');
$('emoji-popover').innerHTML=emojis.map(e=>`<button type="button">${e}</button>`).join('');
$('emoji-button').addEventListener('click',()=>{$('emoji-popover').hidden=!$('emoji-popover').hidden});
$('emoji-popover').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;const i=$('message-input');i.value+=b.textContent;i.focus();$('emoji-popover').hidden=true});
document.addEventListener('click',e=>{if(!$('emoji-button').contains(e.target)&&!$('emoji-popover').contains(e.target))$('emoji-popover').hidden=true});
$('channel-list').addEventListener('click',async(e)=>{const del=e.target.closest('[data-del-channel]');if(del){const slug=del.dataset.delChannel;if(confirm('Delete #'+slug+' and all its messages?')){try{await api('/api/channels/'+encodeURIComponent(slug),{method:'DELETE'});state.channels=state.channels.filter(c=>c.slug!==slug);if(state.room===slug)openChannel('general');else renderChannels();showToast('Channel deleted.')}catch(err){showToast(err.message)}}return}const btn=e.target.closest('[data-channel]');if(btn)openChannel(btn.dataset.channel)});
$('dm-list').addEventListener('click',(e)=>{const btn=e.target.closest('[data-dm]');if(btn)openDm(Number(btn.dataset.dm))});
function toggleSidebar(open){state.sidebarOpen=open;$('app-screen').classList.toggle('sidebar-open',open)}
$('mobile-menu').addEventListener('click',()=>toggleSidebar(!state.sidebarOpen));
$('rail-home').addEventListener('click',()=>{if(innerWidth<=630)toggleSidebar(true);else openChannel('general')});
$('rail-posts').addEventListener('click',openPosts);$('sidebar-posts').addEventListener('click',openPosts);
$('owner-moderation').addEventListener('click',openModeration);
$('rail-dms').addEventListener('click',()=>{if(state.dms[0])openDm(state.dms[0].id);else openFinder()});
$('header-members').addEventListener('click',()=>{
  if(innerWidth<=630){
    const cards=state.members.map(u=>`<button type="button" class="finder-row" data-member="${u.id}">${avatar(u)}<span><strong>${escapeHtml(u.display_name)} ${roleBadge(u)}</strong><small>@${escapeHtml(u.username)} · ${state.online.has(u.id)?'online':'offline'}</small></span></button>`).join('');
    openModal(`<div class="compact-modal">${closeButton}<h2>Community members</h2><p class="muted-p">${state.members.length} people in this community.</p><div class="finder-results">${cards}</div></div>`);
    $('modal-content').addEventListener('click',e=>{const b=e.target.closest('[data-member]');if(b)showUserProfile(Number(b.dataset.member))},{once:false});
  }else if(innerWidth<=1150){$('app-screen').classList.toggle('show-members')}
  else{$('app-screen').classList.toggle('hide-members')}
});
$('header-search').addEventListener('click',openFinder);
$('new-dm').addEventListener('click',openFinder);
$('online-list').addEventListener('click',profileClick);$('offline-list').addEventListener('click',profileClick);
$('messages').addEventListener('click',profileClick);
function profileClick(e){const el=e.target.closest('[data-user]')||e.target.closest('.message-avatar');if(!el)return;const u=el.dataset.user?Number(el.dataset.user):Number(el.closest('.message')?.querySelector('[data-user]')?.dataset.user);if(u)showUserProfile(u)}

// Modal plumbing
function openModal(html){$('modal-content').innerHTML=html;$('modal-backdrop').hidden=false;document.body.style.overflow='hidden';$('modal-content').querySelector('.modal-close')?.addEventListener('click',closeModal)}
function closeModal(){$('modal-backdrop').hidden=true;$('modal-content').replaceChildren();document.body.style.overflow=''}
$('notification-button').addEventListener('click',openNotifications);
async function loadNotifications(){if(!state.me)return;try{state.notifications=await api('/api/notifications');const n=state.notifications.filter(x=>!x.read).length;const badge=$('notification-count');badge.hidden=n===0;badge.textContent=n>99?'99+':String(n);$('notification-button').classList.toggle('has-notifications',n>0)}catch{}}
async function openNotifications(){try{await loadNotifications();const records=state.notifications;openModal(`<div class="compact-modal notification-panel">${closeButton}<h2>Pings & mentions</h2><p class="muted-p">You'll be notified when someone mentions @${escapeHtml(state.me.username)}.</p><div class="notification-list">${records.length?records.map(n=>`<button class="notification-item ${n.read?'':'unread'}" data-ping="${n.id}"><strong>@${escapeHtml(n.sender)}</strong><span>${escapeHtml(n.preview)}</span><small>${n.kind==='post'?'Community posts':n.kind==='channel'?'#'+escapeHtml(n.context):n.kind==='friend'?'Friend request':n.kind==='group'?'Group chat':'Direct messages'} · ${formatTime(n.created_at)}</small></button>`).join(''):'<p class="muted-p">No mentions yet.</p>'}</div></div>`);$('modal-content').querySelectorAll('[data-ping]').forEach(b=>b.addEventListener('click',()=>jumpToPing(records.find(n=>n.id===Number(b.dataset.ping)))));await api('/api/notifications/read',{method:'POST'});await loadNotifications()}catch(err){showToast(err.message)}}
async function jumpToPing(n){if(!n)return;closeModal();if(n.kind==='post'){await openPosts();const target=document.querySelector(`[data-post="${n.target_id}"]`);if(target){target.scrollIntoView({behavior:'smooth',block:'center'});if(n.context==='comments')await toggleComments(n.target_id)}}else if(n.kind==='channel'){await openChannel(n.context);const target=document.querySelector(`[data-msg="${n.target_id}"]`);if(target)target.scrollIntoView({behavior:'smooth',block:'center'})}else if(n.kind==='group'){await v8ReloadGroups();await v8OpenGroup(Number(n.context))}else if(n.kind==='friend'){await v8Friends()}else if(n.kind==='dm'){const sender=state.members.find(m=>m.username.toLowerCase()===n.sender.toLowerCase());if(sender)await openDm(sender.id)}}
setInterval(()=>{if(state.me)loadNotifications()},30000);
$('modal-backdrop').addEventListener('mousedown',(e)=>{if(e.target===$('modal-backdrop'))closeModal()});
document.addEventListener('keydown',(e)=>{if(e.key==='Escape'&&!$('modal-backdrop').hidden)closeModal()});
const closeButton='<button class="modal-close" type="button" aria-label="Close dialog">'+icon('close')+'</button>';
function showUserProfile(id){const u=peerById(id)|| (state.me?.id===id?state.me:null);if(!u)return;
  openModal(`<div class="compact-modal" style="padding:0;max-width:440px">${closeButton}<div class="profile-banner" style="background:${escapeHtml(u.profile_color||'#4a3c66')};${u.banner?`background-image:url('${u.banner}');background-size:cover;background-position:center`:``}"></div>${avatar(u,'user-modal-avatar')}<div class="public-profile-body"><h2>${escapeHtml(u.display_name)} ${roleBadge(u)}</h2><div class="handle">@${escapeHtml(u.username)}</div><div class="settings-label" style="padding:0">ABOUT ME</div><p class="about">${escapeHtml(u.bio||'Just hanging out on Mr H Media.')}</p><p class="v8-user-status">${escapeHtml(u.status||'')}</p>${u.id!==state.me.id?'<button class="save-btn" id="profile-message">'+icon('message')+' &nbsp; Send message</button>':'<button class="save-btn" id="profile-edit">Edit my profile</button>'}</div></div>`);
  if(v8HasPermission('ban_users')&&u.role!=='owner'){$('modal-content').querySelector('.public-profile-body').insertAdjacentHTML('beforeend',`<button class="ban-profile-btn" id="ban-profile">Ban @${escapeHtml(u.username)}</button>`);$('ban-profile').addEventListener('click',()=>banFromProfile(u))}
  $('profile-message')?.addEventListener('click',()=>{closeModal();openDm(u.id)});
  if(u.id!==state.me.id){$('modal-content').querySelector('.public-profile-body').insertAdjacentHTML('beforeend',`<div class="v8-user-actions"><button data-v8-friend="${u.id}">Add friend</button><button data-v8-block="${u.id}">Block</button>${v8HasPermission('manage_roles')?`<button data-v8-assign="${u.id}">Assign role</button>`:''}</div>`)}$('profile-edit')?.addEventListener('click',()=>openSettings('profile'));
}
async function openFinder(){openModal(`<div class="compact-modal">${closeButton}<h2>Find your people.</h2><p class="muted-p">Search the community and start a private conversation.</p><input class="finder-input" id="finder-search" type="search" placeholder="Search username or display name…" autocomplete="off"><div class="finder-results" id="finder-results"></div></div>`);
  const field=$('finder-search');const render=(list)=>{$('finder-results').innerHTML=list.length?list.filter(u=>u.id!==state.me.id).map(u=>`<button type="button" class="finder-row" data-find="${u.id}">${avatar(u)}<span><strong>${escapeHtml(u.display_name)} ${roleBadge(u)}</strong><small>@${escapeHtml(u.username)}</small></span></button>`).join(''):'<div class="finder-empty">No matching people found.</div>'};
  render(state.members);field.focus();let seq=0;field.addEventListener('input',async()=>{const n=++seq;try{const list=await api('/api/members?q='+encodeURIComponent(field.value));if(seq===n)render(list)}catch(e){showToast(e.message)}});
  $('finder-results').addEventListener('click',(e)=>{const b=e.target.closest('[data-find]');if(b){const id=Number(b.dataset.find);closeModal();if(!peerById(id)){refreshMembers().then(()=>openDm(id))}else openDm(id)}});
}
async function refreshMembers(){try{state.members=await api('/api/members');renderMembers()}catch{}}

function openSettings(tab='account'){
  state.settingsTab=tab;
  openModal(`<div class="settings-layout"><aside class="settings-sidebar"><h4>Settings <span style="color:#998afa">✳</span></h4><div class="settings-label">USER SETTINGS</div><button type="button" class="settings-nav" data-tab="account">${icon('users')} My account</button><button type="button" class="settings-nav" data-tab="profile">${icon('edit')} Profile</button><button type="button" class="settings-nav" data-tab="security">${icon('lock')} Security</button><button type="button" class="settings-nav" data-tab="appearance">${icon('spark')} Themes</button><button type="button" class="settings-nav settings-logout" id="settings-logout">${icon('logout')} Log out</button></aside><div class="settings-body" id="settings-body">${closeButton}<div id="settings-main"></div></div></div>`);
  $('modal-content').querySelector('.modal-close').addEventListener('click',closeModal);
  $('modal-content').querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>renderSettings(b.dataset.tab)));
  $('settings-logout').addEventListener('click',logout);
  renderSettings(tab)
}
function renderSettings(tab){state.settingsTab=tab;$('modal-content').querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===tab));const u=state.me;let html='';
  if(tab==='account'){
    html=`<h2>My account</h2><p class="muted-p">the basics. all about you.</p><div class="profile-preview"><div class="profile-banner"></div><div class="profile-preview-bottom">${avatar(u,'profile-large-avatar')}<label for="avatar-file" class="avatar-upload" title="Change profile picture">${icon('edit')}</label><input type="file" id="avatar-file" accept="image/png,image/jpeg,image/webp,image/gif" hidden><label class="avatar-change-text" for="avatar-file">Change PFP</label><strong>${escapeHtml(u.display_name)} ${roleBadge(u)}</strong><small>@${escapeHtml(u.username)}</small></div></div><form class="settings-form" id="profile-form"><label>DISPLAY NAME</label><input name="display_name" maxlength="40" value="${escapeHtml(u.display_name)}" required><label>USERNAME</label><input name="username" maxlength="24" value="${escapeHtml(u.username)}" required><p class="settings-help">3–24 characters. letters, numbers and underscores only.</p><label>ABOUT ME</label><textarea name="bio" maxlength="240" placeholder="Tell the community a little about yourself…">${escapeHtml(u.bio)}</textarea><div class="form-button-row"><button type="submit" class="save-btn">Save changes</button><span class="settings-message" id="profile-status"></span></div></form>`;
  }else if(tab==='profile'){
    html=`<h2>Your profile</h2><p class="muted-p">give your profile a little personality.</p><div class="profile-preview"><div class="profile-banner"></div><div class="profile-preview-bottom">${avatar(u,'profile-large-avatar')}<label for="avatar-file" class="avatar-upload" title="Change profile picture">${icon('edit')}</label><input type="file" id="avatar-file" accept="image/png,image/jpeg,image/webp,image/gif" hidden><label class="avatar-change-text" for="avatar-file">Change PFP</label><strong>${escapeHtml(u.display_name)} ${roleBadge(u)}</strong><small>@${escapeHtml(u.username)}</small></div></div><form class="settings-form" id="profile-form"><label>DISPLAY NAME</label><input name="display_name" maxlength="40" value="${escapeHtml(u.display_name)}" required><label>USERNAME</label><input name="username" maxlength="24" value="${escapeHtml(u.username)}" required><label>ABOUT ME</label><textarea name="bio" maxlength="240" placeholder="What should people know about you?">${escapeHtml(u.bio)}</textarea><div class="form-button-row"><button type="submit" class="save-btn">Update profile</button><span class="settings-message" id="profile-status"></span></div></form>`;
  }else if(tab==='appearance'){
    const t=state.themeDraft||{...DEFAULT_THEME,...(u.theme||{})};
    html=`<h2>Custom theme maker</h2><p class="muted-p">Change colors live. Your theme is saved to your account.</p><div class="theme-presets"><button type="button" data-preset="default">Midnight</button><button type="button" data-preset="ocean">Ocean</button><button type="button" data-preset="rose">Rose</button><button type="button" data-preset="forest">Forest</button></div><form id="theme-form" class="theme-form">${[['accent','Accent'],['sidebar','Sidebar'],['chat','Chat background'],['text','Text']].map(([k,l])=>`<label>${l}<input type="color" name="${k}" value="${escapeHtml(t[k])}"></label>`).join('')}<div class="theme-actions"><button class="save-btn" type="submit">Save theme</button><button type="button" class="theme-reset" id="theme-reset">Reset</button></div><span class="settings-message" id="theme-status"></span></form>`;
  }else{
    html=`<h2>Password & security</h2><p class="muted-p">keep your account safe and yours.</p><div class="password-card">${icon('shield')}<span>Use a unique password with at least 8 characters. Changing it will log out other devices.</span></div><form class="settings-form" id="password-form"><label>CURRENT PASSWORD</label><input type="password" name="current_password" autocomplete="current-password" required><label>NEW PASSWORD</label><input type="password" name="new_password" minlength="8" maxlength="128" autocomplete="new-password" required><label>CONFIRM NEW PASSWORD</label><input type="password" name="confirm" minlength="8" maxlength="128" autocomplete="new-password" required><div class="form-button-row"><button type="submit" class="save-btn">Change password</button><span class="settings-message" id="password-status"></span></div></form>`;
  }
  $('settings-main').innerHTML=html;
  $('profile-form')?.addEventListener('submit',saveProfile);
  $('avatar-file')?.addEventListener('change',uploadAvatar);
  $('password-form')?.addEventListener('submit',savePassword);
  $('theme-form')?.addEventListener('input',e=>{const form=e.currentTarget;state.themeDraft=Object.fromEntries(new FormData(form).entries());applyTheme(state.themeDraft)});
  $('theme-form')?.addEventListener('submit',saveTheme);
  $('modal-content').querySelectorAll('[data-preset]').forEach(b=>b.addEventListener('click',()=>{const presets={default:DEFAULT_THEME,ocean:{accent:'#45bed8',sidebar:'#101d2b',chat:'#102536',text:'#ddf7ff'},rose:{accent:'#f195c8',sidebar:'#251526',chat:'#281d30',text:'#fff0f8'},forest:{accent:'#71d79e',sidebar:'#10251e',chat:'#14271f',text:'#e3fff0'}};state.themeDraft={...presets[b.dataset.preset]};applyTheme(state.themeDraft);renderSettings('appearance')}));
  $('theme-reset')?.addEventListener('click',()=>{state.themeDraft={...DEFAULT_THEME};applyTheme(state.themeDraft);renderSettings('appearance')});
}
async function saveTheme(e){e.preventDefault();const status=$('theme-status');status.textContent='Saving…';try{state.me=await api('/api/me/theme',{method:'PATCH',body:JSON.stringify(state.themeDraft||DEFAULT_THEME)});state.themeDraft=null;applyTheme(state.me.theme);status.textContent='Theme saved to your profile.';showToast('Theme saved.')}catch(err){status.textContent=err.message}}
async function saveProfile(e){e.preventDefault();const form=e.currentTarget;const status=$('profile-status');status.classList.remove('error');status.textContent='Saving…';const data=Object.fromEntries(new FormData(form).entries());
  try{state.me=await api('/api/me',{method:'PATCH',body:JSON.stringify(data)});updateDock();await refreshMembers();renderSettings(state.settingsTab);$('profile-status').textContent='Saved!';showToast('Profile updated.')}
  catch(err){status.textContent=err.message;status.classList.add('error')}
}
async function uploadAvatar(e){const file=e.currentTarget.files?.[0];if(!file)return;if(file.size>4*1024*1024){showToast('Maximum photo size: 4 MB.');return}
  const form=new FormData();form.append('file',file);try{state.me=await api('/api/me/avatar',{method:'POST',body:form});updateDock();await refreshMembers();renderSettings(state.settingsTab);showToast('Profile picture updated.')}
  catch(err){showToast(err.message)}
}
async function savePassword(e){e.preventDefault();const form=e.currentTarget;const status=$('password-status');const data=Object.fromEntries(new FormData(form).entries());if(data.new_password!==data.confirm){status.textContent='Passwords do not match.';status.classList.add('error');return}status.textContent='Updating…';status.classList.remove('error');
  try{await api('/api/me/password',{method:'POST',body:JSON.stringify({current_password:data.current_password,new_password:data.new_password})});form.reset();status.textContent='Password changed!'}catch(err){status.textContent=err.message;status.classList.add('error')}
}
async function logout(){try{await api('/api/logout',{method:'POST'})}catch{}logoutLocal()}
function logoutLocal(){if(state.ws){state.ws.onclose=null;state.ws.close();state.ws=null}state.me=null;closeModal();$('app-screen').hidden=true;$('auth-screen').hidden=false;switchAuth('login');$('auth-password').value=''}
$('dock-avatar').addEventListener('click',()=>openSettings('profile'));$('dock-avatar').title='Change your profile picture';$('rail-settings').addEventListener('click',()=>openSettings());$('dock-settings').addEventListener('click',()=>openSettings());
$('new-channel').addEventListener('click',()=>{
  openModal(`<div class="compact-modal">${closeButton}<h2>Create a channel</h2><p class="muted-p">Give your community a new place to talk.</p><form id="channel-create-form" class="modal-form"><label>CHANNEL NAME</label><input name="name" required maxlength="28" placeholder="music-chat"><label>TOPIC (OPTIONAL)</label><input name="topic" maxlength="120" placeholder="talk about everything music"><span class="settings-message error" id="create-error"></span><button type="submit" class="save-btn">Create channel</button></form></div>`);
  $('channel-create-form').addEventListener('submit',async(e)=>{e.preventDefault();const form=e.target;const data=Object.fromEntries(new FormData(form).entries());try{const channel=await api('/api/channels',{method:'POST',body:JSON.stringify(data)});state.channels.push(channel);closeModal();openChannel(channel.slug);showToast('Channel created.')}catch(err){$('create-error').textContent=err.message}})
});

function attachmentMarkup(a){if(!a)return '';const url=a.url||'/api/attachments/'+encodeURIComponent(a.id);const name=escapeHtml(a.name);const size=(a.size/1024).toFixed(1)+' KB';const image=['image/png','image/jpeg','image/webp','image/gif'].includes(a.mime);return `<div class="attachment-card">${image?`<a href="${url}" target="_blank" rel="noopener"><img class="attachment-image" src="${url}" loading="lazy" alt="${name}"></a>`:''}<a class="attachment-link" href="${url}" ${image?'':'download'}>${icon('paperclip')}<span>${name}</span><small>${size}</small></a></div>`}
function clearSelectedFile(){state.upload=null;$('chat-file').value='';$('selected-file').hidden=true;$('selected-file').textContent=''}
$('add-attachment').addEventListener('click',()=>$('chat-file').click());
$('chat-file').addEventListener('change',e=>{const file=e.target.files?.[0];if(!file)return;if(file.size>8*1024*1024){showToast('Maximum file size is 8 MB.');clearSelectedFile();return}state.upload=file;$('selected-file').hidden=false;$('selected-file').innerHTML='Attached: <strong>'+escapeHtml(file.name)+'</strong> <button type="button" id="clear-chat-file" aria-label="Remove attachment">×</button>';$('clear-chat-file').addEventListener('click',clearSelectedFile);});

function postMarkup(p){return `<article class="post-card" data-post="${p.id}"><div class="post-author">${avatar(p.user)}<div><strong>${escapeHtml(p.user.display_name)} ${roleBadge(p.user)}</strong><small>@${escapeHtml(p.user.username)} · ${formatTime(p.created_at)}</small></div>${p.user.id===state.me.id||state.me.role==='owner'?`<button type="button" class="post-delete" data-delete-post="${p.id}" title="Delete post">Delete</button>`:''}</div><h3>${escapeHtml(p.title)}</h3><p>${pingify(p.body)}</p>${attachmentMarkup(p.attachment)}<div class="post-actions"><button type="button" data-toggle-comments="${p.id}">${icon('message')} <span>${p.comment_count||0}</span> comments</button></div><div class="comment-section" data-comment-section="${p.id}" hidden></div></article>`}
async function openPosts(){state.mode='posts';state.peer=null;state.group=null;$('composer-area').hidden=true;clearSelectedFile();clearReply();if(state.ws){state.ws.onclose=null;state.ws.close();state.ws=null}renderChannels();renderDms();$('header-symbol').innerHTML=icon('image');$('room-name').textContent='Community posts';$('room-topic').textContent='Updates, creations and announcements';$('typing-indicator').textContent='';$('older-holder').replaceChildren();$('messages').innerHTML='<p class="posts-loading">Loading posts…</p>';toggleSidebar(false);await refreshPosts()}
async function refreshPosts(){if(state.mode!=='posts')return;try{const posts=await api('/api/posts');if(state.mode!=='posts')return;$('messages').innerHTML=`<section class="posts-feed"><div class="posts-head"><div><span class="feed-kicker">MR H MEDIA</span><h2>Community feed<span>.</span></h2><p>Share news, screenshots, work and files with everyone.</p></div><button type="button" class="save-btn" id="new-post">+ New post</button></div><div id="posts-list">${posts.length?posts.map(postMarkup).join(''):'<div class="posts-empty">No posts yet. Publish the first one.</div>'}</div></section>`;$('new-post').addEventListener('click',openPostEditor)}catch(e){showToast(e.message)}}
$('messages').addEventListener('click',async(e)=>{
 const toggle=e.target.closest('[data-toggle-comments]');if(toggle){await toggleComments(Number(toggle.dataset.toggleComments));return}
 const commentDelete=e.target.closest('[data-delete-comment]');if(commentDelete){if(confirm('Delete comment?')){const postId=Number(commentDelete.closest('[data-post]').dataset.post);try{await api(`/api/posts/${postId}/comments/${commentDelete.dataset.deleteComment}`,{method:'DELETE'});await refreshComments(postId)}catch(err){showToast(err.message)}}return}
 const b=e.target.closest('[data-delete-post]');if(!b)return;if(!confirm('Delete this post?'))return;try{await api('/api/posts/'+b.dataset.deletePost,{method:'DELETE'});await refreshPosts();showToast('Post deleted.')}catch(err){showToast(err.message)}});
async function toggleComments(postId){const area=document.querySelector(`[data-comment-section="${postId}"]`);if(!area)return;if(!area.hidden){area.hidden=true;return}area.hidden=false;area.innerHTML='<p class="muted-p">Loading discussion…</p>';await refreshComments(postId)}
function commentMarkup(c){return `<div class="comment-row">${avatar(c.user)}<div class="comment-content"><div class="comment-meta"><button type="button" data-user="${c.user.id}">${escapeHtml(c.user.display_name)}</button> ${roleBadge(c.user)} <small>${formatTime(c.created_at)}</small>${c.user.id===state.me.id||state.me.role==='owner'?`<button type="button" class="comment-delete" data-delete-comment="${c.id}">Delete</button>`:''}</div><p>${pingify(c.body)}</p></div></div>`}
async function refreshComments(postId){const area=document.querySelector(`[data-comment-section="${postId}"]`);if(!area||area.hidden)return;try{const rows=await api(`/api/posts/${postId}/comments`);area.innerHTML=`<div class="comments-header">Discussion · ${rows.length} comments</div><div class="comment-list">${rows.length?rows.map(commentMarkup).join(''):'<p class="muted-p">No comments yet. Start the conversation.</p>'}</div><form class="comment-form" data-comment-form="${postId}"><input maxlength="1500" name="body" required placeholder="Write a comment… use @username to ping" aria-label="Comment on post"><button type="submit" class="save-btn">Reply</button></form>`;const counter=document.querySelector(`[data-post="${postId}"] [data-toggle-comments] span`);if(counter)counter.textContent=rows.length}catch(err){showToast(err.message)}}
$('messages').addEventListener('submit',async e=>{const form=e.target.closest('[data-comment-form]');if(!form)return;e.preventDefault();const postId=Number(form.dataset.commentForm),body=form.querySelector('input').value.trim(),btn=form.querySelector('button');if(!body)return;btn.disabled=true;try{await api(`/api/posts/${postId}/comments`,{method:'POST',body:JSON.stringify({body})});await refreshComments(postId)}catch(err){showToast(err.message);btn.disabled=false}});
function openPostEditor(){openModal(`<div class="compact-modal">${closeButton}<h2>New post</h2><p class="muted-p">Publish to your community. You can include one file (up to 8 MB).</p><form class="modal-form" id="post-form"><label>TITLE</label><input name="title" maxlength="120" required placeholder="What are you sharing?"><label>POST</label><textarea name="body" maxlength="5000" rows="5" placeholder="Write something…"></textarea><label>ATTACHMENT (OPTIONAL)</label><input type="file" name="file" id="post-file"><span id="post-status" class="settings-message"></span><button type="submit" class="save-btn">Publish post</button></form></div>`);$('post-form').addEventListener('submit',async(e)=>{e.preventDefault();const form=e.currentTarget,btn=form.querySelector('button[type=submit]'),data=new FormData(form),file=$('post-file').files?.[0];btn.disabled=true;$('post-status').textContent='Publishing…';try{if(file){if(file.size>8*1024*1024)throw new Error('Max file size: 8 MB.');await api('/api/posts/with-file',{method:'POST',body:data})}else{await api('/api/posts',{method:'POST',body:JSON.stringify({title:data.get('title'),body:data.get('body')})})}closeModal();await openPosts();showToast('Post published.')}catch(err){$('post-status').textContent=err.message;btn.disabled=false}})}

async function banFromProfile(u){if(!confirm('Ban @'+u.username+' from Mr H Media? They will be logged out immediately.'))return;const reason=prompt('Reason for ban (optional):')||'';try{await api('/api/admin/users/'+u.id+'/ban',{method:'POST',body:JSON.stringify({reason})});closeModal();await refreshMembers();showToast('User banned.')}catch(err){showToast(err.message)}}
async function openModeration(){if(state.me?.role!=='owner')return;openModal(`<div class="compact-modal moderation-panel">${closeButton}<h2>Moderation</h2><p class="muted-p">Ban users, or restore previously banned accounts.</p><input id="admin-search" class="finder-input" placeholder="Filter users…"><div id="moderation-list" class="finder-results"></div></div>`);let list=[];const render=()=>{$('moderation-list').innerHTML=list.filter(u=>(u.username+' '+u.display_name).toLowerCase().includes($('admin-search').value.toLowerCase())).map(u=>`<div class="moderation-row">${avatar(u)}<div><strong>${escapeHtml(u.username)}</strong><small>${u.role==='owner'?'owner':u.banned?'BANNED · '+escapeHtml(u.ban_reason||'no reason'):'member'}</small></div>${u.role==='owner'?'':`<button data-admin-id="${u.id}" data-admin-action="${u.banned?'unban':'ban'}">${u.banned?'Unban':'Ban'}</button>`}</div>`).join('')};try{list=await api('/api/admin/users');render()}catch(e){showToast(e.message)}$('admin-search').addEventListener('input',render);$('moderation-list').addEventListener('click',async(e)=>{const b=e.target.closest('[data-admin-action]');if(!b)return;const u=list.find(x=>x.id===Number(b.dataset.adminId));if(!u)return;const action=b.dataset.adminAction;if(!confirm((action==='ban'?'Ban ':'Unban ')+u.username+'?'))return;const reason=action==='ban'?(prompt('Reason for ban (optional):')||''):'';try{await api('/api/admin/users/'+u.id+'/'+action,{method:'POST',...(action==='ban'?{body:JSON.stringify({reason})}:{})});list=await api('/api/admin/users');render();await refreshMembers();showToast('User '+(action==='ban'?'banned.':'unbanned.'))}catch(err){showToast(err.message)}})}

async function boot(){try{state.me=await api('/api/me');await enterApp()}catch(e){state.me=null;$('auth-screen').hidden=false;$('app-screen').hidden=true}}
boot();

// Accessible @-mention picker for message composer.
const mentionMenu=document.createElement('div');mentionMenu.id='mention-menu';mentionMenu.className='mention-menu';mentionMenu.hidden=true;$('composer-area').appendChild(mentionMenu);
let mentionMatch=null;let mentionSelected=0;
function updateMentionMenu(){const input=$('message-input');const prefix=input.value.slice(0,input.selectionStart);const m=prefix.match(/(^|\s)@([A-Za-z0-9_]{0,24})$/);if(!m||state.mode==='posts'){mentionMenu.hidden=true;mentionMatch=null;return}const list=state.members.filter(u=>u.id!==state.me?.id&&u.username.toLowerCase().startsWith(m[2].toLowerCase())).slice(0,8);if(!list.length){mentionMenu.hidden=true;return}mentionMatch={start:input.selectionStart-m[2].length-1,end:input.selectionStart,list};mentionSelected=0;mentionMenu.innerHTML='<div class="mention-caption">PING SOMEONE</div>'+list.map((u,i)=>`<button type="button" data-mention="${i}" class="${i===0?'selected':''}">${avatar(u)}<span>@${escapeHtml(u.username)}</span></button>`).join('');mentionMenu.hidden=false}
function chooseMention(idx){if(!mentionMatch)return;const u=mentionMatch.list[idx];if(!u)return;const field=$('message-input');field.value=field.value.slice(0,mentionMatch.start)+'@'+u.username+' '+field.value.slice(mentionMatch.end);const caret=mentionMatch.start+u.username.length+2;field.focus();field.setSelectionRange(caret,caret);mentionMenu.hidden=true;mentionMatch=null}
$('message-input').addEventListener('input',updateMentionMenu);
$('message-input').addEventListener('keydown',e=>{if(mentionMenu.hidden||!mentionMatch)return;if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();mentionSelected=(mentionSelected+(e.key==='ArrowDown'?1:-1)+mentionMatch.list.length)%mentionMatch.list.length;mentionMenu.querySelectorAll('[data-mention]').forEach((el,i)=>el.classList.toggle('selected',i===mentionSelected))}else if(e.key==='Tab'||e.key==='Enter'){e.preventDefault();e.stopImmediatePropagation();chooseMention(mentionSelected)}else if(e.key==='Escape'){e.preventDefault();mentionMenu.hidden=true}},{capture:true});
mentionMenu.addEventListener('click',e=>{const b=e.target.closest('[data-mention]');if(b)chooseMention(Number(b.dataset.mention))});


// Voice rooms: WebRTC sends audio/screen media end-to-end between participants.
// The server relays only membership and SDP/ICE negotiation metadata.
const voice={ws:null,room:null,rooms:[],people:new Map(),peers:new Map(),mic:null,screen:null,
  muted:true,sharing:false,stageOpen:false,connected:false,iceServers:[],epoch:0};
// All call cues are generated locally. No remote audio files or tracking.
let voiceCueContext=null;
let voiceSoundsEnabled=true;
try{voiceSoundsEnabled=localStorage.getItem('mrh-voice-sounds')!=='off'}catch{}
function primeVoiceAudio(){
  if(!voiceSoundsEnabled)return;
  try{voiceCueContext??=new (window.AudioContext||window.webkitAudioContext)();
    if(voiceCueContext.state==='suspended')void voiceCueContext.resume().catch(()=>{});
  }catch{voiceCueContext=null}
}
function playVoiceCue(which){
  if(!voiceSoundsEnabled||!voiceCueContext||voiceCueContext.state!=='running')return;
  const ctx=voiceCueContext;const now=ctx.currentTime+.005;
  const tones=which==='join'?[{freq:560,time:0,dur:.105},{freq:760,time:.105,dur:.135}]:[{freq:680,time:0,dur:.105},{freq:455,time:.105,dur:.145}];
  try{for(const tone of tones){const osc=ctx.createOscillator(),gain=ctx.createGain(),start=now+tone.time;
      osc.type='sine';osc.frequency.setValueAtTime(tone.freq,start);gain.gain.setValueAtTime(.0001,start);
      gain.gain.exponentialRampToValueAtTime(.085,start+.017);
      gain.gain.exponentialRampToValueAtTime(.0001,start+tone.dur);
      osc.connect(gain);gain.connect(ctx.destination);osc.start(start);osc.stop(start+tone.dur+.01)
    }}catch{}
}
function setVoiceFocus(open){
  voice.stageOpen=!!open&&!!voice.ws;
  if(voice.stageOpen)toggleSidebar(false);
  $('app-screen').classList.toggle('voice-focused',voice.stageOpen);
  document.querySelector('.chat-main')?.classList.toggle('voice-focused',voice.stageOpen);
  updateVoiceUI();
}
function leaveVoiceFocus(){if(voice.stageOpen)setVoiceFocus(false)}
function voiceSignal(type,to,data){if(voice.ws?.readyState===WebSocket.OPEN)voice.ws.send(JSON.stringify({type,to,data}))}
function voiceStatus(){if(voice.ws?.readyState===WebSocket.OPEN)voice.ws.send(JSON.stringify({type:'voice-status',muted:voice.muted,sharing:voice.sharing}))}
async function refreshVoiceRooms(){if(!state.me)return;try{const rooms=await api('/api/voice/channels');voice.rooms=rooms;renderVoiceRooms()}catch(e){console.warn('Voice rooms unavailable:',e)}}
function renderVoiceRooms(){const root=$('voice-list');if(!root)return;
  root.innerHTML=voice.rooms.map(r=>`<div class="voice-channel"><button type="button" class="channel-item ${voice.room===r.slug?'active':''}" data-join-voice="${escapeHtml(r.slug)}" title="Join ${escapeHtml(r.name)}">${icon('users')}<span>${escapeHtml(r.name)}</span><span class="voice-count">${voice.room===r.slug&&voice.ws?voice.people.size:(r.members?.length||0)}/8</span></button>${state.me?.role==='owner'&&r.slug!=='lounge'?`<button class="delete-channel" data-delete-voice="${escapeHtml(r.slug)}" title="Delete voice room">×</button>`:''}</div>`).join('');
  $('new-voice').hidden=state.me?.role!=='owner';
}
$('voice-list').addEventListener('click',async(e)=>{
  const del=e.target.closest('[data-delete-voice]');
  if(del){const slug=del.dataset.deleteVoice;if(!confirm('Delete this voice room?'))return;try{await api('/api/voice/channels/'+encodeURIComponent(slug),{method:'DELETE'});if(voice.room===slug)leaveVoice();await refreshVoiceRooms()}catch(err){showToast(err.message)}return}
  const join=e.target.closest('[data-join-voice]');if(join)joinVoice(join.dataset.joinVoice);
});
$('new-voice').addEventListener('click',async()=>{const name=prompt('New voice room name:');if(!name?.trim())return;try{await api('/api/voice/channels',{method:'POST',body:JSON.stringify({name:name.trim()})});await refreshVoiceRooms()}catch(e){showToast(e.message)}});

function voiceUser(id){return voice.people.get(Number(id))||state.members.find(u=>u.id===Number(id))||{id,username:'user'+id,display_name:'user '+id}}
function updateVoiceUI(){
  const connected=!!voice.ws;
  $('voice-dock').hidden=!connected;
  $('voice-stage').hidden=!(connected&&voice.stageOpen);
  $('app-screen').classList.toggle('voice-focused',connected&&voice.stageOpen);
  document.querySelector('.chat-main').classList.toggle('voice-focused',connected&&voice.stageOpen);
  const sounds=$('voice-sounds');sounds.innerHTML=icon(voiceSoundsEnabled?'volume':'volume-off');
  sounds.title=voiceSoundsEnabled?'Turn off join/leave sounds':'Turn on join/leave sounds';
  sounds.setAttribute('aria-label',sounds.title);
  sounds.setAttribute('aria-pressed',String(voiceSoundsEnabled));
  if(!connected){renderVoiceRooms();return}
  const channel=voice.rooms.find(c=>c.slug===voice.room);
  const label=channel?.name||voice.room;
  $('voice-roster-count').textContent=voice.people.size+' '+(voice.people.size===1?'PERSON':'PEOPLE');
  $('voice-roster-list').innerHTML=[...voice.people.values()].map(u=>`<div class="voice-roster-user">${avatar(u)}<div><strong>${escapeHtml(u.display_name||u.username)}</strong><small>${u.sharing?'● LIVE STREAM':u.muted?'Microphone muted':'In voice channel'}</small></div>${icon(u.muted?'mic-off':'mic')}</div>`).join('');
  $('voice-dock-room').textContent=label;
  $('voice-stage-title').textContent=label;
  $('voice-dock-count').textContent=voice.people.size+' online';
  $('voice-stage-status').textContent=voice.connected?(voice.sharing?'Screen sharing live':'Connected'):'Connecting…';
  $('voice-call-footer-count').textContent=voice.people.size+' '+(voice.people.size===1?'member':'members')+' in voice';
  $('voice-mute').textContent=voice.muted?'Unmute':'Mute';
  $('voice-mute').classList.toggle('active',voice.muted);
  $('voice-share').textContent=voice.sharing?'Stop sharing':'Share screen';
  $('voice-share').classList.toggle('active',voice.sharing);
  $('voice-show').textContent=voice.stageOpen?'Hide call':'Call view';
  $('voice-show').classList.toggle('active',voice.stageOpen);
  const mic=$('voice-call-mute');mic.innerHTML=icon(voice.muted?'mic-off':'mic')+'<span>'+(voice.muted?'Unmute':'Mute')+'</span>';
  mic.classList.toggle('is-muted',voice.muted);mic.setAttribute('aria-pressed',String(voice.muted));
  const share=$('voice-call-share');share.innerHTML=icon('monitor-up')+'<span>'+(voice.sharing?'Stop stream':'Share screen')+'</span>';
  share.classList.toggle('is-sharing',voice.sharing);share.setAttribute('aria-pressed',String(voice.sharing));
  $('voice-stage-people').innerHTML=[...voice.people.values()].map(u=>`<div class="voice-member-tile ${u.muted?'is-muted':''} ${u.sharing?'is-streaming':''}" data-voice-member="${u.id}">
    <div class="voice-member-avatar">${avatar(u,'voice-large-avatar')}${u.sharing?'<span class="voice-member-live">LIVE</span>':''}</div>
    <strong>${escapeHtml(u.display_name||u.username)}${u.id===state.me.id?' <small>(you)</small>':''}</strong>
    <div class="voice-member-status">${icon(u.muted?'mic-off':'mic')}<span>${u.muted?'Muted':'In voice'}</span></div>
  </div>`).join('');
  renderVoiceStreams();renderVoiceRooms();
}
function renderVoiceStreams(){const root=$('voice-stream-grid');if(!voice.stageOpen)return;
  const live=[...voice.people.values()].filter(u=>u.sharing);
  if(!live.length){root.replaceChildren();return}
  const keep=new Set(live.map(u=>String(u.id)));
  for(const tile of [...root.querySelectorAll('[data-voice-stream]')]){
    if(!keep.has(tile.dataset.voiceStream))tile.remove();
  }
  for(const u of live){const isSelf=u.id===state.me.id;
    const track=isSelf?voice.screen:voice.peers.get(u.id)?.videoTrack;
    let tile=root.querySelector(`[data-voice-stream="${u.id}"]`);
    if(!tile){tile=document.createElement('div');tile.className='voice-stream';tile.dataset.voiceStream=String(u.id);
      const video=document.createElement('video');video.autoplay=true;video.playsInline=true;video.muted=true;
      const label=document.createElement('label');tile.append(video,label);root.append(tile)
    }
    const video=tile.querySelector('video');
    if(track&&video.dataset.trackId!==track.id){video.srcObject=new MediaStream([track]);video.dataset.trackId=track.id;video.play().catch(()=>{})}
    tile.querySelector('label').textContent=(u.display_name||u.username)+(isSelf?' · your screen':' · live stream');
  }
}
function closeVoicePeer(id){const p=voice.peers.get(Number(id));if(!p)return;
  voice.peers.delete(Number(id));try{p.pc.ontrack=null;p.pc.onicecandidate=null;p.pc.close()}catch{}
  p.audioEl?.remove();
}
function cleanupVoiceMedia(){if(voice.screen){const t=voice.screen;voice.screen=null;t.onended=null;t.stop()}
  if(voice.mic){for(const t of voice.mic.getTracks())t.stop();voice.mic=null}
  for(const id of [...voice.peers.keys()])closeVoicePeer(id);
}
function leaveVoice(){const wasConnected=voice.connected;voice.epoch++;const ws=voice.ws;voice.ws=null;voice.room=null;voice.people.clear();voice.muted=true;voice.sharing=false;voice.stageOpen=false;voice.connected=false;
  if(ws){ws.onclose=null;ws.close()}
  cleanupVoiceMedia();$('voice-stream-grid').replaceChildren();updateVoiceUI();refreshVoiceRooms();
  if(wasConnected)playVoiceCue('leave');
}
async function joinVoice(room){
  if(voice.room===room&&voice.ws){setVoiceFocus(true);return}
  primeVoiceAudio();leaveVoice();const epoch=voice.epoch;voice.room=room;voice.stageOpen=true;voice.connected=false;voice.muted=true;
  if(!navigator.mediaDevices||!window.RTCPeerConnection){showToast('Voice calls need HTTPS and a browser with WebRTC.');leaveVoice();return}
  try{voice.iceServers=(await api('/api/voice/ice-config')).iceServers||[]}
  catch{voice.iceServers=[{urls:'stun:stun.l.google.com:19302'}]}
  if(voice.epoch!==epoch||voice.room!==room)return;
  try{voice.mic=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});voice.muted=false}
  catch(e){voice.mic=null;voice.muted=true;showToast('Microphone unavailable. Joined in listen-only mode.');}
  if(voice.epoch!==epoch||voice.room!==room){cleanupVoiceMedia();return}
  const proto=location.protocol==='https:'?'wss':'ws';const ws=new WebSocket(`${proto}://${location.host}/ws/voice?room=${encodeURIComponent(room)}`);
  voice.ws=ws;toggleSidebar(false);updateVoiceUI();
  ws.onopen=()=>{if(voice.ws!==ws)return;voiceStatus()};
  ws.onmessage=async(event)=>{if(voice.ws!==ws)return;let data;try{data=JSON.parse(event.data)}catch{return}
    try{await handleVoicePacket(data)}catch(err){console.warn('Voice signaling:',err)}};
  ws.onclose=(event)=>{if(voice.ws!==ws)return;const reason=event.reason||'Connection closed';leaveVoice();showToast('Voice disconnected: '+reason)};
  ws.onerror=()=>{if(voice.ws===ws)showToast('Could not connect to the voice room.')};
}
async function createVoicePeer(id){id=Number(id);const old=voice.peers.get(id);if(old)return old;
  const pc=new RTCPeerConnection({iceServers:voice.iceServers});
  const a=pc.addTransceiver('audio',{direction:'sendrecv'});
  const v=pc.addTransceiver('video',{direction:'sendrecv'});
  const obj={pc,audioSender:a.sender,videoSender:v.sender,audioTrack:null,videoTrack:null,pending:[],remoteReady:false,audioEl:null};
  voice.peers.set(id,obj);
  pc.onicecandidate=e=>{if(e.candidate)voiceSignal('ice',id,e.candidate.toJSON())};
  pc.ontrack=e=>{
    if(e.track.kind==='audio'){
      obj.audioTrack=e.track;
      if(!obj.audioEl){obj.audioEl=document.createElement('audio');obj.audioEl.autoplay=true;obj.audioEl.style.display='none';$('voice-audio').appendChild(obj.audioEl)}
      obj.audioEl.srcObject=new MediaStream([e.track]);obj.audioEl.play().catch(()=>{});
    }else{obj.videoTrack=e.track;e.track.onunmute=()=>{if(voice.people.get(id)?.sharing)renderVoiceStreams()};renderVoiceStreams()}
  };
  pc.onconnectionstatechange=()=>{if(pc.connectionState==='failed'){showToast('Peer connection failed. A TURN server may be needed.')}};
  try{await obj.audioSender.replaceTrack(voice.mic?.getAudioTracks()[0]||null);await obj.videoSender.replaceTrack(voice.screen||null)}catch(err){console.warn('Track setup:',err)}
  return obj;
}
async function flushVoiceIce(p){for(const item of p.pending.splice(0)){try{await p.pc.addIceCandidate(item)}catch(err){console.warn('ICE candidate:',err)}}}
async function offerVoicePeer(id){if(!voice.ws||id===state.me.id)return;
  const obj=await createVoicePeer(id);if(!voice.ws)return;
  const desc=await obj.pc.createOffer();await obj.pc.setLocalDescription(desc);
  voiceSignal('offer',id,{type:'offer',sdp:obj.pc.localDescription.sdp});
}
async function handleVoicePacket(data){
  if(data.type==='voice-roster'){
    voice.people=new Map(data.members.map(u=>[u.id,u]));voice.connected=true;playVoiceCue('join');updateVoiceUI();
    // Only the newcomer makes offers. Existing members only answer, avoiding glare.
    for(const u of data.members){if(u.id!==state.me.id)await offerVoicePeer(u.id)}
  }else if(data.type==='voice-joined'){
    voice.people.set(data.user.id,data.user);playVoiceCue('join');updateVoiceUI();
  }else if(data.type==='voice-left'){
    voice.people.delete(data.user_id);closeVoicePeer(data.user_id);playVoiceCue('leave');updateVoiceUI();
  }else if(data.type==='voice-status'){
    const user=voice.people.get(data.user_id);if(user){user.muted=data.muted;user.sharing=data.sharing;updateVoiceUI()}
  }else if(data.type==='voice-signal'){
    const id=data.from;const p=await createVoicePeer(id);const pc=p.pc;
    if(data.signal==='offer'){
      await pc.setRemoteDescription(new RTCSessionDescription(data.data));p.remoteReady=true;await flushVoiceIce(p);
      const answer=await pc.createAnswer();await pc.setLocalDescription(answer);
      voiceSignal('answer',id,{type:'answer',sdp:pc.localDescription.sdp});
    }else if(data.signal==='answer'){
      if(pc.signalingState!=='have-local-offer')return;
      await pc.setRemoteDescription(new RTCSessionDescription(data.data));p.remoteReady=true;await flushVoiceIce(p);
    }else if(data.signal==='ice'){
      if(!p.remoteReady)p.pending.push(data.data);
      else await pc.addIceCandidate(data.data).catch(()=>{});
    }
  }
}
$('voice-mute').addEventListener('click',async()=>{
  if(!voice.ws)return;
  if(!voice.mic){try{voice.mic=await navigator.mediaDevices.getUserMedia({audio:true});
    for(const p of voice.peers.values())await p.audioSender.replaceTrack(voice.mic.getAudioTracks()[0]);
  }catch(e){showToast('Microphone permission denied.');return}}
  voice.muted=!voice.muted;for(const track of voice.mic.getAudioTracks())track.enabled=!voice.muted;
  const me=voice.people.get(state.me.id);if(me)me.muted=voice.muted;voiceStatus();updateVoiceUI();
});
async function stopVoiceShare(){if(!voice.sharing)return;
  const track=voice.screen;voice.screen=null;voice.sharing=false;
  if(track){track.onended=null;track.stop()}
  await Promise.all([...voice.peers.values()].map(p=>p.videoSender.replaceTrack(null).catch(()=>{})));
  const me=voice.people.get(state.me.id);if(me)me.sharing=false;voiceStatus();updateVoiceUI();
}
$('voice-share').addEventListener('click',async()=>{
  if(!voice.ws)return;
  if(voice.sharing){await stopVoiceShare();return}
  if(!navigator.mediaDevices.getDisplayMedia){showToast('Screen sharing is not supported by this browser.');return}
  try{const stream=await navigator.mediaDevices.getDisplayMedia({video:{frameRate:15},audio:false});
    const track=stream.getVideoTracks()[0];if(!voice.ws){track.stop();return}
    voice.screen=track;voice.sharing=true;track.onended=()=>stopVoiceShare();
    await Promise.all([...voice.peers.values()].map(p=>p.videoSender.replaceTrack(track)));
    const me=voice.people.get(state.me.id);if(me)me.sharing=true;
    voice.stageOpen=true;voiceStatus();updateVoiceUI();
  }catch(err){if(err.name!=='NotAllowedError')showToast('Screen sharing failed: '+err.message)}
});
$('voice-call-mute').addEventListener('click',()=>$('voice-mute').click());
$('voice-call-share').addEventListener('click',()=>$('voice-share').click());
$('voice-call-leave').addEventListener('click',leaveVoice);
$('voice-back-chat').addEventListener('click',()=>setVoiceFocus(false));
$('voice-sounds').addEventListener('click',()=>{voiceSoundsEnabled=!voiceSoundsEnabled;
  try{localStorage.setItem('mrh-voice-sounds',voiceSoundsEnabled?'on':'off')}catch{}
  if(voiceSoundsEnabled)primeVoiceAudio();updateVoiceUI();
});
$('voice-leave').addEventListener('click',leaveVoice);
$('voice-show').addEventListener('click',()=>setVoiceFocus(!voice.stageOpen));
$('voice-stage-close').addEventListener('click',()=>setVoiceFocus(false));
window.addEventListener('beforeunload',()=>{cleanupVoiceMedia();voice.ws?.close()});
// Voice is independent of the text-chat websocket and stays connected across channels.
for(const routeName of ['openChannel','openDm','openPosts']){
  const original=({openChannel,openDm,openPosts})[routeName];
  if(routeName==='openChannel')openChannel=async function(...args){leaveVoiceFocus();return original(...args)};
  if(routeName==='openDm')openDm=async function(...args){leaveVoiceFocus();return original(...args)};
  if(routeName==='openPosts')openPosts=async function(...args){leaveVoiceFocus();return original(...args)};
}
const originalEnterApp=enterApp;
enterApp=async function(){await originalEnterApp();await refreshVoiceRooms()};
const originalLogoutLocal=logoutLocal;
logoutLocal=function(){leaveVoice();originalLogoutLocal()};
setInterval(()=>{if(state.me)refreshVoiceRooms()},20000);
