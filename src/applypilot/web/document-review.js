'use strict';

async function openDocumentReview(id) {
  const country=state.country,dialog=document.createElement('dialog');
  dialog.setAttribute('aria-label','Review final application documents');
  dialog.innerHTML='<button type="button" data-review-close>Close ×</button><p role="status">Loading exact current artifacts…</p>';
  document.body.append(dialog);dialog.showModal();
  const close=()=>{dialog.close();dialog.remove();};$('[data-review-close]',dialog).onclick=close;
  const prefix=country==='NZ'?'/api/center/nz/documents/review':'/api/center/documents/review';
  try {
    const out=await api(prefix+'?id='+id);if(!dialog.isConnected||state.country!==country){close();return;}
    const packet=out.packet,review=packet.document_review;
    dialog.innerHTML=`<button type="button" data-review-close>Close ×</button><p class="eyebrow">EXACT FILES / HUMAN REVIEW</p><h2>Review the actual documents.</h2><p>Factual validation and PDF round-trip checks are complete only where marked current. Review every included artifact, then record your document approval. Submission requires a separate fresh approval.</p>
      <div class="stack">${packet.documents.filter(d=>d.required||d.included).map(d=>{const link=(country==='NZ'?'/api/center/nz/packet/document':'/api/center/browser/document')+'?id='+id+'&kind='+encodeURIComponent(d.kind)+'&country_code='+country;return `<section class="panel"><h3>${esc(d.label)}</h3><p>${esc(d.pdf_state)}</p>${d.text_ready?`<a href="${link}&format=html" target="_blank" rel="noopener noreferrer">Review TXT / HTML ↗</a>`:''} ${d.pdf_state==='ready'?`<a href="${link}&format=pdf" target="_blank" rel="noopener noreferrer">Review actual PDF ↗</a>`:''}</section>`;}).join('')}</div>
      <p class="hint">${review.approved?'Current approval recorded by '+esc(review.actor):'No current human document approval.'}</p><p>${esc(packet.missing.concat(packet.excluded).join('; '))}</p>
      <label><input id="final-documents-human-reviewed" type="checkbox" ${!review.documents_ready?'disabled':''}> I reviewed the exact current documents and their country/rights context</label><button type="button" id="final-documents-record" class="primary" ${!review.documents_ready?'disabled':''}>Record final-document review</button><p id="final-document-review-feedback" role="status"></p>`;
    $('[data-review-close]',dialog).onclick=close;
    $('#final-documents-record',dialog).onclick=e=>busy(e.target,async()=>{if(state.country!==country)throw new Error('Country context changed; reopen this review.');if(!$('#final-documents-human-reviewed',dialog).checked)throw new Error('Review the included actual TXT/PDF artifacts first.');await api(prefix,{id,human_reviewed:true,expected_review_token:review.review_token});if(dialog.isConnected&&state.country===country)$('#final-document-review-feedback',dialog).textContent='Document review recorded. This does not authorize or perform a submission.';});
  } catch(error) {if(dialog.isConnected){dialog.innerHTML=`<button type="button" data-review-close>Close ×</button><p role="alert">${esc(error.message)}</p>`;$('[data-review-close]',dialog).onclick=close;}}
}

document.addEventListener('click',event=>{const button=event.target.closest('[data-document-review]');if(!button)return;event.preventDefault();openDocumentReview(Number(button.dataset.documentReview));});

async function openApplicationHandoff(id){
  const country=state.country,prefix=country==='NZ'?'/api/center/nz/submit/':'/api/center/submit/';
  const result=await api(prefix+'review',{id});if(state.country!==country)return;
  const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','Application handoff');
  dialog.innerHTML=`<button type="button" data-handoff-close>Close ×</button><p class="eyebrow">APPLICATION / NEXT STEP</p><h2>${result.mode==='assisted_browser'?'Apply with your reviewed documents.':result.mode==='manual_handoff'?'Continue in your own browser.':'Supported fixture review.'}</h2><p>${esc(result.detail||'Review the packet and the final employer page before submission.')}</p>${result.mode==='assisted_browser'?'<button type="button" data-handoff-agent class="primary">Apply with agent</button>':''}${result.target?`<a href="${esc(safeUrl(result.target))}" target="_blank" rel="noopener noreferrer">Open original application ↗</a>`:''}<p class="hint">Opening a link is not a submission. Your application record is updated after a checked result.</p>`;
  const agent=dialog.querySelector('[data-handoff-agent]');if(agent)agent.onclick=e=>busy(e.target,async()=>{const detail=await api('/api/center/detail?id='+id);dialog.close();dialog.remove();await window.AssistedBrowser.open(detail.job,detail);});
  document.body.append(dialog);dialog.showModal();$('[data-handoff-close]',dialog).onclick=()=>{dialog.close();dialog.remove();};
}
document.addEventListener('click',event=>{const button=event.target.closest('[data-application-handoff]');if(!button)return;event.preventDefault();busy(button,()=>openApplicationHandoff(Number(button.dataset.applicationHandoff)));});
