/* Same-origin remote browser: image pixels and input share the agent's session. */
(() => {
 let dialog, session, job, timer, imageUrl, polling=false, generation=0, inputQueue=Promise.resolve(),pendingInput=[],inputTimer,streaming=false,streamRetry,loadedDraft='',draftBinding=null,shownCommandError=0,pointerActive=null,lastPointerDown=null,frameViewer,visibleFeedbackMs=null,inputTimes=new Map();
 const find=sel=>dialog.querySelector(sel);
 function error(message=''){find('#agent-error').textContent=message;}
  function setSession(value){if(value){session=value;renderState();}}
  function deliver(action,extra={}){const ident=session.id;const delivered=inputQueue.then(()=>sendNow(ident,action,extra));inputQueue=delivered.then(()=>{});return delivered;}
  function flushInput(){clearTimeout(inputTimer);if(!pendingInput.length||!session)return inputQueue;const events=pendingInput;pendingInput=[];return deliver('input',{events,compact:true});}
  function input(event){if(session?.state!=='manual')return;if(event.type==='text'&&(event.text.length>15000)){error('Paste up to 15,000 characters at a time.');return;}if(event.type==='text'&&pendingInput.reduce((n,e)=>n+(e.text?.length||0),0)+event.text.length>15000)flushInput();const last=pendingInput.at(-1);if(last?.type==='text'&&event.type==='text'&&last.text.length+event.text.length<=15000)last.text+=event.text;else if(last?.type==='scroll'&&event.type==='scroll')last.delta=Math.max(-1800,Math.min(1800,last.delta+event.delta));else if(last?.type==='mouse'&&last.phase==='move'&&event.type==='mouse'&&event.phase==='move')pendingInput[pendingInput.length-1]=event;else pendingInput.push(event);clearTimeout(inputTimer);if(pendingInput.length>=48||(event.type==='mouse'&&event.phase!=='move')||event.type==='click')flushInput();else inputTimer=setTimeout(flushInput,event.type==='text'?12:8);}
  function send(action,extra={}){if(['click','text','key','scroll'].includes(action)){input({type:action,...extra});return inputQueue;}if(['takeover','resume','review','approve','stop'].includes(action))releasePointer();flushInput();const delivered=deliver(action,extra);return ['read_fields','select_field','draft_answer','insert_answer'].includes(action)?delivered.then(waitProcessed):delivered.then(Boolean);}
  async function waitProcessed(out){if(!out)return false;const epoch=generation,ident=session.id,deadline=Date.now()+12000;try{while(Date.now()<deadline&&epoch===generation&&session?.id===ident&&dialog.open){const current=await api('/api/center/browser?session_id='+encodeURIComponent(ident));if(epoch!==generation)return false;setSession(current.session);const result=current.session.command_results?.find(c=>c.id===out.command_id);if(result){if(!result.ok)throw new Error(result.error);return true;}await new Promise(resolve=>setTimeout(resolve,25));}throw new Error('Action is still pending. Check the live view before retrying.');}catch(exc){if(epoch===generation&&dialog.open)error(exc.message);return false;}}
  async function sendNow(ident,action,extra={}){
  if(session?.id===ident&&dialog.open)error();
   try{const started=performance.now();const out=await api('/api/center/browser/command',{session_id:ident,action,...extra});if(session?.id===ident&&dialog.open){setSession(out.session);if(action==='input'){inputTimes.set(out.command_id,started);while(inputTimes.size>64)inputTimes.delete(inputTimes.keys().next().value);}}
    return out;
   }catch(exc){if(session?.id===ident&&dialog.open)error(exc.message);return false;}
  }
  function renderQuestion(){
   const context=find('#agent-question-context');if(!context)return;
   const field=session.focused_field;
   context.textContent=field?field.label:'Click an answer field in the live view, or read the page fields below.';
   find('#agent-question-help').textContent=field?.help||'';
   const choice=find('#agent-field-choice'),selected=choice.value,options='<option value="">Choose a page field…</option>'+(session.question_fields||[]).filter(f=>f.writable).map(f=>`<option value="${esc(f.token)}">${esc(f.label.slice(0,110))}${f.has_value?' · has answer':''}</option>`).join('');
   if(choice.innerHTML!==options){choice.innerHTML=options;if([...choice.options].some(o=>o.value===selected))choice.value=selected;}
   if(field&&[...choice.options].some(o=>o.value===field.token))choice.value=field.token;
   const draft=session.answer_draft;
   find('#agent-draft').disabled=!field?.writable||draft?.status==='pending';
   find('#agent-draft').textContent=draft?.status==='pending'?'Drafting · browser still usable…':'Draft an answer from my evidence';
   if(draft&&draft.status!=='pending'){
    if(loadedDraft!==draft.id){loadedDraft=draft.id;draftBinding={token:draft.field_token,value_hash:draft.value_hash};if(draft.answer)find('#agent-text').value=draft.answer;}
    find('#agent-draft-evidence').innerHTML=`<p class="hint">Draft for: ${esc(draft.question)}</p>${(draft.sources||[]).map(s=>`<details><summary>${esc(s.label)}</summary><p>${esc(s.quote)}</p></details>`).join('')}${(draft.missing||[]).map(m=>`<p class="hint">${esc(m)}</p>`).join('')}`;
   }
   const performance=session.performance||{};find('#agent-performance').textContent=`${performance.pending_commands||0} actions queued${performance.dispatch_ms!==null&&performance.dispatch_ms!==undefined?' · browser '+Math.round(performance.dispatch_ms)+' ms · queue '+Math.round(performance.queue_ms||0)+' ms':''}${visibleFeedbackMs!==null?' · visible '+Math.round(visibleFeedbackMs)+' ms':''} · ${streaming?'decoded live frames':'image fallback'}`;
   const failed=(session.command_results||[]).findLast(c=>!c.ok);if(failed&&failed.id>shownCommandError){shownCommandError=failed.id;error(failed.error);}
  }
 function renderState(){
  if(!session)return;
  const ended=['confirmed','uncertain','stopped','failed'].includes(session.state),manual=session.state==='manual',review=session.state==='review';
  find('#agent-state').textContent=label(session.state)+(session.steps?' · page step '+session.steps:'');
  find('#agent-progress').textContent=session.detail;
  const diagnostic=find('#agent-diagnostic');if(diagnostic){diagnostic.hidden=!session.last_error;diagnostic.textContent=session.last_error?`Diagnostic: ${label(session.last_error.category)} · ${label(session.last_error.operation)}`:'';}
  const cookies=find('#agent-cookies');if(cookies){cookies.hidden=!session.cookie_controls?.length||session.state!=='manual';const content=(session.cookie_controls||[]).map(c=>`<button data-cookie-choice="${esc(c.choice)}">${esc(c.label)}</button>`).join('');if(cookies.innerHTML!==content){cookies.innerHTML=content;cookies.querySelectorAll('[data-cookie-choice]').forEach(b=>b.onclick=()=>send('cookie',{choice:b.dataset.cookieChoice}));}}
  find('#agent-address').textContent=session.page_url||'Opening application…';
  find('#agent-image').dataset.manual=String(manual);
  find('#agent-takeover').hidden=ended||manual||session.state==='submitting';
  find('#agent-resume').hidden=!manual;
  find('#agent-review-button').hidden=ended||session.state==='submitting'||review;
  find('#agent-approve').hidden=!review;
  find('#agent-stop').hidden=ended;
  find('#agent-input').hidden=!manual;
  find('#agent-answers').hidden=!review;
  const answers=(session.review_summary||[]).map(a=>`<p><strong>${esc(a.label)}</strong>${esc(a.value)}</p>`).join('');
  if(find('#agent-answers').innerHTML!==answers)find('#agent-answers').innerHTML=answers;
  find('#agent-evidence').hidden=session.state!=='confirmed';
  find('#agent-evidence').href='/api/center/browser/evidence?id='+job.id;
  const upload=find('#agent-document-upload'),chosen=upload.value,options=(session.documents||[]).map(k=>`<option value="${esc(k)}">${esc(label(k))}</option>`).join('');if(upload.innerHTML!==options){upload.innerHTML=options;if((session.documents||[]).includes(chosen))upload.value=chosen;}
  find('#agent-steps').innerHTML=['running','manual','review','confirmed'].map((s,i)=>`<span class="${session.state===s?'active':''}">${i+1} / ${['Prepare','Takeover','Approve','Confirmation'][i]}</span>`).join('');
  const observation=session.decision_observation,output=find('#agent-observation');
  if(output){output.textContent=!observation||observation.status==='disabled'?'Trial off. Enable in Settings → AI preferences.':observation.status==='completed'?`Luna: ${label(observation.prediction.page_state)} · baseline: ${label(observation.baseline.page_state)} · ${Math.round(observation.latency_ms)} ms. Observation only; no actions taken.`:`${label(observation.status)}${observation.category?' · '+label(observation.category):''}. Observation only.`;}
  if(output&&observation?.status==='completed'&&typeof observation.prediction.page_state_confidence==='number')output.textContent+=` Page confidence: ${Math.round(observation.prediction.page_state_confidence*100)}%.`;
   if(ended)clearTimeout(timer);
   renderQuestion();
 }
 async function poll(){
  if(!session||!dialog.open||polling)return;
  polling=true;const epoch=generation,ident=session.id;
  try{
   const out=await api('/api/center/browser?session_id='+encodeURIComponent(ident));
   if(epoch!==generation||!dialog.open)return;
   setSession(out.session);
    const response=streaming?null:await fetch('/api/center/browser/screenshot?session_id='+encodeURIComponent(ident),{cache:'no-store'});
    if(response?.ok){const blob=await response.blob();if(epoch!==generation||!dialog.open)return;const previous=imageUrl;imageUrl=URL.createObjectURL(blob);find('#agent-image').src=imageUrl;find('#agent-image').dataset.transport='snapshot';find('#agent-image').hidden=false;find('#agent-canvas').hidden=true;find('#agent-loading').hidden=true;if(previous)URL.revokeObjectURL(previous);}
  }catch(exc){if(epoch===generation)error('Live browser connection lost: '+exc.message);}
    finally{polling=false;clearTimeout(timer);if(dialog.open&&session&&!document.hidden&&(!['confirmed','uncertain','stopped','failed'].includes(session.state)||session.decision_observation?.status==='pending'))timer=setTimeout(poll,streaming?120:100);}
 }
  function stopStream(){frameViewer?.stop();frameViewer=null;streaming=false;}
  function startStream(){
   if(!dialog?.open||!session||document.hidden||['confirmed','uncertain','stopped','failed'].includes(session.state))return;
   stopStream();const epoch=generation,ident=session.id,image=find('#agent-image');streaming=true;
   const failed=()=>{if(epoch!==generation||!dialog.open)return;streaming=false;find('#agent-canvas').hidden=true;clearTimeout(streamRetry);if(!document.hidden)streamRetry=setTimeout(startStream,1500);};
   frameViewer=new BrowserFrameView.FrameViewer(find('#agent-canvas'),frame=>{
    if(epoch!==generation||!dialog.open||session?.id!==ident)return;
    if(!image.getAttribute('src')){const previous=imageUrl;imageUrl=URL.createObjectURL(frame.blob);image.src=imageUrl;if(previous)URL.revokeObjectURL(previous);}
    image.hidden=false;image.dataset.transport='decoded_frames';find('#agent-loading').hidden=true;
    if(inputTimes.has(frame.inputId)){visibleFeedbackMs=frame.paintedAt-inputTimes.get(frame.inputId);for(const id of inputTimes.keys())if(id<=frame.inputId)inputTimes.delete(id);}
   });
   frameViewer.onError=failed;
   frameViewer.start('/api/center/browser/stream?session_id='+encodeURIComponent(ident)+'&view='+generation).catch(exc=>{if(exc.name!=='AbortError')failed();});
  }
  function live(){
  find('#agent-body').innerHTML=`<div class="agent-layout"><div class="agent-screen"><div class="agent-address"><span class="agent-live">SHARED SESSION</span><span id="agent-address"></span></div><div class="agent-viewport"><span id="agent-loading" class="hint">Starting Chromium…</span><img id="agent-image" hidden tabindex="0" alt="Live employer application. Take over to click and type." draggable="false"></div><div id="agent-steps" class="agent-steps"></div></div><aside class="agent-side"><span class="eyebrow">YOU + YOUR AGENT</span><h3 id="agent-state"></h3><p id="agent-progress" class="agent-checkpoint" role="status"></p><div class="actions"><button id="agent-takeover">Take over</button><button id="agent-resume" class="primary">Resume agent</button><button id="agent-review-button">Request final review</button><button id="agent-approve" class="primary" hidden>Approve & submit once</button><a id="agent-evidence" hidden target="_blank" rel="noopener">View saved confirmation ↗</a><button id="agent-stop" class="danger">Stop session</button></div><div id="agent-input" hidden><p class="hint">Click a field in the live view, then type. Tab moves between fields. Paste long answers below.</p><label>Text to insert<textarea id="agent-text" autocomplete="off" placeholder="Paste an answer or supporting statement…"></textarea></label><button id="agent-insert">Insert into focused field</button><div class="agent-controls">${[['Tab','Tab'],['Shift+Tab','Shift + Tab'],['Backspace','⌫'],['Control+A','Select all'],['Enter','Enter'],['Space','Space'],['Escape','Escape']].map(([key,title])=>`<button data-agent-key="${key}">${title}</button>`).join('')}<button id="agent-up">Scroll ↑</button><button id="agent-down">Scroll ↓</button></div><label>Reviewed document<select id="agent-document-upload"></select></label><button id="agent-upload">Upload to next empty file field</button></div><div id="agent-answers" class="agent-review" hidden></div><p id="agent-error" class="agent-error" role="alert"></p><p class="hint">Forms use your saved profile, verified answers and reviewed documents. Closing this view pauses the agent; sessions expire after 15 minutes away.</p></aside></div>`;
  find('.agent-side').insertAdjacentHTML('beforeend','<details class="agent-observer"><summary>Decisions observer · experimental</summary><p id="agent-observation" class="hint"></p><p class="hint">View benchmark, agreement and usage results in Settings → AI preferences.</p></details>');
  find('#agent-progress').insertAdjacentHTML('afterend','<p id="agent-diagnostic" class="hint" hidden></p>');
   find('#agent-diagnostic').insertAdjacentHTML('afterend','<div id="agent-cookies" class="actions" hidden></div>');
   find('#agent-input').insertAdjacentHTML('afterbegin','<section class="agent-question-panel"><span class="eyebrow">ANSWER THE QUESTION IN FRONT OF YOU</span><p id="agent-question-context" class="agent-selected-question"></p><p id="agent-question-help" class="hint"></p><div class="agent-field-picker"><button id="agent-read-fields">Read page fields</button><select id="agent-field-choice" aria-label="Selected answer field"><option value="">Choose a page field…</option></select></div><label>Draft guidance (optional)<input id="agent-guidance" maxlength="1000" placeholder="For example: keep it to three sentences"></label><button id="agent-draft" class="primary">Draft an answer from my evidence</button><div id="agent-draft-evidence" class="agent-draft-evidence" aria-live="polite"></div></section>');
   find('#agent-text').placeholder='Review and edit your evidence-backed draft, or write your own answer…';
   find('#agent-insert').insertAdjacentHTML('beforebegin','<label class="agent-check"><input id="agent-replace" type="checkbox"> Replace an existing answer in this field</label><label class="agent-check"><input id="agent-save-answer" type="checkbox"> I verified this answer; save it for reuse</label>');
   find('#agent-error').insertAdjacentHTML('afterend','<p id="agent-performance" class="hint" aria-live="off"></p>');
   find('#agent-read-fields').onclick=()=>{draftBinding=null;return send('read_fields');};
   find('#agent-field-choice').onchange=e=>{if(e.target.value)send('select_field',{field_token:e.target.value});};
   find('#agent-draft').onclick=()=>{const field=session.focused_field;if(!field){error('Click an answer field or choose one from Read page fields.');return;}send('draft_answer',{field_token:field.token,guidance:find('#agent-guidance').value});};
  find('#agent-takeover').onclick=()=>send('takeover');find('#agent-resume').onclick=()=>send('resume');find('#agent-review-button').onclick=()=>send('review');
  find('#agent-approve').onclick=async e=>{e.target.disabled=true;try{await send('approve',{approval_token:session.approval_token});}finally{e.target.disabled=false;}};
  find('#agent-stop').onclick=()=>send('stop');
   find('#agent-insert').onclick=async e=>{const field=session.focused_field;if(!field?.writable){error('Select a writable answer field in the live view first.');return;}const input=find('#agent-text'),value=input.value;const hash=draftBinding?.token===field.token?draftBinding.value_hash:field.value_hash;e.target.disabled=true;try{const ok=await send('insert_answer',{field_token:field.token,value_hash:hash,text:value,replace:find('#agent-replace').checked,save_verified:find('#agent-save-answer').checked});if(ok){input.value='';draftBinding=null;find('#agent-replace').checked=false;notify('Answer inserted into the selected field.');}}finally{e.target.disabled=false;}};
  find('#agent-up').onclick=()=>send('scroll',{delta:-650});find('#agent-down').onclick=()=>send('scroll',{delta:650});
  find('#agent-upload').onclick=()=>send('upload',{kind:find('#agent-document-upload').value});
  dialog.querySelectorAll('[data-agent-key]').forEach(button=>button.onclick=()=>send('key',{key:button.dataset.agentKey}));
   const image=find('#agent-image');image.width=session.width;image.height=session.height;
   image.insertAdjacentHTML('afterend','<canvas id="agent-canvas" width="1280" height="900" aria-hidden="true" hidden></canvas>');
   const point=e=>{const rect=image.getBoundingClientRect();return {x:Math.max(0,Math.min(session.width-1,Math.round((e.clientX-rect.left)*session.width/rect.width))),y:Math.max(0,Math.min(session.height-1,Math.round((e.clientY-rect.top)*session.height/rect.height)))};};
   const modifiers=e=>(e.altKey?1:0)|(e.ctrlKey?2:0)|(e.metaKey?4:0)|(e.shiftKey?8:0);
   image.onpointerdown=e=>{if(session.state!=='manual'||!e.isPrimary||pointerActive||![0,1,2].includes(e.button))return;e.preventDefault();image.focus({preventScroll:true});const position=point(e),now=performance.now();const count=lastPointerDown&&lastPointerDown.button===e.button&&now-lastPointerDown.at<450&&Math.hypot(position.x-lastPointerDown.x,position.y-lastPointerDown.y)<5?Math.min(3,lastPointerDown.count+1):1;lastPointerDown={...position,button:e.button,at:now,count};pointerActive={...position,button:['left','middle','right'][e.button],buttons:[1,4,2][e.button],click_count:count,pointerId:e.pointerId};image.setPointerCapture(e.pointerId);input({type:'mouse',phase:'down',...position,button:pointerActive.button,buttons:pointerActive.buttons,click_count:count,modifiers:modifiers(e)});};
   image.onpointermove=e=>{if(session.state!=='manual'||!e.isPrimary)return;const position=point(e);if(pointerActive)Object.assign(pointerActive,position);input({type:'mouse',phase:'move',...position,button:pointerActive?.button||'none',buttons:pointerActive?.buttons||0,modifiers:modifiers(e)});};
   image.onpointerup=e=>{if(!pointerActive||['left','middle','right'][e.button]!==pointerActive.button)return;const position=point(e),active=pointerActive;pointerActive=null;input({type:'mouse',phase:'up',...position,button:active.button,buttons:0,click_count:active.click_count,modifiers:modifiers(e)});if(image.hasPointerCapture(e.pointerId))image.releasePointerCapture(e.pointerId);};
   image.onpointercancel=image.onlostpointercapture=()=>releasePointer();
   image.oncontextmenu=image.ondblclick=e=>e.preventDefault();
   image.onkeydown=e=>{if(session.state!=='manual'||['Shift','Control','Meta','Alt'].includes(e.key))return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='v')return;e.preventDefault();if(e.key===' '&&!e.ctrlKey&&!e.metaKey){send('key',{key:'Space'});return;}if(e.key.length===1&&!e.ctrlKey&&!e.metaKey&&!e.altKey){send('text',{text:e.key});return;}let key=e.key;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='a')key='Control+A';else if(e.shiftKey&&e.key==='Tab')key='Shift+Tab';if(['Tab','Shift+Tab','Backspace','Delete','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','Control+A','Meta+A','Enter','Escape','Space'].includes(key))send('key',{key});};
  image.onpaste=e=>{if(session.state==='manual'){e.preventDefault();send('text',{text:e.clipboardData.getData('text')});}};
   image.addEventListener('wheel',e=>{if(session.state==='manual'){e.preventDefault();input({type:'mouse',phase:'move',...point(e),button:pointerActive?.button||'none',buttons:e.buttons||0,modifiers:modifiers(e)});send('scroll',{delta:Math.round(e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?session.height:1))});}},{passive:false});
  renderState();startStream();poll();
 }
  function packetScreen(packet,previous,selected){
   const reasons=[...(packet.missing||[]),...(packet.excluded||[])];
   const descriptions={ready:'PDF ready',missing:'Text saved · PDF missing',stale:'Text saved · PDF out of date',not_included:'Not included',source_missing:'Draft missing',source_empty:'Draft is empty',unsupported_format:'Unsupported document format',unavailable:'Document unavailable'};
   find('#agent-body').innerHTML=`<section class="agent-packet"><span class="eyebrow">01 / PREPARE & APPROVE YOUR PACKET</span><h3>${packet.ready?'Ready to put your best work forward.':'Prepare your application packet.'}</h3>${previous?`<p class="agent-checkpoint">Previous session: ${esc(previous.detail)}</p>`:''}<p>The agent will open this application, fill from your saved evidence, and pause whenever it needs you. Watch every step in the shared browser.</p><div class="agent-packet-list">${packet.documents.map(d=>{const link='/api/center/browser/document?id='+selected.id+'&kind='+encodeURIComponent(d.kind);return `<article class="agent-packet-document"><p><strong>${esc(d.label)}<small>${d.required?'Required':'Optional'}</small></strong><span class="${d.pdf_state==='ready'?'':'muted'}">${esc(descriptions[d.pdf_state]||d.pdf_state)}</span></p><div class="agent-document-links">${d.text_ready?`<a href="${link}&format=html" target="_blank" rel="noopener">Preview text ↗</a>`:''}${d.pdf_state==='ready'?`<a href="${link}&format=pdf" target="_blank" rel="noopener">View / download PDF ↗</a>`:''}</div></article>`;}).join('')}<p class="hint">Review each included document. PDF preparation renders the saved text without rewriting it.</p></div>${reasons.length?`<div class="agent-packet-blockers" role="status"><strong>Still needed</strong><ul>${reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul></div>`:''}<div class="actions"><button id="agent-prepare" ${packet.can_prepare?'':'hidden'}>Prepare current PDFs</button><button id="agent-packet-refresh">Refresh readiness</button></div><label><input id="agent-documents-approved" type="checkbox" ${packet.ready?'':'disabled'}> I have reviewed and approve the included CV, cover letter and supporting statement.</label><p class="hint">Final submission has its own approval checkpoint. Login, security checks and new questions stay with you.</p><button id="agent-start" class="primary" ${packet.ready?'':'disabled'}>Open application with agent</button><p id="agent-error" class="agent-error" role="alert"></p>${previous?.state==='confirmed'?`<a href="/api/center/browser/evidence?id=${selected.id}" target="_blank" rel="noopener">View saved confirmation ↗</a>`:''}</section>`;
    find('#agent-body').querySelectorAll('a[href^="/api/center/browser/document"]').forEach(link=>{const url=new URL(link.href);url.searchParams.set('country_code',selected.country_code||'GB');link.href=url.href;});
    find('#agent-prepare').onclick=async e=>{const epoch=generation;e.target.disabled=true;e.target.textContent='Preparing PDFs…';try{const result=await api('/api/center/browser/prepare',{id:selected.id});if(epoch!==generation||!dialog.open)return;packetScreen(result.packet,previous,selected);if(result.errors.length)error(result.errors.map(item=>label(item.kind)+': '+item.error).join('; '));else notify(result.rendered.length?'PDFs prepared. Review the packet and approve it to continue.':'Your PDFs are already current.');}catch(exc){if(epoch===generation&&dialog.open)error(exc.message);}finally{if(e.target.isConnected){e.target.disabled=false;e.target.textContent='Prepare current PDFs';}}};
   find('#agent-packet-refresh').onclick=async e=>{e.target.disabled=true;try{await open(selected);}catch(exc){if(dialog.open)error(exc.message);}finally{if(e.target.isConnected)e.target.disabled=false;}};
   find('#agent-start').onclick=async e=>{e.target.disabled=true;const epoch=generation;try{if(!find('#agent-documents-approved').checked)throw new Error('Review and approve your documents first.');const result=await api('/api/center/browser/start',{id:selected.id,documents_approved:true,packet_token:packet.packet_token});if(epoch!==generation||!dialog.open)return;session=result.session;live();}catch(exc){if(epoch===generation&&dialog.open)error(exc.message);}finally{if(e.target.isConnected)e.target.disabled=!packet.ready;}};
  }
  async function open(selected){
    releasePointer();flushInput();stopStream();const epoch=++generation;clearTimeout(timer);clearTimeout(streamRetry);pendingInput=[];job=selected;session=null;loadedDraft='';draftBinding=null;shownCommandError=0;visibleFeedbackMs=null;inputTimes.clear();
   if(!dialog){dialog=document.createElement('dialog');dialog.className='agent-dialog';dialog.setAttribute('aria-labelledby','agent-title');dialog.innerHTML='<header class="agent-header"><div><span class="eyebrow">ASSISTED APPLICATION / LIVE WORKSPACE</span><h2 id="agent-title"></h2><p id="agent-employer"></p></div><button id="agent-close" class="quiet">Close ×</button></header><div id="agent-body"></div>';document.body.append(dialog);find('#agent-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{flushInput();generation++;clearTimeout(timer);clearTimeout(streamRetry);streaming=false;const image=find('#agent-image');if(image)image.src='';if(session&&!['confirmed','uncertain','stopped','failed','submitting'].includes(session.state))send('takeover');if(imageUrl){URL.revokeObjectURL(imageUrl);imageUrl=null;}refresh().catch(()=>{});});}
  find('#agent-title').textContent=selected.title;find('#agent-employer').textContent=selected.company||selected.site||'';
  find('#agent-body').innerHTML='<section class="agent-packet"><p>Checking your browser session…</p><p id="agent-error" role="alert"></p></section>';
  if(!dialog.open)dialog.showModal();
   const out=await api('/api/center/browser?id='+selected.id);
   if(epoch!==generation||!dialog.open)return;
  if(out.session&&!['confirmed','uncertain','stopped','failed'].includes(out.session.state)){session=out.session;live();return;}
   packetScreen(out.packet,out.session,selected);
   if(selected.apply_status==='submission_uncertain')find('#agent-start').insertAdjacentHTML('beforebegin',`<button data-reconcile-submission="${selected.id}" class="primary">Reconcile submission</button>`);
 }
   function releasePointer(){if(!pointerActive)return;const active=pointerActive;pointerActive=null;input({type:'mouse',phase:'cancel',x:active.x,y:active.y,button:active.button,buttons:0,click_count:active.click_count});}
   window.AssistedBrowser={open};
   window.addEventListener('blur',()=>{releasePointer();flushInput();});
   document.addEventListener('visibilitychange',()=>{if(!dialog?.open||!session)return;if(document.hidden){releasePointer();flushInput();clearTimeout(timer);clearTimeout(streamRetry);stopStream();}else{startStream();poll();}});
   document.addEventListener('close',e=>{if(e.target===dialog){releasePointer();flushInput();stopStream();}},true);
})();

(() => {
 async function open(id){
  const ctx=await api('/api/center/submission/reconciliation?id='+id);
  if(!ctx.uncertain){notify('This submission is already resolved. Reload the application record.');return;}
  const dialog=document.createElement('dialog');dialog.className='lifecycle-dialog';dialog.setAttribute('aria-labelledby','reconciliation-title');
  dialog.innerHTML=`<header class="dialog-top"><span class="eyebrow">VERIFY THE EMPLOYER OUTCOME</span><button id="reconciliation-close" class="quiet">Close ×</button></header><h2 id="reconciliation-title">Reconcile this submission.</h2><p>Open the employer portal’s My applications page and check your confirmation email. Use the actual portal/receipt outcome; the current “uncertain” record is not proof that it failed.</p>${ctx.has_attempt_image?`<p><a href="/api/center/submission/attempt-image?id=${id}" target="_blank" rel="noopener">View saved last-attempt image ↗</a></p>`:''}<label>What did you verify?<select id="reconciliation-outcome"><option value="unknown">Still unsure — keep the retry lock</option><option value="submitted">Confirmed submitted / employer received it</option><option value="not_submitted">Confirmed not submitted / draft or validation failure</option></select></label><label>Evidence source<select id="reconciliation-source">${Object.entries(ctx.sources).map(([value,title])=>`<option value="${value}">${esc(title)}</option>`).join('')}</select></label><label>What does the evidence show?<textarea id="reconciliation-evidence" rows="4" maxlength="3000" placeholder="For example: portal shows Submitted with reference X; or employer confirmed no application was received. Missing email alone is insufficient."></textarea></label><label>Application reference (if provided)<input id="reconciliation-reference" maxlength="300"></label><label id="reconciliation-date-label" hidden>Submission date from receipt (optional)<input id="reconciliation-date" type="datetime-local"></label><label><input id="reconciliation-checked" type="checkbox">I checked the employer portal/receipt and the selected outcome is accurate.</label><p id="reconciliation-guidance" class="hint">Still unsure keeps the existing lock. You can contact the employer and save another check later.</p><p id="reconciliation-error" class="inline-error" role="alert"></p><button id="reconciliation-save" class="primary">Save reconciliation</button>`;
  document.body.append(dialog);const find=s=>dialog.querySelector(s);
  dialog.addEventListener('close',()=>dialog.remove());find('#reconciliation-close').onclick=()=>dialog.close();
  find('#reconciliation-outcome').onchange=()=>{const value=find('#reconciliation-outcome').value;find('#reconciliation-date-label').hidden=value!=='submitted';find('#reconciliation-guidance').textContent=value==='submitted'?'Records the application as submitted with your evidence/reference; preserves its timeline and document snapshot.':value==='not_submitted'?'Returns the record to prepared. A new attempt becomes available, but nothing is automatically submitted or retried.':'Still unsure keeps the existing lock. Contact the employer if the portal/email does not establish an outcome.';};
  find('#reconciliation-save').onclick=async e=>{e.target.disabled=true;try{const date=find('#reconciliation-date').value;const out=await api('/api/center/submission/reconcile',{id,outcome:find('#reconciliation-outcome').value,source:find('#reconciliation-source').value,evidence:find('#reconciliation-evidence').value,reference:find('#reconciliation-reference').value,checked:find('#reconciliation-checked').checked,submitted_at:date?new Date(date).toISOString():'',reconciliation_token:ctx.reconciliation_token});dialog.close();const agent=document.querySelector('.agent-dialog');if(agent?.open)agent.close();await refresh();await openDetail(id);notify(out.outcome==='submitted'?'Submission confirmed and recorded.':out.outcome==='not_submitted'?'Confirmed not submitted. Packet is prepared; choose a new attempt when ready.':'Check saved; uncertainty and retry lock remain.');}catch(exc){if(dialog.isConnected)find('#reconciliation-error').textContent=exc.message;}finally{if(e.target.isConnected)e.target.disabled=false;}};
  dialog.showModal();
 }
 document.addEventListener('click',e=>{const button=e.target.closest('[data-reconcile-submission]');if(button)busy(button,()=>open(Number(button.dataset.reconcileSubmission)));});
})();
