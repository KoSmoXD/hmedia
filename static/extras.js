'use strict';
// Mr H Media enhancements. Loaded after the existing client.
(()=>{
  const originalPostMarkup=postMarkup;
  const defaultTheme={accent:'#8a7eff',sidebar:'#151620',chat:'#1a1b26',text:'#e8e9f2'};
  let currentTheme={...defaultTheme}, pings=[];
  function setTheme(theme){
    currentTheme={...defaultTheme,...theme};
    for(const [name,value] of Object.entries(currentTheme)){
      if(/^#[\da-fA-F]{6}$/.test(value)) document.documentElement.style.setProperty('--theme-'+name,value);
    }
    document.documentElement.dataset.customTheme='true';
  }
  async function readTheme(){
    if(!state.me)return;
    try{setTheme(await api('/api/me/theme'))}catch(err){console.warn('Theme unavailable:',err.message)}
  }
  const originalEnterApp=enterApp;
  enterApp=async function(){await originalEnterApp();await readTheme();await readPings()};
  const originalOpenSettings=openSettings;
  openSettings=function(tab='account'){
    originalOpenSettings(tab);
    const side=document.querySelector('.settings-sidebar');
    if(!side)return;
    const button=document.createElement('button');
    button.type='button';button.className='settings-nav';button.dataset.tab='appearance';
    button.innerHTML=icon('spark')+' Theme maker';
    const logout=side.querySelector('#settings-logout');
    side.insertBefore(button,logout);
    button.addEventListener('click',()=>renderSettings('appearance'));
    button.classList.toggle('selected',tab==='appearance');
  };
  const originalRenderSettings=renderSettings;
  renderSettings=function(tab){
    if(tab!=='appearance'){
      originalRenderSettings(tab);
      if(tab==='account'||tab==='profile'){
        const field=$('avatar-file');
        if(field){
          const action=document.createElement('label');
          action.className='avatar-change-text';
          action.htmlFor='avatar-file';
          action.textContent='Change profile picture';
          field.insertAdjacentElement('afterend',action);
        }
      }
      return;
    }
    state.settingsTab='appearance';
    document.querySelectorAll('[data-tab]').forEach(el=>el.classList.toggle('selected',el.dataset.tab==='appearance'));
    const root=$('settings-main');
    if(!root)return;
    root.innerHTML='<h2>Custom theme maker</h2><p class="muted-p">Choose your own colors and save them to your account.</p>'
      +'<div class="theme-presets"><button data-theme-preset="midnight">Midnight</button><button data-theme-preset="ocean">Ocean</button><button data-theme-preset="rose">Rose</button><button data-theme-preset="forest">Forest</button></div>'
      +'<form class="theme-form" id="theme-customizer">'
      +[['accent','Accent color'],['sidebar','Sidebar background'],['chat','Chat background'],['text','Text color']]
        .map(([key,label])=>'<label>'+label+'<input type="color" name="'+key+'" value="'+currentTheme[key]+'"></label>').join('')
      +'<div class="theme-actions"><button class="save-btn" type="submit">Save theme</button><button class="theme-reset" type="button" id="theme-reset">Reset</button></div><p class="settings-message" id="theme-message"></p></form>';
    const form=$('theme-customizer');
    form.addEventListener('input',()=>setTheme(Object.fromEntries(new FormData(form).entries())));
    const presets={
      midnight:defaultTheme,
      ocean:{accent:'#45bed8',sidebar:'#101d2b',chat:'#102536',text:'#ddf7ff'},
      rose:{accent:'#f195c8',sidebar:'#251526',chat:'#281d30',text:'#fff0f8'},
      forest:{accent:'#71d79e',sidebar:'#10251e',chat:'#14271f',text:'#e3fff0'}
    };
    root.querySelectorAll('[data-theme-preset]').forEach(b=>b.addEventListener('click',()=>{
      setTheme(presets[b.dataset.themePreset]);renderSettings('appearance');
    }));
    $('theme-reset').addEventListener('click',()=>{setTheme(defaultTheme);renderSettings('appearance')});
    form.addEventListener('submit',async e=>{
      e.preventDefault();const status=$('theme-message');status.textContent='Saving theme…';
      try{setTheme(await api('/api/me/theme',{method:'PATCH',body:JSON.stringify(currentTheme)}));status.textContent='Theme saved to your account.';showToast('Theme saved.')}
      catch(error){status.textContent=error.message}
    });
  };
  const dock=$('dock-avatar');dock.style.cursor='pointer';dock.title='Change profile picture';
  dock.addEventListener('click',()=>openSettings('profile'));

  // Add comments beneath posts without replacing the original feed.
  postMarkup=function(p){
    const html=originalPostMarkup(p);
    return html.replace('</article>','<div class="post-actions"><button type="button" data-comments="'+p.id+'">'+icon('message')+' Comments</button></div><div class="comment-area" data-comment-area="'+p.id+'" hidden></div></article>');
  };
  function commentHTML(c){
    return '<div class="comment-row">'+avatar(c.user)
      +'<div class="comment-content"><div class="comment-meta"><button data-user="'+c.user.id+'">'+escapeHtml(c.user.display_name)+'</button>'
      +roleBadge(c.user)+' <small>'+formatTime(c.created_at)+'</small>'
      +((c.user.id===state.me.id||state.me.role==='owner')?'<button class="comment-delete" data-remove-comment="'+c.id+'">Delete</button>':'')
      +'</div><p>'+escapeHtml(c.body)+'</p></div></div>';
  }
  async function showComments(id){
    const area=document.querySelector('[data-comment-area="'+id+'"]');
    if(!area||area.hidden)return;
    try{
      const comments=await api('/api/posts/'+id+'/comments');
      area.innerHTML='<div class="comments-head">Discussion · '+comments.length+' comments</div><div class="comment-list">'
        +(comments.length?comments.map(commentHTML).join(''):'<p class="muted-p">No comments yet. Start the discussion.</p>')
        +'</div><form class="comment-form" data-new-comment="'+id+'"><input name="body" maxlength="1500" required placeholder="Comment here · @username to ping" aria-label="Your comment"><button type="submit" class="save-btn">Reply</button></form>';
      const b=document.querySelector('[data-comments="'+id+'"]');
      if(b)b.innerHTML=icon('message')+' '+comments.length+' Comments';
    }catch(e){showToast(e.message)}
  }
  $('messages').addEventListener('click',async e=>{
    const toggle=e.target.closest('[data-comments]');
    if(toggle){
      const id=Number(toggle.dataset.comments),area=document.querySelector('[data-comment-area="'+id+'"]');
      if(!area)return;
      area.hidden=!area.hidden;
      if(!area.hidden)await showComments(id);
      return;
    }
    const button=e.target.closest('[data-remove-comment]');
    if(button){
      const post=button.closest('[data-post]');if(!post||!confirm('Delete comment?'))return;
      try{await api('/api/posts/'+post.dataset.post+'/comments/'+button.dataset.removeComment,{method:'DELETE'});await showComments(Number(post.dataset.post))}
      catch(err){showToast(err.message)}
    }
  });
  $('messages').addEventListener('submit',async e=>{
    const form=e.target.closest('[data-new-comment]');if(!form)return;
    e.preventDefault();const id=Number(form.dataset.newComment),body=form.querySelector('input').value.trim();
    if(!body)return;
    const submit=form.querySelector('button[type=submit]');submit.disabled=true;
    try{await api('/api/posts/'+id+'/comments',{method:'POST',body:JSON.stringify({body})});await showComments(id)}
    catch(err){showToast(err.message);submit.disabled=false}
  });

  // @-mention suggestions for channel and direct chat.
  const input=$('message-input'),container=$('composer-area'),picker=document.createElement('div');
  picker.className='mention-menu';picker.hidden=true;container.appendChild(picker);
  let match=null,selected=0;
  function updatePicker(){
    if(!state.me)return;
    const prefix=input.value.slice(0,input.selectionStart),m=prefix.match(/(^|\s)@([A-Za-z0-9_]{0,24})$/);
    if(!m){picker.hidden=true;match=null;return}
    const list=state.members.filter(u=>u.id!==state.me.id&&u.username.toLowerCase().startsWith(m[2].toLowerCase())).slice(0,8);
    if(!list.length){picker.hidden=true;match=null;return}
    match={start:input.selectionStart-m[2].length-1,end:input.selectionStart,list};selected=0;
    picker.innerHTML='<div class="mention-caption">PING SOMEONE</div>'
      +list.map((u,i)=>'<button data-pick="'+i+'" class="'+(i?'':'selected')+'">'+avatar(u)+'<span>@'+escapeHtml(u.username)+'</span></button>').join('');
    picker.hidden=false;
  }
  function choose(i){
    if(!match)return;const u=match.list[i];if(!u)return;
    input.value=input.value.slice(0,match.start)+'@'+u.username+' '+input.value.slice(match.end);
    const pos=match.start+u.username.length+2;input.focus();input.setSelectionRange(pos,pos);picker.hidden=true;match=null;
  }
  input.addEventListener('input',updatePicker);
  input.addEventListener('keydown',e=>{
    if(picker.hidden||!match)return;
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){
      e.preventDefault();selected=(selected+(e.key==='ArrowDown'?1:-1)+match.list.length)%match.list.length;
      picker.querySelectorAll('[data-pick]').forEach((b,i)=>b.classList.toggle('selected',i===selected));
    }else if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();e.stopImmediatePropagation();choose(selected)}
    else if(e.key==='Escape'){e.preventDefault();picker.hidden=true}
  },{capture:true});
  picker.addEventListener('click',e=>{const b=e.target.closest('[data-pick]');if(b)choose(Number(b.dataset.pick))});

  // Notifications are stored server-side and follow the account across devices.
  const header=document.querySelector('.chat-header .header-tools');
  const notif=document.createElement('button');
  notif.id='notification-button';notif.className='icon-button notif-button';
  notif.title='Pings and notifications';notif.setAttribute('aria-label','Pings and notifications');
  notif.innerHTML=icon('bell')+'<span class="notification-count" id="notification-count" hidden>0</span>';
  header.prepend(notif);
  async function readPings(){
    if(!state.me)return;
    try{
      pings=await api('/api/notifications');
      const total=pings.filter(p=>!p.read).length;
      $('notification-count').hidden=!total;$('notification-count').textContent=total>99?'99+':String(total);
      notif.classList.toggle('has-notifications',total>0);
    }catch(e){console.warn('Notification load failed:',e.message)}
  }
  async function openPings(){
    await readPings();
    openModal('<div class="compact-modal notification-panel">'+closeButton
      +'<h2>Mentions & pings</h2><p class="muted-p">Mention @'+escapeHtml(state.me.username)+' to notify you.</p>'
      +'<div class="notification-list">'+(pings.length?pings.map(p=>'<button class="notification-item '+(p.read?'':'unread')+'" data-ping="'+p.id+'"><strong>@'+escapeHtml(p.sender)+'</strong><span>'+escapeHtml(p.preview)+'</span><small>'+escapeHtml(p.kind)+' · '+formatTime(p.created_at)+'</small></button>').join(''):'<p class="muted-p">No mentions yet.</p>')+'</div></div>');
    document.querySelectorAll('[data-ping]').forEach(button=>button.addEventListener('click',()=>{
      const p=pings.find(x=>x.id===Number(button.dataset.ping));closeModal();if(!p)return;
      if(p.kind==='post')openPosts();
      else if(p.kind==='channel')openChannel(p.context);
      else if(p.kind==='dm'){const sender=state.members.find(u=>u.username.toLowerCase()===p.sender.toLowerCase());if(sender)openDm(sender.id)}
    }));
    try{await api('/api/notifications/read',{method:'POST'});await readPings()}catch(e){showToast(e.message)}
  }
  notif.addEventListener('click',openPings);
  const previousConnectWs=connectWs;
  connectWs=function(){previousConnectWs();if(state.ws){state.ws.addEventListener('message',ev=>{try{if(JSON.parse(ev.data).type==='ping-alert'){readPings();showToast('You were mentioned!')}}catch{}})}};
  setInterval(()=>{if(state.me)readPings()},20000);
  readPings();
})();
