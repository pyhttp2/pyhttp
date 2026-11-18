'use strict';
// ============================================================ sections as pages
// Requests, Flows, Monitors and Mocks are the sections of the rail. Each has its list in the
// sidebar and its page in the middle of the window. This file is the Monitors and the Mocks
// page (the dialogs that edit one monitor, mock server or route stay in features.js) and the
// switch between sections.
const pages = {
  monitor: null, monFilter: 'all', monSearch: '', checks: {}, checksFor: null, failuresOnly: false,
  mock: null, mockTab: 'routes', mockSearch: '', routeSearch: '', unmatchedOnly: false, calls: [], callTimer: null,
};

const currentSection = () => $('.layout').dataset.section || 'collection';

function showSection(name) {
  const tab = $(`#side-tabs button[data-tab="${name}"]`);
  if (tab && !tab.classList.contains('active')) tab.click();
}

function onSection(name) {
  const layout = $('.layout');
  if (name === 'monitors' || name === 'mocks') layout.dataset.section = name;
  else delete layout.dataset.section;
  clearInterval(pages.callTimer);
  pages.callTimer = null;
  if (name === 'monitors') { renderMonitors(); loadMonitors(true); }
  if (name === 'mocks') { renderMocks(); loadMocks(true); }
  savePlace();
}

// ------------------------------------------- where you are, kept in the URL so a refresh stays put
// #flows, #monitors, #mocks, with the open item after a slash (#monitors/<id>). Requests is the
// plain address. replaceState: moving around does not fill the browser's Back history.
const PLACE_TABS = ['flows', 'monitors', 'mocks'];
const activeTab = () => { const b = $('#side-tabs button[data-tab].active'); return b ? b.dataset.tab : 'collection'; };

function savePlace() {
  const tab = activeTab();
  const id = tab === 'monitors' ? pages.monitor : tab === 'mocks' ? pages.mock
    : tab === 'flows' && typeof flowUi !== 'undefined' && flowUi.flow ? flowUi.flow.id : null;
  const hash = PLACE_TABS.includes(tab) ? '#' + tab + (id ? '/' + encodeURIComponent(id) : '') : '';
  if (location.hash !== hash) {
    try { history.replaceState(history.state, '', hash || location.pathname + location.search); } catch { /* sandboxed */ }
  }
  if (typeof saveSession === 'function') saveSession();   // the section is part of the last session (features.js)
}

function restorePlace() {
  const [tab, raw] = location.hash.slice(1).split('/');
  if (!PLACE_TABS.includes(tab)) return;
  let id = null;
  try { id = raw ? decodeURIComponent(raw) : null; } catch { /* a mangled address */ }
  if (tab === 'monitors') pages.monitor = id;
  if (tab === 'mocks') pages.mock = id;
  if (tab === 'flows' && id && typeof flowUi !== 'undefined') flowUi.restore = id;   // opened once the list is read (flows.js)
  showSection(tab);
}

const pageTile = (label, value, cls = '') => `<div class="page-tile ${cls}"><div class="pt-label">${label}</div><div class="pt-value">${value}</div></div>`;

// ================================================================== monitors
const monitorById = (id) => mon.list.find((m) => m.id === id) || null;
const monitorState = (m) => !m.enabled ? 'off' : m.last_status === 'fail' ? 'fail' : m.last_status === 'pass' ? 'pass' : 'waiting';
const MON_BADGE = { off: '<span class="badge">paused</span>', fail: '<span class="badge failed">failing</span>',
                    pass: '<span class="badge ok">passing</span>', waiting: '<span class="badge">waiting</span>' };

function monitorUptime(m) {
  const recent = m.recent || [];
  if (!recent.length) return null;
  return Math.round(recent.filter((c) => c.ok).length / recent.length * 1000) / 10;
}

function showMonitors(id) {
  closeModal();
  if (id !== undefined) pages.monitor = id;
  if (currentSection() !== 'monitors') showSection('monitors');
  else renderMonitors();
}

function renderMonitors() {
  renderMonitorList();
  if (currentSection() === 'monitors') { renderMonitorPage(); savePlace(); }
}

function renderMonitorList() {
  const ul = $('#mon-list');
  if (!ul) return;
  if (pages.monitor && mon.loaded && !monitorById(pages.monitor)) pages.monitor = null;   // not before the list is read: a refresh restores it
  const counts = { all: mon.list.length, fail: mon.list.filter((m) => monitorState(m) === 'fail').length,
                   off: mon.list.filter((m) => !m.enabled).length };
  $('#mon-filter').innerHTML = [['all', 'All'], ['fail', 'Failing'], ['off', 'Paused']].map(([key, label]) =>
    `<button class="chip ${pages.monFilter === key ? 'active' : ''}" data-filter="${key}">${label}<span class="n">${counts[key]}</span></button>`).join('');
  $$('#mon-filter .chip').forEach((b) => b.addEventListener('click', () => { pages.monFilter = b.dataset.filter; renderMonitorList(); }));
  const q = pages.monSearch.trim().toLowerCase();
  const shown = mon.list.filter((m) => (!q || m.name.toLowerCase().includes(q))
    && (pages.monFilter === 'all' || (pages.monFilter === 'fail' ? monitorState(m) === 'fail' : !m.enabled)));
  if (!shown.length) {
    ul.innerHTML = `<li class="page-none muted small">${mon.list.length ? 'No monitor matches.' : 'No monitors yet. Press “+ New”.'}</li>`;
    return;
  }
  ul.innerHTML = shown.map((m) => `<li class="page-item mon-item ${m.enabled ? '' : 'off'} ${pages.monitor === m.id ? 'active' : ''}" data-id="${m.id}" tabindex="0">
      <span class="page-dot s-${monitorState(m)}" title="${monitorState(m)}"></span>
      <span class="page-item-text"><span class="page-item-name">${esc(m.name)}</span>
        <span class="page-item-meta muted mon-meta">${m.request_ids.length} request${m.request_ids.length === 1 ? '' : 's'} · every ${fmtInterval(m.interval_s)}${m.last_run_at ? ` · ${agoText(m.last_run_at)}` : ''}</span></span>
      <label class="switch tiny" title="Pause or resume this monitor"><input type="checkbox" data-act="toggle" ${m.enabled ? 'checked' : ''}><span></span></label>
    </li>`).join('');
  $$('.mon-item', ul).forEach((li) => {
    const m = monitorById(li.dataset.id);
    const open = () => { pages.monitor = m.id; pages.failuresOnly = false; renderMonitors(); };
    li.addEventListener('click', (e) => { if (!e.target.closest('.switch')) open(); });
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target === li) open(); });
    $('[data-act=toggle]', li).addEventListener('change', (e) => toggleMonitor(m, e.target.checked));
  });
}

async function toggleMonitor(m, on) {
  try { await api('/monitors/' + m.id, { method: 'PATCH', body: { enabled: on } }); } catch (err) { toast(err.message, 'error'); }
  loadMonitors(true);
}

async function runMonitorNow(m, button) {
  if (button) { button.disabled = true; button.innerHTML = '<span class="spin"></span>Running…'; }
  try {
    const chk = await api(`/monitors/${m.id}/run-now`, { method: 'POST' });
    toast(chk.ok ? `${m.name}: all ${chk.total} passed` : `${m.name}: ${chk.failed} of ${chk.total} failed`, chk.ok ? 'ok' : 'error');
  } catch (err) { toast(err.message, 'error'); }
  pages.checksFor = null;   // read the checks again
  loadMonitors(true);
}

function renderMonitorPage() {
  const view = $('#monitor-view');
  if (!view) return;
  const m = pages.monitor && monitorById(pages.monitor);
  if (!m) { renderMonitorOverview(view); return; }
  const status = monitorState(m);
  const uptime = monitorUptime(m);
  const recent = m.recent || [];
  const average = recent.length ? recent.reduce((sum, c) => sum + (c.duration_ms || 0), 0) / recent.length : null;
  const requests = m.request_ids.map((id) => (state.requests || []).find((r) => r.id === id) || { id, name: '(deleted request)', method: '' });
  view.innerHTML = `
    <div class="page-head">
      <div class="page-title"><button class="link page-back" data-act="back" title="All monitors">Monitors</button><span class="muted">/</span>
        <h2>${esc(m.name)}</h2>${MON_BADGE[status]}</div>
      <div class="page-actions">
        <label class="switch-label" title="Pause or resume this monitor"><span class="switch"><input type="checkbox" data-act="toggle" ${m.enabled ? 'checked' : ''}><span></span></span>${m.enabled ? 'On' : 'Paused'}</label>
        <button data-act="edit">Edit</button>
        <button class="danger" data-act="del">Delete</button>
        <button class="primary" data-act="run">${icon('play')} Run now</button>
      </div>
    </div>
    <div class="page-tiles">
      ${pageTile('Last check', m.last_run_at ? agoText(m.last_run_at) : 'not run yet')}
      ${pageTile('Every', fmtInterval(m.interval_s))}
      ${pageTile(`Uptime, last ${recent.length || 0} checks`, uptime == null ? '–' : `${uptime}%`, uptime == null ? '' : uptime === 100 ? 'good' : uptime < 90 ? 'bad' : '')}
      ${pageTile('Average time', average == null ? '–' : fmtMs(average))}
      ${pageTile('Webhook on failure', m.webhook_url ? `<span class="mono small" title="${esc(m.webhook_url)}">${esc(m.webhook_url.replace(/^https?:\/\//, '').slice(0, 28))}</span>` : '<span class="muted">none</span>')}
    </div>
    <div class="page-scroll">
      <section class="page-card">
        <h3>Recent checks <span class="muted small">oldest to newest; height is the time each took</span></h3>
        ${monSpark(recent)}
      </section>
      <section class="page-card">
        <h3>Requests it sends, in this order</h3>
        <div class="page-reqs">${requests.map((r, i) => `<button class="page-req" data-req="${esc(r.id)}" ${r.method ? '' : 'disabled'} title="${r.method ? 'Open this request' : ''}">
          <span class="muted">${i + 1}</span><span class="method m-${esc(r.method)}">${esc(r.method || '–')}</span><span class="page-req-name">${esc(r.name)}</span></button>`).join('')}</div>
      </section>
      <section class="page-card">
        <div class="page-card-head"><h3>Check history</h3><span class="grow"></span>
          <label class="small"><input type="checkbox" id="mon-fail-only" ${pages.failuresOnly ? 'checked' : ''}> Failures only</label></div>
        <div id="mon-checks"><p class="muted small">Loading…</p></div>
      </section>
    </div>`;
  $('[data-act=back]', view).addEventListener('click', () => { pages.monitor = null; renderMonitors(); });
  $('[data-act=toggle]', view).addEventListener('change', (e) => toggleMonitor(m, e.target.checked));
  $('[data-act=edit]', view).addEventListener('click', () => monitorDialog(m));
  $('[data-act=run]', view).addEventListener('click', (e) => runMonitorNow(m, e.currentTarget));
  $('[data-act=del]', view).addEventListener('click', async () => {
    if (!await confirmDialog('Delete monitor?', `${strong(m.name)} and its check history will be removed. The requests themselves stay.`, { okLabel: 'Delete', danger: true })) return;
    try { await api('/monitors/' + m.id, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
    pages.monitor = null;
    loadMonitors(true);
  });
  $$('.page-req[data-req]', view).forEach((b) => b.addEventListener('click', async () => {
    showSection('collection');
    try { await openRequest(b.dataset.req); } catch (err) { toast(err.message, 'error'); }
  }));
  $('#mon-fail-only').addEventListener('change', (e) => { pages.failuresOnly = e.target.checked; renderMonitorChecks(m); });
  renderMonitorChecks(m);
  loadMonitorChecks(m);
}

async function loadMonitorChecks(m) {
  const stamp = `${m.id}:${m.last_run_at || 0}`;
  if (pages.checksFor === stamp) return;   // nothing ran since they were read
  let checks;
  try { checks = await api(`/monitors/${m.id}/checks?limit=100`); } catch { return; }
  pages.checks[m.id] = checks;
  pages.checksFor = stamp;
  if (pages.monitor === m.id) renderMonitorChecks(m);
}

function renderMonitorChecks(m) {
  const box = $('#mon-checks');
  if (!box) return;
  const all = pages.checks[m.id];
  if (!all) return;
  const checks = pages.failuresOnly ? all.filter((c) => !c.ok) : all;
  if (!checks.length) {
    box.innerHTML = `<p class="muted small">${all.length ? 'No failed checks in the last ' + all.length + '.' : 'No checks yet. “Run now” sends one at once.'}</p>`;
    return;
  }
  box.innerHTML = `<table class="htable"><tr><th>When</th><th>Result</th><th>Took</th><th>What failed</th></tr>${checks.map((c) => {
    const failed = (c.results || []).filter((r) => !r.ok)
      .map((r) => `${esc(r.name)} (${r.status_code ?? esc((r.error || 'error').slice(0, 80))}${r.tests_failed ? `, ${r.tests_failed} tests` : ''})`).join(', ');
    return `<tr><td>${new Date(c.ts * 1000).toLocaleString()}</td>
      <td class="${c.ok ? 'ok-text' : 'err-text'}">${c.ok ? 'pass' : `${c.failed}/${c.total} failed`}</td>
      <td>${fmtMs(c.duration_ms)}</td><td class="mon-fails">${failed}</td></tr>`;
  }).join('')}</table>`;
}

// nothing picked: every monitor at a glance, and the switches for all of them
function renderMonitorOverview(view) {
  if (!mon.list.length) {
    view.innerHTML = `<div class="empty page-empty">
      <svg class="empty-mark" viewBox="0 0 24 24" width="40" height="40" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" d="M3 12h4l2.5-6 4 12 2.5-6h5"/></svg>
      <p>A monitor sends the requests you pick, again and again on a schedule.</p>
      <p class="muted small">A check fails when a request errors, answers 4xx/5xx or fails one of its tests.<br>Failures show in the status bar and can POST to a webhook.</p>
      <p><button class="primary" data-act="new">+ New monitor</button></p></div>`;
    $('[data-act=new]', view).addEventListener('click', () => monitorDialog());
    return;
  }
  const failing = mon.list.filter((m) => monitorState(m) === 'fail').length;
  const on = mon.list.filter((m) => m.enabled).length;
  view.innerHTML = `
    <div class="page-head">
      <div class="page-title"><h2>Monitors</h2></div>
      <div class="page-actions">
        <button data-act="pause-all" ${on ? '' : 'disabled'} title="Switch every monitor off">Pause all</button>
        <button data-act="resume-all" ${on === mon.list.length ? 'disabled' : ''} title="Switch every monitor on">Resume all</button>
        <button data-act="run-all" ${on ? '' : 'disabled'} title="Send a check of every monitor that is on, now">${icon('play')} Run all now</button>
        <button class="primary" data-act="new">+ New monitor</button>
      </div>
    </div>
    <div class="page-tiles">
      ${pageTile('Monitors', mon.list.length)}
      ${pageTile('Running', on)}
      ${pageTile('Failing', failing, failing ? 'bad' : 'good')}
      ${pageTile('Paused', mon.list.length - on)}
    </div>
    <div class="page-scroll">
      <table class="htable page-table"><tr><th>Monitor</th><th>Status</th><th>Last check</th><th>Every</th><th>Uptime</th><th>Recent</th><th></th></tr>
      ${mon.list.map((m) => { const up = monitorUptime(m); return `<tr class="page-row" data-id="${m.id}" title="Open this monitor">
        <td class="page-row-name">${esc(m.name)}</td><td>${MON_BADGE[monitorState(m)]}</td>
        <td>${m.last_run_at ? agoText(m.last_run_at) : '<span class="muted">not yet</span>'}</td><td>${fmtInterval(m.interval_s)}</td>
        <td class="${up == null ? '' : up === 100 ? 'ok-text' : up < 90 ? 'err-text' : ''}">${up == null ? '–' : up + '%'}</td>
        <td>${monSpark((m.recent || []).slice(-14))}</td>
        <td class="page-row-acts"><button class="link small" data-act="run">Run now</button></td></tr>`; }).join('')}</table>
    </div>`;
  $('[data-act=new]', view).addEventListener('click', () => monitorDialog());
  const setAll = async (enabled) => {
    try { await Promise.all(mon.list.filter((m) => m.enabled !== enabled).map((m) => api('/monitors/' + m.id, { method: 'PATCH', body: { enabled } }))); }
    catch (err) { toast(err.message, 'error'); }
    loadMonitors(true);
  };
  $('[data-act=pause-all]', view).addEventListener('click', () => setAll(false));
  $('[data-act=resume-all]', view).addEventListener('click', () => setAll(true));
  $('[data-act=run-all]', view).addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    e.currentTarget.innerHTML = '<span class="spin"></span>Running…';
    let failed = 0;
    for (const m of mon.list.filter((x) => x.enabled)) {
      try { if (!(await api(`/monitors/${m.id}/run-now`, { method: 'POST' })).ok) failed += 1; } catch (err) { failed += 1; toast(err.message, 'error'); }
    }
    toast(failed ? `${failed} monitor${failed === 1 ? '' : 's'} failing` : 'Every monitor passed', failed ? 'error' : 'ok');
    pages.checksFor = null;
    loadMonitors(true);
  });
  $$('.page-row', view).forEach((tr) => {
    const m = monitorById(tr.dataset.id);
    tr.addEventListener('click', (e) => { if (!e.target.closest('button')) { pages.monitor = m.id; renderMonitors(); } });
    $('[data-act=run]', tr).addEventListener('click', (e) => runMonitorNow(m, e.currentTarget));
  });
}

// ===================================================================== mocks
function showMocks() {
  closeModal();
  if (currentSection() !== 'mocks') showSection('mocks');
  else renderMocks();
}

function showMock(id, tab) {
  pages.mock = id;
  if (tab) pages.mockTab = tab;
  showMocks();
}

function renderMocks() {
  renderMockList();
  if (currentSection() === 'mocks') { renderMockPage(); savePlace(); }
}

function renderMockList() {
  const ul = $('#mock-list');
  if (!ul) return;
  if (pages.mock && mocks.loaded && !mockById(pages.mock)) pages.mock = null;
  const q = pages.mockSearch.trim().toLowerCase();
  const shown = mocks.list.filter((m) => !q || m.name.toLowerCase().includes(q) || (m.slug || '').includes(q));
  if (!shown.length) {
    ul.innerHTML = `<li class="page-none muted small">${mocks.list.length ? 'No mock server matches.' : 'No mock servers yet. Press “+ New”.'}</li>`;
    return;
  }
  ul.innerHTML = shown.map((m) => {
    const routes = (m.routes || []).length;
    return `<li class="page-item mock-item ${m.enabled ? '' : 'off'} ${pages.mock === m.id ? 'active' : ''}" data-id="${m.id}" tabindex="0">
      <span class="page-dot s-${m.enabled ? 'pass' : 'off'}" title="${m.enabled ? 'answering' : 'off'}"></span>
      <span class="page-item-text"><span class="page-item-name">${esc(m.name)}</span>
        <span class="page-item-meta muted mon-meta">/mock/${esc(m.slug)} · ${routes} route${routes === 1 ? '' : 's'} · ${m.hits} call${m.hits === 1 ? '' : 's'}</span></span>
      <label class="switch tiny" title="${m.enabled ? 'Stop answering requests' : 'Start answering requests'}"><input type="checkbox" data-act="toggle" ${m.enabled ? 'checked' : ''}><span></span></label>
    </li>`;
  }).join('');
  $$('.mock-item', ul).forEach((li) => {
    const m = mockById(li.dataset.id);
    const open = () => { pages.mock = m.id; renderMocks(); };
    li.addEventListener('click', (e) => { if (!e.target.closest('.switch')) open(); });
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target === li) open(); });
    $('[data-act=toggle]', li).addEventListener('change', (e) => toggleMock(m, e.target.checked));
  });
}

async function toggleMock(m, on) {
  try { await api('/mocks/' + m.id, { method: 'PATCH', body: { enabled: on } }); } catch (err) { toast(err.message, 'error'); }
  loadMocks(true);
}

function renderMockPage() {
  const view = $('#mock-view');
  if (!view) return;
  clearInterval(pages.callTimer);
  pages.callTimer = null;
  const m = pages.mock && mockById(pages.mock);
  if (!m) { renderMockOverview(view); return; }
  const routes = m.routes || [];
  const off = routes.filter((r) => !r.enabled).length;
  view.innerHTML = `
    <div class="page-head">
      <div class="page-title"><button class="link page-back" data-act="back" title="All mock servers">Mocks</button><span class="muted">/</span>
        <h2>${esc(m.name)}</h2>${m.enabled ? '<span class="badge ok">live</span>' : '<span class="badge">off</span>'}</div>
      <div class="page-actions">
        <label class="switch-label" title="${m.enabled ? 'Stop answering requests' : 'Start answering requests'}"><span class="switch"><input type="checkbox" data-act="toggle" ${m.enabled ? 'checked' : ''}><span></span></span>${m.enabled ? 'Answering' : 'Off'}</label>
        <button data-act="edit">Edit</button>
        <button class="danger" data-act="del">Delete</button>
      </div>
    </div>
    <div class="page-url"><span class="muted small">Base URL</span>
      <button class="mock-url mono" data-act="copy" title="Copy this base URL">${esc(m.url)}<span class="mock-copy">Copy</span></button>
      <span class="muted small">or <code class="mono">{{mock_url}}/${esc(m.slug)}</code> in a request, so it follows the port</span></div>
    <div class="page-tiles">
      ${pageTile('Routes', routes.length + (off ? ` <span class="muted small">(${off} off)</span>` : ''))}
      ${pageTile('Calls answered', m.hits)}
      ${pageTile('Delay on every answer', m.delay_ms ? fmtMs(m.delay_ms) : '<span class="muted">none</span>')}
      ${pageTile('CORS', m.cors ? 'on' : '<span class="muted">off</span>')}
      ${pageTile('Call log', m.log_calls ? 'on' : '<span class="muted">off</span>')}
    </div>
    <div class="tabs page-tabs" id="mock-tabs">
      <button data-tab="routes" class="${pages.mockTab === 'routes' ? 'active' : ''}">Routes<span class="tab-n">${routes.length}</span></button>
      <button data-tab="calls" class="${pages.mockTab === 'calls' ? 'active' : ''}">Calls</button>
    </div>
    <div class="page-scroll" id="mock-panel"></div>`;
  $('[data-act=back]', view).addEventListener('click', () => { pages.mock = null; renderMocks(); });
  $('[data-act=toggle]', view).addEventListener('change', (e) => toggleMock(m, e.target.checked));
  $('[data-act=edit]', view).addEventListener('click', () => mockDialog(m));
  $('[data-act=copy]', view).addEventListener('click', async () => {
    const ok = await copyText(m.url);
    toast(ok ? `Copied ${m.url}` : 'Could not copy', ok ? 'ok' : 'error');
  });
  $('[data-act=del]', view).addEventListener('click', async () => {
    if (!await confirmDialog('Delete mock server?', `${strong(m.name)}, its routes and its call log will be removed. Requests pointing at it stay, but stop getting an answer.`, { okLabel: 'Delete', danger: true })) return;
    try { await api('/mocks/' + m.id, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
    pages.mock = null;
    loadMocks(true);
  });
  $$('#mock-tabs button').forEach((b) => b.addEventListener('click', () => { pages.mockTab = b.dataset.tab; renderMockPage(); }));
  if (pages.mockTab === 'calls') renderMockCallsTab(m);
  else renderMockRoutes(m);
}

function renderMockRoutes(m) {
  const panel = $('#mock-panel');
  const routes = m.routes || [];
  const q = pages.routeSearch.trim().toLowerCase();
  const shown = routes.filter((r) => !q || `${r.method} ${r.path} ${r.name} ${r.status_code}`.toLowerCase().includes(q));
  const rows = shown.map((r) => {
    const i = routes.indexOf(r);
    return `<tr class="mock-route ${r.enabled ? '' : 'off'}" data-id="${r.id}">
    <td><label class="switch tiny" title="${r.enabled ? 'Turn this route off' : 'Turn this route on'}"><input type="checkbox" data-act="toggle" ${r.enabled ? 'checked' : ''}><span></span></label></td>
    <td><span class="method m-${esc(r.method)}">${esc(r.method)}</span></td>
    <td class="mock-path">${esc(r.path)}${r.match_query ? `<span class="muted">?${esc(r.match_query)}</span>` : ''}${r.name ? `<div class="muted small">${esc(r.name)}</div>` : ''}</td>
    <td class="${r.status_code >= 400 ? 'err-text' : 'ok-text'}">${r.status_code}</td>
    <td class="muted">${r.delay_ms ? fmtMs(r.delay_ms) : ''}</td>
    <td class="muted">${r.hits || 0}</td>
    <td class="mock-acts">
      <button class="link small" data-act="up" title="Move up: the first matching route wins" aria-label="Move up" ${i === 0 || q ? 'disabled' : ''}>${icon('arrow-up')}</button>
      <button class="link small" data-act="down" title="Move down" aria-label="Move down" ${i === routes.length - 1 || q ? 'disabled' : ''}>${icon('arrow-down')}</button>
      <button class="link small" data-act="edit">Edit</button>
      <button class="link small" data-act="try" title="Open a new request pointing at this route">Try</button>
      <button class="link small danger" data-act="del" title="Delete this route" aria-label="Delete this route">${icon('x')}</button>
    </td></tr>`;
  }).join('');
  panel.innerHTML = `
    <div class="page-toolbar">
      <input id="mock-route-q" type="search" placeholder="Filter routes…" value="${esc(pages.routeSearch)}" autocomplete="off">
      <span class="muted small grow">Exact paths beat <code>:id</code> ones, and a route with a query condition beats one without.</span>
      <button id="btn-mock-seed" title="Add a route per saved request, answering with the response it last gave">From requests…</button>
      <button id="btn-mock-route-new" class="primary">+ Route</button>
    </div>
    ${routes.length ? `<table class="htable mock-routes page-table"><tr><th></th><th>Method</th><th>Path</th><th>Status</th><th>Delay</th><th title="Calls this route has answered">Hits</th><th></th></tr>${rows}</table>${shown.length ? '' : '<p class="muted small">No route matches the filter.</p>'}`
      : '<div class="empty"><p>No routes yet.</p><p class="muted small">A route is a method, a path and the answer to give back. Press “+ Route”, or build one per request you already have.</p></div>'}`;
  $('#btn-mock-route-new').addEventListener('click', () => mockRouteDialog(m.id));
  $('#btn-mock-seed').addEventListener('click', () => mockSeedDialog(m.id));
  $('#mock-route-q').addEventListener('input', (e) => {
    pages.routeSearch = e.target.value;
    renderMockRoutes(m);
    const again = $('#mock-route-q');
    again.focus();
    again.setSelectionRange(again.value.length, again.value.length);
  });
  $$('.mock-route', panel).forEach((el) => {
    const r = routes.find((x) => x.id === el.dataset.id);
    const after = async () => { await loadMocks(true); };
    const patch = async (body) => {
      try { await api(`/mocks/${m.id}/routes/${r.id}`, { method: 'PATCH', body }); } catch (err) { toast(err.message, 'error'); }
      after();
    };
    const move = async (delta) => {
      const ids = routes.map((x) => x.id);
      const at = ids.indexOf(r.id);
      ids.splice(at + delta, 0, ...ids.splice(at, 1));
      try { await api(`/mocks/${m.id}/routes/reorder`, { method: 'POST', body: { route_ids: ids } }); } catch (err) { toast(err.message, 'error'); }
      after();
    };
    $('[data-act=toggle]', el).addEventListener('change', (e) => patch({ enabled: e.target.checked }));
    $('[data-act=up]', el).addEventListener('click', () => move(-1));
    $('[data-act=down]', el).addEventListener('click', () => move(1));
    $('[data-act=edit]', el).addEventListener('click', () => mockRouteDialog(m.id, r));
    $('[data-act=try]', el).addEventListener('click', () => mockTryRoute(m, r));
    $('[data-act=del]', el).addEventListener('click', async () => {
      if (!await confirmDialog('Delete route?', `${strong(`${r.method} ${r.path}`)} will no longer be answered.`, { okLabel: 'Delete', danger: true })) return;
      try { await api(`/mocks/${m.id}/routes/${r.id}`, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
      after();
    });
  });
}

// ------------------------------------------------------------ the call log
async function renderMockCallsTab(m) {
  const panel = $('#mock-panel');
  panel.innerHTML = `
    <div class="page-toolbar">
      <span class="muted small grow">Every request this mock answered, newest first; click one for the headers and body it arrived with.
        ${m.log_calls ? 'Refreshes by itself.' : '<b>Logging is off for this mock</b> (turn it on in Edit).'}</span>
      <label class="small"><input type="checkbox" id="mock-unmatched" ${pages.unmatchedOnly ? 'checked' : ''}> Unmatched only</label>
      <button id="btn-mock-calls-clear" class="danger">Clear</button>
    </div>
    <div id="mock-calls-wrap"><p class="muted small">Loading…</p></div>`;
  $('#mock-unmatched').addEventListener('change', (e) => { pages.unmatchedOnly = e.target.checked; renderMockCalls(m, pages.calls); });
  $('#btn-mock-calls-clear').addEventListener('click', async () => {
    if (!await confirmDialog('Clear the call log?', `Every logged call of ${strong(m.name)} is removed. Its routes stay.`, { okLabel: 'Clear', danger: true })) return;
    try { await api(`/mocks/${m.id}/calls`, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
    read();
  });
  const read = async () => {
    if (!$('#mock-calls-wrap') || pages.mock !== m.id) { clearInterval(pages.callTimer); return; }   // the page moved on
    try { pages.calls = await api(`/mocks/${m.id}/calls?limit=200`); renderMockCalls(m, pages.calls); } catch { /* keep what is on screen */ }
  };
  await read();
  clearInterval(pages.callTimer);
  pages.callTimer = setInterval(read, 2000);
}

function renderMockCalls(m, all) {
  const wrap = $('#mock-calls-wrap');
  if (!wrap) return;
  const calls = pages.unmatchedOnly ? all.filter((c) => !c.matched) : all;
  if (!calls.length) {
    wrap.innerHTML = all.length ? '<p class="muted small">Every logged call matched a route.</p>'
      : `<div class="empty"><p>No calls yet.</p><p class="muted small">Point something at <code class="mono">${esc(m.url)}</code> and it shows up here.</p></div>`;
    return;
  }
  wrap.innerHTML = `<table class="htable mock-calls page-table"><tr><th>When</th><th>Method</th><th>Path</th><th>Status</th><th>Took</th><th>Route</th></tr>
    ${calls.map((c) => `<tr class="mock-call ${c.matched ? '' : 'unmatched'}" data-id="${c.id}" title="Click for the request headers and body">
      <td>${new Date(c.ts * 1000).toLocaleTimeString()}</td>
      <td><span class="method m-${esc(c.method)}">${esc(c.method)}</span></td>
      <td class="mock-path">${esc(c.path)}${c.query ? `<span class="muted">?${esc(c.query)}</span>` : ''}</td>
      <td class="${c.status_code >= 400 ? 'err-text' : 'ok-text'}">${c.status_code}</td>
      <td>${fmtMs(c.duration_ms)}</td>
      <td>${c.matched ? esc(c.route_name) : '<span class="err-text">no route matched</span>'}</td></tr>`).join('')}</table>`;
  $$('.mock-call', wrap).forEach((el) => el.addEventListener('click', () => {
    const c = calls.find((x) => x.id === el.dataset.id);
    const headers = Object.entries(c.req_headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n');
    dialog({ title: `${c.method} ${c.path}${c.query ? `?${c.query}` : ''}`, message: `
      <p class="muted small">${new Date(c.ts * 1000).toLocaleString()} · answered ${c.status_code} in ${fmtMs(c.duration_ms)}
        · ${c.matched ? `route ${esc(c.route_name)}` : 'no route matched'}</p>
      <div class="muted small mock-label">Request headers</div>
      <pre class="mono mock-pre">${esc(headers) || '<span class="muted">none</span>'}</pre>
      <div class="muted small mock-label">Request body</div>
      <pre class="mono mock-pre">${esc(c.req_body) || '<span class="muted">empty</span>'}</pre>
    `, buttons: [{ label: 'Close', value: null, cancel: true, cls: 'primary', primary: true }] });
  }));
}

function renderMockOverview(view) {
  if (!mocks.list.length) {
    view.innerHTML = `<div class="empty page-empty">
      <svg class="empty-mark" viewBox="0 0 24 24" width="40" height="40" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01"/></g></svg>
      <p>A mock server answers a list of routes with the responses you write.</p>
      <p class="muted small">It lives under <code class="mono">${esc(mocks.baseUrl || '/mock')}/&lt;name&gt;/</code> on the port pyhttp already runs on: nothing else to start.<br>
        A front end or a test can call an endpoint that does not exist yet.</p>
      <p><button class="primary" data-act="new">+ New mock server</button></p></div>`;
    $('[data-act=new]', view).addEventListener('click', () => mockDialog());
    return;
  }
  const live = mocks.list.filter((m) => m.enabled).length;
  view.innerHTML = `
    <div class="page-head">
      <div class="page-title"><h2>Mock servers</h2></div>
      <div class="page-actions"><button class="primary" data-act="new">+ New mock server</button></div>
    </div>
    <div class="page-tiles">
      ${pageTile('Mock servers', mocks.list.length)}
      ${pageTile('Answering', live, live ? 'good' : '')}
      ${pageTile('Routes', mocks.list.reduce((n, m) => n + (m.routes || []).length, 0))}
      ${pageTile('Calls answered', mocks.list.reduce((n, m) => n + (m.hits || 0), 0))}
    </div>
    <div class="page-scroll">
      <table class="htable page-table"><tr><th>Mock server</th><th>Status</th><th>Base URL</th><th>Routes</th><th>Calls</th></tr>
      ${mocks.list.map((m) => `<tr class="page-row" data-id="${m.id}" title="Open this mock server">
        <td class="page-row-name">${esc(m.name)}${m.description ? `<div class="muted small">${esc(m.description)}</div>` : ''}</td>
        <td>${m.enabled ? '<span class="badge ok">live</span>' : '<span class="badge">off</span>'}</td>
        <td class="mono">${esc(m.url)}</td><td>${(m.routes || []).length}</td><td>${m.hits}</td></tr>`).join('')}</table>
    </div>`;
  $('[data-act=new]', view).addEventListener('click', () => mockDialog());
  $$('.page-row', view).forEach((tr) => tr.addEventListener('click', () => { pages.mock = tr.dataset.id; renderMocks(); }));
}

function initPages() {
  const tabs = $('#side-tabs');
  if (!tabs) return;
  $$('button[data-tab]', tabs).forEach((b) => b.addEventListener('click', () => onSection(b.dataset.tab)));
  $('#btn-mon-new').addEventListener('click', () => monitorDialog());
  $('#btn-mock-new').addEventListener('click', () => mockDialog());
  $('#mon-search').addEventListener('input', (e) => { pages.monSearch = e.target.value; renderMonitorList(); });
  $('#mock-search').addEventListener('input', (e) => { pages.mockSearch = e.target.value; renderMockList(); });
  restorePlace();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initPages);
else initPages();
