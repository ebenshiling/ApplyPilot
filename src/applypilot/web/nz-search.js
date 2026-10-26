'use strict';

const nzSearchState={query:'',location:'Auckland',result:null,openSections:new Set()};

function installNzSearch(host,out){
 const owner=host.nzRender,current=()=>host.isConnected&&host.nzRender===owner&&state.country==='NZ';
 const banner=host.querySelector('.nz-discovery-banner'),layout=host.querySelector('.nz-discovery-layout');
 const campaign=$('#nz-campaign-form',host).closest('section'),capture=$('#nz-capture-form',host).closest('section');
 const sources=$('#nz-source-form',host).closest('section'),saved=$('#nz-saved-jobs',host);
 const locations=['Auckland','Wellington','Christchurch','Hamilton','Tauranga','Dunedin','New Zealand','Remote, New Zealand'];
 const stepNav=document.createElement('nav');stepNav.className='nz-journey';stepNav.setAttribute('aria-label','New Zealand job search steps');
 stepNav.innerHTML=[['nz-search-step','Search','Job title + location'],['nz-review-stage','Review jobs','Save a vacancy you like'],['nz-documents-stage','Prepare','Choose and review your CV'],['nz-apply-step','Apply','Submit and track the result']].map(([id,title,note],i)=>`<a href="#${id}" data-nz-step="${id}"><span>0${i+1}</span><strong>${title}</strong><small>${note}</small></a>`).join('');
 const search=document.createElement('section');search.id='nz-search-step';search.className='panel nz-search-start';
 search.innerHTML=`<div class="nz-search-intro"><p class="eyebrow">01 / START HERE</p><h2>What role are you looking for?</h2><p>You only need a job title and an NZ location. Employer details and CV setup come later.</p></div>
  <form id="nz-search-form"><div class="nz-search-fields"><label>Job title or keywords<input id="nz-search-role" list="nz-role-suggestions" required maxlength="160" placeholder="e.g. Application Support Engineer" value="${esc(nzSearchState.query)}"><datalist id="nz-role-suggestions">${out.config.queries.map(q=>`<option value="${esc(q.query)}"></option>`).join('')}</datalist></label><label>Location<select id="nz-search-location">${locations.map(v=>`<option value="${esc(v)}" ${v===nzSearchState.location?'selected':''}>${v==='New Zealand'?'All New Zealand':esc(v)}</option>`).join('')}</select></label><button id="nz-find-jobs" class="primary" type="submit">Find jobs</button></div></form>
  <p class="hint">Search opens matching results on NZ job boards. Saved vacancies appear in Step 2 below.</p><div id="nz-search-results" aria-live="polite"></div>`;
 host.prepend(search);host.prepend(stepNav);
 const review=document.createElement('section');review.id='nz-review-stage';review.className='nz-review-stage';
 review.innerHTML=`<div class="nz-stage-heading"><div><p class="eyebrow">02 / REVIEW JOBS</p><h2>Keep the opportunities you want.</h2><p>Found a job on a board? Save its URL and title here. Employer is optional.</p></div><button id="nz-show-save">Save a vacancy</button></div>`;
 search.after(review);
 const save=document.createElement('details');save.id='nz-save-vacancy';save.className='panel nz-optional';save.innerHTML='<summary>Save a job you found</summary>';
 save.append(capture);review.append(save,saved);
 const captureForm=$('#nz-capture-form',host),urlLabel=$('#nz-url',host).closest('label'),titleLabel=$('#nz-title',host).closest('label'),titleRow=titleLabel.parentElement,saveButton=$('button[type=submit]',captureForm);
 const vacancyDetails=document.createElement('details');vacancyDetails.id='nz-vacancy-details';vacancyDetails.innerHTML='<summary>Optional: employer, salary, location and job text</summary>';
 vacancyDetails.append($('#nz-company',host).closest('label'));titleRow.before(titleLabel);titleRow.remove();
 for(const child of [...captureForm.children])if(child!==urlLabel&&child!==titleLabel&&child!==saveButton)vacancyDetails.append(child);
 captureForm.insertBefore(vacancyDetails,saveButton);saveButton.insertAdjacentHTML('beforebegin','<p class="hint">Only URL and job title are required. This saves a reference link; optional job text supports preparation when its source allows it.</p>');
 saved.querySelector('.eyebrow').textContent='YOUR SAVED NZ VACANCIES';saved.querySelector('h2').textContent='Jobs you have saved.';
 saved.querySelector('.empty')?.replaceChildren(document.createTextNode('No saved jobs yet. Search above, then save the URL and title of a vacancy you want to review.'));
 window.JobLifecycle?.mountSelection(saved,out.jobs,out.jobs);
 const advanced=document.createElement('details');advanced.id='nz-advanced';advanced.className='panel nz-optional';
 advanced.innerHTML='<summary>Advanced: automatic imports and saved search campaign</summary><p class="hint">Optional. Configure sources here only when you want jobs imported directly into ApplyPilot. The job-board search above works without this setup.</p><div id="nz-import-status" class="nz-import-status" role="status" aria-live="polite"></div><div class="actions"><button id="nz-import-check">Check import status</button><button id="nz-import-stop" hidden>Stop import</button><button id="nz-import-view-jobs" hidden>View latest saved jobs</button></div>';
 advanced.append(banner,campaign,sources);layout.remove();
 const documents=document.createElement('details');documents.id='nz-documents-stage';documents.className='panel nz-optional';
 documents.innerHTML='<summary>03 / Prepare documents — after choosing a job</summary><p class="hint">Review your CV sources and facts, then score and prepare documents for a saved vacancy.</p>';
 documents.append($('#canonical-panel',host),$('#nz-preparation-panel',host));review.after(documents);
 const apply=document.createElement('section');apply.id='nz-apply-step';apply.className='panel nz-apply-step';
 apply.innerHTML=`<p class="eyebrow">04 / APPLY & TRACK</p><h2>Ready to apply?</h2><p>Choose a saved vacancy after reviewing its documents. The agent will show its readiness and the employer form.</p><div class="nz-apply-fields"><label>Saved NZ vacancy<select id="nz-apply-job"><option value="">Choose a saved vacancy…</option>${out.jobs.map(j=>`<option value="${j.id}">${esc(j.title)}${j.company?' · '+esc(j.company):''}</option>`).join('')}</select></label><button id="nz-apply-review" class="primary">Review / apply</button><button id="nz-apply-record">Application record</button></div><p class="hint">Opening a vacancy does not submit an application. The browser asks for final approval at the employer's submit step.</p>`;
 documents.after(apply);apply.after(advanced);
 const employer=document.createElement('details');employer.id='nz-employer-checks';employer.className='panel nz-optional';
 employer.innerHTML='<summary>Optional: employer and sponsorship evidence</summary><p class="hint">Check an employer after you find a vacancy. This is not required to search.</p>';employer.append($('#nz-evidence-panel',host));advanced.after(employer);
 for(const section of [save,documents,advanced,employer]){section.open=nzSearchState.openSections.has(section.id);section.addEventListener('toggle',()=>{if(current()){if(section.open)nzSearchState.openSections.add(section.id);else nzSearchState.openSections.delete(section.id);}});}
 $('#nz-search-role',host).oninput=e=>{nzSearchState.query=e.target.value;};$('#nz-search-location',host).onchange=e=>{nzSearchState.location=e.target.value;};
 $('#nz-show-save',host).onclick=()=>{save.open=true;save.scrollIntoView({block:'start',behavior:'smooth'});$('#nz-url',host).focus();};
 $$('[data-nz-step]',stepNav).forEach(link=>link.onclick=e=>{e.preventDefault();const target=$('#'+link.dataset.nzStep,host);if(target.tagName==='DETAILS')target.open=true;target.scrollIntoView({block:'start',behavior:'smooth'});});
 function selectedJob(){const id=Number($('#nz-apply-job',host).value);if(!id)throw new Error('Choose a saved NZ vacancy first.');return id;}
 $('#nz-apply-review',host).onclick=e=>busy(e.target,async()=>{const detail=await api('/api/center/detail?id='+selectedJob());if(current())await window.AssistedBrowser.open(detail.job,detail);});
 $('#nz-apply-record',host).onclick=e=>busy(e.target,()=>openDetail(selectedJob()));
 function showSearch(result){
  if(!current())return;
  $('#nz-search-results',host).innerHTML=`<div class="nz-search-result"><span class="badge">Job-board search links</span><h3>${esc(result.query)} · ${esc(result.location)}</h3><p>Choose a board to view matching vacancies in a new tab.</p><div class="nz-board-links">${result.links.map(link=>`<a href="${esc(safeUrl(link.url))}" target="_blank" rel="noopener noreferrer"><strong>Open ${esc(link.name)} results ↗</strong><span>Search on the job board</span></a>`).join('')}</div><p class="hint">These are external search pages, not vacancies imported into ApplyPilot.</p><button id="nz-save-after-search">Found a job? Save its link here</button></div>`;
  $('#nz-save-after-search',host).onclick=()=>{save.open=true;$('#nz-location',host).value=result.location.includes('New Zealand')?result.location:result.location+', New Zealand';review.scrollIntoView({block:'start',behavior:'smooth'});$('#nz-url',host).focus();};
 }
 if(nzSearchState.result)showSearch(nzSearchState.result);
 async function timedApi(path,body,seconds=15){const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),seconds*1000);try{return await api(path,body,{signal:controller.signal});}finally{clearTimeout(timeout);}}
 let finding=false;
 $('#nz-search-form',host).onsubmit=async e=>{
  e.preventDefault();if(finding)return;finding=true;const button=$('#nz-find-jobs',host);button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='Preparing search…';
  try{const query=$('#nz-search-role',host).value.trim(),location=$('#nz-search-location',host).value;const result=await timedApi('/api/center/nz/search',{query,location});if(!current())return;nzSearchState.query=query;nzSearchState.location=location;nzSearchState.result=result;showSearch(result);}
  catch(error){if(current())$('#nz-search-results',host).innerHTML=`<p class="inline-error" role="alert">${esc(error.name==='AbortError'?'Search preparation timed out. Your entries are retained; click Find jobs to retry.':error.message)}</p>`;}
  finally{finding=false;if(current()){button.disabled=false;button.removeAttribute('aria-busy');button.textContent='Find jobs';}}
 };
 const importButton=$('#nz-start',host),statusNode=$('#nz-import-status',host),stopButton=$('#nz-import-stop',host);
 const ready=out.campaign_installed&&out.readiness.automatic_discovery_available;
 let status=out.import_status||{state:'idle',active:false,detail:'No automatic import is running.'},importing=false,pollTimer,checks=0,errors=0,statusRequest=0,watchStarted=Date.now();
 function showStatus(message){
  if(!current())return;
  statusNode.dataset.state=status.state;statusNode.textContent=message||status.detail;
  if(status.counts)statusNode.textContent+=` ${status.counts.new} new saved · ${status.counts.existing} already saved · ${status.counts.errors} source errors.`;
  importButton.disabled=!ready||status.active||status.other_workflow_active||importing;
  importButton.textContent=importing?'Starting import…':status.active?'Import running…':'Import jobs from configured sources';
  if(importing)importButton.setAttribute('aria-busy','true');else importButton.removeAttribute('aria-busy');
  stopButton.hidden=!status.active||!status.run_id;$('#nz-import-view-jobs',host).hidden=!['completed','partial','failed','interrupted','stopped'].includes(status.state);
 }
 async function checkStatus(manual=false){
  clearTimeout(pollTimer);if(!current())return;
  const request=++statusRequest;
  if(manual){checks=0;errors=0;watchStarted=Date.now();}
  try{const result=await timedApi('/api/center/nz/discovery/status'+(status.run_id?'?run_id='+encodeURIComponent(status.run_id):''));if(!current()||request!==statusRequest)return;status=result;errors=0;checks++;showStatus();
   if(status.active){if(checks<30&&Date.now()-watchStarted<120000)pollTimer=setTimeout(()=>checkStatus(),4000);else showStatus('The import is still running. Automatic checking has paused. Use Check import status or Stop import.');}}
  catch(error){if(!current()||request!==statusRequest)return;errors++;showStatus(error.name==='AbortError'?'Status request timed out. Use Check import status to try again.':error.message);if(status.active&&errors<3)pollTimer=setTimeout(()=>checkStatus(),4000);}
 }
 importButton.onclick=async()=>{
  if(importing||status.active||!ready)return;importing=true;clearTimeout(pollTimer);++statusRequest;showStatus('Starting one automatic import…');
  try{const result=await timedApi('/api/center/nz/start',{});if(!current())return;status={run_id:result.run_id,state:'starting',active:true,detail:'Automatic import is starting.',counts:null};checks=0;errors=0;watchStarted=Date.now();checkStatus();}
  catch(error){if(current()){status={...status,active:false,state:'failed',detail:error.name==='AbortError'?'Start acknowledgement timed out. Check import status before trying another start.':error.message};showStatus();}}
  finally{importing=false;showStatus();}
 };
 $('#nz-import-check',host).onclick=e=>busy(e.target,()=>checkStatus(true));
 stopButton.onclick=e=>busy(e.target,async()=>{await timedApi('/api/center/nz/stop',{run_id:status.run_id});if(current())await checkStatus(true);});
 $('#nz-import-view-jobs',host).onclick=async()=>{await renderNzDiscovery();if(state.country==='NZ')$('#nz-review-stage')?.scrollIntoView({block:'start',behavior:'smooth'});};
 showStatus();if(status.active)checkStatus();
}
