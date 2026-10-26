'use strict';

(() => {
 const originalPayload=pipelinePayload,originalPatch=pipelineConfigPatch,originalPreview=pipelinePreviewHtml;
 let lastFlow=null;
 function scopeValues(){return {max_new_jobs:Number($('#scope-max-jobs').value),results_per_search:Number($('#scope-results').value),title_match:$('#scope-title-match').checked,processing_scope:$('#scope-processing').value};}
 pipelineConfigPatch=function(){
  const patch=originalPatch();if(!$('#pipeline-scope-controls'))return patch;
  const prior=new Map(pipelineState.editorQueries.map(q=>[q.query,q.tier||1]));
  patch.queries=lines($('#native-queries').value).map(query=>({query,tier:prior.get(query)||1}));
  patch.smart_sites=$$('[data-source-choice]:checked').map(input=>input.dataset.sourceChoice);patch.smart_sources_disabled=!patch.smart_sites.length;
  patch.discovery_defaults={...scopeValues(),job_boards:$('#scope-jobspy').checked,workday:$('#scope-workday').checked,direct:$('#scope-direct').checked,
   board_search_budget:Number($('#native-board-budget').value),smart_target_budget:Number($('#native-smart-budget').value),workday_target_budget:Number($('#native-workday-budget').value)};
  return patch;
 };
 pipelinePayload=function(){
  const data=originalPayload();if(!$('#pipeline-scope-controls'))return data;
  const discovering=data.stages.includes('discover');
  if(lastFlow!==pipelineState.workflow){$('#scope-processing').value=discovering?'new':'matching';lastFlow=pipelineState.workflow;}
  $('#scope-processing option[value=new]').disabled=!discovering;
  $('#scope-max-jobs').closest('label').firstChild.textContent=discovering?'Maximum new jobs to save':'Maximum saved jobs to process';
  if(!discovering&&$('#scope-processing').value==='new')$('#scope-processing').value='matching';
  data.config_patch=pipelineConfigPatch();data.search_queries=data.config_patch.queries.map(q=>q.query);
  if(!data.search_queries.length)throw new Error('Enter at least one job title to search. A blank list does not mean all roles.');
  data.scope_policy=scopeValues();data.discover_skip_jobspy=!$('#scope-jobspy').checked;data.discover_skip_workday=!$('#scope-workday').checked;data.discover_skip_smarte=!$('#scope-direct').checked;
  return data;
 };
 function requestRows(scope){
  const rows=[];
  for(const t of scope.requests.boards)rows.push({source:t.board,role:t.query,where:t.location});
  for(const t of scope.requests.workday)rows.push({source:'Workday · '+t.name,role:t.query,where:'Filter returned locations against your selection'});
  for(const t of scope.requests.direct)rows.push({source:t.name,role:t.query||'Static page · matching titles only',where:t.location||'Selected-location filter'});
  return rows;
 }
 pipelinePreviewHtml=function(out){
  const scope=out.scope_summary;if(!scope)return originalPreview(out);
  const rows=requestRows(scope),newOnly=scope.processing_scope==='new';
  return `<div class="scope-summary-top"><span class="badge">${newOnly?'New jobs from this search only':'Matching saved jobs only'}</span><h3>At most ${scope.max_new_jobs} ${scope.discovering?'new jobs saved':'saved jobs processed'}.</h3><p>${newOnly?`Only new vacancies saved by this run can be enriched/scored. ${scope.existing_saved_excluded.toLocaleString()} existing saved jobs are excluded.`:`${scope.matching_saved_selected} matching saved jobs are selected for processing. ${scope.existing_saved_excluded.toLocaleString()} other records are excluded.`}</p></div>
   <dl class="scope-facts"><div><dt>Job titles / keywords</dt><dd>${scope.roles.map(esc).join(' · ')}</dd></div><div><dt>Locations</dt><dd>${scope.locations.map(esc).join(' · ')}</dd></div><div><dt>Title filter</dt><dd>${scope.explicit_picks?'Processing explicitly picked jobs; search titles and locations do not replace that selection.':scope.title_match?'Returned title must contain a selected role phrase, with your excluded titles removed.':'Provider keyword matches; titles may vary. Excluded title phrases still apply.'}</dd></div><div><dt>Search limits</dt><dd>${scope.results_per_search} results inspected per board/portal search · ${rows.length} source searches planned · stops early at the new-job cap.</dd></div></dl>
   ${scope.skipped_boards.length?`<p class="hint">Skipped: ${scope.skipped_boards.map(esc).join(', ')} — unavailable in the installed board adapter.</p>`:''}
   ${out.geography?.filter_mode!=='selected'?'<p class="scope-location-note">Location filtering uses the saved legacy pattern rules. Choose “Match selected countries, states and cities” to restrict results to the locations you selected.</p>':''}
   ${scope.whole_country_locations?.length?`<p class="scope-location-note">Whole-country search selected: ${scope.whole_country_locations.map(esc).join(', ')}. Remove it from Locations if you want only the listed cities.</p>`:''}
   ${scope.unscheduled_roles?.length?`<p class="scope-location-note">Outside this bounded search window: ${scope.unscheduled_roles.map(esc).join(', ')}. Increase the search budgets or run again later to rotate through the remaining roles.</p>`:''}
   ${out.missing_inputs.length?`<p class="pipeline-error">Missing for processing: ${out.missing_inputs.map(esc).join(', ')}</p>`:''}${out.start_problem?`<p class="pipeline-error">${esc(out.start_problem)}</p>`:''}
   <details class="scope-request-details"><summary>These searches will run (${rows.length})</summary><div class="table-wrap"><table><thead><tr><th>Source</th><th>Role / search</th><th>Location</th></tr></thead><tbody>${rows.map(t=>`<tr><td>${esc(t.source)}</td><td>${esc(t.role)}</td><td>${esc(t.where)}</td></tr>`).join('')||'<tr><td colspan="3">No discovery sources selected for this workflow.</td></tr>'}</tbody></table></div></details>
   <p class="hint">This previews searches and limits, not guaranteed live vacancies. Job boards and employer feeds can return fewer or unrelated results; the title filter is applied before saving.</p>
   ${scope.examples.length?`<details><summary>Saved titles matching your role text (${scope.examples.length} examples)</summary>${scope.examples.map(j=>`<p>#${j.id} ${esc(j.title)}</p>`).join('')}<p class="hint">Existing examples, not predictions of newly discovered jobs.</p></details>`:''}`;
 };
 function previewReady(out){const button=$('#native-start');if(button)button.disabled=!!pipelineState.running||!out.can_start||!!out.missing_inputs.length;}
 async function reviewAndRun(input){
  const country=state.country,payload=input||pipelinePayload(),signature=JSON.stringify(payload);
  const preview=await pipelineApi('preview',payload);if(state.country!==country||state.view!=='pipeline')return;
  pipelineState.preview=preview;pipelineState.previewSignature=signature;$('#native-preview-content').innerHTML=pipelinePreviewHtml(preview)+pipelineGeographyPreview(preview);
  if(!preview.can_start)throw new Error(preview.start_problem||'Choose a valid source and scope before starting.');
  if(preview.missing_inputs.length)throw new Error('Complete the listed processing inputs before starting.');
  const dialog=document.createElement('dialog');dialog.className='scope-review-dialog';dialog.setAttribute('aria-label','Review discovery scope');
  dialog.innerHTML=`<div class="dialog-top"><p class="eyebrow">REVIEW THE EXACT RUN</p><button data-scope-cancel class="quiet">Cancel ×</button></div><h2>Search this scope?</h2>${pipelinePreviewHtml(preview)}<p id="scope-review-error" class="pipeline-error" role="alert"></p><div class="actions"><button id="scope-run-confirm" class="primary">Start this run</button><button data-scope-cancel>Back to settings</button></div>`;
  document.body.append(dialog);dialog.showModal();dialog.addEventListener('close',()=>dialog.remove());dialog.querySelectorAll('[data-scope-cancel]').forEach(b=>b.onclick=()=>dialog.close());
  const button=dialog.querySelector('#scope-run-confirm');let starting=false;
  button.onclick=async()=>{
   if(starting)return;starting=true;button.disabled=true;
   try{
    if(state.country!==country||state.view!=='pipeline')throw new Error('Country or page changed. Review this search again.');
    if(!input&&signature!==JSON.stringify(pipelinePayload()))throw new Error('Settings changed. Go back and review the updated scope.');
    const result=await pipelineApi('start',{...payload,expected_preview_token:preview.preview_token});dialog.close();notify('Bounded workflow started · '+result.run_id);await pipelinePoll();
   }catch(error){if(dialog.isConnected)dialog.querySelector('#scope-review-error').textContent=error.message;}
   finally{starting=false;if(button.isConnected)button.disabled=false;}
  };
 }
 function install(out){
  const defaults=out.config.discovery_defaults||{},cfg=out.config;
  $('#native-queries').value=pipelineState.queryOverride||(cfg.queries||[]).map(q=>q.query).join('\n');pipelineState.queryOverride=$('#native-queries').value;
  const label=$('#native-queries').closest('label');label.firstChild.textContent='Job titles / keywords to search (one per line)';$('#native-queries').rows=4;
  const controls=document.createElement('section');controls.id='pipeline-scope-controls';controls.className='scope-controls';
  controls.innerHTML=`<p class="eyebrow">SEARCH SCOPE & LIMITS</p><div class="scope-provider-choices"><label><input id="scope-jobspy" type="checkbox" ${defaults.job_boards!==false?'checked':''}> Job boards</label><label><input id="scope-workday" type="checkbox" ${defaults.workday?'checked':''}> Workday employer portals</label><label><input id="scope-direct" type="checkbox" ${defaults.direct?'checked':''}> Selected direct websites</label></div><p class="hint">Only checked provider groups run. Choose boards and direct websites in the settings below.</p><div class="fields"><label>Maximum new jobs to save<input id="scope-max-jobs" type="number" min="1" max="500" value="${defaults.max_new_jobs||50}"></label><label>Results to inspect per board / portal search<input id="scope-results" type="number" min="1" max="50" value="${defaults.results_per_search||10}"></label></div><label><input id="scope-title-match" type="checkbox" ${defaults.title_match!==false?'checked':''}> Save only matching job titles</label><label>Jobs to process<select id="scope-processing"><option value="new">Only NEW jobs from this search</option><option value="matching">Matching saved jobs (bounded by the same job limit)</option></select></label><p class="hint">The default Find & assess run does not score your existing backlog. Unsaved search edits are included in this run's preview; Save search configuration retains them.</p>`;
  label.after(controls);lastFlow=null;
  const places=document.createElement('div');places.className='scope-visible-locations fields';places.append($('#native-locations').closest('label'),$('#geo-default-country').closest('label'),$('#geo-filter-mode').closest('label'));controls.before(places);
  for(const option of [...$('#native-campaign').options])if(out.campaigns.find(c=>c.id===option.value)?.country_code==='NZ')option.remove();
  for(const [id,title] of [['native-board-budget','Max board searches'],['native-smart-budget','Max direct-page searches'],['native-workday-budget','Max Workday portal searches']]){
   const input=$('#'+id);input.min='1';input.max='100';input.value=String(defaults[{ 'native-board-budget':'board_search_budget','native-smart-budget':'smart_target_budget','native-workday-budget':'workday_target_budget'}[id]]||6);input.closest('label').firstChild.textContent=title;
  }
  for(const [provider,key] of [['jobspy','jobspy'],['workday','workday'],['direct','smarte']])$('#native-skip-'+key).closest('label').hidden=true;
  $('#native-start').textContent='Review & run';$('#native-start').disabled=true;
  $('#native-start').onclick=e=>busy(e.target,()=>reviewAndRun());
  $('#native-preview').onclick=e=>busy(e.target,()=>pipelineUpdatePreview());
  $('#native-campaign').onchange=e=>{pipelineState.campaign=e.target.value;pipelineState.queryOverride='';renderPipeline();};
  $('#pipeline-shell').addEventListener('input',e=>{
   if(e.target.closest('[data-query-row]')){pipelineCollectQueries();$('#native-queries').value=pipelineState.editorQueries.map(q=>q.query).filter(Boolean).join('\n');pipelineState.queryOverride=$('#native-queries').value;}
   if(e.target.id==='native-queries'){pipelineState.queryOverride=e.target.value;const prior=new Map(pipelineState.editorQueries.map(q=>[q.query,q]));pipelineState.editorQueries=lines(e.target.value).map(query=>prior.get(query)||{query,tier:1,variant:''});pipelineRenderQueryEditor();}
   if(e.target.closest('#pipeline-scope-controls,#native-config,.scope-visible-locations')||e.target.matches('[data-source-choice]'))pipelineSchedulePreview();
  });
  $('#pipeline-shell').addEventListener('change',e=>{if(e.target.closest('#pipeline-scope-controls,#native-config,.scope-visible-locations')||e.target.matches('[data-source-choice]'))pipelineSchedulePreview();});
 }
 window.PipelineScope={install,previewReady,reviewAndRun};
 document.addEventListener('click',event=>{const button=event.target.closest('[data-pipeline-resume]');if(!button)return;event.preventDefault();event.stopImmediatePropagation();busy(button,async()=>{const run=pipelineState.data.runs.find(r=>r.run_id===button.dataset.pipelineResume),payload=pipelinePayload();payload.stages=run?.config.stages||payload.stages;payload.scope_policy.processing_scope=payload.stages.includes('discover')?'new':'matching';await reviewAndRun(payload);});},true);
})();
