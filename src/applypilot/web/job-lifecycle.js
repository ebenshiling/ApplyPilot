/* Reasoned closure, deletion and compact read-only history. */
(() => {
 const size=n=>n<1024?n+' B':n<1048576?(n/1024).toFixed(1)+' KB':(n/1048576).toFixed(2)+' MB';
 let offset=0,search='',request=0;
 let selectionView=null,bulkRunning=false,feedback=null;
 async function returnToJobs(country){
  if(state.country!==country)return;
  $('#job-dialog').close();state.detail=null;state.view='jobs';state.dirty=false;history.replaceState(null,'','#jobs');
  await refresh(false);
  if(country==='NZ'){await renderNzDiscovery();$('#nz-review-stage')?.scrollIntoView({block:'start'});}else draw();
 }
 function modal(title,body){const d=document.createElement('dialog');d.className='lifecycle-dialog';d.setAttribute('aria-labelledby','lifecycle-title');d.innerHTML=`<header class="dialog-top"><span class="eyebrow">APPLICATION LIFECYCLE</span><button data-lifecycle-close class="quiet">Close ×</button></header><h2 id="lifecycle-title">${esc(title)}</h2>${body}`;document.body.append(d);d.querySelector('[data-lifecycle-close]').onclick=()=>d.close();d.addEventListener('close',()=>d.remove());d.showModal();return d;}
 async function open(id){
  const out=await api('/api/center/lifecycle/preview?id='+id);
  const d=modal('Close the record. Clear the clutter.',`<p class="muted">${esc(out.job.title)} · #${id}</p>${out.listing_check?`<p class="notice">Listing: ${esc(label(out.listing_check.status))} · ${esc(out.listing_check.detail)}</p>`:''}<label>Reason<select id="lifecycle-reason">${Object.entries(out.reasons).map(([v,name])=>`<option value="${v}" ${v===out.reason?'selected':''}>${name}</option>`).join('')}</select></label><label>What happened?<textarea id="lifecycle-note" maxlength="2000" rows="3" placeholder="Record the closure, missing essential evidence, preparation problem or employer outcome…">${esc(out.closure?.detail||'')}</textarea></label>${out.preparation.tailor_status?`<details><summary>Recorded CV preparation evidence</summary><p>${esc(out.preparation.tailor_status)}</p><p class="description">${esc(out.preparation.tailor_failure_detail)}</p><pre>${esc(out.preparation.tailor_requirement_gaps)}</pre></details>`:''}<fieldset class="lifecycle-choices"><legend>How should ApplyPilot keep this?</legend><label><input type="radio" name="lifecycle-mode" value="close" checked><span><strong>Close and archive</strong><small>Remove from active work. Keep the documents and full record; restore it later if needed.</small></span></label><label><input type="radio" name="lifecycle-mode" value="delete"><span><strong>Delete job & generated files</strong><small>Keep title, outcome, dates, reference and activity timeline in History. Removed files cannot be restored.</small></span></label></fieldset><div class="lifecycle-storage"><div><strong>${out.files.length}</strong><span>exclusive generated files</span></div><div><strong>${size(out.reclaimable_bytes)}</strong><span>potential file space recovered</span></div><div><strong>${out.retained_files.length}</strong><span>shared files retained</span></div></div><p class="hint">Source CVs, profile, portfolio evidence, verified answers and inbox messages are preserved.</p><details><summary>Files included in cleanup</summary>${out.files.map(f=>`<p class="hint">${esc(f.path)} · ${size(f.bytes)}</p>`).join('')||'<p>No exclusive files found.</p>'}</details><p id="lifecycle-blockers" class="inline-error" role="status"></p><p id="lifecycle-error" class="inline-error" role="alert"></p><button id="lifecycle-save" class="primary">Close record</button>`);
  const q=sel=>d.querySelector(sel);
  function choice(){const deleting=q('[name=lifecycle-mode]:checked').value==='delete',blockers=deleting?out.delete_blockers:out.close_blockers;q('#lifecycle-save').textContent=deleting?'Delete job & generated files':'Close record';q('#lifecycle-save').className=deleting?'danger':'primary';q('#lifecycle-save').disabled=!!blockers.length;q('#lifecycle-blockers').textContent=blockers.join('; ');}
  d.querySelectorAll('[name=lifecycle-mode]').forEach(i=>i.onchange=choice);choice();
   q('#lifecycle-save').onclick=async e=>{e.target.disabled=true;const mode=q('[name=lifecycle-mode]:checked').value,country=state.country;try{const result=await api('/api/center/lifecycle/'+(mode==='delete'?'delete':'close'),{id,reason:q('#lifecycle-reason').value,detail:q('#lifecycle-note').value,cleanup_token:out.cleanup_token});d.close();if(state.country===country)state.selected.delete(id);await returnToJobs(country);notify(mode==='delete'?`Job deleted · ${size(result.freed_bytes)} removed${result.pending_files?' · '+result.pending_files+' files need cleanup retry':''}. Timeline saved in History.`:'Opportunity closed and archived; reason saved in the timeline.');}catch(exc){if(d.isConnected){q('#lifecycle-error').textContent=exc.message;choice();}else notify(exc.message,true);}};
 }
 function selectionChanged(){
  const view=selectionView;if(!view||!view.host.isConnected||view.country!==state.country)return;
  const shown=view.jobs.map(j=>Number(j.id)),chosen=shown.filter(id=>state.selected.has(id));
  const all=view.host.querySelector('[data-select-all-jobs]');all.checked=shown.length>0&&chosen.length===shown.length;all.indeterminate=chosen.length>0&&chosen.length<shown.length;all.disabled=!shown.length;
  const count=state.selected.size,hidden=count-chosen.length;
  view.host.querySelector('[data-selection-count]').textContent=`${count} selected${hidden>0?' · '+hidden+' outside this filter':''}`;
  view.host.querySelector('[data-bulk-delete-jobs]').disabled=!count||bulkRunning;
  view.host.querySelector('[data-clear-job-selection]').disabled=!count;
  view.host.querySelectorAll('input[data-select]').forEach(input=>{input.checked=state.selected.has(Number(input.dataset.select));});
 }
 function mountSelection(host,jobs,allJobs=state.data?.jobs||[]){
  if(!host)return;
  const valid=new Set(allJobs.map(j=>Number(j.id)));for(const id of state.selected)if(!valid.has(id))state.selected.delete(id);
  host.querySelector('[data-job-selection]')?.remove();host.querySelector('[data-bulk-delete-feedback]')?.remove();
  for(const job of jobs){
   if(host.querySelector(`input[data-select="${Number(job.id)}"]`))continue;
   const card=host.querySelector(`[data-nz-check="${Number(job.id)}"]`)?.closest('article');
   if(card)card.insertAdjacentHTML('afterbegin',`<label class="job-selection-card"><input type="checkbox" data-select="${Number(job.id)}" aria-label="Select ${esc(job.title)}"> Select job</label>`);
  }
  const bar=document.createElement('div');bar.dataset.jobSelection='';bar.className='job-selection-bar';
  bar.innerHTML=`<label><input type="checkbox" data-select-all-jobs aria-label="Select all shown jobs"> Select all shown (${jobs.length})</label><span data-selection-count role="status" aria-live="polite"></span><button data-clear-job-selection>Clear selection</button><button data-bulk-delete-jobs class="danger" disabled>Delete selected</button>`;
  const toolbar=host.querySelector('.toolbar');if(toolbar)toolbar.after(bar);else host.prepend(bar);
  selectionView={host,jobs,allJobs,country:state.country};
  bar.querySelector('[data-select-all-jobs]').onchange=e=>{for(const job of jobs){if(e.target.checked)state.selected.add(Number(job.id));else state.selected.delete(Number(job.id));}selectionChanged();};
  bar.querySelector('[data-clear-job-selection]').onclick=()=>{state.selected.clear();selectionChanged();};
  bar.querySelector('[data-bulk-delete-jobs]').onclick=e=>busy(e.target,()=>openBulk([...state.selected],allJobs));
  if(feedback&&feedback.country===state.country){
   const result=document.createElement('section');result.dataset.bulkDeleteFeedback='';result.className='bulk-delete-feedback';
   const failures=feedback.results.filter(r=>!r.deleted);
   result.innerHTML=`<div class="actions"><strong>${esc(feedback.summary)}</strong><button class="quiet" data-dismiss-delete-feedback aria-label="Dismiss deletion summary">Dismiss ×</button></div>${failures.length?`<details><summary>Records not deleted or not confirmed (${failures.length})</summary><ul>${failures.slice(0,50).map(r=>`<li>#${r.id} ${esc(r.title)} — ${esc(r.error)}</li>`).join('')}</ul>${failures.length>50?'<p class="hint">Showing the first 50 results. Remaining existing records stay selected.</p>':''}</details>`:''}`;
   result.querySelector('[data-dismiss-delete-feedback]').onclick=()=>{feedback=null;result.remove();};bar.after(result);
  }
  selectionChanged();
 }
 async function deletionApi(path,body){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),90000);try{return await api(path,body,{signal:controller.signal});}finally{clearTimeout(timer);}}
 function openBulk(ids,allJobs){
  if(bulkRunning)throw new Error('A bulk deletion is already in progress.');
  const chosen=[...new Set(ids)].filter(id=>Number.isInteger(id)&&id>0);if(!chosen.length)throw new Error('Select at least one job to delete.');
  const country=state.country,lookup=new Map(allJobs.map(j=>[Number(j.id),j]));
  const d=modal(`Delete ${chosen.length} selected job${chosen.length===1?'':'s'}?`,`<p>This removes the selected job records and their generated application files. Compact history and timelines are retained. Source CVs, profile data and files still used by other records are preserved.</p><details><summary>Selected records (${chosen.length})</summary><ul class="bulk-delete-job-list">${chosen.slice(0,50).map(id=>`<li>#${id} ${esc(lookup.get(id)?.title||'Job record')}</li>`).join('')}</ul>${chosen.length>50?`<p class="hint">And ${chosen.length-50} more selected records.</p>`:''}</details><label>Reason<select id="bulk-delete-reason"><option value="not_pursuing">No longer pursuing</option><option value="advert_closed">Advert closed</option><option value="requirements_not_met">Requirements not met</option><option value="preparation_failed">CV / document preparation failed</option></select></label><label>Note (optional)<textarea id="bulk-delete-note" rows="2" maxlength="2000"></textarea></label><p class="hint">Each record is checked before deletion. Active or uncertain applications are kept and reported below.</p><p id="bulk-delete-progress" role="status" aria-live="polite"></p><progress id="bulk-delete-meter" max="${chosen.length}" value="0" hidden></progress><div class="actions"><button id="bulk-delete-confirm" class="danger">Delete ${chosen.length} job${chosen.length===1?'':'s'} & generated files</button><button id="bulk-delete-stop" hidden>Stop remaining</button></div>`);
  const q=sel=>d.querySelector(sel);let running=false,stop=false;
  function stopRemaining(){stop=true;q('#bulk-delete-stop').disabled=true;q('#bulk-delete-progress').textContent='Stopping after the current record. Already completed deletions will be kept.';}
  q('[data-lifecycle-close]').onclick=()=>{if(running)stopRemaining();else d.close();};
  d.addEventListener('cancel',e=>{if(running){e.preventDefault();stopRemaining();}});
  q('#bulk-delete-stop').onclick=stopRemaining;
  q('#bulk-delete-confirm').onclick=async()=>{
   if(running||bulkRunning)return;if(state.country!==country){q('#bulk-delete-progress').textContent='Country changed. Close this dialog and select records in the current country.';return;}
   running=true;bulkRunning=true;stop=false;q('#bulk-delete-confirm').disabled=true;q('#bulk-delete-reason').disabled=true;q('#bulk-delete-note').disabled=true;q('#bulk-delete-stop').hidden=false;q('#bulk-delete-meter').hidden=false;
   const reason=q('#bulk-delete-reason').value,detail=q('#bulk-delete-note').value,results=[];let freed=0,pending=0;
   try{
    for(const id of chosen){
     if(stop||state.country!==country)break;
     const title=lookup.get(id)?.title||'Job record';let dispatched=false;q('#bulk-delete-progress').textContent=`Checking ${results.length+1} of ${chosen.length}: ${title}`;
     try{
      const plan=await deletionApi('/api/center/lifecycle/preview?id='+id);
      if(stop||state.country!==country)break;
      if(!lookup.get(id)?.url||plan.job.url!==lookup.get(id).url)throw new Error('This job identity changed. Refresh and select the current record before deleting it.');
      if(plan.delete_blockers.length){results.push({id,title,deleted:false,error:plan.delete_blockers.join('; ')});}
      else{
       q('#bulk-delete-progress').textContent=`Deleting ${results.length+1} of ${chosen.length}: ${title}`;
       dispatched=true;
       const result=await deletionApi('/api/center/lifecycle/delete',{id,reason,detail,cleanup_token:plan.cleanup_token});
       if(!result.deleted)throw new Error('Deletion was not confirmed. Refresh this record before retrying.');
       results.push({id,title,deleted:true});freed+=Number(result.freed_bytes)||0;pending+=Number(result.pending_files)||0;
       if(state.country===country)state.selected.delete(id);
      }
     }catch(error){results.push({id,title,deleted:false,unconfirmed:dispatched,error:error.name==='AbortError'?'Request timed out; refresh before retrying.':(dispatched?'Deletion was not confirmed. Refresh before retrying. ':'')+error.message});if(error.name==='AbortError')stop=true;}
     q('#bulk-delete-meter').value=results.length;
    }
   }finally{
    running=false;bulkRunning=false;
    const done=new Set(results.map(r=>r.id));for(const id of chosen)if(!done.has(id))results.push({id,title:lookup.get(id)?.title||'Job record',deleted:false,error:'Not processed; remaining work was stopped.'});
    const deleted=results.filter(r=>r.deleted).length,remaining=results.length-deleted,unconfirmed=results.filter(r=>r.unconfirmed).length,notDeleted=remaining-unconfirmed;
    const summary=`${deleted} job${deleted===1?'':'s'} deleted · ${size(freed)} removed${notDeleted?' · '+notDeleted+' not deleted':''}${unconfirmed?' · '+unconfirmed+' unconfirmed':''}${pending?' · '+pending+' files need cleanup retry':''}.`;
    feedback={country,summary,results};d.close();
    try{await returnToJobs(country);notify(summary,remaining>0);}catch(error){notify(summary+' Refresh Jobs to load the current list. '+error.message,true);}
   }
  };
 }
 async function renderHistory(){
  const current=++request,out=await api('/api/center/lifecycle/history?offset='+offset+'&search='+encodeURIComponent(search));if(current!==request||state.view!=='history')return;
  const s=out.storage;
  $('#content').innerHTML=`<section class="panel lifecycle-history"><h2>The story stays. The files can go.</h2><p class="hint">Deleted jobs retain a compact record and timeline here. Closed records with documents are available through the Archived filter in Jobs.</p><div class="lifecycle-storage"><div><strong>${size(s.generated_bytes)}</strong><span>generated application files</span></div><div><strong>${size(s.database_bytes)}</strong><span>database file</span></div><div><strong>${size(s.reusable_database_bytes)}</strong><span>database space available for reuse</span></div></div><p class="hint">${s.pending_files} files await cleanup. SQLite reuses freed database pages; compact it when pipeline/application work is idle to return space to disk.</p><button id="history-compact">Compact database</button><div class="toolbar"><input id="history-search" type="search" value="${esc(search)}" aria-label="Search application history" placeholder="Title or employer…"><button id="history-find">Search history</button></div><div class="table-wrap"><table><thead><tr><th>Opportunity</th><th>Reason / outcome</th><th>History</th></tr></thead><tbody>${out.records.map(r=>`<tr><td><strong>${esc(r.snapshot.title)}</strong><br>${esc(r.snapshot.company||r.snapshot.site)} · original #${r.original_job_id}<br>${esc(date(r.deleted_at))}</td><td>${esc(out.reasons[r.reason]||label(r.reason))}${r.snapshot.hiring_status?'<br>Hiring: '+esc(label(r.snapshot.hiring_status)):''}${r.snapshot.reference?'<br>Reference: '+esc(r.snapshot.reference):''}</td><td><button data-history-detail="${esc(r.id)}">View timeline</button>${r.pending_files?`<button data-cleanup-retry="${esc(r.id)}">Retry ${r.pending_files} files</button>`:''}</td></tr>`).join('')||'<tr><td colspan="3">No deleted records yet.</td></tr>'}</tbody></table></div><div class="actions"><button id="history-prev" ${offset?'':'disabled'}>Previous</button><span>${out.total} records · ${out.records.length?offset+1:0}–${Math.min(offset+out.records.length,out.total)}</span><button id="history-next" ${offset+out.records.length<out.total?'':'disabled'}>Next</button></div></section>`;
  $('#history-find').onclick=()=>{search=$('#history-search').value;offset=0;renderHistory().catch(e=>notify(e.message,true));};$('#history-prev').onclick=()=>{offset=Math.max(0,offset-50);renderHistory();};$('#history-next').onclick=()=>{offset+=50;renderHistory();};
  $('#history-compact').onclick=e=>busy(e.target,async()=>{const out=await api('/api/center/lifecycle/compact',{});notify('Database compacted · '+size(out.freed_bytes)+' returned to disk.');await renderHistory();});
  $$('[data-history-detail]').forEach(b=>b.onclick=e=>busy(e.target,()=>showHistory(b.dataset.historyDetail)));
  $$('[data-cleanup-retry]').forEach(b=>b.onclick=e=>busy(e.target,async()=>{const out=await api('/api/center/lifecycle/retry',{history_id:b.dataset.cleanupRetry});notify(size(out.freed_bytes)+' removed · '+out.pending_files+' files remain.');await renderHistory();}));
 }
 async function showHistory(id){const out=await api('/api/center/lifecycle/detail?id='+encodeURIComponent(id)),r=out.record,s=r.snapshot;modal(s.title,`<p>${esc(s.company||s.site)} · original #${r.original_job_id}</p><p><span class="badge">${esc(label(r.reason))}</span> ${esc(r.detail)}</p><p class="hint">Submitted: ${esc(date(s.applied_at))} · Reference: ${esc(s.reference||'Not recorded')}</p>${s.notes?`<p class="description">${esc(s.notes)}</p>`:''}<ol class="timeline">${out.events.map(e=>`<li><strong>${esc(label(e.state||e.kind))}</strong><time>${esc(date(e.created_at))} · ${esc(e.source)}</time><p>${esc(e.detail)}</p></li>`).join('')}</ol><details><summary>File cleanup results</summary>${out.cleanup.map(f=>`<p class="hint">${esc(f.path)} · ${esc(f.state)} ${esc(f.detail)}</p>`).join('')}</details>`);}
 document.addEventListener('click',e=>{const b=e.target.closest('[data-job-lifecycle]');if(b)busy(b,()=>open(Number(b.dataset.jobLifecycle)));});
 const originalActions=actions;
 actions=j=>j.closure_reason?`<p class="hint">Closed: ${esc(label(j.closure_reason))}</p><div class="card-actions"><button data-detail="${j.id}">View closed record</button><button data-action="restore" data-id="${j.id}">Restore</button><button data-job-lifecycle="${j.id}">Delete record</button></div>`:originalActions(j);
  window.JobLifecycle={open,renderHistory,mountSelection,selectionChanged,openBulk};
})();
