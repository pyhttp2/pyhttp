'use strict';
// Postman-style features layered on app.js: auth, tests/extract/pre-request variables,
// body modes (form, multipart, binary, GraphQL), response panels, request tabs,
// history, cookies, code snippets, import/export (Postman, cURL), settings,
// drag-to-reorder, shortcuts, Markdown docs and the update check.

// ================================================================= auth tab
const AUTH_LABELS = { inherit: 'Inherit from folder', none: 'No auth', basic: 'Basic auth', bearer: 'Bearer token', apikey: 'API key', digest: 'Digest auth', oauth2: 'OAuth 2.0' };
// every auth field: input id is 'auth-' + key (add_to uses 'auth-addto')
const AUTH_KEYS = ['username', 'password', 'token', 'prefix', 'key', 'value', 'add_to', 'grant', 'token_url', 'client_id', 'client_secret', 'scope', 'audience', 'client_auth'];
const authInputId = (k) => 'auth-' + (k === 'add_to' ? 'addto' : k);
// the auth fields shown in `box`; fields of other types keep their previous values from `prev`
function readAuthFields(box, type, prev) {
  const out = { type };
  AUTH_KEYS.forEach((k) => {
    const el = $('#' + authInputId(k), box);
    if (el) out[k] = el.value;
    else if (prev && prev[k] !== undefined) out[k] = prev[k];
  });
  return out;
}
let inheritedInfo = null; // db.inherited() for the current request's folder

function authFields(a, inheritedNote) {
  const t = a.type || 'inherit';
  const f = (label, id, value, ph = '', type = 'text') => `<label class="field"><span class="fl">${label}</span><input id="${id}" class="grow mono" type="${type}" value="${esc(value || '')}" placeholder="${esc(ph)}" spellcheck="false" autocomplete="off"></label>`;
  if (t === 'basic' || t === 'digest') return f('Username', 'auth-username', a.username, '{{user}}') + f('Password', 'auth-password', a.password, '', 'password') + '<p class="muted small">Sent as an <code>Authorization</code> header' + (t === 'digest' ? ' after the server\'s challenge (Digest)' : ' (Basic, base64)') + '. Variables work in both fields.</p>';
  if (t === 'bearer') return f('Token', 'auth-token', a.token, '{{token}}') + f('Prefix', 'auth-prefix', a.prefix === undefined ? 'Bearer' : a.prefix, 'Bearer') + '<p class="muted small">Sent as <code>Authorization: &lt;prefix&gt; &lt;token&gt;</code>. Clear the prefix to send the bare token.</p>';
  if (t === 'apikey') return f('Key', 'auth-key', a.key, 'X-API-Key') + f('Value', 'auth-value', a.value, '{{api_key}}') + `<label class="field"><span class="fl">Add to</span><select id="auth-addto"><option value="header" ${a.add_to !== 'query' ? 'selected' : ''}>Header</option><option value="query" ${a.add_to === 'query' ? 'selected' : ''}>Query params</option></select></label>`;
  if (t === 'oauth2') {
    const pw = a.grant === 'password';
    const sel = (label, id, value, opts) => `<label class="field"><span class="fl">${label}</span><select id="${id}">${opts.map(([v, l]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>`;
    return sel('Grant type', 'auth-grant', a.grant || 'client_credentials', [['client_credentials', 'Client credentials'], ['password', 'Password (resource owner)']]) +
      f('Token URL', 'auth-token_url', a.token_url, 'https://auth.example.com/oauth/token') +
      f('Client ID', 'auth-client_id', a.client_id, '{{client_id}}') +
      f('Client secret', 'auth-client_secret', a.client_secret, '{{client_secret}}', 'password') +
      `<div class="oa-pw" ${pw ? '' : 'hidden'}>` + f('Username', 'auth-username', a.username, '{{username}}') + f('Password', 'auth-password', a.password, '', 'password') + '</div>' +
      f('Scope', 'auth-scope', a.scope, 'read write') +
      f('Audience', 'auth-audience', a.audience, 'optional') +
      sel('Client auth', 'auth-client_auth', a.client_auth || 'basic', [['basic', 'Basic auth header'], ['body', 'In the request body']]) +
      f('Header prefix', 'auth-prefix', a.prefix === undefined ? 'Bearer' : a.prefix, 'Bearer') +
      `<div class="oa-token"><span class="oa-status muted small">Token: checking…</span><span class="grow"></span>
        <button type="button" class="oa-get small primary">Get new token</button><button type="button" class="oa-clear small link">Clear</button></div>
      <p class="muted small">A token is fetched on the first send and reused until it expires (a refresh token is used when the server gives one). After a <code>401</code> a new token is fetched once and the request retried. Variables work in every field; mark secrets as <em>secret</em> in the environment so they are masked.</p>`;
  }
  if (t === 'none') return '<p class="muted small">No authorization is added. Folder auth is not inherited.</p>';
  return `<p class="muted small">${inheritedNote}</p>`;
}

// ---- variable dropdown for auth fields: focus the Token (or any auth field) and pick one
// of the active environment's, the folder's or the global variables; it is inserted as {{name}}.
const varSuggest = { el: null, input: null, items: [], cur: 0, globals: null, globalsAt: 0 };
async function suggestVariables() {
  if (!varSuggest.globals || Date.now() - varSuggest.globalsAt > 5000) {
    try { varSuggest.globals = await api('/variables'); varSuggest.globalsAt = Date.now(); } catch { varSuggest.globals = varSuggest.globals || {}; }
  }
  const out = new Map();
  const add = (key, value, source, secret) => { if (key && !out.has(key)) out.set(key, { key, value: value == null ? '' : String(value), source, secret: !!secret }); };
  const env = (state.envs || []).find((e) => e.id === state.activeEnvId);
  if (env) (env.variables || []).forEach((v) => { if (v.enabled !== false) add((v.key || '').trim(), v.value, env.name, v.secret); });
  if (inheritedInfo && inheritedInfo.variables) Object.entries(inheritedInfo.variables).forEach(([k, v]) => add(k, v, 'folder'));
  Object.entries(varSuggest.globals || {}).forEach(([k, v]) => add(k, v, 'global'));
  return [...out.values()];
}
function closeVarSuggest() {
  if (varSuggest.el) varSuggest.el.remove();
  varSuggest.el = null; varSuggest.input = null; varSuggest.items = [];
}
// the "{{partial" being typed at the caret, if any
function varQueryAt(input) {
  let caret = input.value.length;
  try { if (input.selectionStart != null) caret = input.selectionStart; } catch { /* password inputs */ }
  const before = input.value.slice(0, caret);
  const m = /\{\{([\w.-]*)$/.exec(before);
  return m ? { q: m[1], start: before.length - m[0].length, caret } : null;
}
function pickVarSuggest(item) {
  const input = varSuggest.input;
  if (!input || !item) return;
  const at = varQueryAt(input);
  const ins = `{{${item.key}}}`;
  let next, pos;
  if (at) { next = input.value.slice(0, at.start) + ins + input.value.slice(at.caret).replace(/^[\w.-]*\}{0,2}/, ''); pos = at.start + ins.length; }
  else { next = ins; pos = ins.length; }  // no {{ typed: the variable becomes the whole value
  input.value = next;
  closeVarSuggest();
  input.focus();
  try { input.setSelectionRange(pos, pos); } catch { /* password inputs */ }
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}
async function openVarSuggest(input) {
  const all = await suggestVariables();
  if (document.activeElement !== input) return;
  const at = varQueryAt(input);
  // with a literal value typed (and no {{ in progress) stay out of the way; ArrowDown still opens it
  const v = input.value.trim();
  if (!at && v && !/^\{\{[\w.-]*\}\}$/.test(v) && !input._varForce) { closeVarSuggest(); return; }
  input._varForce = false;
  const q = (at ? at.q : '').toLowerCase();
  const authy = (k) => /token|auth|key|secret|bearer|jwt/i.test(k) ? 1 : 0;
  const items = all.filter((it) => !q || it.key.toLowerCase().includes(q))
    .sort((a, b) => (b.key.toLowerCase().startsWith(q) - a.key.toLowerCase().startsWith(q)) || (authy(b.key) - authy(a.key)));
  if (!varSuggest.el || varSuggest.input !== input) {
    closeVarSuggest();
    varSuggest.el = document.createElement('div');
    varSuggest.el.className = 'menu var-suggest';
    varSuggest.el.addEventListener('mousedown', (e) => e.preventDefault());  // keep focus in the input
    document.body.appendChild(varSuggest.el);
    varSuggest.input = input;
  }
  varSuggest.items = items.slice(0, 50);
  varSuggest.cur = 0;
  const preview = (it) => it.secret ? '••••••' : (it.value.length > 40 ? it.value.slice(0, 40) + '…' : it.value);
  varSuggest.el.innerHTML = varSuggest.items.length
    ? varSuggest.items.map((it, i) => `<button type="button" data-i="${i}" class="${i === 0 ? 'cur' : ''}" title="Insert {{${esc(it.key)}}}"><span class="vs-k mono">{{${esc(it.key)}}}</span><span class="vs-v mono muted">${esc(preview(it))}</span><span class="vs-s muted small">${esc(it.source)}</span></button>`).join('')
    : `<div class="vs-empty muted small">${all.length ? 'No variable matches' : 'No variables yet. Add them in an environment or in Global variables.'}</div>`;
  $$('button', varSuggest.el).forEach((b) => b.addEventListener('click', () => pickVarSuggest(varSuggest.items[+b.dataset.i])));
  const r = input.getBoundingClientRect();
  Object.assign(varSuggest.el.style, { left: r.left + 'px', top: (r.bottom + 2) + 'px', width: Math.max(r.width, 280) + 'px' });
  varSuggest.el.style.maxHeight = Math.max(120, Math.min(260, window.innerHeight - r.bottom - 8)) + 'px';
}
function moveVarSuggest(d) {
  const btns = $$('button', varSuggest.el);
  if (!btns.length) return;
  varSuggest.cur = (varSuggest.cur + d + btns.length) % btns.length;
  btns.forEach((b, i) => b.classList.toggle('cur', i === varSuggest.cur));
  btns[varSuggest.cur].scrollIntoView({ block: 'nearest' });
}
const isVarSuggestInput = (el) => !!(el && el.tagName === 'INPUT' && el.id && el.id.startsWith('auth-') && el.id !== 'auth-prefix' && el.closest('#auth-fields'));
document.addEventListener('focusin', (e) => { if (isVarSuggestInput(e.target)) openVarSuggest(e.target); });
document.addEventListener('focusout', (e) => { if (e.target === varSuggest.input) setTimeout(() => { if (document.activeElement !== varSuggest.input) closeVarSuggest(); }, 0); });
document.addEventListener('input', (e) => { if (e.isTrusted && isVarSuggestInput(e.target)) openVarSuggest(e.target); });
document.addEventListener('keydown', (e) => {
  if (!isVarSuggestInput(e.target)) return;
  if (!varSuggest.el && e.key === 'ArrowDown') { e.preventDefault(); e.target._varForce = true; openVarSuggest(e.target); return; }
  if (!varSuggest.el || varSuggest.input !== e.target) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); moveVarSuggest(e.key === 'ArrowDown' ? 1 : -1); }
  else if ((e.key === 'Enter' || e.key === 'Tab') && varSuggest.items.length) { e.preventDefault(); e.stopPropagation(); pickVarSuggest(varSuggest.items[varSuggest.cur]); }
  else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeVarSuggest(); }
}, true);
window.addEventListener('resize', closeVarSuggest);
document.addEventListener('scroll', (e) => { if (varSuggest.el && !varSuggest.el.contains(e.target)) closeVarSuggest(); }, true);

// ---- OAuth 2.0 token status / buttons (request Auth tab and folder settings)
function oauthContext(el) {
  const fs = el.closest('#fs-auth');
  if (fs) return { name: 'folder', method: 'GET', url: '', folder_id: fs.dataset.folder, auth: readAuthFields(fs, 'oauth2') };
  if (!state.current) return null;
  return { ...readEditor(), id: state.current.id, folder_id: state.current.folder_id || null, auth: readAuth() };
}
function fmtTokenStatus(st) {
  if (!st || !st.has_token) return 'No token yet. One is fetched on the first send.';
  const exp = st.expires_at ? new Date(st.expires_at * 1000) : null;
  const left = exp ? Math.round((exp - Date.now()) / 60000) : null;
  const when = exp ? (st.valid ? `expires in ${left < 1 ? '<1' : left} min` : 'expired') : 'no expiry given';
  return `<span class="${st.valid ? 'ok-text' : 'err-text'}">●</span> <code>${esc(st.preview)}</code> · ${when}${st.scope ? ' · scope ' + esc(st.scope) : ''}${st.has_refresh ? ' · refreshable' : ''}`;
}
async function refreshOauthStatus(box) {
  const status = box && $('.oa-status', box);
  if (!status) return;
  const ctx = oauthContext(status);
  if (!ctx) { status.textContent = ''; return; }
  try { status.innerHTML = fmtTokenStatus(await api('/oauth2/status', { method: 'POST', body: ctx })); }
  catch (e) { status.textContent = e.message; }
}
document.addEventListener('change', (e) => {
  if (e.target.id === 'auth-grant') {
    const pw = e.target.closest('#auth-fields') && $('.oa-pw', e.target.closest('#auth-fields'));
    if (pw) pw.hidden = e.target.value !== 'password';
  }
});
document.addEventListener('click', async (e) => {
  const get = e.target.closest('.oa-get');
  const clear = e.target.closest('.oa-clear');
  if (!get && !clear) return;
  const box = e.target.closest('#auth-fields');
  const ctx = oauthContext(e.target);
  if (!ctx) return;
  const status = $('.oa-status', box);
  try {
    if (get) {
      get.disabled = true;
      status.innerHTML = '<span class="spin"></span>Requesting a token…';
      status.innerHTML = fmtTokenStatus(await api('/oauth2/token', { method: 'POST', body: ctx }));
      toast('Got a new OAuth 2.0 token', 'ok');
    } else {
      await api('/oauth2/forget', { method: 'POST', body: ctx });
      status.innerHTML = fmtTokenStatus(null);
    }
  } catch (err) { status.innerHTML = `<span class="err-text">${esc(err.message)}</span>`; }
  finally { if (get) get.disabled = false; }
});
function renderAuthPanel(r) {
  const box = $('#auth-panel');
  const a = r.auth || { type: 'inherit' };
  let note = 'This request uses the auth of its folder (or none). Set auth on a folder with its ⋯ menu → Settings.';
  if (inheritedInfo && inheritedInfo.auth && inheritedInfo.auth.type !== 'none') note = `Inherits <strong>${esc(AUTH_LABELS[inheritedInfo.auth.type])}</strong> from folder <strong>${esc(inheritedInfo.auth_from || '')}</strong>.`;
  else if (r.folder_id) note = 'No folder in the path sets auth, so nothing is added. Set auth on a folder with its ⋯ menu → Settings.';
  box.innerHTML = `
    <label class="field"><span class="fl">Type</span>
      <select id="auth-type">${Object.entries(AUTH_LABELS).map(([v, l]) => `<option value="${v}" ${a.type === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
    </label>
    <div id="auth-fields">${authFields(a, note)}</div>`;
  $('#auth-type', box).addEventListener('change', () => {
    const cur = readAuth();
    $('#auth-fields', box).innerHTML = authFields(cur, note);
    updateTabCounts();
    if (cur.type === 'oauth2') refreshOauthStatus(box);
  });
  if (a.type === 'oauth2') refreshOauthStatus(box);
}
function readAuth() {
  const box = $('#auth-panel');
  if (!box || !$('#auth-type', box)) return (state.current && state.current.auth) || { type: 'inherit' };
  return readAuthFields(box, $('#auth-type', box).value, state.current && state.current.auth);
}

// ================================================================ tests tab
const TEST_TYPES = [['status', 'Status code'], ['json', 'JSON value'], ['header', 'Header'], ['body', 'Body'], ['time', 'Response time']];
const TEST_OPS = {
  status: [['equals', 'equals'], ['not_equals', 'does not equal'], ['is_2xx', 'is 2xx'], ['is_3xx', 'is 3xx'], ['is_4xx', 'is 4xx'], ['is_5xx', 'is 5xx'], ['in', 'is one of'], ['less_than', 'is less than'], ['greater_than', 'is greater than']],
  time: [['less_than', 'is less than (ms)'], ['greater_than', 'is greater than (ms)']],
  header: [['exists', 'exists'], ['not_exists', 'does not exist'], ['equals', 'equals'], ['not_equals', 'does not equal'], ['contains', 'contains'], ['not_contains', 'does not contain'], ['matches', 'matches regex']],
  body: [['contains', 'contains'], ['not_contains', 'does not contain'], ['equals', 'equals'], ['not_equals', 'does not equal'], ['matches', 'matches regex'], ['is_json', 'is valid JSON'], ['is_empty', 'is empty'], ['not_empty', 'is not empty']],
  json: [['exists', 'exists'], ['not_exists', 'does not exist'], ['equals', 'equals'], ['not_equals', 'does not equal'], ['contains', 'contains'], ['not_contains', 'does not contain'], ['matches', 'matches regex'], ['type_is', 'is of type'], ['length_equals', 'has length'], ['less_than', 'is less than'], ['greater_than', 'is greater than'], ['is_empty', 'is empty'], ['not_empty', 'is not empty'], ['is_true', 'is true'], ['is_false', 'is false'], ['is_null', 'is null']],
};
const NO_VALUE_OPS = new Set(['exists', 'not_exists', 'is_json', 'is_empty', 'not_empty', 'is_2xx', 'is_3xx', 'is_4xx', 'is_5xx', 'is_true', 'is_false', 'is_null']);
const TEST_PRESETS = [
  { label: 'Status is 200', t: { type: 'status', op: 'equals', value: '200' } },
  { label: 'Status is 2xx', t: { type: 'status', op: 'is_2xx' } },
  { label: 'Faster than 500 ms', t: { type: 'time', op: 'less_than', value: '500' } },
  { label: 'Body is JSON', t: { type: 'body', op: 'is_json' } },
  { label: 'Content-Type has json', t: { type: 'header', target: 'Content-Type', op: 'contains', value: 'json' } },
  { label: 'JSON field exists', t: { type: 'json', target: 'data.id', op: 'exists' } },
];

function testRow(t) {
  const row = document.createElement('div');
  row.className = 'test-row';
  const type = TEST_TYPES.some(([v]) => v === t.type) ? t.type : 'status';
  const ops = TEST_OPS[type];
  const op = ops.some(([v]) => v === t.op) ? t.op : ops[0][0];
  const needsTarget = type === 'json' || type === 'header';
  row.innerHTML = `
    <input type="checkbox" class="t-on" ${t.enabled !== false ? 'checked' : ''} title="Enabled">
    <select class="t-type">${TEST_TYPES.map(([v, l]) => `<option value="${v}" ${v === type ? 'selected' : ''}>${l}</option>`).join('')}</select>
    <input class="t-target mono${needsTarget ? '' : ' t-blank'}" value="${esc(t.target || '')}" placeholder="${type === 'json' ? 'path, e.g. data.items[0].id' : 'header name'}" ${needsTarget ? '' : 'tabindex="-1"'} spellcheck="false">
    <select class="t-op">${ops.map(([v, l]) => `<option value="${v}" ${v === op ? 'selected' : ''}>${l}</option>`).join('')}</select>
    <input class="t-value mono${NO_VALUE_OPS.has(op) ? ' t-blank' : ''}" value="${esc(t.value || '')}" placeholder="${type === 'json' && op === 'type_is' ? 'string | number | boolean | object | array | null' : 'expected value'}" ${NO_VALUE_OPS.has(op) ? 'tabindex="-1"' : ''} spellcheck="false">
    <input class="t-name" value="${esc(t.name || '')}" placeholder="name (optional)" spellcheck="false">
    <select class="t-body" title="Which body this test applies to">${bodyScopeOptions(t.body_id)}</select>
    <button class="kv-del" title="Remove" aria-label="Remove row">✕</button>`;
  $('.t-body', row).addEventListener('change', scheduleSave);
  $('.t-type', row).addEventListener('change', () => { const cur = readTestRow(row); cur.op = ''; row.replaceWith(testRow(cur)); scheduleSave(); });
  $('.t-op', row).addEventListener('change', () => { row.replaceWith(testRow(readTestRow(row))); scheduleSave(); });
  $('.kv-del', row).addEventListener('click', () => { row.remove(); updateTabCounts(); scheduleSave(); });
  return row;
}
function readTestRow(row) {
  return { enabled: $('.t-on', row).checked, type: $('.t-type', row).value, target: $('.t-target', row).value, op: $('.t-op', row).value, value: $('.t-value', row).value, name: $('.t-name', row).value, body_id: $('.t-body', row).value };
}
// A test applies to every body, or to one named body (the select only shows with two or more bodies).
function bodyScopeOptions(selected) {
  const bodies = (typeof state !== 'undefined' && state.bodies) || [];
  const sel = bodies.some((b) => b.id === selected) ? selected : '';
  return `<option value="">All bodies</option>` + bodies.map((b) => `<option value="${esc(b.id)}" ${b.id === sel ? 'selected' : ''}>${esc(b.name || 'Unnamed')}</option>`).join('');
}
function refreshTestBodyScopes() {
  const list = $('#tests-list');
  if (!list) return;
  const bodyType = $('#f-body-type') ? $('#f-body-type').value : 'none';
  list.classList.toggle('scoped', bodyType !== 'none' && state.bodies.length > 1);
  $$('.t-body', list).forEach((sel) => { sel.innerHTML = bodyScopeOptions(sel.value); });
  const hint = $('#tests-scope-hint');
  if (hint) hint.textContent = list.classList.contains('scoped') ? 'Each test runs for all bodies or for the one body you pick.' : '';
  refreshScriptScopes();
}
const EXTRACT_SOURCES = [['json', 'JSON path'], ['header', 'Header'], ['status', 'Status code'], ['body', 'Whole body'], ['regex', 'Regex (first group)']];
function extractRow(e) {
  const row = document.createElement('div');
  row.className = 'extract-row';
  const src = EXTRACT_SOURCES.some(([v]) => v === e.source) ? e.source : 'json';
  row.innerHTML = `
    <input type="checkbox" class="x-on" ${e.enabled !== false ? 'checked' : ''} title="Enabled">
    <span class="muted small">set</span>
    <input class="x-name mono" value="${esc(e.name || '')}" placeholder="variable name" spellcheck="false">
    <select class="x-scope"><option value="env" ${e.scope !== 'global' ? 'selected' : ''}>in environment</option><option value="global" ${e.scope === 'global' ? 'selected' : ''}>globally</option></select>
    <span class="muted small">from</span>
    <select class="x-source">${EXTRACT_SOURCES.map(([v, l]) => `<option value="${v}" ${v === src ? 'selected' : ''}>${l}</option>`).join('')}</select>
    <input class="x-path mono" value="${esc(e.path || '')}" placeholder="${src === 'json' ? 'data.token' : src === 'header' ? 'Set-Cookie' : src === 'regex' ? 'token=(\\w+)' : ''}" ${src === 'status' || src === 'body' ? 'hidden' : ''} spellcheck="false">
    <button class="kv-del" title="Remove" aria-label="Remove row">✕</button>`;
  $('.x-source', row).addEventListener('change', () => { row.replaceWith(extractRow(readExtractRow(row))); scheduleSave(); });
  $('.kv-del', row).addEventListener('click', () => { row.remove(); updateTabCounts(); scheduleSave(); });
  return row;
}
function readExtractRow(row) {
  return { enabled: $('.x-on', row).checked, name: $('.x-name', row).value, scope: $('.x-scope', row).value, source: $('.x-source', row).value, path: $('.x-path', row).value };
}
function preVarRow(v) {
  const row = document.createElement('div');
  row.className = 'prevar-row';
  row.innerHTML = `
    <input type="checkbox" class="p-on" ${v.enabled !== false ? 'checked' : ''} title="Enabled">
    <input class="p-key mono" value="${esc(v.key || '')}" placeholder="name" spellcheck="false">
    <input class="p-value mono grow" value="${esc(v.value || '')}" placeholder="value, e.g. {{$guid}} or {{$timestamp}}" spellcheck="false">
    <select class="p-scope"><option value="local" ${!v.scope || v.scope === 'local' ? 'selected' : ''}>this request only</option><option value="env" ${v.scope === 'env' ? 'selected' : ''}>save in environment</option><option value="global" ${v.scope === 'global' ? 'selected' : ''}>save globally</option></select>
    <button class="kv-del" title="Remove" aria-label="Remove row">✕</button>`;
  $('.kv-del', row).addEventListener('click', () => { row.remove(); scheduleSave(); });
  return row;
}
function renderTestsPanel(r) {
  const box = $('#tests-panel');
  box.innerHTML = `
    <div class="row tight script-lang">
      <span class="muted small">Script language</span>
      <select id="f-script-lang" title="Language of the pre-request and test scripts"><option value="js">JavaScript (Postman pm API)</option><option value="py">Python (py object)</option></select>
      <span class="muted small" id="script-blurb"></span>
      <button class="link small" id="btn-script-help">Reference</button>
    </div>
    <div class="tests-section">
      <div class="ts-head"><h4>Pre-request</h4><span class="muted small">Runs before the request is sent.</span></div>
      <div class="ts-sub">Variables <span class="muted small">Dynamic values: <code>{{$guid}}</code> <code>{{$timestamp}}</code> <code>{{$isoTimestamp}}</code> <code>{{$randomInt 1 100}}</code> <code>{{$randomEmail}}</code>; <code>{{body_name}}</code> is the body being sent.</span></div>
      <div id="prevars-list" class="rows"></div>
      <button class="link" id="btn-prevar-add">+ Add variable</button>
      <div class="ts-sub">Pre-request script ${scriptScopeHtml('pre')}</div>
      <textarea id="f-script-pre" class="mono" rows="6" spellcheck="false"></textarea>
    </div>
    <div class="tests-section">
      <div class="ts-head"><h4>Post-response</h4><span class="muted small">Runs after the response arrives. Results show in the response's Tests tab; a failing test fails the request in a run.</span></div>
      <div class="ts-sub">Tests <span class="muted small" id="tests-scope-hint"></span></div>
      <div id="tests-list" class="rows"></div>
      <div class="row tight wrap"><button class="link" id="btn-test-add">+ Add test</button><span class="muted small">Quick add:</span>${TEST_PRESETS.map((p, i) => `<span class="chip" data-preset="${i}">${esc(p.label)}</span>`).join('')}</div>
      <div class="ts-sub">Extract variables <span class="muted small">Chain requests by saving a token or id for the next request (<code>{{token}}</code>).</span></div>
      <div id="extract-list" class="rows"></div>
      <button class="link" id="btn-extract-add">+ Add extraction</button>
      <div class="ts-sub">Test script ${scriptScopeHtml('test')}</div>
      <textarea id="f-script-test" class="mono" rows="6" spellcheck="false"></textarea>
    </div>`;
  const sc = (r.options && r.options.scripts) || {};
  $('#f-script-lang').value = sc.lang === 'py' ? 'py' : 'js';
  // one script per scope: '' is "All bodies", a body id is that body's own script (shown one at a time)
  scriptStore = { pre: { '': sc.pre || '' }, test: { '': sc.test || '' } };
  Object.entries(sc.bodies || {}).forEach(([id, own]) => { scriptStore.pre[id] = (own && own.pre) || ''; scriptStore.test[id] = (own && own.test) || ''; });
  scriptScope = { pre: '', test: '' };
  $('#f-script-pre').value = scriptStore.pre[''];
  $('#f-script-test').value = scriptStore.test[''];
  const scriptLang = () => ($('#f-script-lang') && $('#f-script-lang').value === 'py' ? 'python' : 'javascript');
  enhanceTextarea($('#f-script-pre'), { lang: scriptLang });
  enhanceTextarea($('#f-script-test'), { lang: scriptLang });
  applyScriptLang();
  ['pre', 'test'].forEach((kind) => {
    $('#f-script-' + kind).addEventListener('input', () => { scriptStore[kind][scriptScope[kind]] = $('#f-script-' + kind).value; refreshScriptScopes(); });
    $(`.script-scope[data-kind="${kind}"]`, box).addEventListener('change', (e) => switchScriptScope(kind, e.target.value));
  });
  $('#f-script-lang').addEventListener('change', () => { applyScriptLang(); scheduleSave(); });
  $('#btn-script-help').addEventListener('click', openScriptHelp);
  (r.pre_vars || []).forEach((v) => $('#prevars-list').appendChild(preVarRow(v)));
  (r.tests || []).forEach((t) => $('#tests-list').appendChild(testRow(t)));
  (r.extract || []).forEach((e) => $('#extract-list').appendChild(extractRow(e)));
  refreshTestBodyScopes();
  $('#btn-prevar-add').addEventListener('click', () => { const row = preVarRow({}); $('#prevars-list').appendChild(row); $('.p-key', row).focus(); });
  $('#btn-test-add').addEventListener('click', () => { const row = testRow({ type: 'status', op: 'equals', value: '200' }); $('#tests-list').appendChild(row); updateTabCounts(); scheduleSave(); });
  $('#btn-extract-add').addEventListener('click', () => { const row = extractRow({ source: 'json', scope: 'env' }); $('#extract-list').appendChild(row); $('.x-name', row).focus(); updateTabCounts(); });
  $$('[data-preset]', box).forEach((c) => c.addEventListener('click', () => { $('#tests-list').appendChild(testRow({ ...TEST_PRESETS[c.dataset.preset].t })); updateTabCounts(); scheduleSave(); }));
}
// ---- scripts per body: the "All bodies" script runs for every body, then the body's own (if written)
let scriptStore = { pre: { '': '' }, test: { '': '' } };   // kind -> scope ('' or body id) -> code
let scriptScope = { pre: '', test: '' };                    // which scope each editor currently shows
function scriptScopeHtml(kind) {
  return `<label class="script-scope-wrap muted small" hidden>for <select class="script-scope" data-kind="${kind}" title="Which body this script runs for: every body, or only the one you pick"></select></label><span class="muted small script-scope-hint" data-kind="${kind}"></span>`;
}
function bodyHasScript(kind, id) { return !!(scriptStore[kind][id] || '').trim(); }
function refreshScriptScopes() {
  const box = $('#tests-panel');
  if (!box || !$('.script-scope', box)) return;
  const bodies = (typeof state !== 'undefined' && state.bodies) || [];
  const bodyType = $('#f-body-type') ? $('#f-body-type').value : 'none';
  ['pre', 'test'].forEach((kind) => {
    const sel = $(`.script-scope[data-kind="${kind}"]`, box);
    const wrap = sel.closest('.script-scope-wrap');
    const hint = $(`.script-scope-hint[data-kind="${kind}"]`, box);
    // a body's own script is only reachable with two or more bodies, or while one body still has its own script
    const scoped = bodyType !== 'none' && (bodies.length > 1 || bodies.some((b) => bodyHasScript(kind, b.id)));
    if (scriptScope[kind] && (!scoped || !bodies.some((b) => b.id === scriptScope[kind]))) { switchScriptScope(kind, ''); return; }
    wrap.hidden = !scoped;
    sel.innerHTML = `<option value="">All bodies${bodyHasScript(kind, '') ? ' ●' : ''}</option>` +
      bodies.map((b) => `<option value="${esc(b.id)}" ${b.id === scriptScope[kind] ? 'selected' : ''}>${esc(b.name || 'Unnamed')}${bodyHasScript(kind, b.id) ? ' ●' : ''}</option>`).join('');
    const own = bodies.find((b) => b.id === scriptScope[kind]);
    hint.textContent = !scoped ? '' : own ? `Runs only when “${own.name || 'Unnamed'}” is sent, after the script for all bodies.` : 'Runs for every body. Pick a body to write a script for that body only (● marks bodies that have one).';
  });
}
function switchScriptScope(kind, id) {
  const ta = $('#f-script-' + kind);
  if (!ta) return;
  scriptScope[kind] = id;
  const code = scriptStore[kind][id] || '';
  if (ta._enhanced) ta._enhanced.setValue(code); else ta.value = code;
  refreshScriptScopes();
}
function readScripts() {
  // the editors may have been set without an input event (e.g. programmatically): take what they show now
  ['pre', 'test'].forEach((kind) => { const ta = $('#f-script-' + kind); if (ta) scriptStore[kind][scriptScope[kind]] = ta.value; });
  const ids = new Set(((typeof state !== 'undefined' && state.bodies) || []).map((b) => b.id));
  const bodies = {};
  ids.forEach((id) => {
    const pre = scriptStore.pre[id] || '', test = scriptStore.test[id] || '';
    if (pre.trim() || test.trim()) bodies[id] = { pre, test };
  });
  return { pre: scriptStore.pre[''] || '', test: scriptStore.test[''] || '', lang: ($('#f-script-lang') || {}).value === 'py' ? 'py' : 'js', bodies };
}
function readTests() {
  return {
    tests: $$('#tests-list .test-row').map(readTestRow),
    extract: $$('#extract-list .extract-row').map(readExtractRow).filter((e) => e.name.trim() || e.path.trim()),
    pre_vars: $$('#prevars-list .prevar-row').map((row) => ({ enabled: $('.p-on', row).checked, key: $('.p-key', row).value, value: $('.p-value', row).value, scope: $('.p-scope', row).value })).filter((v) => v.key.trim()),
  };
}

// ======================================================= request settings
function renderRequestSettings(r) {
  const o = r.options || {};
  $('#f-redirects').value = o.follow_redirects === true ? '1' : o.follow_redirects === false ? '0' : '';
  $('#f-send-cookies').checked = o.send_cookies !== false;
  $('#f-store-cookies').checked = o.store_cookies !== false;
  $('#f-stream').checked = o.stream === true;
  renderDependency(r);
}
// ---- "Depends on": another saved request that is sent first (a token request, typically)
function fmtAge(sec) {
  if (sec == null) return '';
  if (sec < 60) return `${Math.round(sec)} s`;
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  return `${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} min`;
}
function renderDependency(r) {
  const dep = (r.options && r.options.depends) || null;
  const sel = $('#f-dep');
  const others = state.requests.filter((x) => x.id !== r.id).map((x) => ({ x, path: [...folderPath(x.folder_id), x.name].join(' › ') }))
    .sort((a, b) => a.path.localeCompare(b.path));
  sel.innerHTML = '<option value="">none</option>' + others.map(({ x, path }) => `<option value="${esc(x.id)}">${esc(x.method)} ${esc(path)}</option>`).join('');
  sel.value = dep && others.some(({ x }) => x.id === dep.request_id) ? dep.request_id : '';
  const ttl = dep ? Number(dep.ttl) || 0 : 1800;
  const unit = ttl > 0 && ttl % 3600 === 0 ? 3600 : ttl > 0 && ttl % 60 === 0 ? 60 : 1;
  $('#f-dep-unit').value = String(unit);
  $('#f-dep-ttl').value = String(Math.round(ttl / unit));
  $('#f-dep-retry').checked = !dep || dep.retry_on_auth !== false;
  refreshDependencyUi();
}
async function refreshDependencyUi() {
  const id = $('#f-dep').value;
  $('#f-dep-opts').hidden = !id;
  const status = $('#f-dep-status');
  if (!id) { status.textContent = ''; return; }
  const unit = Number($('#f-dep-unit').value) || 1;
  const ttl = (Number($('#f-dep-ttl').value) || 0) * unit;
  try {
    const s = await api(`/requests/${id}/dependency`);
    if ($('#f-dep').value !== id) return;
    if (!s.ran_at) {
      status.innerHTML = 'Not sent yet: it runs before the next send of this request.';
    } else {
      const left = ttl - s.age;
      const when = ttl === 0 ? 'it runs before every send' : left > 0 ? `runs again in ${fmtAge(left)}` : 'runs again before the next send';
      status.innerHTML = `Last sent ${fmtAge(s.age)} ago${s.status_code ? ` (${s.status_code})` : ''}${s.ok ? '' : ', <span class="err-text">failed</span>'} · ${when} · <button class="link small" id="f-dep-reset">send it before the next send</button>`;
      $('#f-dep-reset').addEventListener('click', async () => { await api(`/requests/${id}/dependency`, { method: 'DELETE' }); refreshDependencyUi(); });
    }
  } catch (e) { status.textContent = e.message; }
}
function readDependency() {
  const id = $('#f-dep') ? $('#f-dep').value : '';
  if (!id) return null;
  return { request_id: id, ttl: (Number($('#f-dep-ttl').value) || 0) * (Number($('#f-dep-unit').value) || 1), retry_on_auth: $('#f-dep-retry').checked };
}
function readRequestOptions() {
  const v = $('#f-redirects').value;
  // example responses are saved by their own calls and can be large: the autosave leaves them out
  // (the server keeps what it has when the key is missing)
  const { examples: _examples, ...kept } = (state.current && state.current.options) || {};
  return {
    ...kept,
    follow_redirects: v === '' ? null : v === '1',
    send_cookies: $('#f-send-cookies').checked,
    store_cookies: $('#f-store-cookies').checked,
    stream: $('#f-stream').checked,
    scripts: $('#f-script-pre') ? readScripts() : ((state.current && state.current.options && state.current.options.scripts) || { pre: '', test: '', lang: 'js', bodies: {} }),
    active_body: state.activeBodyId || '',
    depends: readDependency(),
  };
}
const SCRIPT_PLACEHOLDERS = {
  js: {
    pre: 'pm.request.headers.add({ key: "X-Trace", value: pm.variables.get("trace") });\npm.environment.set("ts", Date.now());',
    test: 'pm.test("status is 200", function () {\n  pm.response.to.have.status(200);\n});\nconst data = pm.response.json();\npm.test("has id", () => pm.expect(data.id).to.be.a("number"));\npm.environment.set("user_id", data.id);',
    blurb: 'Postman scripts run here unchanged (<code>pm.test</code>, <code>pm.expect</code>, <code>pm.response</code>, <code>pm.environment</code>…). <code>console.log</code> shows in the Tests tab of the response.',
  },
  py: {
    pre: 'py.request.headers["X-Trace"] = py.variables.get("trace", "none")\npy.variables.set("ts", "123")',
    test: 'py.test("status is 200", py.response.status_code == 200)\nconst data = py.response.json();\npy.test("has id", lambda: py.expect(data.id).to_be_a(int))\npy.environment.set("user_id", data.id)',
    blurb: 'Python with a <code>py</code> object; <code>print()</code> shows in the Tests tab of the response.',
  },
};
function applyScriptLang() {
  const lang = $('#f-script-lang').value === 'py' ? 'py' : 'js';
  $('#f-script-pre').placeholder = SCRIPT_PLACEHOLDERS[lang].pre;
  $('#f-script-test').placeholder = SCRIPT_PLACEHOLDERS[lang].test;
  $('#script-blurb').innerHTML = SCRIPT_PLACEHOLDERS[lang].blurb;
  ['#f-script-pre', '#f-script-test'].forEach((s) => { const ta = $(s); if (ta && ta._enhanced) ta._enhanced.refresh(); });
}
function openScriptHelp() {
  if (($('#f-script-lang') || {}).value !== 'py') { openJsScriptHelp(); return; }
  openModal('Python scripts', `<pre class="mono snippet">${esc(`# Pre-request script: runs before the request is sent
py.variables.set("ts", "123")            # this request only
py.environment.set("token", "abc")       # saved in the active environment
py.globals.set("base", "https://x")      # saved globally
py.variables.get("name", "default")      # effective value
py.request.headers["X-Trace"] = "1"      # dict of headers (add / change / del)
py.request.params["page"] = "2"          # query params
py.request.url / py.request.method / py.request.body   # editable
py.data                                  # the variables-file row in a run
print("debug")                           # shown in the response's Tests tab

# Test script: runs after the response
r = py.response
r.status_code, r.ok, r.text, r.json(), r.headers["content-type"], r.time_ms, r.size
# JavaScript habits accepted: const/let/var, trailing ;, true/false/null, console.log(...)
py.test("status is 200", r.status_code == 200)
py.test("has token", lambda: py.expect(r.json()["token"]).to_be_a(str))
py.expect(x).to_equal(y) .to_not_equal(y) .to_contain(y) .to_be_true() .to_be_false()
            .to_be_none() .to_be_above(n) .to_be_below(n) .to_match(regex)
            .to_be_a(type) .to_have_length(n) .to_have_key(k)
const token = r.json().data.token;        # dotted access like JavaScript; a missing field is None
py.environment.set("token", token)         # chain into the next request
# json and re are imported; a failing assert inside a lambda fails that test;
# an exception outside a test fails the whole script. Timeout: 15 s.`)}</pre>`, [{ label: 'Close', cls: 'primary', onClick: closeModal }]);
}
function openJsScriptHelp() {
  openModal('JavaScript scripts (Postman pm API)', `<pre class="mono snippet">${esc(`// Pre-request script: runs before the request is sent
pm.variables.set("ts", Date.now());            // this request only
pm.environment.set("token", "abc");            // saved in the active environment
pm.globals.set("base", "https://x");           // saved globally
pm.collectionVariables.set("v", "1");          // saved on the request's folder
pm.variables.get("name");                      // local > variables file > environment > folder > global
pm.request.headers.add({ key: "X-Trace", value: "1" });   // .upsert / .remove / .get / .has
pm.request.url.query.add({ key: "page", value: "2" });
pm.request.method = "POST"; pm.request.url = "https://…"; pm.request.body.raw = JSON.stringify({ a: 1 });
pm.iterationData.get("email");                 // variables-file column in a run
console.log("debug", pm.info.requestName);     // shown in the response's Tests tab

// Test script: runs after the response
pm.test("status is 200", function () { pm.response.to.have.status(200); });
pm.response.to.be.ok / .success / .clientError / .serverError;  pm.response.to.not.have.status(404);
pm.response.to.have.header("Content-Type", "application/json");
pm.response.to.have.jsonBody("data.token");
pm.response.code, pm.response.status, pm.response.responseTime, pm.response.responseSize
pm.response.json(), pm.response.text(), pm.response.headers.get("x-id"), pm.cookies.get("session")
const data = pm.response.json();
pm.test("token", () => pm.expect(data.token).to.be.a("string").and.not.empty);
pm.expect(x).to.equal(y) .eql(y) .include(y) .have.property("k", v) .have.lengthOf(n)
            .be.above(n) .be.below(n) .be.within(a, b) .match(/re/) .be.oneOf([...])
            .be.true / .false / .null / .undefined / .ok / .empty / .exist   (.not and .deep work)
pm.expect(data).to.have.nested.property("items[0].id");
pm.environment.set("token", data.token);        // chain into the next request
tests["legacy style"] = responseCode.code === 200;   // old Postman globals also work
// async tests: pm.test("x", async () => { await …; });   setTimeout works, pm.sendRequest does not.
// Engine: ${esc('{{engine}}')}; timeout 15 s; no network or file access from scripts.`).replace('{{engine}}', 'QuickJS or V8')}</pre>`, [{ label: 'Close', cls: 'primary', onClick: closeModal }]);
}

// ==================================================== params: path variables
function renderPathVars() {
  const box = $('#path-vars');
  const url = $('#f-url').value || '';
  const names = [...new Set((url.split('?')[0].match(/(?<=\/):([A-Za-z_][A-Za-z0-9_]*)/g) || []).map((s) => s.slice(1)))];
  if (!names.length) { box.hidden = true; box.innerHTML = ''; return; }
  const have = new Set(readKv($('#kv-params')).map((p) => p.key));
  box.hidden = false;
  box.innerHTML = `<span class="muted small">Path variables in the URL:</span> ${names.map((n) => `<span class="chip ${have.has(n) ? 'ok' : ''}" data-pv="${esc(n)}" title="${have.has(n) ? 'Value comes from the Params row ' + n : 'Click to add a Params row for :' + n}">:${esc(n)}${have.has(n) ? ' ✓' : ''}</span>`).join(' ')}
    <span class="muted small">Give each a value in the rows above (key = name without the colon); it replaces <code>:name</code> in the path instead of becoming a query parameter.</span>`;
  $$('[data-pv]', box).forEach((c) => c.addEventListener('click', () => {
    const n = c.dataset.pv;
    if (have.has(n)) return;
    const rows = readKv($('#kv-params'));
    rows.push({ key: n, value: '', enabled: true });
    $('#kv-params').dataset.mode = 'rows';
    renderKv($('#kv-params'), rows);
    const inputs = $$('#kv-params .kv-row');
    const last = inputs[inputs.length - 1];
    if (last) $('.kv-val', last).focus();
    renderPathVars();
    scheduleSave();
  }));
}

// ========================================================== inherited headers
function renderInheritedHeaders() {
  const box = $('#inherited-headers');
  const rows = inheritedInfo ? inheritedInfo.headers : [];
  if (!rows || !rows.length) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  box.innerHTML = `<div class="muted small">Inherited from folders (a request header with the same name wins):</div>` +
    rows.map((h) => `<div class="inh-row mono"><span class="k">${esc(h.key)}</span><span class="v">${esc(h.value)}</span></div>`).join('');
}

// ================================================================ tab counts
function updateTabCounts() {
  const n = (id, count) => { const el = $('#' + id); if (el) el.textContent = count ? String(count) : ''; };
  n('n-params', readKv($('#kv-params')).filter((p) => p.enabled !== false).length);
  n('n-headers', readKv($('#kv-headers')).filter((p) => p.enabled !== false).length + ((inheritedInfo && inheritedInfo.headers) ? inheritedInfo.headers.length : 0));
  const auth = $('#auth-type') ? $('#auth-type').value : 'inherit';
  const authEl = $('#n-auth');
  if (authEl) authEl.textContent = auth === 'inherit' ? ((inheritedInfo && inheritedInfo.auth && inheritedInfo.auth.type !== 'none') ? '↑' : '') : auth === 'none' ? '' : '●';
  const t = readTests();
  n('n-tests', t.tests.filter((x) => x.enabled).length + t.extract.filter((x) => x.enabled).length);
  const bt = $('#f-body-type').value;
  n('n-body', bt === 'none' ? 0 : state.bodies.filter((b) => b.enabled).length);
}

// ============================================================== body modes
// Text-like bodies use the enhanced textarea; form/multipart use key/value rows
// (stored as "key: value" lines, "key: @path" for files); binary stores a path;
// GraphQL stores {"query", "variables"} as JSON.
const bodyEditor = { mode: 'text', api: null };
const bodyLang = () => { const t = $('#f-body-type').value; return ['json', 'graphql'].includes(t) ? 'json' : t; };
function bodyModeFor(type) {
  if (type === 'form' || type === 'multipart') return 'form';
  if (type === 'binary') return 'binary';
  if (type === 'graphql') return 'graphql';
  return 'text';
}
function initBodyEditor() {
  bodyEditor.api = enhanceTextarea($('#f-body'), { lang: bodyLang });
  const gq = enhanceTextarea($('#f-gql-query'), { lang: 'graphql-query' });
  const gv = enhanceTextarea($('#f-gql-vars'), { lang: 'json' });
  bodyEditor.gql = { q: gq, v: gv };
  $('#f-gql-query').addEventListener('input', () => { flushBody(); renderBodies(); });
  $('#f-gql-vars').addEventListener('input', () => { flushBody(); renderBodies(); });
  $('#f-binary-path').addEventListener('input', () => { flushBody(); renderBodies(); checkBinaryPath(); });
  $('#btn-binary-browse').addEventListener('click', async () => {
    const p = await pickFilePath();
    if (p) { $('#f-binary-path').value = p; flushBody(); renderBodies(); checkBinaryPath(); scheduleSave(); }
  });
  $('#kv-form').addEventListener('input', () => { flushBody(); renderBodies(); });
  $('#kv-form').addEventListener('change', () => { flushBody(); renderBodies(); });
  $('#kv-form').addEventListener('click', (e) => { if (e.target.closest('.kv-del')) { flushBody(); renderBodies(); } });
}
async function pickFilePath() {
  if (native() && native().pick_file) {
    try { return await native().pick_file(); } catch (e) { toast('Could not open the file dialog: ' + e.message, 'error'); return null; }
  }
  return promptDialog('File path', { label: 'Full path of the file on this computer', placeholder: 'C:\\data\\photo.jpg', okLabel: 'Use' });
}
async function checkBinaryPath() {
  const p = $('#f-binary-path').value.trim();
  const info = $('#binary-info');
  if (!p) { info.textContent = 'The file is read when the request is sent. Content-Type is guessed from the extension unless you set a header.'; return; }
  try {
    const r = await api('/path-exists?path=' + encodeURIComponent(p));
    info.textContent = r.exists ? `✓ ${r.name} (${fmtBytes(r.size)})` : '✕ File not found';
    info.className = 'muted small ' + (r.exists ? 'ok' : 'err');
  } catch { info.textContent = ''; }
}
// kv rows <-> "key: value" lines for form bodies (file rows keep the @ prefix in the value)
function formTextToRows(text) {
  return (text || '').split(/\r?\n/).map((line) => {
    let l = line.trim();
    if (!l) return null;
    let enabled = true;
    if (l.startsWith('//')) { enabled = false; l = l.slice(2).trim(); }
    const ci = l.indexOf(':'), ei = l.indexOf('=');
    const i = ei !== -1 && (ci === -1 || ei < ci) ? ei : ci;
    const key = (i === -1 ? l : l.slice(0, i)).trim();
    const value = i === -1 ? '' : l.slice(i + 1).trim();
    return (key || value) ? { key, value, enabled } : null;
  }).filter(Boolean);
}
function formRowsToText(rows) {
  return rows.map((r) => (r.enabled === false ? '// ' : '') + `${r.key}: ${r.value}`).join('\n');
}
function showBodyEditor(b) {
  const type = $('#f-body-type').value;
  const mode = bodyModeFor(type);
  bodyEditor.mode = mode;
  $('#body-text-wrap').hidden = mode !== 'text';
  $('#body-form-wrap').hidden = mode !== 'form';
  $('#body-binary-wrap').hidden = mode !== 'binary';
  $('#body-graphql-wrap').hidden = mode !== 'graphql';
  $('#btn-format-json').hidden = !(mode === 'text' && ['json', 'xml', 'html'].includes(type)) && mode !== 'graphql';
  const hint = $('#body-hint');
  if (mode === 'text') {
    if ($('#f-body').value !== b.body) $('#f-body').value = b.body;
    bodyEditor.api.refresh();   // also switches the editor language when the body type changed
    hint.textContent = type === 'json' ? 'Ctrl+/ comments out a line (removed when sent) · Tab indents · Ctrl+B beautifies' : type === 'xml' || type === 'html' ? 'Ctrl+/ wraps the selection in <!-- --> · Ctrl+B formats' : 'Tab indents · Ctrl+/ toggles // on a line';
  } else if (mode === 'form') {
    const rows = formTextToRows(b.body);
    const kv = $('#kv-form');
    if (kv.dataset.bodyId !== b.id || formRowsToText(readKv(kv)) !== formRowsToText(rows)) {
      kv.dataset.bodyId = b.id;
      renderKv(kv, rows, true);
    }
    $('#form-hint').innerHTML = type === 'multipart'
      ? 'Sent as <code>multipart/form-data</code>. For a file field, set the value to <code>@C:\\path\\to\\file</code> (or use Bulk edit). Variables work in keys and values.'
      : 'Sent as <code>application/x-www-form-urlencoded</code>. Variables work in keys and values.';
    const browse = $('#btn-form-file');
    if (type === 'multipart' && !browse) {
      const btn = document.createElement('button');
      btn.id = 'btn-form-file';
      btn.className = 'link';
      btn.textContent = '+ File field…';
      btn.addEventListener('click', async () => {
        const p = await pickFilePath();
        if (!p) return;
        const rows = readKv($('#kv-form'));
        rows.push({ key: 'file', value: '@' + p, enabled: true });
        $('#kv-form').dataset.mode = 'rows';
        renderKv($('#kv-form'), rows, true);
        flushBody(); renderBodies(); scheduleSave();
      });
      $('#form-hint').before(btn);
    } else if (type !== 'multipart' && browse) browse.remove();
  } else if (mode === 'binary') {
    if ($('#f-binary-path').value !== b.body) $('#f-binary-path').value = b.body;
    checkBinaryPath();
  } else if (mode === 'graphql') {
    let q = '', v = '';
    try { const d = JSON.parse(b.body || '{}'); q = d.query || ''; v = typeof d.variables === 'string' ? d.variables : JSON.stringify(d.variables || {}, null, 2); }
    catch { q = b.body || ''; v = '{}'; }
    if ($('#f-gql-query').value !== q) { $('#f-gql-query').value = q; bodyEditor.gql.q.refresh(); }
    if ($('#f-gql-vars').value !== v) { $('#f-gql-vars').value = v; bodyEditor.gql.v.refresh(); bodyEditor.gql.v.lint(); }
  }
}
function readBodyEditor() {
  const mode = bodyEditor.mode;
  if (mode === 'form') return formRowsToText(readKv($('#kv-form')));
  if (mode === 'binary') return $('#f-binary-path').value.trim();
  if (mode === 'graphql') return JSON.stringify({ query: $('#f-gql-query').value, variables: $('#f-gql-vars').value });
  return $('#f-body').value;
}
function beautifyBody() {
  if (bodyEditor.mode === 'graphql') { bodyEditor.gql.v.format(); return; }
  if (bodyEditor.mode === 'text' && bodyEditor.api.format()) { flushBody(); renderBodies(); scheduleSave(); }
}

// ========================================================== response panels
let lastResult = null;
function renderResponsePanels(result) {
  lastResult = result;
  const resp = result.response;
  const bodyBox = $('#r-body');
  let view;
  try { view = localStorage.getItem('pyhttp.rv.view') || undefined; } catch { view = undefined; }
  let wrap = true;
  try { wrap = localStorage.getItem('pyhttp.rv.wrap') !== '0'; } catch { /* ignore */ }
  if (resp) mountViewer(bodyBox, resp, { view, wrap, fullBody: resp.truncated && result.history_id ? () => fetchFullBody(result.history_id) : null });
  else { bodyBox.classList.remove('rv'); bodyBox.innerHTML = `<pre class="mono rv-pre err-text">${esc(result.error || 'Unknown error')}</pre>`; }
  // headers
  const hs = resp ? resp.headers : [];
  $('#n-r-headers').textContent = hs.length ? String(hs.length) : '';
  $('#r-headers').innerHTML = hs.length ? `<table class="htable"><tbody>${hs.map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td class="v">${esc(v)}</td></tr>`).join('')}</tbody></table>` +
    (resp && resp.hops && resp.hops.length ? `<div class="hops"><div class="muted small">Redirects (${resp.hops.length})</div>${resp.hops.map((h) => `<div class="hop mono"><span class="status ${codeClass(h.status_code)}">${h.status_code}</span> ${esc(h.url)} → ${esc(h.location)}</div>`).join('')}</div>` : '') +
    (resp ? `<div class="muted small" style="margin-top:8px">${esc(resp.http_version || '')}${resp.content_type ? ' · ' + esc(resp.content_type) : ''}</div>` : '')
    : '<div class="muted small">No headers</div>';
  // cookies
  const cs = resp ? (resp.cookies || []) : [];
  $('#n-r-cookies').textContent = cs.length ? String(cs.length) : '';
  $('#r-cookies').innerHTML = cs.length ? `<table class="htable"><thead><tr><th>Name</th><th>Value</th><th>Domain</th><th>Path</th><th>Expires</th><th></th></tr></thead><tbody>${cs.map((c) => `<tr><td class="k">${esc(c.name)}</td><td class="v">${esc(c.value)}</td><td>${esc(c.domain)}</td><td>${esc(c.path)}</td><td>${c.expires ? esc(fmtDate(c.expires)) : 'session'}</td><td>${c.secure ? '<span class="chip">secure</span>' : ''}${c.http_only ? '<span class="chip">httpOnly</span>' : ''}</td></tr>`).join('')}</tbody></table><p class="muted small">Cookies set by this response. They are stored in the cookie jar and sent with later requests to the same domain. <button class="link small" id="r-open-cookies">Manage cookies</button></p>`
    : '<div class="muted small">The response set no cookies. <button class="link small" id="r-open-cookies">Open the cookie jar</button></div>';
  const oc = $('#r-open-cookies'); if (oc) oc.addEventListener('click', openCookieManager);
  // tests
  const tests = result.tests || [];
  const failed = tests.filter((t) => !t.passed).length;
  const badge = $('#r-tests-badge');
  badge.hidden = !tests.length;
  badge.className = 'tests-badge ' + (failed ? 'fail' : 'pass');
  badge.textContent = tests.length ? (failed ? `${failed} of ${tests.length} test${tests.length === 1 ? '' : 's'} failed` : `${tests.length} test${tests.length === 1 ? '' : 's'} passed`) : '';
  $('#n-r-tests').textContent = tests.length ? `${tests.length - failed}/${tests.length}` : '';
  $('#n-r-tests').className = 'tab-n ' + (tests.length ? (failed ? 'fail' : 'pass') : '');
  const ext = result.extracted || [];
  const pre = result.pre_vars || [];
  const sc = result.scripts || {};
  const scriptHtml = ['pre', 'pre_body', 'test', 'test_body'].filter((k) => sc[k]).map((k) => {
    const rep = sc[k];
    const logs = (rep.logs || []).map((l) => `<div class="mono small">${esc(l)}</div>`).join('');
    const title = `${k.startsWith('pre') ? 'Pre-request' : 'Test'} script${k.endsWith('_body') ? ` · ${esc(rep.body_name || 'this body')}` : ''}`;
    return `<h4 class="rt-h">${title}</h4>${rep.ok ? `<div class="muted small">ran OK${rep.timed_out ? ' (timed out)' : ''}</div>` : `<div class="err-text small mono">${esc(rep.error || 'error')}</div>`}${logs ? `<div class="script-log">${logs}</div>` : ''}`;
  }).join('');
  $('#r-tests').innerHTML = (tests.length ? `<ul class="test-results">${tests.map((t) => `<li class="${t.passed ? 'pass' : 'fail'}"><span class="mark">${t.passed ? '✓' : '✕'}</span><span class="tn">${esc(t.name)}</span><span class="note muted small">${esc(t.note || '')}</span></li>`).join('')}</ul>`
    : '<div class="muted small">No tests on this request. Add some in the <button class="link small" id="r-goto-tests">Tests</button> tab (status, headers, JSON values, response time).</div>') + scriptHtml +
    (ext.length ? `<h4 class="rt-h">Extracted variables</h4><ul class="test-results">${ext.map((e) => `<li class="${e.ok ? 'pass' : 'fail'}"><span class="mark">${e.ok ? '✓' : '✕'}</span><span class="tn mono">${esc(e.name)} = ${esc(e.ok ? (e.value.length > 120 ? e.value.slice(0, 117) + '…' : e.value) : '')}</span><span class="note muted small">${esc(e.ok ? `saved ${e.scope === 'env' ? 'in the environment' : 'globally'}` : e.note)}</span></li>`).join('')}</ul>` : '') +
    (pre.length ? `<h4 class="rt-h">Pre-request variables</h4><ul class="test-results">${pre.map((p) => `<li class="pass"><span class="mark">•</span><span class="tn mono">${esc(p.name)} = ${esc(p.value)}</span><span class="note muted small">${esc(p.scope)}</span></li>`).join('')}</ul>` : '');
  const gt = $('#r-goto-tests'); if (gt) gt.addEventListener('click', () => showTab($('#editor-tabs'), 'tests'));
  // request as sent
  const req = result.request || {};
  const unresolved = req.unresolved || [];
  $('#r-request').innerHTML = `<div class="req-sent mono">
      <div class="line"><span class="method m-${esc(req.method)}">${esc(req.method)}</span> ${esc(req.url || '')}</div>
      ${(req.params || []).length ? `<div class="muted small">Query params: ${req.params.map(([k, v]) => esc(k) + '=' + esc(v)).join('&amp;')}</div>` : ''}
      <table class="htable"><tbody>${(req.headers || []).map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td class="v">${esc(v)}</td></tr>`).join('')}</tbody></table>
      ${req.auth_type && req.auth_type !== 'none' ? `<div class="muted small">Auth: ${esc(AUTH_LABELS[req.auth_type] || req.auth_type)}${req.auth_from ? ` (from folder ${esc(req.auth_from)})` : ''}</div>` : ''}
      ${unresolved.length ? `<div class="warn-box">⚠ Unresolved variables: ${unresolved.map((u) => `<code>{{${esc(u)}}}</code>`).join(' ')}</div>` : ''}
      ${(req.files || []).length ? `<div class="muted small">Files: ${req.files.map((f) => `${esc(f.field)} = ${esc(f.filename)} (${fmtBytes(f.size)})`).join(', ')}</div>` : ''}
      ${req.body ? `<pre class="rv-pre mono">${esc(req.body)}</pre>` : '<div class="muted small">No body</div>'}
    </div>`;
  afterResponseForViews(result);
  $('#r-save').disabled = !resp;
  $('#r-example').disabled = !resp || !!resp.is_binary;
}

// The whole body of a call whose response was too large for the viewer (kept with its history entry).
async function fetchFullBody(historyId) {
  const r = await fetch(`/api/history/${encodeURIComponent(historyId)}/body`);
  if (!r.ok) throw new Error(r.status === 404 ? 'the whole body was not kept for this call' : `${r.status} ${r.statusText}`);
  return r.text();
}

// ================================================================ history
const hist = { q: '', status: '', loaded: false, timer: null, compare: null };
// reload the request history after a send, but only while it is on screen
function refreshHistoryList() {
  const panel = $('#runs-panels .tab-panel[data-panel="requests"]');
  if (panel && !panel.hidden && runsPanelShown()) loadHistoryList();
}
const HISTORY_PAGE = 200;
async function loadHistoryList(before = null) {
  const ul = $('#history-list');
  if (!before) renderCompareBanner();
  try {
    const items = await api('/history?q=' + encodeURIComponent(hist.q) + '&status=' + encodeURIComponent(hist.status)
      + '&limit=' + HISTORY_PAGE + (before ? '&before=' + encodeURIComponent(before) : ''));
    hist.loaded = true;
    if (!items.length && !before) { ul.innerHTML = `<li class="empty small">${hist.q || hist.status ? 'Nothing matches.' : 'Nothing sent yet. Every request you send or run appears here.'}</li>`; return; }
    const oldMore = $('.load-more', ul);
    let lastDay = oldMore ? oldMore.dataset.day : '';
    if (oldMore) oldMore.remove();
    const keep = before ? ul.scrollTop : 0;
    const html = items.map((h) => {
      const day = new Date(h.ts * 1000).toLocaleDateString();
      const head = day !== lastDay ? `<li class="h-day">${esc(day)}</li>` : '';
      lastDay = day;
      const picked = hist.compare && hist.compare.id === h.id;
      return head + `<li class="req-item h-item${picked ? ' compare-base' : ''}" data-hid="${h.id}" title="${esc(h.request_name)}${h.body_name ? ' · ' + esc(h.body_name) : ''} (${esc(h.source)}${h.duration_ms != null ? ', ' + esc(fmtMs(h.duration_ms)) : ''})">
        <span class="h-time muted">${esc(new Date(h.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))}</span>
        <span class="method m-${esc(h.method)}">${esc(h.method)}</span>
        <div style="min-width:0"><div class="name">${esc(h.request_name || h.url)}${h.body_name ? ` <span class="bn">· ${esc(h.body_name)}</span>` : ''}</div><div class="sub">${esc(h.url)}</div></div>
        <span class="h-code ${codeClass(h.status_code)}">${h.status_code ?? 'ERR'}</span>
        <button class="h-resend" title="Send again" aria-label="Send again">↻</button>
        <button class="more" title="More actions" aria-label="More actions" aria-haspopup="menu">⋯</button></li>`;
    }).join('') + (items.length === HISTORY_PAGE ? `<li class="load-more" data-before="${items[items.length - 1].ts}" data-day="${esc(lastDay)}"><button class="link small">Show older entries…</button></li>` : '');
    if (before) ul.insertAdjacentHTML('beforeend', html); else ul.innerHTML = html;
    if (before) ul.scrollTop = keep;
    const lm = $('.load-more', ul);
    if (lm) $('button', lm).addEventListener('click', () => loadHistoryList(Number(lm.dataset.before)));
    $$('.h-item:not([data-bound])', ul).forEach((li) => {
      li.dataset.bound = '1';
      li.addEventListener('click', () => {
        if (hist.compare && hist.compare.id !== li.dataset.hid) { const a = hist.compare.id; setCompareBase(null); openCompare(a, li.dataset.hid); return; }
        openHistoryEntry(li.dataset.hid);
      });
      $('.h-resend', li).addEventListener('click', (e) => { e.stopPropagation(); resendHistory(li.dataset.hid, e.currentTarget); });
      $('.more', li).addEventListener('click', (e) => { e.stopPropagation(); openHistoryMenu(li.dataset.hid, e.currentTarget); });
      li.addEventListener('contextmenu', (e) => { e.preventDefault(); openHistoryMenu(li.dataset.hid, li, { x: e.clientX, y: e.clientY }); });
    });
  } catch (e) { ul.innerHTML = `<li class="empty small">${esc(e.message)}</li>`; }
}
function openHistoryMenu(hid, anchor, at) {
  openMenu('h:' + hid, anchor, at, [
    { act: 'view', icon: '👁', label: 'View response', run: () => openHistoryEntry(hid) },
    { act: 'views', icon: '◧', label: 'Show in its views', run: () => openHistoryEntry(hid, 'views') },
    { act: 'views-out', icon: '↗', label: 'Open its views in the browser', run: () => openHistoryViews(hid) },
    { act: 'resend', icon: '↻', label: 'Send again', run: () => resendHistory(hid) },
    { act: 'compare', icon: '⇄', label: 'Compare with…', run: () => pickCompare(hid) },
    { act: 'restore', icon: '↩', label: 'Open as new request', run: () => restoreHistory(hid) },
    { act: 'open', icon: '→', label: 'Go to the saved request', run: () => gotoHistoryRequest(hid) },
    '-',
    { act: 'del', icon: '🗑', label: 'Remove from history', danger: true, run: async () => { await api('/history/' + hid, { method: 'DELETE' }); if (hist.compare && hist.compare.id === hid) setCompareBase(null); loadHistoryList(); } },
  ]);
}
async function resendHistory(hid, btn = null) {
  if (btn) { btn.disabled = true; btn.classList.add('spinning'); }
  try {
    const r = await api(`/history/${hid}/resend`, { method: 'POST' });
    toast(r.status_code != null ? `Sent again: ${r.status_code}` : `Sent again: ${r.error || r.status}`, r.status_code != null && r.status_code < 400 ? 'ok' : 'error');
    await loadHistoryList();
    pollConsole();
    return r;
  } catch (e) { toast(e.message, 'error'); return null; }
  finally { if (btn) { btn.disabled = false; btn.classList.remove('spinning'); } }
}
async function gotoHistoryRequest(hid) {
  const h = await api('/history/' + hid);
  if (historyRequestId(h)) openRequest(historyRequestId(h));
  else { toast('That request no longer exists; opening a copy', 'error'); restoreHistory(hid, h); }
}
async function restoreHistory(hid, h = null) {
  h = h || await api('/history/' + hid);
  await saveNow();
  const snap = h.snapshot || {};
  const created = await api('/requests', { method: 'POST', body: { ...snap, name: (snap.name || 'Request') + ' (from history)', folder_id: snap.folder_id || null } });
  await loadRequests();
  await openRequest(created.id);
  toast('Opened a copy from history', 'ok');
}
async function openHistoryEntry(hid, tab = '') {
  const h = await api('/history/' + hid);
  const d = h.detail || {};
  const resp = d.response || null;
  const tests = d.tests || [];
  const failed = tests.filter((t) => !t.passed).length;
  const headers = (resp && resp.headers) || [];
  const bits = [fmtMs(h.duration_ms), resp ? fmtBytes(resp.size) : '', fmtDate(h.ts), h.source + (h.environment ? ' · ' + h.environment : ''), h.body_name ? 'body “' + h.body_name + '”' : ''].filter(Boolean);
  openModal(`${h.method} ${h.request_name || h.url}`, `
    <div class="hd-sum"><span class="status ${codeClass(h.status_code)}">${h.status_code == null ? 'Error' : esc(`${h.status_code} ${d.reason || ''}`.trim())}</span>${bits.map((b) => `<span class="muted small">${esc(b)}</span>`).join('')}</div>
    <div class="hd-url mono small muted" title="${esc(h.url)}">${esc(h.url)}</div>
    ${h.error ? `<pre class="mono hd-error">${esc(h.error)}</pre>` : ''}
    <div class="tabs hd-tabs" id="hd-tabs">
      <button data-tab="body" class="active">Body</button>
      <button data-tab="views" title="This response in the request's own HTML pages">Views<span class="tab-n" id="hd-n-views"></span></button>
      <button data-tab="headers">Headers<span class="tab-n">${headers.length || ''}</span></button>
      <button data-tab="tests">Tests<span class="tab-n ${tests.length ? (failed ? 'fail' : 'pass') : ''}">${tests.length ? `${tests.length - failed}/${tests.length}` : ''}</span></button>
      <button data-tab="request">Request</button>
    </div>
    <div class="tab-panels hd-panels" id="hd-panels">
      <div class="tab-panel" data-panel="body"><div id="h-viewer"></div></div>
      <div class="tab-panel vw-host" data-panel="views" hidden><div id="h-views" class="vw-host"><div class="muted small">Loading…</div></div></div>
      <div class="tab-panel" data-panel="headers" hidden>${headers.length ? `<table class="htable"><tbody>${headers.map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td class="v">${esc(v)}</td></tr>`).join('')}</tbody></table>` : '<div class="muted small">No headers</div>'}</div>
      <div class="tab-panel" data-panel="tests" hidden>${tests.length ? `<ul class="test-results">${tests.map((t) => `<li class="${t.passed ? 'pass' : 'fail'}"><span class="mark">${t.passed ? '✓' : '✕'}</span><span class="tn">${esc(t.name)}</span><span class="note muted small">${esc(t.note || '')}</span></li>`).join('')}</ul>` : '<div class="muted small">This call ran no tests.</div>'}</div>
      <div class="tab-panel" data-panel="request" hidden><pre class="mono hd-pre">${esc(formatRequest(d.request))}</pre></div>
    </div>
  `, [
    { label: '↻ Send again & compare', onClick: async (e) => { const r = await resendHistory(hid, e.currentTarget); if (r && r.id) { closeModal(); openCompare(hid, r.id); } } },
    { label: '⇄ Compare with…', onClick: () => { closeModal(); pickCompare(hid); } },
    { label: 'Open as new request', onClick: () => { closeModal(); restoreHistory(hid, h); } },
    { label: 'Close', cls: 'primary hd-close', onClick: closeModal },
  ]);
  $('#modal .modal').classList.add('wide');
  const [bResend, bCompare, bRestore] = $$('#modal-foot button');
  bResend.id = 'h-resend'; bCompare.id = 'h-compare'; bRestore.id = 'h-restore';
  bResend.title = 'Send this request again and compare the new response with this one';
  bCompare.title = 'Pick another history entry to compare with';
  bindTabs($('#hd-tabs'), $('#hd-panels'));
  if (resp) mountViewer($('#h-viewer'), resp, { fullBody: resp.truncated ? () => fetchFullBody(hid) : null });
  else $('#h-viewer').innerHTML = '<div class="muted small">No response</div>';
  // the request's views, drawn with the response this entry holds
  const views = await historyViews(h);
  const box = $('#h-views');
  if (!box || !box.isConnected) return;   // closed meanwhile
  $('#hd-n-views').textContent = views.length ? String(views.length) : '';
  if (views.length && resp) {
    mountViews(box, { result: historyResult(h), views, name: h.request_name || 'response' });
  } else {
    const saved = historyRequestId(h);
    box.innerHTML = `<div class="vw-empty">${VIEW_ICON}
      <h4>${views.length ? 'This call has no response to show' : 'No views on this request yet'}</h4>
      ${views.length ? '' : `<p>A view is an HTML page of your own that draws the response: a chart, a table, a dashboard. Add one to the request and every call in the history, this one included, opens in it.</p>
      ${saved ? '<div class="vw-actions"><button class="primary" id="h-add-views">Add views to this request</button></div>' : '<p class="muted small">The saved request of this call no longer exists. “Open as new request” brings it back.</p>'}`}</div>`;
    const add = $('#h-add-views');
    if (add) add.addEventListener('click', async () => {
      closeModal();
      await openRequest(saved);
      showTab($('#editor-tabs'), 'views');
      openViewsDialog();
    });
  }
  if (tab === 'views' || (!tab && views.length && resp)) showTab($('#hd-tabs'), 'views');
}

// ---- compare two history entries
async function pickCompare(hid) {
  const h = await api('/history/' + hid).catch(() => null);
  if (!h) return;
  setCompareBase({ id: hid, label: `${h.method} ${h.request_name || h.url} · ${new Date(h.ts * 1000).toLocaleTimeString()}` });
  showTab($('#runs-tabs'), 'requests');
  if (!runsPanelShown()) setRunsPanel(true);
  loadHistoryList();
}
function setCompareBase(c) {
  hist.compare = c;
  renderCompareBanner();
  $$('#history-list .h-item').forEach((li) => li.classList.toggle('compare-base', !!c && li.dataset.hid === c.id));
}
function renderCompareBanner() {
  const box = $('#history-compare');
  if (!box) return;
  box.hidden = !hist.compare;
  if (!hist.compare) { box.innerHTML = ''; return; }
  box.innerHTML = `<span>⇄ Click another entry to compare with <strong>${esc(hist.compare.label)}</strong></span><button class="link small" id="h-compare-cancel">Cancel</button>`;
  $('#h-compare-cancel', box).addEventListener('click', () => setCompareBase(null));
}
// stable text for diffing: JSON pretty-printed with sorted keys, anything else as-is
function diffText(resp) {
  if (!resp) return '';
  const text = resp.body_text || '';
  if (resp.is_json) {
    const sortKeys = (v) => (Array.isArray(v) ? v.map(sortKeys) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortKeys(v[k])])) : v);
    try { return JSON.stringify(sortKeys(JSON.parse(text)), null, 2); } catch { /* not JSON after all */ }
  }
  return text;
}
// line diff (LCS); falls back to a positional compare for very large inputs
function lineDiff(a, b) {
  const A = a.split('\n');
  const B = b.split('\n');
  let pre = 0;
  while (pre < A.length && pre < B.length && A[pre] === B[pre]) pre++;
  let suf = 0;
  while (suf < A.length - pre && suf < B.length - pre && A[A.length - 1 - suf] === B[B.length - 1 - suf]) suf++;
  const a2 = A.slice(pre, A.length - suf);
  const b2 = B.slice(pre, B.length - suf);
  const out = A.slice(0, pre).map((s) => ({ t: ' ', s }));
  const n = a2.length;
  const m = b2.length;
  if (n * m > 4e6) {
    a2.forEach((s) => out.push({ t: '-', s }));
    b2.forEach((s) => out.push({ t: '+', s }));
  } else {
    const w = m + 1;
    const L = new Uint32Array((n + 1) * w);
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i * w + j] = a2[i] === b2[j] ? L[(i + 1) * w + j + 1] + 1 : Math.max(L[(i + 1) * w + j], L[i * w + j + 1]);
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a2[i] === b2[j]) { out.push({ t: ' ', s: a2[i] }); i++; j++; }
      else if (L[(i + 1) * w + j] >= L[i * w + j + 1]) out.push({ t: '-', s: a2[i++] });
      else out.push({ t: '+', s: b2[j++] });
    }
    while (i < n) out.push({ t: '-', s: a2[i++] });
    while (j < m) out.push({ t: '+', s: b2[j++] });
  }
  A.slice(A.length - suf).forEach((s) => out.push({ t: ' ', s }));
  return out;
}
function renderDiff(ops) {
  const changed = ops.some((o) => o.t !== ' ');
  if (!changed) return '<div class="muted small">Identical.</div>';
  const html = [];
  const CONTEXT = 3;
  let i = 0;
  while (i < ops.length) {
    if (ops[i].t !== ' ') { html.push(`<div class="d-line d-${ops[i].t === '+' ? 'add' : 'del'}"><span class="d-mark">${ops[i].t}</span>${esc(ops[i].s)}</div>`); i++; continue; }
    let j = i;
    while (j < ops.length && ops[j].t === ' ') j++;
    const run = j - i;
    const head = i === 0 ? 0 : CONTEXT;
    const tail = j === ops.length ? 0 : CONTEXT;
    if (run > head + tail + 2) {
      for (let k = i; k < i + head; k++) html.push(`<div class="d-line"><span class="d-mark"> </span>${esc(ops[k].s)}</div>`);
      html.push(`<div class="d-skip">… ${run - head - tail} unchanged line(s) …</div>`);
      for (let k = j - tail; k < j; k++) html.push(`<div class="d-line"><span class="d-mark"> </span>${esc(ops[k].s)}</div>`);
    } else for (let k = i; k < j; k++) html.push(`<div class="d-line"><span class="d-mark"> </span>${esc(ops[k].s)}</div>`);
    i = j;
  }
  return `<div class="diff mono">${html.join('')}</div>`;
}
async function openCompare(aId, bId) {
  let a;
  let b;
  try { [a, b] = await Promise.all([api('/history/' + aId), api('/history/' + bId)]); } catch (e) { toast(e.message, 'error'); return; }
  if (a.ts > b.ts) [a, b] = [b, a]; // older on the left / as "−"
  const side = (h) => ({
    label: `${h.method} ${h.request_name || h.url}`, status_code: h.status_code, duration_ms: h.duration_ms,
    response: (h.detail || {}).response, request: (h.detail || {}).request,
    when: `${esc(fmtDate(h.ts))}<br>${esc(h.source)}${h.environment ? ' · ' + esc(h.environment) : ''}`,
  });
  showCompare(side(a), side(b));
}
// Two responses of the request on screen (one per body, or per iteration), straight from the
// response strip. They are compared from memory, so the whole body takes part, not the cut copy
// the history keeps.
function compareResults(a, b) {
  const side = (x) => ({ label: x.label, status_code: x.result.status_code, duration_ms: x.result.duration_ms,
    response: x.result.response, request: x.result.request, when: x.result.error ? esc(x.result.error) : '' });
  showCompare(side(a), side(b));
}
// a, b: { label, status_code, duration_ms, response, request, when (HTML) }; a is shown as "−", b as "+"
function showCompare(a, b) {
  const ra = a.response;
  const rb = b.response;
  const hdr = (r) => ((r || {}).headers || []).filter(([k]) => !/^(date|age|expires|x-request-id|x-amzn-requestid|cf-ray|set-cookie|etag|last-modified|content-length)$/i.test(k)).map(([k, v]) => `${k.toLowerCase()}: ${v}`).sort().join('\n');
  const bodyOps = lineDiff(diffText(ra), diffText(rb));
  const adds = bodyOps.filter((o) => o.t === '+').length;
  const dels = bodyOps.filter((o) => o.t === '-').length;
  const cell = (h, r) => `<td><span class="status ${codeClass(h.status_code)}">${h.status_code ?? 'ERR'}</span></td><td>${fmtMs(h.duration_ms)}</td><td>${r ? fmtBytes(r.size) : '—'}</td><td class="muted small">${h.when || ''}</td>`;
  openModal('Compare responses', `
    <table class="cmp-table">
      <tr><th></th><th>Status</th><th>Time</th><th>Size</th><th>${a.when || b.when ? 'When' : ''}</th></tr>
      <tr class="cmp-a"><th>− ${esc(a.label)}</th>${cell(a, ra)}</tr>
      <tr class="cmp-b"><th>+ ${esc(b.label)}</th>${cell(b, rb)}</tr>
    </table>
    <div class="detail-section"><h4>Response body <span class="muted small">${adds || dels ? `<span class="ok-text">+${adds}</span> <span class="err-text">−${dels}</span> lines${ra && ra.is_json ? ' · JSON keys sorted' : ''}` : ''}</span></h4>${renderDiff(bodyOps)}</div>
    <div class="detail-section"><h4>Response headers <span class="muted small">(volatile ones such as Date are left out)</span></h4>${renderDiff(lineDiff(hdr(ra), hdr(rb)))}</div>
    <div class="detail-section"><h4>Request as sent</h4>${renderDiff(lineDiff(formatRequest(a.request), formatRequest(b.request)))}</div>
  `, [{ label: 'Close', onClick: closeModal }]);
  $('#modal .modal').classList.add('wide');
}

// ============================================================ example responses
// A response pinned to its request (Postman's "examples"). The server keeps them in the request's
// options, so they travel with exports and Git sync; they are changed through /examples calls only.
const examplesOf = (r) => ((r && r.options && r.options.examples) || []);
function applyExamples(r, out) {
  r.options = out.options;
  if (state.current === r) renderExamplesPanel();
}
function renderExamplesPanel() {
  const box = $('#examples-panel');
  const r = state.current;
  if (!box || !r) return;
  const list = examplesOf(r);
  $('#n-examples').textContent = list.length ? String(list.length) : '';
  box.innerHTML = `<div class="vwp exp">
    <p class="muted small">Responses pinned to this request. They are listed in the documentation export, travel with exports and Git sync,
      and each one can become the answer of a mock route. Send the request, then use the pin button in the response bar (or the button below) to add one.</p>
    ${list.length ? `<ul class="exp-list">${list.map((e) => `<li data-eid="${esc(e.id)}">
        <span class="status ${codeClass(e.status_code)}">${esc(String(e.status_code))}</span>
        <span class="exp-name" title="${esc(e.name)}">${esc(e.name)}</span>
        <span class="muted small exp-meta">${esc([e.content_type, fmtBytes((e.body || '').length) + (e.truncated ? ' (cut)' : ''), e.body_name ? 'body “' + e.body_name + '”' : '', fmtDate(e.created_at)].filter(Boolean).join(' · '))}</span>
        <span class="exp-acts"><button class="link small" data-act="view">View</button><button class="link small" data-act="mock" title="Add a mock route that answers with this example">Mock…</button><button class="link small" data-act="rename">Rename</button><button class="link small danger-text" data-act="del">Delete</button></span></li>`).join('')}</ul>`
      : '<div class="vwp-none muted small">No examples on this request yet.</div>'}
    <div class="vw-actions"><button id="exp-pin" class="${list.length ? '' : 'primary'}">Pin the response on screen</button></div></div>`;
  $('#exp-pin').addEventListener('click', pinExample);
  $$('.exp-list li', box).forEach((li) => {
    const e = list.find((x) => x.id === li.dataset.eid);
    $$('button[data-act]', li).forEach((b) => b.addEventListener('click', () => exampleAction(r, e, b.dataset.act)));
  });
}
async function pinExample() {
  if (!state.current) return;
  await saveNow();   // a draft has no id yet
  const r = state.current;
  const rs = responsesFor();
  const result = rs && rs.byKey[rs.selected];
  if (!r.id) { toast('Give the request a URL or a name first, so it can be saved', 'error'); return; }
  if (!result || !result.response) { toast('There is no response to pin yet: send the request first', 'error'); return; }
  const resp = result.response;
  if (resp.is_binary) { toast('A binary response cannot be pinned as an example', 'error'); return; }
  const suggestion = `${result.status_code} ${result.reason || ''}`.trim() + (result.body_name ? ` · ${result.body_name}` : '');
  const name = await promptDialog('Pin as example', { label: 'Name of the example', value: suggestion, okLabel: 'Pin',
    message: 'The status, headers and body of this response are saved with the request.' });
  if (!name) return;
  try {
    const out = await api(`/requests/${r.id}/examples`, { method: 'POST', body: {
      name, status_code: result.status_code, reason: result.reason || '', headers: resp.headers || [],
      content_type: resp.content_type || '', body: resp.body_text || '', truncated: !!resp.truncated, body_name: result.body_name || '' } });
    applyExamples(r, out);
    toast(`Pinned “${out.example.name}”`, 'ok', { label: 'Show examples', onClick: () => showTab($('#editor-tabs'), 'examples') });
  } catch (e) { toast(e.message, 'error'); }
}
async function exampleAction(r, e, act) {
  const base = `/requests/${r.id}/examples/${encodeURIComponent(e.id)}`;
  try {
    if (act === 'view') {
      openModal(`Example: ${e.name}`, `
        <div class="hd-sum"><span class="status ${codeClass(e.status_code)}">${esc(`${e.status_code} ${e.reason || ''}`.trim())}</span>
          <span class="muted small">${esc(fmtBytes((e.body || '').length))}</span><span class="muted small">pinned ${esc(fmtDate(e.created_at))}</span></div>
        <div class="tabs hd-tabs" id="ex-tabs"><button data-tab="body" class="active">Body</button><button data-tab="headers">Headers<span class="tab-n">${e.headers.length || ''}</span></button></div>
        <div class="tab-panels hd-panels" id="ex-panels">
          <div class="tab-panel" data-panel="body"><div id="ex-viewer"></div></div>
          <div class="tab-panel" data-panel="headers" hidden>${e.headers.length ? `<table class="htable"><tbody>${e.headers.map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td class="v">${esc(v)}</td></tr>`).join('')}</tbody></table>` : '<div class="muted small">No headers</div>'}</div>
        </div>`, [{ label: 'Close', cls: 'primary', onClick: closeModal }]);
      $('#modal .modal').classList.add('wide');
      bindTabs($('#ex-tabs'), $('#ex-panels'));
      mountViewer($('#ex-viewer'), { body_text: e.body || '', content_type: e.content_type || '', headers: e.headers, size: (e.body || '').length, truncated: !!e.truncated });
    } else if (act === 'rename') {
      const name = await promptDialog('Rename example', { label: 'Name', value: e.name });
      if (name && name !== e.name) applyExamples(r, await api(base, { method: 'PATCH', body: { name } }));
    } else if (act === 'del') {
      if (!await confirmDialog('Delete this example?', `${strong(e.name)} is removed from the request. Mock routes made from it stay.`, { okLabel: 'Delete', danger: true })) return;
      applyExamples(r, await api(base, { method: 'DELETE' }));
    } else if (act === 'mock') {
      await loadMocks(false);
      const NEW = '__new';
      const pick = await selectDialog('Mock route from this example', {
        label: 'Mock server', okLabel: 'Add route', value: mocks.list.length ? mocks.list[0].id : NEW,
        options: [...mocks.list.map((m) => ({ value: m.id, label: m.name })), { value: NEW, label: `＋ New mock server “${r.name}”` }],
        message: `Adds a route for ${strong(r.method)} and the path of this request that answers with ${strong(e.name)}.`,
      });
      if (!pick) return;
      const out = await api(base + '/mock', { method: 'POST', body: pick === NEW ? {} : { mock_id: pick } });
      await loadMocks(false);
      toast(`Route added to “${out.mock.name}”: ${out.route.method} ${out.route.path}`, 'ok', { label: 'Open mocks', onClick: () => openMocksModal() });
    }
  } catch (err) { toast(err.message, 'error'); }
}

// ================================================================ cookies
async function openCookieManager() {
  let cookies = await api('/cookies');
  const render = () => {
    const box = $('#cookie-box');
    if (!box) return;
    const domains = [...new Set(cookies.map((c) => c.domain))].sort();
    box.innerHTML = cookies.length ? domains.map((d) => `
      <div class="cookie-domain"><div class="cd-head"><strong>${esc(d)}</strong><span class="muted small">${cookies.filter((c) => c.domain === d).length} cookie(s)</span><span class="grow"></span><button class="link small danger" data-deldom="${esc(d)}">Delete all</button></div>
      <table class="htable"><thead><tr><th>Name</th><th>Value</th><th>Path</th><th>Expires</th><th></th><th></th></tr></thead><tbody>
      ${cookies.filter((c) => c.domain === d).map((c) => `<tr><td class="k">${esc(c.name)}</td><td class="v"><input class="mono ck-val" data-domain="${esc(c.domain)}" data-name="${esc(c.name)}" data-path="${esc(c.path)}" value="${esc(c.value)}"></td><td>${esc(c.path)}</td><td>${c.expires ? esc(fmtDate(c.expires)) : 'session'}</td><td>${c.secure ? '<span class="chip">secure</span>' : ''}${c.http_only ? '<span class="chip">httpOnly</span>' : ''}</td><td><button class="kv-del" data-del="1" data-domain="${esc(c.domain)}" data-name="${esc(c.name)}" data-path="${esc(c.path)}" title="Delete" aria-label="Delete cookie">✕</button></td></tr>`).join('')}
      </tbody></table></div>`).join('') : '<div class="empty small">The cookie jar is empty. Cookies set by responses are stored here and sent with later requests to the same domain, like a browser.</div>';
    $$('[data-deldom]', box).forEach((b) => b.addEventListener('click', async () => {
      if (!await confirmDialog('Delete cookies?', `All cookies for ${strong(b.dataset.deldom)} will be removed.`, { okLabel: 'Delete', danger: true })) return;
      await api('/cookies?domain=' + encodeURIComponent(b.dataset.deldom), { method: 'DELETE' });
      cookies = await api('/cookies'); render();
    }));
    $$('[data-del]', box).forEach((b) => b.addEventListener('click', async () => {
      await api(`/cookies?domain=${encodeURIComponent(b.dataset.domain)}&name=${encodeURIComponent(b.dataset.name)}&path=${encodeURIComponent(b.dataset.path)}`, { method: 'DELETE' });
      cookies = await api('/cookies'); render();
    }));
    $$('.ck-val', box).forEach((inp) => inp.addEventListener('change', async () => {
      const c = cookies.find((x) => x.domain === inp.dataset.domain && x.name === inp.dataset.name && x.path === inp.dataset.path);
      if (!c) return;
      cookies = await api('/cookies', { method: 'PUT', body: { ...c, value: inp.value } });
      toast('Cookie updated', 'ok');
    }));
  };
  openModal('Cookies', `
    <p class="muted small">Stored cookies are sent automatically with matching requests (turn this off per request under Settings, or globally in ⚙ Settings). Edit a value and press Enter to change it.</p>
    <div class="row tight"><input id="ck-domain" placeholder="domain, e.g. api.example.com" class="mono"><input id="ck-name" placeholder="name" class="mono"><input id="ck-value" placeholder="value" class="mono grow"><button id="ck-add">Add cookie</button></div>
    <div id="cookie-box"></div>
  `, [{ label: 'Clear all', cls: 'danger', onClick: async () => {
    if (!cookies.length) return;
    if (!await confirmDialog('Clear the cookie jar?', 'Every stored cookie will be removed.', { okLabel: 'Clear all', danger: true })) return;
    await api('/cookies', { method: 'DELETE' }); cookies = []; render();
  } }, { label: 'Done', cls: 'primary', onClick: closeModal }]);
  render();
  $('#ck-add').addEventListener('click', async () => {
    const domain = $('#ck-domain').value.trim(), name = $('#ck-name').value.trim();
    if (!domain || !name) { toast('Domain and name are required', 'error'); return; }
    try {
      cookies = await api('/cookies', { method: 'PUT', body: { domain, name, value: $('#ck-value').value, path: '/' } });
      $('#ck-name').value = ''; $('#ck-value').value = '';
      render();
    } catch (e) { toast(e.message, 'error'); }
  });
}

// ========================================================== code snippets
async function openCodeModal() {
  if (!state.current) return;
  const data = readEditor();
  let resolved;
  try { resolved = await api('/resolve?body_id=' + encodeURIComponent(state.activeBodyId || ''), { method: 'POST', body: { ...data, id: state.current.id, folder_id: state.current.folder_id || null } }); }
  catch (e) { toast('Could not resolve the request: ' + e.message, 'error'); return; }
  let lang = 'curl';
  try { lang = localStorage.getItem('pyhttp.snippet') || 'curl'; } catch { /* ignore */ }
  openModal('Code snippet', `
    <div class="row tight">
      <select id="snip-lang">${SNIPPET_LANGS.map(([v, l]) => `<option value="${v}" ${v === lang ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <span class="muted small grow">Variables and auth are resolved; secrets appear in clear text.${resolved.unresolved && resolved.unresolved.length ? ` ⚠ Unresolved: ${resolved.unresolved.map((u) => `{{${esc(u)}}}`).join(', ')}` : ''}</span>
      <button id="snip-copy">Copy</button>
    </div>
    <pre id="snip-out" class="mono snippet"></pre>`, [{ label: 'Close', onClick: closeModal }]);
  const render = () => { $('#snip-out').textContent = snippet($('#snip-lang').value, resolved); };
  $('#snip-lang').addEventListener('change', () => { render(); try { localStorage.setItem('pyhttp.snippet', $('#snip-lang').value); } catch { /* ignore */ } });
  $('#snip-copy').addEventListener('click', async () => { if (await copyText($('#snip-out').textContent)) toast('Snippet copied', 'ok'); });
  render();
}

// ======================================================== import / export
function importDialog() {
  openModal('Import', `
    <div class="import-grid">
      <button id="imp-file" class="import-card"><strong>From a file</strong><span class="muted small">pyhttp export, Postman collection v2.1 or environment, OpenAPI 3 / Swagger 2 spec (JSON or YAML), browser HAR file</span></button>
      <button id="imp-paste" class="import-card"><strong>Paste cURL, JSON or YAML</strong><span class="muted small">A <code>curl …</code> command becomes a request; anything else is treated like a file</span></button>
    </div>
    <div id="imp-paste-box" hidden>
      <textarea id="imp-text" class="mono" rows="8" placeholder="curl -X POST https://api.example.com/login -H 'Content-Type: application/json' -d '{&quot;user&quot;: &quot;a&quot;}'" spellcheck="false"></textarea>
      <label class="field">Put the request in <select id="imp-folder"></select></label>
    </div>`, [
    { label: 'Cancel', onClick: closeModal },
    { label: 'Import', cls: 'primary', onClick: async () => {
      const text = ($('#imp-text') || {}).value || '';
      if (!text.trim()) { toast('Paste something first, or choose a file', 'error'); return; }
      const folderId = $('#imp-folder').value || null;
      closeModal();
      if (/^\s*curl(\.exe)?\s/i.test(text)) await importCurl(text, folderId); else await importText(text);
    } },
  ]);
  const sel = $('#imp-folder');
  folderOptions().forEach((o) => { const opt = document.createElement('option'); opt.value = o.value; opt.textContent = o.label; sel.appendChild(opt); });
  if (state.current && state.current.folder_id) sel.value = state.current.folder_id;
  $('#imp-file').addEventListener('click', () => { closeModal(); importFromFile(); });
  $('#imp-paste').addEventListener('click', () => { $('#imp-paste-box').hidden = false; $('#imp-text').focus(); $('#imp-paste').classList.add('on'); });
}
async function importFromFile() {
  if (!native()) { $('#import-file').click(); return; }
  try {
    const file = await native().open_file();
    if (file) await importText(file.content);
  } catch (e) { toast('Import failed: ' + e.message, 'error'); }
}
async function importCurl(command, folderId) {
  try {
    await saveNow();
    const r = await api('/import/curl', { method: 'POST', body: { command, folder_id: folderId } });
    await loadRequests();
    await openRequest(r.id);
    toast(`Imported ${r.method} ${r.url}`, 'ok');
  } catch (e) { toast('cURL import failed: ' + e.message, 'error'); }
}
async function importPostman(data, kind, info) {
  if (kind === 'postman_environment') {
    const res = await api('/import/postman', { method: 'POST', body: { data } });
    toast(`Imported environment “${info.name}” (${info.variables} variables)`, 'ok');
    await loadEnvironments();
    return res;
  }
  const titles = { postman_collection: 'Import Postman collection', openapi: `Import OpenAPI ${info.version || ''} spec`, har: 'Import HAR file' };
  const notes = {
    postman_collection: 'Pre-request and test scripts run unchanged.',
    openapi: 'One folder per tag. The server URL becomes the <code>{{baseUrl}}</code> variable on the top folder; bodies are filled from the examples and schemas; auth placeholders such as <code>{{token}}</code> come from the security schemes.',
    har: `${info.entries} recorded entries; scripts, styles, images and fonts are skipped. One folder per host.`,
  };
  const choice = await dialog({
    title: titles[kind] || 'Import',
    message: `${strong(info.name)}: ${info.requests} request(s) in ${info.folders} folder(s). ${notes[kind] || ''} Add to your collection or replace everything?`,
    buttons: [
      { label: 'Cancel', value: null, cancel: true },
      { label: 'Replace everything', value: 'replace', cls: 'danger' },
      { label: 'Add to collection', value: 'add', cls: 'primary', primary: true },
    ],
  });
  if (!choice) return null;
  if (choice === 'replace' && !await confirmDialog('Replace everything?', 'All current requests, folders and environments are deleted and replaced by the file. This cannot be undone.', { okLabel: 'Replace everything', danger: true })) return null;
  const res = await api('/import/postman', { method: 'POST', body: { data, replace: choice === 'replace' } });
  toast(`Imported ${res.imported} request(s) in ${res.folders} folder(s)`, 'ok');
  state.selected = [];
  if (choice === 'replace' && state.current) { autosave.dirty = false; state.current = null; renderEditor(); }
  await Promise.all([loadRequests(), loadEnvironments()]);
  return res;
}
function exportMenu(anchor) {
  openMenu('export', anchor, null, [
    { act: 'pyhttp', icon: '⬇', label: 'pyhttp JSON (whole project: collection, environments, mocks, monitors)', run: exportAll },
    { act: 'postman', icon: '🚀', label: 'Postman collection (v2.1)', run: () => exportPostman() },
    { act: 'postman-env', icon: '🌐', label: 'Postman environments', run: () => exportPostman(true) },
    '-',
    { act: 'docs', icon: '📄', label: 'Documentation (Markdown)', run: () => exportDocs() },
    { act: 'docs-html', icon: '🌐', label: 'Documentation (HTML page)', run: () => exportDocs('', 'html') },
  ]);
}
async function exportDocs(folderId = '', format = 'md') {
  try {
    const qs = new URLSearchParams();
    if (folderId) qs.set('folder_id', folderId);
    if (format === 'html') qs.set('format', 'html');
    const d = await api('/export/docs' + (qs.toString() ? '?' + qs.toString() : ''));
    await downloadText(format === 'html' ? d.html : d.markdown, d.filename);
  } catch (e) { toast('Export failed: ' + e.message, 'error'); }
}
async function exportPostman(envsOnly = false, folderId = '') {
  try {
    const data = await api('/export/postman' + (folderId ? '?folder_id=' + encodeURIComponent(folderId) : ''));
    if (envsOnly) {
      if (!data.environments.length) { toast('No environments to export', 'error'); return; }
      for (const env of data.environments) await downloadText(JSON.stringify(env, null, 2), `${env.name.replace(/[^\w.-]+/g, '_')}.postman_environment.json`);
      return;
    }
    const name = data.collection.info.name.replace(/[^\w.-]+/g, '_');
    await downloadText(JSON.stringify(data.collection, null, 2), `${name}.postman_collection.json`);
  } catch (e) { toast('Export failed: ' + e.message, 'error'); }
}
async function downloadText(text, filename) {
  if (native()) {
    try { const path = await native().save_file(text, filename); if (path) toast('Saved to ' + path, 'ok'); } catch (e) { toast('Save failed: ' + e.message, 'error'); }
    return;
  }
  const blob = new Blob([text], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
async function saveResponseBody() {
  if (!lastResult || !lastResult.response) return;
  const resp = lastResult.response;
  const ct = (resp.content_type || '').split(';')[0].trim();
  const ext = ct.includes('json') ? '.json' : ct.includes('html') ? '.html' : ct.includes('xml') ? '.xml' : ct.startsWith('image/') ? '.' + ct.split('/')[1].split('+')[0] : ct === 'application/pdf' ? '.pdf' : ct.startsWith('text/') ? '.txt' : '.bin';
  const base = ((state.current && state.current.name) || 'response').replace(/[^\w.-]+/g, '_');
  const filename = `${base}${ext}`;
  if (resp.is_binary) {
    if (!resp.body_base64) { toast('The body is too large to save from the viewer; use a run to save it to disk', 'error'); return; }
    if (native() && native().save_file_b64) {
      try { const p = await native().save_file_b64(resp.body_base64, filename); if (p) toast('Saved to ' + p, 'ok'); } catch (e) { toast('Save failed: ' + e.message, 'error'); }
      return;
    }
    const bytes = Uint8Array.from(atob(resp.body_base64), (c) => c.charCodeAt(0));
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([bytes], { type: ct || 'application/octet-stream' }));
    a.download = filename; a.click(); URL.revokeObjectURL(a.href);
    return;
  }
  await downloadText(resp.body_text || '', filename);
}

// ============================================================ request tabs
const tabs = { list: [] }; // [{id}] ; id === 'draft' for the unsaved new request
function tabKey(r) { return r ? (r.id || 'draft') : null; }
function tabsAdd(r) {
  const key = tabKey(r);
  if (!key) return;
  if (!tabs.list.some((t) => t.id === key)) tabs.list.push({ id: key });
  renderReqTabs();
}
function tabsRename(from, to) { tabs.list.forEach((t) => { if (t.id === from) t.id = to; }); renderReqTabs(); }
function tabsRemove(key) { tabs.list = tabs.list.filter((t) => t.id !== key); renderReqTabs(); }
// Re-rendered only when the set of tabs changes: a blur right before a click must not
// replace the element being clicked. The "unsaved" dot is updated in place.
let tabsSignature = '';
function renderReqTabs() {
  const box = $('#req-tabs');
  const cur = tabKey(state.current);
  tabs.list = tabs.list.filter((t) => t.id === 'draft' ? (cur === 'draft') : state.requests.some((r) => r.id === t.id) || t.id === cur);
  box.hidden = tabs.list.length === 0;
  const items = tabs.list.map((t) => {
    const r = t.id === 'draft' ? state.current : (cur === t.id ? state.current : state.requests.find((x) => x.id === t.id));
    const url = cur === t.id ? $('#f-url').value : r && r.url;
    return r ? { key: t.id, name: r.name || 'Untitled', method: /^\s*wss?:\/\//i.test(url || '') ? 'WS' : r.method, active: t.id === cur } : null;
  }).filter(Boolean);
  const sig = JSON.stringify(items);
  if (sig !== tabsSignature) {
    tabsSignature = sig;
    box.innerHTML = items.map((it) => `<div class="req-tab ${it.active ? 'active' : ''}" data-key="${esc(it.key)}" title="${esc(it.name)} (drag to reorder)" draggable="true"><span class="method m-${esc(it.method)}">${esc(it.method)}</span><span class="rt-name">${esc(it.name)}</span><span class="rt-dot" title="Unsaved changes" hidden>●</span><button class="rt-close" title="Close (Ctrl+W)">✕</button></div>`).join('') +
      '<button class="rt-add" title="New request (Ctrl+N)">+</button>';
  }
  $$('.req-tab', box).forEach((el) => { $('.rt-dot', el).hidden = !(el.dataset.key === cur && autosave.dirty); });
}
// drag a tab left or right to reorder
let tabDrag = null;
function bindTabDrag(box) {
  box.addEventListener('dragstart', (e) => {
    const el = e.target.closest('.req-tab');
    if (!el) return;
    tabDrag = el.dataset.key;
    el.classList.add('drag-src');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', tabDrag);
  });
  box.addEventListener('dragover', (e) => {
    const el = e.target.closest('.req-tab');
    if (!el || !tabDrag || el.dataset.key === tabDrag) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const r = el.getBoundingClientRect();
    const left = e.clientX < r.left + r.width / 2;
    $$('.req-tab', box).forEach((t) => t.classList.remove('drop-left', 'drop-right'));
    el.classList.add(left ? 'drop-left' : 'drop-right');
  });
  box.addEventListener('dragleave', (e) => { if (!box.contains(e.relatedTarget)) $$('.req-tab', box).forEach((t) => t.classList.remove('drop-left', 'drop-right')); });
  box.addEventListener('drop', (e) => {
    const el = e.target.closest('.req-tab');
    $$('.req-tab', box).forEach((t) => t.classList.remove('drop-left', 'drop-right'));
    if (!el || !tabDrag || el.dataset.key === tabDrag) return;
    e.preventDefault();
    const r = el.getBoundingClientRect();
    const before = e.clientX < r.left + r.width / 2;
    const from = tabs.list.findIndex((t) => t.id === tabDrag);
    if (from === -1) return;
    const [moved] = tabs.list.splice(from, 1);
    let to = tabs.list.findIndex((t) => t.id === el.dataset.key);
    if (!before) to += 1;
    tabs.list.splice(to, 0, moved);
    tabsSignature = '';
    renderReqTabs();
  });
  box.addEventListener('dragend', () => { tabDrag = null; $$('.req-tab', box).forEach((t) => t.classList.remove('drag-src', 'drop-left', 'drop-right')); });
}
function bindReqTabs() {
  const box = $('#req-tabs');
  bindTabDrag(box);
  box.addEventListener('click', (e) => {
    if (e.target.closest('.rt-add')) { newRequest(); return; }
    const el = e.target.closest('.req-tab');
    if (!el) return;
    if (e.target.closest('.rt-close')) { closeTab(el.dataset.key); return; }
    if (el.dataset.key !== tabKey(state.current)) openRequest(el.dataset.key);
  });
  box.addEventListener('auxclick', (e) => { const el = e.target.closest('.req-tab'); if (el && e.button === 1) closeTab(el.dataset.key); });
}
async function closeTab(key) {
  const cur = tabKey(state.current);
  const i = tabs.list.findIndex((t) => t.id === key);
  if (i === -1) return;
  tabs.list.splice(i, 1);
  if (key === cur) {
    await saveNow();
    const next = tabs.list[Math.min(i, tabs.list.length - 1)];
    if (next) await openRequest(next.id);
    else { state.current = null; renderEditor(); renderList(); }
  }
  renderReqTabs();
}

// ============================================================== shortcuts
function openHelp() {
  const rows = [
    ['Ctrl+Enter', 'Send the request'], ['Ctrl+Shift+Enter', 'Send only the body you are viewing'], ['Ctrl+S', 'Save now'], ['Ctrl+N', 'New request'], ['Ctrl+D', 'Duplicate the request'],
    ['Ctrl+W', 'Close the current tab'], ['Ctrl+K', 'Search the collection'], ['Ctrl+\\', 'Show or hide the sidebar'], ['Ctrl+Shift+C', 'Code snippet'],
    ['Ctrl+/', 'Comment or uncomment the line(s) in the body'], ['Ctrl+B', 'Beautify the body'], ['Tab / Shift+Tab', 'Indent / outdent in the body'],
    ['Ctrl+F', 'Find: in the body editor when the request has focus, in the response body otherwise'], ['Ctrl+Alt+C', 'Toggle the console'], ['Escape', 'Close dialogs and menus'], ['?', 'This help'],
  ];
  openModal('Keyboard shortcuts', `<table class="htable keys"><tbody>${rows.map(([k, d]) => `<tr><td class="k"><kbd>${esc(k).replace(/\+/g, '</kbd>+<kbd>')}</kbd></td><td>${esc(d)}</td></tr>`).join('')}</tbody></table>
    <p class="muted small">On macOS use ⌘ instead of Ctrl.</p>`, [{ label: 'Close', cls: 'primary', onClick: closeModal }]);
}
function inEditable(e) {
  const t = e.target;
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
}
function bindShortcuts() {
  document.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (!$('#modal').hidden || $('.dialog-backdrop')) return;
    if (mod && !e.shiftKey && e.key.toLowerCase() === 'n') { e.preventDefault(); newRequest(state.current ? state.current.folder_id : null); return; }
    if (mod && !e.shiftKey && e.key.toLowerCase() === 'd' && state.current && state.current.id) { e.preventDefault(); duplicateRequestById(state.current.id); return; }
    if (mod && !e.shiftKey && e.key.toLowerCase() === 'w' && state.current) { e.preventDefault(); closeTab(tabKey(state.current)); return; }
    if (mod && !e.shiftKey && e.key.toLowerCase() === 'k') { e.preventDefault(); if (!sidebarShown()) setSidebar(true); $('#search').focus(); $('#search').select(); return; }
    if (mod && e.key === '\\') { e.preventDefault(); setSidebar(!sidebarShown()); return; }
    if (mod && e.shiftKey && e.key.toLowerCase() === 'c' && state.current) { e.preventDefault(); openCodeModal(); return; }
    if (mod && !e.shiftKey && e.key.toLowerCase() === 'f' && state.current) {
      // Find follows the focus: in the request pane it searches the body editor (CodeMirror has its
      // own panel), in the response pane (or anywhere else) it searches the response body.
      const t = e.target instanceof Element ? e.target : null;
      if (t && t.closest('.editor-request') && !t.closest('#response')) {
        const cm = t.closest('.cm-editor');
        if (cm) {
          // Cmd+F reaches CodeMirror's own keymap on a Mac, Ctrl+F does not: open the panel of the focused editor either way
          const own = $$('textarea').find((ta) => ta._enhanced && ta._enhanced.wrap && ta._enhanced.wrap.contains(cm));
          if (own) { e.preventDefault(); own._enhanced.search(); }
          return;
        }
        const api = $('#f-body')._enhanced;
        const bodyTab = $('#editor-panels .tab-panel[data-panel="body"]');
        if (api && bodyTab && !bodyTab.hidden && !$('#body-text-wrap').hidden && !$('#body-editor').hidden) { e.preventDefault(); api.search(); }
        return;   // other tabs: the browser's own find
      }
      if ($('#response').hidden) return;
      const box = $('#r-body');
      if (box._focusSearch) { e.preventDefault(); showTab($('#resp-tabs'), 'body'); box._focusSearch(); }
      return;
    }
    if (e.key === '?' && !inEditable(e)) { e.preventDefault(); openHelp(); }
  });
}

// ================================================================ markdown
function mdToHtml(md) {
  const lines = (md || '').split(/\r?\n/);
  let html = '', inList = null, inCode = false;
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  const closeList = () => { if (inList) { html += `</${inList}>`; inList = null; } };
  lines.forEach((line) => {
    if (line.trim().startsWith('```')) { closeList(); inCode = !inCode; html += inCode ? '<pre><code>' : '</code></pre>'; return; }
    if (inCode) { html += esc(line) + '\n'; return; }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) { closeList(); html += `<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`; return; }
    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (ul || ol) {
      const kind = ul ? 'ul' : 'ol';
      if (inList !== kind) { closeList(); html += `<${kind}>`; inList = kind; }
      html += `<li>${inline((ul || ol)[1])}</li>`;
      return;
    }
    closeList();
    if (!line.trim()) { html += ''; return; }
    html += `<p>${inline(line)}</p>`;
  });
  closeList();
  if (inCode) html += '</code></pre>';
  return html;
}
function bindDescriptionPreview() {
  const btn = $('#btn-desc-preview');
  const ta = $('#f-description');
  const box = $('#desc-preview');
  btn.addEventListener('click', () => {
    const show = box.hidden;
    box.hidden = !show;
    ta.hidden = show;
    btn.textContent = show ? 'Edit' : 'Preview';
    if (show) box.innerHTML = mdToHtml(ta.value) || '<span class="muted small">No description yet.</span>';
  });
}
function resetDescriptionPreview() {
  $('#desc-preview').hidden = true;
  $('#f-description').hidden = false;
  $('#btn-desc-preview').textContent = 'Preview';
}

// ========================================================= folder settings
async function openFolderSettings(f) {
  const fresh = folderById(f.id) || f;
  openModal(`Folder settings: ${fresh.name}`, `
    <p class="muted small">Requests inside this folder inherit these settings (like a Postman collection or folder). Nested folders override their parents.</p>
    <div class="set-section"><h4>Description</h4><textarea id="fs-desc" rows="2" placeholder="What this folder is for">${esc(fresh.description || '')}</textarea></div>
    <div class="set-section"><h4>Authorization</h4><div id="fs-auth"></div></div>
    <div class="set-section"><h4>Headers added to every request</h4><div id="fs-headers" class="kv"></div></div>
    <div class="set-section"><h4>Response views for every request inside</h4><p class="muted small">Name on the left, the full path of an HTML file (or a page address) on the right. Each request in this folder shows them in the View tab of its response, next to its own.</p><div id="fs-views" class="kv"></div></div>
    <div class="set-section"><h4>Variables (collection variables)</h4><p class="muted small">Lowest precedence: environment and global variables with the same name win, request pre-request variables win over those.</p><div id="fs-vars" class="kv"></div></div>
  `, [
    { label: 'Cancel', onClick: closeModal },
    { label: 'Save', cls: 'primary', onClick: async () => {
      const box = $('#fs-auth');
      const auth = readAuthFields(box, $('#fs-auth-type', box).value, fresh.auth);
      try {
        const old = fresh.views || [];
        const views = readKv($('#fs-views')).filter((v) => (v.value || '').trim()).map((v) => {
          const file = v.value.trim();
          const was = old.find((o) => o.file === file);
          return { id: was ? was.id : newViewId(), name: (v.key || '').trim() || (file.split(/[\\/]/).pop() || 'View').replace(/\.html?$/i, ''), file };
        });
        await api('/folders/' + f.id, { method: 'PATCH', body: { description: $('#fs-desc').value, auth, headers: readKv($('#fs-headers')), variables: readKv($('#fs-vars')), views } });
        closeModal();
        toast('Folder settings saved', 'ok');
        await loadRequests();
        if (state.current) await refreshInherited();
      } catch (e) { toast(e.message, 'error'); }
    } },
  ]);
  const a = fresh.auth || { type: 'inherit' };
  const box = $('#fs-auth');
  box.dataset.folder = f.id;
  const labels = { ...AUTH_LABELS, inherit: 'Inherit from parent folder' };
  const draw = (cur) => {
    box.innerHTML = `<label class="field"><span class="fl">Type</span><select id="fs-auth-type">${Object.entries(labels).map(([k, l]) => `<option value="${k}" ${cur.type === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label><div id="auth-fields">${authFields(cur, 'Requests use the auth of the parent folder, if any.')}</div>`;
    $('#fs-auth-type', box).addEventListener('change', () => draw(readAuthFields(box, $('#fs-auth-type', box).value, cur)));
    if (cur.type === 'oauth2') refreshOauthStatus(box);
  };
  draw(a);
  renderKv($('#fs-headers'), fresh.headers || [], true);
  renderKv($('#fs-views'), (fresh.views || []).map((v) => ({ key: v.name, value: v.file, enabled: true })), false);
  $('#fs-vars').dataset.secrets = '1';
  renderKv($('#fs-vars'), fresh.variables || [], true);
}
async function refreshInherited() {
  const r = state.current;
  if (!r) return;
  inheritedInfo = r.folder_id ? await api(`/folders/${r.folder_id}/inherited`).catch(() => null) : null;
  if (state.current !== r) return;
  renderAuthPanel({ ...r, auth: readAuth() });
  renderInheritedHeaders();
  updateTabCounts();
  if (typeof updateViewTab === 'function') updateViewTab();   // the folders' views count too
}

// ============================================================ reorder DnD
// Dragging a request/folder over the top or bottom half of a sibling inserts it
// before/after; dropping on a folder (middle) moves it inside (handled by app.js).
let dropMark = null;
function reorderTarget(e) {
  const li = e.target.closest('.req-item, .folder-item');
  if (!li || !state.drag) return null;
  const rect = li.getBoundingClientRect();
  const y = (e.clientY - rect.top) / rect.height;
  const isFolder = !!li.dataset.fid;
  if (isFolder && y > 0.25 && y < 0.75) return null; // middle of a folder: move inside
  const pos = y < 0.5 ? 'before' : 'after';
  const sameKind = (state.drag.type === 'request') === !isFolder;
  if (!sameKind) return null;
  const id = isFolder ? li.dataset.fid : li.dataset.rid;
  if (id === state.drag.id) return null;
  return { li, pos, id, isFolder };
}
function showDropMark(t) {
  clearDropMark();
  if (!t) return;
  t.li.classList.add(t.pos === 'before' ? 'drop-before' : 'drop-after');
  dropMark = t.li;
}
function clearDropMark() { if (dropMark) { dropMark.classList.remove('drop-before', 'drop-after'); dropMark = null; } }
async function applyReorder(t) {
  const d = state.drag;
  if (!d || !t) return false;
  if (t.isFolder) {
    const target = folderById(t.id);
    const parent = target.parent_id || '';
    if (folderDescendants(d.id).has(target.id)) return false;
    const siblings = state.folders.filter((f) => (f.parent_id || '') === parent && f.id !== d.id);
    const idx = siblings.findIndex((f) => f.id === t.id) + (t.pos === 'after' ? 1 : 0);
    siblings.splice(idx, 0, folderById(d.id));
    const me = folderById(d.id);
    if ((me.parent_id || '') !== parent) await api('/folders/' + d.id, { method: 'PATCH', body: { parent_id: parent || null, move: true } });
    await api('/reorder', { method: 'POST', body: { folder_ids: siblings.map((f) => f.id) } });
  } else {
    const target = state.requests.find((r) => r.id === t.id);
    const parent = target.folder_id || '';
    const siblings = state.requests.filter((r) => (r.folder_id || '') === parent && r.id !== d.id);
    const idx = siblings.findIndex((r) => r.id === t.id) + (t.pos === 'after' ? 1 : 0);
    const me = state.requests.find((r) => r.id === d.id);
    siblings.splice(idx, 0, me);
    if ((me.folder_id || '') !== parent) await api(`/requests/${d.id}/move`, { method: 'POST', body: { folder_id: parent || null } });
    await api('/reorder', { method: 'POST', body: { request_ids: siblings.map((r) => r.id) } });
    if (state.current && state.current.id === d.id) state.current.folder_id = parent || null;
  }
  await loadRequests();
  return true;
}

// ========================================================== runner extras
const runData = { rows: [], columns: [], name: '' };
async function loadDataFile(file) {
  try {
    const text = await file.text();
    const r = await api('/runs/data-file', { method: 'POST', body: { text, filename: file.name } });
    runData.rows = r.rows; runData.columns = r.columns; runData.name = file.name;
    renderDataFileInfo();
  } catch (e) { toast(e.message, 'error'); }
}
async function pickDataFile() {
  if (native() && native().open_file) {
    try {
      const f = await native().open_file('data');
      if (f) {
        const r = await api('/runs/data-file', { method: 'POST', body: { text: f.content, filename: f.name } });
        runData.rows = r.rows; runData.columns = r.columns; runData.name = f.name;
        renderDataFileInfo();
      }
    } catch (e) { toast(e.message, 'error'); }
    return;
  }
  $('#data-file').click();
}
function renderDataFileInfo() {
  const el = $('#run-data-info');
  if (!el) return;
  const it = $('#run-iterations');
  if (!runData.rows.length) {
    el.innerHTML = '<span class="muted">none: every iteration uses the same variable values</span>';
    if (it) it.disabled = false;   // a removed variables file hands the iteration count back to the user
    return;
  }
  el.innerHTML = `<strong>${esc(runData.name)}</strong>: ${runData.rows.length} row(s) → ${runData.rows.length} iteration(s). Variables: ${runData.columns.map((c) => `<code>{{${esc(c)}}}</code>`).join(' ')} <button class="link small" id="run-data-clear">remove</button>`;
  $('#run-data-clear').addEventListener('click', () => { runData.rows = []; runData.columns = []; runData.name = ''; renderDataFileInfo(); });
  if (it) { it.value = runData.rows.length; it.disabled = true; }
}
function runReportHtml(run) {
  const results = run.results || [];
  const rows = results.map((r) => {
    const tests = r.tests || [];
    return `<tr class="${r.status}"><td>${r.position}</td><td>${r.iteration || 1}</td><td>${esc(r.method)}</td><td>${esc(r.request_name)}${r.body_name ? ' · ' + esc(r.body_name) : ''}</td><td>${esc((r.detail || {}).request ? r.detail.request.url : '')}</td><td>${r.status_code ?? 'ERR'}</td><td>${Math.round(r.duration_ms || 0)} ms</td><td>${esc(r.status)}</td><td>${tests.length ? tests.map((t) => `<div class="${t.passed ? 'p' : 'f'}">${t.passed ? '✓' : '✕'} ${esc(t.name)}${t.note ? ` <small>(${esc(t.note)})</small>` : ''}</div>`).join('') : ''}${r.error ? `<div class="f">${esc(r.error)}</div>` : ''}</td></tr>`;
  }).join('');
  return `<!doctype html><html><head><meta charset="utf-8"><title>pyhttp run: ${esc(run.name)}</title>
<style>body{font:14px system-ui,sans-serif;margin:24px;color:#1c2330}h1{font-size:20px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #d6dbe3;padding:6px 8px;vertical-align:top;text-align:left}th{background:#f0f2f5}tr.failed td{background:#fff1f1}.p{color:#15803d}.f{color:#b91c1c}.sum{display:flex;gap:18px;margin:10px 0 18px}.sum b{font-size:18px;display:block}</style></head>
<body><h1>pyhttp run: ${esc(run.name)}</h1>
<div class="sum"><div><b>${run.completed}/${run.total}</b>sent</div><div><b class="${run.failed ? 'f' : 'p'}">${run.failed}</b>failed</div><div><b>${run.tests_total - run.tests_failed}/${run.tests_total}</b>tests passed</div><div><b>${esc(run.status)}</b>status</div><div><b>${esc(fmtDate(run.started_at))}</b>started</div>${run.options && run.options.environment ? `<div><b>${esc(run.options.environment)}</b>environment</div>` : ''}</div>
<table><thead><tr><th>#</th><th>Iter.</th><th>Method</th><th>Request</th><th>URL</th><th>Status</th><th>Time</th><th>Result</th><th>Tests</th></tr></thead><tbody>${rows}</tbody></table>
<p style="color:#5d6877;font-size:12px">Generated by pyhttp ${esc(run.pyhttp_version || '')} · output folder: ${esc(run.output_dir)}</p></body></html>`;
}
async function exportRunReport(runId, kind) {
  try {
    const run = await api(`/runs/${runId}/report`);
    const base = `pyhttp-run-${(run.name || 'run').replace(/[^\w.-]+/g, '_')}`;
    if (kind === 'html') await downloadText(runReportHtml(run), base + '.html');
    else await downloadText(JSON.stringify(run, null, 2), base + '.json');
  } catch (e) { toast(e.message, 'error'); }
}

// ================================================================ updates
async function checkForUpdate(manual = false, statusEl = null, btn = null) {
  if (btn) { btn.disabled = true; btn.dataset.label = btn.dataset.label || btn.textContent; btn.innerHTML = '<span class="spin"></span>Checking…'; }
  if (statusEl) { statusEl.textContent = ''; statusEl.className = 'small'; }
  try {
    const u = await api('/update-check' + (manual ? '?force=1' : ''));
    const pill = $('#btn-update');
    pill.hidden = !u.available;
    if (u.available) {
      pill.textContent = `⬆ Update ${u.latest}`;
      pill.onclick = () => openUpdateDialog(u);
    }
    if (statusEl) {
      if (u.available) { statusEl.innerHTML = `⬆ Version ${esc(u.latest)} is available (you have ${esc(u.current)}). <button class="link small" id="upd-open">Details</button>`; statusEl.className = 'small ok-text'; $('#upd-open').addEventListener('click', () => openUpdateDialog(u)); }
      else if (u.reason) { statusEl.textContent = `Could not check: ${u.reason}`; statusEl.className = 'small err-text'; }
      else { statusEl.textContent = `✓ You have the latest version (${u.current}).`; statusEl.className = 'small ok-text'; }
    } else if (manual) {
      if (u.available) openUpdateDialog(u);
      else toast(u.reason ? `Could not check: ${u.reason}` : `You have the latest version (${u.current})`, u.reason ? 'error' : 'ok');
    }
  } catch (e) {
    if (statusEl) { statusEl.textContent = 'Could not check: ' + e.message; statusEl.className = 'small err-text'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.label; }
  }
}
function openUpdateDialog(u) {
  const inst = u.install || {};
  const a = u.asset;
  const head = `<p>You are on <strong>${esc(u.current)}</strong>; version <strong>${esc(u.latest)}</strong> was published${u.published_at ? ' on ' + esc(new Date(u.published_at).toLocaleDateString()) : ''}.</p>`;
  const notes = u.notes ? `<div class="set-section"><h4>Release notes</h4><div class="md">${mdToHtml(u.notes)}</div></div>` : '';
  const download = a
    ? `<p>Download for this computer: <a href="${esc(a.url)}" target="_blank" rel="noopener" id="upd-asset">${esc(a.name)}</a> (${fmtBytes(a.size)}). Close pyhttp, run the new file, and your data stays in place.</p>`
    : `<p>Get it from the <a href="${esc(u.url)}" target="_blank" rel="noopener">release page</a>.</p>`;
  const cmds = (inst.commands || []).length
    ? `<div class="set-section"><h4>${esc(inst.commands_title || 'Update from the terminal')}</h4>${inst.commands_note ? `<p class="small muted">${esc(inst.commands_note)}</p>` : ''}<pre class="upd-cmds" id="upd-cmds">${esc(inst.commands.join('\n'))}</pre><button class="link small" id="upd-copy">Copy commands</button></div>`
    : '';
  const progress = '<div id="upd-progress" class="upd-progress" hidden></div>';
  let body;
  const buttons = [{ label: 'Later', onClick: closeModal }];
  if (inst.self_update && (inst.kind === 'installer' || inst.kind === 'portable') && a) {
    body = head + (inst.kind === 'installer'
      ? `<p>pyhttp downloads the installer (${fmtBytes(a.size)}), verifies it, runs it quietly, and opens again on the new version. Your requests and settings stay in place.</p>`
      : `<p>pyhttp downloads the new <code>${esc(a.name)}</code> (${fmtBytes(a.size)}), verifies it, replaces its own program file and starts again. Your requests and settings stay in place.</p>`)
      + progress + notes;
    buttons.push({ label: 'Download instead', onClick: () => openExternal(a.url) });
    buttons.push({ label: 'Install and restart', cls: 'primary', onClick: () => startSelfUpdate() });
  } else if (inst.self_update && inst.kind === 'brew') {
    // the cask is pushed a minute or two after the release: say so instead of failing
    const lag = inst.ready === false
      ? `<p class="small muted">Homebrew's cask still offers ${esc(inst.cask_version || 'the previous version')}; it is updated within minutes of a release. The update waits for it for about a minute, or you can come back later.</p>`
      : '';
    body = head + `<p>pyhttp was installed with Homebrew. The update runs <code>brew upgrade --cask pyhttp</code> here, then pyhttp opens again on the new version.</p>` + lag + progress + cmds + notes;
    buttons.push({ label: inst.ready === false ? 'Wait for the cask and update' : 'Update with Homebrew', cls: 'primary', onClick: () => startSelfUpdate() });
  } else {
    body = head + download + cmds + notes;
    buttons.push({ label: a ? 'Download' : 'Open release page', cls: 'primary', onClick: () => openExternal(a ? a.url : u.url) });
  }
  openModal(`Update available: ${u.latest}`, body, buttons);
  const copy = $('#upd-copy');
  if (copy) copy.addEventListener('click', async () => { if (await copyText($('#upd-cmds').textContent)) toast('Commands copied', 'ok'); });
}
// Install the update found by checkForUpdate and show its progress until pyhttp restarts.
async function startSelfUpdate() {
  const box = $('#upd-progress');
  if (!box) return;
  box.hidden = false;
  box.innerHTML = '<div class="progress"><div style="width:0%"></div></div><div class="small muted upd-msg" id="upd-msg"><span class="spin"></span>Starting…</div><pre class="upd-log" id="upd-log" hidden></pre>';
  const foot = $$('#modal-foot button');
  foot.forEach((b) => { b.disabled = true; });
  const fail = (msg) => {
    $('#upd-msg').innerHTML = `<span class="err-text">${esc(msg)}</span>`;
    $('.progress', box).hidden = true;
    foot.forEach((b) => { b.disabled = false; });
  };
  try { await api('/update/install', { method: 'POST', body: {} }); } catch (e) { fail(e.message); return; }
  let gone = 0;
  const poll = async () => {
    let s;
    try { s = await api('/update/status'); } catch { if (++gone < 20) setTimeout(poll, 1000); return; }   // the server is gone: pyhttp is restarting
    const bar = $('.progress > div', box);
    if (s.progress != null) bar.style.width = `${Math.round(s.progress * 100)}%`;
    else if (s.state === 'restarting') bar.style.width = '100%';
    if (s.log && s.log.length) { const log = $('#upd-log'); log.hidden = false; log.textContent = s.log.join('\n'); log.scrollTop = log.scrollHeight; }
    if (s.state === 'error') { fail(s.error || s.message || 'Update failed'); return; }
    $('#upd-msg').innerHTML = `<span class="spin"></span>${esc(s.message || s.state)}`;
    if (s.state === 'restarting') { $('#upd-msg').innerHTML = `✓ ${esc(s.message)} You can close this window if it does not close by itself.`; return; }
    setTimeout(poll, 500);
  };
  poll();
}
function openExternal(url) {
  if (native() && native().open_url) { native().open_url(url).catch(() => window.open(url, '_blank')); return; }
  window.open(url, '_blank', 'noopener');
}

// ========================================================= runs panel toggle
function setRunsPanel(show) {
  $('.layout').classList.toggle('no-runs', !show);
  $('#sb-runs').classList.toggle('on', show);
  try { localStorage.setItem('pyhttp.runs', show ? '1' : '0'); } catch { /* ignore */ }
}
function runsPanelShown() { return !$('.layout').classList.contains('no-runs'); }

// ======================================================= resizable panels
// Drag the edge of the sidebar, the runs panel or (layout "beside") the response to resize;
// double-click an edge to reset. Sizes are remembered per browser.
// split: response to the right (share of the width); vsplit: response below (share of the height)
const PANEL_SIZES = { side: ['--side-w', 200, 640], runs: ['--runs-w', 260, 900], split: ['--req-grow', 0.2, 0.8], vsplit: ['--req-grow', 0.2, 0.85] };
const isBeside = () => $('#editor-section').classList.contains('beside');
const splitName = () => (isBeside() ? 'split' : 'vsplit');
function applyPanelSize(name, value, remember = true) {
  const root = document.documentElement.style;
  const [prop] = PANEL_SIZES[name];
  if (value == null) {
    root.removeProperty(prop);
    if (name === 'split' || name === 'vsplit') root.removeProperty('--resp-grow');
  } else if (name === 'split' || name === 'vsplit') {
    root.setProperty('--req-grow', String(value));
    root.setProperty('--resp-grow', String(1 - value));
  } else root.setProperty(prop, Math.round(value) + 'px');
  if (!remember) return;
  try { if (value == null) localStorage.removeItem('pyhttp.size.' + name); else localStorage.setItem('pyhttp.size.' + name, String(value)); } catch { /* ignore */ }
}
function savedPanelSize(name) {
  let v = null;
  try { v = localStorage.getItem('pyhttp.size.' + name); } catch { /* ignore */ }
  return v != null && !Number.isNaN(Number(v)) ? Number(v) : null;
}
function initResizers() {
  const make = (parent, nameOf, measure) => {
    const h = document.createElement('div');
    h.className = 'resizer';
    h.dataset.resize = typeof nameOf === 'function' ? nameOf() : nameOf;
    h.title = 'Drag to resize, double-click to reset';
    parent.appendChild(h);
    h.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const name = typeof nameOf === 'function' ? nameOf() : nameOf;
      h.setPointerCapture(e.pointerId);
      h.classList.add('dragging');
      document.body.classList.add('resizing');
      document.body.classList.toggle('rows', name === 'vsplit');
      const [, min, max] = PANEL_SIZES[name];
      const move = (ev) => applyPanelSize(name, Math.min(max, Math.max(min, measure(ev))));
      const up = () => {
        h.classList.remove('dragging');
        document.body.classList.remove('resizing', 'rows');
        h.removeEventListener('pointermove', move);
        h.removeEventListener('pointerup', up);
        h.removeEventListener('pointercancel', up);
      };
      h.addEventListener('pointermove', move);
      h.addEventListener('pointerup', up);
      h.addEventListener('pointercancel', up);
    });
    h.addEventListener('dblclick', () => applyPanelSize(typeof nameOf === 'function' ? nameOf() : nameOf, null));
  };
  make($('.sidebar'), 'side', (e) => e.clientX - $('.sidebar').getBoundingClientRect().left);
  make($('#runs-panel'), 'runs', (e) => $('#runs-panel').getBoundingClientRect().right - e.clientX);
  make($('#response'), splitName, (e) => {
    const r = $('#editor-form').getBoundingClientRect();
    return isBeside() ? (e.clientX - r.left) / r.width : (e.clientY - r.top) / r.height;
  });
  ['side', 'runs'].forEach((name) => { const v = savedPanelSize(name); if (v != null) applyPanelSize(name, v); });
  applyPanelSize(splitName(), savedPanelSize(splitName()), false);
}

// ================================================================ sidebar
function setSidebar(show) {
  $('.layout').classList.toggle('no-side', !show);
  const b = $('#btn-sidebar');
  b.classList.toggle('on', show);
  b.title = show ? 'Hide the sidebar (Ctrl+\\)' : 'Show the sidebar (Ctrl+\\)';
  try { localStorage.setItem('pyhttp.side', show ? '1' : '0'); } catch { /* ignore */ }
}
function sidebarShown() { return !$('.layout').classList.contains('no-side'); }

// ================================================= response collapse / max
function setResponseMax(on) {
  $('#editor-form').classList.toggle('resp-max', on);
  $('#r-max').textContent = on ? '⤡' : '⤢';
  $('#r-max').title = on ? 'Restore the request editor (Esc)' : 'Maximize the response (Esc restores)';
  if (on) $('#response').classList.remove('collapsed');
}
function setResponseCollapsed(on) {
  $('#response').classList.toggle('collapsed', on);
  $('#r-collapse').textContent = on ? '▸' : '▾';
  $('#r-collapse').title = on ? 'Show the response' : 'Hide the response to see the whole request';
  if (on) setResponseMax(false);
}

// ================================================================= layout
const LAYOUT_ICONS = {
  below: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="2" width="12" height="5" rx="1"/><rect x="2" y="9" width="12" height="5" rx="1" opacity=".45"/></svg>',
  beside: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="2" width="5" height="12" rx="1"/><rect x="9" y="2" width="5" height="12" rx="1" opacity=".45"/></svg>',
};
function applyLayout(layout) {
  const beside = layout === 'beside';
  $('#editor-section').classList.toggle('beside', beside);
  $$('#sb-layout button[data-layout]').forEach((b) => b.classList.toggle('on', b.dataset.layout === (beside ? 'beside' : 'below')));
  const rl = $('#r-layout');
  rl.innerHTML = beside ? LAYOUT_ICONS.below : LAYOUT_ICONS.beside;   // the icon shows where a click moves the response
  rl.title = beside ? 'Move the response below the request' : 'Move the response to the right of the request';
  const h = $('#response .resizer');
  if (h) h.dataset.resize = beside ? 'split' : 'vsplit';
  applyPanelSize(beside ? 'split' : 'vsplit', savedPanelSize(beside ? 'split' : 'vsplit'), false);
}
// Settings > Appearance fonts: id -> CSS stack. Only Inter is bundled; the others fall back when not installed.
const SANS_TAIL = 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';
const MONO_TAIL = 'ui-monospace, "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace';
const UI_FONTS = [
  ['inter', 'Inter (default)', `"Inter Variable", Inter, ${SANS_TAIL}`],
  ['system', 'System default', SANS_TAIL],
  ['segoe', 'Segoe UI', `"Segoe UI", ${SANS_TAIL}`],
  ['roboto', 'Roboto', `Roboto, ${SANS_TAIL}`],
  ['helvetica', 'Helvetica / Arial', `"Helvetica Neue", Helvetica, Arial, ${SANS_TAIL}`],
  ['ubuntu', 'Ubuntu', `Ubuntu, ${SANS_TAIL}`],
  ['verdana', 'Verdana', `Verdana, ${SANS_TAIL}`],
];
const MONO_FONTS = [
  ['default', 'System monospace (default)', MONO_TAIL],
  ['cascadia', 'Cascadia Code', `"Cascadia Code", "Cascadia Mono", ${MONO_TAIL}`],
  ['jetbrains', 'JetBrains Mono', `"JetBrains Mono", ${MONO_TAIL}`],
  ['consolas', 'Consolas', `Consolas, ${MONO_TAIL}`],
  ['fira', 'Fira Code', `"Fira Code", ${MONO_TAIL}`],
  ['source', 'Source Code Pro', `"Source Code Pro", ${MONO_TAIL}`],
  ['courier', 'Courier New', `"Courier New", ${MONO_TAIL}`],
];
const fontStack = (list, id) => (list.find((f) => f[0] === id) || list[0])[2];
function applyFonts(ui, mono) {
  const s = document.documentElement.style;
  const u = fontStack(UI_FONTS, ui), m = fontStack(MONO_FONTS, mono);
  s.setProperty('--sans', u);
  s.setProperty('--mono', m);
  try { localStorage.setItem('pyhttp.uifont', u); localStorage.setItem('pyhttp.monofont', m); } catch { /* ignore */ }
}
function applyFontSize(px) {
  document.documentElement.style.setProperty('--editor-font', (px || 13) + 'px');
  try { localStorage.setItem('pyhttp.fontsize', String(px || 13)); } catch { /* ignore */ }
}

// ================================================================== init
function initFeatures() {
  $('button[data-tab="requests"]', $('#runs-tabs')).addEventListener('click', () => loadHistoryList());
  $$('#history-filters button').forEach((b) => b.addEventListener('click', () => {
    hist.status = b.dataset.status;
    $$('#history-filters button').forEach((x) => x.classList.toggle('on', x === b));
    loadHistoryList();
  }));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && hist.compare && $('#modal').hidden) setCompareBase(null); });
  $('#history-search').addEventListener('input', (e) => { clearTimeout(hist.timer); hist.timer = setTimeout(() => { hist.q = e.target.value.trim(); loadHistoryList(); }, 150); });
  $('#btn-history-clear').addEventListener('click', async () => {
    if (!await confirmDialog('Clear history?', 'Every entry is removed. Run outputs on disk are not touched.', { okLabel: 'Clear', danger: true })) return;
    await api('/history', { method: 'DELETE' });
    loadHistoryList();
  });
  initBodyEditor();
  bindReqTabs();
  bindDescriptionPreview();
  bindShortcuts();
  $('#btn-code').addEventListener('click', openCodeModal);
  $('#btn-cookies').addEventListener('click', openCookieManager);
  $('#btn-help').addEventListener('click', openHelp);
  $('#r-save').addEventListener('click', saveResponseBody);
  $('#r-example').addEventListener('click', pinExample);
  $('#f-url').addEventListener('input', renderPathVars);
  $('#kv-params').addEventListener('input', () => { renderPathVars(); updateTabCounts(); });
  $('#kv-params').addEventListener('click', (e) => { if (e.target.closest('.kv-del')) setTimeout(() => { renderPathVars(); updateTabCounts(); }, 0); });
  $('#kv-headers').addEventListener('input', updateTabCounts);
  $('#kv-headers').addEventListener('click', (e) => { if (e.target.closest('.kv-del')) setTimeout(updateTabCounts, 0); });
  $('#tests-panel').addEventListener('input', updateTabCounts);
  $('#tests-panel').addEventListener('change', updateTabCounts);
  $('#data-file').addEventListener('change', (e) => { if (e.target.files[0]) loadDataFile(e.target.files[0]); e.target.value = ''; });
  $$('#sb-layout button[data-layout]').forEach((b) => b.addEventListener('click', () => { if (appSettings.layout !== b.dataset.layout) saveSettings({ layout: b.dataset.layout }); }));
  $('#r-layout').addEventListener('click', () => saveSettings({ layout: appSettings.layout === 'beside' ? 'below' : 'beside' }));
  $('#btn-sidebar').addEventListener('click', () => setSidebar(!sidebarShown()));
  try { if (localStorage.getItem('pyhttp.side') === '0') setSidebar(false); } catch { /* ignore */ }
  initResizers();
  $('#sb-runs').addEventListener('click', () => setRunsPanel(!runsPanelShown()));
  $('#runs-close').addEventListener('click', () => setRunsPanel(false));
  try { setRunsPanel(localStorage.getItem('pyhttp.runs') !== '0'); } catch { setRunsPanel(true); }
  $('#r-max').addEventListener('click', () => setResponseMax(!$('#editor-form').classList.contains('resp-max')));
  $('#r-collapse').addEventListener('click', () => setResponseCollapsed(!$('#response').classList.contains('collapsed')));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('#editor-form').classList.contains('resp-max') && $('#modal').hidden && !$('.dialog-backdrop')) setResponseMax(false); });
  $('#sb-proxy').addEventListener('click', openSettings);
  // reorder: run before app.js's dragover/drop handlers (capture phase)
  const ul = $('#req-list');
  ul.addEventListener('dragover', (e) => {
    const t = reorderTarget(e);
    if (t) { e.preventDefault(); e.stopImmediatePropagation(); e.dataTransfer.dropEffect = 'move'; clearDropMarks(); showDropMark(t); }
    else clearDropMark();
  }, true);
  ul.addEventListener('drop', async (e) => {
    const t = reorderTarget(e);
    if (!t) { clearDropMark(); return; }
    e.preventDefault(); e.stopImmediatePropagation();
    const d = state.drag;
    state.drag = null;
    clearDropMark(); clearDropMarks();
    $$('.dragging').forEach((x) => x.classList.remove('dragging'));
    try { state.drag = d; await applyReorder(t); } catch (err) { toast(err.message, 'error'); } finally { state.drag = null; }
  }, true);
  ul.addEventListener('dragend', clearDropMark);
  setTimeout(() => checkForUpdate(false), 2500);
  initMonitors();
  initMocks();
  initGit();
  initViews();
}

// ============================================================== monitors
// Scheduled checks: picked requests sent on an interval while the app is open.
// The list lives in the runs panel; a failing monitor shows in the status bar.
const mon = { list: [], timer: null };

const fmtInterval = (sec) =>
  sec % 3600 === 0 ? `${sec / 3600} h` : sec % 60 === 0 ? `${sec / 60} min` : `${sec} s`;

function agoText(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

async function loadMonitors(render = true) {
  let data;
  try { data = await api('/monitors'); } catch { return; }
  mon.list = data.monitors;
  const sb = $('#sb-monitors');
  sb.hidden = !data.failing;
  sb.textContent = data.failing === 1 ? '⚠ 1 monitor failing' : `⚠ ${data.failing} monitors failing`;
  const badge = $('#mon-badge');
  badge.hidden = !data.failing;
  badge.textContent = data.failing || '';
  if (render && $('#mon-list')) renderMonitors();
}

// the Monitors dialog (topbar button, or the ⚠ chip in the status bar)
function openMonitorsModal() {
  openModal('Monitors', `
    <div class="mon-bar">
      <span class="muted small grow">Picked requests are sent again on a schedule while the app is open. A check fails when a request errors, answers 4xx/5xx, or fails a test; failures show in the status bar and can POST to a webhook.</span>
      <button id="btn-mon-new" class="primary" title="Monitor requests on a schedule">+ Monitor</button>
    </div>
    <div id="mon-list" class="mon-list"></div>
  `, [{ label: 'Done', cls: 'primary', onClick: closeModal }]);
  $('#btn-mon-new').addEventListener('click', () => monitorDialog());
  renderMonitors();
  loadMonitors();   // refresh in the background; renders again when it lands
}

function monSpark(recent) {
  if (!recent.length) return '<span class="muted small">no checks yet</span>';
  const max = Math.max(...recent.map((c) => c.duration_ms || 1), 1);
  return `<span class="mon-spark">${recent.map((c) =>
    `<i class="${c.ok ? '' : 'bad'}" style="height:${Math.max(18, Math.round((c.duration_ms || 1) / max * 100))}%"` +
    ` title="${new Date(c.ts * 1000).toLocaleString()} · ${c.ok ? 'pass' : `${c.failed} of ${c.total} failed`} · ${fmtMs(c.duration_ms)}"></i>`).join('')}</span>`;
}

function renderMonitors() {
  const box = $('#mon-list');
  if (!box) return;
  if (!mon.list.length) {
    box.innerHTML = '<div class="empty"><p>No monitors yet.</p><p class="muted small">A monitor sends picked requests every few minutes and flags failures (errors, 4xx/5xx, failed tests). Press “+ Monitor”.</p></div>';
    return;
  }
  box.innerHTML = mon.list.map((m) => {
    const st = !m.enabled ? '<span class="badge">off</span>'
      : m.last_status === 'fail' ? '<span class="badge failed">fail</span>'
      : m.last_status === 'pass' ? '<span class="badge ok">pass</span>' : '<span class="badge">waiting</span>';
    const last = m.last_run_at ? `${agoText(m.last_run_at)}` : 'not run yet';
    return `<div class="mon-item ${m.enabled ? '' : 'off'}" data-id="${m.id}">
      <div class="mon-head">
        <label class="switch" title="Pause or resume this monitor"><input type="checkbox" data-act="toggle" ${m.enabled ? 'checked' : ''}><span></span></label>
        <span class="mon-name">${esc(m.name)}</span>
        ${st}
      </div>
      <div class="mon-meta muted small">${m.request_ids.length} request${m.request_ids.length === 1 ? '' : 's'} · every ${fmtInterval(m.interval_s)} · ${last}${m.webhook_url ? ' · webhook' : ''}</div>
      ${monSpark(m.recent || [])}
      <div class="mon-actions">
        <button class="link small" data-act="results">Results</button>
        <button class="link small" data-act="run">Run now</button>
        <button class="link small" data-act="edit">Edit</button>
        <span class="grow"></span>
        <button class="link small danger" data-act="del">Delete</button>
      </div>
    </div>`;
  }).join('');
  $$('.mon-item', box).forEach((el) => {
    const m = mon.list.find((x) => x.id === el.dataset.id);
    $('[data-act=toggle]', el).addEventListener('change', async (e) => {
      try { await api('/monitors/' + m.id, { method: 'PATCH', body: { enabled: e.target.checked } }); } catch (err) { toast(err.message, 'error'); }
      loadMonitors();
    });
    $('[data-act=run]', el).addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.innerHTML = '<span class="spin"></span>Running…';
      try {
        const chk = await api(`/monitors/${m.id}/run-now`, { method: 'POST' });
        toast(chk.ok ? `${m.name}: all ${chk.total} passed` : `${m.name}: ${chk.failed} of ${chk.total} failed`, chk.ok ? 'ok' : 'error');
      } catch (err) { toast(err.message, 'error'); }
      loadMonitors();
    });
    $('[data-act=results]', el).addEventListener('click', () => monitorResults(m));
    $('[data-act=edit]', el).addEventListener('click', () => monitorDialog(m));
    $('[data-act=del]', el).addEventListener('click', async () => {
      if (!await confirmDialog('Delete monitor?', `${strong(m.name)} and its check history will be removed. The requests themselves stay.`, { okLabel: 'Delete', danger: true })) return;
      try { await api('/monitors/' + m.id, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
      loadMonitors();
    });
  });
}

async function monitorResults(m) {
  let checks = [];
  try { checks = await api(`/monitors/${m.id}/checks?limit=50`); } catch (err) { toast(err.message, 'error'); return; }
  const rows = checks.map((c) => {
    const failedNames = (c.results || []).filter((r) => !r.ok)
      .map((r) => `${esc(r.name)} (${r.status_code ?? esc((r.error || 'error').slice(0, 60))}${r.tests_failed ? `, ${r.tests_failed} tests` : ''})`).join(', ');
    return `<tr><td>${new Date(c.ts * 1000).toLocaleString()}</td>
      <td class="${c.ok ? 'ok-text' : 'err-text'}">${c.ok ? 'pass' : `${c.failed}/${c.total} failed`}</td>
      <td>${fmtMs(c.duration_ms)}</td><td class="mon-fails">${failedNames || ''}</td></tr>`;
  }).join('');
  openModal(`Monitor: ${m.name}`, `
    <p class="muted small">Every ${fmtInterval(m.interval_s)}, newest first. A check fails when a request errors, answers 4xx/5xx, or fails a test.</p>
    ${checks.length ? `<table class="htable"><tr><th>When</th><th>Result</th><th>Took</th><th>Failures</th></tr>${rows}</table>` : '<p class="muted">No checks yet. “Run now” sends one at once.</p>'}
  `, [{ label: 'Back to monitors', cls: 'primary', onClick: openMonitorsModal }]);
}

function monitorDialog(existing = null) {
  const m = existing || { name: '', request_ids: state.selected.map((x) => x.id), interval_s: 300, webhook_url: '' };
  const unit = m.interval_s % 3600 === 0 ? 3600 : m.interval_s % 60 === 0 ? 60 : 1;
  const picked = new Set(m.request_ids);
  const reqRow = (r) => `<label class="mon-pick-row"><input type="checkbox" value="${r.id}" ${picked.has(r.id) ? 'checked' : ''}>
    <span class="method m-${esc(r.method)}">${esc(r.method)}</span><span class="mon-pick-name">${esc(r.name)}</span></label>`;
  openModal(existing ? 'Edit monitor' : 'New monitor', `
    <label class="field">Name <input id="mon-name" class="grow" value="${esc(m.name)}" placeholder="e.g. Production health"></label>
    <label class="field">Every <input id="mon-every" type="number" min="1" step="1" value="${m.interval_s / unit}" style="width:80px">
      <select id="mon-unit"><option value="60" ${unit === 60 ? 'selected' : ''}>minutes</option><option value="1" ${unit === 1 ? 'selected' : ''}>seconds</option><option value="3600" ${unit === 3600 ? 'selected' : ''}>hours</option></select>
      <span class="muted small">while the app is open</span></label>
    <label class="field">Webhook <input id="mon-hook" class="grow mono" value="${esc(m.webhook_url || '')}" placeholder="optional: a URL to POST to when a check fails"></label>
    <div class="muted small" style="margin-bottom:6px">Requests, sent in this order (extractions and <code>{{prev.…}}</code> work between them):</div>
    <input id="mon-pick-q" type="search" placeholder="Filter requests…" autocomplete="off" style="width:100%;margin-bottom:6px">
    <div id="mon-pick" class="mon-pick">${state.requests.map(reqRow).join('') || '<p class="muted small">No requests yet: create one first.</p>'}</div>
  `, [
    { label: 'Cancel', onClick: openMonitorsModal },
    { label: existing ? 'Save' : 'Create monitor', cls: 'primary', onClick: async () => {
      const ids = $$('#mon-pick input:checked').map((i) => i.value);
      const every = Math.max(1, parseInt($('#mon-every').value, 10) || 5) * parseInt($('#mon-unit').value, 10);
      const body = { name: $('#mon-name').value.trim() || 'Monitor', request_ids: ids,
                     interval_s: Math.max(10, every), webhook_url: $('#mon-hook').value.trim() };
      try {
        if (existing) await api('/monitors/' + existing.id, { method: 'PATCH', body });
        else await api('/monitors', { method: 'POST', body });
        toast(existing ? 'Monitor saved' : 'Monitor created: it runs on its schedule while the app is open', 'ok');
        openMonitorsModal();
      } catch (err) { toast(err.message, 'error'); }
    } },
  ]);
  $('#mon-pick-q').addEventListener('input', (e) => {
    const q = e.target.value.toLowerCase();
    $$('#mon-pick .mon-pick-row').forEach((row) => { row.hidden = q && !row.textContent.toLowerCase().includes(q); });
  });
}

function initMonitors() {
  $('#btn-monitors').addEventListener('click', openMonitorsModal);
  $('#sb-monitors').addEventListener('click', openMonitorsModal);
  loadMonitors(false);
  mon.timer = setInterval(() => loadMonitors(true), 30000);   // renders only while the dialog is open
}

// ========================================================== mock servers
// Fake endpoints answered by pyhttp itself, on the port it already listens on:
// http://127.0.0.1:<port>/mock/<slug>/<route path>. No second server to start,
// no second port to remember, and `{{mock_url}}` works whatever the port is.
const mocks = { list: [], baseUrl: '', callTimer: null, timer: null };
const MOCK_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'ANY'];
const MOCK_TYPES = ['application/json', 'text/plain', 'text/html', 'application/xml', 'text/csv',
                    'application/javascript', 'application/octet-stream'];
const mockById = (id) => mocks.list.find((m) => m.id === id) || null;

async function loadMocks(render = true) {
  let data;
  try { data = await api('/mocks'); } catch { return; }
  mocks.list = data.mocks;
  mocks.baseUrl = data.base_url;
  const badge = $('#mock-badge');
  badge.hidden = !data.enabled;
  badge.textContent = data.enabled || '';
  $('#btn-mocks').title = data.enabled
    ? `${data.enabled} mock server${data.enabled === 1 ? '' : 's'} answering under ${data.base_url}/`
    : 'Mock servers: fake endpoints answered by pyhttp itself, on the port it already runs on';
  if (render && $('#mock-list')) renderMocks();
}

// the Mocks dialog (topbar → Mocks)
function openMocksModal() {
  openModal('Mock servers', `
    <div class="mon-bar">
      <span class="muted small grow">Endpoints answered by pyhttp itself, on the port it already runs on — nothing else to start.
        Point an app or a request at <code class="mono">${esc(mocks.baseUrl || '/mock')}/&lt;name&gt;/…</code> and it gets your canned answer.</span>
      <button id="btn-mock-new" class="primary" title="Add a mock server">+ Mock server</button>
    </div>
    <div id="mock-list" class="mon-list"></div>
  `, [{ label: 'Done', cls: 'primary', onClick: closeModal }]);
  $('#btn-mock-new').addEventListener('click', () => mockDialog());
  renderMocks();
  loadMocks();   // refresh in the background; renders again when it lands
}

function renderMocks() {
  const box = $('#mock-list');
  if (!box) return;
  if (!mocks.list.length) {
    box.innerHTML = '<div class="empty"><p>No mock servers yet.</p>'
      + '<p class="muted small">A mock server answers a list of routes under <code>/mock/&lt;name&gt;/</code> on this very port, so a front end or a test '
      + 'can call an endpoint that does not exist yet. Press “+ Mock server”.</p></div>';
    return;
  }
  box.innerHTML = mocks.list.map((m) => {
    const all = m.routes || [];
    const off = all.filter((r) => !r.enabled).length;
    return `<div class="mon-item mock-item ${m.enabled ? '' : 'off'}" data-id="${m.id}">
      <div class="mon-head">
        <label class="switch" title="${m.enabled ? 'Stop answering requests' : 'Start answering requests'}"><input type="checkbox" data-act="toggle" ${m.enabled ? 'checked' : ''}><span></span></label>
        <span class="mon-name">${esc(m.name)}</span>
        ${m.enabled ? '<span class="badge ok">live</span>' : '<span class="badge">off</span>'}
      </div>
      <button class="mock-url mono" data-act="copy" title="Copy this base URL">${esc(m.url)}<span class="mock-copy">Copy</span></button>
      <div class="mon-meta muted small">${all.length} route${all.length === 1 ? '' : 's'}${off ? ` (${off} off)` : ''}
        · ${m.hits} call${m.hits === 1 ? '' : 's'} answered${m.delay_ms ? ` · ${fmtMs(m.delay_ms)} delay` : ''}${m.cors ? ' · CORS' : ''}${m.description ? ` · ${esc(m.description)}` : ''}</div>
      <div class="mon-actions">
        <button class="link small" data-act="routes">Routes</button>
        <button class="link small" data-act="calls">Calls</button>
        <button class="link small" data-act="edit">Edit</button>
        <span class="grow"></span>
        <button class="link small danger" data-act="del">Delete</button>
      </div>
    </div>`;
  }).join('');
  $$('.mock-item', box).forEach((el) => {
    const m = mockById(el.dataset.id);
    $('[data-act=toggle]', el).addEventListener('change', async (e) => {
      try { await api('/mocks/' + m.id, { method: 'PATCH', body: { enabled: e.target.checked } }); }
      catch (err) { toast(err.message, 'error'); }
      loadMocks();
    });
    $('[data-act=copy]', el).addEventListener('click', async () => {
      const ok = await copyText(m.url);
      toast(ok ? `Copied ${m.url}` : 'Could not copy', ok ? 'ok' : 'error');
    });
    $('[data-act=routes]', el).addEventListener('click', () => openMockRoutes(m.id));
    $('[data-act=calls]', el).addEventListener('click', () => openMockCalls(m.id));
    $('[data-act=edit]', el).addEventListener('click', () => mockDialog(m));
    $('[data-act=del]', el).addEventListener('click', async () => {
      if (!await confirmDialog('Delete mock server?', `${strong(m.name)}, its routes and its call log will be removed. Requests pointing at it stay, but stop getting an answer.`, { okLabel: 'Delete', danger: true })) return;
      try { await api('/mocks/' + m.id, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
      loadMocks();
    });
  });
}

function mockDialog(existing = null) {
  const m = existing || { name: '', slug: '', description: '', delay_ms: 0, cors: true, log_calls: true };
  openModal(existing ? 'Edit mock server' : 'New mock server', `
    <label class="field">Name <input id="mk-name" class="grow" value="${esc(m.name)}" placeholder="e.g. Payments API"></label>
    <label class="field">Base URL <span class="mono muted small">${esc(mocks.baseUrl || '/mock')}/</span>
      <input id="mk-slug" class="mono" value="${esc(m.slug || '')}" placeholder="from the name" style="width:170px"></label>
    <label class="field">Note <input id="mk-desc" class="grow" value="${esc(m.description || '')}" placeholder="optional, shown in the list"></label>
    <label class="field">Slow every answer by <input id="mk-delay" type="number" min="0" step="50" value="${m.delay_ms || 0}" style="width:90px">
      <span class="muted small">ms — to see how your app behaves on a slow API</span></label>
    <label class="field"><input type="checkbox" id="mk-cors" ${m.cors ? 'checked' : ''}> Send CORS headers and answer preflights
      <span class="muted small">(so a browser app on another origin can call it)</span></label>
    <label class="field"><input type="checkbox" id="mk-log" ${m.log_calls ? 'checked' : ''}> Log the calls it answers
      <span class="muted small">(the last 200, with their headers and body)</span></label>
  `, [
    { label: 'Cancel', onClick: openMocksModal },
    { label: existing ? 'Save' : 'Create mock server', cls: 'primary', onClick: async () => {
      const body = { name: $('#mk-name').value.trim(), slug: $('#mk-slug').value.trim(),
                     description: $('#mk-desc').value.trim(),
                     delay_ms: Math.max(0, parseFloat($('#mk-delay').value) || 0),
                     cors: $('#mk-cors').checked, log_calls: $('#mk-log').checked };
      if (!body.name) { toast('Give the mock server a name', 'error'); return; }
      if (!body.slug) delete body.slug;
      try {
        const saved = existing ? await api('/mocks/' + existing.id, { method: 'PATCH', body })
                               : await api('/mocks', { method: 'POST', body });
        await loadMocks(false);
        if (existing) { openMocksModal(); toast('Mock server saved', 'ok'); }
        else { openMockRoutes(saved.id); toast(`Answering on ${saved.url}`, 'ok'); }
      } catch (err) { toast(err.message, 'error'); }
    } },
  ]);
  $('#mk-name').focus();
}

// ----------------------------------------------------------- routes of one mock
function openMockRoutes(mockId) {
  const m = mockById(mockId);
  if (!m) { openMocksModal(); return; }
  const routes = m.routes || [];
  const rows = routes.map((r, i) => `<tr class="mock-route ${r.enabled ? '' : 'off'}" data-id="${r.id}">
    <td><label class="switch tiny" title="${r.enabled ? 'Turn this route off' : 'Turn this route on'}"><input type="checkbox" data-act="toggle" ${r.enabled ? 'checked' : ''}><span></span></label></td>
    <td><span class="method m-${esc(r.method)}">${esc(r.method)}</span></td>
    <td class="mock-path">${esc(r.path)}${r.match_query ? `<span class="muted">?${esc(r.match_query)}</span>` : ''}${r.name ? `<div class="muted small">${esc(r.name)}</div>` : ''}</td>
    <td class="${r.status_code >= 400 ? 'err-text' : 'ok-text'}">${r.status_code}</td>
    <td class="muted">${r.hits || 0}</td>
    <td class="mock-acts">
      <button class="link small" data-act="up" title="Move up: the first matching route wins" ${i === 0 ? 'disabled' : ''}>↑</button>
      <button class="link small" data-act="down" title="Move down" ${i === routes.length - 1 ? 'disabled' : ''}>↓</button>
      <button class="link small" data-act="edit">Edit</button>
      <button class="link small" data-act="try" title="Open a new request pointing at this route">Try</button>
      <button class="link small danger" data-act="del" title="Delete this route">✕</button>
    </td></tr>`).join('');
  openModal(`Mock: ${m.name}`, `
    <div class="mon-bar">
      <span class="muted small grow">Base URL <code class="mono">${esc(m.url)}</code>, or write <code class="mono">{{mock_url}}/${esc(m.slug)}</code> in a request so it follows the port.
        Exact paths beat <code>:id</code> ones, and a route with a query condition beats one without.</span>
      <button id="btn-mock-route-new" class="primary">+ Route</button>
      <button id="btn-mock-seed" title="Add a route per saved request, answering with the response it last gave">From requests…</button>
    </div>
    ${routes.length ? `<table class="htable mock-routes"><tr><th></th><th>Method</th><th>Path</th><th>Status</th><th title="Calls this route has answered">Hits</th><th></th></tr>${rows}</table>`
      : '<div class="empty"><p>No routes yet.</p><p class="muted small">A route is a method, a path and the answer to give back. '
        + 'Press “+ Route”, or build one per request you already have.</p></div>'}
  `, [
    { label: 'Back to mocks', onClick: openMocksModal },
    { label: 'Calls', onClick: () => openMockCalls(m.id) },
    { label: 'Done', cls: 'primary', onClick: closeModal },
  ]);
  $('#btn-mock-route-new').addEventListener('click', () => mockRouteDialog(m.id));
  $('#btn-mock-seed').addEventListener('click', () => mockSeedDialog(m.id));
  $$('.mock-route').forEach((el) => {
    const r = routes.find((x) => x.id === el.dataset.id);
    const patch = async (body) => {
      try { await api(`/mocks/${m.id}/routes/${r.id}`, { method: 'PATCH', body }); } catch (err) { toast(err.message, 'error'); }
      await loadMocks(false);
      openMockRoutes(m.id);
    };
    const move = async (delta) => {
      const ids = routes.map((x) => x.id);
      const at = ids.indexOf(r.id);
      ids.splice(at + delta, 0, ...ids.splice(at, 1));
      try { await api(`/mocks/${m.id}/routes/reorder`, { method: 'POST', body: { route_ids: ids } }); } catch (err) { toast(err.message, 'error'); }
      await loadMocks(false);
      openMockRoutes(m.id);
    };
    $('[data-act=toggle]', el).addEventListener('change', (e) => patch({ enabled: e.target.checked }));
    $('[data-act=up]', el).addEventListener('click', () => move(-1));
    $('[data-act=down]', el).addEventListener('click', () => move(1));
    $('[data-act=edit]', el).addEventListener('click', () => mockRouteDialog(m.id, r));
    $('[data-act=try]', el).addEventListener('click', () => mockTryRoute(m, r));
    $('[data-act=del]', el).addEventListener('click', async () => {
      if (!await confirmDialog('Delete route?', `${strong(`${r.method} ${r.path}`)} will no longer be answered.`, { okLabel: 'Delete', danger: true })) return;
      try { await api(`/mocks/${m.id}/routes/${r.id}`, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
      await loadMocks(false);
      openMockRoutes(m.id);
    });
  });
}

function mockRouteDialog(mockId, existing = null) {
  const m = mockById(mockId);
  const r = existing || { name: '', method: 'GET', path: '/', status_code: 200, content_type: 'application/json',
                          body: '{\n  "ok": true\n}', delay_ms: 0, match_query: '', headers: [], enabled: true };
  openModal(existing ? 'Edit route' : 'New route', `
    <datalist id="mock-content-types">${MOCK_TYPES.map((t) => `<option>${t}</option>`).join('')}</datalist>
    <label class="field">Answers
      <select id="mr-method" class="method-select m-${esc(r.method)}" title="ANY answers every method">${MOCK_METHODS.map((x) => `<option ${x === r.method ? 'selected' : ''}>${x}</option>`).join('')}</select>
      <input id="mr-path" class="grow mono" value="${esc(r.path)}" placeholder="/users/:id" spellcheck="false" autocomplete="off"></label>
    <p class="muted small mock-hint">→ <code class="mono" id="mr-preview"></code><br>
      <code>:id</code> or <code>{id}</code> captures one segment as <code>{{req.params.id}}</code>; <code>*</code> matches one segment and <code>**</code> the rest of the path.</p>
    <label class="field">Name <input id="mr-name" class="grow" value="${esc(r.name)}" placeholder="optional, e.g. “user not found”"></label>
    <div class="field mock-row">
      <label>Status <input id="mr-status" type="number" min="100" max="599" value="${r.status_code}" style="width:84px"></label>
      <label>Content-Type <input id="mr-ct" class="mono" value="${esc(r.content_type)}" list="mock-content-types" style="width:200px"></label>
      <label>Delay <input id="mr-delay" type="number" min="0" step="50" value="${r.delay_ms || 0}" style="width:90px"> <span class="muted small">ms</span></label>
    </div>
    <label class="field">Only when the query has
      <input id="mr-when" class="grow mono" value="${esc(r.match_query || '')}" placeholder="optional: fail=1&kind=card — lets one path give several answers" spellcheck="false" autocomplete="off"></label>
    <div class="muted small mock-label">Response headers</div>
    <div id="mr-headers" class="kv"></div>
    <div class="muted small mock-label">Response body — a template:
      <code>{{req.params.id}}</code>, <code>{{req.query.q}}</code>, <code>{{req.json.name}}</code>,
      <code>{{req.headers.x-key}}</code>, <code>{{$guid}}</code> and your own <code>{{variables}}</code></div>
    <textarea id="mr-body" class="mono mock-body" spellcheck="false" rows="9">${esc(r.body)}</textarea>
  `, [
    { label: 'Cancel', onClick: () => openMockRoutes(mockId) },
    { label: existing ? 'Save route' : 'Add route', cls: 'primary', onClick: async () => {
      const body = {
        name: $('#mr-name').value.trim(), method: $('#mr-method').value,
        path: $('#mr-path').value.trim() || '/', status_code: parseInt($('#mr-status').value, 10) || 200,
        content_type: $('#mr-ct').value.trim() || 'application/json', body: $('#mr-body').value,
        delay_ms: Math.max(0, parseFloat($('#mr-delay').value) || 0),
        match_query: $('#mr-when').value.trim().replace(/^\?/, ''),
        headers: readKv($('#mr-headers')),
      };
      try {
        if (existing) await api(`/mocks/${mockId}/routes/${existing.id}`, { method: 'PATCH', body });
        else await api(`/mocks/${mockId}/routes`, { method: 'POST', body });
        await loadMocks(false);
        openMockRoutes(mockId);
        toast(existing ? 'Route saved' : `Answering ${body.method} ${body.path}`, 'ok');
      } catch (err) { toast(err.message, 'error'); }
    } },
  ]);
  renderKv($('#mr-headers'), r.headers || [], true);
  const method = $('#mr-method');
  const preview = () => {
    $('#mr-preview').textContent = `${method.value} ${m ? m.url : ''}${$('#mr-path').value.trim().replace(/^\/?/, '/').replace(/\/$/, '')}`
      + ($('#mr-when').value.trim() ? `?${$('#mr-when').value.trim().replace(/^\?/, '')}` : '');
    method.className = 'method-select m-' + method.value;
  };
  method.addEventListener('change', preview);
  $('#mr-path').addEventListener('input', preview);
  $('#mr-when').addEventListener('input', preview);
  preview();
  $('#mr-path').focus();
}

// Routes from requests already in the collection, answering with what each last returned.
function mockSeedDialog(mockId) {
  const reqRow = (r) => `<label class="mon-pick-row"><input type="checkbox" value="${r.id}">
    <span class="method m-${esc(r.method)}">${esc(r.method)}</span><span class="mon-pick-name">${esc(r.name)}</span></label>`;
  openModal('Routes from requests', `
    <p class="muted small">One route per request: its method and the path part of its URL, answering with the response that request
      last gave (from the request history) or an empty <code>{}</code> when it has never been sent. Edit any of them afterwards.</p>
    <input id="mock-seed-q" type="search" placeholder="Filter requests…" autocomplete="off" style="width:100%;margin-bottom:6px">
    <div id="mock-seed-pick" class="mon-pick">${state.requests.map(reqRow).join('') || '<p class="muted small">No requests yet: create one first.</p>'}</div>
  `, [
    { label: 'Cancel', onClick: () => openMockRoutes(mockId) },
    { label: 'Add routes', cls: 'primary', onClick: async () => {
      const ids = $$('#mock-seed-pick input:checked').map((i) => i.value);
      if (!ids.length) { toast('Tick at least one request', 'error'); return; }
      try {
        const out = await api(`/mocks/${mockId}/from-requests`, { method: 'POST', body: { request_ids: ids } });
        await loadMocks(false);
        openMockRoutes(mockId);
        toast(`Added ${out.added} route${out.added === 1 ? '' : 's'}`, 'ok');
      } catch (err) { toast(err.message, 'error'); }
    } },
  ]);
  $('#mock-seed-q').addEventListener('input', (e) => {
    const q = e.target.value.toLowerCase();
    $$('#mock-seed-pick .mon-pick-row').forEach((row) => { row.hidden = q && !row.textContent.toLowerCase().includes(q); });
  });
}

// Open a route as a real request: {{mock_url}} keeps it working whatever port we get.
async function mockTryRoute(m, r) {
  const method = r.method === 'ANY' ? 'GET' : r.method;
  // {id} is written as :id (what the Params tab fills in), and the wildcards get a sample segment
  const path = (r.path === '/' ? '' : r.path)
    .replace(/\{([A-Za-z_]\w*)\}/g, ':$1').replace(/\/\*\*/g, '/sample/path').replace(/\/\*/g, '/sample');
  const params = (r.match_query || '').split('&').filter(Boolean).map((pair) => {
    const [key, ...rest] = pair.split('=');
    return { key, value: rest.join('='), enabled: true };
  });
  // a value per path variable, so Send works straight away
  (path.match(/(?<=\/):([A-Za-z_]\w*)/g) || []).forEach((name) => {
    params.push({ key: name.slice(1), value: '1', enabled: true });
  });
  try {
    await saveNow();
    const created = await api('/requests', { method: 'POST', body: {
      name: r.name || `${method} ${r.path}`,
      method,
      url: `{{mock_url}}/${m.slug}${path}`,
      description: `Mock route in “${m.name}”. \`{{mock_url}}\` is this app's own /mock base, so the URL follows the port.`,
      params,
    } });
    closeModal();
    await loadRequests();
    await openRequest(created.id);
    toast(`New request pointing at ${method} ${r.path}`, 'ok');
  } catch (err) { toast(err.message, 'error'); }
}

// ------------------------------------------------------------ the call log
async function openMockCalls(mockId) {
  const m = mockById(mockId);
  if (!m) { openMocksModal(); return; }
  let calls = [];
  try { calls = await api(`/mocks/${m.id}/calls?limit=200`); } catch (err) { toast(err.message, 'error'); return; }
  openModal(`Calls: ${m.name}`, `
    <div class="mon-bar">
      <span class="muted small grow">Every request this mock answered, newest first — click one to see the headers and body it arrived with.
        ${m.log_calls ? 'Refreshes while this is open.' : '<b>Logging is off for this mock</b> (turn it on in Edit).'}</span>
      <button id="btn-mock-calls-clear" class="danger">Clear</button>
    </div>
    <div id="mock-calls-wrap"></div>
  `, [
    { label: 'Routes', onClick: () => openMockRoutes(m.id) },
    { label: 'Back to mocks', onClick: openMocksModal },
    { label: 'Done', cls: 'primary', onClick: closeModal },
  ]);
  $('#btn-mock-calls-clear').addEventListener('click', async () => {
    if (!await confirmDialog('Clear the call log?', `Every logged call of ${strong(m.name)} is removed. Its routes stay.`, { okLabel: 'Clear', danger: true })) return;
    try { await api(`/mocks/${m.id}/calls`, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
    openMockCalls(m.id);
  });
  renderMockCalls(m, calls);
  clearInterval(mocks.callTimer);
  mocks.callTimer = setInterval(async () => {
    if (!$('#mock-calls-wrap')) { clearInterval(mocks.callTimer); return; }   // dialog closed or moved on
    try { renderMockCalls(m, await api(`/mocks/${m.id}/calls?limit=200`)); } catch { /* keep what is on screen */ }
  }, 2000);
}

function renderMockCalls(m, calls) {
  const wrap = $('#mock-calls-wrap');
  if (!wrap) return;
  if (!calls.length) {
    wrap.innerHTML = '<div class="empty"><p>No calls yet.</p><p class="muted small">Point something at '
      + `<code class="mono">${esc(m.url)}</code> and it shows up here.</p></div>`;
    return;
  }
  wrap.innerHTML = `<table class="htable mock-calls"><tr><th>When</th><th>Method</th><th>Path</th><th>Status</th><th>Took</th><th>Route</th></tr>
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

function initMocks() {
  $('#btn-mocks').addEventListener('click', openMocksModal);
  loadMocks(false);
  mocks.timer = setInterval(() => loadMocks(true), 30000);   // renders only while the dialog is open
}

// ========================================================= response views
// A request can name HTML pages that draw its response their own way (a chart, a table, a
// map…). pyhttp keeps the response under a short-lived address and opens each page with
// ?api=<that address>; the page reads it from its URL and fetches it. Views show in the View
// tab of a response and in a history entry, one at a time or all side by side, and open in the
// browser too.
const viewUi = { sessions: new WeakMap() };   // result -> { view file: { url, api } }
const VIEW_ICON = '<svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18M8 17v-3M12 17v-5M16 17v-2"/></g></svg>';
const viewsOf = (req) => ((req && req.options && req.options.views) || []);
// The request's own views, then the ones its folders give to every request inside them.
function mergeViews(own, inherited) {
  const files = new Set(own.map((v) => v.file));
  return [...own, ...(inherited || []).filter((v) => !files.has(v.file))];
}
const effectiveViews = () => mergeViews(viewsOf(state.current), inheritedInfo && state.current && state.current.folder_id ? inheritedInfo.views : []);
const newViewId = () => 'v' + Math.random().toString(36).slice(2, 9);

async function viewSession(result, view, name) {
  let per = viewUi.sessions.get(result);
  if (!per) { per = {}; viewUi.sessions.set(result, per); }
  if (!per[view.file]) {
    const resp = result.response || {};
    per[view.file] = await api('/views/open', { method: 'POST', body: { file: view.file, name, sent_at: result.ts || null, result: {
      status: result.status, status_code: result.status_code, reason: result.reason, error: result.error,
      duration_ms: result.duration_ms, body_name: result.body_name,
      history_id: result.history_id || null,   // a body too large for the viewer is served whole from the history's copy
      request: { method: (result.request || {}).method, url: (result.request || {}).url },
      response: { headers: resp.headers, content_type: resp.content_type, size: resp.size, body_text: resp.body_text,
                  body_base64: resp.body_base64, truncated: resp.truncated } } } });
  }
  return per[view.file];
}

// Draw the views of one response into `box`. ctx: { result, views, name, onEdit?, onStarter? }
function mountViews(box, ctx) {
  const { result, views, name } = ctx;
  if (!views.length) {
    box.innerHTML = `<div class="vw-empty">${VIEW_ICON}
      <h4>See this response your way</h4>
      <p>Attach an HTML page to this request and pyhttp opens it with the response: a chart, a table, a dashboard,
        a receipt, whatever the data deserves. Attach several and look at them side by side.</p>
      <div class="vw-actions"><button class="primary" data-act="starter">Create a starter view</button><button data-act="edit">Choose HTML files…</button></div>
      <p class="muted small">The page finds the response in its own address: <code>new URLSearchParams(location.search).get('api')</code>, then <code>fetch(api)</code>.</p></div>`;
    $('[data-act="starter"]', box).addEventListener('click', ctx.onStarter);
    $('[data-act="edit"]', box).addEventListener('click', ctx.onEdit);
    return;
  }
  if (!result || !result.response) { box.innerHTML = '<div class="muted small">There is no response to show in a view.</div>'; return; }
  let sel = box.dataset.sel;
  if (!(sel === 'all' && views.length > 1) && !views.some((v) => v.id === sel)) sel = views[0].id;
  const draw = () => {
    box.dataset.sel = sel;
    const shown = sel === 'all' ? views : views.filter((v) => v.id === sel);
    const many = shown.length > 1;
    box.innerHTML = `<div class="vw-bar">
        <div class="vw-chips" role="tablist">${views.length > 1 ? `<button class="vw-chip ${sel === 'all' ? 'on' : ''}" data-v="all" title="Show every view side by side">All ${views.length}</button>` : ''}${
          views.map((v) => `<button class="vw-chip ${sel === v.id ? 'on' : ''}" data-v="${esc(v.id)}" title="${esc(v.file)}">${esc(v.name)}</button>`).join('')}</div>
        <span class="grow"></span>
        <button class="link small" data-act="reload" title="Load the page${many ? 's' : ''} again, for example after editing the HTML file">↻ Reload</button>
        <button class="link small" data-act="open" title="Open ${many ? 'every view' : 'this view'} in your browser">↗ Open${many ? ' all' : ''} in browser</button>
        ${ctx.onEdit ? '<button class="link small" data-act="edit" title="Add, rename or remove the HTML pages of this request">Edit views…</button>' : ''}
      </div>
      ${result.response.truncated ? '<div class="vw-note small">This response was cut short when it was stored, so a view may get incomplete data.</div>' : ''}
      <div class="vw-grid ${many ? 'many' : ''}">${shown.map((v) => `<div class="vw-frame" data-id="${esc(v.id)}">
        ${many ? `<div class="vw-title"><b>${esc(v.name)}</b><button class="link small" data-one="${esc(v.id)}" title="Show only this view">Expand</button></div>` : ''}
        <iframe title="${esc(v.name)}" sandbox="allow-scripts allow-popups allow-forms allow-modals allow-downloads" referrerpolicy="no-referrer"></iframe>
        <div class="vw-err small" hidden></div></div>`).join('')}</div>`;
    $$('.vw-chip', box).forEach((b) => b.addEventListener('click', () => { sel = b.dataset.v; draw(); }));
    $$('[data-one]', box).forEach((b) => b.addEventListener('click', () => { sel = b.dataset.one; draw(); }));
    const load = (v, fresh) => {
      const cell = $(`.vw-frame[data-id="${CSS.escape(v.id)}"]`, box);
      if (!cell) return Promise.resolve(null);
      if (fresh) delete (viewUi.sessions.get(result) || {})[v.file];
      return viewSession(result, v, name).then((s) => {
        $('.vw-err', cell).hidden = true;
        $('iframe', cell).hidden = false;
        $('iframe', cell).src = `${s.url}&theme=${document.documentElement.getAttribute('data-theme') || 'dark'}`;
        return s;
      }).catch((e) => {
        $('iframe', cell).hidden = true;
        $('.vw-err', cell).hidden = false;
        $('.vw-err', cell).textContent = `${v.name}: ${e.message}`;
        return null;
      });
    };
    shown.forEach((v) => load(v, false));
    $('[data-act="reload"]', box).addEventListener('click', () => shown.forEach((v) => load(v, true)));
    $('[data-act="open"]', box).addEventListener('click', () => openViewsOutside(result, shown, name));
    if (ctx.onEdit) $('[data-act="edit"]', box).addEventListener('click', ctx.onEdit);
  };
  draw();
}

async function openViewsOutside(result, views, name) {
  for (const v of views) {
    try { openExternal((await viewSession(result, v, name)).url); } catch (e) { toast(`${v.name}: ${e.message}`, 'error'); }
  }
}

// ---- the View tab of the response
function renderRespView() {
  const box = $('#r-view');
  if (!box || !state.current) return;
  mountViews(box, { result: lastResult, views: effectiveViews(), name: state.current.name,
    onEdit: openViewsDialog, onStarter: () => addStarterView() });
}
function setViews(views, viewFirst) {
  const opts = { ...(state.current.options || {}), views };
  if (viewFirst === true) opts.view_first = true; else if (viewFirst === false) delete opts.view_first;
  state.current.options = opts;
  scheduleSave();
  updateViewTab();
  renderRespView();
}
function updateViewTab() {
  const n = effectiveViews().length;
  $('#n-r-view').textContent = n ? String(n) : '';
  $('#n-views').textContent = n ? String(n) : '';
  renderViewsPanel();
}
// the Views tab of the request editor: what is attached, before anything is sent
function renderViewsPanel() {
  const box = $('#views-panel');
  if (!box || !state.current) return;
  const views = viewsOf(state.current);
  const fromFolders = effectiveViews().slice(views.length);
  box.innerHTML = `<div class="vwp">
    <p class="muted small">HTML pages of your own that draw this request's response: a chart, a table, a dashboard. After you send,
      they show in the <b>View</b> tab of the response, and for every past call in <b>Request history</b>.</p>
    ${views.length ? `<ul class="vwp-list">${views.map((v) => `<li><span class="vwp-name">${esc(v.name)}</span><span class="mono muted small" title="${esc(v.file)}">${esc(v.file)}</span></li>`).join('')}</ul>`
      : '<div class="vwp-none muted small">No views on this request yet.</div>'}
    ${fromFolders.length ? `<p class="muted small" style="margin-bottom:4px">From its folders (set in the folder's ⋯ menu → Settings):</p>
      <ul class="vwp-list">${fromFolders.map((v) => `<li><span class="vwp-name">${esc(v.name)} <span class="muted small">· ${esc(v.from || 'folder')}</span></span><span class="mono muted small" title="${esc(v.file)}">${esc(v.file)}</span></li>`).join('')}</ul>` : ''}
    <div class="vw-actions"><button id="vwp-edit" class="${views.length ? '' : 'primary'}">${views.length ? 'Edit views…' : 'Choose HTML files…'}</button><button id="vwp-starter" title="Writes a working page (chart, table, raw data) you can edit">Create a starter view</button></div>
    <p class="muted small">A page finds the response in its own address: <code>new URLSearchParams(location.search).get('api')</code>, then <code>fetch(api)</code>.</p></div>`;
  $('#vwp-edit').addEventListener('click', openViewsDialog);
  $('#vwp-starter').addEventListener('click', () => addStarterView());
}
async function addStarterView() {
  try {
    const made = await api('/views/starter', { method: 'POST', body: { name: state.current.name } });
    const views = [...viewsOf(state.current), { id: newViewId(), name: viewsOf(state.current).length ? 'Starter' : 'Overview', file: made.file }];
    if ($('#vw-rows')) return made;   // called from the dialog: it adds the row itself
    setViews(views);
    toast('Starter view created. Edit the file to make it yours', 'ok', { label: 'Show file', onClick: () => api('/open-path', { method: 'POST', body: { path: made.file } }).catch(() => {}) });
    return made;
  } catch (e) { toast(e.message, 'error'); return null; }
}
// called whenever another response is shown
function afterResponseForViews(result) {
  if (!state.current) return;
  updateViewTab();
  const views = effectiveViews();
  const onView = !$('#r-view').hidden;
  if (views.length && result.response && (state.current.options || {}).view_first && !onView) { showTab($('#resp-tabs'), 'view'); return; }
  if (onView) renderRespView();
}

function openViewsDialog() {
  const rows = viewsOf(state.current).map((v) => ({ ...v }));
  if (!rows.length) rows.push({ id: newViewId(), name: '', file: '' });
  const canPick = !!(native() && native().pick_file);
  openModal('Response views', `
    <p class="muted small set-intro">HTML pages that show this request's response their own way. Give the full path of a file on this computer,
      or the address of a page (<code>http://localhost:5173/chart.html</code>). pyhttp opens each one with <code>?api=…</code> pointing at the response.</p>
    <div class="vw-head muted small"><span>Name</span><span>HTML file or page address</span><span></span></div>
    <div id="vw-rows"></div>
    <div class="vw-actions"><button id="vw-add">+ Add a view</button><button id="vw-starter" title="Writes a working page (chart, table, raw data) you can edit">Create a starter view</button></div>
    <label class="switch-row" style="margin-top:14px"><span class="txt"><b>Show the view first</b><span class="muted small">When a response arrives, open the View tab instead of the body.</span></span><span class="switch"><input type="checkbox" id="vw-first" ${state.current.options && state.current.options.view_first ? 'checked' : ''}><span></span></span></label>
    <details class="vw-how"><summary>How a page reads the response</summary>
<pre class="mono small">const api = new URLSearchParams(location.search).get('api');
const data = await fetch(api).then((r) => r.json());   // the body, as the API sent it
// ?meta=… gives { name, method, url, status, duration_ms, headers, body, json }</pre></details>`,
  [{ label: 'Cancel', onClick: closeModal },
   { label: 'Save', cls: 'primary', onClick: () => {
     const views = rows.map((r) => ({ id: r.id, name: r.name.trim() || fileLabel(r.file), file: r.file.trim() })).filter((r) => r.file);
     const first = $('#vw-first').checked;
     closeModal();
     setViews(views, first && views.length > 0);
     if (views.length) showTab($('#resp-tabs'), 'view');
   } }]);
  const fileLabel = (f) => (f.split(/[\\/]/).pop() || 'View').replace(/\.html?$/i, '').replace(/[?#].*$/, '') || 'View';
  const draw = () => {
    const box = $('#vw-rows');
    box.innerHTML = rows.map((r, i) => `<div class="vw-row"><input data-i="${i}" data-k="name" value="${esc(r.name)}" placeholder="Sales chart"><input class="mono" data-i="${i}" data-k="file" value="${esc(r.file)}" placeholder="C:\\views\\sales.html" spellcheck="false">${canPick ? `<button data-browse="${i}">Browse…</button>` : ''}<button class="kv-del" data-del="${i}" title="Remove" aria-label="Remove this view">✕</button></div>`).join('');
    $$('input', box).forEach((inp) => inp.addEventListener('input', () => { rows[inp.dataset.i][inp.dataset.k] = inp.value; }));
    $$('[data-del]', box).forEach((b) => b.addEventListener('click', () => { rows.splice(Number(b.dataset.del), 1); draw(); }));
    $$('[data-browse]', box).forEach((b) => b.addEventListener('click', async () => {
      const path = await native().pick_file().catch(() => null);
      if (path) { rows[b.dataset.browse].file = path; if (!rows[b.dataset.browse].name) rows[b.dataset.browse].name = fileLabel(path); draw(); }
    }));
  };
  draw();
  $('#vw-add').addEventListener('click', () => { rows.push({ id: newViewId(), name: '', file: '' }); draw(); $$('#vw-rows input[data-k="name"]').pop().focus(); });
  $('#vw-starter').addEventListener('click', async () => {
    const made = await addStarterView();
    if (!made) return;
    const empty = rows.find((r) => !r.file.trim());
    if (empty) { empty.file = made.file; empty.name = empty.name || 'Overview'; } else rows.push({ id: newViewId(), name: 'Starter', file: made.file });
    draw();
    toast('Starter view created in ' + made.folder, 'ok');
  });
}

// ---- views of a history entry
function historyResult(h) {
  const d = h.detail || {};
  return { status: h.status, status_code: h.status_code, reason: d.reason, duration_ms: h.duration_ms, error: h.error,
    body_name: h.body_name, ts: h.ts, request: d.request, response: d.response, history_id: h.id };
}
// The saved request a history entry belongs to. Older entries did not record the id: those
// are matched by name and method.
function historyRequestId(h) {
  if (h.request_id && state.requests.some((r) => r.id === h.request_id)) return h.request_id;
  const same = state.requests.filter((r) => r.name === h.request_name && r.method === h.method);
  return same.length === 1 ? same[0].id : null;
}
// Views as the request has them now (so a view added later also shows older calls); the ones
// the call was sent with when the request is gone.
async function historyViews(h) {
  const rid = historyRequestId(h);
  const req = rid ? await api('/requests/' + rid).catch(() => null) : null;
  const inherited = req && req.folder_id ? await api(`/folders/${req.folder_id}/inherited`).catch(() => null) : null;
  const views = mergeViews(viewsOf(req), inherited ? inherited.views : []);
  return views.length ? views : viewsOf(h.snapshot);
}
async function openHistoryViews(hid) {
  const h = await api('/history/' + hid).catch(() => null);
  if (!h) return;
  const views = await historyViews(h);
  if (!views.length) { toast('This request has no views yet. Add one in the View tab of its response.', 'error'); return; }
  if (!(h.detail || {}).response) { toast('This entry has no response to show', 'error'); return; }
  openViewsOutside(historyResult(h), views, h.request_name || 'response');
}

function initViews() {
  $('#resp-tabs button[data-tab="view"]').addEventListener('click', renderRespView);
}

// ============================================================== git sync
// The project's definitions as files in a Git repository. The server writes, commits and pushes
// a few seconds after every change; here is only the top bar button (state at a glance) and the
// Git tab of Settings (folder, remote, the automatic switches).
const gitSync = { info: null, timer: null, pulledAt: undefined };

function gitSummary(g) {
  if (!g || g.state === 'off') return 'Git sync is off.';
  if (g.state === 'syncing') return 'Syncing…';
  if (g.state === 'pending') return 'Changes are waiting to be committed…';
  if (g.state === 'error') return g.error;
  if (!g.synced_at) return 'Not synced yet.';
  const parts = [g.commit ? `Last commit ${g.commit.hash} “${g.commit.subject}”, ${agoText(g.commit.at)}` : 'Nothing committed yet'];
  if (g.dirty) parts.push('changes not committed (automatic commit is off)');
  if (!g.remote_name) parts.push('no remote, so commits stay on this computer');
  else if (g.unpushed) parts.push(`not pushed to ${g.remote_name} yet`);
  else if (g.commit) parts.push(`pushed to ${g.remote_name}`);
  return parts.join(' · ');
}

function renderGitButton() {
  const g = gitSync.info;
  const on = !!g && g.state !== 'off';
  const kind = !on ? '' : g.state === 'error' ? 'err' : g.state === 'ok' && !g.dirty && !g.unpushed ? 'ok' : 'busy';
  const dot = $('#git-dot');
  dot.hidden = !on;
  dot.className = 'git-dot ' + kind;
  $('#btn-git').dataset.state = on ? g.state : 'off';
  $('#btn-git').title = on ? `Git sync: ${gitSummary(g)}\n${g.dir}`
    : 'Git sync: keep this project\'s requests, environments and mocks in a Git repository, committed and pushed on every change. Click to set it up';
}

function renderGitStatus() {
  const el = $('#git-status');
  const g = gitSync.info;
  if (!el || !g) return;
  el.classList.toggle('err', g.state === 'error');
  const where = g.state !== 'off' && g.repo ? `Repository ${g.repo}${g.branch ? `, branch ${g.branch}` : ''}` : '';
  // a conflict is the one thing sync will not decide by itself
  const sides = g.conflict === 'adopt' ? ['Load the folder into this project', 'Overwrite the folder with this project']
    : g.conflict === 'merge' ? ['Take the remote version', 'Keep my version'] : null;
  const pick = sides ? `<div class="git-conflict"><button data-use="theirs">${sides[0]}</button><button data-use="mine">${sides[1]}</button></div>` : '';
  el.innerHTML = `<div>${esc(gitSummary(g))}</div>${pick}${where ? `<div class="muted">${esc(where)}</div>` : ''}`;
  $$('[data-use]', el).forEach((b) => b.addEventListener('click', async () => {
    $$('[data-use]', el).forEach((x) => { x.disabled = true; });
    try {
      gitSync.info = await api('/git/resolve', { method: 'POST', body: { use: b.dataset.use } });
      if (gitSync.info.state === 'error') toast('Git: ' + gitSync.info.error, 'error'); else toast('Synced', 'ok');
    } catch (e) { toast(e.message, 'error'); }
    gitChanged(true);
  }));
}

// New status from the server: redraw, and when a sync changed the project itself (a pull, or
// loading a folder) show the new content. The page reloads unless an edit is still unsaved.
function gitChanged(redraw = false) {
  const g = gitSync.info;
  renderGitButton();
  // the status block holds the conflict buttons: leave it alone while it says the same thing
  const key = [g.state, g.error, g.conflict, g.commit && g.commit.hash, g.dirty, g.unpushed, g.synced_at].join('|');
  if (redraw || key !== gitSync.key || ($('#git-status') && !$('#git-status').innerHTML)) renderGitStatus();
  gitSync.key = key;
  const first = gitSync.pulledAt === undefined;
  const moved = !first && g.pulled_at && g.pulled_at !== gitSync.pulledAt;
  gitSync.pulledAt = g.pulled_at || null;
  if (!moved) return;
  if (typeof autosave !== 'undefined' && (autosave.dirty || autosave.inFlight)) {
    toast('Git: changes were pulled into this project', 'ok', { label: 'Reload', onClick: () => location.reload() });
  } else {
    try { sessionStorage.setItem('pyhttp.gitPulled', '1'); } catch { /* ignore */ }
    location.reload();
  }
}

// Called after every call that changes something. With Git sync on, the server commits a few
// seconds after an edit; the status is polled every 4 seconds, which could miss that short
// "waiting to be committed" state altogether. Ask again right after the edit instead.
const GIT_QUIET = /^\/(git|send|ws|resolve|views|history|runs|console|oauth|cookies|open-path|update|import\/(detect|parse))/;
function gitNudge(path) {
  if (!gitSync.info || gitSync.info.state === 'off' || GIT_QUIET.test(path || '')) return;
  clearTimeout(gitSync.nudge);
  gitSync.nudge = setTimeout(loadGit, 300);
}

async function loadGit() {
  try { gitSync.info = await api('/git'); } catch { return; }
  gitChanged();
  // the server commits on its own a few seconds after an edit: keep the button honest while it is on
  const on = gitSync.info.state !== 'off';
  if (on && !gitSync.timer) gitSync.timer = setInterval(() => { if (!document.hidden) loadGit(); }, 4000);
  if (!on && gitSync.timer) { clearInterval(gitSync.timer); gitSync.timer = null; }
}

// The Git tab of Settings. Folder and remote take effect with the button (they create a
// repository and push); the switches save at once.
function renderGitPanel(box) {
  if (!box) return;
  const g = gitSync.info || { state: 'off', dir: '', remote: '', auto_commit: true, auto_push: true, auto_pull: true, secrets: false, git: true, suggested_dir: '' };
  const on = g.state !== 'off';
  const sw = (id, label, desc, checked) => `<label class="switch-row"><span class="txt"><b>${label}</b><span class="muted small">${desc}</span></span><span class="switch"><input type="checkbox" id="${id}" ${checked ? 'checked' : ''}><span></span></span></label>`;
  box.innerHTML = `
    <div class="set-section">
      <h4>Git sync</h4>
      <p class="muted small set-intro">Keeps this project's definitions in a Git repository: every request, folder, environment, mock and monitor is a JSON file,
        and a change is committed and pushed a few seconds after you make it. History, run outputs and cookies are not included. Each project has its own folder.</p>
      ${g.git ? '' : '<div class="ssl-warn">⚠ Git was not found on this computer. Install it from git-scm.com and restart pyhttp.</div>'}
      <div class="set-grid">
        <div class="set-field wide"><span class="set-label">Folder</span>
          <span class="git-dir-row"><input id="git-dir" class="mono" value="${esc(g.dir || '')}" placeholder="${esc(g.suggested_dir || '')}" spellcheck="false"><button id="git-browse" hidden>Browse…</button></span>
          <span class="set-hint muted small">A Git repository, or a folder inside one (to keep the requests next to the code they test). Any other folder becomes a new repository. Only the files pyhttp writes are committed.${on ? '' : ' Leave it empty to use the folder shown.'}</span></div>
        <div class="set-field wide"><span class="set-label">Remote URL</span>
          <input id="git-remote" class="mono" value="${esc(g.remote || '')}" placeholder="https://github.com/you/api-requests.git" spellcheck="false">
          <span class="set-hint muted small">Where to push; it becomes the <code>origin</code> remote. Leave it empty to use the remote the repository already has. Without a remote, commits stay on this computer.</span></div>
      </div>
      <div class="set-actions">
        <button id="git-apply" class="primary">${on ? 'Apply' : 'Turn on Git sync'}</button>
        ${on ? '<button id="git-sync" title="Write the files, commit and push now, whatever the switches below say">Sync now</button><button id="git-open" class="link small">Open folder</button><span class="grow"></span><button id="git-off" class="link small">Turn off</button>' : ''}
      </div>
      <div id="git-status" class="git-status small"></div>
    </div>
    <div class="set-section">
      <h4>When something changes</h4>
      <div class="switch-list">
        ${sw('git-auto-commit', 'Commit automatically', 'A few seconds after the last edit, with the names of what changed as the message. Off: the files are still written, and “Sync now” commits them.', g.auto_commit)}
        ${sw('git-auto-push', 'Push automatically', 'After every commit. A push that fails (offline, no access) is tried again with the next change. Off: “Sync now” pushes.', g.auto_push)}
        ${sw('git-auto-pull', 'Pull automatically', 'With every sync, when the app starts and on the timer below: changes from the remote are merged and read into this project. In a repository shared with code, that merges the remote branch into the one checked out. Off: “Sync now” pulls.', g.auto_pull)}
        ${sw('git-secrets', 'Include secret values', 'Off: the values of variables marked 🔒 are written empty, so they never reach the repository. Tokens and passwords typed straight into a request are always written.', g.secrets)}
      </div>
      <div class="set-grid">
        <div class="set-field"><span class="set-label">Look at the remote every</span>
          <select id="git-poll">${[[1, '1 minute'], [5, '5 minutes'], [15, '15 minutes'], [60, 'hour'], [0, 'never (only with a sync)']].map(([v, l]) => `<option value="${v}" ${Number(g.poll_minutes ?? 5) === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <span class="set-hint muted small">So a teammate's push arrives while you are only reading. Needs “Pull automatically” and a remote.</span></div>
      </div>
    </div>`;
  renderGitStatus();
  const done = (info, msg) => {
    gitSync.info = info;
    renderGitPanel(box);
    gitChanged(true);
    if (info.state === 'error') toast('Git: ' + info.error, 'error');
    else if (msg) toast(msg, 'ok');
    loadGit();   // starts or stops the polling
  };
  const busy = async (btn, label, work) => {
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
    try { await work(); } catch (e) { toast(e.message, 'error'); btn.disabled = false; btn.textContent = old; }
  };
  const pick = native() && native().pick_folder;
  $('#git-browse').hidden = !pick;
  $('#git-browse').addEventListener('click', async () => {
    const path = await native().pick_folder();
    if (path) $('#git-dir').value = path;
  });
  $('#git-apply').addEventListener('click', (e) => busy(e.currentTarget, 'Working…', async () => {
    const dir = $('#git-dir').value.trim() || (on ? '' : g.suggested_dir);
    if (!dir) throw new Error('Choose a folder (or use “Turn off” to stop syncing)');
    const info = await api('/git', { method: 'PUT', body: {
      dir, remote: $('#git-remote').value.trim(), auto_commit: $('#git-auto-commit').checked,
      auto_push: $('#git-auto-push').checked, auto_pull: $('#git-auto-pull').checked, secrets: $('#git-secrets').checked,
      poll_minutes: Number($('#git-poll').value) } });
    done(info, on ? 'Git settings saved' : 'Git sync is on');
  }));
  $('#git-poll').addEventListener('change', async (e) => {
    if (!on) return;
    try { done(await api('/git', { method: 'PUT', body: { poll_minutes: Number(e.target.value) } })); } catch (err) { toast(err.message, 'error'); }
  });
  [['git-auto-commit', 'auto_commit'], ['git-auto-push', 'auto_push'], ['git-auto-pull', 'auto_pull'], ['git-secrets', 'secrets']].forEach(([id, key]) => {
    $('#' + id).addEventListener('change', async (e) => {
      if (!on) return;   // not on yet: the switches are sent along with “Turn on”
      try { done(await api('/git', { method: 'PUT', body: { [key]: e.target.checked } })); } catch (err) { toast(err.message, 'error'); }
    });
  });
  if (!on) return;
  $('#git-sync').addEventListener('click', (e) => busy(e.currentTarget, 'Syncing…', async () => {
    done(await api('/git/sync', { method: 'POST' }), 'Synced');
  }));
  $('#git-open').addEventListener('click', () => api('/open-path', { method: 'POST', body: { path: g.dir } }).catch((e) => toast(e.message, 'error')));
  $('#git-off').addEventListener('click', async () => {
    try { done(await api('/git', { method: 'PUT', body: { dir: '' } }), 'Git sync is off. The folder and its history are kept'); } catch (e) { toast(e.message, 'error'); }
  });
}

function initGit() {
  $('#btn-git').addEventListener('click', () => openSettings('git'));
  try {
    if (sessionStorage.getItem('pyhttp.gitPulled')) {
      sessionStorage.removeItem('pyhttp.gitPulled');
      setTimeout(() => toast('Git: changes were pulled into this project', 'ok'), 400);
    }
  } catch { /* ignore */ }
  loadGit();
}

document.addEventListener('DOMContentLoaded', initFeatures);


// ============================================================== websocket
// A request whose address starts with ws:// or wss:// is a WebSocket: Send becomes Connect, and
// the response area becomes a console (a live log of messages, and a box to send one). The
// connection itself is held by the server (see wsclient.py); the log arrives through a long poll.
const wsUi = { byKey: {}, shown: null, rendered: 0, filter: '', mode: false };
const isWsUrl = (u) => /^\s*wss?:\/\//i.test(u || '');
const isWsRequest = () => !!state.current && isWsUrl($('#f-url').value);
function wsSession() { return wsUi.byKey[responseKey()] || null; }
const wsLive = (s) => !!s && (s.state === 'open' || s.state === 'connecting');

function wsApply(s, snap) {
  Object.assign(s, { state: snap.state, url: snap.url, last: snap.last, subprotocol: snap.subprotocol, close_code: snap.close_code, close_reason: snap.close_reason });
  s.events.push(...snap.events);
  if (s.events.length > 2000) { s.events.splice(0, s.events.length - 2000); if (wsUi.shown === s) wsUi.rendered = -1; }   // -1: draw from scratch
}
function wsNote(s, text) {
  s.events.push({ n: 0, ts: Date.now() / 1000, dir: 'error', kind: 'close', text, size: 0 });
}
async function wsPoll(key, s) {
  while (wsUi.byKey[key] === s && wsLive(s)) {
    try { wsApply(s, await api(`/ws/${s.id}?after=${s.last}&wait=20`)); }
    catch (e) { s.state = 'error'; wsNote(s, e.message); }
    if (responseKey() === key) { renderSendButton(); renderWsPanel(); }
  }
}
async function wsConnect() {
  await saveNow();
  if (!state.current) return;
  const data = readEditor();
  data.folder_id = state.current.folder_id || null;
  const key = responseKey();
  const s = wsUi.byKey[key] = { id: null, state: 'connecting', url: data.url, events: [], last: 0 };
  renderSendButton();
  renderWsPanel();
  try {
    const snap = await api('/ws/connect', { method: 'POST', body: { ...data, id: state.current.id } });
    s.id = snap.id;
    wsApply(s, snap);
    wsPoll(key, s);
  } catch (e) { s.state = 'error'; wsNote(s, e.message); }
  if (responseKey() === key) { renderSendButton(); renderWsPanel(); }
}
async function wsDisconnect() {
  const s = wsSession();
  if (!s || !s.id || !wsLive(s)) return;
  try { wsApply(s, await api(`/ws/${s.id}/close`, { method: 'POST' })); } catch (e) { toast(e.message, 'error'); }
  renderSendButton();
  renderWsPanel();
}
// connectOnly: the keyboard shortcut connects (or moves to the message box) but never hangs up
function wsToggle(connectOnly = false) {
  if (wsLive(wsSession())) { if (connectOnly) $('#ws-text').focus(); else wsDisconnect(); } else wsConnect();
}
async function wsSendMessage() {
  const s = wsSession();
  const text = $('#ws-text').value;
  if (!s || s.state !== 'open') { toast('Not connected: press Connect first', 'error'); return; }
  if (!text) return;
  try { await api(`/ws/${s.id}/send`, { method: 'POST', body: { text, folder_id: (state.current && state.current.folder_id) || null } }); }
  catch (e) { toast(e.message, 'error'); }
}
function wsRowHtml(e) {
  const arrow = e.dir === 'in' ? '↓' : e.dir === 'out' ? '↑' : e.dir === 'error' ? '!' : '•';
  const kind = e.kind === 'text' || e.kind === 'open' || e.kind === 'close' ? '' : `<span class="ws-kind">${esc(e.kind)}</span>`;
  const size = e.dir === 'in' || e.dir === 'out' ? `<span class="ws-size muted">${esc(fmtBytes(e.size || 0))}</span>` : '';
  return `<div class="ws-row ws-${e.dir}" data-n="${e.n}" title="${e.dir === 'in' ? 'Received' : e.dir === 'out' ? 'Sent' : ''} ${esc(fmtClock(e.ts))}${e.kind === 'text' ? '. Click to pretty-print JSON' : ''}">
    <span class="ws-time muted">${esc(fmtClock(e.ts))}</span><span class="ws-arrow">${arrow}</span>${kind}<span class="ws-msg">${esc(e.text || '')}${e.truncated ? ' …' : ''}</span>${size}</div>`;
}
// Draws the console for the request on screen; returns false when it is not a WebSocket request.
function renderWsPanel() {
  const box = $('#response');
  const on = isWsRequest();
  wsUi.mode = on;
  box.classList.toggle('ws-mode', on);
  $('#ws-panel').hidden = !on;
  if (!on) { wsUi.shown = null; return false; }
  box.hidden = false;
  const s = wsSession();
  const dot = $('#ws-dot');
  dot.className = 'ws-dot ' + (!s ? '' : s.state === 'open' ? 'ok' : s.state === 'connecting' ? 'busy' : s.state === 'error' ? 'err' : '');
  $('#ws-status').textContent = !s ? 'Not connected. Press Connect to open the WebSocket.'
    : s.state === 'connecting' ? `Connecting to ${s.url}…`
    : s.state === 'open' ? `Connected to ${s.url}${s.subprotocol ? ` (${s.subprotocol})` : ''}`
    : s.state === 'error' ? 'Could not connect, or the connection failed'
    : `Closed${s.close_code ? ` (${s.close_code}${s.close_reason ? ': ' + s.close_reason : ''})` : ''}`;
  $('#ws-send').disabled = !s || s.state !== 'open';
  $('#ws-ping').disabled = !s || s.state !== 'open';
  const text = $('#ws-text');
  if (!text.value && !text.dataset.touched) {   // start from the body of the request, if it has one
    const b = typeof activeBody === 'function' ? activeBody() : null;
    if (b && $('#f-body-type').value !== 'none' && (b.body || '').trim()) text.value = b.body;
  }
  const log = $('#ws-log');
  const events = s ? s.events : [];
  const q = wsUi.filter.toLowerCase();
  const fresh = wsUi.shown !== s || wsUi.rendered < 0 || wsUi.rendered > events.length || wsUi.q !== q;
  const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  const rows = (fresh ? events : events.slice(wsUi.rendered)).filter((e) => !q || (e.text || '').toLowerCase().includes(q));
  if (fresh) log.innerHTML = rows.length || s ? '' : '';
  if (rows.length) log.insertAdjacentHTML('beforeend', rows.map(wsRowHtml).join(''));
  if (!log.children.length) log.innerHTML = `<div class="ws-empty muted small">${!s ? 'Messages appear here once connected. Headers, auth, query parameters and {{variables}} of this request are used for the connection.' : q ? 'No message matches the filter.' : 'No messages yet.'}</div>`;
  else { const empty = $('.ws-empty', log); if (empty && log.children.length > 1) empty.remove(); }
  wsUi.shown = s;
  wsUi.rendered = events.length;
  wsUi.q = q;
  if (fresh || atEnd) log.scrollTop = log.scrollHeight;
  return true;
}
function initWs() {
  $('#ws-send').addEventListener('click', wsSendMessage);
  $('#ws-ping').addEventListener('click', async () => {
    const s = wsSession();
    if (s && s.state === 'open') { try { await api(`/ws/${s.id}/send`, { method: 'POST', body: { ping: true } }); } catch (e) { toast(e.message, 'error'); } }
  });
  $('#ws-clear').addEventListener('click', () => { const s = wsSession(); if (s) s.events = []; wsUi.rendered = -1; renderWsPanel(); });
  $('#ws-filter').addEventListener('input', (e) => { wsUi.filter = e.target.value; renderWsPanel(); });
  const text = $('#ws-text');
  text.addEventListener('input', () => { text.dataset.touched = '1'; });
  text.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); wsSendMessage(); }
  });
  // a text message that is JSON opens pretty-printed on click, and folds back on the next one
  $('#ws-log').addEventListener('click', (e) => {
    const row = e.target.closest('.ws-row');
    const s = wsSession();
    if (!row || !s || window.getSelection().toString()) return;
    const ev = s.events.find((x) => String(x.n) === row.dataset.n && x.n);
    if (!ev || ev.kind !== 'text') return;
    const msg = $('.ws-msg', row);
    if (row.classList.toggle('open')) {
      try { msg.textContent = JSON.stringify(JSON.parse(ev.text), null, 2); } catch { msg.textContent = ev.text; }
    } else msg.textContent = ev.text + (ev.truncated ? ' …' : '');
  });
  // the address decides what this request is: redraw when it turns into (or stops being) a WebSocket one
  $('#f-url').addEventListener('input', () => {
    if (isWsRequest() !== wsUi.mode) { renderSendButton(); renderResponseArea(); renderReqTabs(); }
  });
  // connections are held by the server: hang up when the window goes away
  window.addEventListener('pagehide', () => {
    Object.values(wsUi.byKey).forEach((s) => { if (s.id && wsLive(s)) navigator.sendBeacon(`/api/ws/${s.id}/close`); });
  });
}
document.addEventListener('DOMContentLoaded', initWs);
