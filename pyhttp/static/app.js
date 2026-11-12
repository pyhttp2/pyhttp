'use strict';

// ------------------------------------------------------------------ helpers
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtMs = (ms) => ms == null ? '' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
const fmtBytes = (n) => n == null ? '' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(2)} MB`;
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString();

async function api(path, { method = 'GET', body } = {}) {
  const init = { method };
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  const res = await fetch('/api' + path, init);
  const ct = res.headers.get('content-type') || '';
  const data = ct.includes('application/json') ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : res.statusText);
  }
  // an edit schedules a Git commit on the server: show that on the Git button now, not at the next poll
  if (method !== 'GET' && typeof gitNudge === 'function') gitNudge(path);
  return data;
}

let toastTimer = null;
// the URL row's method select wears the color of the method it shows
function tintMethodSelect() {
  const sel = $('#f-method');
  sel.className = 'method-select m-' + sel.value;
}

function toast(msg, kind = '', action = null) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast ' + kind;
  if (action) {
    const b = document.createElement('button');
    b.className = 'toast-act';
    b.textContent = action.label;
    b.addEventListener('click', () => { clearTimeout(toastTimer); el.hidden = true; action.onClick(); });
    el.appendChild(b);
  }
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, action ? 6500 : kind === 'error' ? 6000 : 2800);
}

// Tab strips follow the ARIA tabs pattern: role=tablist/tab/tabpanel, aria-selected, one tab stop
// per strip (the selected tab) and the arrow keys, Home and End to move between tabs.
let tabStripSeq = 0;
function syncTabs(tabsEl, panelsEl) {
  const tabs = $$('button[data-tab]', tabsEl);
  const strip = tabsEl.dataset.strip || (tabsEl.dataset.strip = tabsEl.id || 'tabs' + (++tabStripSeq));
  tabsEl.setAttribute('role', 'tablist');
  tabs.forEach((b) => {
    const on = b.classList.contains('active');
    if (!b.id) b.id = `${strip}-tab-${b.dataset.tab}`;
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', on ? 'true' : 'false');
    b.tabIndex = on ? 0 : -1;
    const panel = panelsEl && $$('.tab-panel', panelsEl).find((p) => p.dataset.panel === b.dataset.tab);
    if (!panel) return;
    if (!panel.id) panel.id = `${strip}-panel-${b.dataset.tab}`;
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', b.id);
    b.setAttribute('aria-controls', panel.id);
  });
  if (tabs.length && !tabs.some((b) => b.tabIndex === 0)) tabs[0].tabIndex = 0;
}
function bindTabs(tabsEl, panelsEl) {
  $$('button[data-tab]', tabsEl).forEach((btn) => {
    btn.addEventListener('click', () => {
      $$('button[data-tab]', tabsEl).forEach((b) => b.classList.toggle('active', b === btn));
      $$('.tab-panel', panelsEl).forEach((p) => { p.hidden = p.dataset.panel !== btn.dataset.tab; });
      syncTabs(tabsEl, panelsEl);
    });
  });
  tabsEl.addEventListener('keydown', (e) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key) || e.ctrlKey || e.metaKey || e.altKey) return;
    const tabs = $$('button[data-tab]', tabsEl).filter((b) => !b.hidden && !b.disabled && b.offsetParent !== null);
    const i = tabs.indexOf(document.activeElement);
    if (i === -1) return;
    e.preventDefault();
    e.stopPropagation();
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].click();
    tabs[next].focus();
  });
  syncTabs(tabsEl, panelsEl);
}
function showTab(tabsEl, name) {
  const btn = $(`button[data-tab="${name}"]`, tabsEl);
  if (btn) btn.click();
}

function prettyBody(resp) {
  if (!resp) return '';
  const text = resp.body_text || '';
  if (resp.is_json) {
    try { return JSON.stringify(JSON.parse(text), null, 2); } catch { /* fall through */ }
  }
  return text + (resp.truncated ? '\n\n… [truncated in viewer; full body saved to file]' : '');
}
function formatRequest(req) {
  if (!req) return '';
  const lines = [`${req.method} ${req.url}`];
  (req.headers || []).forEach(([k, v]) => lines.push(`${k}: ${v}`));
  if (req.body) lines.push('', req.body);
  return lines.join('\n');
}

// -------------------------------------------------------------------- modal
// The modal is a dialog for assistive tech too: focus moves into it, Tab stays inside it, and
// closing it gives the focus back to whatever opened it.
const FOCUSABLE = 'a[href], button, input, select, textarea, [contenteditable="true"], [tabindex]:not([tabindex="-1"])';
function focusableIn(root) {
  return $$(FOCUSABLE, root).filter((el) => !el.disabled && !el.hidden && el.offsetParent !== null);
}
function trapFocus(root, e) {
  if (e.key !== 'Tab' || e.defaultPrevented) return;   // an editor inside may use Tab to indent
  const f = focusableIn(root);
  if (!f.length) { e.preventDefault(); return; }
  const i = f.indexOf(document.activeElement);
  if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
  else if (!e.shiftKey && (i === -1 || i === f.length - 1)) { e.preventDefault(); f[0].focus(); }
}
let modalPrevFocus = null;
function openModal(title, bodyHtml, footButtons = []) {
  const back = $('#modal');
  if (back.hidden) modalPrevFocus = document.activeElement;   // one modal replacing another keeps the first opener
  $('#modal .modal').className = 'modal';   // a dialog that wants another size adds its class after opening
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = bodyHtml;
  const foot = $('#modal-foot');
  foot.innerHTML = '';
  footButtons.forEach(({ label, cls = '', onClick }) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.className = cls;
    b.addEventListener('click', onClick);
    foot.appendChild(b);
  });
  back.hidden = false;
  if (!back.dataset.trap) {
    back.dataset.trap = '1';
    back.addEventListener('keydown', (e) => trapFocus($('#modal .modal'), e));
  }
  // focus moves into the dialog at once; callers that want a field of their own focus it right after
  if (!back.contains(document.activeElement)) $('#modal .modal').focus();
}
function closeModal() {
  if (typeof envSave !== 'undefined' && envSave.timer) flushEnvSave();
  const wasOpen = !$('#modal').hidden;
  $('#modal').hidden = true;
  $('#modal-body').innerHTML = '';   // no stale ids or listeners left behind
  $('#modal-foot').innerHTML = '';
  const prev = modalPrevFocus;
  modalPrevFocus = null;
  if (wasOpen && prev && prev.focus && document.contains(prev)) prev.focus();
}

// --------------------------------------------------------------- kv editor
function kvRow(r, withEnable, lists = null, secrets = false) {
  const row = document.createElement('div');
  row.className = 'kv-row' + (secrets ? ' with-secret' : '') + (r.secret ? ' secret' : '');
  row.innerHTML =
    (withEnable ? `<input type="checkbox" class="kv-on" ${r.enabled !== false ? 'checked' : ''}>` : '') +
    `<input class="kv-key mono" placeholder="Key" value="${esc(r.key)}" spellcheck="false" autocomplete="off" ${lists ? `list="${lists.key}"` : ''}>` +
    `<input class="kv-val mono" placeholder="Value" value="${esc(r.value)}" spellcheck="false" autocomplete="off" ${lists ? `list="${lists.value}"` : ''} ${r.secret ? 'type="password"' : ''}>` +
    (secrets ? `<button class="kv-secret" title="${r.secret ? 'Secret: masked here and in saved history and run files. Click to make it a normal value' : 'Mark as secret (masked here and in saved history and run files)'}">${r.secret ? '🔒' : '🔓'}</button>` : '') +
    `<button class="kv-del" title="Remove" aria-label="Remove row">✕</button>`;
  $('.kv-del', row).addEventListener('click', () => row.remove());
  if (secrets) {
    const val = $('.kv-val', row);
    $('.kv-secret', row).addEventListener('click', (e) => {
      const on = !row.classList.contains('secret');
      row.classList.toggle('secret', on);
      val.type = on ? 'password' : 'text';
      e.currentTarget.textContent = on ? '🔒' : '🔓';
      e.currentTarget.title = on ? 'Secret: masked here and in saved history and run files. Click to make it a normal value' : 'Mark as secret (masked here and in saved history and run files)';
    });
    // a secret shows while it is being edited
    val.addEventListener('focus', () => { if (row.classList.contains('secret')) val.type = 'text'; });
    val.addEventListener('blur', () => { if (row.classList.contains('secret')) val.type = 'password'; });
  }
  return row;
}
// Rows <-> text for bulk edit. One "key: value" per line; "//" disables a line.
function kvToText(rows, withEnable) {
  return (rows || []).map((r) => (withEnable && r.enabled === false ? '// ' : '') + `${r.key}: ${r.value}`).join('\n');
}
function parseBulk(text, withEnable) {
  const rows = [];
  const push = (part, enabled) => {
    const ci = part.indexOf(':');
    const ei = part.indexOf('=');
    const i = ei !== -1 && (ci === -1 || ei < ci) ? ei : ci;
    const key = (i === -1 ? part : part.slice(0, i)).trim();
    const value = i === -1 ? '' : part.slice(i + 1).trim();
    if (key || value) rows.push({ key, value, enabled: withEnable ? enabled : true });
  };
  text.split(/\r?\n/).forEach((line) => {
    let l = line.trim();
    if (!l) return;
    let enabled = true;
    if (l.startsWith('//')) { enabled = false; l = l.slice(2).trim(); }
    if (l.startsWith('?')) l = l.slice(1);
    // a pasted query string such as a=1&b=2 becomes several rows
    if (!l.includes(':') && l.includes('=') && l.includes('&')) l.split('&').forEach((part) => push(part, enabled));
    else push(l, enabled);
  });
  return rows;
}
function renderKv(container, rows, withEnable = true) {
  container.innerHTML = '';
  container.classList.toggle('no-enable', !withEnable);
  container.dataset.withEnable = withEnable ? '1' : '0';
  const bulk = container.dataset.mode === 'bulk';
  const bar = document.createElement('div');
  bar.className = 'kv-bar';
  const toggle = document.createElement('button');
  toggle.className = 'link small kv-mode';
  toggle.textContent = bulk ? 'Key-value edit' : 'Bulk edit';
  toggle.title = bulk ? 'Back to rows' : 'Edit all rows as text, one "key: value" per line';
  toggle.addEventListener('click', () => {
    const current = readKv(container);
    container.dataset.mode = bulk ? 'rows' : 'bulk';
    renderKv(container, current, withEnable);
    const ta = $('.kv-bulk', container);
    if (ta) ta.focus();
  });
  const secrets = container.dataset.secrets === '1';
  if (secrets) container._secretKeys = new Set((rows || []).filter((r) => r.secret).map((r) => r.key));
  if (bulk) {
    const ta = document.createElement('textarea');
    ta.className = 'kv-bulk mono';
    ta.spellcheck = false;
    ta.rows = Math.min(16, Math.max(5, (rows || []).length + 2));
    ta.placeholder = withEnable ? 'Content-Type: application/json\n// X-Disabled: lines starting with // are off' : 'base_url: https://api.example.com';
    ta.value = kvToText(rows, withEnable);
    container.appendChild(ta);
    const hint = document.createElement('span');
    hint.className = 'muted small';
    hint.textContent = withEnable ? 'One key: value per line. Start a line with // to disable it.' : 'One key: value per line.';
    bar.appendChild(hint);
  } else {
    const lists = container.id === 'kv-headers' || container.id === 'fs-headers' ? { key: 'header-names', value: 'header-values' } : null;
    const list = document.createElement('div');
    list.className = 'kv-rows';
    (rows || []).forEach((r) => list.appendChild(kvRow(r, withEnable, lists, secrets)));
    // select all / unselect all: one checkbox above the enable column
    let syncAll = () => {};
    if (withEnable) {
      const head = document.createElement('label');
      head.className = 'kv-all muted small';
      head.innerHTML = '<input type="checkbox" class="kv-all-on"> <span></span>';
      const all = $('.kv-all-on', head);
      syncAll = () => {
        const boxes = $$('.kv-on', list);
        const on = boxes.filter((b) => b.checked).length;
        all.checked = boxes.length > 0 && on === boxes.length;
        all.indeterminate = on > 0 && on < boxes.length;
        all.disabled = !boxes.length;
        $('span', head).textContent = all.checked ? 'Unselect all' : 'Select all';
        head.title = all.checked ? 'Turn every row off' : 'Turn every row on';
      };
      all.addEventListener('change', (e) => {
        e.stopPropagation();
        const on = all.checked;
        $$('.kv-on', list).forEach((b) => { b.checked = on; });
        syncAll();
        // let the editor save and refresh its counts as if a row was toggled
        container.dispatchEvent(new Event('input', { bubbles: true }));
        container.dispatchEvent(new Event('change', { bubbles: true }));
      });
      list.addEventListener('change', (e) => { if (e.target.classList.contains('kv-on')) syncAll(); });
      list.addEventListener('click', (e) => { if (e.target.closest('.kv-del')) setTimeout(syncAll, 0); });
      container.appendChild(head);
    }
    container.appendChild(list);
    syncAll();
    const add = document.createElement('button');
    add.className = 'link';
    add.textContent = '+ Add row';
    add.addEventListener('click', () => {
      const row = kvRow({ key: '', value: '', enabled: true }, withEnable, lists, secrets);
      list.appendChild(row);
      syncAll();
      $('.kv-key', row).focus();
    });
    bar.appendChild(add);
  }
  const spacer = document.createElement('span');
  spacer.className = 'grow';
  bar.appendChild(spacer);
  bar.appendChild(toggle);
  container.appendChild(bar);
}
function readKv(container) {
  const ta = $('.kv-bulk', container);
  const secrets = container.dataset.secrets === '1';
  if (ta) {
    // bulk text has no secret flag: keys that were secret stay secret
    const keys = container._secretKeys || new Set();
    return parseBulk(ta.value, container.dataset.withEnable !== '0').map((r) => (secrets && keys.has(r.key) ? { ...r, secret: true } : r));
  }
  return $$('.kv-row', container).map((row) => ({
    enabled: $('.kv-on', row) ? $('.kv-on', row).checked : true,
    key: $('.kv-key', row).value,
    value: $('.kv-val', row).value,
    ...(secrets && row.classList.contains('secret') ? { secret: true } : {}),
  })).filter((r) => r.key || r.value);
}

// -------------------------------------------------------------------- state
const state = {
  requests: [],
  tags: [],
  q: '',
  tag: '',
  selected: [],      // [{id, name, method}] in selection order
  current: null,     // request being edited (null id => unsaved)
  activeRunId: null,
  sending: null,     // {kind: "single"|"run", key, id|runId} while a Send is in flight
  pollTimer: null,
  bodies: [],        // named bodies of the request in the editor
  activeBodyId: null,
  bodyQ: '',
  responses: {},     // responses per request, one per body
  folders: [],
  expanded: new Set(), // open folder ids (remembered)
  treeOrder: [],     // filtered requests in sidebar order
  drag: null,
  envs: [],
  activeEnvId: null,
};
const isSelected = (id) => state.selected.some((s) => s.id === id);

// --------------------------------------------------------------- collection
let loadRequestsSeq = 0;
async function loadRequests() {
  const seq = ++loadRequestsSeq;
  const qs = new URLSearchParams({ q: state.q, tag: state.tag, fields: 'list' });   // sidebar rows only, never body text
  const [requests, tags, folders] = await Promise.all([
    api('/requests?' + qs.toString()), api('/tags'), api('/folders'),
  ]);
  // Calls overlap (typing in the search box, autosave): only the newest may fill the list, or a slow
  // answer to an older, filtered query would land last and leave a few rows under an empty search box.
  if (seq !== loadRequestsSeq) return;
  state.requests = requests.filter((r) => !pendingDeletes.has(r.id));
  state.tags = tags;
  state.folders = folders;
  renderTags();
  renderList();
}

function renderTags() {
  const box = $('#tag-filter');
  box.innerHTML = '';
  state.tags.forEach(({ tag, count }) => {
    const chip = document.createElement('span');
    chip.className = 'chip' + (state.tag === tag ? ' active' : '');
    chip.innerHTML = `${esc(tag)}<span class="n">${count}</span>`;
    chip.addEventListener('click', () => {
      state.tag = state.tag === tag ? '' : tag;
      loadRequests();
    });
    box.appendChild(chip);
  });
}

// ------------------------------------------------- keyboard list navigation
// ArrowUp/Down move a focus ring over the visible requests; Enter opens, Space
// ticks for a run, Delete deletes (undoable). Esc clears the ring.
function kbMove(step) {
  const rows = $$('#req-list .req-item');
  if (!rows.length) return;
  let i = rows.findIndex((row) => row.dataset.rid === state.kbId);
  if (i === -1) i = rows.findIndex((row) => row.classList.contains('active'));
  i = Math.min(rows.length - 1, Math.max(0, i + step));
  state.kbId = rows[i].dataset.rid;
  rows.forEach((row) => row.classList.toggle('kb-focus', row.dataset.rid === state.kbId));
  rows[i].scrollIntoView({ block: 'nearest' });
}

function toggleSelect(r) {
  if (isSelected(r.id)) state.selected = state.selected.filter((s) => s.id !== r.id);
  else state.selected.push(selEntry(r));
  renderList();
}
function updateSelectionBar() {
  const n = state.selected.length;
  $('#selected-count').textContent = `${n} selected`;
  $('#btn-run').disabled = n === 0;
  $('#btn-run').textContent = n ? `▶ Run selected (${n})` : '▶ Run selected';
  $('#btn-run-now').disabled = n === 0;
  $('#btn-clear-sel').hidden = n === 0;
  const shown = state.requests;
  $('#select-all').checked = shown.length > 0 && shown.every((r) => isSelected(r.id));
}

// ------------------------------------------------------------------- editor
const blankRequest = () => ({
  id: null, name: '', description: '', tags: [], method: 'GET', url: '',
  params: [], headers: [], body_type: 'none', bodies: [], timeout: 30, verify_ssl: true,
});
// How many times a request is sent in a run: once per checked body (at least once).
function sendCount(r) {
  if (!r || r.body_type === 'none') return 1;
  return Math.max(1, (r.bodies || []).filter((b) => b.enabled).length);
}
const selEntry = (r) => ({ id: r.id, name: r.name, method: r.method, n: sendCount(r) });

async function openRequest(id) {
  if (id === 'draft') return;
  if (state.current && state.current.id === id) return;
  await saveNow();
  state.current = await api('/requests/' + id);
  tabsAdd(state.current);
  renderEditor();
  renderList();
}
async function newRequest(folderId = null) {
  // saveNow() reads the editor synchronously, so the blank form can show at once
  const pending = autosave.inFlight ? saveNow() : (saveNow(), null);
  if (pending) await pending;
  state.current = { ...blankRequest(), folder_id: folderId || null };
  if (folderId) expandTo(folderId);
  tabsAdd(state.current);
  renderEditor();
  renderList();
  $('#f-url').focus();
}
function renderEditor() {
  const r = state.current;
  $('#editor-empty').hidden = !!r;
  $('#editor-form').hidden = !r;
  renderReqTabs();
  if (!r) { $('#response').hidden = true; return; }
  $('#f-name').value = r.name;
  $('#f-tags').value = r.tags.join(', ');
  $('#f-description').value = r.description;
  resetDescriptionPreview();
  $('#f-method').value = r.method;
  tintMethodSelect();
  $('#f-url').value = r.url;
  renderKv($('#kv-params'), r.params);
  renderKv($('#kv-headers'), r.headers);
  $('#f-body-type').value = r.body_type;
  state.bodies = (r.bodies || []).map((b) => ({ ...b }));
  const remembered = r.options && r.options.active_body;
  const fallback = state.bodies.find((b) => b.enabled) || state.bodies[0] || null;
  state.activeBodyId = state.bodies.some((b) => b.id === remembered) ? remembered : (fallback ? fallback.id : null);
  state.bodyQ = '';
  $('#body-search').value = '';
  renderBodies();
  $('#f-timeout').value = r.timeout;
  inheritedInfo = null;
  renderAuthPanel(r);
  if (typeof renderExamplesPanel === 'function') renderExamplesPanel();
  renderTestsPanel(r);
  renderRequestSettings(r);
  updateViewTab();
  renderPathVars();
  renderInheritedHeaders();
  updateTabCounts();
  refreshInherited();
  setSaveState(r.id ? 'saved' : '', r.id ? 'Saved' : 'Draft (saves when you edit)');
  renderMetaTags();
  renderCrumb();
  renderResponseArea();
}
function readEditor() {
  flushBody();
  return {
    name: $('#f-name').value.trim() || 'Untitled',
    description: $('#f-description').value,
    tags: $('#f-tags').value.split(',').map((t) => t.trim()).filter(Boolean),
    method: $('#f-method').value,
    url: $('#f-url').value.trim(),
    params: readKv($('#kv-params')),
    headers: readKv($('#kv-headers')),
    body_type: $('#f-body-type').value,
    bodies: state.bodies.map((b) => ({ ...b })),
    timeout: Number($('#f-timeout').value) || 30,
    auth: readAuth(),
    ...readTests(),
    options: readRequestOptions(),
  };
}

// ------------------------------------------------------------ named bodies
const activeBody = () => state.bodies.find((b) => b.id === state.activeBodyId) || null;
const newBodyId = () => 'b' + Math.random().toString(36).slice(2, 10);

function flushBody() {
  const b = activeBody();
  if (!b) return;
  b.name = $('#body-name').value;
  b.body = readBodyEditor();
}
function bodyMatches(b, q) {
  if (!q) return true;
  const hay = `${b.name}\n${b.body}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every((t) => hay.includes(t));
}
const SEND_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M2.5 3.3 22 12 2.5 20.7l2.9-7.2L15 12 5.4 10.5z"/></svg>';
function renderBodies() {
  const ul = $('#body-list');
  const shown = state.bodies.filter((b) => bodyMatches(b, state.bodyQ));
  const keepScroll = ul.scrollTop;   // rebuilding the list must not jump it back to the top
  ul.innerHTML = '';
  if (!state.bodies.length) ul.innerHTML = '<li class="none">No bodies yet.</li>';
  else if (!shown.length) ul.innerHTML = '<li class="none">No bodies match.</li>';
  shown.forEach((b) => {
    const li = document.createElement('li');
    li.className = 'body-item' + (b.id === state.activeBodyId ? ' active' : '') + (b.enabled ? '' : ' off');
    li.dataset.id = b.id;
    const preview = (b.body || '').replace(/\s+/g, ' ').slice(0, 80) || 'empty';
    li.innerHTML =
      `<input type="checkbox" ${b.enabled ? 'checked' : ''} title="Include this body when pressing Send">` +
      `<div style="min-width:0"><div class="bname">${esc(b.name || 'Unnamed')}</div><div class="bprev">${esc(preview)}</div></div>` +
      `<button type="button" class="icon bsend" title="Send only “${esc(b.name || 'Unnamed')}” now (the checkboxes stay as they are)" aria-label="Send only ${esc(b.name || 'Unnamed')}">${SEND_ICON}</button>` +
      `<button type="button" class="icon bmore" title="Body actions" aria-label="Actions for ${esc(b.name || 'Unnamed')}" aria-haspopup="menu">⋯</button>`;
    $('input', li).addEventListener('click', (e) => {
      e.stopPropagation();
      b.enabled = e.target.checked;
      renderBodies();
      scheduleSave();   // checked bodies are remembered with the request, across tabs and restarts
    });
    $('.bsend', li).addEventListener('click', (e) => {
      e.stopPropagation();
      sendOneBody(b.id);
    });
    $('.bmore', li).addEventListener('click', (e) => { e.stopPropagation(); openBodyMenu(b, e.currentTarget); });
    li.addEventListener('contextmenu', (e) => { e.preventDefault(); openBodyMenu(b, li, { x: e.clientX, y: e.clientY }); });
    li.addEventListener('click', () => selectBody(b.id));
    ul.appendChild(li);
  });
  ul.scrollTop = keepScroll;
  ul.classList.toggle('one', state.bodies.length < 2);
  const b = activeBody();
  $('#body-editor').hidden = !b;
  $('#body-editor-empty').hidden = !!b;
  if (b) {
    if ($('#body-name').value !== b.name) $('#body-name').value = b.name;
    showBodyEditor(b);
  }
  updateBodyHints();
  updateTabCounts();
  if (typeof refreshTestBodyScopes === 'function') refreshTestBodyScopes();   // tests can be scoped to a body
}
function updateBodyHints() {
  const total = state.bodies.length;
  const on = state.bodies.filter((b) => b.enabled).length;
  $('#bodies-count').textContent = total ? `${on} of ${total} checked` : '';
  const type = $('#f-body-type').value;
  $('#body-list').classList.toggle('no-send', type === 'none');   // no per-body ➤ when bodies are not sent
  let hint = '';
  if (type === 'none' && total) hint = 'Body type is “No body”, so bodies are not sent.';
  else if (type !== 'none' && on > 1) hint = `Send hits this request ${on}× (once per checked body) and saves each output.`;
  else if (type !== 'none' && on === 1) hint = 'Send uses the one checked body.';
  else if (type !== 'none' && total) hint = 'No body is checked, so the request is sent without a body.';
  $('#bodies-hint').textContent = hint;
  $('#bodies-hint').title = hint;
  renderSendButton();
}
// Send turns into Cancel while this request's call (or multi-body run) is in flight.
// What happened to the request this one depends on (token fetched, still fresh, refreshed and retried, failed)
function renderDependencyBadge(dep) {
  const el = $('#r-dep');
  if (!el) return;
  el.hidden = !dep;
  if (!dep) return;
  const name = dep.name || 'dependency';
  let text = '', title = '', cls = 'dep-badge';
  if (dep.state === 'shared') { text = `⛓ ${name}: shared`; title = `“${name}” was sent once for all the bodies sent together, so it was not sent again for this one.`; }
  else if (dep.state === 'fresh') { text = `⛓ ${name}: reused`; title = `“${name}” was sent ${fmtAge(dep.age)} ago and is still valid for ${fmtAge(Math.max(0, dep.ttl - dep.age))}, so it was not sent again.`; }
  else if (dep.state === 'ran' && dep.ok) { text = `⛓ ${name}: sent first${dep.retried ? ', then retried' : ''}`; title = `“${name}” was sent before this request (${dep.status_code ?? 'OK'}, ${fmtMs(dep.duration_ms)})${dep.retried ? ' because the previous response was 401/403; this request was then sent again' : ''}.`; }
  else if (dep.state === 'ran') { text = `⛓ ${name}: failed`; cls += ' fail'; title = `“${name}” failed (${dep.error || 'HTTP ' + dep.status_code}).`; }
  else if (dep.state === 'missing') { text = '⛓ dependency missing'; cls += ' fail'; title = 'The request this one depends on was deleted. Pick another in the Settings tab.'; }
  else if (dep.state === 'loop') { text = `⛓ ${name}: skipped`; title = 'Skipped: the dependencies form a loop.'; }
  el.textContent = text; el.title = title; el.className = cls;
}
function renderSendButton() {
  const btn = $('#btn-send');
  if (typeof isWsRequest === 'function' && isWsRequest()) {   // a ws:// address connects instead of sending
    const s = wsSession();
    const live = !!s && (s.state === 'open' || s.state === 'connecting');
    const label = live ? 'Disconnect' : 'Connect';
    const title = live ? 'Close this WebSocket connection' : 'Open a WebSocket connection to this address (Ctrl+Enter)';
    btn.classList.toggle('cancel', live);
    btn.disabled = false;
    if (btn.title !== title) btn.title = title;
    if (btn.textContent !== label) btn.textContent = label;
    $('#btn-send-mode').hidden = true;
    $('#body-list').classList.remove('sending');
    return;
  }
  const type = $('#f-body-type').value;
  const on = state.bodies.filter((b) => b.enabled).length;
  const n = type === 'none' ? 1 : on;
  const sending = state.sending && state.sending.key === responseKey();
  btn.classList.toggle('cancel', !!sending);
  btn.disabled = false;
  const par = !!appSettings.parallel;
  const title = sending ? 'Stop the call(s) in flight' : n > 1 ? `Send the ${n} checked bodies ${par ? 'in parallel (at most ' + (appSettings.concurrency || 8) + ' at once)' : 'one after another'}. Use ▾ to change. Ctrl+Enter` : 'Ctrl+Enter';
  const label = sending ? (state.sending.kind === 'run' ? `Cancel (${state.sending.done || 0}/${state.sending.total})` : 'Cancel') : (n > 1 ? `Send ×${n}${par ? ' ∥' : ''}` : 'Send');
  // Only touch the DOM when the text changes: this runs on every blur of a body field, i.e. between
  // the mousedown and mouseup of a click on Send, and WebKit drops a click whose target text node
  // was replaced in between (the macOS app window is WebKit, where Send then did nothing).
  if (btn.title !== title) btn.title = title;
  if (btn.textContent !== label) btn.textContent = label;
  $('#body-list').classList.toggle('sending', !!sending);
  const mode = $('#btn-send-mode');
  mode.hidden = sending || n < 2;
  mode.title = `Sending ${par ? 'in parallel' : 'in order'}. Click to change how the checked bodies are sent.`;
  renderSendingUi();
}

// While a call is in flight: a sliding bar under the URL, a live strip with the elapsed time on
// top of the response (the previous response stays usable, slightly dimmed), a shimmer placeholder
// on the first send, and a spinner on the request's tab and sidebar entry. Nothing is blocked.
const sendingUi = { timer: null };
function fmtElapsed(ms) {
  if (ms < 10000) return (ms / 1000).toFixed(1) + ' s';
  if (ms < 60000) return Math.floor(ms / 1000) + ' s';
  const sec = Math.floor(ms / 1000);
  return `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, '0')}s`;
}
function sendingLabel(s) {
  if (s.kind === 'run') return `Sending ${s.total} bodies${s.done ? ` · ${s.done} of ${s.total} done` : ''}`;
  let target = $('#f-url').value.trim();
  if (!target.includes('{{')) { try { const u = new URL(target); target = u.host + u.pathname; } catch { /* keep as typed */ } }
  return `Sending ${$('#f-method').value} ${target.length > 60 ? target.slice(0, 60) + '…' : target}`;
}
function tickSendingUi() {
  const s = state.sending;
  $$('.req-tab').forEach((el) => el.classList.toggle('sending', !!s && el.dataset.key === s.key));
  $$('.req-item').forEach((el) => el.classList.toggle('sending', !!s && el.dataset.rid === s.key));
  const live = $('#resp-live');
  if (!s || !live) return;
  $('.rl-time', live).textContent = fmtElapsed(performance.now() - s.started);
  $('.rl-text', live).textContent = sendingLabel(s);
}
function renderSendingUi() {
  const s = state.sending;
  if (s && !s.started) s.started = performance.now();
  const here = !!(s && s.key === responseKey());
  const resp = $('#response');
  $('.url-row').classList.toggle('sending', here);
  let live = $('#resp-live');
  if (here) {
    if (resp.hidden) { resp.hidden = false; resp.classList.add('first-send'); }
    resp.classList.add('is-sending');
    if (!live) {
      live = document.createElement('div');
      live.id = 'resp-live';
      live.className = 'resp-live';
      live.setAttribute('role', 'status');
      live.innerHTML = '<span class="rl-dot" aria-hidden="true"></span><span class="rl-text"></span><span class="grow"></span><span class="rl-time mono"></span>' +
        '<div class="rl-skeleton" aria-hidden="true"><i style="width:38%"></i><i style="width:72%"></i><i style="width:56%"></i><i style="width:64%"></i><i style="width:30%"></i></div>';
      const at = $('#resp-bodies');
      at.parentNode.insertBefore(live, at);
    }
  } else {
    resp.classList.remove('is-sending');
    if (live) live.remove();
    if (resp.classList.contains('first-send')) {
      resp.classList.remove('first-send');
      const rs = responsesFor();
      if (!rs || !Object.keys(rs.byKey).length) resp.hidden = true;
    }
  }
  tickSendingUi();
  if (s && !sendingUi.timer) sendingUi.timer = setInterval(tickSendingUi, 100);
  if (!s && sendingUi.timer) { clearInterval(sendingUi.timer); sendingUi.timer = null; tickSendingUi(); }
}
function openSendModeMenu(anchor) {
  const par = !!appSettings.parallel;
  const pick = async (parallel) => {
    await saveSettings({ parallel });
    renderSendButton();
    toast(parallel ? `Bodies are sent in parallel, at most ${appSettings.concurrency || 8} at once (Settings → Requests)` : 'Bodies are sent one after another', 'ok');
  };
  openMenu('send-mode', anchor, null, [
    { act: 'seq', icon: par ? '' : '✓', label: 'Send in order, one after another', run: () => pick(false) },
    { act: 'par', icon: par ? '✓' : '', label: `Send in parallel, at most ${appSettings.concurrency || 8} at once`, run: () => pick(true) },
  ]);
}
async function cancelSending() {
  const s = state.sending;
  if (!s) return;
  if (streamAbort) { streamAbort.abort(); toast('Stream stopped'); return; }
  try {
    if (s.kind === 'run') await api(`/runs/${s.runId}/cancel`, { method: 'POST' });
    else if (s.id) await api(`/send/${s.id}/cancel`, { method: 'POST' });
    toast('Cancelling…');
  } catch (e) { toast(e.message, 'error'); }
}

// --------------------------------------------------- streaming send (SSE, chunked)
// POST /api/send-stream and show the body as it arrives in a live pane; the final
// "done" event carries the same result object /api/send returns, rendered as usual.
// Stopping (the Send button turned Cancel) closes the connection and keeps what arrived.
let streamAbort = null;
async function sendStreaming(payload) {
  const started = performance.now();
  streamAbort = new AbortController();
  const box = $('#response');
  box.hidden = false;
  box.classList.add('streaming');
  box.classList.remove('first-send');
  let live = $('#r-stream-live');
  if (!live) {
    live = document.createElement('pre');
    live.id = 'r-stream-live';
    box.appendChild(live);
  }
  live.textContent = '';
  let meta = null;
  let received = '';
  let result = null;
  try {
    const res = await fetch('/api/send-stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), signal: streamAbort.signal,
    });
    if (!res.ok) throw new Error(res.statusText || 'HTTP ' + res.status);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.type === 'status') {
          meta = ev;
          const c = ev.status_code || 0;
          $('#r-status').className = 'status ' + (c && c < 400 ? 'ok' : c < 500 ? 'warn' : 'err');
          $('#r-status').textContent = `${c} ${ev.reason || ''}`.trim();
          $('#r-time').textContent = 'streaming…';
        } else if (ev.type === 'chunk') {
          received += ev.text;
          const stick = live.scrollTop + live.clientHeight >= live.scrollHeight - 12;
          live.append(ev.text);
          if (stick) live.scrollTop = live.scrollHeight;
        } else if (ev.type === 'done') {
          result = ev.result;
        }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') toast('Send failed: ' + e.message, 'error');
  } finally {
    streamAbort = null;
    box.classList.remove('streaming');
    const el = $('#r-stream-live');
    if (el) el.remove();
  }
  if (result || !meta) return result;   // finished normally, or nothing arrived at all
  // stopped mid-stream: keep what arrived as the response
  return {
    status: 'ok', status_code: meta.status_code, reason: meta.reason,
    duration_ms: performance.now() - started, error: null,
    request: { method: payload.method, url: payload.url, params: [], headers: [], body: null },
    response: {
      headers: meta.headers || [], content_type: meta.content_type || '', size: received.length,
      body_text: received, truncated: false, is_json: false, is_binary: false, body_base64: null,
      http_version: meta.http_version || '', redirects: [], hops: [], cookies: [],
    },
    tests: [], tests_failed: 0, extracted: [], pre_vars: [], scripts: {}, stopped: true,
  };
}
function sendOrCancel() {
  if (typeof isWsRequest === 'function' && isWsRequest()) { wsToggle(); return; }
  if (state.sending && state.sending.key === responseKey()) cancelSending(); else sendCurrent();
}
// The ➤ button next to a body: send that one body right away, whatever is checked.
function sendOneBody(id) {
  if (!state.bodies.some((b) => b.id === id)) return;
  if (state.sending && state.sending.key === responseKey()) { toast('A send is already in flight. Cancel it first.', 'error'); return; }
  if ($('#f-body-type').value === 'none') { toast('Body type is “No body”. Pick a body type to send a body.', 'error'); return; }
  selectBody(id);   // the editor and the response pane follow the body that is sent
  sendCurrent({ onlyBodyId: id });
}
function selectBody(id) {
  flushBody();
  if (state.activeBodyId !== id) scheduleSave();   // the selected body is remembered with the request
  state.activeBodyId = id;
  renderBodies();
  // show the response that belongs to this body, if there is one
  const rs = responsesFor();
  const key = rs && (rs.byKey[id] ? id : Object.keys(rs.byKey).find((k) => bodyOfKey(k) === id));
  if (key && bodyOfKey(rs.selected || '') !== id) { rs.selected = key; renderResponseArea(); }
  else if (rs && rs.selected && rs.byKey[rs.selected] && !rs.byKey[rs.selected].pending && !rs.byKey[rs.selected].notSent) renderResponse(rs.byKey[rs.selected], !$('#resp-bodies').hidden);
}
function addBody(from, at = null) {
  flushBody();
  const b = {
    id: newBodyId(),
    name: from ? `${from.name} (copy)` : `Body ${state.bodies.length + 1}`,
    body: from ? from.body : '',
    enabled: true,
  };
  if (at == null || at < 0 || at > state.bodies.length) state.bodies.push(b); else state.bodies.splice(at, 0, b);
  state.activeBodyId = b.id;
  if ($('#f-body-type').value === 'none') $('#f-body-type').value = 'json';
  if (!bodyMatches(b, state.bodyQ)) { state.bodyQ = ''; $('#body-search').value = ''; }
  renderBodies();
  scheduleSave();
  $('#body-name').focus();
  $('#body-name').select();
}
// The ⋯ of a body row: manage a body without making it the one in the editor.
function openBodyMenu(b, anchor, at) {
  flushBody();
  const i = state.bodies.indexOf(b);
  const moveTo = (j) => {
    state.bodies.splice(i, 1);
    state.bodies.splice(j, 0, b);
    renderBodies();
    scheduleSave();
  };
  openMenu('body:' + b.id, anchor, at, [
    { act: 'send', icon: '➤', label: 'Send only this body', run: () => sendOneBody(b.id) },
    { act: 'dup', icon: '⧉', label: 'Duplicate', run: () => addBody(b, i + 1) },
    { act: 'up', icon: '▲', label: 'Move up', run: () => { if (i > 0) moveTo(i - 1); } },
    { act: 'down', icon: '▼', label: 'Move down', run: () => { if (i < state.bodies.length - 1) moveTo(i + 1); } },
    '-',
    { act: 'del', icon: '🗑', label: 'Delete', danger: true, run: () => deleteBody(b) },
  ]);
}
async function deleteBody(which = null) {
  const b = which || activeBody();
  if (!b) return;
  flushBody();
  const reqId = state.current && state.current.id;
  const i = state.bodies.indexOf(b);
  if (i < 0) return;
  state.bodies.splice(i, 1);
  const wasActive = state.activeBodyId === b.id;
  const next = state.bodies[Math.min(i, state.bodies.length - 1)];
  if (wasActive) state.activeBodyId = next ? next.id : null;
  renderBodies();
  renderResponseArea();
  scheduleSave();
  toast(`Deleted body “${b.name || 'Unnamed'}”`, '', { label: 'Undo', onClick: () => {
    if (!state.current || state.current.id !== reqId) { toast('That body belongs to another request', 'error'); return; }
    state.bodies.splice(Math.min(i, state.bodies.length), 0, b);
    if (wasActive || !state.activeBodyId) state.activeBodyId = b.id;
    renderBodies();
    renderResponseArea();
    scheduleSave();
  } });
}
function setAllBodies(on) {
  state.bodies.filter((b) => bodyMatches(b, state.bodyQ)).forEach((b) => { b.enabled = on; });
  renderBodies();
  scheduleSave();
}

// ----------------------------------------------------------------- autosave
// Every edit schedules a save shortly after typing stops. Saves never touch
// the editor's fields, so typing during a save is never overwritten.
const autosave = { timer: null, inFlight: null, dirty: false, retry: null };
const AUTOSAVE_MS = 500;

function setSaveState(kind, text) {
  const el = $('#save-state');
  el.className = 'save-state ' + kind;
  el.textContent = text;
  if (typeof renderReqTabs === 'function') renderReqTabs();
}
function scheduleSave() {
  if (!state.current) return;
  autosave.dirty = true;
  setSaveState('saving', 'Editing…');
  clearTimeout(autosave.timer);
  autosave.timer = setTimeout(saveNow, AUTOSAVE_MS);
}
async function saveNow() {
  clearTimeout(autosave.timer);
  clearTimeout(autosave.retry);
  autosave.timer = null;
  while (autosave.inFlight) await autosave.inFlight;
  if (!autosave.dirty || !state.current) return;
  autosave.dirty = false;
  const target = state.current;
  const data = readEditor();
  setSaveState('saving', 'Saving…');
  autosave.inFlight = (async () => {
    try {
      const saved = target.id
        ? await api('/requests/' + target.id, { method: 'PUT', body: data })
        : await api('/requests', { method: 'POST', body: { ...data, folder_id: target.folder_id || null } });
      const wasDraft = !target.id;
      if (wasDraft) moveResponses('draft', saved.id);
      // the sidebar shows name, method, tags, folder and the body count (and filters on text):
      // typing in a URL, header or body must not re-fetch and redraw the whole tree
      const sidebarSig = (r) => JSON.stringify([r.name, r.method, r.tags, r.folder_id, r.body_type, sendCount(r)]);
      const sidebarChanged = wasDraft || !!(state.q || state.tag) || sidebarSig(target) !== sidebarSig(saved);
      Object.assign(target, { id: saved.id, folder_id: saved.folder_id, name: saved.name, method: saved.method, tags: saved.tags,
        body_type: saved.body_type, bodies: saved.bodies, auth: saved.auth, tests: saved.tests, extract: saved.extract,
        pre_vars: saved.pre_vars, options: saved.options });
      if (wasDraft) tabsRename('draft', saved.id);
      state.selected = state.selected.map((x) => x.id === saved.id ? selEntry(saved) : x);
      if (state.current === target) {
        if (!autosave.dirty) setSaveState('saved', 'Saved');
      }
      if (sidebarChanged) loadRequests();
      else {
        // keep the in-memory list current so a later full render shows the saved values
        const i = state.requests.findIndex((r) => r.id === saved.id);
        if (i >= 0) state.requests[i] = { ...state.requests[i], name: saved.name, method: saved.method, url: saved.url, tags: saved.tags, folder_id: saved.folder_id, body_type: saved.body_type, bodies: saved.bodies.map((b) => ({ id: b.id, name: b.name, enabled: b.enabled })), updated_at: saved.updated_at };
      }
    } catch (e) {
      autosave.dirty = true;
      setSaveState('error', 'Not saved, retrying…');
      toast('Autosave failed: ' + e.message, 'error');
      autosave.retry = setTimeout(saveNow, 3000);
    } finally {
      autosave.inFlight = null;
    }
  })();
  await autosave.inFlight;
}
// Last-chance save when the window closes with pending edits.
function saveOnExit() {
  if (!autosave.dirty || !state.current) return;
  const data = readEditor();
  const id = state.current.id;
  fetch('/api/requests' + (id ? '/' + id : ''), {
    method: id ? 'PUT' : 'POST', keepalive: true,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  });
}

// ------------------------------------------------------- compact header
function renderMetaTags() {
  const tags = $('#f-tags').value.split(',').map((t) => t.trim()).filter(Boolean);
  const box = $('#meta-tags');
  box.innerHTML = tags.map((t) => `<span class="chip">#${esc(t)}</span>`).join('');
  $$('.chip', box).forEach((c) => c.addEventListener('click', () => toggleDetails(true)));
  const hasDesc = $('#f-description').value.trim().length > 0;
  const open = !$('#details').hidden;
  $('#btn-details').textContent = (open ? 'Hide details ▴' : 'Details ▾') + (!open && hasDesc ? ' •' : '');
  $('#btn-details').title = hasDesc && !open ? 'Has a description. Click to view or edit tags and description' : 'Edit tags and description';
}
function toggleDetails(force) {
  const open = force === undefined ? $('#details').hidden : force;
  $('#details').hidden = !open;
  try { localStorage.setItem('pyhttp.details', open ? '1' : '0'); } catch { /* storage unavailable */ }
  renderMetaTags();
  if (open && force === true) $('#f-tags').focus();
}

// Deleting a request is undoable: the row disappears at once, the server delete fires after a
// grace period, and Undo on the toast brings it back untouched (same id, history intact).
const pendingDeletes = new Map(); // request id -> timer
const UNDO_MS = 6000;
function commitPendingDeletes() {
  for (const [id, timer] of pendingDeletes) {
    clearTimeout(timer);
    fetch('/api/requests/' + id, { method: 'DELETE', keepalive: true }).catch(() => {});
  }
  pendingDeletes.clear();
}
async function deleteRequestById(id, name) {
  const isCurrent = !!(state.current && state.current.id === id);
  if (isCurrent) {
    clearTimeout(autosave.timer);
    clearTimeout(autosave.retry);
    autosave.dirty = false;
    while (autosave.inFlight) await autosave.inFlight;
  }
  const wasSelected = state.selected.find((x) => x.id === id) || null;
  state.selected = state.selected.filter((x) => x.id !== id);
  state.requests = state.requests.filter((r) => r.id !== id);
  if (state.kbId === id) state.kbId = null;
  tabsRemove(id);
  if (isCurrent) { state.current = null; renderEditor(); }
  renderList();
  pendingDeletes.set(id, setTimeout(async () => {
    pendingDeletes.delete(id);
    try { await api('/requests/' + id, { method: 'DELETE' }); }
    catch (e) { toast(e.message, 'error'); loadRequests(); }
  }, UNDO_MS));
  toast(`Deleted “${name}”`, '', { label: 'Undo', onClick: async () => {
    const timer = pendingDeletes.get(id);
    if (timer === undefined) return;
    clearTimeout(timer);
    pendingDeletes.delete(id);
    if (wasSelected) state.selected.push(wasSelected);
    await loadRequests();
    openRequest(id);
  } });
}
async function duplicateRequestById(id) {
  await saveNow();
  try {
    const copy = await api(`/requests/${id}/duplicate`, { method: 'POST' });
    await loadRequests();
    await openRequest(copy.id);
    toast('Duplicated');
  } catch (e) { toast(e.message, 'error'); }
}

async function sendCurrent({ onlyBodyId = null } = {}) {
  if (!state.current) return;
  if (typeof isWsRequest === 'function' && isWsRequest()) { wsToggle(true); return; }   // Ctrl+Enter: connect, never disconnect
  await saveNow();
  const data = readEditor();
  data.folder_id = state.current.folder_id || null;
  if (onlyBodyId && data.body_type !== 'none') {
    // this send only: just that body, the checkboxes stay as they are
    data.bodies = data.bodies.map((b) => ({ ...b, enabled: b.id === onlyBodyId }));
  }
  let enabled = data.body_type === 'none' ? [] : data.bodies.filter((b) => b.enabled);
  if (state.sending && state.sending.key === responseKey()) return;
  const viewing = onlyBodyId || data.body_type === 'none' ? null : data.bodies.find((b) => b.id === state.activeBodyId);
  if (viewing && !viewing.enabled) {
    const what = enabled.length === 1 ? `the checked body ${strong(enabled[0].name)}`
      : enabled.length ? `the ${enabled.length} checked bodies` : 'no body (none is checked)';
    const choice = await dialog({
      title: 'Send which body?',
      message: `You are viewing ${strong(viewing.name)}, but it is not checked, so Send would use ${what}. Only checked bodies are sent; the checkbox next to a body picks it.`,
      buttons: [
        { label: 'Cancel', value: null, cancel: true },
        { label: enabled.length ? 'Send checked' : 'Send without a body', value: 'checked' },
        { label: `Send “${viewing.name}”`, value: 'viewing', cls: 'primary', primary: true },
      ],
    });
    if (!choice) return;
    if (choice === 'viewing') {
      // this send only: the checkboxes stay as they are
      data.bodies = data.bodies.map((b) => ({ ...b, enabled: b.id === viewing.id }));
      enabled = data.bodies.filter((b) => b.enabled);
    }
  }
  // pre-flight: warn about {{variables}} that have no value (like Postman's red variables)
  try {
    const pre = await api('/resolve?body_id=' + encodeURIComponent(enabled[0] ? enabled[0].id : ''), { method: 'POST', body: { ...data, id: state.current.id } });
    // a dependency (e.g. a token request) usually supplies the missing values, so no warning in that case
    if (pre.unresolved && pre.unresolved.length && !appSettings.skip_unresolved_warning && !(data.options && data.options.depends)) {
      const ok = await confirmDialog('Unresolved variables', `No value is defined for ${pre.unresolved.map((u) => `<code>{{${esc(u)}}}</code>`).join(' ')}. They will be sent as-is. Define them in Variables or the active environment, or send anyway?`, { okLabel: 'Send anyway' });
      if (!ok) return;
    }
  } catch (e) { toast('Could not prepare the request: ' + e.message, 'error'); return; }
  const sendKey = responseKey();
  state.sending = { kind: 'single', key: sendKey, id: 's' + Math.random().toString(36).slice(2, 12) };
  renderSendButton();
  const rs = responsesFor(true);
  try {
    if (enabled.length > 1) {
      // one send per checked body; every output is saved to a run folder
      delete rs.byKey[NO_BODY];
      enabled.forEach((b) => { rs.byKey[b.id] = { pending: true, body_name: b.name }; });
      rs.selected = enabled.some((b) => b.id === state.activeBodyId) ? state.activeBodyId : enabled[0].id;
      renderResponseArea();
      const run = await api('/send-bodies', { method: 'POST', body: { ...data, id: state.current.id } });
      rs.run = { id: run.id, keys: enabled.map((b) => b.id), total: run.total, completed: 0, failed: 0, status: 'running', output_dir: run.output_dir, parallel: !!(run.options && run.options.parallel) };
      state.sending = { kind: 'run', key: sendKey, runId: run.id, total: run.total, done: 0, started: state.sending && state.sending.started };
      watchRun(run.id);
      pollBodyRun(rs);
    } else {
      const key = enabled.length === 1 ? enabled[0].id : NO_BODY;
      const result = data.options && data.options.stream
        ? await sendStreaming({ ...data, id: state.current.id })
        : await api('/send', { method: 'POST', body: { ...data, id: state.current.id, send_id: state.sending.id } });
      if (result) {
        if (key === NO_BODY) rs.byKey = {};   // sent without a body: old body responses no longer apply
        else delete rs.byKey[NO_BODY];        // sent with a body: drop the old no-body response
        rs.byKey[key] = result;
        rs.selected = key;
        rs.run = null;
        renderResponseArea();
      }
    }
  } catch (e) { toast('Send failed: ' + e.message, 'error'); }
  finally {
    if (state.sending && state.sending.key === sendKey && state.sending.kind === 'single') state.sending = null;
    renderSendButton();
    pollConsole();
    if (typeof refreshHistoryList === 'function') refreshHistoryList();
    if (typeof refreshDependencyUi === 'function') refreshDependencyUi();   // "last sent … ago" in the Settings tab
  }
}

// ------------------------------------------------- responses (one per body)
// state.responses[requestKey] = { byKey: {bodyId|NO_BODY: result}, selected, run }
const NO_BODY = '__none__';
function responseKey() { return state.current ? (state.current.id || 'draft') : null; }
function responsesFor(create = false) {
  const k = responseKey();
  if (!k) return null;
  if (!state.responses[k] && create) state.responses[k] = { byKey: {}, selected: null, run: null };
  return state.responses[k] || null;
}
function moveResponses(from, to) {
  if (state.responses[from]) { state.responses[to] = state.responses[from]; delete state.responses[from]; }
}
function runResultToResponse(r) {
  const d = r.detail || {};
  const tests = r.tests || [];
  return {
    status: d.response ? 'ok' : 'error', status_code: r.status_code, reason: d.reason || '',
    duration_ms: r.duration_ms, error: r.error, request: d.request, response: d.response, body_name: r.body_name,
    tests, tests_failed: tests.filter((t) => !t.passed).length, extracted: d.extracted || [], iteration: r.iteration || 1, dependency: d.dependency || null,
    history_id: d.history_id || null,
  };
}
// response keys: a body id or NO_BODY, plus "#n" for iteration n of a run with several iterations
const bodyOfKey = (k) => String(k).split('#')[0];
const iterOfKey = (k) => Number(String(k).split('#')[1] || 0);
function responseKeyFor(req, bodyName, iteration) {
  let id = NO_BODY;
  if (req && (req.body_type || 'none') !== 'none') {
    const bodies = req.bodies || [];
    const b = bodyName ? bodies.find((x) => x.name === bodyName) : (bodies.length === 1 ? bodies[0] : null);
    if (b) id = b.id;
  }
  return iteration ? `${id}#${iteration}` : id;
}
// A collection run's results land in the response area of each request it sent: one tab per body and iteration,
// so the responses are in view beside the request without opening the run report.
function applyRunResponses(run) {
  if (!run || (run.options && run.options.mode === 'bodies')) return;   // "Send" with several bodies has its own polling
  const multiIter = run.options && run.options.iterations > 1;
  const byReq = {};
  (run.results || []).forEach((r) => { if (r.request_id) (byReq[r.request_id] = byReq[r.request_id] || []).push(r); });
  Object.entries(byReq).forEach(([id, list]) => {
    const req = state.current && state.current.id === id
      ? { body_type: $('#f-body-type').value, bodies: state.bodies }
      : state.requests.find((x) => x.id === id);
    if (!state.responses[id]) state.responses[id] = { byKey: {}, selected: null, run: null };
    const rs = state.responses[id];
    if (!rs.run || rs.run.id !== run.id) { rs.byKey = {}; rs.selected = null; rs.run = { id: run.id, keys: [] }; }
    list.forEach((r) => { rs.byKey[responseKeyFor(req, r.body_name, multiIter ? r.iteration : 0)] = runResultToResponse(r); });
    Object.assign(rs.run, { name: run.name || '', completed: run.completed, total: run.total, failed: run.failed, status: run.status, output_dir: run.output_dir, parallel: !!(run.options && run.options.parallel) });
  });
  if (state.current && byReq[state.current.id]) renderResponseArea();
}
async function pollBodyRun(rs) {
  const run = rs.run;
  if (!run) return;
  try {
    const data = await api('/runs/' + run.id);
    if (rs.run !== run) return; // a newer send replaced this one
    (data.results || []).forEach((r) => {
      const key = run.keys[r.position - 1];
      if (key) rs.byKey[key] = runResultToResponse(r);
    });
    Object.assign(run, { completed: data.completed, failed: data.failed, status: data.status });
    if (state.sending && state.sending.runId === run.id) {
      state.sending.done = data.completed;
      if (data.status !== 'running') state.sending = null;
      renderSendButton();
    }
    if (data.status !== 'running') {
      run.keys.forEach((k) => { if (rs.byKey[k] && rs.byKey[k].pending) rs.byKey[k] = { notSent: true, body_name: rs.byKey[k].body_name }; });
    }
    if (responsesFor() === rs) renderResponseArea();
    if (data.status === 'running') setTimeout(() => pollBodyRun(rs), 400);
  } catch (e) { toast(e.message, 'error'); }
}
function renderResponseArea() {
  if (typeof renderWsPanel === 'function' && renderWsPanel()) return;   // a WebSocket request shows its console here
  const rs = responsesFor();
  if (rs) {  // forget responses of bodies that were deleted
    Object.keys(rs.byKey).forEach((k) => { if (bodyOfKey(k) !== NO_BODY && !state.bodies.some((b) => b.id === bodyOfKey(k))) delete rs.byKey[k]; });
  }
  const keys = rs ? Object.keys(rs.byKey) : [];
  if (!keys.length) { $('#response').hidden = true; renderSendingUi(); return; }
  $('#response').hidden = false;
  // order tabs like the body list, then by iteration; bodies deleted since sending go last
  const order = [NO_BODY, ...state.bodies.map((b) => b.id)];
  const rank = (k) => (order.indexOf(bodyOfKey(k)) + 1 || 999) * 10000 + iterOfKey(k);
  keys.sort((a, b) => rank(a) - rank(b));
  if (!rs.selected || !rs.byKey[rs.selected]) rs.selected = keys[0];
  const strip = $('#resp-bodies');
  strip.hidden = keys.length < 2 && !(rs.run && rs.run.status === 'running');
  if (!strip.hidden) {
    const nameOf = (k) => {
      const id = bodyOfKey(k), n = iterOfKey(k);
      if (id === NO_BODY) return n ? `Iteration ${n}` : 'No body';
      const b = state.bodies.find((x) => x.id === id);
      return ((b && b.name) || rs.byKey[k].body_name || 'Body') + (n ? ` #${n}` : '');
    };
    const tab = (k) => {
      const r = rs.byKey[k];
      let meta = '';
      if (r.pending) meta = '<span class="rb-ms">sending…</span>';
      else if (r.notSent) meta = '<span class="rb-ms">not sent</span>';
      else meta = `<span class="rb-code ${codeClass(r.status_code)}">${r.status_code ?? 'ERR'}</span><span class="rb-ms">${fmtMs(r.duration_ms)}</span>`;
      const title = iterOfKey(k) ? `Show the response from iteration ${iterOfKey(k)}` : `Show the response for “${esc(nameOf(k))}”`;
      return `<button class="rb-tab ${k === rs.selected ? 'active' : ''} ${r.pending || r.notSent ? 'pending' : ''}" data-key="${esc(k)}" title="${title}">${esc(nameOf(k))}${meta}</button>`;
    };
    const runInfo = rs.run
      ? `<span class="rb-run">${rs.run.name !== undefined ? `Run${rs.run.name ? ` “${esc(rs.run.name)}”` : ''}${rs.run.status === 'running' ? ' (running)' : ''}: ` : ''}${rs.run.completed}/${rs.run.total} sent${rs.run.parallel ? ' in parallel' : ''}${rs.run.failed ? ` · ${rs.run.failed} failed` : ''}
          <button class="link small rb-folder" title="${esc(rs.run.output_dir || '')}">Open output folder</button></span>`
      : '';
    // two or more real responses: any two of them can be compared as a diff
    const real = keys.filter((k) => !rs.byKey[k].pending && !rs.byKey[k].notSent);
    const cmpBtn = real.length > 1 ? '<button class="link small rb-compare" title="Compare the response on screen with another one: body, headers and the request as sent, as a diff">⇄ Compare</button>' : '';
    strip.innerHTML = keys.map(tab).join('') + cmpBtn + runInfo;
    const cmp = $('.rb-compare', strip);
    if (cmp) cmp.addEventListener('click', () => {
      const others = real.filter((k) => k !== rs.selected);
      if (!real.includes(rs.selected) || !others.length) return;
      const go = (k) => compareResults({ label: nameOf(k), result: rs.byKey[k] }, { label: nameOf(rs.selected), result: rs.byKey[rs.selected] });
      if (others.length === 1) go(others[0]);
      else openMenu('rb-compare', cmp, null, others.map((k) => ({ act: 'cmp:' + k, icon: '⇄', label: `with “${nameOf(k)}”`, run: () => go(k) })));
    });
    $$('.rb-tab', strip).forEach((el) => el.addEventListener('click', () => {
      rs.selected = el.dataset.key;
      renderResponseArea();
      const bid = bodyOfKey(rs.selected);
      if (state.bodies.some((b) => b.id === bid) && state.activeBodyId !== bid) {
        flushBody();
        state.activeBodyId = bid;
        renderBodies();
      }
    }));
    const folderBtn = $('.rb-folder', strip);
    if (folderBtn) folderBtn.addEventListener('click', () => openRunFolder(rs.run.id));
  }
  const r = rs.byKey[rs.selected];
  const head = $('#response .response-head');
  const panels = $('#resp-panels');
  let pend = $('#resp-pending');
  if (r.pending || r.notSent) {
    head.hidden = true;
    panels.hidden = true;
    if ($('#r-tip')) $('#r-tip').hidden = true;
    if (!pend) { pend = document.createElement('div'); pend.id = 'resp-pending'; pend.className = 'resp-pending'; panels.after(pend); }
    pend.hidden = false;
    pend.textContent = r.pending ? `Sending “${r.body_name}”…` : `“${r.body_name}” was not sent (the run stopped first).`;
    return;
  }
  head.hidden = false;
  panels.hidden = false;
  if (pend) pend.hidden = true;
  renderResponse(r, !strip.hidden);
}
function renderResponse(result, inTabs = false) {
  $('#response').hidden = false;
  const bn = $('#r-body-name');
  const shownBody = bodyOfKey((responsesFor() || {}).selected || '');
  const otherBody = shownBody && shownBody !== NO_BODY && state.activeBodyId && shownBody !== state.activeBodyId
    && state.bodies.some((b) => b.id === state.activeBodyId);
  bn.hidden = (!result.body_name || inTabs) && !otherBody;
  bn.textContent = result.body_name || (state.bodies.find((b) => b.id === shownBody) || {}).name || '';
  bn.classList.toggle('other', !!otherBody);
  bn.title = otherBody ? `This is the response of “${bn.textContent}”, not of the body you are viewing (“${(activeBody() || {}).name || ''}”), which has not been sent yet.` : 'The body this response belongs to';
  renderMethodTip(result);
  renderDependencyBadge(result.dependency);
  const st = $('#r-status');
  if (result.status === 'cancelled') {
    st.textContent = 'Cancelled';
    st.className = 'status warn';
    st.title = 'The call was stopped before a response arrived';
    $('#r-time').textContent = '';
    $('#r-size').textContent = '';
  } else if (result.status === 'error' || !result.response) {
    st.textContent = 'Error';
    st.className = 'status err';
    st.title = result.error || '';
    $('#r-time').textContent = fmtMs(result.duration_ms);
    $('#r-size').textContent = '';
  } else {
    const code = result.status_code;
    st.textContent = `${code} ${result.reason || ''}`.trim();
    st.className = 'status ' + (code < 300 ? 'ok' : code < 400 ? 'warn' : 'err');
    st.title = '';
    $('#r-time').textContent = fmtMs(result.duration_ms);
    $('#r-size').textContent = fmtBytes(result.response.size);
  }
  if ($('#r-body')._lastResult !== result) {
    $('#r-body')._lastResult = result;
    renderResponsePanels(result);
  }
}

// A GET that fails often just needs another method (405 Method Not Allowed, or an API that
// expects the body in a POST): suggest one, using the server's Allow header when it sends one.
function renderMethodTip(result) {
  let tip = $('#r-tip');
  const method = ((result.request && result.request.method) || $('#f-method').value || '').toUpperCase();
  const code = result.status_code;
  const httpErr = code >= 400 && ![401, 403, 407, 429].includes(code);
  if (method !== 'GET' || !(httpErr || (result.status === 'error' && result.response))) { if (tip) tip.hidden = true; return; }
  if (!tip) {
    tip = document.createElement('div');
    tip.id = 'r-tip';
    tip.className = 'r-tip small';
    $('#response .response-head').after(tip);
  }
  const allowHdr = ((result.response && result.response.headers) || []).find(([k]) => k.toLowerCase() === 'allow');
  const allowed = allowHdr ? allowHdr[1].split(',').map((m) => m.trim().toUpperCase()).filter((m) => m && m !== 'GET' && m !== 'HEAD' && m !== 'OPTIONS') : [];
  const methods = $$('#f-method option').map((o) => o.value);
  const suggest = (allowed.filter((m) => methods.includes(m)).length ? allowed.filter((m) => methods.includes(m)) : ['POST', 'PUT', 'PATCH']).slice(0, 3);
  const hasBody = $('#f-body-type').value !== 'none' && state.bodies.some((b) => b.enabled && (b.body || '').trim());
  let why = `GET returned ${code ?? 'an error'}.`;
  if (code === 405) why = `The server does not allow GET here (405 Method Not Allowed)${allowed.length ? `; it accepts ${allowed.map((m) => `<code>${esc(m)}</code>`).join(', ')}` : ''}.`;
  else if (hasBody) why = `GET returned ${code ?? 'an error'} and this request has a body; most servers ignore a body on GET.`;
  tip.innerHTML = `<span class="r-tip-i">💡</span><span><strong>Tip:</strong> ${why} Try ${suggest.length > 1 ? 'another method, such as' : ''}</span>` +
    suggest.map((m) => `<button type="button" class="link small r-tip-m" data-m="${esc(m)}">${esc(m)}</button>`).join('<span class="muted">·</span>') +
    '<span class="grow"></span><button type="button" class="link small r-tip-x" title="Hide this tip">✕</button>';
  tip.hidden = false;
  $$('.r-tip-m', tip).forEach((b) => b.addEventListener('click', () => {
    const sel = $('#f-method');
    sel.value = b.dataset.m;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    tip.hidden = true;
    toast(`Method set to ${b.dataset.m}. Press Send to try it.`, 'ok');
  }));
  $('.r-tip-x', tip).addEventListener('click', () => { tip.hidden = true; });
}

// --------------------------------------------------------------------- runs
function openRunModal(opts = {}) {
  const order = (opts.items || state.selected).slice();
  if (!order.length) return;
  const env = state.envs.find((e) => e.id === state.activeEnvId);
  const renderOrder = () => {
    const ul = $('#order-list');
    ul.innerHTML = '';
    order.forEach((s, i) => {
      const li = document.createElement('li');
      li.innerHTML =
        `<span class="muted small">${i + 1}</span>` +
        `<span class="method m-${esc(s.method)}">${esc(s.method)}</span>` +
        `<span class="name">${esc(s.name)}${s.n > 1 ? ` <span class="bcount">×${s.n} bodies</span>` : ''}</span>` +
        `<span class="ctl"><button class="icon" data-act="up" title="Move up" aria-label="Move up" ${i === 0 ? 'disabled' : ''}>▲</button>` +
        `<button class="icon" data-act="down" title="Move down" aria-label="Move down" ${i === order.length - 1 ? 'disabled' : ''}>▼</button>` +
        `<button class="icon" data-act="rm" title="Remove from run" aria-label="Remove from run">✕</button></span>`;
      $$('button', li).forEach((b) => b.addEventListener('click', () => {
        if (b.dataset.act === 'up') [order[i - 1], order[i]] = [order[i], order[i - 1]];
        if (b.dataset.act === 'down') [order[i + 1], order[i]] = [order[i], order[i + 1]];
        if (b.dataset.act === 'rm') order.splice(i, 1);
        renderOrder();
      }));
      ul.appendChild(li);
    });
    const sends = order.reduce((a, s) => a + (s.n || 1), 0);
    $('#order-total').textContent = `${order.length} request(s), ${sends} send(s) in total.`;
  };
  runData.rows = []; runData.columns = []; runData.name = '';
  // in order / in parallel is one setting (Settings → Requests, Send ▾, this dialog): never a stale per-run value
  const parallel = !!appSettings.parallel;
  const concurrency = appSettings.concurrency || 8;
  openModal(opts.title || 'Run requests', `
    <label class="field">Run name <input id="run-name" class="grow" placeholder="optional: left empty, the run is named after the time"></label>
    <div class="run-order">
      <span class="set-label">How to send</span>
      <div class="seg" id="run-mode" role="radiogroup">
        <button type="button" data-mode="seq" class="${parallel ? '' : 'on'}" title="One after another, top to bottom; {{prev.…}} reads the previous response">In order</button>
        <button type="button" data-mode="par" class="${parallel ? 'on' : ''}" title="Everything at once; fastest, but no order and no {{prev.…}}">In parallel</button>
      </div>
      <label class="run-conc" ${parallel ? '' : 'hidden'}>at most <input type="number" id="run-concurrency" min="1" max="64" step="1" value="${concurrency}"> at once</label>
      <input type="checkbox" id="run-parallel" ${parallel ? 'checked' : ''} hidden>
    </div>
    <div class="muted small" id="run-order-hint"></div>
    <div class="muted small">A request with several checked bodies is sent once per body. Use ▲▼ to reorder. The default is in Settings → Requests.</div>
    <div id="order-total" class="small" style="margin-top:6px"></div>
    <div class="small" style="margin-top:4px">Environment: ${env ? `<strong>${esc(env.name)}</strong>` : '<span class="muted">none (global variables only)</span>'}</div>
    <ul id="order-list" class="order-list"></ul>
    <div class="opts">
      <label><input type="checkbox" id="run-stop"> Stop the run at the first failure</label>
      <label><input type="checkbox" id="run-http-fail" checked> Treat HTTP status ≥ 400 as a failure</label>
      <label><input type="checkbox" id="run-test-fail" checked> Treat a failing test as a failure</label>
      <label>Delay between requests <input type="number" id="run-delay" min="0" step="50" value="0"> ms <span class="muted small">(in order only)</span></label>
      <label>Iterations <input type="number" id="run-iterations" min="1" max="1000" step="1" value="1"> <span class="muted small">run the whole list this many times</span></label>
      <label><span style="white-space:nowrap">Variables file</span> <button id="run-data-pick" type="button" title="A CSV or JSON file with one row of variable values per iteration">Choose CSV or JSON…</button> <span id="run-data-info" class="small"></span></label>
      <div class="muted small">Not a request body: each row holds the values of your <code>{{variables}}</code> for one iteration, with a column per variable (also as <code>{{data.column}}</code>).
        CSV with a header row, or a JSON array of objects. <button class="link small" id="run-data-sample" type="button" title="Save a sample file with a column for each {{variable}} these requests use">Download sample CSV</button>
        <button class="link small" id="run-data-try" type="button" title="Run with the sample file to see how a variables file works">Use the sample</button></div>
    </div>
  `, [
    { label: 'Cancel', onClick: closeModal },
    { label: '▶ Start run', cls: 'accent', onClick: async () => {
      if (!order.length) return toast('Nothing to run', 'error');
      const body = {
        request_ids: order.map((s) => s.id),
        name: $('#run-name').value,
        stop_on_error: $('#run-stop').checked,
        fail_on_http_error: $('#run-http-fail').checked,
        fail_on_test: $('#run-test-fail').checked,
        delay_ms: Number($('#run-delay').value) || 0,
        iterations: Math.max(1, Number($('#run-iterations').value) || 1),
        data: runData.rows,
        parallel: $('#run-parallel').checked,
        concurrency: Math.max(1, Number($('#run-concurrency').value) || 8),
      };
      rememberRunOptions(body);
      if (body.parallel !== !!appSettings.parallel || (body.parallel && body.concurrency !== (appSettings.concurrency || 8))) {
        try { await saveSettings({ parallel: body.parallel, concurrency: body.concurrency }); renderSendButton(); } catch { /* the run still uses the choice */ }
      }
      closeModal();
      await startRun(body);
    } },
  ]);
  renderOrder();
  const setMode = (par) => {
    $('#run-parallel').checked = par;
    $$('#run-mode button').forEach((b) => b.classList.toggle('on', (b.dataset.mode === 'par') === par));
    $('.run-conc').hidden = !par;
    $('#run-delay').disabled = par;
    $('#run-order-hint').textContent = par
      ? 'Everything is sent at once (bounded by the number above). Fastest, but there is no order, no delay, and {{prev.…}} has no previous response.'
      : 'Requests are sent one by one, top to bottom; each one can read the previous response with {{prev.…}}.';
  };
  $$('#run-mode button').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode === 'par')));
  setMode(parallel);
  $('#run-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); $('#modal-foot button.accent').click(); } });
  renderDataFileInfo();
  $('#run-data-pick').addEventListener('click', pickDataFile);
  const sampleData = () => api('/runs/sample-data?request_ids=' + encodeURIComponent(order.map((s) => s.id).filter(Boolean).join(',')));
  $('#run-data-sample').addEventListener('click', async () => {
    try { const f = await sampleData(); await downloadText(f.text, f.filename); } catch (e) { toast(e.message, 'error'); }
  });
  $('#run-data-try').addEventListener('click', async () => {
    try {
      const f = await sampleData();
      runData.rows = f.rows; runData.columns = f.columns; runData.name = f.filename;
      renderDataFileInfo();
    } catch (e) { toast(e.message, 'error'); }
  });
  $('#run-name').value = opts.name || '';
  $('#run-name').focus();
}

// The options of the last run (not the name or the variables file), so "Run now" can skip the dialog.
// Parallel and concurrency are not among them: they come from the settings, so an old run cannot override them.
const RUN_OPTION_KEYS = ['stop_on_error', 'fail_on_http_error', 'fail_on_test', 'delay_ms', 'iterations'];
function lastRunOptions() {
  try { return JSON.parse(localStorage.getItem('pyhttp.runOptions') || '{}') || {}; } catch { return {}; }
}
function rememberRunOptions(body) {
  const keep = {};
  RUN_OPTION_KEYS.forEach((k) => { keep[k] = body[k]; });
  try { localStorage.setItem('pyhttp.runOptions', JSON.stringify(keep)); } catch { /* storage unavailable */ }
}
async function startRun(body) {
  try {
    const run = await api('/runs', { method: 'POST', body });
    watchRun(run.id);
    showTab($('#runs-tabs'), 'current');
  } catch (e) { toast(e.message, 'error'); }
}
// "Run now": the selected requests straight away, with the last run's options and no name.
// Replay the ⚡ flash on every press (the class is removed again when the animation ends).
function zap(btn) {
  if (!btn) return;
  btn.classList.remove('zap');
  void btn.offsetWidth;   // restart the animation
  btn.classList.add('zap');
  btn.addEventListener('animationend', () => btn.classList.remove('zap'), { once: true });
}
function runSelectedNow() {
  if (!state.selected.length) return;
  zap($('#btn-run-now'));
  const n = state.selected.reduce((a, s) => a + (s.n || 1), 0);
  toast(`⚡ Running ${state.selected.length} request${state.selected.length === 1 ? '' : 's'} now (${n} send${n === 1 ? '' : 's'})`, 'ok');
  const last = lastRunOptions();
  startRun({ request_ids: state.selected.map((s) => s.id), name: '', parallel: !!appSettings.parallel, concurrency: appSettings.concurrency || 8,
             ...Object.fromEntries(RUN_OPTION_KEYS.filter((k) => k in last).map((k) => [k, last[k]])), iterations: 1, data: [] });
}

function watchRun(runId) {
  clearTimeout(state.pollTimer);
  state.activeRunId = runId;
  if (typeof setRunsPanel === 'function' && !runsPanelShown()) setRunsPanel(true);
  pollRun();
}
async function pollRun() {
  if (!state.activeRunId) return;
  try {
    const run = await api('/runs/' + state.activeRunId);
    renderCurrentRun(run);
    applyRunResponses(run);
    if (run.status === 'running') {
      state.pollTimer = setTimeout(pollRun, 600);
    } else {
      loadHistory();
      if (typeof refreshHistoryList === 'function') refreshHistoryList();
      const msg = `Run ${run.status}: ${run.completed}/${run.total} sent, ${run.failed} failed`;
      toast(msg, run.failed || run.status !== 'completed' ? 'error' : 'ok');
    }
  } catch (e) { toast(e.message, 'error'); }
}

function renderCurrentRun(run) {
  const box = $('#run-current');
  const pct = run.total ? Math.round((run.completed / run.total) * 100) : 0;
  const results = run.results || [];
  const testsTotal = results.reduce((a, r) => a + (r.tests || []).length, 0);
  const testsFailed = results.reduce((a, r) => a + (r.tests || []).filter((t) => !t.passed).length, 0);
  const iterations = run.options && run.options.iterations > 1 ? run.options.iterations : 0;
  const failedOnly = box.dataset.failedOnly === '1';
  const head = `
    <div class="run-head">
      <div class="title"><span class="grow">${esc(run.name)}</span>${run.options && run.options.environment ? `<span class="run-env" title="Environment">${esc(run.options.environment)}</span>` : ''}<span class="badge ${esc(run.status)}">${esc(run.status)}</span></div>
      <div class="progress ${run.failed ? 'has-fail' : ''}"><div style="width:${pct}%"></div></div>
      <div class="muted small">${run.completed}/${run.total} sent · ${run.failed} failed${testsTotal ? ` · tests <span class="${testsFailed ? 'err-text' : 'ok-text'}">${testsTotal - testsFailed}/${testsTotal} passed</span>` : ''}${iterations ? ` · ${iterations} iterations` : ''}${run.options && run.options.parallel ? ' · in parallel' : ''} · started ${fmtDate(run.started_at)}</div>
      <div class="path" title="Output folder">${esc(run.output_dir)}</div>
      <div class="run-actions">
        ${run.status === 'running' ? '<button id="btn-cancel-run" class="danger">Cancel run</button>' : ''}
        <button id="btn-open-folder">Open folder</button>
        <a class="link" href="/api/runs/${run.id}/files/summary.json" target="_blank"><button>summary.json</button></a>
        <button id="btn-report-html" title="Save an HTML report of this run">Report (HTML)</button>
        <button id="btn-report-json" title="Save the full results as JSON">Report (JSON)</button>
        <label class="small failed-filter"><input type="checkbox" id="run-failed-only" ${failedOnly ? 'checked' : ''}> Failed only</label>
      </div>
    </div>`;
  box.innerHTML = head + `<ul class="result-list">${resultItemsHtml(run, failedOnly)}</ul>`;
  const cancelBtn = $('#btn-cancel-run', box);
  if (cancelBtn) cancelBtn.addEventListener('click', async () => {
    await api(`/runs/${run.id}/cancel`, { method: 'POST' });
    toast('Cancelling…');
  });
  $('#btn-open-folder', box).addEventListener('click', () => openRunFolder(run.id));
  $('#btn-report-html', box).addEventListener('click', () => exportRunReport(run.id, 'html'));
  $('#btn-report-json', box).addEventListener('click', () => exportRunReport(run.id, 'json'));
  $('#run-failed-only', box).addEventListener('change', (e) => { box.dataset.failedOnly = e.target.checked ? '1' : '0'; renderCurrentRun(run); });
  bindResultClicks(box, run);
}
async function openRunFolder(runId) {
  try { await api(`/runs/${runId}/open-folder`, { method: 'POST' }); }
  catch (e) { toast(e.message, 'error'); }
}
function resultItemsHtml(run, failedOnly = false) {
  const results = run.results || [];
  const rows = results.filter((r) => !failedOnly || r.status === 'failed').map((r) => resultRow(run, r));
  if (failedOnly && !rows.length) rows.push('<li class="empty small">No failed requests.</li>');
  // in order: only the next one is in flight; in parallel: up to `concurrency` of them at once
  const opts = run.options || {};
  const inFlight = opts.parallel ? Math.max(1, Math.min(64, opts.concurrency || 8)) : 1;
  for (let i = results.length + 1; i <= run.total && !failedOnly; i++) {
    const now = run.status === 'running' && i <= results.length + inFlight;
    const label = now ? 'sending…' : run.status === 'running' ? 'waiting' : 'not sent';
    rows.push(`<li class="result-item pending ${now ? 'active-now' : ''}"><span class="pos">${i}</span><span class="name muted">${label}</span><span></span></li>`);
  }
  return rows.join('');
}
function bindResultClicks(box, run) {
  $$('.result-item[data-rid]', box).forEach((li) => li.addEventListener('click', () => {
    const r = (run.results || []).find((x) => x.id === li.dataset.rid);
    if (r) openResultModal(run, r);
  }));
}
function resultRow(run, r) {
  const code = r.status_code;
  const codeCls = code == null ? 'err' : code < 300 ? 'ok' : code < 400 ? 'warn' : 'err';
  const tests = r.tests || [];
  const tf = tests.filter((t) => !t.passed).length;
  const iter = run.options && run.options.iterations > 1 ? `<span class="iter" title="Iteration">#${r.iteration || 1}</span>` : '';
  return `<li class="result-item ${r.status === 'failed' ? 'failed' : ''}" data-rid="${r.id}">
    <span class="pos">${r.position}</span>
    <span class="name">${iter}<span class="method m-${esc(r.method)}">${esc(r.method)}</span> ${esc(r.request_name)}${r.body_name ? ` <span class="bn">· ${esc(r.body_name)}</span>` : ''}</span>
    <span class="meta">${tests.length ? `<span class="tcount ${tf ? 'fail' : 'pass'}" title="${tests.length - tf} of ${tests.length} tests passed">${tf ? '✕' : '✓'} ${tests.length - tf}/${tests.length}</span>` : ''}<span class="status ${codeCls}">${code == null ? 'ERR' : code}</span><span class="muted">${fmtMs(r.duration_ms)}</span></span>
  </li>`;
}

function openResultModal(run, r) {
  const d = r.detail || {};
  const resp = d.response;
  // the two files a result writes to the run folder, each with what it holds (the names alone confuse people)
  const fileRow = (f, what) => f ? `<div class="file-row"><a class="mono" href="/api/runs/${run.id}/files/${encodeURIComponent(f)}" target="_blank">${esc(f)}</a><span class="muted small">${what}</span></div>` : '';
  const files = fileRow(r.detail_file, 'Report of this send: the request as sent, status, timing, response headers, tests and extracted variables (JSON).')
    + fileRow(r.body_file, 'The raw response body only, exactly as the server returned it (open it in a browser or editor).');
  const code = r.status_code;
  const codeCls = code == null ? 'err' : code < 300 ? 'ok' : code < 400 ? 'warn' : 'err';
  const tests = r.tests || [];
  const ext = d.extracted || [];
  openModal(`${r.position}. ${r.request_name}${r.body_name ? ' · ' + r.body_name : ''}${run.options && run.options.iterations > 1 ? ` (iteration ${r.iteration || 1})` : ''}`, `
    <div class="row">
      <span class="status ${codeCls}">${code == null ? 'Error' : `${code} ${esc(d.reason || '')}`}</span>
      <span class="badge ${esc(r.status)}">${esc(r.status)}</span>
      <span class="muted small">${fmtMs(r.duration_ms)}</span>
      <span class="muted small">${resp ? fmtBytes(resp.size) : ''}</span>
      <span class="grow"></span>
      ${r.request_id ? `<button class="small" id="res-goto">Open request</button>` : ''}
    </div>
    ${r.error ? `<pre class="mono err-text">${esc(r.error)}</pre>` : ''}
    ${tests.length ? `<div class="detail-section"><h4>Tests</h4><ul class="test-results">${tests.map((t) => `<li class="${t.passed ? 'pass' : 'fail'}"><span class="mark">${t.passed ? '✓' : '✕'}</span><span class="tn">${esc(t.name)}</span><span class="note muted small">${esc(t.note || '')}</span></li>`).join('')}</ul></div>` : ''}
    ${ext.length ? `<div class="detail-section"><h4>Extracted variables</h4><ul class="test-results">${ext.map((e) => `<li class="${e.ok ? 'pass' : 'fail'}"><span class="mark">${e.ok ? '✓' : '✕'}</span><span class="tn mono">${esc(e.name)}${e.ok ? ' = ' + esc(String(e.value).slice(0, 120)) : ''}</span><span class="note muted small">${esc(e.ok ? e.scope : e.note)}</span></li>`).join('')}</ul></div>` : ''}
    <div class="detail-section"><h4>Files</h4><div class="files">${files || '<span class="muted">none</span>'}</div></div>
    <div class="detail-section"><h4>Response body</h4><div id="res-viewer"></div></div>
    <div class="detail-section"><h4>Response headers</h4><pre class="mono">${esc((resp ? resp.headers : []).map(([k, v]) => `${k}: ${v}`).join('\n'))}</pre></div>
    <div class="detail-section"><h4>Request as sent</h4><pre class="mono">${esc(formatRequest(d.request))}</pre></div>
  `, [{ label: 'Close', onClick: closeModal }]);
  if (resp) mountViewer($('#res-viewer'), resp); else $('#res-viewer').innerHTML = '<div class="muted small">No response</div>';
  const go = $('#res-goto');
  if (go) go.addEventListener('click', () => { closeModal(); openRequest(r.request_id).catch(() => toast('That request no longer exists', 'error')); });
}

const RUN_PAGE = 100;
async function loadHistory(before = null) {
  const box = $('#run-history');
  try {
    const runs = await api('/runs?limit=' + RUN_PAGE + (before ? '&before=' + encodeURIComponent(before) : ''));
    if (!runs.length && !before) { box.innerHTML = '<div class="empty small">No runs yet.</div>'; return; }
    const more = $('.load-more', box);
    if (more) more.remove();
    const html = runs.map((r) => `
      <div class="history-item" data-id="${r.id}">
        <div class="line"><span class="name">${esc(r.name)}</span><span class="badge ${esc(r.status)}">${esc(r.status)}</span>
          <button class="icon link" data-del="${r.id}" title="Delete from run history (files are kept)" aria-label="Delete run from history">✕</button></div>
        <div class="line muted small"><span>${fmtDate(r.started_at)}</span><span>· ${r.completed}/${r.total} sent</span><span>· ${r.failed} failed</span>${r.options && r.options.environment ? `<span>· ${esc(r.options.environment)}</span>` : ''}</div>
      </div>`).join('') + (runs.length === RUN_PAGE ? `<button class="link small load-more" data-before="${runs[runs.length - 1].started_at}">Show older runs…</button>` : '');
    if (before) box.insertAdjacentHTML('beforeend', html); else box.innerHTML = html;
    const lm = $('.load-more', box);
    if (lm) lm.addEventListener('click', () => loadHistory(Number(lm.dataset.before)));
    $$('.history-item:not([data-bound])', box).forEach((el) => el.addEventListener('click', () => {
      watchRun(el.dataset.id);
      showTab($('#runs-tabs'), 'current');
    }));
    $$('.history-item:not([data-bound]) button[data-del]', box).forEach((b) => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!await confirmDialog('Remove run from run history?', 'The run is removed from this list. Its output files stay on disk.', { okLabel: 'Remove', danger: true })) return;
      try { await api('/runs/' + b.dataset.del, { method: 'DELETE' }); loadHistory(); }
      catch (err) { toast(err.message, 'error'); }
    }));
    $$('.history-item:not([data-bound])', box).forEach((el) => { el.dataset.bound = '1'; });
  } catch (e) { box.innerHTML = `<div class="empty small">${esc(e.message)}</div>`; }
}

// ---------------------------------------------------------------- variables
async function openVariables() {
  const vars = await api('/variables');
  const envNow = state.envs.find((e) => e.id === state.activeEnvId);
  openModal('Global variables', `
    <p class="muted small">Use anywhere as <code>{{name}}</code>. Inside a run, <code>{{prev.json.path}}</code>, <code>{{prev.status}}</code>, <code>{{prev.body}}</code> and <code>{{prev.headers.name}}</code> read the previous response.</p>
    <p class="muted small">Global variables apply in every environment. ${envNow ? `Values in the active environment ${strong(envNow.name)} override these.` : 'Pick an environment in the top bar to override them per stage (Dev, QA, Prod…).'}</p>
    <div id="kv-vars" class="kv"></div>
  `, [
    { label: 'Cancel', onClick: closeModal },
    { label: 'Save', cls: 'primary', onClick: async () => {
      const obj = {};
      readKv($('#kv-vars')).forEach((r) => { if (r.key.trim()) obj[r.key.trim()] = r.value; });
      try { await api('/variables', { method: 'PUT', body: { variables: obj } }); closeModal(); toast('Variables saved', 'ok'); }
      catch (e) { toast(e.message, 'error'); }
    } },
  ]);
  renderKv($('#kv-vars'), Object.entries(vars).map(([key, value]) => ({ key, value })), false);
}

// ------------------------------------------------------------ import/export
const native = () => window.pywebview && window.pywebview.api;

async function exportAll() {
  const data = await api('/export');
  const text = JSON.stringify(data, null, 2);
  const filename = `pyhttp-export-${new Date().toISOString().slice(0, 10)}.json`;
  if (native()) {
    try {
      const path = await native().save_file(text, filename);
      if (path) toast('Exported to ' + path, 'ok');
    } catch (e) { toast('Export failed: ' + e.message, 'error'); }
    return;
  }
  const blob = new Blob([text], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
async function importClicked() { importDialog(); }
async function importFile(file) {
  const text = await file.text();
  if (/^\s*curl(\.exe)?\s/i.test(text)) await importCurl(text, state.current ? state.current.folder_id : null);
  else await importText(text);
}
async function importText(text) {
  let data;
  try { data = JSON.parse(text); } catch {
    // not JSON: an OpenAPI spec in YAML?
    try { data = (await api('/import/parse', { method: 'POST', body: { text } })).data; }
    catch (e) { toast('Import failed: the file is not valid JSON or YAML (or a curl command)', 'error'); return; }
  }
  try {
    const kind = await api('/import/detect', { method: 'POST', body: { data } });
    if (['postman_collection', 'postman_environment', 'openapi', 'har'].includes(kind.kind)) { await importPostman(data, kind.kind, kind); return; }
    if (kind.kind === 'unknown') { toast('Import failed: not a pyhttp export, Postman collection or environment, OpenAPI / Swagger spec or HAR file', 'error'); return; }
  } catch (e) { toast('Import failed: ' + e.message, 'error'); return; }
  const requests = Array.isArray(data) ? data : (data.requests || []);
  const folders = Array.isArray(data) ? [] : (data.folders || []);
  const environments = Array.isArray(data) ? [] : (data.environments || []);
  const variables = Array.isArray(data) ? {} : (data.variables || {});
  const mocks = Array.isArray(data) ? [] : (data.mocks || []);
  const monitors = Array.isArray(data) ? [] : (data.monitors || []);
  const what = [`${requests.length} request(s)`];
  if (folders.length) what.push(`${folders.length} folder(s)`);
  if (environments.length) what.push(`${environments.length} environment(s)`);
  if (mocks.length) what.push(`${mocks.length} mock server(s)`);
  if (monitors.length) what.push(`${monitors.length} monitor(s)`);
  const choice = await dialog({
    title: 'Import collection',
    message: `The file contains ${strong(what.join(', '))}. Add them to your collection, or replace everything you have now?`,
    buttons: [
      { label: 'Cancel', value: null, cancel: true },
      { label: 'Replace everything', value: 'replace', cls: 'danger' },
      { label: 'Add to collection', value: 'add', cls: 'primary', primary: true },
    ],
  });
  if (!choice) return;
  const replace = choice === 'replace';
  if (replace && !await confirmDialog('Replace everything?', 'All current requests, folders and environments (and mocks and monitors, when the file has them) are deleted and replaced by the file. This cannot be undone.', { okLabel: 'Replace everything', danger: true })) return;
  try {
    const res = await api('/import', { method: 'POST', body: { requests, folders, environments, variables, mocks, monitors, replace } });
    toast(`Imported ${res.imported} request(s)` + (res.folders ? `, ${res.folders} folder(s)` : '') + (res.environments ? `, ${res.environments} environment(s)` : '')
      + (res.mocks ? `, ${res.mocks} mock server(s)` : '') + (res.monitors ? `, ${res.monitors} monitor(s)` : ''), 'ok');
    state.selected = [];
    if (replace && state.current) { autosave.dirty = false; state.current = null; renderEditor(); }
    await Promise.all([loadRequests(), loadEnvironments(),
      typeof loadMocks === 'function' ? loadMocks(false) : null, typeof loadMonitors === 'function' ? loadMonitors(false) : null]);
  } catch (e) { toast('Import failed: ' + e.message, 'error'); }
}

// ================================================================ dialogs
// In-app replacements for confirm()/prompt(). All return promises.
function dialog({ title, message = '', field = null, buttons, danger = false }) {
  return new Promise((resolve) => {
    const prevFocus = document.activeElement;
    const back = document.createElement('div');
    back.className = 'dialog-backdrop';
    let fieldHtml = '';
    if (field && field.options) {
      fieldHtml = `<div class="dialog-field">${field.label ? `<label for="dlg-in">${esc(field.label)}</label>` : ''}
        <select id="dlg-in">${field.options.map((o) => `<option value="${esc(o.value)}" ${o.value === field.value ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select></div>`;
    } else if (field) {
      fieldHtml = `<div class="dialog-field">${field.label ? `<label for="dlg-in">${esc(field.label)}</label>` : ''}
        <input id="dlg-in" value="${esc(field.value || '')}" placeholder="${esc(field.placeholder || '')}" spellcheck="false" autocomplete="off"></div>`;
    }
    back.innerHTML = `
      <div class="dialog ${danger ? 'danger' : ''}" role="alertdialog" aria-modal="true" aria-labelledby="dlg-title">
        <div class="dialog-head"><span class="dialog-icon">${danger ? '!' : field ? '✎' : '?'}</span><h3 id="dlg-title">${esc(title)}</h3></div>
        ${message ? `<div class="dialog-msg">${message}</div>` : ''}
        ${fieldHtml}
        <div class="dialog-foot"></div>
      </div>`;
    const foot = $('.dialog-foot', back);
    const input = $('#dlg-in', back);
    let primary = null;
    const done = (value) => {
      back.remove();
      if (prevFocus && prevFocus.focus) prevFocus.focus();
      resolve(value);
    };
    const cancelValue = (buttons.find((b) => b.cancel) || {}).value ?? null;
    buttons.forEach((b) => {
      const el = document.createElement('button');
      el.textContent = b.label;
      el.className = b.cls || '';
      el.addEventListener('click', () => {
        if (input && b.value === true) {
          const v = input.value.trim();
          if (!v && !field.options) { input.focus(); return; }
          done(v);
        } else done(b.value);
      });
      foot.appendChild(el);
      if (b.primary) primary = el;
    });
    back.addEventListener('mousedown', (e) => { if (e.target === back) done(cancelValue); });
    back.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Escape') { e.preventDefault(); done(cancelValue); }
      if (e.key === 'Enter' && primary && e.target.tagName !== 'BUTTON') { e.preventDefault(); primary.click(); }
      trapFocus(back, e);   // keep focus inside the dialog
    });
    document.body.appendChild(back);
    if (input) { input.focus(); if (input.select) input.select(); } else (primary || $('button', foot)).focus();
  });
}
async function confirmDialog(title, message, { okLabel = 'OK', danger = false } = {}) {
  const v = await dialog({ title, message, danger, buttons: [
    { label: 'Cancel', value: false, cancel: true },
    { label: okLabel, value: true, cls: danger ? 'danger-solid' : 'primary', primary: true },
  ] });
  return v === true;
}
function promptDialog(title, { label = '', value = '', placeholder = '', okLabel = 'Save', message = '' } = {}) {
  return dialog({ title, message, field: { label, value, placeholder }, buttons: [
    { label: 'Cancel', value: null, cancel: true },
    { label: okLabel, value: true, cls: 'primary', primary: true },
  ] });
}
function selectDialog(title, { label = '', options, value, okLabel = 'OK', message = '' }) {
  return dialog({ title, message, field: { label, options, value }, buttons: [
    { label: 'Cancel', value: null, cancel: true },
    { label: okLabel, value: true, cls: 'primary', primary: true },
  ] });
}
const strong = (s) => `<strong>${esc(s)}</strong>`;

// ================================================================ folders
function saveExpanded() {
  try { localStorage.setItem('pyhttp.expanded', JSON.stringify([...state.expanded])); } catch { /* ignore */ }
}
function loadExpanded() {
  try { state.expanded = new Set(JSON.parse(localStorage.getItem('pyhttp.expanded') || '[]')); } catch { state.expanded = new Set(); }
}
const folderById = (id) => state.folders.find((f) => f.id === id) || null;
function folderPath(id) {
  const parts = [];
  let f = folderById(id);
  const seen = new Set();
  while (f && !seen.has(f.id)) { seen.add(f.id); parts.unshift(f.name); f = folderById(f.parent_id); }
  return parts;
}
function folderDescendants(id) {
  const out = new Set([id]);
  let grew = true;
  while (grew) {
    grew = false;
    state.folders.forEach((f) => { if (f.parent_id && out.has(f.parent_id) && !out.has(f.id)) { out.add(f.id); grew = true; } });
  }
  return out;
}
function expandTo(folderId) {
  let f = folderById(folderId);
  while (f) { state.expanded.add(f.id); f = folderById(f.parent_id); }
  saveExpanded();
}
function folderOptions(excludeIds = new Set()) {
  const opts = [{ value: '', label: '— Top level (no folder) —' }];
  const walk = (parent, depth) => {
    state.folders.filter((f) => (f.parent_id || '') === parent && !excludeIds.has(f.id))
      .forEach((f) => { opts.push({ value: f.id, label: `${'   '.repeat(depth)}📁 ${f.name}` }); walk(f.id, depth + 1); });
  };
  walk('', 0);
  return opts;
}
function renderCrumb() {
  const el = $('#crumb');
  if (!el) return;
  const path = state.current && state.current.folder_id ? folderPath(state.current.folder_id) : [];
  el.textContent = path.length ? path.join(' / ') + ' /' : '';
  el.title = path.length ? 'Folder: ' + path.join(' / ') : '';
}

// Tree model for the current (possibly filtered) request list.
function buildTree() {
  const known = new Set(state.folders.map((f) => f.id));
  const kids = new Map();
  state.folders.forEach((f) => {
    const p = f.parent_id && known.has(f.parent_id) ? f.parent_id : '';
    if (!kids.has(p)) kids.set(p, []);
    kids.get(p).push(f);
  });
  const reqs = new Map();
  state.requests.forEach((r) => {
    const p = r.folder_id && known.has(r.folder_id) ? r.folder_id : '';
    if (!reqs.has(p)) reqs.set(p, []);
    reqs.get(p).push(r);
  });
  const sub = new Map();
  const subtree = (fid) => {
    if (sub.has(fid)) return sub.get(fid);
    const list = [];
    (kids.get(fid) || []).forEach((f) => list.push(...subtree(f.id)));
    list.push(...(reqs.get(fid) || []));
    sub.set(fid, list);
    return list;
  };
  return { kids, reqs, subtree };
}

function requestRow(r, depth) {
  const li = document.createElement('li');
  li.className = 'req-item' +
    (state.current && state.current.id === r.id ? ' active' : '') +
    (isSelected(r.id) ? ' selected' : '') +
    (state.kbId === r.id ? ' kb-focus' : '');
  li.style.setProperty('--depth', depth);
  li.draggable = true;
  li.dataset.rid = r.id;
  li.dataset.folder = r.folder_id || '';
  const tags = r.tags.map((t) => `<span class="tag">#${esc(t)}</span>`).join('');
  li.innerHTML =
    `<input type="checkbox" ${isSelected(r.id) ? 'checked' : ''} title="Select for run">` +
    (typeof isWsUrl === 'function' && isWsUrl(r.url) ? '<span class="method m-WS" title="WebSocket">WS</span>' : `<span class="method m-${esc(r.method)}">${esc(r.method)}</span>`) +
    `<div style="min-width:0"><div class="name">${esc(r.name)}${sendCount(r) > 1 ? `<span class="bcount" title="Checked bodies">×${sendCount(r)}</span>` : ''}</div><div class="sub">${tags}${esc(r.url)}</div></div>` +
    '<button class="more" title="More actions" aria-label="More actions" aria-haspopup="menu">⋯</button>';
  $('.more', li).addEventListener('click', (e) => { e.stopPropagation(); openReqMenu(r, e.currentTarget); });
  li.addEventListener('contextmenu', (e) => { e.preventDefault(); openReqMenu(r, li, { x: e.clientX, y: e.clientY }); });
  $('input', li).addEventListener('click', (e) => { e.stopPropagation(); toggleSelect(r); });
  li.addEventListener('click', () => openRequest(r.id));
  return li;
}

function folderRow(f, depth, list, open) {
  const li = document.createElement('li');
  li.className = 'folder-item' + (open ? ' open' : '');
  li.style.setProperty('--depth', depth);
  li.draggable = true;
  li.dataset.fid = f.id;
  const nSel = list.filter((r) => isSelected(r.id)).length;
  li.innerHTML =
    `<input type="checkbox" title="Select every request in this folder" ${list.length ? '' : 'disabled'} ${list.length && nSel === list.length ? 'checked' : ''}>` +
    `<span class="fname"><span class="caret">▶</span><svg class="ficon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2h8.5A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/></svg><span class="t">${esc(f.name)}</span></span>` +
    `<span class="fcount">${list.length}</span>` +
    '<button class="more" title="Folder actions" aria-label="Folder actions" aria-haspopup="menu">⋯</button>';
  const cb = $('input', li);
  cb.indeterminate = nSel > 0 && nSel < list.length;
  cb.addEventListener('click', (e) => {
    e.stopPropagation();
    if (cb.checked) list.forEach((r) => { if (!isSelected(r.id)) state.selected.push(selEntry(r)); });
    else { const ids = new Set(list.map((r) => r.id)); state.selected = state.selected.filter((x) => !ids.has(x.id)); }
    renderList();
  });
  $('.more', li).addEventListener('click', (e) => { e.stopPropagation(); openFolderMenu(f, e.currentTarget); });
  li.addEventListener('contextmenu', (e) => { e.preventDefault(); openFolderMenu(f, li, { x: e.clientX, y: e.clientY }); });
  li.addEventListener('click', () => {
    if (state.expanded.has(f.id)) state.expanded.delete(f.id); else state.expanded.add(f.id);
    saveExpanded();
    renderList();
  });
  return li;
}

// The sidebar is patched in place: every row has a key (its folder or request id) and a signature
// of everything it shows. A row whose signature is unchanged keeps its DOM node, so renaming one
// request (or ticking one checkbox) touches one <li> instead of rebuilding hundreds.
function requestSig(r, depth) {
  return JSON.stringify([r.name, r.method, r.url, typeof isWsUrl === 'function' && isWsUrl(r.url), r.tags, r.folder_id || '', depth, sendCount(r),
    !!(state.current && state.current.id === r.id), isSelected(r.id), state.kbId === r.id]);
}
function folderSig(f, depth, list, open) {
  return JSON.stringify([f.name, f.parent_id || '', depth, open, list.map((r) => r.id + (isSelected(r.id) ? '+' : '')).join(',')]);
}
function renderList() {
  const ul = $('#req-list');
  const keepScroll = ul.scrollTop;   // autosave re-renders the list: it must not jump to the top
  const filtering = !!(state.q || state.tag);
  const tree = buildTree();
  state.treeOrder = [...(tree.kids.get('') || []).flatMap((f) => tree.subtree(f.id)), ...(tree.reqs.get('') || [])];
  const rows = [];   // what the list must show, in order: { key, sig, make }
  const walk = (fid, depth) => {
    (tree.kids.get(fid) || []).forEach((f) => {
      const list = tree.subtree(f.id);
      if (filtering && !list.length) return;
      const open = filtering || state.expanded.has(f.id);
      rows.push({ key: 'f:' + f.id, sig: folderSig(f, depth, list, open), make: () => folderRow(f, depth, list, open) });
      if (open) walk(f.id, depth + 1);
    });
    (tree.reqs.get(fid) || []).forEach((r) => rows.push({ key: 'r:' + r.id, sig: requestSig(r, depth), make: () => requestRow(r, depth) }));
  };
  walk('', 0);
  const old = new Map();
  Array.from(ul.children).forEach((li) => { if (li.dataset.key) old.set(li.dataset.key, li); else li.remove(); });
  let at = ul.firstElementChild;   // the node that should come next
  rows.forEach((row) => {
    let li = old.get(row.key);
    old.delete(row.key);
    if (li && li.dataset.sig !== row.sig) {   // same thing, different content: swap the node, keep its place and transient marks
      const fresh = row.make();
      if (li.classList.contains('sending')) fresh.classList.add('sending');
      if (li === at) at = li.nextElementSibling;
      li.remove();
      li = fresh;
    } else if (!li) li = row.make();
    li.dataset.key = row.key;
    li.dataset.sig = row.sig;
    if (li === at) at = at.nextElementSibling;
    else ul.insertBefore(li, at);
  });
  old.forEach((li) => li.remove());
  if (!rows.length) {
    ul.innerHTML = `<li class="empty small">${filtering ? 'No requests match.' : 'No requests yet. Click “+ New”, or create a folder first.'}</li>`;
  }
  ul.scrollTop = keepScroll;
  updateSelectionBar();
  renderCrumb();
  if (typeof renderReqTabs === 'function') renderReqTabs();
}

// --------------------------------------------------------- generic menu
function closeMenu() {
  const m = $('#ctx-menu');
  if (m) m.remove();
  $$('.menu-open').forEach((li) => li.classList.remove('menu-open'));
}
function openMenu(key, anchor, at, items) {
  const already = $('#ctx-menu') && $('#ctx-menu').dataset.key === key;
  closeMenu();
  if (already && !at) return; // a second click on ⋯ closes it
  const m = document.createElement('div');
  m.id = 'ctx-menu';
  m.className = 'menu';
  m.dataset.key = key;
  m.setAttribute('role', 'menu');
  items.forEach((it) => {
    if (it === '-') { m.insertAdjacentHTML('beforeend', '<div class="sep"></div>'); return; }
    const b = document.createElement('button');
    b.setAttribute('role', 'menuitem');
    b.dataset.act = it.act;
    if (it.danger) b.className = 'danger';
    b.innerHTML = `<span class="mi">${it.icon}</span>${esc(it.label)}`;
    b.addEventListener('click', () => { closeMenu(); it.run(); });
    m.appendChild(b);
  });
  document.body.appendChild(m);
  const row = anchor.closest('.req-item, .folder-item, .h-item, .body-item');
  if (row) row.classList.add('menu-open');
  const rect = anchor.getBoundingClientRect();
  let left = at ? at.x : rect.right - m.offsetWidth;
  let top = at ? at.y : rect.bottom + 4;
  if (top + m.offsetHeight > window.innerHeight - 8) top = (at ? at.y : rect.top - 4) - m.offsetHeight;
  left = Math.max(8, Math.min(left, window.innerWidth - m.offsetWidth - 8));
  m.style.left = left + 'px';
  m.style.top = Math.max(8, top) + 'px';
  $('button', m).focus();
  m.addEventListener('keydown', (e) => {
    const bs = $$('button', m);
    const i = bs.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); bs[(i + 1) % bs.length].focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); bs[(i - 1 + bs.length) % bs.length].focus(); }
  });
}
function openReqMenu(r, anchor, at) {
  openMenu('r:' + r.id, anchor, at, [
    { act: 'dup', icon: '⧉', label: 'Duplicate', run: () => duplicateRequestById(r.id) },
    { act: 'move', icon: '↪', label: 'Move to folder…', run: () => moveRequestDialog(r) },
    '-',
    { act: 'del', icon: '🗑', label: 'Delete', danger: true, run: () => deleteRequestById(r.id, r.name) },
  ]);
}
function openFolderMenu(f, anchor, at) {
  openMenu('f:' + f.id, anchor, at, [
    { act: 'add-req', icon: '＋', label: 'Add request', run: () => newRequest(f.id) },
    { act: 'add-folder', icon: '📁', label: 'Add folder', run: () => createFolder(f.id) },
    { act: 'run', icon: '▶', label: 'Run folder', run: () => runFolder(f) },
    '-',
    { act: 'settings', icon: '⚙', label: 'Settings (auth, headers, variables)…', run: () => openFolderSettings(f) },
    { act: 'export', icon: '🚀', label: 'Export as Postman collection', run: () => exportPostman(false, f.id) },
    { act: 'docs', icon: '📄', label: 'Export documentation (Markdown)', run: () => exportDocs(f.id) },
    { act: 'docs-html', icon: '🌐', label: 'Export documentation (HTML page)', run: () => exportDocs(f.id, 'html') },
    '-',
    { act: 'rename', icon: '✎', label: 'Rename', run: () => renameFolder(f) },
    { act: 'move', icon: '↪', label: 'Move to…', run: () => moveFolderDialog(f) },
    { act: 'del', icon: '🗑', label: 'Delete', danger: true, run: () => deleteFolder(f) },
  ]);
}

// ------------------------------------------------------- folder actions
async function createFolder(parentId = null) {
  const where = parentId ? ` inside ${strong(folderPath(parentId).join(' / '))}` : '';
  const name = await promptDialog('New folder', { label: 'Folder name', placeholder: 'e.g. QA, Prod, Users', okLabel: 'Create', message: where ? `The folder is created${where}.` : '' });
  if (!name) return;
  try {
    const f = await api('/folders', { method: 'POST', body: { name, parent_id: parentId } });
    if (parentId) expandTo(parentId);
    state.expanded.add(f.id);
    saveExpanded();
    await loadRequests();
    toast(`Folder “${f.name}” created`, 'ok');
  } catch (e) { toast(e.message, 'error'); }
}
async function renameFolder(f) {
  const name = await promptDialog('Rename folder', { label: 'Folder name', value: f.name, okLabel: 'Rename' });
  if (!name || name === f.name) return;
  try { await api('/folders/' + f.id, { method: 'PATCH', body: { name } }); await loadRequests(); }
  catch (e) { toast(e.message, 'error'); }
}
async function deleteFolder(f) {
  let c = { folders: 0, requests: 0 };
  try { c = await api(`/folders/${f.id}/contents`); } catch { /* counts are informational */ }
  const parts = [];
  if (c.requests) parts.push(`${c.requests} request${c.requests === 1 ? '' : 's'}`);
  if (c.folders) parts.push(`${c.folders} subfolder${c.folders === 1 ? '' : 's'}`);
  const msg = parts.length
    ? `This deletes ${strong(f.name)} and everything inside it: ${parts.join(' and ')}. This cannot be undone.`
    : `The empty folder ${strong(f.name)} will be removed.`;
  if (!await confirmDialog('Delete folder?', msg, { okLabel: 'Delete folder', danger: true })) return;
  const doomed = folderDescendants(f.id);
  if (state.current && doomed.has(state.current.folder_id)) {
    clearTimeout(autosave.timer); clearTimeout(autosave.retry); autosave.dirty = false;
    while (autosave.inFlight) await autosave.inFlight;
  }
  try {
    const out = await api('/folders/' + f.id, { method: 'DELETE' });
    const gone = new Set(out.requests);
    state.selected = state.selected.filter((x) => !gone.has(x.id));
    if (state.current && gone.has(state.current.id)) { state.current = null; renderEditor(); }
    toast(`Folder “${f.name}” deleted`);
    await loadRequests();
  } catch (e) { toast(e.message, 'error'); }
}
async function moveRequestTo(rid, folderId) {
  await saveNow();
  const r = await api(`/requests/${rid}/move`, { method: 'POST', body: { folder_id: folderId || null } });
  if (state.current && state.current.id === rid) state.current.folder_id = r.folder_id;
  if (folderId) expandTo(folderId);
  await loadRequests();
  return r;
}
async function moveFolderTo(fid, parentId) {
  await api('/folders/' + fid, { method: 'PATCH', body: { parent_id: parentId || null, move: true } });
  if (parentId) expandTo(parentId);
  await loadRequests();
}
async function moveRequestDialog(r) {
  const to = await selectDialog('Move request', { label: `Move “${r.name}” to`, options: folderOptions(), value: r.folder_id || '', okLabel: 'Move' });
  if (to === null) return;
  try { await moveRequestTo(r.id, to); toast('Moved', 'ok'); } catch (e) { toast(e.message, 'error'); }
}
async function moveFolderDialog(f) {
  const to = await selectDialog('Move folder', { label: `Move “${f.name}” into`, options: folderOptions(folderDescendants(f.id)), value: f.parent_id || '', okLabel: 'Move' });
  if (to === null) return;
  try { await moveFolderTo(f.id, to); toast('Moved', 'ok'); } catch (e) { toast(e.message, 'error'); }
}
function runFolder(f) {
  const list = buildTree().subtree(f.id);
  if (!list.length) { toast(`Folder “${f.name}” has no requests to run`, 'error'); return; }
  openRunModal({ items: list.map(selEntry), name: f.name, title: `Run folder “${f.name}”` });
}

// --------------------------------------------------------- drag and drop
function dropTargetFor(el) {
  const folderLi = el.closest('.folder-item');
  if (folderLi) return { folderId: folderLi.dataset.fid, el: folderLi };
  const reqLi = el.closest('.req-item');
  if (reqLi && reqLi.dataset.folder) {
    return { folderId: reqLi.dataset.folder, el: $(`.folder-item[data-fid="${reqLi.dataset.folder}"]`) };
  }
  return { folderId: '', el: null };
}
function canDrop(target) {
  const d = state.drag;
  if (!d) return false;
  if (d.type === 'folder') return !folderDescendants(d.id).has(target.folderId) && (folderById(d.id).parent_id || '') !== target.folderId;
  const r = state.requests.find((x) => x.id === d.id);
  return !!r && (r.folder_id || '') !== target.folderId;
}
function clearDropMarks() {
  $$('.drop-target').forEach((x) => x.classList.remove('drop-target'));
  $('#req-list').classList.remove('drop-root');
}
function bindDragAndDrop() {
  const ul = $('#req-list');
  ul.addEventListener('dragstart', (e) => {
    const li = e.target.closest('.req-item, .folder-item');
    if (!li) return;
    state.drag = li.dataset.rid ? { type: 'request', id: li.dataset.rid } : { type: 'folder', id: li.dataset.fid };
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', state.drag.id);
    li.classList.add('dragging');
    closeMenu();
  });
  ul.addEventListener('dragend', () => {
    state.drag = null;
    clearDropMarks();
    $$('.dragging').forEach((x) => x.classList.remove('dragging'));
  });
  ul.addEventListener('dragover', (e) => {
    if (!state.drag) return;
    const t = dropTargetFor(e.target);
    clearDropMarks();
    if (!canDrop(t)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (t.el) t.el.classList.add('drop-target'); else ul.classList.add('drop-root');
  });
  ul.addEventListener('dragleave', (e) => { if (!ul.contains(e.relatedTarget)) clearDropMarks(); });
  ul.addEventListener('drop', async (e) => {
    if (!state.drag) return;
    e.preventDefault();
    const t = dropTargetFor(e.target);
    const ok = canDrop(t);
    const d = state.drag;
    state.drag = null;
    clearDropMarks();
    if (!ok) return;
    try {
      if (d.type === 'request') await moveRequestTo(d.id, t.folderId);
      else await moveFolderTo(d.id, t.folderId);
      toast(t.folderId ? `Moved to “${folderPath(t.folderId).join(' / ')}”` : 'Moved to top level', 'ok');
    } catch (err) { toast(err.message, 'error'); }
  });
}

// =========================================================== environments
const ENV_PRESETS = [
  { name: 'Dev', base_url: 'https://dev.api.example.com' },
  { name: 'QA', base_url: 'https://qa.api.example.com' },
  { name: 'Staging', base_url: 'https://staging.api.example.com' },
  { name: 'Prod', base_url: 'https://api.example.com' },
];
async function loadEnvironments() {
  const data = await api('/environments');
  state.envs = data.environments;
  state.activeEnvId = data.active_id;
  renderEnvSelect();
  if (typeof wsRefresh === 'function') wsRefresh();   // an environment variable may hold a ws:// address
  return data;
}
// ------------------------------------------------------------- projects
// A project is a separate workspace (own database and output folder). Switching reopens the
// database on the server, so the page reloads to drop every in-memory bit of the old project.
let projectsInfo = { current: 'default', projects: [] };
async function loadProjects() {
  try { projectsInfo = await api('/projects'); } catch { return; }
  renderProjectButton();
}
function currentProject() {
  return projectsInfo.projects.find((p) => p.id === projectsInfo.current) || { id: projectsInfo.current, name: 'Default' };
}
function renderProjectButton() {
  const p = currentProject();
  $('#project-name').textContent = p.name;
  document.title = p.id === 'default' && projectsInfo.projects.length < 2 ? 'pyhttp' : `${p.name} · pyhttp`;
  $('#btn-project').classList.toggle('many', projectsInfo.projects.length > 1);
}
async function switchProject(id) {
  if (id === projectsInfo.current) return;
  await saveNow();
  try {
    await api(`/projects/${encodeURIComponent(id)}/activate`, { method: 'POST' });
  } catch (e) { toast(e.message, 'error'); return; }
  location.reload();
}
async function createProjectDialog() {
  const name = await promptDialog('New project', {
    label: 'Project name', placeholder: 'e.g. Shop API, Client X, Playground', okLabel: 'Create and open',
    message: 'A project is a separate workspace: its own requests, folders, environments, variables, history, cookies, mocks and monitors. Your app settings are copied into it.',
  });
  if (!name) return;
  await saveNow();
  try {
    await api('/projects', { method: 'POST', body: { name } });
  } catch (e) { toast(e.message, 'error'); return; }
  location.reload();
}
async function renameProjectDialog() {
  const p = currentProject();
  const name = await promptDialog('Rename project', { label: 'Project name', value: p.name, okLabel: 'Rename' });
  if (!name) return;
  try {
    projectsInfo = await api(`/projects/${encodeURIComponent(p.id)}`, { method: 'PATCH', body: { name } });
    renderProjectButton();
  } catch (e) { toast(e.message, 'error'); }
}
async function deleteProjectDialog(p) {
  const ok = await confirmDialog('Delete project', `Delete ${strong(p.name)} with all its requests, environments, history, run outputs, mocks and monitors? This cannot be undone.`, { okLabel: 'Delete project', danger: true });
  if (!ok) return;
  try {
    projectsInfo = await api(`/projects/${encodeURIComponent(p.id)}`, { method: 'DELETE' });
    renderProjectButton();
    toast(`Project “${p.name}” deleted`, 'ok');
  } catch (e) { toast(e.message, 'error'); }
}
function openProjectMenu(anchor) {
  const cur = projectsInfo.current;
  const items = projectsInfo.projects.map((p) => ({
    act: 'p:' + p.id, icon: p.id === cur ? '✓' : '', label: p.name, run: () => switchProject(p.id),
  }));
  const others = projectsInfo.projects.filter((p) => p.id !== cur);
  items.push('-', { act: 'new', icon: '＋', label: 'New project…', run: createProjectDialog });
  items.push({ act: 'rename', icon: '✎', label: `Rename “${currentProject().name}”…`, run: renameProjectDialog });
  if (others.length) {
    items.push('-');
    others.forEach((p) => items.push({ act: 'del:' + p.id, icon: '🗑', label: `Delete “${p.name}”…`, danger: true, run: () => deleteProjectDialog(p) }));
  }
  openMenu('projects', anchor, null, items);
}

function renderEnvSelect() {
  setTimeout(updateStatusbar, 0);
  const sel = $('#env-select');
  const active = state.envs.find((e) => e.id === state.activeEnvId);
  sel.innerHTML = '<option value="">No environment</option>' +
    state.envs.map((e) => `<option value="${e.id}">${esc(e.name)}</option>`).join('') +
    '<option disabled>──────────</option><option value="__new">＋ New environment…</option><option value="__manage">Manage environments…</option>';
  sel.value = active ? active.id : '';
  const picker = sel.closest('.env-picker');
  picker.classList.toggle('on', !!active);
  picker.classList.toggle('prod', !!active && /prod/i.test(active.name));
}
async function setActiveEnv(id) {
  try {
    const data = await api('/environments/active', { method: 'PUT', body: { id: id || null } });
    state.envs = data.environments;
    state.activeEnvId = data.active_id;
    renderEnvSelect();
    const env = state.envs.find((e) => e.id === id);
    toast(env ? `Environment: ${env.name}` : 'No environment', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

// Environment manager (modal). Edits save automatically.
const envSave = { timer: null, id: null };
async function flushEnvSave() {
  if (!envSave.timer) return;
  clearTimeout(envSave.timer);
  envSave.timer = null;
  await saveEnvEditor();
}
async function saveEnvEditor() {
  const id = envSave.id;
  const box = $('#env-editor');
  if (!id || !box) return;
  const name = $('#env-name', box).value.trim();
  const variables = readKv($('#kv-env', box));
  try {
    const env = await api('/environments/' + id, { method: 'PATCH', body: { name: name || undefined, variables } });
    const i = state.envs.findIndex((e) => e.id === id);
    if (i >= 0) state.envs[i] = env;
    renderEnvSelect();
    const label = $(`.env-item[data-id="${id}"] .en`);
    if (label) label.textContent = env.name;
    const st = $('#env-save-state');
    if (st) { st.className = 'save-state saved'; st.textContent = 'Saved'; }
  } catch (e) { toast('Could not save environment: ' + e.message, 'error'); }
}
function scheduleEnvSave() {
  const st = $('#env-save-state');
  if (st) { st.className = 'save-state saving'; st.textContent = 'Editing…'; }
  clearTimeout(envSave.timer);
  envSave.timer = setTimeout(() => { envSave.timer = null; saveEnvEditor(); }, 500);
}
async function openEnvManager(focusId = null, createNew = false) {
  await loadEnvironments();
  openModal('Environments', '<div id="env-manager" class="env-manager"></div>', [
    { label: 'Done', cls: 'primary', onClick: async () => { await flushEnvSave(); closeModal(); } },
  ]);
  envSave.id = focusId || state.activeEnvId || (state.envs[0] || {}).id || null;
  renderEnvManager();
  if (createNew) createEnvironment();
}
function renderEnvManager() {
  const root = $('#env-manager');
  if (!root) return;
  const existing = new Set(state.envs.map((e) => e.name.toLowerCase()));
  const quick = ENV_PRESETS.filter((p) => !existing.has(p.name.toLowerCase()));
  root.innerHTML = `
    <div class="env-list">
      ${state.envs.map((e) => `<div class="env-item ${e.id === envSave.id ? 'active-edit' : ''}" data-id="${e.id}"><span class="en">${esc(e.name)}</span>${e.id === state.activeEnvId ? '<span class="live">active</span>' : ''}</div>`).join('') || '<div class="muted small">No environments yet.</div>'}
      <button id="env-add" class="link" style="text-align:left">＋ New environment</button>
      ${quick.length ? `<div class="muted small" style="margin-top:8px">Quick add</div><div class="env-quick">${quick.map((p) => `<span class="chip" data-preset="${esc(p.name)}">＋ ${esc(p.name)}</span>`).join('')}</div>` : ''}
    </div>
    <div id="env-editor"></div>`;
  $$('.env-item', root).forEach((el) => el.addEventListener('click', async () => {
    await flushEnvSave();
    envSave.id = el.dataset.id;
    renderEnvManager();
  }));
  $('#env-add', root).addEventListener('click', () => createEnvironment());
  $$('[data-preset]', root).forEach((el) => el.addEventListener('click', () => {
    const p = ENV_PRESETS.find((x) => x.name === el.dataset.preset);
    createEnvironment(p.name, [{ key: 'base_url', value: p.base_url, enabled: true }]);
  }));
  renderEnvEditor();
}
function renderEnvEditor() {
  const box = $('#env-editor');
  const env = state.envs.find((e) => e.id === envSave.id);
  if (!env) {
    box.innerHTML = `<div class="empty"><p>Create an environment such as <strong>Dev</strong>, <strong>QA</strong> or <strong>Prod</strong>.</p>
      <p class="muted small">Each environment has its own variables. Use them as <code>{{base_url}}</code> and switch environments from the top bar.</p></div>`;
    return;
  }
  const isActive = env.id === state.activeEnvId;
  box.innerHTML = `
    <div class="env-edit-head">
      <input id="env-name" value="${esc(env.name)}" placeholder="Environment name" spellcheck="false">
      <button id="env-activate" class="${isActive ? '' : 'accent'}">${isActive ? '✓ Active' : 'Set active'}</button>
      <button id="env-dup" class="icon-btn" title="Duplicate environment" aria-label="Duplicate environment">⧉</button>
      <button id="env-del" class="icon-btn danger" title="Delete environment" aria-label="Delete environment">🗑</button>
    </div>
    <p class="muted small env-note">These values override global variables with the same name while this environment is active. Unchecked rows are ignored. 🔒 marks a secret: masked on screen and in saved history and run files. <span id="env-save-state" class="save-state saved">Saved</span></p>
    <div id="kv-env" class="kv"></div>`;
  $('#kv-env', box).dataset.secrets = '1';
  renderKv($('#kv-env', box), env.variables, true);
  box.addEventListener('input', scheduleEnvSave);
  box.addEventListener('change', scheduleEnvSave);
  box.addEventListener('click', (e) => { if (e.target.closest('.kv-del, .kv-secret')) scheduleEnvSave(); });
  $('#env-activate', box).addEventListener('click', async () => {
    await flushEnvSave();
    await setActiveEnv(isActive ? null : env.id);
    renderEnvManager();
  });
  $('#env-dup', box).addEventListener('click', async () => {
    await flushEnvSave();
    try {
      const copy = await api(`/environments/${env.id}/duplicate`, { method: 'POST' });
      await loadEnvironments();
      envSave.id = copy.id;
      renderEnvManager();
      $('#env-name').select();
    } catch (e) { toast(e.message, 'error'); }
  });
  $('#env-del', box).addEventListener('click', async () => {
    if (!await confirmDialog('Delete environment?', `${strong(env.name)} and its ${env.variables.length} variable(s) will be deleted.`, { okLabel: 'Delete', danger: true })) return;
    clearTimeout(envSave.timer); envSave.timer = null;
    try {
      await api('/environments/' + env.id, { method: 'DELETE' });
      await loadEnvironments();
      envSave.id = (state.envs[0] || {}).id || null;
      renderEnvManager();
    } catch (e) { toast(e.message, 'error'); }
  });
}
async function createEnvironment(name = null, variables = null) {
  await flushEnvSave();
  if (!name) {
    name = await promptDialog('New environment', { label: 'Name', placeholder: 'e.g. Dev, QA, Staging, Prod', okLabel: 'Create' });
    if (!name) return;
    variables = [{ key: 'base_url', value: '', enabled: true }];
  }
  try {
    const env = await api('/environments', { method: 'POST', body: { name, variables } });
    await loadEnvironments();
    envSave.id = env.id;
    if (!$('#env-manager') || $('#modal').hidden) await openEnvManager(env.id); else renderEnvManager();
    const firstVal = $('#kv-env .kv-val');
    if (firstVal) firstVal.focus();
  } catch (e) { toast(e.message, 'error'); }
}

// ================================================================ console
// Every request the backend sends (Send, multi-body Send, runs) is logged on the
// server; the console polls for new entries and shows full details per request.
const con = { open: false, lastSeq: 0, entries: [], timer: null, expanded: new Set(), q: '', errorsOnly: false };
const CON_MAX = 500;
const conIsError = (e) => e.status === 'error' || (e.status_code || 0) >= 400;
const codeClass = (c) => (c == null ? 'err' : c < 300 ? 'ok' : c < 400 ? 'warn' : 'err');
function fmtClock(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0');
}

async function pollConsole() {
  clearTimeout(con.timer);
  try {
    const d = await api('/console?after=' + con.lastSeq);
    if (d.last_seq < con.lastSeq) { con.entries = []; con.lastSeq = 0; } // backend restarted
    if (d.entries.length) {
      con.entries.push(...d.entries);
      if (con.entries.length > CON_MAX) con.entries.splice(0, con.entries.length - CON_MAX);
      con.lastSeq = d.last_seq;
      if (con.open) renderConsole();
      updateStatusbar();
    }
  } catch { /* backend busy or restarting; try again */ }
  con.timer = setTimeout(pollConsole, con.open ? 700 : 2500);
}
function updateStatusbar() {
  const n = con.entries.length;
  const errs = con.entries.filter(conIsError).length;
  $('#console-count').textContent = n ? String(n) : '';
  const ec = $('#console-errcount');
  ec.hidden = !errs;
  ec.textContent = errs ? `${errs} failed` : '';
  ec.title = errs ? `${errs} request(s) failed or returned 4xx/5xx` : '';
  const last = con.entries[n - 1];
  $('#sb-last').innerHTML = last
    ? `Last: ${esc(last.method)} <span class="${codeClass(last.status_code)}">${last.status_code ?? 'ERR'}</span> · ${fmtMs(last.duration_ms)} · ${esc(last.url)}`
    : '';
  const env = state.envs ? state.envs.find((e) => e.id === state.activeEnvId) : null;
  $('#sb-env').textContent = env ? `Environment: ${env.name}` : 'No environment';
}

function conMatches(e) {
  if (con.errorsOnly && !conIsError(e)) return false;
  if (!con.q) return true;
  const hay = `${e.method} ${e.url} ${e.status_code ?? 'error'} ${e.reason} ${e.request_name} ${e.body_name || ''} ${e.source} ${e.error || ''}`.toLowerCase();
  return con.q.toLowerCase().split(/\s+/).filter(Boolean).every((t) => hay.includes(t));
}
function kvTable(rows) {
  if (!rows || !rows.length) return '<div class="muted small" style="padding:2px 0 6px 14px">none</div>';
  return `<div class="c-kv">${rows.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${esc(v)}</span>`).join('')}</div>`;
}
function conBody(text, isJson, cut, seq, kind) {
  if (!text) return '<div class="muted small" style="padding:2px 0 6px 14px">empty</div>';
  if (kind) return `<div class="c-viewer" data-seq="${seq}" data-kind="${kind}"></div>`;
  let out = text;
  if (isJson !== false) { try { out = JSON.stringify(JSON.parse(text), null, 2); } catch { /* not JSON */ } }
  return `<pre class="mono">${esc(out)}${cut ? '\n… [truncated in console]' : ''}</pre>`;
}
function mountConsoleViewers(list) {
  $$('.c-viewer', list).forEach((el) => {
    const e = con.entries.find((x) => x.seq === Number(el.dataset.seq));
    if (!e) return;
    const r = el.dataset.kind === 'resp' ? e.response : null;
    const resp = r
      ? { body_text: r.body, is_json: r.is_json, content_type: r.content_type, truncated: r.body_truncated, size: r.size, is_binary: false }
      : { body_text: e.request.body, is_json: undefined, content_type: (e.request.headers.find(([k]) => k.toLowerCase() === 'content-type') || [])[1] || '', truncated: e.request.body_truncated, is_binary: false };
    mountViewer(el, resp, { wrap: true });
  });
}
function conDetail(e) {
  const r = e.response;
  const general = [
    ['Request URL', e.url], ['Method', e.method],
    ['Status', e.status_code != null ? `${e.status_code} ${e.reason}` : 'no response'],
    ['Duration', fmtMs(e.duration_ms)],
  ];
  if (r) {
    general.push(['Response size', fmtBytes(r.size)], ['Content type', r.content_type || '—'], ['HTTP version', r.http_version || '—']);
    if (r.redirects && r.redirects.length) general.push(['Redirected from', r.redirects.join(' → ')]);
  }
  general.push(['Environment', e.environment || 'none'], ['Source', e.source],
    ['Request', e.request_name + (e.body_name ? ` · body “${e.body_name}”` : '')], ['Sent at', new Date(e.ts * 1000).toLocaleString()]);
  return `<div class="c-detail">
    ${e.error ? `<div class="c-err">✕ ${esc(e.error)}</div>` : ''}
    <details open><summary>General</summary>${kvTable(general)}</details>
    <details open><summary>Request headers (${e.request.headers.length})</summary>${kvTable(e.request.headers)}</details>
    ${e.request.body ? `<details open><summary>Request body</summary>${conBody(e.request.body, undefined, e.request.body_truncated, e.seq, 'req')}</details>` : ''}
    ${r ? `<details open><summary>Response headers (${r.headers.length})</summary>${kvTable(r.headers)}</details>
    <details open><summary>Response body</summary>${conBody(r.body, r.is_json, r.body_truncated, e.seq, 'resp')}</details>` : ''}
  </div>`;
}
function renderConsole() {
  const list = $('#console-list');
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 30;
  const shown = con.entries.filter(conMatches);
  const errs = con.entries.filter(conIsError).length;
  $('#console-info').textContent = con.entries.length
    ? `${shown.length === con.entries.length ? '' : `${shown.length} of `}${con.entries.length} request(s)${errs ? `, ${errs} failed` : ''}`
    : '';
  $('#console-expand').textContent = shown.length && shown.every((e) => con.expanded.has(e.seq)) ? 'Collapse all' : 'Expand all';
  if (!shown.length) {
    list.innerHTML = `<div class="c-empty">${con.entries.length ? 'No entries match the filter.' : 'No requests yet. Everything you send, including runs, appears here.'}</div>`;
    return;
  }
  list.innerHTML = shown.map((e) => {
    const open = con.expanded.has(e.seq);
    const label = e.request_name + (e.body_name ? ` · ${e.body_name}` : '');
    return `<div class="c-row ${open ? 'open' : ''} ${conIsError(e) ? 'err' : ''}" data-seq="${e.seq}" title="${esc(label)} (${esc(e.source)})">
      <span class="caret">▶</span>
      <span class="ts">${fmtClock(e.ts)}</span>
      <span class="src">${esc(e.source)}</span>
      <span class="method m-${esc(e.method)}">${esc(e.method)}</span>
      <span class="code ${codeClass(e.status_code)}">${e.status_code ?? 'ERROR'}</span>
      <span class="url">${esc(e.url)}</span>
      <span class="dur">${fmtMs(e.duration_ms)}</span>
      <span class="size">${e.response ? fmtBytes(e.response.size) : '—'}</span>
    </div>${open ? conDetail(e) : ''}`;
  }).join('');
  mountConsoleViewers(list);
  if (atBottom) list.scrollTop = list.scrollHeight;
}
function setConsoleOpen(open) {
  con.open = open;
  $('#console').hidden = !open;
  $('#btn-console').classList.toggle('on', open);
  try { localStorage.setItem('pyhttp.console', open ? '1' : '0'); } catch { /* ignore */ }
  if (open) {
    renderConsole();
    const list = $('#console-list');
    list.scrollTop = list.scrollHeight;
    pollConsole();
  }
}
function bindConsole() {
  $('#btn-console').addEventListener('click', () => setConsoleOpen(!con.open));
  $('#console-close').addEventListener('click', () => setConsoleOpen(false));
  $('#console-list').addEventListener('click', (e) => {
    const row = e.target.closest('.c-row');
    if (!row) return;
    const seq = Number(row.dataset.seq);
    if (con.expanded.has(seq)) con.expanded.delete(seq); else con.expanded.add(seq);
    const list = $('#console-list');
    const keep = list.scrollTop;
    renderConsole();
    list.scrollTop = keep;
  });
  let ft = null;
  $('#console-filter').addEventListener('input', (e) => {
    clearTimeout(ft);
    ft = setTimeout(() => { con.q = e.target.value.trim(); renderConsole(); }, 120);
  });
  $('#console-errors').addEventListener('change', (e) => { con.errorsOnly = e.target.checked; renderConsole(); });
  $('#console-expand').addEventListener('click', () => {
    const shown = con.entries.filter(conMatches);
    const all = shown.length && shown.every((x) => con.expanded.has(x.seq));
    shown.forEach((x) => (all ? con.expanded.delete(x.seq) : con.expanded.add(x.seq)));
    renderConsole();
  });
  $('#console-clear').addEventListener('click', async () => {
    try { await api('/console', { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); return; }
    con.entries = [];
    con.expanded.clear();
    renderConsole();
    updateStatusbar();
  });
  // drag the top edge to resize
  const panel = $('#console');
  $('#console-resize').addEventListener('mousedown', (e) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = panel.getBoundingClientRect().height;
    panel.classList.add('resizing');
    const move = (ev) => {
      const h = Math.max(120, Math.min(window.innerHeight * 0.8, startH + (startY - ev.clientY)));
      panel.style.setProperty('--console-h', h + 'px');
    };
    const up = () => {
      panel.classList.remove('resizing');
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      try { localStorage.setItem('pyhttp.console.h', panel.style.getPropertyValue('--console-h')); } catch { /* ignore */ }
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
  try {
    const h = localStorage.getItem('pyhttp.console.h');
    if (h) panel.style.setProperty('--console-h', h);
    if (localStorage.getItem('pyhttp.console') === '1') setConsoleOpen(true);
  } catch { /* ignore */ }
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.altKey && e.key.toLowerCase() === 'c') { e.preventDefault(); setConsoleOpen(!con.open); }
  });
  pollConsole();
}

// ----------------------------------------------------------- copy response
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* fall back below */ }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  ta.remove();
  return ok;
}
function currentRespView() {
  const active = $('#resp-tabs button.active');
  return active ? active.dataset.tab : 'body';
}
function updateCopyButton() {
  const view = currentRespView();
  const label = { body: 'response body', headers: 'response headers', request: 'request as sent', cookies: 'cookies', tests: 'test results' }[view] || 'response';
  $('#r-copy').title = `Copy the ${label}`;
  $('#r-save').hidden = view !== 'body';
  $('#r-copy').hidden = view === 'view';   // a view is a page of its own: nothing here to copy
}
function responseTextFor(view) {
  const r = lastResult;
  if (!r) return '';
  const resp = r.response;
  if (view === 'body') return resp ? (resp.body_text || '') : (r.error || '');
  if (view === 'headers') return resp ? resp.headers.map(([k, v]) => `${k}: ${v}`).join('\n') : '';
  if (view === 'cookies') return resp ? (resp.cookies || []).map((c) => `${c.name}=${c.value}; Domain=${c.domain}; Path=${c.path}`).join('\n') : '';
  if (view === 'tests') return (r.tests || []).map((t) => `${t.passed ? 'PASS' : 'FAIL'}  ${t.name}${t.note ? '  (' + t.note + ')' : ''}`).join('\n');
  if (view === 'request') return formatRequest(r.request);
  return '';
}
async function copyResponse() {
  const view = currentRespView();
  const text = responseTextFor(view);
  const btn = $('#r-copy');
  if (!text) { toast('Nothing to copy', 'error'); return; }
  if (await copyText(text)) {
    btn.classList.add('done');
    $('span', btn).textContent = 'Copied ✓';
    clearTimeout(btn._t);
    btn._t = setTimeout(() => { btn.classList.remove('done'); $('span', btn).textContent = 'Copy'; }, 1500);
  } else toast('Could not copy to the clipboard', 'error');
}

// ============================================================== settings
const appSettings = { theme: 'dark', verify_ssl: true };
const darkQuery = window.matchMedia('(prefers-color-scheme: light)');
function applyTheme(theme) {
  const t = theme === 'system' ? (darkQuery.matches ? 'light' : 'dark') : theme;
  document.documentElement.setAttribute('data-theme', t);
  try { localStorage.setItem('pyhttp.theme', theme); } catch { /* ignore */ }
}
darkQuery.addEventListener('change', () => { if (appSettings.theme === 'system') applyTheme('system'); });
function renderSslWarning() {
  $('#sb-ssl').hidden = appSettings.verify_ssl;
  $('#sb-proxy').hidden = !(appSettings.proxy_url || '').trim();
}
function applyAppSettings() {
  applyTheme(appSettings.theme);
  renderSslWarning();
  applyLayout(appSettings.layout);
  applyFontSize(appSettings.editor_font_size);
  applyFonts(appSettings.ui_font, appSettings.mono_font);
}
async function loadSettings() {
  try {
    Object.assign(appSettings, await api('/settings'));
    applyAppSettings();
  } catch { /* keep defaults */ }
}
async function saveSettings(patch) {
  try {
    Object.assign(appSettings, await api('/settings', { method: 'PUT', body: patch }));
    applyAppSettings();
  } catch (e) { toast('Could not save settings: ' + e.message, 'error'); }
}
function openSettings(tab) {   // tab: the one to show ('git', 'network'…); also used as a click handler
  const themes = [['dark', '☾', 'Dark'], ['light', '☀', 'Light'], ['system', '◐', 'System']];
  const s = appSettings;
  const sw = (id, label, desc, on) => `<label class="switch-row"><span class="txt"><b>${label}</b><span class="muted small">${desc}</span></span><span class="switch"><input type="checkbox" id="${id}" ${on ? 'checked' : ''}><span></span></span></label>`;
  // a labelled field: the label above the control, an optional hint below (keeps every tab on one grid)
  const fld = (label, control, hint = '', cls = '') => `<div class="set-field ${cls}"><span class="set-label">${label}</span>${control}${hint ? `<span class="set-hint muted small">${hint}</span>` : ''}</div>`;
  openModal('Settings', `
    <div class="set-tabs tabs small" id="set-tabs">
      <button data-tab="general" class="active">General</button><button data-tab="network" >Network</button><button data-tab="certs">Certificates</button><button data-tab="git">Git</button><button data-tab="ai">AI (MCP)</button><button data-tab="about">About</button>
    </div>
    <div class="tab-panels" id="set-panels">
    <div class="tab-panel" data-panel="general">
      <div class="set-section">
        <h4>Appearance</h4>
        <div class="set-grid">
          ${fld('Theme', `<div class="seg" id="set-theme" role="radiogroup" aria-label="Theme">${themes.map(([v, icon, label]) => `<button role="radio" data-theme="${v}" class="${s.theme === v ? 'on' : ''}" aria-checked="${s.theme === v}"><span>${icon}</span>${label}</button>`).join('')}</div>`, '', 'wide')}
          ${fld('Layout', `<select id="set-layout"><option value="below" ${s.layout === 'below' ? 'selected' : ''}>Response below the request</option><option value="beside" ${s.layout === 'beside' ? 'selected' : ''}>Response beside the request</option></select>`, 'Also switchable from the status bar.')}
          ${fld('Font', `<select id="set-uifont">${UI_FONTS.map(([v, label, stack]) => `<option value="${v}" style="font-family:${esc(stack)}" ${(s.ui_font || 'inter') === v ? 'selected' : ''}>${label}</option>`).join('')}</select>`, 'For menus, labels and the sidebar.')}
          ${fld('Code font', `<select id="set-monofont">${MONO_FONTS.map(([v, label, stack]) => `<option value="${v}" style="font-family:${esc(stack)}" ${(s.mono_font || 'default') === v ? 'selected' : ''}>${label}</option>`).join('')}</select>`, 'For URLs, bodies and responses. A font that is not installed falls back to the default.')}
          ${fld('Editor font size', `<span class="set-unit"><input type="number" id="set-font" min="10" max="24" value="${s.editor_font_size}"><span class="muted small">px</span></span>`, 'For bodies, scripts and the response viewer.')}
        </div>
      </div>
      <div class="set-section">
        <h4>Requests</h4>
        <div class="switch-list">
          ${sw('set-ssl', 'Verify SSL certificates', 'Applies to every Send and run. Turn off only for local or test servers with self-signed or invalid certificates.', s.verify_ssl)}
          <div id="set-ssl-warn" class="ssl-warn" ${s.verify_ssl ? 'hidden' : ''}>⚠ Certificates are not checked. Connections can be intercepted without warning.</div>
          ${sw('set-redirects', 'Follow redirects', 'Default for every request; a request can override it in its Settings tab.', s.follow_redirects)}
          ${sw('set-cookies', 'Send and store cookies', 'Keep a cookie jar like a browser. Each request can opt out in its Settings tab.', s.send_cookies)}
          ${sw('set-keyring', 'Keep 🔒 values in the system keychain', `The values of variables marked secret (environments and folders) are stored in ${s.keyring_name ? esc(s.keyring_name) : 'the operating system\'s credential store'} instead of this project\'s database file, which then holds no secrets. They stay on this computer: on another one, or after copying the data folder, type them in again.`, s.secrets_keyring)}
          ${sw('set-parallel', 'Send in parallel', 'Send ×N (several bodies) and runs send everything at once instead of one after another. Fastest, but no order and no {{prev.…}}. The run dialog can override this per run.', s.parallel)}
        </div>
        <div class="set-grid">
          ${fld('Max redirects', `<input type="number" id="set-maxredir" min="0" max="50" value="${s.max_redirects}">`, 'Stops a redirect loop.')}
          ${fld('Default timeout', `<span class="set-unit"><input type="number" id="set-timeout" min="1" max="3600" value="${s.default_timeout}"><span class="muted small">seconds</span></span>`, 'A request can set its own.')}
          ${fld('History size', `<input type="number" id="set-history" min="50" max="5000" value="${s.max_history}">`, 'Sends kept in Request history.')}
          ${fld('Parallel sends at once', `<input type="number" id="set-concurrency" min="1" max="64" value="${s.concurrency || 8}" ${s.parallel ? '' : 'disabled'}>`, '<span id="set-concurrency-hint"></span>')}
          ${fld('User-Agent', `<input id="set-ua" class="mono" value="${esc(s.user_agent || '')}" placeholder="pyhttp/…" spellcheck="false">`, '<span id="set-ua-hint">Sent with every request that has no User-Agent header of its own; leave it empty for the default.</span>', 'wide')}
        </div>
      </div>
    </div>
    <div class="tab-panel" data-panel="network" hidden>
      <div class="set-section">
        <h4>Proxy</h4>
        <div class="set-grid">
          ${fld('Proxy URL', `<input id="set-proxy" class="mono" value="${esc(s.proxy_url || '')}" placeholder="http://user:pass@proxy.local:3128" spellcheck="false">`, 'Used for every request whose host is not in the bypass list. Empty means no proxy.', 'wide')}
          ${fld('Bypass for', `<input id="set-proxy-bypass" class="mono" value="${esc(s.proxy_bypass || '')}" placeholder="localhost, 127.0.0.1, *.internal" spellcheck="false">`, 'Comma-separated hosts. They match exactly or as a suffix: <code>example.com</code> also matches <code>api.example.com</code>.', 'wide')}
        </div>
      </div>
    </div>
    <div class="tab-panel" data-panel="certs" hidden>
      <div class="set-section">
        <h4>Client certificates</h4>
        <p class="muted small set-intro">For servers that require mutual TLS. Give the host (or <code>*.example.com</code>), the PEM certificate file and, if the key is separate, the PEM key file.</p>
        <div class="cert-head muted small"><span>Host</span><span>Certificate file (PEM)</span><span>Key file (optional)</span><span></span></div>
        <div id="set-certs"></div>
        <button class="link" id="set-cert-add">+ Add certificate</button>
      </div>
    </div>
    <div class="tab-panel" data-panel="git" hidden><div id="set-git"></div></div>
    <div class="tab-panel" data-panel="ai" hidden>
      <div class="set-section">
        <h4>MCP server</h4>
        <p class="small">pyhttp ships an <a href="https://modelcontextprotocol.io" target="_blank" rel="noopener">MCP</a> server, so AI tools
          (Claude Code, Claude Desktop, Cursor…) can manage this collection: list, create and edit requests, send them,
          run folders or tags like the collection runner, read history, switch environments and set variables.</p>
        <p class="small">It runs on this app's data folder — keep pyhttp open or closed, both work. The AI tool starts
          and stops the server by itself; there is nothing to keep running here.</p>
      </div>
      <div class="set-section">
        <h4>Claude Code</h4>
        <p class="muted small">Run this once in a terminal:</p>
        <div class="set-code"><pre id="mcp-cmd-code" class="mono">loading…</pre><button class="copy-btn" data-copy="mcp-cmd-code"><span>Copy</span></button></div>
      </div>
      <div class="set-section">
        <h4>Claude Desktop / Cursor / other MCP clients</h4>
        <p class="muted small">Add this to the client's MCP configuration (for Claude Desktop: <code>claude_desktop_config.json</code>):</p>
        <div class="set-code"><pre id="mcp-cmd-json" class="mono">loading…</pre><button class="copy-btn" data-copy="mcp-cmd-json"><span>Copy</span></button></div>
      </div>
      <div class="set-section">
        <h4>Try it</h4>
        <p class="muted small">Then ask the AI things like “list my pyhttp requests”, “create a POST request to … with this body and a 200 test”,
          “send Login and show the response”, or “run the Smoke folder and summarise the failures”.</p>
      </div>
    </div>
    <div class="tab-panel" data-panel="about" hidden>
      <div class="set-section">
        <h4>Updates</h4>
        <div class="switch-list">
          ${sw('set-update', 'Check for updates at start', 'Looks up the latest GitHub release once per hour and shows an Update button when a newer version exists.', s.update_check)}
        </div>
        <div class="set-grid">
          ${fld('GitHub repository', `<input id="set-repo" class="mono" value="${esc(s.update_repo || '')}" placeholder="owner/repo" spellcheck="false">`, 'Where releases are published. Packaged builds have this filled in.', 'wide')}
        </div>
        <div class="set-actions"><button id="set-update-now">Check now</button><span id="set-update-status" class="small"></span></div>
        <div class="muted small" id="set-version"></div>
      </div>
      <div class="set-section">
        <h4>Data</h4>
        <p class="muted small" id="set-datadir"></p>
      </div>
      <div class="set-section">
        <h4>License</h4>
        <p class="small"><strong>pyhttp Dev Team</strong> · <em>From the unknown to the unowned</em></p>
        <p class="small">Copyright © 2026 pyhttp Dev Team. Free for everyone under the pyhttp Free License 1.0 (LICENSE.md):
          use it at home or at work, at a company of any size, for any purpose, with no fees and no registration.
          Questions: <a href="mailto:dev@pyhttp.com">dev@pyhttp.com</a>.</p>
        <p class="muted small">Built with FastAPI, Starlette, Pydantic, Uvicorn, httpx, anyio, h11, idna, certifi, click and pywebview (MIT / BSD / PSF / MPL-2.0 licensed). See THIRD-PARTY-NOTICES.md.</p>
      </div>
    </div>
    </div>
  `, [{ label: 'Done', cls: 'primary', onClick: closeModal }]);
  bindTabs($('#set-tabs'), $('#set-panels'));
  renderGitPanel($('#set-git'));
  if (typeof tab === 'string') showTab($('#set-tabs'), tab);
  $$('#set-theme button').forEach((b) => b.addEventListener('click', async () => {
    $$('#set-theme button').forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-checked', x === b); });
    await saveSettings({ theme: b.dataset.theme });
  }));
  $('#set-ssl').addEventListener('change', async (e) => {
    $('#set-ssl-warn').hidden = e.target.checked;
    await saveSettings({ verify_ssl: e.target.checked });
    toast(e.target.checked ? 'SSL verification on' : 'SSL verification off', e.target.checked ? 'ok' : 'error');
  });
  $('#set-redirects').addEventListener('change', (e) => saveSettings({ follow_redirects: e.target.checked }));
  $('#set-cookies').addEventListener('change', (e) => saveSettings({ send_cookies: e.target.checked }));
  $('#set-keyring').addEventListener('change', async (e) => {
    const box = e.target;
    box.disabled = true;
    try {
      Object.assign(appSettings, await api('/settings', { method: 'PUT', body: { secrets_keyring: box.checked } }));
      toast(box.checked ? 'Secret values moved to the system keychain' : 'Secret values moved back into the project database', 'ok');
    } catch (err) {
      box.checked = !box.checked;   // nothing was moved
      toast(err.message, 'error');
    } finally { box.disabled = false; }
  });
  const concurrencyHint = () => {
    const on = $('#set-parallel').checked;
    $('#set-concurrency').disabled = !on;
    $('#set-concurrency-hint').textContent = on ? 'Upper limit for Send ×N and parallel runs.' : 'Turn on “Send in parallel” above to use this.';
  };
  $('#set-parallel').addEventListener('change', (e) => { saveSettings({ parallel: e.target.checked }); concurrencyHint(); if (typeof renderSendButton === 'function' && state.current) renderSendButton(); });
  concurrencyHint();
  $('#set-update').addEventListener('change', (e) => saveSettings({ update_check: e.target.checked }));
  $('#set-layout').addEventListener('change', (e) => saveSettings({ layout: e.target.value }));
  $('#set-uifont').addEventListener('change', (e) => saveSettings({ ui_font: e.target.value }));
  $('#set-monofont').addEventListener('change', (e) => saveSettings({ mono_font: e.target.value }));
  const num = (id, key, min, max) => $('#' + id).addEventListener('change', (e) => { const v = Math.min(max, Math.max(min, Number(e.target.value) || min)); e.target.value = v; saveSettings({ [key]: v }); });
  num('set-font', 'editor_font_size', 10, 24);
  num('set-maxredir', 'max_redirects', 0, 50);
  num('set-timeout', 'default_timeout', 1, 3600);
  num('set-history', 'max_history', 50, 5000);
  num('set-concurrency', 'concurrency', 1, 64);
  const txt = (id, key) => $('#' + id).addEventListener('change', (e) => saveSettings({ [key]: e.target.value.trim() }));
  txt('set-ua', 'user_agent'); txt('set-proxy', 'proxy_url'); txt('set-proxy-bypass', 'proxy_bypass'); txt('set-repo', 'update_repo');
  const certs = (s.certs || []).map((c) => ({ ...c }));
  const drawCerts = () => {
    const box = $('#set-certs');
    box.innerHTML = certs.length ? certs.map((c, i) => `<div class="cert-row"><input class="mono" data-i="${i}" data-k="host" value="${esc(c.host || '')}" placeholder="api.example.com"><input class="mono" data-i="${i}" data-k="cert" value="${esc(c.cert || '')}" placeholder="C:\\certs\\client.pem"><input class="mono" data-i="${i}" data-k="key" value="${esc(c.key || '')}" placeholder="key file (optional)"><button class="kv-del" data-del="${i}" title="Remove" aria-label="Remove">✕</button></div>`).join('') : '<div class="muted small">No client certificates.</div>';
    $$('input', box).forEach((inp) => inp.addEventListener('change', () => { certs[inp.dataset.i][inp.dataset.k] = inp.value.trim(); saveSettings({ certs }); }));
    $$('[data-del]', box).forEach((b) => b.addEventListener('click', () => { certs.splice(Number(b.dataset.del), 1); saveSettings({ certs }); drawCerts(); }));
  };
  drawCerts();
  $('#set-cert-add').addEventListener('click', () => { certs.push({ host: '', cert: '', key: '' }); drawCerts(); $('#set-certs input').focus(); });
  $('#set-update-now').addEventListener('click', (e) => checkForUpdate(true, $('#set-update-status'), e.currentTarget));
  $$('#set-panels .set-code .copy-btn').forEach((b) => b.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('#' + b.dataset.copy).textContent);
      b.classList.add('done');
      $('span', b).textContent = 'Copied';
      setTimeout(() => { b.classList.remove('done'); $('span', b).textContent = 'Copy'; }, 1600);
    } catch { toast('Could not copy; select the text instead', 'error'); }
  }));
  api('/meta').then((m) => {
    const kinds = { installer: 'Windows installer, updates install themselves', portable: 'portable .exe, updates install themselves', brew: 'installed with Homebrew, updates install themselves', app: 'macOS app, updates via Homebrew or the .dmg', source: 'running from source', deb: 'Ubuntu/Debian package, updates via the .deb', other: 'packaged build' };
    const how = m.install && kinds[m.install.kind] ? ' · ' + kinds[m.install.kind] : '';
    $('#set-version').textContent = `pyhttp ${m.version}${how}${m.github_repo ? ' · ' + m.github_repo : ' · no GitHub repository configured for update checks'}`;
    $('#set-datadir').textContent = `Database and run outputs: ${m.data_dir}`;
    // MCP setup commands, built from this very instance (same interpreter, same data folder)
    const q = (p) => (/[\s"']/.test(p) ? `"${p}"` : p);
    const argv = m.frozen ? [m.python, 'mcp', '--data-dir', m.data_dir]
      : [m.python, '-m', 'pyhttp', 'mcp', '--data-dir', m.data_dir];
    $('#mcp-cmd-code').textContent = 'claude mcp add pyhttp -- ' + argv.map(q).join(' ');
    $('#mcp-cmd-json').textContent = JSON.stringify(
      { mcpServers: { pyhttp: { command: argv[0], args: argv.slice(1) } } }, null, 2);
    if (m.user_agent) { $('#set-ua').placeholder = m.user_agent; $('#set-ua-hint').textContent = `Sent with every request that has no User-Agent header of its own. Empty means the default: ${m.user_agent}`; }
  }).catch(() => {});
}

// --------------------------------------------------------------------- init
function init() {
  bindTabs($('#editor-tabs'), $('#editor-panels'));
  bindTabs($('#resp-tabs'), $('#resp-panels'));
  bindTabs($('#runs-tabs'), $('#runs-panels'));

  let searchTimer = null;
  $('#search').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = e.target.value; loadRequests(); }, 150);
  });
  $('#btn-new').addEventListener('click', () => newRequest());
  $('#btn-new-folder').addEventListener('click', () => createFolder());
  bindDragAndDrop();
  $('#env-select').addEventListener('change', (e) => {
    const v = e.target.value;
    if (v === '__new') { renderEnvSelect(); openEnvManager(null, true); return; }
    if (v === '__manage') { renderEnvSelect(); openEnvManager(); return; }
    setActiveEnv(v);
  });
  $('#btn-envs').addEventListener('click', () => openEnvManager());
  $('#btn-project').addEventListener('click', (e) => openProjectMenu(e.currentTarget));
  $('#select-all').addEventListener('change', (e) => {
    if (e.target.checked) {
      state.treeOrder.forEach((r) => { if (!isSelected(r.id)) state.selected.push(selEntry(r)); });
    } else {
      const shown = new Set(state.requests.map((r) => r.id));
      state.selected = state.selected.filter((s) => !shown.has(s.id));
    }
    renderList();
  });
  $('#btn-clear-sel').addEventListener('click', () => { state.selected = []; renderList(); });
  $('#btn-run').addEventListener('click', () => openRunModal());
  $('#btn-run-now').addEventListener('click', runSelectedNow);

  // autosave: any typing, pasting or toggling inside the editor
  const editorForm = $('#editor-form');
  const onEdit = (e) => { if (e.target.id !== 'body-search') scheduleSave(); };
  editorForm.addEventListener('input', onEdit);
  editorForm.addEventListener('change', onEdit);
  editorForm.addEventListener('click', (e) => { if (e.target.closest('.kv-del')) scheduleSave(); });
  window.addEventListener('beforeunload', saveOnExit);
  window.addEventListener('pagehide', saveOnExit);
  window.addEventListener('pagehide', commitPendingDeletes);
  $('#f-tags').addEventListener('input', renderMetaTags);
  $('#f-description').addEventListener('input', renderMetaTags);
  $('#btn-details').addEventListener('click', () => toggleDetails());
  try { if (localStorage.getItem('pyhttp.details') === '1') $('#details').hidden = false; } catch { /* ignore */ }
  $('#f-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); $('#f-url').focus(); } });
  $('#f-method').addEventListener('change', tintMethodSelect);
  $('#btn-send').addEventListener('click', sendOrCancel);
  $('#btn-send-mode').addEventListener('click', (e) => openSendModeMenu(e.currentTarget));
  $('#f-dep').addEventListener('change', refreshDependencyUi);
  $('#editor-tabs button[data-tab="settings"]').addEventListener('click', () => refreshDependencyUi());
  $('#f-dep-ttl').addEventListener('change', refreshDependencyUi);
  $('#f-dep-unit').addEventListener('change', refreshDependencyUi);
  $('#btn-format-json').addEventListener('click', beautifyBody);
  $('#f-body').addEventListener('input', () => {
    if ($('#f-body-type').value === 'none' && $('#f-body').value.trim()) $('#f-body-type').value = 'json';
    flushBody();
    renderBodies();
  });
  $('#body-name').addEventListener('input', () => { flushBody(); renderBodies(); });
  $('#f-body-type').addEventListener('change', () => {
    flushBody();
    const b = activeBody();
    if (b) showBodyEditor(b);
    updateBodyHints();
    updateTabCounts();
  });
  let bodySearchTimer = null;
  $('#body-search').addEventListener('input', (e) => {
    clearTimeout(bodySearchTimer);
    bodySearchTimer = setTimeout(() => { flushBody(); state.bodyQ = e.target.value.trim(); renderBodies(); }, 120);
  });
  $('#btn-body-add').addEventListener('click', () => addBody());
  $('#btn-body-dup').addEventListener('click', () => { flushBody(); if (activeBody()) addBody(activeBody()); });
  $('#btn-body-del').addEventListener('click', () => deleteBody());
  $('#btn-body-all').addEventListener('click', () => setAllBodies(true));
  $('#btn-body-none').addEventListener('click', () => setAllBodies(false));

  $('#btn-variables').addEventListener('click', openVariables);
  $('#btn-export').addEventListener('click', (e) => exportMenu(e.currentTarget));
  $('#btn-import').addEventListener('click', importClicked);
  $('#import-file').addEventListener('change', (e) => {
    if (e.target.files[0]) importFile(e.target.files[0]);
    e.target.value = '';
  });

  document.addEventListener('mousedown', (e) => { if (!e.target.closest('#ctx-menu, .more, #btn-export, #btn-send-mode, #btn-project')) closeMenu(); });
  $('#req-list').addEventListener('scroll', closeMenu);
  window.addEventListener('resize', closeMenu);
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal').addEventListener('click', (e) => { if (e.target === $('#modal')) closeModal(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('#ctx-menu')) { closeMenu(); return; }
    if (e.key === 'Escape' && !$('#modal').hidden) closeModal();
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && state.current) {
      e.preventDefault();
      // Shift: only the body being viewed, like its ➤ button
      if (e.shiftKey && activeBody() && $('#f-body-type').value !== 'none') sendOneBody(state.activeBodyId); else sendCurrent();
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's' && state.current) { e.preventDefault(); autosave.dirty = true; saveNow().then(() => toast('Saved', 'ok')); }
    // list navigation, only while not typing and nothing modal is open
    const typing = e.target.closest && e.target.closest('input, textarea, select, button, [contenteditable], .cm-editor');
    if (typing || e.ctrlKey || e.metaKey || e.altKey || !$('#modal').hidden || $('.dialog-backdrop')) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); kbMove(1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); kbMove(-1); return; }
    if (!state.kbId) return;
    const r = state.requests.find((x) => x.id === state.kbId);
    if (e.key === 'Enter') { e.preventDefault(); openRequest(state.kbId); }
    else if (e.key === ' ' && r) { e.preventDefault(); toggleSelect(r); }
    else if (e.key === 'Delete' && r) { e.preventDefault(); deleteRequestById(r.id, r.name); }
    else if (e.key === 'Escape') { state.kbId = null; renderList(); }
  });

  api('/meta').then((m) => { const el = $('#meta-info'); el.textContent = `v${m.version}`; el.title = 'Run outputs are saved in ' + m.output_dir; }).catch(() => {});
  bindConsole();
  loadSettings();
  $('#r-copy').addEventListener('click', copyResponse);
  $$('#resp-tabs button').forEach((b) => b.addEventListener('click', updateCopyButton));
  $('#btn-settings').addEventListener('click', openSettings);
  $('#sb-ssl').addEventListener('click', openSettings);
  $('#link-settings').addEventListener('click', openSettings);
  loadExpanded();
  loadProjects();
  loadRequests();
  loadEnvironments();
  loadHistory();
}
document.addEventListener('DOMContentLoaded', init);
function markNative() { if (window.pywebview) document.body.classList.add('native'); }
window.addEventListener('pywebviewready', markNative);
document.addEventListener('DOMContentLoaded', markNative);
