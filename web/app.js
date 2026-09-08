const $ = (id) => document.getElementById(id);
let appState = null;
let scanState = {people:[], conversations:[], running:false, progress:0};
let previewProfile = null;
let networkRows = [];
let networkFilter = 'ALL';
let activeDrawer = null;
let modalConfirm = null;

function esc(v='') { return String(v).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function fmt(n){ return Number(n||0).toLocaleString(); }
function api(){ return window.pywebview?.api; }

function toast(message){
  const t=$('toast'); t.textContent=message; t.classList.add('show');
  clearTimeout(window.__toast); window.__toast=setTimeout(()=>t.classList.remove('show'),2600);
}

function showModal({kicker='CONFIRM ACTION', title, body='', content='', confirmText='CONFIRM', danger=false, onConfirm=null}){
  $('modalKicker').textContent=kicker; $('modalTitle').textContent=title; $('modalBody').textContent=body;
  $('modalContent').innerHTML=content; $('modalActions').innerHTML='';
  const cancel=document.createElement('button'); cancel.className='ghost'; cancel.textContent='CANCEL'; cancel.onclick=closeModal;
  $('modalActions').appendChild(cancel);
  if(onConfirm){
    const btn=document.createElement('button'); btn.className=danger?'ghost danger':'primary'; btn.textContent=confirmText;
    btn.onclick=async()=>{ btn.disabled=true; try{ await onConfirm(); } finally{ closeModal(); } };
    $('modalActions').appendChild(btn);
  }
  $('modalBackdrop').classList.add('open'); $('modalBackdrop').setAttribute('aria-hidden','false');
}
function closeModal(){ $('modalBackdrop').classList.remove('open'); $('modalBackdrop').setAttribute('aria-hidden','true'); }

function switchView(name){
  document.querySelectorAll('.navRail button').forEach(b=>b.classList.toggle('active', b.dataset.view===name));
  document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active', v.id===`view-${name}`));
  if(name==='network') refreshNetwork();
}

async function init(){
  appState=await api().app_state();
  $('version').textContent=`v${appState.version}`;
  applyMode(); fillProfiles(); applyProfile(appState.activeProfile); updateNetworkStats(appState.networkStats);
  networkRows=await api().network_rows(); renderNetwork();
  drawStars();
}

function updateQuiet(){
  const live=appState?.mode==='live';
  const rules=appState?.rules||{}; const seen=appState?.seen||{};
  const base=live
    ? 'Fresh live X sweep every START. No candidate cache. Follow / unfollow happens only after your click.'
    : 'Fresh demo sweep every START. Connect your own X developer app when you are ready to go live.';
  $('missionQuiet').textContent=`${base} Quality ≥${rules.qualityPeople??'—'} people / ≥${rules.qualityConversations??'—'} openings. Seen ${seen.person||0}/${seen.conversation||0}.`;
}
function applyMode(){
  const c=appState?.connection||{}; const live=Boolean(c.connected);
  const username=c.account?.username||'';
  $('modeLabel').textContent=live?`LIVE X // @${username||'CONNECTED'}`:'DEMO // OFFLINE';
  $('modeDot').classList.toggle('live', live);
  $('openXConnection').classList.toggle('live',live);
  $('openXConnection').classList.toggle('connecting',Boolean(c.connecting));
  $('xLinkDot').classList.toggle('live',live);
  $('xLinkText').textContent=live?(username?`@${username}`:'CONNECTED'):(c.connecting?'WAITING':'CONNECT');
  updateQuiet();
}

function fillProfiles(){
  const select=$('profileSelect'); select.innerHTML='';
  for(const name of appState.profiles){ const o=document.createElement('option'); o.value=name; o.textContent=name; select.appendChild(o); }
  select.value=appState.activeProfile.name;
}

function applyProfile(p){
  if(!p) return; previewProfile=JSON.parse(JSON.stringify(p));
  $('topicDescription').value=p.description||'';
  $('topicExclusions').value=(p.blockedTerms||[]).join(', ');
  $('blockedEditor').value=(p.blockedTerms||[]).join('\n');
  $('profileName').value=p.name||'Custom Scout';
  renderChips(p.wantedTerms||[]);
}
function renderChips(terms){
  const root=$('wantedChips');
  root.innerHTML='';
  for(const term of (terms||[])){
    const chip=document.createElement('span');
    chip.className='chip removableChip';

    const label=document.createElement('span');
    label.className='chipLabel';
    label.textContent=term;

    const remove=document.createElement('button');
    remove.type='button';
    remove.className='chipRemove';
    remove.textContent='×';
    remove.title=`Remove ${term} from this Scout profile`;
    remove.setAttribute('aria-label', `Remove ${term}`);
    remove.addEventListener('click',()=>removeWantedTerm(term));

    chip.append(label,remove);
    root.appendChild(chip);
  }
  if(!(terms||[]).length){
    root.innerHTML='<span class="chipEmpty">No core signals selected — analyse again or load a profile.</span>';
  }
}

function removeWantedTerm(term){
  if(!previewProfile) return;
  const key=String(term||'').trim().toLowerCase();
  previewProfile.wantedTerms=(previewProfile.wantedTerms||[]).filter(t=>String(t).trim().toLowerCase()!==key);
  renderChips(previewProfile.wantedTerms);
  toast(`${term} removed from core signals.`);
}

async function onProfileChange(){
  appState=await api().activate_profile($('profileSelect').value); applyProfile(appState.activeProfile); updateNetworkStats(appState.networkStats); updateQuiet(); toast(`Mission profile: ${appState.activeProfile.name}`);
}

async function analyseTopics(){
  const p=await api().analyse_topics($('topicDescription').value, $('topicExclusions').value); previewProfile=p;
  $('profileName').value=p.name; $('blockedEditor').value=(p.blockedTerms||[]).join('\n'); renderChips(p.wantedTerms||[]);
  toast('Scout vectors resolved. Edit exclusions if needed.');
}

async function saveProfile(){
  if(!previewProfile) await analyseTopics();
  if(!(previewProfile.wantedTerms||[]).length){ toast('Keep at least one core signal before saving.'); return; }
  previewProfile.name=$('profileName').value.trim()||'Custom Scout';
  previewProfile.description=$('topicDescription').value.trim()||previewProfile.description;
  previewProfile.blockedTerms=$('blockedEditor').value.split(/[,\n;]/).map(s=>s.trim()).filter(Boolean);
  appState=await api().save_profile(previewProfile); fillProfiles(); applyProfile(appState.activeProfile); toast('Profile saved and armed.'); switchView('people');
}

async function startScan(){
  const btn=$('startScout'); btn.disabled=true; $('radarWrap').classList.add('scanning');
  scanState=await api().start_scan(); renderScanState(); pollScan();
}
async function pollScan(){
  scanState=await api().scan_state(); renderScanState();
  if(scanState.running){ setTimeout(pollScan,260); return; }
  $('startScout').disabled=false; $('radarWrap').classList.remove('scanning');
  if(scanState.error){ toast(scanState.error); return; }
  renderPeople(); renderConversations();
  networkRows=await api().network_rows(); renderNetwork();
  appState=await api().app_state(); updateNetworkStats(appState.networkStats); updateQuiet();
  toast('Scout sweep complete.');
}
function renderScanState(){
  const p=Math.max(0,Math.min(100,Number(scanState.progress||0))); $('scanPct').textContent=String(Math.round(p)).padStart(2,'0'); $('progressFill').style.width=`${p}%`;
  $('missionMessage').textContent=scanState.message||'RADAR STANDBY';
  $('signalMetric').textContent=scanState.signals ? fmt(scanState.signals) : (p===100 ? fmt((scanState.people?.length||0)+(scanState.conversations?.length||0)) : '—');
  $('peopleMetric').textContent=scanState.people?.length||'—'; $('conversationMetric').textContent=scanState.conversations?.length||'—';
  $('peopleCount').textContent=String(scanState.people?.length||0).padStart(2,'0'); $('conversationCount').textContent=String(scanState.conversations?.length||0).padStart(2,'0');
}

function renderPeople(){
  const root=$('peopleStream'); const people=scanState.people||[];
  if(!people.length){root.innerHTML='<div class="emptyState">NO NEW PEOPLE <span>Quality floor held. Press START again for another unseen sweep.</span></div>';return;}
  root.innerHTML=people.map(p=>`
    <article class="signalCard" data-person="${esc(p.id)}">
      <div class="scoreRing">${Math.round(p.score)}</div>
      <div>
        <div class="accountLine">${esc(p.name)} <span class="handle">@${esc(p.username)}</span> <span class="verify">${esc(p.verifiedType).toUpperCase()}</span></div>
        <div class="signalReason">${esc(p.reason)}</div>
        <div class="signalMeta"><span class="hot">ACTIVE ${esc(p.age)}</span><span>${fmt(p.followers)} FOLLOWERS</span><span>${esc(p.relationship||'NEW')}</span></div>
      </div>
      <div class="quickActions">
        <button class="quickAction" data-follow="${esc(p.id)}">FOLLOW →</button>
        <button class="quickAction mutedAction" data-notforme="${esc(p.id)}">NOT FOR ME</button>
        <button class="quickAction dangerAction" data-never="${esc(p.id)}">NEVER</button>
      </div>
    </article>`).join('');
  root.querySelectorAll('[data-person]').forEach(el=>el.addEventListener('click',e=>{ if(e.target.closest('.quickActions')) return; openPerson(el.dataset.person); }));
  root.querySelectorAll('[data-follow]').forEach(btn=>btn.addEventListener('click',e=>{e.stopPropagation(); confirmFollow(btn.dataset.follow);}));
  root.querySelectorAll('[data-notforme]').forEach(btn=>btn.addEventListener('click',e=>{e.stopPropagation(); notForMe(btn.dataset.notforme);}));
  root.querySelectorAll('[data-never]').forEach(btn=>btn.addEventListener('click',e=>{e.stopPropagation(); setDecision('person',btn.dataset.never,'never_show');}));
}

function renderConversations(){
  const root=$('conversationStream'); const items=scanState.conversations||[];
  if(!items.length){root.innerHTML='<div class="emptyState">NO LIVE OPENINGS <span>Try another profile or sweep later.</span></div>';return;}
  root.innerHTML=items.map(c=>`
    <article class="signalCard conversationCard" data-conversation="${esc(c.id)}">
      <div class="scoreRing">${Math.round(c.score)}</div>
      <div>
        <div class="accountLine">@${esc(c.username)} <span class="verify">${esc(c.verifiedType).toUpperCase()}</span>${c.relationship!=='NEW'?`<span class="networkBadge">${esc(c.relationship)}</span>`:''}</div>
        <div class="conversationText">${esc(c.text)}</div>
        <div class="signalMeta"><span class="hot">${esc(c.age)} AGO</span><span>${fmt(c.replyCount)} REPLIES</span><span>${fmt(c.likeCount)} LIKES</span></div>
      </div>
    </article>`).join('');
  root.querySelectorAll('[data-conversation]').forEach(el=>el.addEventListener('click',()=>openConversation(el.dataset.conversation)));
}

function openDrawer({type,score,title,meta,text,reason,actions}){
  activeDrawer={type}; $('drawerType').textContent=type; $('drawerScore').textContent=Math.round(score||0); $('drawerTitle').textContent=title; $('drawerMeta').textContent=meta||''; $('drawerText').textContent=text||''; $('drawerReason').textContent=reason||'';
  const root=$('drawerActions'); root.innerHTML=''; for(const a of actions||[]){const b=document.createElement('button');b.className=a.primary?'primary':'ghost';b.textContent=a.label;b.onclick=a.click;root.appendChild(b)}
  $('detailDrawer').classList.add('open'); $('detailDrawer').setAttribute('aria-hidden','false');
}
function closeDrawer(){ $('detailDrawer').classList.remove('open'); $('detailDrawer').setAttribute('aria-hidden','true'); }
function openPerson(id){
  const p=(scanState.people||[]).find(x=>x.id===id); if(!p)return;
  openDrawer({type:'PEOPLE SIGNAL',score:p.score,title:`${p.name}  @${p.username}`,meta:`${p.verifiedType.toUpperCase()} // ${fmt(p.followers)} FOLLOWERS // ACTIVE ${p.age}`,
    text:p.description,reason:p.reason,actions:[
      {label:'FOLLOW',primary:true,click:()=>confirmFollow(p.id)},
      {label:'OPEN ON X',click:()=>api().open_url(p.url)},
      {label:'NOT FOR ME',click:()=>notForMe(p.id)},
      {label:'NEVER SHOW',click:()=>setDecision('person',p.id,'never_show')}
    ]});
}
function openConversation(id){
  const c=(scanState.conversations||[]).find(x=>x.id===id); if(!c)return;
  openDrawer({type:'CONVERSATION SIGNAL',score:c.score,title:`@${c.username}`,meta:`${c.relationship} // ${c.age} AGO // ${c.replyCount} REPLIES`,text:c.text,reason:c.reason,actions:[
    {label:'OPEN CONVERSATION',primary:true,click:()=>api().open_url(c.url)},
    {label:'SAVE',click:()=>setDecision('conversation',c.id,'keep')},
    {label:'NOT FOR ME',click:()=>setDecision('conversation',c.id,'not_for_me')}
  ]});
}
async function setDecision(type,id,decision,reason=''){
  await api().decision(type,id,decision,reason);
  scanState=await api().scan_state(); renderPeople(); renderConversations(); renderScanState();
  appState=await api().app_state(); updateQuiet();
  toast(`${decision.replaceAll('_',' ').toUpperCase()} recorded.`); closeDrawer();
}
function notForMe(id){
  const p=(scanState.people||[]).find(x=>x.id===id); if(!p)return;
  let selected='';
  const options=[
    ['spammy','SPAMMY'],['too_salesy','TOO SALESY'],['ragebait','RAGEBAIT'],['crypto_rubbish','CRYPTO RUBBISH'],
    ['low_quality','LOW QUALITY'],['not_builder','NOT A BUILDER'],['engagement_farmer','ENGAGEMENT FARMER'],
    ['too_corporate','TOO CORPORATE'],['other','OTHER']
  ];
  const content=`<div class="reasonPrompt"><div class="reasonHelp">Optional: tell Scout why. The account will be suppressed either way; the reason only nudges future scoring.</div><div class="reasonGrid">${options.map(([v,l])=>`<button type="button" data-reason="${v}">${l}</button>`).join('')}</div></div>`;
  showModal({kicker:'TASTE FEEDBACK',title:`Not for me // @${p.username}`,body:'Remove this person from future Scout results?',content,confirmText:'NOT FOR ME',onConfirm:async()=>{await setDecision('person',id,'not_for_me',selected);}});
  document.querySelectorAll('[data-reason]').forEach(b=>b.onclick=()=>{
    selected=b.dataset.reason||''; document.querySelectorAll('[data-reason]').forEach(x=>x.classList.toggle('selected',x===b));
  });
}

function confirmFollow(id){
  const p=(scanState.people||[]).find(x=>x.id===id); if(!p)return;
  showModal({kicker:'HUMAN AUTHORISATION',title:`Follow @${p.username}?`,body:appState?.mode==='live'?'Scout will send this follow to X only because you clicked it. No autonomous follow actions.':'Scout will never make this decision by itself. In Demo Mode the click updates only the local network.',confirmText:'YES — FOLLOW',onConfirm:async()=>{
    const r=await api().follow_person(id); if(r.ok){scanState.people=scanState.people.filter(x=>x.id!==id);renderPeople();renderScanState();updateNetworkStats(r.stats);networkRows=await api().network_rows();renderNetwork();toast(r.message);closeDrawer();}else{toast(r.message||'Follow failed.');}
  }});
}

async function resetSeen(){
  showModal({kicker:'PRESENTATION MEMORY',title:'Reset seen signals?',body:"This only clears the current mission profile's seen list. Rejected accounts, Following, Network and NEVER SHOW decisions remain intact.",confirmText:'RESET SEEN',onConfirm:async()=>{
    const r=await api().reset_seen(); appState=await api().app_state(); updateQuiet(); toast(`${r.removed} seen signal records reset for ${r.profile}.`);
  }});
}

async function refreshNetwork(){networkRows=await api().network_rows();renderNetwork();const s=await api().app_state();appState=s;updateNetworkStats(s.networkStats);}
function updateNetworkStats(stats={}){$('mutualMetric').textContent=fmt(stats.mutual||0);$('networkCount').textContent=String(stats.following||0).padStart(2,'0');}
function renderNetwork(){
  const root=$('networkStream'); const rows=networkRows.filter(r=>networkFilter==='ALL'||r.status===networkFilter);
  if(!rows.length){root.innerHTML='<div class="emptyState">NO NETWORK ROWS IN THIS ORBIT</div>';return;}
  root.innerHTML=rows.map(r=>{
    const cls=`status-${r.status.replaceAll(' ','-')}`; const age=r.ageDays==null?'—':`${r.ageDays}d`;
    const protectLabel=r.protected?'UNPROTECT':'PROTECT';
    return `<div class="orbitRow">
      <div class="orbitName"><strong>${esc(r.name||'@'+r.username)}</strong><span>@${esc(r.username)}</span></div>
      <span class="statusPill ${cls}">${esc(r.status)}</span>
      <span class="orbitAge">${age}</span>
      <span class="orbitRec">${esc(r.recommendation)}</span>
      <div class="rowActions">
        ${r.youFollow?`<button data-protect="${esc(r.username)}" data-value="${r.protected?'0':'1'}">${protectLabel}</button>`:''}
        ${r.youFollow&&r.status!=='MUTUAL'?`<button class="danger" data-unfollow="${esc(r.username)}">UNFOLLOW</button>`:''}
      </div>
    </div>`;
  }).join('');
  root.querySelectorAll('[data-protect]').forEach(b=>b.onclick=async()=>{const v=b.dataset.value==='1';const r=await api().set_protected(b.dataset.protect,v);toast(r.message);await refreshNetwork();});
  root.querySelectorAll('[data-unfollow]').forEach(b=>b.onclick=()=>confirmUnfollow(b.dataset.unfollow));
}
function confirmUnfollow(username){showModal({kicker:'HUMAN AUTHORISATION',title:`Unfollow @${username}?`,body:appState?.mode==='live'?'Scout is recommending only. You are authorising the real X unfollow with this click.':'Scout is recommending only. You are making the action. Demo Mode changes local state only.',confirmText:'YES — UNFOLLOW',danger:true,onConfirm:async()=>{const r=await api().unfollow(username);toast(r.message);await refreshNetwork();}})}

function openImport(){
  const content=`<div class="importGrid"><label>FOLLOWING LIST<textarea id="followingPaste" placeholder="Paste ugly copied X following list here..."></textarea></label><label>FOLLOWERS LIST<textarea id="followersPaste" placeholder="Paste copied X followers list here..."></textarea></label></div>`;
  showModal({kicker:'NETWORK INGEST',title:'Import Following + Followers',body:'Paste exactly what X gives you. Scout extracts @handles and x.com profile links.',content,confirmText:'IMPORT',onConfirm:async()=>{
    const r=await api().import_network($('followingPaste').value,$('followersPaste').value);networkRows=r.rows;renderNetwork();updateNetworkStats(r.stats);toast(`Imported ${r.followingImported} following / ${r.followersImported} followers.`);
  }});
}


async function refreshConnectionState(){
  const c=await api().x_connection_state();
  if(!appState) appState={};
  appState.connection=c; appState.mode=c.connected?'live':'demo';
  applyMode();
  return c;
}

function connectionMarkup(c={}){
  const account=c.account||{}; const status=c.connected?'LIVE':(c.connecting?'WAITING FOR X':'NOT CONNECTED');
  const statusClass=c.connected?'live':(c.error?'error':'');
  const scopes=(c.scopes||[]).map(x=>`<span>${esc(x)}</span>`).join('');
  return `<div class="xConnectPanel">
    <div class="xConnectionHero"><div><strong>${c.connected?`@${esc(account.username||'connected')}`:'Connect your own X Developer App'}</strong><span>Each Scout install talks directly from this computer to X. No Scout server, no shared account, no Darren backend.</span></div><div class="connectionState ${statusClass}" id="xConnectionStateLabel">${status}</div></div>
    <div class="connectionSteps"><span><b>01</b>Create your own app at console.x.com</span><span><b>02</b>Enable OAuth 2.0 as a Native App / public client</span><span><b>03</b>Add the callback URL below</span><span><b>04</b>Paste the Client ID and connect</span></div>
    <div class="connectionField"><label>OAuth 2.0 Client ID</label><input id="xClientId" value="${esc(c.clientId||'')}" placeholder="Paste the Client ID from console.x.com" autocomplete="off" spellcheck="false"></div>
    <div class="callbackField"><label>Callback URL — add this exact URL to your X App OAuth settings</label><div class="callbackBox"><code id="xCallbackUrl">${esc(c.redirectUri||'http://127.0.0.1:8765/callback')}</code><button class="linkLike" id="copyCallback">COPY</button></div></div>
    <div><div class="micro">SCOPES SCOUT REQUESTS</div><div class="connectionScopes">${scopes}</div></div>
    <div class="connectionButtons"><button class="primary" id="xConnectGo">CONNECT X</button><button class="ghost" id="xTestGo">TEST LINK</button><button class="ghost" id="xDeveloperConsole">OPEN X CONSOLE</button>${c.connected?'<button class="ghost danger" id="xDisconnectGo">DISCONNECT</button>':''}</div>
    <div class="connectionStatusLine ${statusClass}" id="xConnectionMessage">${esc(c.error|| (c.connected?'OAuth link is ready. START SCOUT now uses live X data.':'Paste your Client ID, then press CONNECT X.'))}</div>
    <div class="connectionNote"><strong>Local-only credential path.</strong> Client ID is saved in Scout's local data folder. On Windows, OAuth tokens are protected with Windows DPAPI for the current Windows user. Scout never asks for your X password and nothing is sent through an X Scout server. API credits stay with your own X Developer account.</div>
  </div>`;
}

async function openXConnection(){
  const c=await api().x_connection_state();
  showModal({kicker:'LOCAL X LINK',title:'Connect X Scout',body:'Bring your own X Developer App. One connection per local Scout install.',content:connectionMarkup(c)});
  wireConnectionModal(c);
}

function wireConnectionModal(c){
  const client=$('xClientId');
  $('copyCallback')?.addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('xCallbackUrl').textContent);toast('Callback URL copied.');}catch{toast('Copy failed — select the callback URL manually.');}});
  $('xDeveloperConsole')?.addEventListener('click',()=>api().open_external('https://console.x.com/'));
  $('xConnectGo')?.addEventListener('click',async()=>{
    const btn=$('xConnectGo'); btn.disabled=true;
    const r=await api().x_begin_connect(client.value.trim());
    $('xConnectionMessage').textContent=r.message||''; $('xConnectionMessage').classList.toggle('error',!r.ok);
    btn.disabled=false;
    if(!r.ok){toast(r.message);return;}
    toast('Browser opened — authorise X Scout.');
    pollConnectionModal();
  });
  $('xTestGo')?.addEventListener('click',async()=>{
    const r=await api().x_test_connection();
    $('xConnectionMessage').textContent=r.message||''; $('xConnectionMessage').classList.toggle('error',!r.ok);
    if(r.ok){toast(r.message);await refreshConnectionState();}
  });
  $('xDisconnectGo')?.addEventListener('click',()=>showModal({kicker:'LOCAL X LINK',title:'Disconnect X?',body:'This removes the local OAuth tokens from this computer. Your X Developer App is not deleted.',confirmText:'DISCONNECT',danger:true,onConfirm:async()=>{const r=await api().x_disconnect();toast(r.message);appState=await api().app_state();applyMode();}}));
}

async function pollConnectionModal(){
  for(let i=0;i<300;i++){
    await new Promise(r=>setTimeout(r,800));
    const c=await refreshConnectionState();
    const label=$('xConnectionStateLabel'); const msg=$('xConnectionMessage');
    if(label){label.textContent=c.connected?'LIVE':(c.connecting?'WAITING FOR X':(c.error?'LINK FAILED':'NOT CONNECTED'));label.className=`connectionState ${c.connected?'live':(c.error?'error':'')}`;}
    if(msg){msg.textContent=c.error|| (c.connected?`Connected as @${c.account?.username||''}`:'Waiting for browser authorisation…');msg.className=`connectionStatusLine ${c.connected?'live':(c.error?'error':'')}`;}
    if(c.connected){
      if(msg){msg.textContent='X connected. Synchronising your Following + Followers…';msg.className='connectionStatusLine live';}
      const sync=await api().x_sync_network();
      if(sync.ok){networkRows=sync.rows||[];renderNetwork();updateNetworkStats(sync.stats||{});}
      toast(sync.ok?`X LINK LIVE // @${c.account?.username||''} // network synced`:`X LINK LIVE // @${c.account?.username||''}`);
      appState=await api().app_state();applyMode();return;
    }
    if(c.error){toast(c.error);return;}
  }
}

async function syncNetworkFromX(){
  if(appState?.mode!=='live'){toast('Connect X first.');openXConnection();return;}
  const btn=$('syncNetwork'); btn.disabled=true; btn.textContent='SYNCING…';
  const r=await api().x_sync_network();
  btn.disabled=false; btn.textContent='SYNC X';
  if(!r.ok){toast(r.message);return;}
  networkRows=r.rows||[]; renderNetwork(); updateNetworkStats(r.stats||{}); toast(r.message);
}

function drawStars(){
  const c=$('stars'),ctx=c.getContext('2d');let stars=[];
  function resize(){c.width=innerWidth*devicePixelRatio;c.height=innerHeight*devicePixelRatio;c.style.width=innerWidth+'px';c.style.height=innerHeight+'px';ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);stars=Array.from({length:150},()=>({x:Math.random()*innerWidth,y:Math.random()*innerHeight,r:Math.random()*1.2+.2,a:Math.random()*.45+.08,v:Math.random()*.04+.01}));}
  function frame(){ctx.clearRect(0,0,innerWidth,innerHeight);for(const s of stars){s.a+=s.v;if(s.a>.58||s.a<.08)s.v*=-1;ctx.fillStyle=`rgba(120,205,235,${s.a})`;ctx.beginPath();ctx.arc(s.x,s.y,s.r,0,Math.PI*2);ctx.fill()}requestAnimationFrame(frame)}
  resize();addEventListener('resize',resize);frame();
}

// Wiring -----------------------------------------------------------------
document.querySelectorAll('.navRail button').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
$('profileSelect').addEventListener('change',onProfileChange); $('startScout').addEventListener('click',startScan); $('resetSeen').addEventListener('click',resetSeen);
$('openTopicFinder').addEventListener('click',()=>switchView('profiles')); $('analyseTopics').addEventListener('click',analyseTopics); $('saveProfile').addEventListener('click',saveProfile);
$('loadActiveProfile').addEventListener('click',async()=>{const p=await api().profile($('profileSelect').value);applyProfile(p);toast('Active profile loaded.');});
$('drawerClose').addEventListener('click',closeDrawer); $('modalClose').addEventListener('click',closeModal); $('modalBackdrop').addEventListener('click',e=>{if(e.target===$('modalBackdrop'))closeModal()});
$('openXConnection').addEventListener('click',openXConnection); $('syncNetwork').addEventListener('click',syncNetworkFromX);
$('importNetwork').addEventListener('click',openImport); $('refreshNetwork').addEventListener('click',refreshNetwork);
$('networkFilters').querySelectorAll('button').forEach(b=>b.onclick=()=>{networkFilter=b.dataset.filter;$('networkFilters').querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));renderNetwork();});

document.addEventListener('pywebviewready', init);
// Makes the static HTML still look intentional if opened outside pywebview.
setTimeout(()=>{if(!window.pywebview){drawStars();$('missionQuiet').textContent='Launch through python launch.py to connect the local Python engine.';}},400);
