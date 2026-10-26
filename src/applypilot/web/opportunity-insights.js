/* Analysis ledger: current and retired opportunities share a durable URL key. */
(() => {
 let filters={search:'',kind:'',job_url:'',offset:0},request=0,loaded;
 const query=()=>new URLSearchParams(Object.entries(filters).filter(([,v])=>v!==''&&v!==null));
 function downloads(){const all='/api/center/insights/export';return `<a href="${all}?format=zip" class="primary analysis-download">Download analysis pack ↗</a><a href="${all}?format=json">JSON dataset ↗</a><a href="${all}?format=csv&dataset=opportunities">Opportunities CSV ↗</a><a href="${all}?format=csv&dataset=events">Activity CSV ↗</a>`;}
 async function render(){
  const content=document.querySelector('#content');if(!content||state.view!=='analytics')return;
  const section=document.createElement('section');section.className='panel activity-ledger';section.id='opportunity-ledger';
  section.innerHTML=`<div class="ledger-heading"><div><span class="eyebrow">THE OPPORTUNITY LEDGER</span><h2>Every step, kept in context.</h2><p class="hint">Discovery, preparation, application attempts and employer updates—including archived and deleted records.</p></div><button id="activity-add" class="primary">+ Record activity</button></div><div class="analysis-downloads">${downloads()}</div><p id="activity-coverage" class="hint"></p><div id="activity-stages" class="activity-stages"></div><div class="toolbar ledger-filters"><input id="activity-search" type="search" aria-label="Search activity" placeholder="Find an employer, opportunity or event…" value="${esc(filters.search)}"><select id="activity-kind" aria-label="Activity type"><option value="">All activity types</option></select><select id="activity-opportunity" aria-label="Opportunity"><option value="">All opportunities, including history</option></select><button id="activity-filter">Filter</button></div><p id="activity-total" class="hint" role="status">Loading activity…</p><div id="activity-list" class="activity-list"></div><div id="activity-pages" class="actions"></div><p id="activity-error" class="inline-error" role="alert"></p>`;
  content.prepend(section);
  const visuals=document.createElement('section');visuals.id='insight-visuals';visuals.className='insight-visuals';content.prepend(visuals);
  section.querySelector('#activity-add').onclick=()=>openRecord().catch(e=>notify(e.message,true));
   section.querySelector('#activity-filter').onclick=e=>busy(e.target,async()=>{filters={...filters,search:section.querySelector('#activity-search').value,kind:section.querySelector('#activity-kind').value,job_url:section.querySelector('#activity-opportunity').value,offset:0};await load();});
  section.querySelector('#activity-search').onkeydown=e=>{if(e.key==='Enter')section.querySelector('#activity-filter').click();};
  await load();
 }
 async function load(){
  const epoch=++request,root=document.querySelector('#opportunity-ledger');if(!root)return;
  try{
   const out=await api('/api/center/insights?'+query());if(epoch!==request||!root.isConnected||state.view!=='analytics')return;loaded=out;
   window.InsightsCharts?.render(document.querySelector('#insight-visuals'),out,async values=>{filters=values.reset?{search:'',kind:'',job_url:'',offset:0}:{...filters,...values,offset:0};root.querySelector('#activity-search').value=filters.search||'';await load();},filters);
   root.querySelector('#activity-coverage').textContent=out.coverage;
   root.querySelector('#activity-stages').innerHTML=out.stages.map(s=>`<div><strong>${s.completed}<small>${s.failed?`${s.failed} failed / needs recovery`:''}</small></strong><span>${esc(s.label)} · recorded</span></div>`).join('');
   const kind=root.querySelector('#activity-kind');kind.innerHTML='<option value="">All activity types</option>'+out.kinds.map(k=>`<option value="${esc(k.kind)}">${esc(label(k.kind))} (${k.count})</option>`).join('');kind.value=filters.kind;
   const opportunity=root.querySelector('#activity-opportunity');opportunity.innerHTML='<option value="">All opportunities, including history</option>'+out.opportunities.map(j=>`<option value="${esc(j.job_url)}">#${j.job_id} ${esc(j.title||'Untitled')} · ${esc(j.record_state)}</option>`).join('');opportunity.value=filters.job_url;
   const a=out.activity;root.querySelector('#activity-total').textContent=`${a.total} matching events · ${out.record_count} retained opportunity identities · event time and recording time are exported separately`;
   root.querySelector('#activity-list').innerHTML=a.events.map(e=>{
    const p=e.payload||{},changes=p.after?Object.entries(p.after).filter(([key,value])=>JSON.stringify(value)!==JSON.stringify(p.before?.[key])).map(([key,value])=>`<div><dt>${esc(label(key))}</dt><dd><span class="muted">${esc(formatValue(p.before?.[key]))}</span> → ${esc(formatValue(value))}</dd></div>`).join(''):'';
    const job=out.opportunities.find(j=>j.job_url===e.job_url);const title=e.title||'Workspace activity';
    return `<article class="activity-entry"><div class="activity-stamp"><time datetime="${esc(e.occurred_at)}">${esc(date(e.occurred_at))}</time><span class="badge">${esc(label(e.kind))}</span></div><div><div class="activity-context">${job?.record_state!=='deleted'&&job?`<button data-detail="${job.job_id}" class="quiet">${esc(title)}</button>`:`<strong>${esc(title)}</strong>`}${e.company?`<span class="muted">${esc(e.company)}</span>`:''}<span class="hint">${esc(e.source)}${e.state?' · '+esc(label(e.state)):''}</span></div><p>${esc(e.detail)}</p>${changes?`<details><summary>Changed values</summary><dl class="activity-changes">${changes}</dl></details>`:''}${p.attempt_id?`<span class="hint">Attempt ${esc(p.attempt_id)}</span>`:''}${p.latency_ms!==undefined?`<span class="hint">${p.latency_ms} ms</span>`:''}${e.created_at!==e.occurred_at?`<p class="hint">Recorded ${esc(date(e.created_at))}</p>`:''}</div></article>`;
   }).join('')||'<p class="empty">No activity matches this filter.</p>';
   root.querySelector('#activity-pages').innerHTML=`<button id="activity-prev" ${a.offset===0?'disabled':''}>← Newer</button><span class="hint">${a.total?`${a.offset+1}–${Math.min(a.offset+a.limit,a.total)} of ${a.total}`:'0 events'}</span><button id="activity-next" ${a.offset+a.limit>=a.total?'disabled':''}>Older →</button><a href="/api/center/insights/export?${query()}&format=csv&dataset=events">Download all matching activity ↗</a>`;
   root.querySelector('#activity-prev').onclick=e=>busy(e.target,async()=>{filters.offset=Math.max(0,filters.offset-a.limit);await load();});
   root.querySelector('#activity-next').onclick=e=>busy(e.target,async()=>{filters.offset+=a.limit;await load();});
  }catch(e){if(root.isConnected)root.querySelector('#activity-error').textContent=e.message;}
 }
 function formatValue(value){if(value===null||value===undefined||value==='')return 'Not recorded';return typeof value==='object'?JSON.stringify(value):String(value);}
 async function openRecord(url){
  const out=loaded||await api('/api/center/insights');
  const dialog=document.createElement('dialog');dialog.className='lifecycle-dialog';dialog.setAttribute('aria-labelledby','activity-record-title');
  dialog.innerHTML=`<header class="dialog-top"><span class="eyebrow">CANDIDATE-RECORDED EVIDENCE</span><button id="activity-close" class="quiet">Close ×</button></header><h2 id="activity-record-title">What happened?</h2><label>Opportunity<select id="activity-job">${out.opportunities.map(j=>`<option value="${esc(j.job_url)}">#${j.job_id} ${esc(j.title)} · ${esc(j.record_state)}</option>`).join('')}</select></label><div class="fields"><label>Activity type<select id="activity-category">${['note','employer_contact','follow_up','assessment','interview','offer','rejection','withdrawal','other'].map(k=>`<option value="${k}">${esc(label(k))}</option>`).join('')}</select></label><label>When it happened (optional)<input id="activity-when" type="datetime-local"></label></div><label>Details / evidence<textarea id="activity-detail" rows="5" maxlength="4000" placeholder="What happened, what was said, and what you plan to do next…"></textarea></label><label>Reference (optional)<input id="activity-reference" maxlength="300"></label><p class="hint">This adds a dated event to the analysis history. Use the record’s hiring controls or submission reconciliation to change its status.</p><p id="activity-record-error" class="inline-error" role="alert"></p><button id="activity-save" class="primary">Save activity</button>`;
  document.body.append(dialog);const find=s=>dialog.querySelector(s);if(url)find('#activity-job').value=url;
  find('#activity-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove());
  find('#activity-save').onclick=async e=>{e.target.disabled=true;try{const when=find('#activity-when').value;await api('/api/center/insights/activity',{job_url:find('#activity-job').value,category:find('#activity-category').value,detail:find('#activity-detail').value,reference:find('#activity-reference').value,occurred_at:when?new Date(when).toISOString():null});dialog.close();notify('Activity recorded in the opportunity history.');if(state.view==='analytics')await load();if(state.detail?.job?.url===url)await openDetail(state.detail.job.id);}catch(exc){find('#activity-record-error').textContent=exc.message;}finally{if(e.target.isConnected)e.target.disabled=false;}};
  dialog.showModal();
 }
 document.addEventListener('click',e=>{const button=e.target.closest('[data-record-activity]');if(button)busy(button,()=>openRecord(button.dataset.recordActivity));});
 window.OpportunityInsights={render,openRecord};
})();
