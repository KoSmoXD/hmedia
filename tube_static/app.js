let videos = [];

const $ = s => document.querySelector(s);
const main = $('#main');
const nav = $('#navList');
const search = $('#searchInput');
const profiles = new Map();
const categories = ['All','Music','Learning','Science','Animation','Gaming','Classics','Shorts','Live'];
const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem('viewtube:'+key)) ?? fallback; } catch { return fallback; } };
const write = (key, value) => { try { localStorage.setItem('viewtube:'+key,JSON.stringify(value)); } catch {} };
const state = {page:'home',category:'All',query:'',video:null,channel:null,user:null,likes:[],saved:[],history:[],comments:{},light:read('light',false),collapsed:read('collapsed',false),authMode:'login',pendingUpload:false};

async function api(path,options={}){const response=await fetch(path,{credentials:'same-origin',...options});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||'Request failed.');return data;}
function applySession(data){state.user=data.user;state.likes=data.likes||[];state.saved=data.saved||[];state.history=data.history||[];$('#accountButton').textContent=state.user?(state.user.display_name||state.user.username):'Sign in';render();}
function showAccountPanel(panel){const dialog=$('#accountDialog');$('#guestPanel').hidden=panel!=='guest';$('#userPanel').hidden=panel!=='user';$('#uploadPanel').hidden=panel!=='upload';if(panel==='user')fillProfileForm();if(!dialog.open)dialog.showModal();}
function fillProfileForm(){
  const {username,display_name,bio,avatar_url}=state.user;
  const form=$('#profileForm');
  form.reset();
  form.elements.namedItem('display_name').value=display_name||'';
  form.elements.namedItem('bio').value=bio||'';
  $('#accountName').textContent='Signed in as @'+username;
  $('#viewProfile').href='#channel/'+encodeURIComponent(username);
  $('#removeAvatar').hidden=!avatar_url;
  $('#profileError').hidden=true;
}
async function saveProfile(remove=false){
  const form=$('#profileForm');
  const error=$('#profileError');
  const buttons=form.querySelectorAll('button');
  const data=new FormData(form);
  const avatar=form.elements.namedItem('avatar').files[0];
  if(!avatar||remove)data.delete('avatar');
  if(remove)data.set('remove_avatar','true');
  buttons.forEach(button=>button.disabled=true);
  error.hidden=true;
  try{
    const user=await api('/api/me/profile',{method:'PUT',body:data});
    const applyProfile=v=>v.channel===user.username?{...v,channel_name:user.display_name||user.username,channel_avatar:user.avatar_url}:v;
    videos=videos.map(applyProfile);
    const cached=profiles.get(user.username.toLowerCase());
    if(cached?.entries)profiles.set(user.username.toLowerCase(),{user,entries:cached.entries.map(applyProfile)});
    applySession({...state,user});
    fillProfileForm();
    toast(remove?'Profile picture removed':'Profile saved');
  }catch(err){error.textContent=err.message;error.hidden=false;}
  finally{buttons.forEach(button=>button.disabled=false);}
}
function openAccount(){showAccountPanel(state.user?'user':'guest');}
function openUpload(){if(!state.user){state.pendingUpload=true;showAccountPanel('guest');return;}showAccountPanel('upload');}
function setAuthMode(mode){state.authMode=mode;$('#loginTab').classList.toggle('selected',mode==='login');$('#signupTab').classList.toggle('selected',mode==='signup');$('#authSubmit').textContent=mode==='login'?'Log in':'Create account';$('#authForm [name=password]').autocomplete=mode==='login'?'current-password':'new-password';$('#authError').hidden=true;}

function escapeHtml(value){return String(value).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
function toast(message){const box=$('#toast');box.textContent=message;box.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>box.classList.remove('show'),2500);}
function thumb(v){const image=v.video_url?(v.thumbnail_url?`<img src="${v.thumbnail_url}" alt="" loading="lazy">`:`<div class="upload-placeholder">${escapeHtml(v.title)}</div>`):`<img src="thumbnails/${v.id}.jpg" alt="" loading="lazy" onerror="this.style.opacity='.18'">`;return `<div class="thumb">${image}<span class="duration">${v.duration}</span></div>`;}
function channelIcon(v){
  if(v.channel_avatar)return `<img class="channel-avatar" src="${escapeHtml(v.channel_avatar)}" alt="">`;
  return `<span class="channel-avatar" style="background:${v.color}">${escapeHtml((v.channel_name||v.channel)[0])}</span>`;}
function metadata(v){return `<div class="video-meta">${channelIcon(v)}<div class="video-text"><div class="video-title">${escapeHtml(v.title)}</div><div class="video-subtitle">${escapeHtml(v.channel_name||v.channel)}<br>${escapeHtml(v.views)} · ${escapeHtml(v.age)}</div></div></div>`;}
function card(v,mode='grid'){const details=mode==='result'?`<div>${metadata(v)}<div class="description">${escapeHtml(v.description)}</div></div>`:metadata(v);return `<article class="${mode==='result'?'result-card':'video-card'}" data-video="${v.id}" role="button" tabindex="0" aria-label="Watch ${escapeHtml(v.title)}">${thumb(v)}${details}</article>`;}
function mini(v){return `<article class="mini-card" data-video="${v.id}" role="button" tabindex="0" aria-label="Watch ${escapeHtml(v.title)}">${thumb(v)}<div><div class="video-title">${escapeHtml(v.title)}</div><div class="video-subtitle">${escapeHtml(v.channel_name||v.channel)}<br>${escapeHtml(v.views)}</div></div></article>`;}
function navButton(page,icon,label){return `<button class="nav-item ${state.page===page?'active':''}" data-page="${page}"><span class="nav-icon">${icon}</span><span>${label}</span></button>`;}
function renderNav(){nav.innerHTML=navButton('home','⌂','Home')+navButton('shorts','▣','Shorts')+navButton('subscriptions','▤','Subscriptions')+'<div class="nav-divider"></div>'+navButton('history','◷','History')+navButton('saved','☷','Watch later')+navButton('liked','♡','Liked videos');$('#mobileNav').innerHTML=navButton('home','⌂','Home')+navButton('shorts','▣','Shorts')+navButton('history','◷','History')+navButton('saved','☷','Saved')+navButton('liked','♡','Liked');$('#sidebar').classList.toggle('collapsed',state.collapsed);}
function filtered(){let list=videos;if(state.page==='history')list=state.history.map(id=>videos.find(v=>v.id===id)).filter(Boolean);if(state.page==='saved')list=state.saved.map(id=>videos.find(v=>v.id===id)).filter(Boolean);if(state.page==='liked')list=state.likes.map(id=>videos.find(v=>v.id===id)).filter(Boolean);if(state.page==='subscriptions')list=videos.filter(v=>['Lofi Girl','Bro Code','SuperSimpleDev','NASA'].includes(v.channel));if(state.category!=='All'&&['home','search'].includes(state.page))list=list.filter(v=>state.category==='Live'?v.duration==='LIVE':v.category===state.category);if(state.page==='search'){const q=state.query.toLowerCase().trim();list=list.filter(v=>(v.title+' '+v.channel+' '+(v.channel_name||'')+' '+v.category+' '+v.description).toLowerCase().includes(q));}return list;}
function chips(){return `<div class="chips">${categories.map(c=>`<button class="chip ${state.category===c?'selected':''}" data-category="${c}">${c}</button>`).join('')}</div>`;}
function empty(title,description,icon='⌕'){return `<div class="empty"><div class="empty-icon">${icon}</div><h2>${escapeHtml(title)}</h2><div>${escapeHtml(description)}</div></div>`;}
function renderFeed(){const list=filtered();let intro=state.page==='home'?'<h1 class="page-heading catalog-heading">Video library</h1>':'';if(state.page==='search')intro=`<h1 class="page-heading">Search results for “${escapeHtml(state.query)}”</h1>`;else if(!['home'].includes(state.page))intro=`<h1 class="page-heading">${{shorts:'Shorts',subscriptions:'Subscriptions',history:'History',saved:'Watch later',liked:'Liked videos'}[state.page]}</h1>`;const showChips=['home','search'].includes(state.page);main.innerHTML=(showChips?chips():'')+intro+(list.length?`<div class="${state.page==='search'?'result-list':'video-grid'}">${list.map(v=>card(v,state.page==='search'?'result':'grid')).join('')}</div>`:empty(state.page==='search'?'No matching videos':'Nothing here yet',state.page==='search'?'Try another search or category.':state.page==='home'?'Community uploads will appear here.':'Watch or save videos to build your library.'));}
function channelLink(v){
  return `<a href="#channel/${encodeURIComponent(v.channel)}">${escapeHtml(v.channel_name||v.channel)}</a>`;
}
function playerMarkup(v, autoplay=true){
  if(v.video_url){
    const poster=v.thumbnail_url?`poster="${escapeHtml(v.thumbnail_url)}"`:'';
    return `<video src="${escapeHtml(v.video_url)}" controls playsinline preload="metadata" ${poster}></video>`;
  }
  return `<iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(v.id)}?autoplay=${Number(autoplay)}&rel=0" title="${escapeHtml(v.title)}" allow="accelerometer; autoplay; encrypted-media; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
}
async function loadChannel(channel,key){
  try{
    const [user,entries]=await Promise.all([
      api('/api/profiles/'+encodeURIComponent(channel)),
      api('/api/videos?channel='+encodeURIComponent(channel))
    ]);
    profiles.set(key,{user,entries});
  }catch(error){profiles.set(key,{error:error.message});}
  if(state.page==='channel'&&state.channel.toLowerCase()===key)renderChannel();
}
function renderChannel(){
  const key=state.channel.toLowerCase();
  const profile=profiles.get(key);
  if(!profile){
    profiles.set(key,{});
    loadChannel(state.channel,key);
  }
  if(!profile?.user){
    main.innerHTML=profile?.error?empty('Profile unavailable',profile.error):'<p role="status">Loading profile…</p>';
    return;
  }
  const {user,entries}=profile;
  const own=state.user?.id===user.id;
  const name=user.display_name||user.username;
  main.innerHTML=`<header class="channel-header">${channelIcon({channel:user.username,channel_name:name,channel_avatar:user.avatar_url,color:'#8066a5'})}<div><h1>${escapeHtml(name)} ${user.is_owner?'<span class="owner-badge">Owner</span>':''}</h1><p>@${escapeHtml(user.username)} · ${entries.length} ${entries.length===1?'video':'videos'}</p>${user.bio?`<p class="profile-bio">${escapeHtml(user.bio)}</p>`:''}${own?'<button class="pill-button" id="editProfileButton">Edit profile</button>':''}${state.user?.is_owner&&!user.is_owner?`<button class="pill-button" id="banAccount">${user.is_banned?'Unban':'Ban'} account</button>`:''}</div></header>${entries.length?`<div class="video-grid">${entries.map(v=>card(v)).join('')}</div>`:empty('No uploads yet','Public videos will appear here.')}`;
}
function renderShorts(){
  const entries=videos.filter(v=>v.category==='Shorts');
  const index=state.video?entries.findIndex(v=>v.id===state.video):0;
  if(!entries.length){main.innerHTML=empty('No Shorts yet','Community uploads marked as Shorts will appear here.');return;}
  const v=entries[index];
  if(!v){main.innerHTML=empty('Short unavailable','Browse the Shorts feed for available videos.');return;}
  const previous=entries[index-1];
  const next=entries[index+1];
  main.innerHTML=`<section class="short-view"><div class="short-media">${playerMarkup(v,false)}</div><div class="short-details"><h1>${escapeHtml(v.title)}</h1><p>${channelLink(v)}</p><p>${escapeHtml(v.description)}</p><nav aria-label="Shorts navigation">${previous?`<a class="pill-button" href="#shorts/${encodeURIComponent(previous.id)}">Previous</a>`:''}${next?`<a class="pill-button" href="#shorts/${encodeURIComponent(next.id)}">Next</a>`:''}</nav><a class="pill-button" href="#watch/${encodeURIComponent(v.id)}">Open watch page</a></div></section>`;
}
function findVideo(id){return videos.find(v=>v.id===id)||[...profiles.values()].flatMap(p=>p.entries||[]).find(v=>v.id===id);}
function renderWatch(){const v=findVideo(state.video);if(!v){main.innerHTML=empty('Video not found','Choose another video.');return;}const recommendations=videos.filter(x=>x.id!==v.id).sort((a,b)=>(a.category===v.category?-1:0)-(b.category===v.category?-1:0)).slice(0,12);const liked=state.likes.includes(v.id),saved=state.saved.includes(v.id);const player=playerMarkup(v);main.innerHTML=`<div class="watch-layout"><section><div class="player">${player}</div><h1 class="watch-title">${escapeHtml(v.title)}</h1><div class="watch-row"><div class="channel-block">${channelIcon(v)}<div><strong>${channelLink(v)}</strong><small>Video creator</small></div></div><div class="watch-actions"><button class="pill-button ${liked?'active':''}" id="likeButton">${liked?'♥ Liked':'♡ Like'}</button><button class="pill-button ${saved?'active':''}" id="saveButton">${saved?'✓ Saved':'+ Save'}</button><button class="pill-button" id="shareButton">↗ Share</button>${state.user?.is_owner?'<button class="pill-button" id="removeVideo">Remove video</button>':''}${v.video_url?'':`<a class="pill-button" href="https://www.youtube.com/watch?v=${v.id}" target="_blank" rel="noopener noreferrer">Open on YouTube</a>`}</div></div><div class="desc-box"><strong>${escapeHtml(v.views)} · ${escapeHtml(v.age)}</strong><p>${escapeHtml(v.description)}</p></div><section class="comments"><h3>Comments</h3><form class="comment-form" id="commentForm"><span class="avatar">${state.user?escapeHtml(state.user.username[0].toUpperCase()):'?'}</span><input id="commentInput" placeholder="${state.user?'Add a comment…':'Log in to comment'}" maxlength="500" required><button type="submit">Comment</button></form><div id="commentList"></div></section></section><aside class="recommendations"><h3>Up next</h3>${recommendations.map(mini).join('')}</aside></div>`;renderComments(v.id);loadComments(v.id);}
function renderComments(id){const list=state.comments[id]||[];const target=$('#commentList');if(!target)return;target.innerHTML=list.length?'':`<p class="video-subtitle">No comments yet. Be the first.</p>`;for(const item of list){const row=document.createElement('div');row.className='comment';const avatar=document.createElement('span');avatar.className='avatar';avatar.textContent=(item.username||'?')[0].toUpperCase();const body=document.createElement('div');const name=document.createElement('strong');name.textContent=item.username||'Member';const p=document.createElement('p');p.textContent=item.text;const date=document.createElement('small');date.textContent=new Date(item.time).toLocaleString();body.append(name,p,date);if(state.user?.is_owner){const button=document.createElement('button');button.type='button';button.className='pill-button';button.textContent='Remove comment';button.addEventListener('click',()=>moderateComment(item.id,id));body.append(button);}row.append(avatar,body);target.append(row);}}
async function loadComments(id){try{const list=await api('/api/comments/'+encodeURIComponent(id));state.comments[id]=list;if(state.video===id)renderComments(id);}catch{}}
function render(){document.body.classList.toggle('light',state.light);renderNav();search.value=state.page==='search'?state.query:'';const renderPage={watch:renderWatch,shorts:renderShorts,channel:renderChannel};(renderPage[state.page]||renderFeed)();document.title=state.page==='watch'?(findVideo(state.video)?.title||'Video')+' — MrHTube':'MrHTube';}
function go(page,options={}){state.page=page;state.video=null;if(options.video){state.video=options.video;if(state.user){state.history=[options.video,...state.history.filter(x=>x!==options.video)].slice(0,100);api('/api/history/'+encodeURIComponent(options.video),{method:'POST'}).catch(()=>{});}}if(options.query!==undefined){state.query=options.query;state.category='All';}const hash=page==='watch'?`#watch/${state.video}`:page==='search'?`#search/${encodeURIComponent(state.query)}`:`#${page}`;if(location.hash!==hash)history.pushState(null,'',hash);render();window.scrollTo(0,0);}
function syncHash(){let raw;try{raw=decodeURIComponent(location.hash.slice(1)||'home');}catch{raw='home';}state.video=null;if(raw.startsWith('watch/')){state.page='watch';state.video=raw.slice(6);}else if(raw.startsWith('shorts/')){state.page='shorts';state.video=raw.slice(7);}else if(raw.startsWith('channel/')){state.page='channel';state.channel=raw.slice(8);}else if(raw.startsWith('search/')){state.page='search';state.query=raw.slice(7);state.category='All';}else{state.page=['home','shorts','subscriptions','history','saved','liked'].includes(raw)?raw:'home';}render();}
async function toggleList(key,id){if(!state.user){openAccount();return;}try{const result=await api('/api/library/'+key+'/'+encodeURIComponent(id),{method:'POST'});state[key]=result.active?[id,...state[key].filter(x=>x!==id)]:state[key].filter(x=>x!==id);const button=$(key==='likes'?'#likeButton':'#saveButton');if(button){button.classList.toggle('active',result.active);button.textContent=key==='likes'?(result.active?'♥ Liked':'♡ Like'):(result.active?'✓ Saved':'+ Save');}toast(result.active?'Saved to your account':'Removed from your account');}catch(error){toast(error.message);}}

async function moderateAccount(){
  const key=state.channel.toLowerCase();
  const user=profiles.get(key)?.user;
  if(!user)return;
  const action=user.is_banned?'Unban':'Ban';
  if(!confirm(action+' @'+user.username+'?'))return;
  try{
    await api('/api/owner/ban/'+encodeURIComponent(user.username)+'?banned='+!user.is_banned,{method:'PUT'});
    profiles.delete(key);
    render();
    toast(action==='Ban'?'Account banned':'Account unbanned');
  }catch(error){toast(error.message);}
}
async function moderateVideo(){
  const id=state.video;
  if(!confirm('Remove this video from MrHTube?'))return;
  try{
    await api('/api/owner/videos/'+encodeURIComponent(id),{method:'DELETE'});
    videos=videos.filter(video=>video.id!==id);
    profiles.clear();
    go('home');
    toast('Video removed');
  }catch(error){toast(error.message);}
}
async function moderateComment(commentId,videoId){
  if(!confirm('Remove this comment?'))return;
  try{
    await api('/api/owner/comments/'+encodeURIComponent(commentId),{method:'DELETE'});
    await loadComments(videoId);
  }catch(error){toast(error.message);}
}

$('#passwordForm').addEventListener('submit',async event=>{
  event.preventDefault();
  const form=event.currentTarget;
  const error=$('#passwordError');
  const submit=form.querySelector('button');
  submit.disabled=true;
  error.hidden=true;
  try{
    await api('/api/me/password',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    form.reset();
    toast('Password changed');
  }catch(err){error.textContent=err.message;error.hidden=false;}
  finally{submit.disabled=false;}
});

$('#searchForm').addEventListener('submit',e=>{e.preventDefault();const q=search.value.trim();if(q)go('search',{query:q});});
$('#clearSearch').addEventListener('click',()=>{search.value='';search.focus();});
$('#brandButton').addEventListener('click',()=>{state.category='All';go('home');});
$('#menuButton').addEventListener('click',()=>{state.collapsed=!state.collapsed;write('collapsed',state.collapsed);renderNav();});
$('#themeButton').addEventListener('click',()=>{state.light=!state.light;write('light',state.light);render();});
$('#accountButton').addEventListener('click',openAccount);
$('#uploadButton').addEventListener('click',openUpload);
$('#closeAccount').addEventListener('click',()=>$('#accountDialog').close());
$('#loginTab').addEventListener('click',()=>setAuthMode('login'));
$('#signupTab').addEventListener('click',()=>setAuthMode('signup'));
$('#authForm').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;const button=$('#authSubmit');const error=$('#authError');button.disabled=true;error.hidden=true;try{const data=await api('/api/'+(state.authMode==='login'?'login':'signup'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:form.elements.namedItem('username').value.trim(),password:form.elements.namedItem('password').value})});form.reset();applySession(data);if(state.pendingUpload){state.pendingUpload=false;showAccountPanel('upload');}else $('#accountDialog').close();toast('Welcome, '+data.user.username+'!');}catch(err){error.textContent=err.message;error.hidden=false;}finally{button.disabled=false;}});
$('#profileForm').addEventListener('submit',event=>{event.preventDefault();saveProfile();});
$('#removeAvatar').addEventListener('click',()=>saveProfile(true));
$('#viewProfile').addEventListener('click',()=>$('#accountDialog').close());
$('#logoutButton').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST'});applySession({user:null,likes:[],saved:[],history:[]});$('#accountDialog').close();toast('Logged out');}catch(error){toast(error.message);}});
$('#uploadForm').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;const button=$('#uploadSubmit');const error=$('#uploadError');error.hidden=true;button.disabled=true;button.textContent='Uploading…';try{const data=await api('/api/upload',{method:'POST',body:new FormData(form)});const uploaded=await api('/api/videos');videos=uploaded;form.reset();$('#accountDialog').close();profiles.delete(state.user.username.toLowerCase());go('watch',{video:data.id});toast('Video published');}catch(err){error.textContent=err.message;error.hidden=false;}finally{button.disabled=false;button.textContent='Publish video';}});
document.addEventListener('click',e=>{const page=e.target.closest('[data-page]');if(page){state.category='All';go(page.dataset.page);return;}const category=e.target.closest('[data-category]');if(category){state.category=category.dataset.category;renderFeed();return;}if(e.target.id==='banAccount'){moderateAccount();return;}if(e.target.id==='removeVideo'){moderateVideo();return;}if(e.target.id==='editProfileButton'){openAccount();return;}const video=e.target.closest('[data-video]');if(video){go('watch',{video:video.dataset.video});return;}if(e.target.id==='likeButton')toggleList('likes',state.video);if(e.target.id==='saveButton')toggleList('saved',state.video);if(e.target.id==='shareButton'){const url=location.href;navigator.clipboard?.writeText(url).then(()=>toast('Link copied')).catch(()=>toast(url));}});
document.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-video]')){e.preventDefault();go('watch',{video:e.target.dataset.video});}if(e.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();search.focus();}});
document.addEventListener('submit',async e=>{if(e.target.id!=='commentForm')return;e.preventDefault();if(!state.user){openAccount();return;}const input=$('#commentInput');const value=input.value.trim();if(!value)return;try{await api('/api/comments/'+encodeURIComponent(state.video),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:value})});input.value='';await loadComments(state.video);toast('Comment posted');}catch(error){toast(error.message);}});
window.addEventListener('hashchange',syncHash);
syncHash();
Promise.allSettled([api('/api/me'),api('/api/videos')]).then(results=>{if(results[0].status==='fulfilled')applySession(results[0].value);if(results[1].status==='fulfilled'){videos=results[1].value;render();}});
