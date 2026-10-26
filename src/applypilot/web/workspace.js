'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = value => { try { const u = new URL(value); return ['http:','https:'].includes(u.protocol) ? u.href : '#'; } catch { return '#'; } };
const label = value => String(value || 'ready').replaceAll('_', ' ');
const date = value => value ? new Date(value).toLocaleString() : 'Not set';
const state = {view:'jobs', country:'GB', data:null, setup:null, selected:new Set(), search:'', filter:'active', detail:null, cvTab:'qualifications', credentials:[], experiences:[], variant:'default', dirty:false};
const names = {jobs:['DISCOVER / DECIDE / APPLY','Find your next move.','Your opportunities, prioritized and ready for action.'],applications:['SUBMITTED / FOLLOW UP / PROGRESS','Every application, accounted for.','Track execution separately from what happens next in hiring.'],cv:['YOUR EXPERIENCE / YOUR EVIDENCE','A CV that keeps up with you.','Update your professional facts, then regenerate the documents that need them.'],pipeline:['PREPARE / EXECUTE / OBSERVE','Keep the work moving.','Run the existing pipeline and inspect its progress.'],inbox:['EVIDENCE / REVIEW / UPDATE','Turn messages into next steps.','Import an employer message, review its match, and update the hiring record.'],analytics:['OBSERVE / LEARN / REFINE','Understand your progress.','Measured from your saved application records.'],settings:['PROFILE / PREFERENCES / CONFIGURATION','Make it yours.','Manage your contact profile, searches, resume variants, and generation settings.']};

names.history=['CLOSE / RETAIN / CLEANUP','Every opportunity has a story.','Keep lightweight outcomes and timelines, and reclaim generated-file storage.'];
async function api(path, body, requestOptions={}) {
  const country=state.country;
  if(path.startsWith('/api/center')){
    if(body!==undefined)body={...body,country_code:country};
    else path+=(path.includes('?')?'&':'?')+'country_code='+encodeURIComponent(country);
  }
  if(path.startsWith('/api/center/detail?'))cancelAssistant('detail',false);
  const options = {headers:{}};
  if (body !== undefined) {
    options.method='POST'; options.headers['Content-Type']='application/json'; options.body=JSON.stringify(body);
    const token = document.cookie.split('; ').find(x=>x.startsWith('ap_csrf='));
    if (token) options.headers['X-CSRF-Token']=decodeURIComponent(token.split('=').slice(1).join('='));
  }
  if(requestOptions.signal)options.signal=requestOptions.signal;
  const response=await fetch(path, options);
  if(response.status===401){location.href='/';throw new Error('Sign in to your workspace.');}
  const data=await response.json();
  if(!response.ok || data.ok===false) throw new Error(data.detail || data.error || 'Request failed');
  if(path==='/api/center/assistant'&&!requestOptions.skipAssistantMeta){
    const output=$(body?.task==='copilot'?'#ai-overview':'#ai-detail');
    if(output){
      let meta=output.previousElementSibling;
      if(!meta?.classList.contains('ai-response-meta')){meta=document.createElement('p');meta.className='hint ai-response-meta';output.before(meta);}
      meta.textContent=`${data.provider} · ${data.model} · ${data.routing_task||body?.task}${data.cached?' · cached':''}`;
    }
  }
  return data;
}

// Capture assistant controls before the shared action handler: these requests
// own their output node and terminal state, rather than relying on toast errors.
const assistantRequests = new Map();
function cancelAssistant(scope, showState=true){
 const request=assistantRequests.get(scope);if(!request)return;
 request.cancelled=true;request.controller.abort();
 if(showState&&request.output.isConnected){request.output.dataset.state='cancelled';request.output.textContent='Stopped waiting for this answer. The provider request may still finish and count toward usage.';}
}
async function askAssistant(control, body, scope){
 const output=$(scope==='overview'?'#ai-overview':'#ai-detail');if(!output)return;
 cancelAssistant(scope,false);
 const request={output,controller:new AbortController(),cancelled:false,timedOut:false};
 assistantRequests.set(scope,request);
 const isCurrent=()=>assistantRequests.get(scope)===request&&output.isConnected&&output===$(scope==='overview'?'#ai-overview':'#ai-detail')&&(scope!=='detail'||$('#job-dialog').open&&Number(state.detail?.job.id)===body.id);
 const controls=document.createElement('div');controls.className='actions ai-request-controls';
 const stop=document.createElement('button');stop.textContent='Stop waiting';stop.onclick=()=>cancelAssistant(scope);controls.append(stop);
 output.parentElement.querySelector('.ai-request-controls')?.remove();output.after(controls);
 output.parentElement.querySelector('.ai-response-meta')?.remove();
 output.dataset.state='loading';output.setAttribute('aria-busy','true');output.textContent='Reading saved evidence… Waiting for the selected AI provider (up to 60 seconds).';
 const text=control.textContent;control.disabled=true;control.textContent='Working…';
 const timeout=setTimeout(()=>{request.timedOut=true;request.controller.abort();},60000);
 try{
  const out=await api('/api/center/assistant',body,{signal:request.controller.signal,skipAssistantMeta:true});
  if(!isCurrent()||request.cancelled)return;
  if(typeof out.answer!=='string'||!out.answer.trim())throw new Error('The AI provider returned no usable explanation. Retry or check Settings.');
  output.dataset.state='completed';output.textContent=out.answer;
  const meta=document.createElement('p');meta.className='hint ai-response-meta';meta.textContent=`${out.provider} · ${out.model} · ${out.routing_task||body.task}${out.cached?' · cached':''}`;output.before(meta);
 }catch(error){
  if(!isCurrent()||request.cancelled)return;
  output.dataset.state='error';output.textContent=request.timedOut?'No answer arrived within 60 seconds. Retry, or check the task model and credential origin in Settings.':error.message;
 }finally{
  clearTimeout(timeout);control.disabled=false;control.textContent=text;
  if(assistantRequests.get(scope)===request){
   output.removeAttribute('aria-busy');controls.replaceChildren();
   if(isCurrent()&&output.dataset.state!=='completed'){
    const retry=document.createElement('button');retry.textContent='Retry answer';retry.onclick=()=>askAssistant(control,body,scope);controls.append(retry);
    const settings=document.createElement('button');settings.textContent='AI settings';settings.onclick=()=>{$('#job-dialog').close();navigate('settings');};controls.append(settings);
   }
   assistantRequests.delete(scope);
  }
 }
}
document.addEventListener('click',event=>{
 const button=event.target.closest('button');if(!button)return;
 if(button.dataset.ai||button.id==='copilot'){
  event.preventDefault();event.stopImmediatePropagation();
  const overview=button.id==='copilot';
  askAssistant(button,overview?{task:'copilot',question:$('#copilot-question').value}:{task:button.dataset.ai,id:Number(button.dataset.id)},overview?'overview':'detail');
 }else if(button.dataset.view)cancelAssistant('overview',false);
},true);
$('#job-dialog').addEventListener('close',()=>cancelAssistant('detail',false));
$('#job-dialog').addEventListener('cancel',()=>cancelAssistant('detail',false));
let toastTimer;
function notify(text, error=false){const t=$('#toast');t.textContent=text;t.className='toast'+(error?' error':'');t.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.hidden=true,error?8000:4500);}
async function busy(control, work){
 const isButton=control?.tagName==='BUTTON';
 const text=isButton?control.textContent:null;
 const wasDisabled=control?.disabled;
 const previousBusy=control?.getAttribute('aria-busy');
 if(control){control.disabled=true;control.setAttribute('aria-busy','true');if(isButton)control.textContent='Working…';}
 try{return await work();}catch(e){notify(e.message,true);}
 finally{if(control){control.disabled=wasDisabled;if(isButton)control.textContent=text;if(previousBusy===null)control.removeAttribute('aria-busy');else control.setAttribute('aria-busy',previousBusy);}}
}
async function refresh(render=true){const country=state.country,data=await api('/api/center');if(country!==state.country)return;state.data=data;$('#workspace-country option[value="NZ"]').disabled=!data.nz_workflow_enabled;if(render&&state.view==='inbox'&&$('#inbox-list')){renderMetrics();await loadInbox();await loadMailPanel();}else if(render)draw();else renderMetrics();}
function dirty(value=true){state.dirty=value;$('#save-state').textContent=value?'Unsaved CV changes':'Saved';}
function renderMetrics(){if(!state.data)return;if(state.country==='NZ'){$('#metrics').innerHTML='';return;}const m=state.data.metrics;$('#metrics').innerHTML=[['Current jobs',m.active_jobs??m.total],['Archived jobs',m.archived??0],['Deleted history',m.retired??0],['Picked to apply',m.picked],['Submitted · all time',m.submitted],['Interviews / offers · all time',m.interviews]].map(([name,value])=>`<div class="metric"><strong>${value}</strong><span>${name}</span></div>`).join('');}
function navigate(view){if(state.dirty && !confirm('Leave CV Studio with unsaved changes?'))return;state.view=view;state.dirty=false;$('#save-state').textContent='';state.selected.clear();history.replaceState(null,'','#'+view);draw();}
function setSidebarCollapsed(collapsed,persist=true){
 document.documentElement.dataset.sidebarCollapsed=String(collapsed);
 const toggle=$('#sidebar-toggle');toggle.setAttribute('aria-expanded',String(!collapsed));toggle.setAttribute('aria-label',collapsed?'Expand sidebar':'Collapse sidebar');toggle.title=collapsed?'Expand sidebar':'Collapse sidebar';
 if(persist)try{localStorage.setItem('applypilot-sidebar-collapsed',String(collapsed));}catch{}
}
$$('.rail nav [data-view]').forEach(button=>{const number=button.querySelector('span');const text=button.textContent.replace(/^\s*(?:\d+|↳)\s*/,'').trim();number.className='nav-number';button.innerHTML=number.outerHTML+`<span class="nav-label">${esc(text)}</span>`;button.setAttribute('aria-label',text);button.title=text;});
let sidebarPreference=false;try{sidebarPreference=localStorage.getItem('applypilot-sidebar-collapsed')==='true';}catch{}
setSidebarCollapsed(sidebarPreference,false);$('#sidebar-toggle').onclick=()=>setSidebarCollapsed(document.documentElement.dataset.sidebarCollapsed!=='true');
function draw(){
  if(!state.data)return;
  if(state.country==='NZ'){
    $('#eyebrow').textContent='NEW ZEALAND / DISCOVER / PREPARE / APPLY';$('#page-title').textContent='Your New Zealand job search.';$('#page-description').textContent='Discover NZ opportunities, prepare your documents, apply with the agent and track your progress.';$('#metrics').innerHTML='';$('#page-actions').innerHTML='';
    renderNzDiscovery();return;
  }
 if(!Object.hasOwn(names,state.view))state.view='jobs';
 const info=names[state.view];$('#eyebrow').textContent=info[0];$('#page-title').textContent=info[1];$('#page-description').textContent=info[2];
  $$('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===state.view);if(b.closest('.rail')){if(b.dataset.view===state.view)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}});
 $('#page-actions').innerHTML=state.view==='analytics'?'<a href="/api/center/insights/export?format=zip">Download complete analysis ↗</a>':'';
 renderMetrics();
 if(state.view==='jobs'||state.view==='applications')renderJobs();
 else if(state.view==='cv')renderCv();
 else if(state.view==='inbox'){renderInbox();loadMailPanel().catch(e=>notify(e.message,true));}
  else if(state.view==='analytics'){renderAnalytics();window.OpportunityInsights?.render().catch(e=>notify(e.message,true));renderOperationalInsights();renderPipelineInsights();}
  else if(state.view==='pipeline')renderPipeline();
  else if(state.view==='settings')renderNativeSettings();
  else if(state.view==='portfolio')renderPortfolio();
  else if(state.view==='history'){if(window.JobLifecycle)window.JobLifecycle.renderHistory().catch(e=>notify(e.message,true));else $('#content').innerHTML='<p class="empty">History tools could not load. Refresh the workspace to retry.</p>';}
  else if(['cover','statement'].includes(state.view))renderWritingStudio(state.view);
  if(state.view==='analytics')renderAiRoutes();
}

function renderAiRoutes(){
 const routes=state.data.ai_routes||[];
 const panel=document.createElement('section');panel.className='panel';panel.style.marginTop='20px';
 panel.innerHTML=`<h2>AI task routing</h2><p class="hint">Effective server configuration. Edit LLM_TASK_PROVIDER / LLM_TASK_MODEL in .env and recreate the service to change routes. Configured credentials do not represent a document-quality benchmark.</p><div class="table-wrap"><table><thead><tr><th>Task</th><th>Provider</th><th>Model</th><th>Reasoning / thinking</th><th>Configuration</th></tr></thead><tbody>${routes.map(route=>`<tr><td>${esc(route.label||route.task)}</td><td>${esc(route.provider||'—')}</td><td>${esc(route.model||'—')}</td><td>${esc(route.reasoning_effort??(route.thinking_budget!==null&&route.thinking_budget!==undefined?'Budget '+route.thinking_budget:'Provider default'))}</td><td>${route.configured?`<span class="badge">${route.explicit?'Task override':'Default inherited'}</span>`:`<span class="inline-error">${esc(route.error||'Needs configuration')}</span>`}</td></tr>`).join('')}</tbody></table></div>`;
 $('#content').append(panel);
}
function actions(j){const active=['in_progress','running','submitting'].includes(j.status),submitted=!!j.applied_at;let main=active?`<button data-detail="${j.id}" class="primary">View progress</button>`:submitted?`<button data-detail="${j.id}" class="primary">Add hiring update</button>`:j.status==='failed'?`<button data-action="retry" data-id="${j.id}" class="primary">Retry</button>`:`<button data-action="${j.status==='selected'?'unpick':'pick'}" data-id="${j.id}" class="primary">${j.status==='selected'?'Unpick':'Pick'}</button>`;return `<div class="card-actions">${main}<button data-detail="${j.id}">View record</button><details><summary>More</summary><div><button data-action="applied" data-id="${j.id}">Mark applied</button><button data-action="failed" data-id="${j.id}">Record failure</button><button data-regen="${j.id}">Prepare / regenerate CV</button><button data-action="${j.visibility==='archived'?'restore':'archive'}" data-id="${j.id}">${j.visibility==='archived'?'Restore':'Archive'}</button><button class="danger" data-action="block" data-id="${j.id}">Block…</button></div></details></div>`;}
function filteredJobs(){let jobs=state.data.jobs;if(state.view==='applications')jobs=jobs.filter(j=>j.applied_at||['selected','in_progress','prepared','failed','submission_uncertain'].includes(j.status));return jobs.filter(j=>!state.search||`${j.title} ${j.company} ${j.site} ${j.location}`.toLowerCase().includes(state.search.toLowerCase())).filter(j=>state.filter==='all'||(state.filter==='active'?j.visibility==='active'&&j.status!=='skipped':state.filter==='archived'?j.visibility==='archived':state.filter==='blocked'?j.visibility==='blocked':j.status===state.filter));}
function renderJobs(){const jobs=filteredJobs();$('#content').innerHTML=`<div class="toolbar"><input id="job-search" type="search" aria-label="Search jobs" placeholder="Search title, employer, location…" value="${esc(state.search)}"><select id="job-filter" aria-label="Filter jobs">${['active','all','selected','in_progress','applied','failed','submission_uncertain','archived','blocked'].map(s=>`<option value="${s}" ${state.filter===s?'selected':''}>${label(s)}</option>`).join('')}</select><button id="bulk-pick">Pick selected</button><button id="bulk-regen">Regenerate selected CVs</button></div><p class="live-note">${jobs.length} records · Select cards for bulk actions. Progress updates automatically.</p><div class="cards">${jobs.map(j=>`<article class="card"><div class="card-top"><label><input type="checkbox" data-select="${j.id}" aria-label="Select ${esc(j.title)}" ${state.selected.has(j.id)?'checked':''}></label><span class="score">${j.fit_score??'—'} / 10 fit</span><span class="badge">${esc(label(j.status))}</span></div><h3><button data-detail="${j.id}">${esc(j.title||'Untitled opportunity')}</button></h3><span class="muted">${esc(j.company||'Employer not specified')}</span><div class="meta"><span>${esc(j.location||'Location unknown')}</span><span>· ${esc(j.site||'Direct listing')}</span>${j.salary?`<span>· ${esc(j.salary)}</span>`:''}</div>${j.hiring_status?`<p><span class="badge">Hiring: ${esc(label(j.hiring_status))}</span></p>`:''}${j.document_stale?'<p class="hint inline-error">CV needs regeneration · professional inputs changed or version is untracked.</p>':''}${j.due_at?`<p class="hint">Next action: ${esc(date(j.due_at))}</p>`:''}${j.apply_error?`<p class="hint">${esc(j.apply_error.slice(0,160))}</p>`:''}${actions(j)}</article>`).join('')||'<p class="empty">No jobs in this view. Change the filter or run discovery from Pipeline.</p>'}</div>`;
  $('#job-search').addEventListener('input',e=>{state.search=e.target.value;const caret=e.target.selectionStart;renderJobs();$('#job-search').focus();$('#job-search').setSelectionRange(caret,caret);});$('#job-filter').onchange=e=>{state.filter=e.target.value;renderJobs();};$('#bulk-pick').onclick=e=>busy(e.target,()=>perform('pick',[...state.selected]));$('#bulk-regen').onclick=e=>busy(e.target,()=>regenerate([...state.selected]));window.JobLifecycle?.mountSelection($('#content'),jobs,state.data.jobs);}
async function perform(action, ids, extra={}){if(!ids.length)throw new Error('Choose at least one job.');if(action==='failed'){const reason=prompt('What failed? Include the step and what happened.');if(reason===null)return;extra.reason=reason;}if(action==='applied'){const evidence=prompt('Submission confirmation or reference (optional). This is a manual status update.','');if(evidence===null)return;extra.evidence=evidence;}if(action==='block'){const scope=prompt('Block scope: job, employer, or domain','job');if(scope===null)return;const reason=prompt('Why are you blocking it?','Not suitable');if(reason===null)return;extra={...extra,scope,reason};}const out=await api('/api/center/action',{action,ids,...extra});const failed=out.results.filter(r=>!r.ok);notify(`${out.results.length-failed.length} updated${failed.length?'; '+failed.map(r=>`#${r.id}: ${r.error}`).join('; '):''}`,!!failed.length);await refresh();}
async function regenerate(ids){if(!ids.length)throw new Error('Choose at least one job.');const out=await api('/api/center/regenerate',{ids});notify(`CV regeneration started · run ${out.run_id||''}. Follow progress in Pipeline.`);await refresh(false);}
async function openDetail(id){const out=await api('/api/center/detail?id='+id);state.detail=out;const j=out.job,m=out.meta;$('#job-detail').innerHTML=`<h2 id="detail-title">${esc(j.title)}</h2><p class="muted">${esc(j.company||j.site)} · #${j.id}</p><div class="actions"><a href="${safeUrl(j.url)}" target="_blank" rel="noopener noreferrer">Listing ↗</a><a href="${safeUrl(j.application_url||j.url)}" target="_blank" rel="noopener noreferrer">Open application ↗</a><button data-regen="${j.id}">Prepare / regenerate CV</button><button data-action="applied" data-id="${j.id}">Mark applied</button><button data-action="failed" data-id="${j.id}">Record failure</button></div><p class="hint">Opening the application page does not record a submission.</p><div class="split"><div class="stack"><section class="panel"><h3>Next action & hiring</h3><label>Hiring status<select id="detail-hiring"><option value="">Not submitted</option>${['submitted','acknowledged','assessment','interview','offer','rejected','withdrawn','no_response'].map(s=>`<option value="${s}" ${(m.hiring_status||(j.applied_at?'submitted':''))===s?'selected':''}>${label(s)}</option>`).join('')}</select></label><div class="fields"><label>Priority<select id="detail-priority">${[0,1,2,3].map(v=>`<option value="${v}" ${Number(m.priority||0)===v?'selected':''}>${['Normal','Important','High','First'][v]}</option>`).join('')}</select></label><label>Reminder / deadline<input id="detail-due" type="datetime-local" value="${esc(toLocalInput(m.due_at))}"></label></div><label>Recruiter contact<input id="detail-recruiter" value="${esc(m.recruiter)}"></label><label>Application reference<input id="detail-reference" value="${esc(m.reference)}"></label><label>Notes<textarea id="detail-notes">${esc(m.notes)}</textarea></label><button id="save-detail" class="primary">Save application record</button></section>${j.apply_error?`<section class="panel"><h3>Recovery guidance</h3><span class="badge warn">${esc(out.failure.category)}</span><p>${esc(j.apply_error)}</p><p>${esc(out.failure.recovery)}</p>${out.failure.retryable?`<button data-action="retry" data-id="${j.id}">Queue retry</button>`:''}</section>`:''}<section class="panel"><h3>CV versions</h3>${out.documents.map(d=>`<p><span class="badge">Version ${d.id}</span> ${esc(date(d.created_at))} · ${esc(d.variant)} ${d.stale?'<span class="badge warn">Outdated inputs</span>':''}<br><a href="/api/center/document?id=${d.id}" target="_blank">Preview</a> · <a href="/api/center/document?id=${d.id}&format=txt">Text</a>${d.pdf_path?` · <a href="/api/center/document?id=${d.id}&format=pdf">PDF</a>`:''}</p>`).join('')||'<p class="hint">No versioned CV yet. Prepare a CV to create the first version.</p>'}</section></div><div class="stack"><section class="panel"><h3>Activity timeline</h3><ul class="timeline">${out.events.map(e=>`<li><strong>${esc(label(e.state||e.kind))}</strong><time>${esc(date(e.created_at))} · ${esc(e.source)}</time><p>${esc(e.detail)}</p></li>`).join('')||'<li>No recorded activity yet.</li>'}</ul></section><section class="panel"><h3>AI assistant</h3><p class="hint">Uses this job and your saved professional evidence. Requires a configured AI provider.</p><div class="actions">${[['explain','Explain fit'],['diagnose','Diagnose failure'],['interview','Interview prep'],['followup','Draft follow-up']].map(([task,title])=>`<button data-ai="${task}" data-id="${j.id}">${title}</button>`).join('')}</div><div id="ai-detail" class="ai-output" aria-live="polite"></div></section></div></div><details class="panel" style="margin-top:20px"><summary>Full job description</summary><p class="description">${esc(j.full_description||j.description||'Not enriched yet.')}</p></details>`;
 $('#save-detail').onclick=e=>busy(e.target,async()=>{const hiring=$('#detail-hiring').value;const data={action:hiring?'hiring':'notes',ids:[j.id],hiring_status:hiring,priority:Number($('#detail-priority').value),notes:$('#detail-notes').value,recruiter:$('#detail-recruiter').value,reference:$('#detail-reference').value,due_at:$('#detail-due').value?new Date($('#detail-due').value).toISOString():''};const result=await api('/api/center/action',data);if(!result.results[0].ok)throw new Error(result.results[0].error);notify('Application record saved.');await refresh();await openDetail(j.id);});
  const bar=$('.actions',$('#job-detail'));
  bar.insertAdjacentHTML('beforeend',`<button data-document-review="${j.id}">Review final documents</button>`);
  bar.insertAdjacentHTML('beforeend',`<button data-application-handoff="${j.id}">Application support / manual handoff</button>`);
  if(j.apply_status==='submission_uncertain'){
   $('#detail-hiring').options[0].textContent='Submission uncertain — verify with employer';
   bar.insertAdjacentHTML('beforeend',`<button data-reconcile-submission="${j.id}" class="primary">Reconcile submission</button>`);
   bar.insertAdjacentHTML('afterend','<p class="notice">Submit was attempted, but no receipt was verified. Check My applications and employer email, then use Reconcile submission. A missing email alone does not prove failure.</p>');
  }
  bar.insertAdjacentHTML('beforeend',`<button data-job-lifecycle="${j.id}">Close / delete record</button>`);
  if(out.closure)bar.insertAdjacentHTML('afterend',`<p class="notice">Opportunity closed: ${esc(label(out.closure.reason))} · ${esc(out.closure.detail)}. Find it under Archived; use Restore to reopen.</p>`);
  bar.insertAdjacentHTML('beforeend',`<button data-record-activity="${esc(j.url)}">Record activity</button><button id="check-listing">Check listing availability</button><button id="export-calendar">Export reminder to calendar</button>`);
  $('#detail-due').closest('label').insertAdjacentHTML('afterend','<p id="calendar-error" class="inline-error" role="alert"></p>');
  $('#export-calendar').onclick=e=>busy(e.target,async()=>{
   const due=$('#detail-due'),notice=$('#calendar-error');notice.textContent='';
   if(!due.value){notice.textContent='Choose a reminder date and time, then export your calendar reminder.';due.focus();due.scrollIntoView({block:'center'});return;}
   try{const due_at=new Date(due.value).toISOString();const saved=await api('/api/center/action',{action:'notes',ids:[j.id],due_at});if(!saved.results[0].ok)throw new Error(saved.results[0].error);
    const response=await fetch('/api/center/calendar?id='+j.id,{cache:'no-store'});if(!response.ok){const out=await response.json();throw new Error(out.error||'Could not export reminder.');}
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download=`opportunity-${j.id}-reminder.ics`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Reminder saved and calendar file downloaded.');await refresh(false);
   }catch(exc){notice.textContent=exc.message;}
  });
 const check=out.listing_check;if(check.checked_at)bar.insertAdjacentHTML('afterend',`<p class="hint">Listing check: ${esc(check.status)} · ${esc(date(check.checked_at))} · ${esc(check.detail)}</p>`);
 if(out.possible_duplicates.length)bar.insertAdjacentHTML('afterend',`<p class="notice">Possible duplicate listings (same employer and title): ${out.possible_duplicates.map(d=>`#${d.id} ${esc(d.site)}`).join(', ')}. Compare listings before another submission.</p>`);
  $('#check-listing').onclick=e=>busy(e.target,async()=>{const result=await api('/api/center/listing/check',{id:j.id});notify(result.detail);await openDetail(j.id);});
  bar.insertAdjacentHTML('beforeend',`<button id="apply-with-agent" class="primary">Apply with agent</button><details><summary>CLI application engines</summary><select id="apply-engine" aria-label="Application engine" style="width:auto"><option value="llm">AI-assisted browser</option><option value="claude">Claude Code</option></select><select id="apply-mode" aria-label="Application execution mode" style="width:auto"><option value="prepare">Prepare without submitting</option><option value="submit">Submit application</option></select><button id="start-application">Start application</button></details>`);
  $('#apply-with-agent').onclick=e=>busy(e.target,async()=>{if(!window.AssistedBrowser)throw new Error('Browser workspace did not load. Refresh and try again.');await window.AssistedBrowser.open(j,out);});
 $('#start-application').onclick=e=>busy(e.target,async()=>{const submit=$('#apply-mode').value==='submit',engine=$('#apply-engine').value;const out=await api('/api/center/apply',{id:j.id,engine,submit,dry_run:engine==='claude'&&!submit});notify(`Application run started · ${out.run_id}. Follow progress in Pipeline.`);});
 if(['in_progress','running','submitting'].includes(j.apply_status)&&String(j.agent_id||'').startsWith('llm-worker-'))bar.insertAdjacentHTML('beforeend',`${['pause','resume','cancel'].map(action=>`<button data-action="${action}" data-id="${j.id}">${label(action)} at checkpoint</button>`).join('')}`);
 $('#job-detail').insertAdjacentHTML('beforeend',`<section class="panel" style="margin-top:20px"><h3>Schedule this application</h3><p class="hint">Runs while the dashboard server is running. Interrupted submissions are held for review instead of automatically replayed.</p><div class="fields"><label>Start time<input id="schedule-at" type="datetime-local"></label><label>Execution mode<select id="schedule-mode"><option value="prepare">Prepare without submitting</option><option value="submit">Submit application</option></select></label></div><button id="save-schedule">Schedule application</button></section>`);
  $('#save-schedule').onclick=e=>busy(e.target,async()=>{const when=$('#schedule-at').value;if(!when)throw new Error('Choose a schedule time.');await api('/api/center/schedule',{id:j.id,run_at:new Date(when).toISOString(),mode:$('#schedule-mode').value});notify('Application scheduled.');await refresh(false);});
   if(state.data.submission_safety?.live_execution_enabled===false)for(const id of ['apply-with-agent','start-application','save-schedule']){const control=$('#'+id);control.disabled=true;control.title=state.data.submission_safety.reason;}
 $$('.timeline li',$('#job-detail')).forEach((el,index)=>{if(out.events[index])el.dataset.eventId=String(out.events[index].id);});
  if(!$('#job-dialog').open)$('#job-dialog').showModal();pipelineInspect(j.id).catch(e=>notify(e.message,true));}
function toLocalInput(value){if(!value)return '';const d=new Date(value);if(!Number.isFinite(d.getTime()))return '';return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}

async function loadSetup(){state.setup=await api('/api/setup/read');const rs=state.setup.profile?.resume_sections||{};for(const key of ['certifications','education','memberships','professional_development'])if(typeof rs[key]==='string')rs[key]=rs[key].split(/\r?\n/).filter(Boolean);state.credentials=structuredClone(rs.credentials||[]);state.experiences=structuredClone(rs.professional_experience||[]);}
function renderCv(){
 if(state.view!=='cv')return;
 if(!state.setup){$('#content').innerHTML='<p class="empty">Loading professional profile…</p>';loadSetup().then(renderCv).catch(e=>notify(e.message,true));return;}
 const rs=state.setup.profile?.resume_sections||{};
 ++previewRequest;
 $('#content').innerHTML=`<div class="split"><div class="stack"><section class="panel"><div class="editor-tabs">${[['qualifications','Qualifications'],['experience','Experience & projects'],['resume','Resume text']].map(([key,title])=>`<button data-cv-tab="${key}" class="${state.cvTab===key?'active':''}">${title}</button>`).join('')}</div><div id="cv-editor"></div></section><section class="panel"><h3>Professional input revisions</h3><p class="hint">Shared facts are added to the selected resume as verified evidence. Existing role-specific text remains intact. Submitted document versions remain preserved.</p><span class="badge">Current revision ${esc(state.data.revision.slice(0,12))}</span></section></div><section class="panel preview-panel"><h2>Document preview</h2><label>Choose job<select id="preview-job"><option value="">Choose an opportunity…</option>${state.data.jobs.map(j=>`<option value="${j.id}">#${j.id} ${esc(j.title)}${j.document_stale?' · needs regeneration':''}</option>`).join('')}</select></label><div id="preview-tools"><p class="hint">Choose a job to inspect saved CV versions and regenerate its document.</p></div><div id="cv-preview-state" class="preview-state" role="status" aria-live="polite"><span class="eyebrow">YOUR DOCUMENT</span><h3>Choose a job to preview its CV</h3><p>Saved CVs and their versions will appear here.</p></div><iframe id="cv-preview" class="preview" title="Generated CV preview" sandbox hidden></iframe></section></div>`;
 if(state.cvTab==='qualifications')$('#cv-editor').innerHTML=`<h2>Qualifications, kept current.</h2><p class="hint">One credential per entry. Dates and status are rendered exactly as saved.</p><div id="credentials"></div><button id="add-credential">+ Add certification</button><label style="margin-top:20px">Existing certification lines (one per line)<textarea id="legacy-certs">${esc((rs.certifications||[]).join('\n'))}</textarea></label><label>Education (one qualification per line)<textarea id="education">${esc((rs.education||[]).join('\n'))}</textarea></label><label>Professional memberships<textarea id="memberships">${esc((rs.memberships||[]).join('\n'))}</textarea></label><label>Professional development / CPD<textarea id="development">${esc((rs.professional_development||[]).join('\n'))}</textarea></label><button id="save-professional" class="primary">Save qualifications</button>`;
  else if(state.cvTab==='experience')$('#cv-editor').innerHTML=`<h2>Experience with evidence.</h2><p class="hint">Add verified roles or achievements. New entries retain their exact employer, dates and bullets.</p><div id="experiences"></div><div class="actions"><button id="add-experience">+ Add professional experience</button><button data-view="portfolio">Manage projects in Portfolio</button></div><details style="margin-top:20px"><summary>Advanced project data</summary><textarea id="projects">${esc(JSON.stringify(rs.projects||[],null,2))}</textarea></details><button id="save-experience" class="primary">Save experience & projects</button>`;
 else $('#cv-editor').innerHTML=`<h2>Your source resume.</h2><label>Base resume / variant<select id="resume-variant"><option value="default">Default resume.txt</option>${(state.setup.resume_variants||[]).map(v=>`<option value="${esc(v.key)}">${esc(v.key)}</option>`).join('')}</select></label><label>Resume text<textarea id="source-resume" style="min-height:430px">${esc(state.setup.resume_text||'')}</textarea></label><p class="hint">Saving this text updates this source only. Shared professional facts are supplied to future tailoring across variants.</p><button id="save-resume" class="primary">Save source resume</button>`;
   if(state.cvTab==='resume'&&state.variant!=='default'&&!state.setup.resume_variants.some(v=>v.key===state.variant))state.variant='default';
  if(state.cvTab==='qualifications'){renderCredentials();$('#add-credential').onclick=()=>{collectCredentials();state.credentials.push({name:'',issuer:'',awarded:'',expires:'',status:'earned',family:'all',include:true});renderCredentials();dirty();};$('#save-professional').onclick=e=>busy(e.target,async()=>{collectCredentials();await saveSections({credentials:state.credentials,certifications:lines($('#legacy-certs').value),education:lines($('#education').value),memberships:lines($('#memberships').value),professional_development:lines($('#development').value)});});}
 if(state.cvTab==='experience'){renderExperiences();$('#add-experience').onclick=()=>{collectExperiences();state.experiences.push({header:'',subtitle:'',bullets:[]});renderExperiences();dirty();};$('#save-experience').onclick=e=>busy(e.target,async()=>{collectExperiences();let projects;try{projects=JSON.parse($('#projects').value);}catch{throw new Error('Projects must be valid JSON.');}await saveSections({professional_experience:state.experiences,projects});});}
   if(state.cvTab==='resume'){$('#resume-variant').onchange=e=>chooseResumeVariant(e.target);$('#save-resume').onclick=e=>busy(e.target,async()=>{await api(state.variant==='default'?'/api/setup/resume-text':'/api/setup/resume-variant',{text:$('#source-resume').value,key:state.variant});await loadSetup();await refresh(false);dirty(false);notify('Source resume saved. Regenerate affected CVs to update their output.');});}
   if(state.cvTab==='resume'){$('#resume-variant').value=state.variant;if(state.variant!=='default')chooseResumeVariant($('#resume-variant'));}
  const markEditorDirty=e=>{if(e.target.id!=='resume-variant')dirty();};
  $('#cv-editor').addEventListener('input',markEditorDirty);$('#cv-editor').addEventListener('change',markEditorDirty);
  const choice=$('#preview-job');
  choice.onchange=e=>{const id=Number(e.target.value);busy(e.target,()=>loadPreview(id));};
  choice.value=String(state.previewJobId||'');
   if(choice.value)busy(choice,()=>loadPreview(Number(choice.value)));
   renderCvExtras().catch(e=>notify(e.message,true));
}

function chooseResumeVariant(control){
 const next=control.value, previous=state.variant;
 const source=$('#source-resume'), save=$('#save-resume');
 if(state.dirty&&!confirm('Discard unsaved source resume edits?')){control.value=previous;return;}
 const sourceDisabled=source.disabled, saveDisabled=save.disabled;
 source.disabled=true;save.disabled=true;
 return busy(control,async()=>{
  try{
   const text=next==='default'?state.setup.resume_text:(await api('/api/setup/resume-variant?key='+encodeURIComponent(next))).text;
   if(!source.isConnected)return;
   source.value=text||'';state.variant=next;dirty(false);
  }catch(error){control.value=previous;throw error;}
  finally{source.disabled=sourceDisabled;save.disabled=saveDisabled;}
 });
}
const lines=text=>text.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
async function saveSections(patch){await api('/api/setup/profile',{profile:{resume_sections:patch}});await loadSetup();await refresh(false);dirty(false);notify('Professional facts saved. Existing documents are preserved; regenerate affected CVs.');}
function renderCredentials(){$('#credentials').innerHTML=state.credentials.map((c,i)=>`<div class="credential" data-credential="${i}"><div class="credential-head"><label><input type="checkbox" data-field="include" ${c.include!==false?'checked':''}> Include in CV</label><button data-remove-credential="${i}" class="quiet danger">Remove</button></div><div class="fields">${[['name','Certification name'],['issuer','Issuer'],['awarded','Awarded date'],['expires','Expiry date'],['link','Credential link']].map(([key,title])=>`<label>${title}<input data-field="${key}" value="${esc(c[key])}"></label>`).join('')}<label>Status<select data-field="status">${['earned','in_progress','expired'].map(s=>`<option value="${s}" ${c.status===s?'selected':''}>${label(s)}</option>`).join('')}</select></label><label>Role applicability<select data-field="family">${['all','data_bi','support','application_support','engineering'].map(s=>`<option value="${s}" ${(c.family||'all')===s?'selected':''}>${label(s)}</option>`).join('')}</select></label></div></div>`).join('')||'<p class="hint">No structured certifications yet. Add one or retain existing lines below.</p>';}
function collectCredentials(){state.credentials=$$('[data-credential]').map(el=>Object.fromEntries($$('[data-field]',el).map(input=>[input.dataset.field,input.type==='checkbox'?input.checked:input.value])));}
function renderExperiences(){$('#experiences').innerHTML=state.experiences.map((entry,i)=>`<div class="credential" data-experience="${i}"><div class="credential-head"><strong>Professional experience ${i+1}</strong><button data-remove-experience="${i}" class="quiet danger">Remove</button></div><label>Role at employer<input data-field="header" value="${esc(entry.header)}" placeholder="Application Support Analyst at Example Ltd"></label><label>Dates / location<input data-field="subtitle" value="${esc(entry.subtitle)}" placeholder="2024–present | London"></label><label>Achievements and responsibilities (one per line)<textarea data-field="bullets">${esc((entry.bullets||[]).join('\n'))}</textarea></label></div>`).join('')||'<p class="hint">Your existing resume experience is still used. Add new verified entries here.</p>';}
function collectExperiences(){state.experiences=$$('[data-experience]').map(el=>({header:$('[data-field=header]',el).value,subtitle:$('[data-field=subtitle]',el).value,bullets:lines($('[data-field=bullets]',el).value)}));}
let previewRequest=0;
function previewStatus(kind,title,message){
 const frame=$('#cv-preview'), status=$('#cv-preview-state');
 if(!frame||!status)return;
 frame.hidden=true;frame.onload=null;frame.removeAttribute('src');frame.removeAttribute('srcdoc');
 status.hidden=false;status.dataset.state=kind;
 status.innerHTML=`<span class="eyebrow">${kind==='error'?'PREVIEW UNAVAILABLE':kind==='loading'?'LOADING DOCUMENT':'YOUR DOCUMENT'}</span><h3>${esc(title)}</h3><p>${esc(message)}</p>`;
}
async function loadPreview(id){
 const request=++previewRequest;
 if(state.previewJobId!==id)state.previewVersionId=0;
 state.previewJobId=id;
 if(!id){$('#preview-tools').innerHTML='<p class="hint">Choose a job to inspect saved CV versions and regenerate its document.</p>';previewStatus('empty','Choose a job to preview its CV','Saved CVs and their versions will appear here.');return;}
 $('#preview-tools').innerHTML='<p class="hint">Loading saved CV versions…</p>';
 previewStatus('loading','Loading your CV','Reading the saved document for this opportunity.');
 try{
  const out=await api('/api/center/detail?id='+id);
  if(request!==previewRequest||state.view!=='cv')return;
  $('#preview-tools').innerHTML=`<div class="actions"><button data-regen="${id}" class="primary">Regenerate this CV</button><button data-detail="${id}">Application record</button></div>${out.documents.length?`<label style="margin-top:16px">Saved version<select id="preview-version">${out.documents.map(d=>`<option value="${d.id}">${d.variant==='legacy snapshot'?'Existing CV':'Version '+d.id} · ${esc(date(d.created_at))}${d.stale?' · outdated or untracked inputs':''}</option>`).join('')}</select></label><div id="preview-downloads"></div>`:''}`;
  if(!out.documents.length){previewStatus('empty','No saved CV available',out.document_notice||'Prepare or regenerate this job’s CV to create its first preview.');return;}
  const choice=$('#preview-version');
  if(out.documents.some(d=>d.id===state.previewVersionId))choice.value=String(state.previewVersionId);
  choice.onchange=e=>{const version=out.documents.find(d=>d.id===Number(e.target.value));busy(e.target,()=>showPreviewVersion(version));};
  await showPreviewVersion(out.documents.find(d=>d.id===Number(choice.value)));
 }catch(error){
  if(request!==previewRequest||state.view!=='cv')return;
  previewStatus('error','Could not load this job’s CV',error.message);
  $('#preview-tools').innerHTML='<button id="preview-retry">Retry loading</button>';
  $('#preview-retry').onclick=e=>busy(e.target,()=>loadPreview(id));
 }
}
async function showPreviewVersion(document){
 const request=++previewRequest;
 if(!document)return;
 state.previewVersionId=document.id;
 previewStatus('loading','Loading your CV','Opening the selected saved version.');
 $('#preview-downloads').innerHTML=`<a href="/api/center/document?id=${document.id}&format=txt">Download text</a>${document.pdf_path?` · <a href="/api/center/document?id=${document.id}&format=pdf">Download PDF</a>`:''}<p class="hint">Source: ${esc(document.variant)}${document.fingerprint==='legacy-untracked'?' · original input revision not recorded':' · revision '+esc(document.fingerprint.slice(0,12))}</p>`;
 try{
  const response=await fetch('/api/center/document?id='+document.id);
  if(!response.ok){let message='Unable to read this saved CV.';try{const error=await response.json();message=error.error||message;}catch{}throw new Error(message);}
  if(!response.headers.get('content-type')?.includes('text/html'))throw new Error('The saved CV did not return a readable preview.');
  const html=await response.text();
  if(request!==previewRequest||state.view!=='cv')return;
  const frame=$('#cv-preview');
  frame.onload=()=>{if(request!==previewRequest||!frame.isConnected)return;$('#cv-preview-state').hidden=true;frame.hidden=false;};
  frame.srcdoc=html;
 }catch(error){
  if(request!==previewRequest||state.view!=='cv')return;
  previewStatus('error','This saved CV is unavailable',error.message+' Choose another version or regenerate the CV.');
 }
}

function renderAnalytics(){const m=state.data.metrics;const due=state.data.jobs.filter(j=>j.due_at).sort((a,b)=>a.due_at.localeCompare(b.due_at));$('#content').innerHTML=`<div class="split"><section class="panel"><h2>Application funnel</h2><p><strong>${m.submitted}</strong> submitted → <strong>${m.responses}</strong> recorded responses → <strong>${m.interviews}</strong> interviews / offers</p><p class="hint">Response rate: ${m.response_rate===null?'No submitted applications yet':m.response_rate+'%'} · All recorded history. Unknown or no-response outcomes are excluded from responses.</p><div class="table-wrap"><table><thead><tr><th>Source</th><th>Submitted</th><th>Responses</th><th>Interviews / offers</th></tr></thead><tbody>${Object.entries(m.sources).map(([source,v])=>`<tr><td>${esc(source)}</td><td>${v.submitted}</td><td>${v.responses}</td><td>${v.interviews}</td></tr>`).join('')}</tbody></table></div></section><section class="panel"><h2>Next actions</h2>${due.map(j=>`<p><button data-detail="${j.id}" class="quiet">${esc(j.title)}</button><br><span class="badge ${new Date(j.due_at)<new Date()?'warn':''}">${esc(date(j.due_at))}</span></p>`).join('')||'<p class="hint">Add reminders from an application record.</p>'}</section><section class="panel"><h2>Block rules</h2>${state.data.blocks.map(b=>`<p><span class="badge">${esc(b.scope)}</span> ${esc(b.value)}<br><span class="hint">${esc(b.reason)}</span> ${b.active?`<button data-unblock="${b.id}" class="quiet">Unblock</button>`:'<span class="muted">Inactive</span>'}</p>`).join('')||'<p class="hint">No block rules.</p>'}</section><section class="panel"><h2>Application copilot</h2><label>Question<textarea id="copilot-question" placeholder="Which picked jobs should I prioritize? Which applications need a follow-up?"></textarea></label><button id="copilot" class="primary">Ask about my records</button><div id="ai-overview" class="ai-output" aria-live="polite"></div></section></div>`;$('#copilot').onclick=e=>busy(e.target,async()=>{const out=await api('/api/center/assistant',{task:'copilot',question:$('#copilot-question').value});$('#ai-overview').textContent=out.answer;notify(`${out.provider} · ${out.model}${out.cached?' · cached':''}`);});}
const inboxBuckets={applications:'Application mail',unclassified:'Unclassified / other',alerts:'Job alerts',promotions:'Promotions',recorded:'Recorded',dismissed:'Dismissed',ignored:'Ignored',all:'All messages',duplicates:'Duplicate copies (audit)'};
Object.assign(state,{inboxBucket:'applications',inboxOffset:0,inboxSearch:'',inboxDrafts:{},inboxOpen:{},inboxRenderSignature:null});
let inboxRequest=0;
function renderInbox(){
 state.inboxRenderSignature=null;
 $('#content').innerHTML=`<div class="split inbox-layout"><section class="panel"><h2>Add employer evidence</h2><p class="hint">Import a message for classification and review. Importing and synchronization do not run paid AI analysis.</p><label>Sender<input id="message-sender" type="text"></label><label>Subject<input id="message-subject" type="text"></label><label>Received time (optional)<input id="message-received" type="datetime-local"></label><label>Message<textarea id="message-body" style="min-height:240px"></textarea></label><button id="message-import" class="primary">Import for review</button><p class="hint">Only use Analyze with AI when needed. Luna results are suggestions backed by source quotes.</p></section><section class="panel"><h2>Evidence inbox</h2><div class="toolbar"><select id="inbox-filter" aria-label="Inbox filter">${Object.entries(inboxBuckets).map(([key,title])=>`<option value="${key}" ${state.inboxBucket===key?'selected':''}>${title}</option>`).join('')}</select><button id="inbox-refresh">Refresh messages</button></div><div class="toolbar"><input id="inbox-search" type="search" aria-label="Search messages" placeholder="Search sender, subject or message…" value="${esc(state.inboxSearch)}"><button id="inbox-find">Search</button></div><p id="inbox-count" class="hint" role="status"></p><div id="inbox-list"><p class="hint">Loading messages…</p></div><div class="actions" id="inbox-pagination"></div><details id="inbox-ignore-rules"><summary>Ignored senders</summary><div id="inbox-rules"></div></details></section></div>`;
 $('#inbox-filter').onchange=e=>{state.inboxBucket=e.target.value;state.inboxOffset=0;busy(e.target,loadInbox);};
 $('#inbox-refresh').onclick=e=>busy(e.target,loadInbox);
 $('#inbox-find').onclick=e=>{state.inboxSearch=$('#inbox-search').value;state.inboxOffset=0;busy(e.target,loadInbox);};
 $('#message-import').onclick=e=>busy(e.target,async()=>{
  const message=(await api('/api/center/messages/import',{sender:$('#message-sender').value,subject:$('#message-subject').value,body:$('#message-body').value,received_at:$('#message-received').value?new Date($('#message-received').value).toISOString():null})).message;
  state.inboxBucket=message.status==='ignored'?'ignored':message.category==='application'?'applications':message.category==='job_alert'?'alerts':message.category==='promotion'?'promotions':'unclassified';state.inboxOffset=0;
  notify(`Imported to ${inboxBuckets[state.inboxBucket]}. No hiring update applied.`);await loadInbox();
 });
 loadInbox().catch(e=>notify(e.message,true));
}
async function loadInbox(){
 const request=++inboxRequest;
 const out=await api('/api/center/messages?'+new URLSearchParams({bucket:state.inboxBucket,search:state.inboxSearch,offset:state.inboxOffset,limit:25}));
 if(request!==inboxRequest||state.view!=='inbox'||!$('#inbox-list'))return;
 state.inboxData=out;
 state.inboxOffset=out.offset;
 const signature=state.inboxBucket+'|'+state.inboxSearch+'|'+JSON.stringify(out);
 if(signature===state.inboxRenderSignature)return;
 $$('#inbox-list [data-inbox-message]').forEach(card=>{const source=$('details',card);if(source)state.inboxOpen[card.dataset.inboxMessage]=source.open;});
 state.inboxRenderSignature=signature;
 $('#inbox-filter').value=state.inboxBucket;
 $$('#inbox-filter option').forEach(option=>option.textContent=`${inboxBuckets[option.value]} (${out.counts[option.value]||0})`);
 $('#inbox-count').textContent=`${out.total} messages in this view · reminders use your browser time zone (${Intl.DateTimeFormat().resolvedOptions().timeZone}).`;
 $('#inbox-list').innerHTML=out.messages.map(renderInboxMessage).join('')||`<p class="empty">No ${esc(inboxBuckets[state.inboxBucket].toLowerCase())} here. Check other filters or synchronize your mailbox.</p>`;
 $$('#inbox-list [data-inbox-message]').forEach(card=>{const source=$('details',card);if(source)source.open=!!state.inboxOpen[card.dataset.inboxMessage];});
 $('#inbox-pagination').innerHTML=`<button data-inbox-page="previous" ${out.offset===0?'disabled':''}>Previous</button><span class="hint">${out.total?out.offset+1:0}–${Math.min(out.total,out.offset+out.messages.length)} of ${out.total} · Page ${Math.floor(out.offset/out.limit)+1} of ${Math.max(1,Math.ceil(out.total/out.limit))}</span><button data-inbox-page="next" ${out.offset+out.messages.length>=out.total?'disabled':''}>Next</button>`;
 $('#inbox-rules').innerHTML=out.ignore_rules.map(rule=>`<p>${esc(rule.sender)} <button data-inbox-unignore="${rule.id}" class="quiet">Stop ignoring sender</button></p>`).join('')||'<p class="hint">No ignored senders.</p>';
}
function renderInboxMessage(m){
 if(m.duplicate_of)return `<article class="credential inbox-message" data-inbox-message="${m.id}"><span class="badge">Duplicate audit copy</span><p><strong>${esc(m.subject)}</strong></p><p class="hint">This retained copy is represented by message #${m.duplicate_of}. Its original source and prior decisions are preserved; it cannot be reviewed twice.</p><details><summary>View duplicate source</summary><p class="description">${esc(m.body)}</p></details><button data-inbox-original="${m.duplicate_of}" data-original-subject="${esc(m.subject)}">Open original in All messages</button></article>`;
 const draft=state.inboxDrafts[m.id]||{}, analysis=m.analysis||{};
 const value=(key,fallback)=>Object.prototype.hasOwnProperty.call(draft,key)?draft[key]:fallback;
 const matched=state.data.jobs.find(j=>j.url===m.job_url);
 const job=value('job',matched?.id||''), outcome=value('outcome',m.proposed_state||'');
 const events=analysis.events||[], dateIndex=Number(value('dateIndex',0));
 const due=value('due',toLocalInput(events[dateIndex]?.datetime||m.proposed_due_at));
 const review=m.status==='review'&&m.category==='application';
 const analyze=m.status==='review'&&!['job_alert','promotion'].includes(m.category);
 return `<article class="credential inbox-message" data-inbox-message="${m.id}"><div class="card-top"><span class="badge">${esc(label(m.category))}</span><span class="badge">${esc(label(m.status))}</span></div><strong>${esc(m.subject)}</strong><p class="hint">${esc(m.sender)}<br>${m.received_at?'Received '+esc(date(m.received_at)):'Received time unknown'} · imported ${esc(date(m.created_at))}</p><details><summary>View source message</summary><p class="description">${esc(m.body)}</p></details>${m.classification_evidence?`<p class="hint">Classification evidence: “${esc(m.classification_evidence)}”</p>`:''}${m.analysis_status==='running'?'<p class="notice">AI analysis is running…</p>':''}${m.analysis_error?`<p class="inline-error">${esc(m.analysis_error)}</p>`:''}${analysis.summary?`<p class="ai-output">${esc(analysis.summary)}</p>`:''}${m.analysis_model?`<p class="hint">${esc(m.analysis_provider)} · ${esc(m.analysis_model)} · AI confidence estimate ${Math.round((m.confidence||0)*100)}%</p>`:''}${analysis.status_evidence?`<p class="notice">Suggested outcome evidence: “${esc(analysis.status_evidence)}”</p>`:''}${(analysis.issues||[]).map(issue=>`<p class="hint inline-error">${esc(issue)}</p>`).join('')}<div class="actions inbox-actions">${analyze?`<button data-inbox-analyze="${m.id}" ${m.analysis_status==='running'?'disabled':''}>${m.analysis_status==='done'?'Analyze / reuse cached result':'Analyze with AI'}</button>`:''}${m.status==='review'?`<button data-inbox-action="dismiss" data-message-id="${m.id}">Dismiss</button><button data-inbox-action="ignore_sender" data-message-id="${m.id}">Ignore sender</button>${m.category!=='application'?`<button data-inbox-move="${m.id}">Move to application review</button>`:''}`:['dismissed','ignored'].includes(m.status)?`<button data-inbox-action="restore" data-message-id="${m.id}">Restore message</button>`:''}</div>${review?`<label>Application<select id="message-job-${m.id}" data-review-field="job"><option value="">Choose application…</option>${state.data.jobs.filter(j=>j.applied_at).map(j=>`<option value="${j.id}" ${String(job)===String(j.id)?'selected':''}>#${j.id} ${esc(j.title)} · ${esc(j.company)}</option>`).join('')}</select></label><p class="hint">${esc(m.match_reason||'No verified application match')}</p><label>Hiring update<select id="message-state-${m.id}" data-review-field="outcome"><option value="">Choose an outcome after reviewing…</option>${['acknowledged','assessment','interview','offer','rejected','withdrawn'].map(s=>`<option value="${s}" ${s===outcome?'selected':''}>${label(s)}</option>`).join('')}</select></label>${events.length?`<label>Extracted dates<select data-review-field="dateIndex">${events.map((event,i)=>`<option value="${i}" ${i===dateIndex?'selected':''}>${esc(event.kind)} · ${event.datetime?esc(date(event.datetime)):esc(event.date_text||'Unresolved date')}</option>`).join('')}</select></label>${events.map(event=>`<p class="hint">“${esc(event.evidence)}”${event.issue?`<br><span class="inline-error">${esc(event.issue)}</span>`:''}</p>`).join('')}`:''}<label><input type="checkbox" data-review-field="useDate" ${value('useDate',false)?'checked':''}> Save a verified next-action reminder</label><label>Reminder time (review or enter manually)<input type="datetime-local" data-review-field="due" value="${esc(due)}"></label>${m.meeting_url?`<p><a href="${safeUrl(m.meeting_url)}" target="_blank" rel="noopener noreferrer">Source meeting link ↗</a></p><label><input type="checkbox" data-review-field="useLink" ${value('useLink',false)?'checked':''}> Add the source meeting link to application notes</label>`:''}${m.proposed_recruiter?`<label><input type="checkbox" data-review-field="useRecruiter" ${value('useRecruiter',false)?'checked':''}> Save contact: ${esc(m.proposed_recruiter)}</label>`:''}<button data-inbox-review="${m.id}" class="primary">Accept reviewed update</button>`:m.status==='accepted'?'<p class="badge">Recorded in application history</p>':'<p class="hint">This message has no application update selected. Dismiss it, ignore its sender, or move it to application review if appropriate.</p>'}</article>`;
}
document.addEventListener('change',e=>{
 const field=e.target.dataset.reviewField, card=e.target.closest('[data-inbox-message]');if(!field||!card)return;
 const id=Number(card.dataset.inboxMessage), draft=state.inboxDrafts[id]||(state.inboxDrafts[id]={});
 draft[field]=e.target.type==='checkbox'?e.target.checked:e.target.value;
 if(field==='dateIndex'){
  const message=state.inboxData.messages.find(m=>m.id===id), event=message.analysis?.events?.[Number(e.target.value)];
  const input=$('[data-review-field="due"]',card);input.value=toLocalInput(event?.datetime);draft.due=input.value;
 }
});
document.addEventListener('click',e=>{
 const button=e.target.closest('button');if(!button)return;
 if(button.disabled)return;
 if(button.dataset.inboxPage){state.inboxOffset=Math.max(0,state.inboxOffset+(button.dataset.inboxPage==='next'?25:-25));busy(button,loadInbox);}
 if(button.dataset.inboxOriginal)busy(button,async()=>{state.inboxBucket='all';state.inboxSearch=button.dataset.originalSubject;state.inboxOffset=0;$('#inbox-search').value=state.inboxSearch;await loadInbox();$(`[data-inbox-message="${button.dataset.inboxOriginal}"]`)?.scrollIntoView({block:'nearest'});});
 if(button.dataset.inboxAnalyze)busy(button,async()=>{await api('/api/center/messages/analyze',{message_id:Number(button.dataset.inboxAnalyze)});notify('AI suggestions ready. Review the quoted evidence before accepting.');await loadInbox();});
 if(button.dataset.inboxAction)busy(button,async()=>{await api('/api/center/messages/action',{message_id:Number(button.dataset.messageId),action:button.dataset.inboxAction});notify(button.dataset.inboxAction==='ignore_sender'?'Sender ignored for future imports.':'Message updated.');await loadInbox();});
 if(button.dataset.inboxMove)busy(button,async()=>{await api('/api/center/messages/action',{message_id:Number(button.dataset.inboxMove),action:'set_category',category:'application'});state.inboxBucket='applications';state.inboxOffset=0;await loadInbox();});
 if(button.dataset.inboxUnignore)busy(button,async()=>{await api('/api/center/messages/unignore',{id:Number(button.dataset.inboxUnignore)});notify('Future messages from this sender will be classified normally. Restore earlier messages separately.');await loadInbox();});
 if(button.dataset.inboxReview)busy(button,async()=>{
  const id=Number(button.dataset.inboxReview), card=button.closest('[data-inbox-message]');
  const field=name=>$('[data-review-field="'+name+'"]',card);
  const job=Number(field('job').value), outcome=field('outcome').value;
  if(!job||!outcome)throw new Error('Choose an application and a verified hiring outcome.');
  const useDate=field('useDate').checked, localDue=field('due').value;
  if(useDate&&!localDue)throw new Error('Choose a verified reminder time.');
  await api('/api/center/messages/review',{message_id:id,id:job,hiring_status:outcome,use_date:useDate,due_at:useDate?new Date(localDue).toISOString():null,include_details:field('useLink')?.checked||false,use_recruiter:field('useRecruiter')?.checked||false});
  delete state.inboxDrafts[id];notify('Reviewed update saved to application history.');await refresh(false);await loadInbox();
 });
});

async function loadMailPanel(){
 const out=await api('/api/center/mail');if(state.view!=='inbox')return;
 const signature=JSON.stringify(out.connections), current=$('#mail-connection-panel');
 if(current?.dataset.signature===signature)return;
 const rows=out.connections;const panel=document.createElement('section');panel.className='panel';panel.style.marginBottom='20px';
 panel.id='mail-connection-panel';
 panel.dataset.signature=signature;
 panel.innerHTML=`<h3>Connected mailboxes</h3><p class="hint">Gmail and Outlook use read-only message access. Automatic synchronization runs every five minutes while the server is running. Messages enter the review inbox.</p><div class="actions">${['gmail','outlook'].map(provider=>{const row=rows.find(r=>r.provider===provider);return row?`<span class="badge">${provider} · ${esc(row.status)}</span><button data-mail-sync="${provider}">Sync now</button><button data-mail-disconnect="${provider}">Disconnect</button><span class="hint">Last sync ${esc(date(row.last_sync))}${row.error?' · '+esc(row.error):''}</span>`:`<button data-mail-connect="${provider}">Connect ${provider}</button>`;}).join('')}</div>`;
 const existing=$('#mail-connection-panel');if(existing)existing.replaceWith(panel);else $('#content').prepend(panel);
}
function renderOperationalInsights(){
 $('#content').insertAdjacentHTML('beforeend',`<div class="split" style="margin-top:20px"><section class="panel"><h2>Scheduled work</h2>${state.data.schedules.map(s=>`<p><button data-detail="${s.job_id}" class="quiet">${esc(s.title||'Removed job')}</button><br><span class="badge">${esc(s.state)}</span> ${esc(date(s.run_at))} · ${esc(s.mode)}<br><span class="hint">${esc(s.detail)}</span>${s.state==='queued'?` <button data-cancel-schedule="${s.id}" class="quiet">Cancel schedule</button>`:''}</p>`).join('')||'<p class="hint">Schedule an application from its record.</p>'}</section><section class="panel"><h2>Assistant usage</h2><p class="hint">Provider-reported tokens for command-center assistant requests. Cached answers do not make another model request. Pricing varies by model and is not inferred here.</p>${state.data.ai_usage.map(u=>`<p>${esc(u.provider)} · ${esc(u.model)}<br><strong>${u.requests}</strong> requests · ${u.input_tokens??'unknown'} input / ${u.output_tokens??'unknown'} output tokens</p>`).join('')||'<p class="hint">No assistant requests yet.</p>'}</section></div>`);
}
function renderPreferences(){
 state.editingAnswer=null;
 const prefs=state.data.preferences;const panel=document.createElement('section');panel.className='panel';panel.style.marginBottom='20px';
 panel.innerHTML=`<h2>Application preferences & answers</h2><div class="split"><div><label>Excluded title terms (one per line)<textarea id="preference-titles">${esc(prefs.excluded_title_terms.join('\n'))}</textarea></label><label>Excluded locations (one per line)<textarea id="preference-locations">${esc(prefs.excluded_locations.join('\n'))}</textarea></label><label><input id="preference-remote" type="checkbox" ${prefs.remote_only?'checked':''}> Prefer remote only for applications</label><p class="hint">These rules exclude matching jobs from preparation and application queues. An unknown location is not treated as an on-site role.</p><button id="save-preferences" class="primary">Save application preferences</button></div><div><label>Reusable screening question<input id="answer-question" placeholder="Exact question from an application form"></label><label>Verified answer<textarea id="answer-text" placeholder="Your truthful answer, ready to reuse"></textarea></label><button id="save-answer">Save verified answer</button><p class="hint">The assisted browser can reuse saved answers for matching questions. These are user-supplied facts.</p>${state.data.answers.map(a=>`<p><strong>${esc(a.question)}</strong><br>${esc(a.answer)}<br><button data-edit-answer="${a.id}" class="quiet">Edit</button><button data-delete-answer="${a.id}" class="quiet danger">Delete</button></p>`).join('')}</div></div>`;
 $('#content').prepend(panel);
 $('#save-preferences').onclick=e=>busy(e.target,async()=>{await api('/api/center/preferences',{excluded_title_terms:lines($('#preference-titles').value),excluded_locations:lines($('#preference-locations').value),remote_only:$('#preference-remote').checked});notify('Application preferences saved.');await refresh();});
 $('#save-answer').onclick=e=>busy(e.target,async()=>{await api('/api/center/answers/save',{id:state.editingAnswer,question:$('#answer-question').value,answer:$('#answer-text').value});notify('Verified answer saved.');await refresh();});
 $$('[data-edit-answer]',panel).forEach(b=>b.onclick=()=>{const answer=state.data.answers.find(a=>a.id===Number(b.dataset.editAnswer));state.editingAnswer=answer.id;$('#answer-question').value=answer.question;$('#answer-text').value=answer.answer;});
 $$('[data-delete-answer]',panel).forEach(b=>b.onclick=()=>busy(b,async()=>{await api('/api/center/answers/delete',{id:Number(b.dataset.deleteAnswer)});await refresh();}));
}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.view)navigate(b.dataset.view);if(b.dataset.detail)busy(b,()=>openDetail(Number(b.dataset.detail)));if(b.dataset.action)busy(b,()=>perform(b.dataset.action,[Number(b.dataset.id)]));if(b.dataset.regen)busy(b,()=>regenerate([Number(b.dataset.regen)]));if(b.dataset.cvTab){if(state.dirty&&!confirm('Discard unsaved edits before switching sections?'))return;state.cvTab=b.dataset.cvTab;dirty(false);renderCv();}if(b.dataset.removeCredential!==undefined){collectCredentials();state.credentials.splice(Number(b.dataset.removeCredential),1);renderCredentials();dirty();}if(b.dataset.removeExperience!==undefined){collectExperiences();state.experiences.splice(Number(b.dataset.removeExperience),1);renderExperiences();dirty();}if(b.dataset.unblock)busy(b,async()=>{await api('/api/center/unblock',{id:Number(b.dataset.unblock)});await refresh();notify('Block rule removed.');});if(b.dataset.ai)busy(b,async()=>{$('#ai-detail').textContent='Reading saved evidence…';const out=await api('/api/center/assistant',{task:b.dataset.ai,id:Number(b.dataset.id)});$('#ai-detail').textContent=out.answer;});if(b.dataset.reviewMessage)busy(b,async()=>{const id=b.dataset.reviewMessage;const job=Number($('#message-job-'+id).value);if(!job)throw new Error('Choose an application to match this message.');await api('/api/center/messages/review',{message_id:Number(id),id:job,hiring_status:$('#message-state-'+id).value});await refresh();notify('Reviewed hiring update saved.');});if(b.dataset.mailConnect)busy(b,async()=>{const out=await api('/api/center/mail/connect',{provider:b.dataset.mailConnect});location.href=out.authorization_url;});if(b.dataset.mailSync)busy(b,async()=>{const out=await api('/api/center/mail/sync',{provider:b.dataset.mailSync});notify(`Mailbox synchronized · ${out.processed} messages processed.`);await refresh();});if(b.dataset.mailDisconnect)busy(b,async()=>{await api('/api/center/mail/disconnect',{provider:b.dataset.mailDisconnect});notify('Mailbox disconnected.');await refresh();});if(b.dataset.cancelSchedule)busy(b,async()=>{await api('/api/center/schedule/cancel',{id:Number(b.dataset.cancelSchedule)});notify('Schedule canceled.');await refresh();});});
document.addEventListener('change',e=>{if(e.target.dataset.select){const id=Number(e.target.dataset.select);if(e.target.checked)state.selected.add(id);else state.selected.delete(id);window.JobLifecycle?.selectionChanged();}});
$('#close-dialog').onclick=()=>$('#job-dialog').close();$('#refresh').onclick=e=>busy(e.target,()=>refresh(!state.dirty));
$('#workspace-country').onchange=e=>busy(e.target,async()=>{
  const next=e.target.value;
  if(!['GB','NZ'].includes(next)||next==='NZ'&&!state.data.nz_workflow_enabled){e.target.value=state.country;return;}
  if(state.dirty&&!confirm('Leave unsaved changes before switching country?')){e.target.value=state.country;return;}
  cancelAssistant('detail',false);cancelAssistant('overview',false);$('#job-dialog').close();
  state.country=next;state.view='settings';state.selected.clear();state.detail=null;state.setup=null;state.dirty=false;
  $('#content').innerHTML='<p class="empty">Loading country context…</p>';await refresh();
});
$('#theme').onclick=()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;localStorage.setItem('applypilot-workspace-theme',theme);};document.documentElement.dataset.theme=localStorage.getItem('applypilot-workspace-theme')||'light';
window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue='';}});
let refreshTimer;function changed(){clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refresh(!state.dirty&&!['cv','inbox','pipeline','portfolio','cover','statement','settings'].includes(state.view)).catch(e=>notify(e.message,true)),400);}
function applyLiveEvent(event){if(state.country==='NZ'){state.eventCursor=event.id;return;}if(event.id<=state.eventCursor)return;state.eventCursor=event.id;if(state.detail&&$('#job-dialog').open&&event.job_url===state.detail.job.url){const list=$('.timeline',$('#job-detail'));if(list&&!$(`[data-event-id="${event.id}"]`,list)){list.insertAdjacentHTML('afterbegin',`<li data-event-id="${event.id}"><strong>${esc(label(event.state||event.kind))}</strong><time>${esc(date(event.created_at))} · ${esc(event.source)}</time><p>${esc(event.detail)}</p></li>`);}}if(event.kind==='submission'||(event.kind==='execution'&&['failed','submission_uncertain','prepared'].includes(event.state)))notify(`${label(event.state)}: ${event.detail||'Application record updated'}`,event.state==='failed');changed();}
const seenReminders=new Set();
function remind(){const upcoming=state.data.jobs.find(j=>j.due_at&&new Date(j.due_at).getTime()<=Date.now()+3600000&&!seenReminders.has(j.id+':'+j.due_at));if(upcoming){seenReminders.add(upcoming.id+':'+upcoming.due_at);notify(`Next action due: ${upcoming.title} · ${date(upcoming.due_at)}`);}}
function connect(){state.eventCursor=state.data.event_cursor||0;if('EventSource' in window){const stream=new EventSource('/api/center/stream?since='+state.eventCursor);stream.onopen=()=>$('#connection').textContent='Live updates connected';stream.onmessage=e=>{try{applyLiveEvent(JSON.parse(e.data));}catch{changed();}};stream.onerror=()=>$('#connection').textContent='Reconnecting · polling available';}setInterval(async()=>{if(document.visibilityState!=='visible')return;try{const events=await api('/api/center/events?since='+state.eventCursor);events.events.forEach(applyLiveEvent);await refresh(false);if(state.country==='NZ')return;if(['jobs','applications'].includes(state.view)&&!$('#job-dialog').open)renderJobs();if(state.view==='inbox'&&!document.activeElement?.closest('.inbox-message')){await loadInbox();await loadMailPanel();}remind();}catch{$('#connection').textContent='Connection unavailable';}},15000);remind();}
let workspaceStarted=false;
$('#refresh').disabled=true;
function initializeWorkspace(){
 if(workspaceStarted)return;
 workspaceStarted=true;
  if(typeof renderNativeSettings!=='function'||typeof renderPipeline!=='function'||typeof renderWritingStudio!=='function'||typeof installNhsPlanner!=='function'||typeof renderCanonicalPanel!=='function'||typeof renderNzDiscovery!=='function'||typeof renderNzEvidence!=='function'||typeof installNzSearch!=='function'||!window.PipelineScope){
  $('#content').innerHTML='<section class="preview-state" data-state="error" role="alert"><h2>Workspace tools could not load.</h2><p>A required page script did not load. Reload the workspace to try again.</p><div class="actions"><button id="reload-workspace" class="primary">Reload workspace</button></div></section>';
  $('#reload-workspace').onclick=()=>location.reload();$('#connection').textContent='Workspace tools unavailable';return;
 }
 // Studios registers additional hash views. Resolve them only once that
 // script, Pipeline and the NHS planner have finished executing.
 const hash=location.hash.slice(1);if(names[hash])state.view=hash;
 api('/api/auth/me').then(out=>{if(out.multi_user&&out.user){$('#account').innerHTML=`${esc(out.user.username)} <button id="logout" class="quiet">Sign out</button>`;$('#logout').onclick=e=>busy(e.target,async()=>{await api('/api/auth/logout',{});location.href='/';});}}).catch(()=>{});
 refresh().then(()=>{$('#refresh').disabled=false;connect();}).catch(e=>{$('#refresh').disabled=false;$('#content').innerHTML=`<p class="empty">${esc(e.message)}</p>`;notify(e.message,true);});
}
// A deferred script runs while readyState is already "interactive". Waiting
// for DOMContentLoaded is essential: later deferred scripts may still be loading.
if(document.readyState==='complete')initializeWorkspace();
else document.addEventListener('DOMContentLoaded',initializeWorkspace,{once:true});
