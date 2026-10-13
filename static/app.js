'use strict';

const $ = (id) => document.getElementById(id);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"></use></svg>`;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (x) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const state = {me:null,channels:[],members:[],dms:[],online:new Set(),mode:'channel',room:'general',peer:null,ws:null,msgIds:new Set(),msgById:new Map(),replyTo:null,earliest:null,hasOlder:false,loading:false,lastTyping:0,typingTimeout:null,toastTimer:null,sidebarOpen:innerWidth>630,showMembers:false,unreadDms:new Set(),upload:null,authMode:'login',settingsTab:'account',notifications:[],openedPost:null,themeDraft:null};
const palette = ['#7967e7','#408fad','#c77e9e','#b48b55','#54a18a','#a37bcc','#a47b64'];
const colorFor = (value) => {let h=0;for(const c of String(value))h=((h<<5)-h+c.charCodeAt(0))|0;return palette[Math.abs(h)%palette.length]};
function avatar(user,cls=''){if(!user)return `<div class="avatar ${cls}">?</div>`;
  const name = user.display_name||user.username||'?';
  if(user.avatar && /^\/uploads\/[0-9]+_[a-f0-9]{24}\.png$/.test(user.avatar)) return `<img class="avatar ${cls}" src="${user.avatar}" alt="${escapeHtml(name)}" loading="lazy">`;
  return `<div class="avatar ${cls}" style="background:${colorFor(user.username)}">${escapeHtml(name.slice(0,1).toUpperCase())}</div>`;
}
const DEFAULT_THEME={accent:'#8a7eff',sidebar:'#151620',chat:'#1a1b26',text:'#e8e9f2'};
function applyTheme(theme){const cfg={...DEFAULT_THEME,...(theme||{})};for(const [name,value] of Object.entries(cfg)){if(/^#[a-fA-F0-9]{6}$/.test(value))document.documentElement.style.setProperty('--theme-'+name,value)}document.documentElement.dataset.customTheme='1';}
function pingify(text){return escapeHtml(text).replace(/(^|[^\w@])@([A-Za-z0-9_]{3,24})\b/g,(match,pre,name)=>pre+'<span class="ping-name">@'+name+'</span>')}
const roleBadge = (user) => user.role==='owner' ? '<span class="role-badge" title="Verified community owner">✦ OWNER</span>' : '';
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
  state.mode='channel';state.room=slug;state.peer=null;$('composer-area').hidden=false;clearSelectedFile();clearReply();
  $('header-symbol').innerHTML=icon('hash');$('room-name').textContent=ch.name;$('room-topic').textContent=ch.topic;$('message-input').placeholder='Message #'+ch.name;renderChannels();renderDms();await loadConversation();connectWs();toggleSidebar(false)
}
async function openDm(id){const peer=peerById(id);if(!peer)return;
  state.mode='dm';state.peer=id;$('composer-area').hidden=false;clearSelectedFile();clearReply();state.unreadDms.delete(id);if(!state.dms.some(u=>u.id===id))state.dms.unshift(peer);
  $('header-symbol').innerHTML=icon('message');$('room-name').textContent=peer.display_name;$('room-topic').textContent='Direct messages · @'+peer.username;$('message-input').placeholder='Message @'+peer.username;renderChannels();renderDms();await loadConversation();connectWs();toggleSidebar(false)
}
function urlForMessages(){return state.mode==='channel'?`/api/channels/${encodeURIComponent(state.room)}/messages`:`/api/dms/${state.peer}/messages`}
async function loadConversation(){
  $('messages').replaceChildren();$('older-holder').replaceChildren();$('typing-indicator').textContent='';
  state.msgIds=new Set();state.msgById=new Map();state.earliest=null;state.hasOlder=false;state.loading=true;
  const current=state.mode==='channel'?`channel:${state.room}`:`dm:${state.peer}`;
  $('messages').innerHTML=welcomeMarkup();
  try{const list=await api(urlForMessages());if((state.mode==='channel'?`channel:${state.room}`:`dm:${state.peer}`)!==current)return;
    state.hasOlder=list.length===80;if(list.length)state.earliest=list[0].id;
    appendMessages(list,true);updateOlder();scrollBottom();
  }catch(err){showToast(err.message)}finally{state.loading=false}
}
function welcomeMarkup(){const isChannel=state.mode==='channel';const n=isChannel?('#'+channelBySlug(state.room)?.name):('@'+peerById(state.peer)?.username);
  return `<div class="chat-welcome"><div class="chat-welcome-icon">${icon(isChannel?'hash':'message')}</div><h2>${isChannel?'Welcome to '+escapeHtml(n)+'!':'Your conversation with '+escapeHtml(n)}</h2><p>${isChannel?'This is the beginning of the <strong>'+escapeHtml(n)+'</strong> channel. Pull up a chair and join the conversation.':'This is the start of your private conversation. Only you two can see these messages.'}</p><div class="welcome-line"><span>THE CONVERSATION STARTS HERE</span></div></div>`;
}
function formatTime(s){const t=new Date(s.endsWith('Z')||s.includes('+')?s:s.replace(' ','T')+'Z');return Number.isNaN(t.getTime())?'':t.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}
function replyExcerpt(reply){return String(reply?.preview||reply?.file||'(attachment)').slice(0,180)}
function replyReference(reply){return reply?`<button class="message-reply" type="button" data-jump-message="${reply.id}" title="Jump to referenced message"><span class="reply-connector">↳</span><strong>@${escapeHtml(reply.username||'unknown')}</strong><span class="reply-excerpt">${escapeHtml(replyExcerpt(reply))}</span></button>`:''}
function messageHtml(m){return `<article class="message" data-msg="${m.id}">${avatar(m.user,'message-avatar')}<div class="message-body">${replyReference(m.reply)}<div class="message-header"><button class="message-name" data-user="${m.user.id}">${escapeHtml(m.user.display_name)}</button>${roleBadge(m.user)}<span class="message-time">${formatTime(m.created_at)}</span><button type="button" class="reply-action" data-reply-message="${m.id}" aria-label="Reply to ${escapeHtml(m.user.username)}">↩ Reply</button></div><div class="message-text">${pingify(m.body)}</div>${attachmentMarkup(m.attachment)}</div></article>`}
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
  const query=state.mode==='channel'?`channel=${encodeURIComponent(state.room)}`:`dm=${state.peer}`;
  const proto=location.protocol==='https:'?'wss':'ws';const ws=new WebSocket(`${proto}://${location.host}/ws?${query}`);state.ws=ws;
  ws.onmessage=(event)=>{if(state.ws!==ws)return;let packet;try{packet=JSON.parse(event.data)}catch{return}
    if(packet.type==='message'){
      // Ignore messages for a different conversation during navigation.
      if(state.mode==='channel'&&packet.message.channel_slug!==state.room)return;
      if(state.mode==='dm'&&packet.message.channel_slug!==null)return;
      const scroll=$('chat-scroller');const near=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<175;
      appendMessages([packet.message]);if(near||packet.message.user.id===state.me.id)scrollBottom();
      if(state.mode==='dm'&&packet.message.user.id!==state.me.id)refreshDms();
    }else if(packet.type==='channel-deleted'){showToast('Channel was deleted.');state.channels=state.channels.filter(c=>c.slug!==packet.slug);openChannel('general')}else if(packet.type==='presence'){state.online=new Set(packet.online);renderMembers();if(packet.online.some(id=>id!==state.me.id&&!state.members.some(u=>u.id===id)))refreshMembers()}
    else if(packet.type==='ping-alert'){loadNotifications();showToast('Someone mentioned you.')}
    else if(packet.type==='dm-alert'){state.unreadDms.add(packet.from_user_id);refreshDms();showToast('New private message.')}
    else if(packet.type==='typing'&&packet.user_id!==state.me.id){$('typing-indicator').textContent=packet.name+' is typing…';clearTimeout(state.typingTimeout);state.typingTimeout=setTimeout(()=>$('typing-indicator').textContent='',1500)}
  };
  ws.onclose=(event)=>{if(state.ws!==ws)return;state.ws=null;if(event.code===1008){logoutLocal();showToast('Session ended or account banned.');return}setTimeout(()=>{if(state.me&&!state.ws)connectWs()},2800)};
}
async function refreshDms(){try{state.dms=await api('/api/dms');renderDms()}catch{}}
$('message-form').addEventListener('submit',async(e)=>{e.preventDefault();const input=$('message-input'),body=input.value.trim();if((!body&&!state.upload)||!state.me)return;const button=$('send-button');button.disabled=true;
  const destination=urlForMessages();const attachment=state.upload;const reply=state.replyTo;if(!body&&!attachment){button.disabled=false;return}input.value='';input.style.height='auto';
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
async function openNotifications(){try{await loadNotifications();const records=state.notifications;openModal(`<div class="compact-modal notification-panel">${closeButton}<h2>Pings & mentions</h2><p class="muted-p">You'll be notified when someone mentions @${escapeHtml(state.me.username)}.</p><div class="notification-list">${records.length?records.map(n=>`<button class="notification-item ${n.read?'':'unread'}" data-ping="${n.id}"><strong>@${escapeHtml(n.sender)}</strong><span>${escapeHtml(n.preview)}</span><small>${n.kind==='post'?'Community posts':n.kind==='channel'?'#'+escapeHtml(n.context):'Direct messages'} · ${formatTime(n.created_at)}</small></button>`).join(''):'<p class="muted-p">No mentions yet.</p>'}</div></div>`);$('modal-content').querySelectorAll('[data-ping]').forEach(b=>b.addEventListener('click',()=>jumpToPing(records.find(n=>n.id===Number(b.dataset.ping)))));await api('/api/notifications/read',{method:'POST'});await loadNotifications()}catch(err){showToast(err.message)}}
async function jumpToPing(n){if(!n)return;closeModal();if(n.kind==='post'){await openPosts();const target=document.querySelector(`[data-post="${n.target_id}"]`);if(target){target.scrollIntoView({behavior:'smooth',block:'center'});if(n.context==='comments')await toggleComments(n.target_id)}}else if(n.kind==='channel'){await openChannel(n.context);const target=document.querySelector(`[data-msg="${n.target_id}"]`);if(target)target.scrollIntoView({behavior:'smooth',block:'center'})}else if(n.kind==='dm'){const sender=state.members.find(m=>m.username.toLowerCase()===n.sender.toLowerCase());if(sender)await openDm(sender.id)}}
setInterval(()=>{if(state.me)loadNotifications()},30000);
$('modal-backdrop').addEventListener('mousedown',(e)=>{if(e.target===$('modal-backdrop'))closeModal()});
document.addEventListener('keydown',(e)=>{if(e.key==='Escape'&&!$('modal-backdrop').hidden)closeModal()});
const closeButton='<button class="modal-close" type="button" aria-label="Close dialog">'+icon('close')+'</button>';
function showUserProfile(id){const u=peerById(id)|| (state.me?.id===id?state.me:null);if(!u)return;
  openModal(`<div class="compact-modal" style="padding:0;max-width:440px">${closeButton}<div class="profile-banner"></div>${avatar(u,'user-modal-avatar')}<div class="public-profile-body"><h2>${escapeHtml(u.display_name)} ${roleBadge(u)}</h2><div class="handle">@${escapeHtml(u.username)}</div><div class="settings-label" style="padding:0">ABOUT ME</div><p class="about">${escapeHtml(u.bio||'Just hanging out on Mr H Media.')}</p>${u.id!==state.me.id?'<button class="save-btn" id="profile-message">'+icon('message')+' &nbsp; Send message</button>':'<button class="save-btn" id="profile-edit">Edit my profile</button>'}</div></div>`);
  if(state.me.role==='owner'&&u.role!=='owner'){$('modal-content').querySelector('.public-profile-body').insertAdjacentHTML('beforeend',`<button class="ban-profile-btn" id="ban-profile">Ban @${escapeHtml(u.username)}</button>`);$('ban-profile').addEventListener('click',()=>banFromProfile(u))}
  $('profile-message')?.addEventListener('click',()=>{closeModal();openDm(u.id)});$('profile-edit')?.addEventListener('click',()=>openSettings('profile'));
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

function attachmentMarkup(a){if(!a)return '';const url='/api/attachments/'+encodeURIComponent(a.id);const name=escapeHtml(a.name);const size=(a.size/1024).toFixed(1)+' KB';const image=a.mime==='image/png';return `<div class="attachment-card">${image?`<a href="${url}" target="_blank" rel="noopener"><img class="attachment-image" src="${url}" loading="lazy" alt="${name}"></a>`:''}<a class="attachment-link" href="${url}" ${image?'':'download'}>${icon('paperclip')}<span>${name}</span><small>${size}</small></a></div>`}
function clearSelectedFile(){state.upload=null;$('chat-file').value='';$('selected-file').hidden=true;$('selected-file').textContent=''}
$('add-attachment').addEventListener('click',()=>$('chat-file').click());
$('chat-file').addEventListener('change',e=>{const file=e.target.files?.[0];if(!file)return;if(file.size>8*1024*1024){showToast('Maximum file size is 8 MB.');clearSelectedFile();return}state.upload=file;$('selected-file').hidden=false;$('selected-file').innerHTML='Attached: <strong>'+escapeHtml(file.name)+'</strong> <button type="button" id="clear-chat-file" aria-label="Remove attachment">×</button>';$('clear-chat-file').addEventListener('click',clearSelectedFile);});

function postMarkup(p){return `<article class="post-card" data-post="${p.id}"><div class="post-author">${avatar(p.user)}<div><strong>${escapeHtml(p.user.display_name)} ${roleBadge(p.user)}</strong><small>@${escapeHtml(p.user.username)} · ${formatTime(p.created_at)}</small></div>${p.user.id===state.me.id||state.me.role==='owner'?`<button type="button" class="post-delete" data-delete-post="${p.id}" title="Delete post">Delete</button>`:''}</div><h3>${escapeHtml(p.title)}</h3><p>${pingify(p.body)}</p>${attachmentMarkup(p.attachment)}<div class="post-actions"><button type="button" data-toggle-comments="${p.id}">${icon('message')} <span>${p.comment_count||0}</span> comments</button></div><div class="comment-section" data-comment-section="${p.id}" hidden></div></article>`}
async function openPosts(){state.mode='posts';state.peer=null;$('composer-area').hidden=true;clearSelectedFile();clearReply();if(state.ws){state.ws.onclose=null;state.ws.close();state.ws=null}renderChannels();renderDms();$('header-symbol').innerHTML=icon('image');$('room-name').textContent='Community posts';$('room-topic').textContent='Updates, creations and announcements';$('typing-indicator').textContent='';$('older-holder').replaceChildren();$('messages').innerHTML='<p class="posts-loading">Loading posts…</p>';toggleSidebar(false);await refreshPosts()}
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
