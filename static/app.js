'use strict';

const $ = (id) => document.getElementById(id);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"></use></svg>`;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (x) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const state = {me:null,channels:[],members:[],dms:[],online:new Set(),mode:'channel',room:'general',peer:null,ws:null,msgIds:new Set(),earliest:null,hasOlder:false,loading:false,lastTyping:0,typingTimeout:null,toastTimer:null,sidebarOpen:innerWidth>630,showMembers:false,unreadDms:new Set(),authMode:'login',settingsTab:'account'};
const palette = ['#7967e7','#408fad','#c77e9e','#b48b55','#54a18a','#a37bcc','#a47b64'];
const colorFor = (value) => {let h=0;for(const c of String(value))h=((h<<5)-h+c.charCodeAt(0))|0;return palette[Math.abs(h)%palette.length]};
function avatar(user,cls=''){if(!user)return `<div class="avatar ${cls}">?</div>`;
  const name = user.display_name||user.username||'?';
  if(user.avatar && /^\/uploads\/[0-9]+_[a-f0-9]{24}\.png$/.test(user.avatar)) return `<img class="avatar ${cls}" src="${user.avatar}" alt="${escapeHtml(name)}" loading="lazy">`;
  return `<div class="avatar ${cls}" style="background:${colorFor(user.username)}">${escapeHtml(name.slice(0,1).toUpperCase())}</div>`;
}
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
  $('auth-screen').hidden=true;$('app-screen').hidden=false;
  try{
    const [channels,members,dms]=await Promise.all([api('/api/channels'),api('/api/members'),api('/api/dms')]);
    state.channels=channels;state.members=members;state.dms=dms;
    $('new-channel').hidden=state.me.role!=='owner';updateDock();renderChannels();renderDms();renderMembers();
    await openChannel('general');
    if(innerWidth<=630)toggleSidebar(false);
  }catch(e){showToast(e.message)}
}
function updateDock(){if(!state.me)return;$('dock-avatar').innerHTML=avatar(state.me,'dock-avatar-img');$('dock-name').textContent=state.me.display_name;$('dock-handle').textContent='@'+state.me.username}
function renderChannels(){
  $('channel-list').innerHTML=state.channels.map(c=>`<button class="channel-item ${state.mode==='channel'&&state.room===c.slug?'active':''}" data-channel="${escapeHtml(c.slug)}">${icon('hash')}<span>${escapeHtml(c.name)}</span></button>`).join('');
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
  state.mode='channel';state.room=slug;state.peer=null;
  $('header-symbol').innerHTML=icon('hash');$('room-name').textContent=ch.name;$('room-topic').textContent=ch.topic;$('message-input').placeholder='Message #'+ch.name;renderChannels();renderDms();await loadConversation();connectWs();toggleSidebar(false)
}
async function openDm(id){const peer=peerById(id);if(!peer)return;
  state.mode='dm';state.peer=id;state.unreadDms.delete(id);if(!state.dms.some(u=>u.id===id))state.dms.unshift(peer);
  $('header-symbol').innerHTML=icon('message');$('room-name').textContent=peer.display_name;$('room-topic').textContent='Direct messages · @'+peer.username;$('message-input').placeholder='Message @'+peer.username;renderChannels();renderDms();await loadConversation();connectWs();toggleSidebar(false)
}
function urlForMessages(){return state.mode==='channel'?`/api/channels/${encodeURIComponent(state.room)}/messages`:`/api/dms/${state.peer}/messages`}
async function loadConversation(){
  $('messages').replaceChildren();$('older-holder').replaceChildren();$('typing-indicator').textContent='';
  state.msgIds=new Set();state.earliest=null;state.hasOlder=false;state.loading=true;
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
function messageHtml(m){return `<article class="message" data-msg="${m.id}">${avatar(m.user,'message-avatar')}<div class="message-body"><div class="message-header"><button class="message-name" data-user="${m.user.id}">${escapeHtml(m.user.display_name)}</button>${roleBadge(m.user)}<span class="message-time">${formatTime(m.created_at)}</span></div><div class="message-text">${escapeHtml(m.body)}</div></div></article>`}
function appendMessages(list,initial=false){const root=$('messages');
  for(const msg of list){if(state.msgIds.has(msg.id))continue;state.msgIds.add(msg.id);root.insertAdjacentHTML('beforeend',messageHtml(msg))}
  if(!initial){const scroll=$('chat-scroller');const nearBottom=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<165;if(nearBottom||list.some(m=>m.user.id===state.me.id))scrollBottom()}
}
function updateOlder(){$('older-holder').innerHTML=state.hasOlder?'<button id="load-older" class="load-older">Load older messages ↑</button>':''}
$('older-holder').addEventListener('click',async(e)=>{if(e.target.id!=='load-older'||state.loading||!state.earliest)return;
  state.loading=true;const btn=e.target;btn.disabled=true;btn.textContent='Loading…';const scroller=$('chat-scroller');const prevHeight=scroller.scrollHeight;
  try{const list=await api(urlForMessages()+`?before=${state.earliest}`);state.hasOlder=list.length===80;
    if(list.length)state.earliest=list[0].id;
    // insert older messages after welcome, before existing messages
    const root=$('messages');const firstMessage=root.querySelector('.message');
    for(const m of list){if(state.msgIds.has(m.id))continue;state.msgIds.add(m.id);if(firstMessage)firstMessage.insertAdjacentHTML('beforebegin',messageHtml(m));else root.insertAdjacentHTML('beforeend',messageHtml(m))}
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
    }else if(packet.type==='presence'){state.online=new Set(packet.online);renderMembers();if(packet.online.some(id=>id!==state.me.id&&!state.members.some(u=>u.id===id)))refreshMembers()}
    else if(packet.type==='dm-alert'){state.unreadDms.add(packet.from_user_id);refreshDms();showToast('New private message.')}
    else if(packet.type==='typing'&&packet.user_id!==state.me.id){$('typing-indicator').textContent=packet.name+' is typing…';clearTimeout(state.typingTimeout);state.typingTimeout=setTimeout(()=>$('typing-indicator').textContent='',1500)}
  };
  ws.onclose=()=>{if(state.ws===ws){state.ws=null;setTimeout(()=>{if(state.me&&!state.ws)connectWs()},2800)}};
}
async function refreshDms(){try{state.dms=await api('/api/dms');renderDms()}catch{}}
$('message-form').addEventListener('submit',async(e)=>{e.preventDefault();const input=$('message-input'),body=input.value.trim();if(!body||!state.me)return;const button=$('send-button');button.disabled=true;
  const destination=urlForMessages();input.value='';input.style.height='auto';
  try{const msg=await api(destination,{method:'POST',body:JSON.stringify({body})});if(destination===urlForMessages()&&!state.msgIds.has(msg.id)){appendMessages([msg]);scrollBottom()}if(state.mode==='dm')refreshDms()}
  catch(err){input.value=body;showToast(err.message)}finally{button.disabled=false;input.focus()}
});
$('message-input').addEventListener('keydown',(e)=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('message-form').requestSubmit()}});
$('message-input').addEventListener('input',()=>{const el=$('message-input');el.style.height='auto';el.style.height=Math.min(el.scrollHeight,130)+'px';const now=Date.now();if(now-state.lastTyping>1500&&state.ws?.readyState===WebSocket.OPEN){state.ws.send(JSON.stringify({type:'typing'}));state.lastTyping=now}});
const emojis='😀 😎 😂 ❤️ 🔥 ✨ 👍 🎉 👀 💜 🙌 💀 🫶 🤝 🚀 😭 👋 🌟 🎮 💯 🥳 😁 🤔 ☕ 🎵 💬 ✳️'.split(' ');
$('emoji-popover').innerHTML=emojis.map(e=>`<button type="button">${e}</button>`).join('');
$('emoji-button').addEventListener('click',()=>{$('emoji-popover').hidden=!$('emoji-popover').hidden});
$('emoji-popover').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;const i=$('message-input');i.value+=b.textContent;i.focus();$('emoji-popover').hidden=true});
document.addEventListener('click',e=>{if(!$('emoji-button').contains(e.target)&&!$('emoji-popover').contains(e.target))$('emoji-popover').hidden=true});
$('channel-list').addEventListener('click',(e)=>{const btn=e.target.closest('[data-channel]');if(btn)openChannel(btn.dataset.channel)});
$('dm-list').addEventListener('click',(e)=>{const btn=e.target.closest('[data-dm]');if(btn)openDm(Number(btn.dataset.dm))});
function toggleSidebar(open){state.sidebarOpen=open;$('app-screen').classList.toggle('sidebar-open',open)}
$('mobile-menu').addEventListener('click',()=>toggleSidebar(!state.sidebarOpen));
$('rail-home').addEventListener('click',()=>{if(innerWidth<=630)toggleSidebar(true);else openChannel('general')});
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
$('modal-backdrop').addEventListener('mousedown',(e)=>{if(e.target===$('modal-backdrop'))closeModal()});
document.addEventListener('keydown',(e)=>{if(e.key==='Escape'&&!$('modal-backdrop').hidden)closeModal()});
const closeButton='<button class="modal-close" type="button" aria-label="Close dialog">'+icon('close')+'</button>';
function showUserProfile(id){const u=peerById(id)|| (state.me?.id===id?state.me:null);if(!u)return;
  openModal(`<div class="compact-modal" style="padding:0;max-width:440px">${closeButton}<div class="profile-banner"></div>${avatar(u,'user-modal-avatar')}<div class="public-profile-body"><h2>${escapeHtml(u.display_name)} ${roleBadge(u)}</h2><div class="handle">@${escapeHtml(u.username)}</div><div class="settings-label" style="padding:0">ABOUT ME</div><p class="about">${escapeHtml(u.bio||'Just hanging out on Mr H Media.')}</p>${u.id!==state.me.id?'<button class="save-btn" id="profile-message">'+icon('message')+' &nbsp; Send message</button>':'<button class="save-btn" id="profile-edit">Edit my profile</button>'}</div></div>`);
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
  openModal(`<div class="settings-layout"><aside class="settings-sidebar"><h4>Settings <span style="color:#998afa">✳</span></h4><div class="settings-label">USER SETTINGS</div><button type="button" class="settings-nav" data-tab="account">${icon('users')} My account</button><button type="button" class="settings-nav" data-tab="profile">${icon('edit')} Profile</button><button type="button" class="settings-nav" data-tab="security">${icon('lock')} Security</button><button type="button" class="settings-nav settings-logout" id="settings-logout">${icon('logout')} Log out</button></aside><div class="settings-body" id="settings-body">${closeButton}<div id="settings-main"></div></div></div>`);
  $('modal-content').querySelector('.modal-close').addEventListener('click',closeModal);
  $('modal-content').querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>renderSettings(b.dataset.tab)));
  $('settings-logout').addEventListener('click',logout);
  renderSettings(tab)
}
function renderSettings(tab){state.settingsTab=tab;$('modal-content').querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===tab));const u=state.me;let html='';
  if(tab==='account'){
    html=`<h2>My account</h2><p class="muted-p">the basics. all about you.</p><div class="profile-preview"><div class="profile-banner"></div><div class="profile-preview-bottom">${avatar(u,'profile-large-avatar')}<label for="avatar-file" class="avatar-upload" title="Change profile picture">${icon('edit')}</label><input type="file" id="avatar-file" accept="image/png,image/jpeg,image/webp,image/gif" hidden><strong>${escapeHtml(u.display_name)} ${roleBadge(u)}</strong><small>@${escapeHtml(u.username)}</small></div></div><form class="settings-form" id="profile-form"><label>DISPLAY NAME</label><input name="display_name" maxlength="40" value="${escapeHtml(u.display_name)}" required><label>USERNAME</label><input name="username" maxlength="24" value="${escapeHtml(u.username)}" required><p class="settings-help">3–24 characters. letters, numbers and underscores only.</p><label>ABOUT ME</label><textarea name="bio" maxlength="240" placeholder="Tell the community a little about yourself…">${escapeHtml(u.bio)}</textarea><div class="form-button-row"><button type="submit" class="save-btn">Save changes</button><span class="settings-message" id="profile-status"></span></div></form>`;
  }else if(tab==='profile'){
    html=`<h2>Your profile</h2><p class="muted-p">give your profile a little personality.</p><div class="profile-preview"><div class="profile-banner"></div><div class="profile-preview-bottom">${avatar(u,'profile-large-avatar')}<label for="avatar-file" class="avatar-upload" title="Change profile picture">${icon('edit')}</label><input type="file" id="avatar-file" accept="image/png,image/jpeg,image/webp,image/gif" hidden><strong>${escapeHtml(u.display_name)} ${roleBadge(u)}</strong><small>@${escapeHtml(u.username)}</small></div></div><form class="settings-form" id="profile-form"><label>DISPLAY NAME</label><input name="display_name" maxlength="40" value="${escapeHtml(u.display_name)}" required><label>USERNAME</label><input name="username" maxlength="24" value="${escapeHtml(u.username)}" required><label>ABOUT ME</label><textarea name="bio" maxlength="240" placeholder="What should people know about you?">${escapeHtml(u.bio)}</textarea><div class="form-button-row"><button type="submit" class="save-btn">Update profile</button><span class="settings-message" id="profile-status"></span></div></form>`;
  }else{
    html=`<h2>Password & security</h2><p class="muted-p">keep your account safe and yours.</p><div class="password-card">${icon('shield')}<span>Use a unique password with at least 8 characters. Changing it will log out other devices.</span></div><form class="settings-form" id="password-form"><label>CURRENT PASSWORD</label><input type="password" name="current_password" autocomplete="current-password" required><label>NEW PASSWORD</label><input type="password" name="new_password" minlength="8" maxlength="128" autocomplete="new-password" required><label>CONFIRM NEW PASSWORD</label><input type="password" name="confirm" minlength="8" maxlength="128" autocomplete="new-password" required><div class="form-button-row"><button type="submit" class="save-btn">Change password</button><span class="settings-message" id="password-status"></span></div></form>`;
  }
  $('settings-main').innerHTML=html;
  $('profile-form')?.addEventListener('submit',saveProfile);
  $('avatar-file')?.addEventListener('change',uploadAvatar);
  $('password-form')?.addEventListener('submit',savePassword);
}
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
$('rail-settings').addEventListener('click',()=>openSettings());$('dock-settings').addEventListener('click',()=>openSettings());
$('new-channel').addEventListener('click',()=>{
  openModal(`<div class="compact-modal">${closeButton}<h2>Create a channel</h2><p class="muted-p">Give your community a new place to talk.</p><form id="channel-create-form" class="modal-form"><label>CHANNEL NAME</label><input name="name" required maxlength="28" placeholder="music-chat"><label>TOPIC (OPTIONAL)</label><input name="topic" maxlength="120" placeholder="talk about everything music"><span class="settings-message error" id="create-error"></span><button type="submit" class="save-btn">Create channel</button></form></div>`);
  $('channel-create-form').addEventListener('submit',async(e)=>{e.preventDefault();const form=e.target;const data=Object.fromEntries(new FormData(form).entries());try{const channel=await api('/api/channels',{method:'POST',body:JSON.stringify(data)});state.channels.push(channel);closeModal();openChannel(channel.slug);showToast('Channel created.')}catch(err){$('create-error').textContent=err.message}})
});
async function boot(){try{state.me=await api('/api/me');await enterApp()}catch(e){state.me=null;$('auth-screen').hidden=false;$('app-screen').hidden=true}}
boot();
