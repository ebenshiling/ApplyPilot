/* Preserve free-text locations and advanced rows; structure only edited places. */
(() => {
 let original=[],rows=[],cfg={},dirty=new Set();
 const byId=id=>document.getElementById(id);
 function edited(r,i,country){
  if(!dirty.has(i))return structuredClone(r);
  const root=byId('geo-rows').querySelector(`[data-geo-row="${i}"]`),read=key=>root.querySelector(`[data-geo="${key}"]`).value.trim(),target=structuredClone(r),kind=read('kind');
  target.country=read('country')||country;target.state=read('state');target.city=read('city');target.kind=kind;target.remote=kind==='remote';
  if(!target.country)throw new Error('Choose a country for each structured place, or enter a default country.');
  if(kind==='region'&&!target.state)throw new Error('State / region scope needs a state or province.');
  if(kind==='country')target.city=target.state='';
  const parts=[target.city,target.state,target.country.toLowerCase()==='worldwide'?'':target.country].filter(Boolean);target.location=parts.join(', ')||(target.remote?'Remote':target.country);target.label=target.label||target.location;return target;
 }
 function reconcile(){const previous=rows,country=byId('geo-default-country').value.trim(),committed=previous.map((r,i)=>edited(r,i,country));rows=lines(byId('native-locations').value).map(value=>{const index=previous.findIndex(r=>r.location===value);return structuredClone(index>=0?committed[index]:original.find(r=>r.location===value)||{location:value,remote:/^remote$/i.test(value)});});byId('native-locations').value=rows.map(r=>r.location).join('\n');}
 function draw(){
  byId('geo-rows').innerHTML=rows.map((r,i)=>`<article class="geo-place" data-geo-row="${i}"><div class="geo-place-heading"><strong>${esc(r.label||r.location)}</strong><span class="hint">${r.country?esc(r.country):'Uses default country unless specified'}</span></div><div class="geo-place-fields"><label>Country / territory<input data-geo="country" list="geo-countries" value="${esc(r.country||'')}" placeholder="Default country"></label><label>State / province / region<input data-geo="state" value="${esc(r.state||'')}" placeholder="e.g. Ontario, Texas, Greater Accra"></label><label>City<input data-geo="city" value="${esc(r.city||'')}" placeholder="e.g. Toronto, Austin, Accra"></label><label>Search scope<select data-geo="kind">${[['city','City / entered place'],['region','State / region'],['country','Whole country'],['remote','Remote']].map(([value,title])=>`<option value="${value}" ${value===(r.kind||(r.remote?'remote':'city'))?'selected':''}>${title}</option>`).join('')}</select></label></div><p class="hint">For worldwide remote work, use Country “Worldwide” and scope “Remote”. Country, state and city names accept free text.</p></article>`).join('')||'<p class="hint">Add a location above, or create a structured place below.</p>';
  byId('geo-rows').oninput=e=>{const row=e.target.closest('[data-geo-row]');if(row)dirty.add(Number(row.dataset.geoRow));};byId('geo-rows').onchange=byId('geo-rows').oninput;
 }
 function collect(){
  const current=lines(byId('native-locations').value);if(JSON.stringify(current)!==JSON.stringify(rows.map(r=>r.location))){reconcile();dirty.clear();draw();}
  const country=byId('geo-default-country').value.trim(),mode=byId('geo-filter-mode').value;
  const result=rows.map((r,i)=>edited(r,i,country));
  const patch={locations:result,location_filter_mode:mode,geography_enabled:!!cfg.geography_enabled||dirty.size>0||result.some(r=>r.country||r.state||r.city)||mode==='selected'||country!==(cfg.country||cfg.defaults?.country_indeed||cfg.defaults?.country||'')};if(country)patch.country=country;return patch;
 }
 function mount(out){
  cfg=out.config;original=structuredClone(cfg.locations||[]);rows=structuredClone(original);dirty=new Set();
  byId('native-locations').closest('label').insertAdjacentHTML('afterend',`<label>Default search country<input id="geo-default-country" list="geo-countries" value="${esc(cfg.country||cfg.defaults?.country_indeed||cfg.defaults?.country||'')}" placeholder="e.g. United Kingdom, Canada, Ghana"></label><label>Result location filtering<select id="geo-filter-mode"><option value="legacy" ${cfg.location_filter_mode!=='selected'?'selected':''}>Existing accept/reject pattern rules</option><option value="selected" ${cfg.location_filter_mode==='selected'?'selected':''}>Match selected countries, states and cities</option></select></label>`);
  const panel=document.createElement('details');panel.className='geo-editor';panel.innerHTML=`<summary>Country, state & city editor</summary><p class="hint">Keep the one-per-line list for quick edits. Configure exact places here to search several countries in one campaign. Match selected locations uses this selection; existing pattern rules keep your saved legacy filters.</p><datalist id="geo-countries">${(out.geography?.countries||[]).map(c=>`<option value="${esc(c.name)}"></option>`).join('')}</datalist><div id="geo-rows"></div><button id="geo-add-place">+ Add country / state / city</button><p id="geo-editor-error" class="pipeline-error" role="alert"></p>`;
  byId('native-excludes').closest('.fields').insertAdjacentElement('afterend',panel);draw();
  byId('native-locations').addEventListener('change',()=>{try{reconcile();dirty.clear();draw();}catch(e){byId('geo-editor-error').textContent=e.message;}});
  byId('geo-add-place').onclick=()=>{try{const saved=collect();rows=saved.locations;rows.push({location:'New place',country:byId('geo-default-country').value.trim(),state:'',city:'',kind:'city',remote:false});byId('native-locations').value=rows.map(r=>r.location).join('\n');dirty.clear();dirty.add(rows.length-1);draw();byId('geo-rows').lastElementChild.querySelector('[data-geo="country"]').focus();}catch(e){byId('geo-editor-error').textContent=e.message;}};
 }
 window.GeographyEditor={mount,collect};
})();
