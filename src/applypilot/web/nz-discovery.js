'use strict';

// Reuses workspace authentication, CSRF, typography, panels and country snapshots.
async function renderNzDiscovery() {
  const host = $('#content'), token = {};
  host.nzRender = token;
  const current = () => state.country === 'NZ' && host.nzRender === token;
  const reload = () => { if (current()) return renderNzDiscovery(); };
  host.innerHTML = '<p class="empty" role="status">Loading NZ campaign and source readiness…</p>';
  try {
    const [out, prefs] = await Promise.all([api('/api/center/nz/discovery'), api('/api/center/country/preferences')]);
    if (!current()) return;
    const cfg = out.config, ready = out.readiness, selected = ready.sources.filter(s => s.selected);
    host.innerHTML = `
      <section class="panel nz-discovery-banner">
        <p class="eyebrow">NZ / DISCOVERY WORKSPACE</p>
        <h2>Automatic imports are optional.</h2>
        <p>${ready.automatic_discovery_available ? 'Your selected sources are ready for an in-app import.' : 'No automatic source is selected and ready. Use Find jobs above to search NZ job boards now.'}</p>
        <div class="actions"><span class="badge">${ready.automatic_discovery_available?'Sources ready':'No import sources ready'}</span><span>${out.jobs.length} saved NZ vacancies</span><button id="nz-start" class="primary" ${!out.campaign_installed || !ready.automatic_discovery_available ? 'disabled' : ''}>Import jobs from configured sources</button></div>
        <p class="hint">Use your reviewed CV masters for preparation, then apply with the agent below. Unknown salary or work rights do not remove a vacancy.</p>
      </section>
      <div class="split nz-discovery-layout"><section class="panel">
        <p class="eyebrow">SAVED IMPORT CAMPAIGN</p><h2>Advanced search settings.</h2>
        <form id="nz-campaign-form">
          <label>Role searches · one per line<textarea id="nz-queries" rows="7">${esc(cfg.queries.map(q => q.query).join('\n'))}</textarea></label>
          <label>Selected NZ cities · one per line<textarea id="nz-cities" rows="3">${esc(cfg.locations.filter(p => !p.remote).map(p => p.city || p.location.split(',')[0]).join('\n'))}</textarea></label>
          <p class="hint">Primary: Auckland, Wellington, Christchurch. Optional: Hamilton, Tauranga, Dunedin or another explicitly selected NZ city.</p>
          <label><input id="nz-remote" type="checkbox" ${cfg.locations.some(p => p.remote) ? 'checked' : ''}> Include NZ remote roles (hybrid is not remote-only)</label>
          <div class="fields"><label>Direct target cap<input id="nz-direct-cap" type="number" min="1" max="10000" value="${cfg.smart_target_budget}"></label><label>Workday target cap<input id="nz-workday-cap" type="number" min="1" max="10000" value="${cfg.workday_target_budget}"></label></div>
          <label>NZ compensation preference (JSON; empty object keeps unknowns)<textarea id="nz-compensation" rows="3">${esc(JSON.stringify(cfg.compensation || {}, null, 2))}</textarea></label>
          <p class="hint">Example: {"min":80000,"currency":"NZD","interval":"year","basis":"base"}. No implicit currency conversion or annualisation.</p>
          <h3>Selected sources</h3>
          <div class="stack">${ready.sources.map(s => `<label class="nz-source-choice"><input type="checkbox" data-nz-source="${esc(s.id)}" ${s.selected ? 'checked' : ''}><span>${esc(s.name)} <small>${esc(s.kind)} · ${s.ready ? 'ready' : esc(s.reason)}</small></span></label>`).join('')}</div>
          <p class="hint">An empty selection means no automated sources.</p>
          <button class="primary" type="submit">${out.campaign_installed ? 'Save NZ campaign' : 'Add NZ campaign'}</button>
        </form>
      </section><section class="panel">
        <p class="eyebrow">SAVE A VACANCY</p><h2>Keep a job you found.</h2>
        <form id="nz-capture-form">
          <label>Original vacancy URL<input id="nz-url" type="url" required placeholder="https://careers.employer.nz/job?id=123"></label>
          <div class="fields"><label>Job title<input id="nz-title" required maxlength="300"></label><label>Employer (optional)<input id="nz-company" maxlength="500"></label></div>
          <label>Advertised location<input id="nz-location" placeholder="Auckland, New Zealand"></label>
          <label>Salary as advertised<input id="nz-salary" placeholder="NZD 80k–100k per year + KiwiSaver"></label>
          <label>Original application link (optional)<input id="nz-application" type="url"></label>
          <label><input id="nz-reference" type="checkbox" checked> Reference only · no page fetch, job text or AI use</label>
          <label>Permitted employer / user-owned job text<textarea id="nz-text" rows="6" maxlength="50000" disabled></textarea></label>
          <label>Storage permission reference<input id="nz-permission" disabled placeholder="Agreement or user-owned text reference"></label>
          <label><input id="nz-ai" type="checkbox" disabled> Permission also covers AI use (no AI is called during capture)</label>
          <p class="hint">Restricted boards and INZ remain reference-only. This action never retrieves the URL.</p>
          <button class="primary" type="submit">Save NZ vacancy</button>
        </form>
      </section></div>
      <section class="panel"><p class="eyebrow">03 / SOURCE READINESS</p><h2>Access and evidence, kept visible.</h2>
        <p class="hint">${selected.length} selected. Permission attestations are separate from server-recorded NZ samples. A failed check never becomes a verified empty result.</p>
        <div class="table-wrap"><table><thead><tr><th>Source</th><th>Readiness</th><th>Latest validation</th><th>Read-only check</th></tr></thead><tbody>${ready.sources.map(s => `<tr><td>${esc(s.name)}<br><small>${esc(s.id)}</small></td><td>${s.ready ? 'Ready' : esc(s.reason)}</td><td>${esc(s.source?._nz_validation?.status || 'Not validated')}</td><td><button type="button" data-nz-validate="${esc(s.id)}">Validate NZ sample</button><button type="button" data-nz-edit="${esc(s.id)}">Edit metadata</button></td></tr>`).join('')}</tbody></table></div>
        <details><summary>Add / edit a source access record</summary><form id="nz-source-form">
          <label>Existing adapter<select id="nz-source-kind"><option value="direct">Direct employer JSON-LD</option><option value="workday">Workday CXS</option><option value="board">Existing board (execution blocked)</option></select></label>
          <label>Source configuration and permissions (JSON)<textarea id="nz-source-json" rows="15"></textarea></label>
          <p class="hint">Direct/Workday imports use the source access settings and a current NZ sample. These settings are separate from Apply with agent, which is available for reviewed application packets.</p>
          <button type="submit">Save source record</button>
        </form></details>
      </section>
      <section id="nz-saved-jobs" class="panel"><p class="eyebrow">04 / VACANCY OBSERVATIONS</p><h2>Original links. Distinct dates.</h2>
        <div class="cards">${out.jobs.map(j => {
          const m = j.vacancy_metadata, salary = m.salary || {};
          return `<article class="card"><span class="badge">${esc(m.reference_only ? 'reference only' : m.extraction_method)}</span><h3>${esc(j.title)}</h3><p>${esc(j.company)} · ${esc(j.location || 'Location unknown')}</p><p>${esc(salary.raw || (salary.min ? `${salary.currency || 'Currency unknown'} ${salary.min}${salary.max !== salary.min ? '–' + salary.max : ''} / ${salary.interval || 'interval unknown'}` : 'Salary unknown'))}</p><p class="hint">Advert country: ${esc(m.advertised_country || 'unknown')} · arrangement: ${esc(m.work_arrangement)}<br>Posted: ${esc(m.posted_at || m.posting_date_raw || 'unknown')}<br>First discovered: ${esc(m.first_discovered_at)}<br>Last seen: ${esc(m.last_seen_at)}<br>Source expiry: ${esc(m.source_valid_through || 'unknown')}</p><div class="actions"><a href="${esc(safeUrl(m.original_listing_url || j.url))}" target="_blank" rel="noopener noreferrer">Original listing ↗</a>${m.application_url_original ? `<a href="${esc(safeUrl(m.application_url_original))}" target="_blank" rel="noopener noreferrer">Application link ↗</a>` : ''}<button data-nz-check="${j.id}">Check availability</button></div><p class="hint">Availability: ${esc(m.availability?.status || 'not checked')}. Work-rights evidence is unassessed.</p>${m.possible_duplicates?.length ? '<p class="hint">Similar title/employer records exist; review duplicates manually.</p>' : ''}<details><summary>Saved description and provenance</summary><pre class="nz-job-text">${esc(j.full_description || j.description || 'No job text stored.')}</pre><pre class="nz-job-text">${esc(JSON.stringify(m, null, 2))}</pre></details></article>`;
        }).join('') || '<p class="empty">No NZ vacancies yet. Capture an employer URL above.</p>'}</div>
      </section>
      <div id="nz-evidence-panel"></div>
      <div id="canonical-panel"></div>
      <div id="nz-preparation-panel"></div>
      <details class="panel"><summary>Country-specific preferences</summary><label>Country preference sections (JSON)<textarea id="country-preferences" rows="8">${esc(JSON.stringify(prefs.config, null, 2))}</textarea></label><button id="save-country-preferences">Save country preferences</button></details>`;

    $('#nz-reference').onchange = e => { for (const id of ['nz-text', 'nz-permission', 'nz-ai']) $('#' + id).disabled = e.target.checked; if (e.target.checked) { $('#nz-text').value = ''; $('#nz-ai').checked = false; } };
    $('#nz-campaign-form').onsubmit = e => { e.preventDefault(); busy(e.submitter, async () => {
      const queries = $('#nz-queries').value.split('\n').map(s => s.trim()).filter(Boolean).map(query => ({query, tier: cfg.queries.find(q => q.query === query)?.tier || 1}));
      const locations = $('#nz-cities').value.split('\n').map(s => s.trim()).filter(Boolean).map(city => ({location: city + ', New Zealand', city, country: 'New Zealand', kind: 'city', remote: false}));
      if ($('#nz-remote').checked) locations.push({location: 'Remote, New Zealand', country: 'New Zealand', kind: 'country', remote: true});
      const chosen = new Set($$('[data-nz-source]:checked', host).map(el => el.dataset.nzSource));
      const namesFor = kind => ready.sources.filter(s => s.kind === kind && chosen.has(s.id)).map(s => kind === 'workday' ? s.source._nz_registry_id : s.name);
      await api('/api/center/nz/campaign/save', {expected_revision: out.config_revision, campaign: {...cfg, id: 'nz-job-search', queries, locations, boards: namesFor('board'), smart_sites: namesFor('direct'), workday_employers: namesFor('workday'), smart_target_budget: Number($('#nz-direct-cap').value), workday_target_budget: Number($('#nz-workday-cap').value), compensation: JSON.parse($('#nz-compensation').value)}});
      if (current()) notify('NZ campaign saved.'); await reload();
    }); };
    $('#nz-capture-form').onsubmit = e => { e.preventDefault(); busy(e.submitter, async () => {
      const reference_only = $('#nz-reference').checked;
      const r = await api('/api/center/nz/capture', {url: $('#nz-url').value, title: $('#nz-title').value, company: $('#nz-company').value, location: $('#nz-location').value, salary: $('#nz-salary').value, application_url: $('#nz-application').value, reference_only, job_text: reference_only ? '' : $('#nz-text').value, permission_reference: $('#nz-permission').value, storage_allowed: !reference_only, ai_use_allowed: $('#nz-ai').checked});
      if (current()) notify(`${r.new} NZ vacancies added · ${r.duplicates} existing.`); await reload();
    }); };
    const template = {id: 'nz-employer-careers', name: 'NZ employer careers', type: 'static', url: 'https://careers.example.nz/jobs', countries: ['NZ'], access: {state: 'unreviewed', methods: ['http'], storage_allowed: false, ai_use_allowed: false, application_mode: 'manual', terms_url: '', permission_reference: '', reviewed_at: '', min_interval_seconds: 1, max_requests_per_run: 30, max_cache_minutes: 0}};
    $('#nz-source-json').value = JSON.stringify(template, null, 2);
    $$('[data-nz-edit]', host).forEach(b => b.onclick = () => { const s = ready.sources.find(s => s.id === b.dataset.nzEdit); $('#nz-source-kind').value = s.kind; const item = {...s.source}; if (s.kind === 'workday') item.id = item._nz_registry_id; for (const k of ['_nz_validation', '_nz_registry_id', 'provider_kind', 'origin']) delete item[k]; $('#nz-source-json').value = JSON.stringify(item, null, 2); $('#nz-source-form').parentElement.open = true; $('#nz-source-json').focus(); });
    $('#nz-source-form').onsubmit = e => { e.preventDefault(); busy(e.submitter, async () => { await api('/api/center/nz/source/save', {kind: $('#nz-source-kind').value, source: JSON.parse($('#nz-source-json').value)}); if (current()) notify('Source saved; readiness still requires validation.'); await reload(); }); };
    $$('[data-nz-validate]', host).forEach(b => b.onclick = e => busy(e.target, async () => { const r = await api('/api/center/nz/source/validate', {id: b.dataset.nzValidate}); if (current()) notify(`Source check: ${r.validation.status}`); await reload(); }));
    $$('[data-nz-check]', host).forEach(b => b.onclick = e => busy(e.target, async () => { const r = await api('/api/center/nz/listing/check', {id: Number(b.dataset.nzCheck)}); if (current()) notify(r.detail); await reload(); }));
    $('#save-country-preferences').onclick = e => busy(e.target, async () => { await api('/api/center/country/preferences', {patch: JSON.parse($('#country-preferences').value), expected_revision: prefs.revision}); if (current()) notify('Country preferences saved.'); await reload(); });
    renderNzEvidence($('#nz-evidence-panel'));
    renderCanonicalPanel($('#canonical-panel'),true);
    renderNzPreparation($('#nz-preparation-panel'));
    installNzSearch(host,out);
  } catch (error) {
    if (current()) host.innerHTML = `<section class="panel" role="alert"><h2>NZ discovery could not load.</h2><p>${esc(error.message)}</p><button id="nz-retry">Retry</button></section>`;
    if (current()) $('#nz-retry').onclick = reload;
  }
}
