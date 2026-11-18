'use strict';
// ================================================================== flows
// Saved requests wired into a small program (flows.py runs it). The sidebar has two tabs:
// Requests and Flows. With Flows chosen, the list on the left is the flows and the middle of
// the window is the canvas of the open one: a block leaves through one of its ports, and the
// wire on that port leads to the next block. Drag a block by its title, drag from a port to
// another block to wire them, click a wire to remove it. Changes save as they are made.
// mailState: 'ready' (there is a test inbox), 'sign_in' (there is one after signing in to the
// pyhttp cloud: the desktop app), 'offline' (signed in, the cloud does not answer)
const flowUi = { list: [], tabs: [], listed: false, restore: null, mail: '', mailState: 'sign_in', mailProblem: '', emailStart: false, limits: {}, flow: null, selected: null, saveTimer: null, saving: null, run: null, marks: {},
                 loaded: false, poll: null, search: '',
                 checked: new Set(),      // flows ticked in the list, for Run selected
                 batch: null,             // the Run selected going (or just ended): its snapshot
                 batchTimer: null,
                 stopOnFail: false,
                 closed: flowClosedFolders(),   // folders folded shut in the list
                 ask: null };             // the view block a run waits on, as it is on screen

function flowClosedFolders() {
  try { return new Set(JSON.parse(localStorage.getItem('pyhttp.flowFoldersClosed') || '[]')); } catch { return new Set(); }
}
function saveFlowClosed() {
  try { localStorage.setItem('pyhttp.flowFoldersClosed', JSON.stringify([...flowUi.closed])); } catch { /* private window */ }
}

const FLOW_BLOCKS = {
  start: { title: 'Start', ports: ['out'], hint: 'Where a run begins: when you press Run, or by itself when an email arrives.' },
  request: { title: 'Send request', ports: ['ok', 'fail'], hint: 'Sends a saved request. “fail” is an error, a 4xx/5xx answer or a failed test.' },
  email: { title: 'Wait for email', ports: ['received', 'timeout'], hint: 'Waits for a message to your test inbox, then offers it as {{email.code}}, {{email.link}}, {{email.subject}}, {{email.from}}, {{email.text}}.' },
  set: { title: 'Set variable', ports: ['out'], hint: 'Puts a value in a variable the next blocks and requests can use as {{name}}.' },
  if: { title: 'If', ports: ['true', 'false'], hint: 'Compares two values and takes one of two ways.' },
  delay: { title: 'Wait', ports: ['out'], hint: 'Waits before the next block.' },
  log: { title: 'Log', ports: ['out'], hint: 'Writes a line to the run log.' },
  view: { title: 'HTML / JS view', short: 'HTML/JS', ports: ['submitted', 'timeout'], hint: 'Shows a list (say the items of the last response) and waits for you to pick from it: as a list to tick, or with your own HTML and JavaScript. What you pick is handed to the next block and kept as {{selected}}.' },
  script: { title: 'JavaScript', short: 'Script', ports: ['ok', 'fail'], hint: 'Runs JavaScript on what came before (input: the items picked, or the last response) and hands on what it returns. set(name, value) makes {{name}} for the next requests.' },
  stop: { title: 'Stop', ports: [], hint: 'Ends the flow as passed or failed.' },
};
const FLOW_OPS = [['equals', 'equals'], ['not_equals', 'does not equal'], ['contains', 'contains'], ['not_contains', 'does not contain'],
                  ['matches', 'matches pattern'], ['greater', 'is greater than'], ['less', 'is less than'], ['empty', 'is empty'], ['not_empty', 'is not empty']];
// what a message is told apart by: [key in the block's settings, label, placeholder]
const FLOW_MATCH = [['from', 'From', 'e.g. no-reply@shop.com'], ['subject', 'Subject', 'e.g. Verify'], ['contains', 'Text', 'e.g. your code'], ['to', 'To', 'e.g. +signup']];
const flowNode = (id) => flowUi.flow.graph.nodes.find((n) => n.id === id) || null;
const flowRequest = (id) => (state.requests || []).find((r) => r.id === id) || null;
const flowStart = (flow) => (flow.graph.nodes || []).find((n) => n.type === 'start') || null;
const flowByEmail = (flow) => { const s = flowStart(flow); return !!(s && s.config && s.config.trigger === 'email'); };

async function loadFlows() {
  const data = await api('/flows');
  flowUi.list = data.flows;
  flowUi.listed = true;
  const before = flowUi.mailState + flowUi.mail;
  flowUi.mail = data.mail || '';
  flowUi.mailState = data.mail_state || (data.mail ? 'ready' : 'sign_in');
  flowUi.mailProblem = data.mail_problem || '';
  flowUi.emailStart = !!data.email_start;
  flowUi.limits = data.limits || {};
  flowUi.loaded = true;
  // a Run selected going on the server (started before a reload, or in another window): follow it
  if (data.batch && (!flowUi.batch || flowUi.batch.id !== data.batch.id)) followFlowBatch(data.batch);
  // signed in or out since the last look: the open block's settings speak of the inbox
  if (before !== flowUi.mailState + flowUi.mail && flowUi.flow && $('#flow-config')) renderFlowConfig();
}

// what to say, and offer, where a block needs the inbox and there is none
function flowNoMail(what) {
  if (flowUi.mailState === 'offline') {
    return `<div class="flow-nomail"><strong>The pyhttp cloud is not reachable.</strong> ${esc(flowUi.mailProblem || '')} Your test inbox lives there; ${what} once it answers again.</div>`;
  }
  return `<div class="flow-nomail"><strong>Sign in to receive email.</strong> Your account has a test inbox (an address like <code>name@inbox.pyhttp.com</code>). Signed in, ${what}.
    <div><button class="primary small flow-signin">Sign in to pyhttp cloud</button></div></div>`;
}

function bindFlowSignIn(scope) {
  $$('.flow-signin', scope).forEach((b) => b.addEventListener('click', () => {
    const cloudButton = $('#btn-cloud');
    if (cloudButton) cloudButton.click();
  }));
}

// ----------------------------------------------------- the sidebar's Flows tab
function setFlowMode(on) {
  $('.layout').classList.toggle('flow-mode', on);
  clearInterval(flowUi.poll);
  flowUi.poll = null;
  if (!on) return;
  refreshFlows();
  // an email may start a flow while nobody presses anything: keep the list's results current
  flowUi.poll = setInterval(() => { if (!document.hidden) refreshFlows(true); }, 15000);
  if (flowUi.flow) requestAnimationFrame(drawFlowWires);
}

async function refreshFlows(quiet = false) {
  try { await loadFlows(); } catch (err) { if (!quiet) toast(err.message, 'error'); return; }
  renderFlowList();
  renderFlowInbox();
  renderFlowTabs();
  if (flowUi.restore) {   // the flow that was open before a refresh (pages.js restorePlace)
    const f = flowUi.list.find((x) => x.id === flowUi.restore);
    flowUi.restore = null;
    if (f) { openFlow(f); return; }
  }
  if (!flowUi.flow) renderFlowView();
}

function flowBadge(f) {
  if (flowUi.flow && flowUi.flow.id === f.id && flowUi.run) return '<span class="flow-dot running" title="Running"></span>';
  if (f.last_status === 'passed') return '<span class="flow-dot ok" title="Last run passed"></span>';
  if (f.last_status === 'failed') return '<span class="flow-dot bad" title="Last run failed"></span>';
  return '<span class="flow-dot" title="Not run yet"></span>';
}

// The list is a tree: a flow's folder is a name ("Auth/Signup" is Signup inside Auth). Each
// flow and each folder has a box to tick; the bar above the list runs the ticked flows one
// after the other. Drag a flow onto a folder to move it there (with others ticked, all of them).
function flowTree(shown) {
  const root = { name: '', path: '', folders: new Map(), flows: [] };
  shown.forEach((f) => {
    let at = root;
    String(f.folder || '').split('/').filter(Boolean).forEach((part) => {
      const path = at.path ? `${at.path}/${part}` : part;
      if (!at.folders.has(part)) at.folders.set(part, { name: part, path, folders: new Map(), flows: [] });
      at = at.folders.get(part);
    });
    at.flows.push(f);
  });
  return root;
}
const flowsIn = (node) => [...node.flows, ...[...node.folders.values()].flatMap(flowsIn)];
const flowFolders = () => [...new Set(flowUi.list.map((f) => f.folder || '').filter(Boolean)
  .flatMap((p) => p.split('/').map((_, i, all) => all.slice(0, i + 1).join('/'))))].sort((a, b) => a.localeCompare(b));

function flowItemHtml(f, depth) {
  const blocks = (f.graph.nodes || []).length;
  const when = f.last_run_at ? ` · ran ${typeof agoText === 'function' ? agoText(f.last_run_at) : fmtDate(f.last_run_at)}` : '';
  return `<li class="flow-item${flowUi.flow && flowUi.flow.id === f.id ? ' active' : ''}" data-id="${f.id}" tabindex="0" draggable="true" style="--depth:${depth}">
    <input type="checkbox" class="flow-tick" ${flowUi.checked.has(f.id) ? 'checked' : ''} aria-label="Tick ${esc(f.name)} to run it with others" title="Tick to run it with others">
    ${flowBadge(f)}
    <span class="flow-item-text"><span class="flow-item-name">${esc(f.name)}</span>
      <span class="flow-item-meta muted">${flowByEmail(f) ? '<span class="flow-auto" title="Starts by itself when a matching email arrives">' + icon('mail') + ' on email</span> · ' : ''}${blocks} block${blocks === 1 ? '' : 's'}${when}</span></span>
    <button class="link flow-item-act" data-act="move" title="Move to a folder" aria-label="Move to a folder">${icon('folder')}</button>
    <button class="link flow-item-act" data-act="copy" title="Duplicate this flow" aria-label="Duplicate">${icon('copy')}</button>
    <button class="link flow-item-act danger" data-act="del" title="Delete this flow" aria-label="Delete">${icon('x')}</button>
  </li>`;
}

function flowFolderHtml(node, depth, searching) {
  const all = flowsIn(node);
  const ticked = all.filter((f) => flowUi.checked.has(f.id)).length;
  const open = searching || !flowUi.closed.has(node.path);
  const inner = open ? flowBranchHtml(node, depth + 1, searching) : '';
  return `<li class="flow-folder${open ? ' open' : ''}" data-path="${esc(node.path)}" style="--depth:${depth}">
      <div class="flow-folder-row" tabindex="0" title="${esc(node.path)}">
        <input type="checkbox" class="flow-tick" ${ticked && ticked === all.length ? 'checked' : ''} data-some="${ticked && ticked < all.length ? 1 : ''}" aria-label="Tick every flow in ${esc(node.name)}" title="Tick every flow in this folder">
        <span class="flow-caret">${icon(open ? 'chevron-down' : 'chevron-right')}</span>
        <span class="flow-folder-name">${icon('folder')} ${esc(node.name)}</span>
        <span class="flow-folder-n muted">${all.length}</span>
        <button class="link flow-item-act" data-act="run" title="Run every flow in this folder, one after the other" aria-label="Run folder">${icon('play')}</button>
        <button class="link flow-item-act" data-act="new" title="New flow in this folder" aria-label="New flow here">${icon('plus')}</button>
        <button class="link flow-item-act" data-act="rename" title="Rename this folder" aria-label="Rename folder">${icon('pencil')}</button>
        <button class="link flow-item-act danger" data-act="unfolder" title="Remove the folder (its flows stay, one level up)" aria-label="Remove folder">${icon('x')}</button>
      </div>
      ${open ? `<ul class="flow-sub">${inner}</ul>` : ''}
    </li>`;
}

function flowBranchHtml(node, depth, searching) {
  const folders = [...node.folders.values()].sort((a, b) => a.name.localeCompare(b.name));
  return folders.map((f) => flowFolderHtml(f, depth, searching)).join('') + node.flows.map((f) => flowItemHtml(f, depth)).join('');
}

function renderFlowSelBar() {
  let bar = $('#flow-selbar');
  const ul = $('#flow-list');
  if (!ul) return;
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'flow-selbar';
    bar.className = 'flow-selbar';
    ul.before(bar);
  }
  // ticks of flows that were deleted meanwhile do not count
  flowUi.checked = new Set([...flowUi.checked].filter((id) => flowUi.list.some((f) => f.id === id)));
  const n = flowUi.checked.size;
  const all = flowUi.list.length && n === flowUi.list.length;
  bar.classList.toggle('some', n > 0);
  bar.innerHTML = `<label class="flow-selall" title="Tick every flow"><input type="checkbox" id="flow-tick-all" ${all ? 'checked' : ''} ${flowUi.list.length ? '' : 'disabled'}>
      <span>${n ? `${n} ticked` : 'Tick flows to run several'}</span></label>
    <span class="grow"></span>
    <button class="small" id="flow-folder-new" title="New folder (the ticked flows go into it)">${icon('folder-plus')}</button>
    ${n ? `<button class="small" id="flow-sel-move" title="Move the ticked flows to a folder">${icon('folder')} Move</button>
    <button class="small accent" id="flow-sel-run" title="Run the ticked flows one after the other">${icon('play')} Run ${n}</button>` : ''}`;
  const tickAll = $('#flow-tick-all');
  tickAll.indeterminate = n > 0 && !all;
  tickAll.addEventListener('change', () => {
    flowUi.checked = tickAll.checked ? new Set(flowUi.list.map((f) => f.id)) : new Set();
    renderFlowList();
  });
  $('#flow-folder-new').addEventListener('click', newFlowFolder);
  if (n) {
    $('#flow-sel-move').addEventListener('click', () => moveFlows([...flowUi.checked]));
    $('#flow-sel-run').addEventListener('click', () => runFlowBatch(flowUi.list.filter((f) => flowUi.checked.has(f.id)).map((f) => f.id)));
  }
}

function renderFlowList() {
  const ul = $('#flow-list');
  if (!ul) return;
  renderFlowSelBar();
  const q = flowUi.search.trim().toLowerCase();
  const shown = flowUi.list.filter((f) => !q || f.name.toLowerCase().includes(q) || String(f.folder || '').toLowerCase().includes(q));
  if (!shown.length) {
    ul.innerHTML = `<li class="flow-none muted small">${flowUi.list.length ? 'No flow matches the search.' : 'No flows yet. Press “+ New”.'}</li>`;
    return;
  }
  ul.innerHTML = flowBranchHtml(flowTree(shown), 0, !!q);
  $$('.flow-folder-row .flow-tick', ul).forEach((box) => { box.indeterminate = !!box.dataset.some; });

  $$('.flow-item', ul).forEach((li) => {
    const f = flowUi.list.find((x) => x.id === li.dataset.id);
    const open = () => openFlow(f);
    li.addEventListener('click', (e) => { if (!e.target.closest('.flow-item-act, .flow-tick')) open(); });
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target === li) open(); });
    $('.flow-tick', li).addEventListener('change', (e) => {
      if (e.target.checked) flowUi.checked.add(f.id); else flowUi.checked.delete(f.id);
      renderFlowList();
    });
    li.addEventListener('dragstart', (e) => {
      const ids = flowUi.checked.has(f.id) ? [...flowUi.checked] : [f.id];
      e.dataTransfer.setData('text/x-pyhttp-flows', JSON.stringify(ids));
      e.dataTransfer.effectAllowed = 'move';
    });
    $('[data-act=move]', li).addEventListener('click', () => moveFlows([f.id]));
    $('[data-act=copy]', li).addEventListener('click', async () => {
      try { await api('/flows', { method: 'POST', body: { name: f.name + ' copy', graph: f.graph, folder: f.folder || '' } }); } catch (err) { toast(err.message, 'error'); }
      refreshFlows();
    });
    $('[data-act=del]', li).addEventListener('click', async () => {
      if (!await confirmDialog('Delete flow?', `${strong(f.name)} will be removed. The requests it sends stay.`, { okLabel: 'Delete', danger: true })) return;
      try { await api('/flows/' + f.id, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
      flowUi.checked.delete(f.id);
      flowUi.tabs = flowUi.tabs.filter((id) => id !== f.id);
      if (flowUi.flow && flowUi.flow.id === f.id) { clearTimeout(flowUi.saveTimer); flowUi.flow = null; flowUi.run = null; renderFlowView(); if (typeof savePlace === 'function') savePlace(); }
      refreshFlows();
    });
  });

  $$('.flow-folder', ul).forEach((li) => {
    const path = li.dataset.path;
    const row = $('.flow-folder-row', li);
    const inside = () => flowUi.list.filter((f) => f.folder === path || String(f.folder || '').startsWith(path + '/'));
    const toggle = () => {
      if (flowUi.closed.has(path)) flowUi.closed.delete(path); else flowUi.closed.add(path);
      saveFlowClosed();
      renderFlowList();
    };
    row.addEventListener('click', (e) => { if (!e.target.closest('.flow-item-act, .flow-tick')) toggle(); });
    row.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === row) { e.preventDefault(); toggle(); } });
    $('.flow-tick', row).addEventListener('change', (e) => {
      inside().forEach((f) => { if (e.target.checked) flowUi.checked.add(f.id); else flowUi.checked.delete(f.id); });
      renderFlowList();
    });
    $('[data-act=run]', row).addEventListener('click', () => runFlowBatch(flowsIn(flowTree(flowUi.list)).filter((f) => inside().includes(f)).map((f) => f.id)));
    $('[data-act=new]', row).addEventListener('click', () => newFlow(path));
    $('[data-act=rename]', row).addEventListener('click', () => renameFlowFolder(path));
    $('[data-act=unfolder]', row).addEventListener('click', async () => {
      const n = inside().length;
      if (!await confirmDialog('Remove folder?', `The folder ${strong(path)} goes. Its ${n} flow${n === 1 ? '' : 's'} stay${n === 1 ? 's' : ''}, moved to the top of the list.`, { okLabel: 'Remove folder' })) return;
      try { await api('/flows/folders/rename', { method: 'POST', body: { name: path, to: '' } }); } catch (err) { toast(err.message, 'error'); }
      refreshFlows();
    });
    // a flow dropped here moves into this folder
    row.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('text/x-pyhttp-flows')) { e.preventDefault(); row.classList.add('drop'); } });
    row.addEventListener('dragleave', () => row.classList.remove('drop'));
    row.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      row.classList.remove('drop');
      try { moveFlowsTo(JSON.parse(e.dataTransfer.getData('text/x-pyhttp-flows')), path); } catch { /* not ours */ }
    });
  });
}

// dropped on the list itself, outside any folder: back to the top level
function bindFlowListDrop() {
  const ul = $('#flow-list');
  if (!ul || ul.dataset.drop) return;
  ul.dataset.drop = '1';
  ul.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('text/x-pyhttp-flows')) e.preventDefault(); });
  ul.addEventListener('drop', (e) => {
    if (e.target.closest('.flow-folder-row')) return;
    e.preventDefault();
    try {
      const ids = JSON.parse(e.dataTransfer.getData('text/x-pyhttp-flows'));
      if (ids.some((id) => (flowUi.list.find((f) => f.id === id) || {}).folder)) moveFlowsTo(ids, '');
    } catch { /* not ours */ }
  });
}

async function moveFlowsTo(ids, folder) {
  try { await api('/flows/move', { method: 'POST', body: { ids, folder } }); } catch (err) { toast(err.message, 'error'); return; }
  if (folder) flowUi.closed.delete(folder);
  flowUi.list.forEach((f) => { if (ids.includes(f.id)) f.folder = folder; });
  if (flowUi.flow && ids.includes(flowUi.flow.id)) flowUi.flow.folder = folder;
  renderFlowList();
  toast(`${ids.length === 1 ? 'Moved' : `${ids.length} flows moved`} to ${folder ? folder : 'the top level'}`, 'ok');
}

async function moveFlows(ids) {
  const here = flowUi.list.find((f) => f.id === ids[0]);
  const known = flowFolders();
  const folder = await promptDialog(ids.length === 1 ? 'Move flow to a folder' : `Move ${ids.length} flows to a folder`, {
    label: 'Folder', value: (here && here.folder) || '', placeholder: 'e.g. Auth or Auth/Signup', okLabel: 'Move',
    message: known.length ? `Folders: ${known.map(esc).join(', ')}. A new name makes a new folder; empty moves to the top level; “/” nests one folder in another.` : 'A new name makes a new folder; “/” nests one folder in another.' });
  if (folder === null || folder === undefined) return;
  moveFlowsTo(ids, String(folder).split('/').map((p) => p.trim()).filter(Boolean).join('/'));
}

async function newFlowFolder() {
  const ticked = [...flowUi.checked];
  const name = await promptDialog('New folder', { label: 'Folder name', placeholder: 'e.g. Checkout', okLabel: 'Create',
    message: ticked.length ? `The ${ticked.length} ticked flow${ticked.length === 1 ? '' : 's'} go into it.` : 'A first flow is made in it; drag others onto the folder to move them in.' });
  const folder = String(name || '').split('/').map((p) => p.trim()).filter(Boolean).join('/');
  if (!folder) return;
  if (ticked.length) { await moveFlowsTo(ticked, folder); flowUi.checked.clear(); renderFlowList(); } else newFlow(folder);
}

async function renameFlowFolder(path) {
  const name = await promptDialog('Rename folder', { label: 'Folder', value: path, okLabel: 'Rename', message: '“/” nests it in another folder.' });
  const to = String(name || '').split('/').map((p) => p.trim()).filter(Boolean).join('/');
  if (!to || to === path) return;
  try { await api('/flows/folders/rename', { method: 'POST', body: { name: path, to } }); } catch (err) { toast(err.message, 'error'); return; }
  refreshFlows();
}

// ------------------------------------------------------------ Run selected
// Ticked flows run one after the other on the server; this panel under the list follows them.
async function runFlowBatch(ids) {
  if (!ids.length) { toast('Tick the flows to run first', 'error'); return; }
  await flushFlowSave();
  let batch;
  try { batch = await api('/flow-batches', { method: 'POST', body: { flow_ids: ids, stop_on_fail: flowUi.stopOnFail } }); } catch (err) { toast(err.message, 'error'); return; }
  followFlowBatch(batch);
}

function followFlowBatch(batch) {
  flowUi.batch = batch;
  renderFlowBatch();
  clearTimeout(flowUi.batchTimer);
  const tick = async () => {
    if (!flowUi.batch || flowUi.batch.id !== batch.id) return;
    let now;
    try { now = await api(`/flow-batches/${batch.id}`); } catch { flowUi.batch = null; renderFlowBatch(); return; }
    if (!flowUi.batch || flowUi.batch.id !== batch.id) return;
    const moved = JSON.stringify(now.items) !== JSON.stringify(flowUi.batch.items);
    flowUi.batch = now;
    if (moved) { renderFlowBatch(); refreshFlows(true); }
    if (now.state === 'running') flowUi.batchTimer = setTimeout(tick, 1000);
    else {
      renderFlowBatch();
      if (flowUi.flow) loadFlowHistory(flowUi.flow);
      if (typeof loadHistory === 'function') loadHistory();
    }
  };
  flowUi.batchTimer = setTimeout(tick, 600);
}

function renderFlowBatch() {
  let box = $('#flow-batch');
  const inbox = $('#flow-inbox');
  if (!box && inbox) {
    box = document.createElement('div');
    box.id = 'flow-batch';
    box.className = 'flow-batch';
    inbox.before(box);
  }
  if (!box) return;
  const b = flowUi.batch;
  box.hidden = !b;
  if (!b) { box.innerHTML = ''; return; }
  const c = b.counts || {};
  const going = b.state === 'running';
  const done = (c.passed || 0) + (c.failed || 0) + (c.cancelled || 0) + (c.skipped || 0);
  const cls = (st) => (st === 'passed' ? 'ok' : st === 'failed' ? 'failed' : st === 'running' ? 'running' : 'cancelled');
  box.innerHTML = `<div class="flow-batch-head">
      <strong>${going ? `Running ${b.items.length} flows` : `${b.items.length} flows ran`}</strong>
      <span class="badge ${cls(b.state)}">${going ? `${done}/${b.items.length}` : esc(b.state)}</span>
      <span class="grow"></span>
      ${going ? `<button class="small" id="flow-batch-stop">${icon('stop')} Stop</button>` : '<button class="link small" id="flow-batch-close" title="Hide">' + icon('x') + '</button>'}
    </div>
    <div class="flow-batch-sum muted small">${c.passed || 0} passed · ${c.failed || 0} failed${c.skipped ? ` · ${c.skipped} not run` : ''}${c.cancelled ? ` · ${c.cancelled} stopped` : ''}</div>
    <div class="flow-batch-list">${b.items.map((it) => `<button class="flow-batch-row" data-id="${esc(it.flow_id)}" title="${esc(it.message || 'Open this flow')}">
        <span class="badge ${it.waiting ? 'running' : cls(it.state)}">${it.waiting ? 'needs you' : esc(it.state)}</span>
        <span class="fbr-name">${esc(it.name)}</span>
        ${it.message ? `<span class="fbr-msg muted">${esc(it.message)}</span>` : ''}</button>`).join('')}</div>
    <label class="flow-check small muted"><input type="checkbox" id="flow-batch-stoponfail" ${flowUi.stopOnFail ? 'checked' : ''}> Next time, stop at the first flow that does not pass</label>`;
  const stop = $('#flow-batch-stop');
  if (stop) stop.addEventListener('click', async () => { try { await api(`/flow-batches/${b.id}/cancel`, { method: 'POST' }); } catch (err) { toast(err.message, 'error'); } });
  const close = $('#flow-batch-close');
  if (close) close.addEventListener('click', () => { flowUi.batch = null; renderFlowBatch(); });
  $('#flow-batch-stoponfail').addEventListener('change', (e) => { flowUi.stopOnFail = e.target.checked; });
  $$('.flow-batch-row', box).forEach((row) => row.addEventListener('click', () => {
    const f = flowUi.list.find((x) => x.id === row.dataset.id);
    if (f) openFlow(f);
  }));
}

function renderFlowInbox() {
  const box = $('#flow-inbox');
  if (!box) return;
  box.hidden = false;
  if (!flowUi.mail) {
    box.innerHTML = '<div class="muted small">Your test inbox</div>' + flowNoMail('flows here can wait for mail sent to it');
    bindFlowSignIn(box);
    return;
  }
  box.innerHTML = `<div class="muted small">Your test inbox</div>
    <div class="flow-inbox-addr">
      <span class="flow-inbox-mark">${icon('mail')}</span>
      <code title="${esc(flowUi.mail)}">${esc(flowUi.mail)}</code>
      <button class="icon-btn flow-inbox-btn" id="flow-addr-copy" title="Copy address" aria-label="Copy address">${icon('copy')}</button>
      <button class="icon-btn flow-inbox-btn" id="flow-inbox-open" title="Open inbox" aria-label="Open inbox">${icon('inbox')}</button>
    </div>`;
  const copyButton = $('#flow-addr-copy');
  copyButton.addEventListener('click', async () => {
    if (!await copyText(flowUi.mail)) return;
    toast('Address copied', 'ok');
    copyButton.innerHTML = icon('check');
    copyButton.classList.add('done');
    setTimeout(() => { copyButton.innerHTML = icon('copy'); copyButton.classList.remove('done'); }, 1400);
  });
  $('#flow-inbox-open').addEventListener('click', openFlowInbox);
}

async function newFlow(folder = '') {
  if (typeof folder !== 'string') folder = '';   // called from a click
  try {
    const flow = await api('/flows', { method: 'POST', body: { name: `Flow ${flowUi.list.length + 1}`, folder } });
    if (folder) { flowUi.closed.delete(folder); saveFlowClosed(); }
    await refreshFlows();
    openFlow(flowUi.list.find((f) => f.id === flow.id) || flow);
    const name = $('#flow-name');
    if (name) { name.focus(); name.select(); }
  } catch (err) { toast(err.message, 'error'); }
}

// ------------------------------------------------------------ the open flows' tabs
// Like the request tabs: a flow that is opened gets a tab, and stays open until its tab is
// closed. The tabs, their order and the one in front are part of the last session
// (features.js), so they come back after a refresh of the web page or a restart of the app.
const FLOW_TAB_MARK = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="7" height="5" rx="1.2"/><rect x="14" y="15" width="7" height="5" rx="1.2"/><path d="M10 6.5h3.5a2 2 0 0 1 2 2V15"/></g></svg>';
let flowTabsSig = '';

function flowTabsAdd(id) {
  if (id && !flowUi.tabs.includes(id)) flowUi.tabs.push(id);
  renderFlowTabs();
}

function renderFlowTabs() {
  const box = $('#flow-tabs');
  if (!box) return;
  const cur = flowUi.flow ? flowUi.flow.id : null;
  // the list is read only once the Flows section is opened: until then every saved tab is kept
  if (flowUi.listed) flowUi.tabs = flowUi.tabs.filter((id) => id === cur || flowUi.list.some((f) => f.id === id));
  const items = flowUi.tabs.map((id) => {
    const f = id === cur ? flowUi.flow : flowUi.list.find((x) => x.id === id);
    return f ? { id, name: f.name || 'Untitled flow', active: id === cur } : null;
  }).filter(Boolean);
  box.hidden = !items.length;
  const sig = JSON.stringify(items);
  if (sig !== flowTabsSig) {
    flowTabsSig = sig;
    box.innerHTML = items.map((it) => `<div class="req-tab flow-tab ${it.active ? 'active' : ''}" data-id="${esc(it.id)}" title="${esc(it.name)}">
      <span class="flow-tab-mark">${FLOW_TAB_MARK}</span><span class="rt-name">${esc(it.name)}</span>
      <button class="rt-close" title="Close (Ctrl+W)" aria-label="Close ${esc(it.name)}">${icon('x')}</button></div>`).join('')
      + '<button class="rt-add" title="New flow">+</button>';
  }
  if (typeof saveSession === 'function') saveSession();
}

async function closeFlowTab(id) {
  const i = flowUi.tabs.indexOf(id);
  if (i === -1) return;
  flowUi.tabs.splice(i, 1);
  if (flowUi.flow && flowUi.flow.id === id) {
    const nextId = flowUi.tabs[Math.min(i, flowUi.tabs.length - 1)];
    const next = nextId && flowUi.list.find((f) => f.id === nextId);
    if (next) { await openFlow(next); return; }
    await flushFlowSave();
    flowUi.flow = null;
    flowUi.selected = null;
    flowUi.run = null;
    renderFlowList();
    renderFlowView();
    if (typeof savePlace === 'function') savePlace();
  }
  renderFlowTabs();
}

function bindFlowTabs() {
  const box = $('#flow-tabs');
  if (!box) return;
  box.addEventListener('click', (e) => {
    if (e.target.closest('.rt-add')) { newFlow(''); return; }
    const el = e.target.closest('.flow-tab');
    if (!el) return;
    if (e.target.closest('.rt-close')) { closeFlowTab(el.dataset.id); return; }
    const f = flowUi.list.find((x) => x.id === el.dataset.id);
    if (f) openFlow(f);
  });
  box.addEventListener('auxclick', (e) => { const el = e.target.closest('.flow-tab'); if (el && e.button === 1) closeFlowTab(el.dataset.id); });
}

// The flows of the last session (features.js restoreSession): their tabs at once, the one in
// front as soon as the list is read (now, when the Flows section is already showing).
function restoreFlowSession(ids, front) {
  const saved = (Array.isArray(ids) ? ids : []).filter((id) => typeof id === 'string');
  flowUi.tabs = [...saved, ...flowUi.tabs.filter((id) => !saved.includes(id))];
  if (!flowUi.flow && typeof front === 'string' && front && !flowUi.restore) flowUi.restore = front;
  renderFlowTabs();
  if (flowUi.restore && $('.layout').classList.contains('flow-mode')) refreshFlows(true);
}

// -------------------------------------------------------------- the inbox
// Only where the app has a mail system (pyhttp on the web): /api/mail is answered there.
async function openFlowInbox() {
  let data;
  try { data = await api('/mail'); } catch (err) { toast(err.message, 'error'); return; }
  const rows = data.messages.map((m) => `<tr data-id="${esc(m.id)}"><td>${fmtDate(m.received_at)}</td><td>${esc(m.from)}</td><td>${esc(m.subject || '(no subject)')}</td>
    <td><button class="link small" data-act="read">Read</button><button class="link small danger" data-act="del">Delete</button></td></tr>`).join('');
  openModal('Test inbox', `
    <p class="muted small">Mail to <code>${esc(data.address)}</code> (and <code>${esc(data.address.replace('@', '+anything@'))}</code>). The newest ${data.kept} messages are kept; text only, attachments are not stored.</p>
    ${rows ? `<table class="htable"><tr><th>Received</th><th>From</th><th>Subject</th><th></th></tr>${rows}</table>` : '<p class="muted">Nothing received yet.</p>'}
    <pre id="flow-mail-text" class="flow-mail-text" hidden></pre>
  `, [{ label: 'Refresh', onClick: openFlowInbox }, { label: 'Done', cls: 'primary', onClick: closeModal }]);
  $$('#modal-body tr[data-id]').forEach((tr) => {
    $('[data-act=read]', tr).addEventListener('click', async () => {
      try {
        const m = await api('/mail/' + tr.dataset.id);
        const box = $('#flow-mail-text');
        box.hidden = false;
        box.textContent = `From: ${m.from}\nTo: ${m.to}\nSubject: ${m.subject}\n\n${m.text}`;
      } catch (err) { toast(err.message, 'error'); }
    });
    $('[data-act=del]', tr).addEventListener('click', async () => {
      try { await api('/mail/' + tr.dataset.id, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
      openFlowInbox();
    });
  });
}

// ------------------------------------------------- the middle of the window
function renderFlowView() {
  const view = $('#flow-view');
  if (!view) return;
  const flow = flowUi.flow;
  if (!flow) {
    view.innerHTML = `<div class="empty flow-empty">
      <svg class="empty-mark" viewBox="0 0 24 24" width="40" height="40" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" d="M4 5h6v5H4zM14 14h6v5h-6zM10 7.5h3a2 2 0 0 1 2 2V14"/></svg>
      <p>${flowUi.list.length ? 'Select a flow on the left, or create a new one.' : 'A flow wires your saved requests into a small program.'}</p>
      <p class="muted small">Send a request, wait for an email, pick the code out of it, decide, send the next one.<br>
        For example: sign up → wait for the verification email → send its code back.</p>
      <p><button id="flow-empty-new" class="primary">+ New flow</button></p>
    </div>`;
    $('#flow-empty-new').addEventListener('click', newFlow);
    return;
  }
  const add = Object.entries(FLOW_BLOCKS).filter(([type]) => type !== 'start')
    .map(([type, b]) => `<button class="small flow-add" data-type="${type}" title="${esc(b.hint)}">+ ${esc(b.short || b.title)}</button>`).join('');
  view.innerHTML = `
    <div class="flow-bar">
      <input id="flow-name" value="${esc(flow.name)}" aria-label="Flow name" title="Flow name (click to rename)" maxlength="120" spellcheck="false">
      <span class="flow-adds" role="group" aria-label="Add a block">${add}</span>
      <span class="grow"></span>
      <span id="flow-saved" class="save-state"></span>
      <button id="flow-stop" hidden>${icon('stop')} Stop</button>
      <button id="flow-run" class="accent" title="Run this flow now">${icon('play')} Run</button>
    </div>
    <div class="flow-main">
      <div id="flow-canvas" class="flow-canvas" data-ask=""><div id="flow-area" class="flow-area"><svg id="flow-wires" class="flow-wires"></svg></div></div>
      <div class="flow-side">
        <div id="flow-config" class="flow-config"></div>
        <div class="flow-log-head">
          <div class="tabs small" id="flow-log-tabs">
            <button data-tab="log" class="active" title="What each block of one run said">Run log</button>
            <button data-tab="history" title="Every finished run of this flow, newest first">History<span class="tab-n" id="flow-history-n"></span></button>
          </div>
          <span id="flow-state"></span><span class="grow"></span><span id="flow-run-by" class="muted small"></span></div>
        <div id="flow-log-panels" class="flow-log-panels">
          <div id="flow-log" class="flow-log tab-panel" data-panel="log"><p class="muted small">Press Run. Each block reports here as it finishes.</p></div>
          <div id="flow-history" class="flow-log tab-panel" data-panel="history" hidden></div>
        </div>
      </div>
    </div>`;
  if (flowUi.ask) { clearInterval(flowUi.ask.clock); flowUi.ask = null; }   // its panel went with the old view
  $('#flow-name').addEventListener('input', (e) => {
    flow.name = e.target.value;
    const label = $(`.flow-item[data-id="${CSS.escape(flow.id)}"] .flow-item-name`);
    if (label) label.textContent = flow.name;
    renderFlowTabs();
    scheduleFlowSave();
  });
  $$('.flow-add').forEach((b) => b.addEventListener('click', () => addFlowBlock(b.dataset.type)));
  $('#flow-run').addEventListener('click', runFlow);
  $('#flow-stop').addEventListener('click', async () => {
    if (flowUi.run) { try { await api(`/flow-runs/${flowUi.run}/cancel`, { method: 'POST' }); } catch { /* it ended by itself */ } }
  });
  // empty canvas: a click clears the selection, a drag moves the view (blocks, ports and wires handle their own)
  $('#flow-area').addEventListener('pointerdown', (e) => {
    if (e.target.id !== 'flow-area' && e.target.id !== 'flow-wires') return;
    selectFlowBlock(null);
    panFlowCanvas(e);
  });
  // the middle button pans from anywhere, over blocks too
  $('#flow-canvas').addEventListener('pointerdown', (e) => { if (e.button === 1) panFlowCanvas(e); });
  bindTabs($('#flow-log-tabs'), $('#flow-log-panels'));
  $('#flow-log-tabs button[data-tab="history"]').addEventListener('click', () => loadFlowHistory(flow));
  renderFlow();
  renderFlowConfig();
  loadFlowHistory(flow);
}

// ---------------------------------------------------------------- history
// Every finished run of the flow, kept with its log: pick one to read it again.
async function loadFlowHistory(flow) {
  let data;
  try { data = await api(`/flows/${flow.id}/runs`); } catch { return; }
  const box = $('#flow-history');
  if (!box || flowUi.flow !== flow) return;
  $('#flow-history-n').textContent = data.runs.length || '';
  if (!data.runs.length) {
    box.innerHTML = '<p class="muted small">No runs yet. Every run of this flow is kept here with its log: when it ran, what started it and how it ended.</p>';
    return;
  }
  const passed = data.runs.filter((r) => r.state === 'passed').length;
  box.innerHTML = `<div class="flow-history-head muted small"><span>${data.runs.length} run${data.runs.length === 1 ? '' : 's'} kept · ${passed} passed, ${data.runs.length - passed} not</span>
      <span class="grow"></span><button class="link small danger" id="flow-history-clear">Clear</button></div>`
    + data.runs.map((r) => {
      const cls = r.state === 'passed' ? 'ok' : r.state === 'failed' ? 'failed' : 'cancelled';
      const took = r.ended_at ? fmtMs((r.ended_at - r.started_at) * 1000) : '';
      return `<button class="flow-run-row" data-id="${esc(r.id)}" title="Show this run's log">
        <span class="badge ${cls}">${esc(r.state)}</span>
        <span class="frr-when">${fmtDate(r.started_at)}</span>
        <span class="frr-meta muted">${r.blocks} block${r.blocks === 1 ? '' : 's'} · ${took}${r.started_by ? ` · ${esc(r.started_by)}` : ''}</span>
        ${r.message ? `<span class="frr-msg muted">${esc(r.message)}</span>` : ''}</button>`;
    }).join('');
  $('#flow-history-clear').addEventListener('click', async () => {
    if (!await confirmDialog('Clear this flow\'s history?', 'Every kept run and its log is removed. The flow itself stays.', { okLabel: 'Clear', danger: true })) return;
    try { await api(`/flows/${flow.id}/runs`, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
    loadFlowHistory(flow);
  });
  $$('.flow-run-row', box).forEach((row) => row.addEventListener('click', async () => {
    if (flowUi.run) { toast('A run is going: its log is on screen. Look at older runs when it ends.', 'error'); return; }
    let run;
    try { run = await api(`/flows/${flow.id}/runs/${row.dataset.id}`); } catch (err) { toast(err.message, 'error'); return; }
    if (flowUi.flow !== flow) return;
    flowUi.marks = {};
    $$('.flow-node').forEach((el) => { el.className = el.className.replace(/ r-\w+/g, ''); });
    $('#flow-log').innerHTML = '';
    showFlowEvents(run.events);
    showFlowState(run);
    showTab($('#flow-log-tabs'), 'log');
  }));
}

async function openFlow(flow) {
  if (flowUi.flow && flowUi.flow.id === flow.id) return;
  await flushFlowSave();
  flowUi.flow = flow;
  flowUi.selected = null;
  flowUi.run = null;
  flowUi.marks = {};
  flowTabsAdd(flow.id);
  renderFlowList();
  renderFlowView();
  showLastFlowRun(flow);
  if (typeof savePlace === 'function') savePlace();
}

// the newest run still kept by the app: one started by an email is seen this way
async function showLastFlowRun(flow) {
  let data;
  try { data = await api(`/flows/${flow.id}/last-run`); } catch { return; }
  if (!data.run || flowUi.flow !== flow || flowUi.run) return;
  if (data.run.state === 'running') { followFlowRun(flow, data.run); return; }
  $('#flow-log').innerHTML = '';
  showFlowEvents(data.run.events);
  showFlowState(data.run);
}

function scheduleFlowSave() {
  clearTimeout(flowUi.saveTimer);
  const note = $('#flow-saved');
  if (note) note.textContent = 'Saving…';
  flowUi.saveTimer = setTimeout(flushFlowSave, 500);
}

async function flushFlowSave() {
  const pending = flowUi.saveTimer !== null;
  clearTimeout(flowUi.saveTimer);
  flowUi.saveTimer = null;
  const flow = flowUi.flow;
  if (!flow || !pending) { await flowUi.saving; return; }
  const note = $('#flow-saved');
  // one save at a time, in order: a slow one must not land after a newer one
  flowUi.saving = (flowUi.saving || Promise.resolve()).then(async () => {
    try {
      await api('/flows/' + flow.id, { method: 'PUT', body: { name: flow.name.trim() || 'Flow', graph: flow.graph } });
      if (note && !flowUi.saveTimer) note.textContent = 'Saved';
    } catch (err) {
      if (note) note.textContent = 'Not saved';
      toast(err.message, 'error');
    }
  });
  await flowUi.saving;
}

function addFlowBlock(type) {
  const g = flowUi.flow.graph;
  const canvas = $('#flow-canvas');
  const id = type + '-' + Math.random().toString(36).slice(2, 8);
  // on its own it lands under the lowest block, where nothing is in the way
  const lowest = g.nodes.reduce((y, node) => Math.max(y, node.y), 0);
  g.nodes.push({ id, type, x: Math.round(canvas.scrollLeft + 300), y: lowest + 150, config: flowDefaults(type) });
  // wire it to the selected block's first free port, so adding blocks in order builds a chain
  const from = flowUi.selected && flowNode(flowUi.selected);
  if (from) {
    const free = FLOW_BLOCKS[from.type].ports.find((p) => !g.edges.some((e) => e.from === from.id && e.port === p));
    if (free) {
      g.edges.push({ from: from.id, port: free, to: id });
      const added = flowNode(id);
      added.x = from.x + 260;
      added.y = from.y + FLOW_BLOCKS[from.type].ports.indexOf(free) * 110;
    }
  }
  renderFlow();
  selectFlowBlock(id);
  const el = $(`.flow-node[data-id="${CSS.escape(id)}"]`);
  if (el) el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
  scheduleFlowSave();
}

function flowDefaults(type) {
  if (type === 'email') return { mode: 'all', from: '', subject: '', contains: '', to: '', timeout_s: 60 };
  if (type === 'set') return { name: '', value: '', pattern: '', save: false };
  if (type === 'if') return { left: '', op: 'equals', right: '' };
  if (type === 'delay') return { seconds: 2 };
  if (type === 'stop') return { result: 'pass', message: '' };
  if (type === 'request') return { request_id: '' };
  if (type === 'log') return { message: '' };
  if (type === 'view') return { data: '', title: 'Pick items', multiple: true, min: 1, max: 0, field: '', name: 'selected', timeout_s: 300, html: '', js: '', sample: '' };
  if (type === 'script') return { input: '', name: 'result', code: FLOW_SCRIPT_EXAMPLE };
  return {};
}

// "from has “x” and subject has “y”", as the block shows it
function flowMatchText(c) {
  const parts = FLOW_MATCH.filter(([key]) => String(c[key] || '').trim()).map(([key, label]) => `${label.toLowerCase()} has “${String(c[key]).trim()}”`);
  return parts.length ? parts.join(c.mode === 'any' ? ' OR ' : ' AND ') : 'any message';
}

function flowSummary(node) {
  const c = node.config;
  if (node.type === 'request') {
    const r = flowRequest(c.request_id);
    return r ? `<span class="method m-${esc(r.method)}">${esc(r.method)}</span> ${esc(r.name)}` : '<span class="err-text">pick a request</span>';
  }
  if (node.type === 'email') return `${esc(flowMatchText(c))} · ${esc(c.timeout_s || 60)} s`;
  if (node.type === 'set') return c.name ? `${esc(c.name)} = <span class="mono">${esc(String(c.value).slice(0, 40))}</span>` : '<span class="err-text">name it</span>';
  if (node.type === 'if') return `<span class="mono">${esc(String(c.left).slice(0, 24))}</span> ${esc((FLOW_OPS.find((o) => o[0] === c.op) || FLOW_OPS[0])[1])} <span class="mono">${esc(String(c.right).slice(0, 24))}</span>`;
  if (node.type === 'delay') return `${esc(c.seconds)} s`;
  if (node.type === 'log') return esc(String(c.message).slice(0, 60)) || '<span class="muted">nothing to write yet</span>';
  if (node.type === 'stop') return c.result === 'fail' ? 'as failed' : 'as passed';
  if (node.type === 'view') {
    const how = c.html || c.js ? 'own HTML/JS' : c.multiple === false ? 'pick one' : `tick ${c.min ? c.min : ''}${c.max ? `–${c.max}` : c.min ? '+' : 'any'}`;
    return `${esc(c.title || 'Pick')} · <span class="muted">${esc(how)}</span> → <span class="mono">{{${esc(c.name || 'selected')}}}</span>`;
  }
  if (node.type === 'script') {
    const first = String(c.code || '').split('\n').map((l) => l.trim()).find((l) => l && !l.startsWith('//')) || '';
    return first ? `<span class="mono">${esc(first.slice(0, 48))}</span>` : '<span class="err-text">write the script</span>';
  }
  return c.trigger === 'email' ? `${icon('mail')} when an email arrives: ${esc(flowMatchText(c))}` : 'when you press Run';
}

function renderFlow() {
  const area = $('#flow-area');
  if (!area) return;
  $$('.flow-node', area).forEach((el) => el.remove());
  flowUi.flow.graph.nodes.forEach((node) => {
    const b = FLOW_BLOCKS[node.type];
    const el = document.createElement('div');
    el.className = `flow-node t-${node.type}` + (flowUi.selected === node.id ? ' sel' : '') + (flowUi.marks[node.id] ? ` r-${flowUi.marks[node.id]}` : '');
    el.dataset.id = node.id;
    el.style.left = node.x + 'px';
    el.style.top = node.y + 'px';
    el.innerHTML = `${node.type === 'start' ? '' : '<span class="fn-in" title="Wires end here"></span>'}
      <div class="fn-head"><span class="fn-title">${esc(b.title)}</span>${node.type === 'start' ? '' : '<button class="fn-del link" title="Remove this block" aria-label="Remove block">' + icon('x') + '</button>'}</div>
      <div class="fn-body" title="">${flowSummary(node)}</div>
      <div class="fn-ports">${b.ports.map((p) => `<span class="fn-port p-${p}" data-port="${p}" title="Drag to the block that runs next">${b.ports.length > 1 ? esc(p) : ''}<i></i></span>`).join('')}</div>`;
    area.appendChild(el);
    $('.fn-head', el).addEventListener('pointerdown', (e) => { if (!e.target.closest('.fn-del')) dragFlowBlock(e, node, el); });
    el.addEventListener('pointerdown', (e) => { if (!e.target.closest('.fn-port, .fn-del')) selectFlowBlock(node.id); });
    const del = $('.fn-del', el);
    if (del) del.addEventListener('click', () => removeFlowBlock(node.id));
    $$('.fn-port', el).forEach((port) => port.addEventListener('pointerdown', (e) => dragFlowWire(e, node, port)));
  });
  fitFlowArea();
  drawFlowWires();
}

function selectFlowBlock(id) {
  if (flowUi.selected === id) return;
  flowUi.selected = id;
  $$('.flow-node').forEach((el) => el.classList.toggle('sel', el.dataset.id === id));
  renderFlowConfig();
}

function removeFlowBlock(id) {
  const g = flowUi.flow.graph;
  g.nodes = g.nodes.filter((n) => n.id !== id);
  g.edges = g.edges.filter((e) => e.from !== id && e.to !== id);
  if (flowUi.selected === id) flowUi.selected = null;
  renderFlow();
  renderFlowConfig();
  scheduleFlowSave();
}

// where a port's dot, or a block's entry, is on the canvas area
function flowPoint(el) {
  const a = $('#flow-area').getBoundingClientRect();
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2 - a.left, y: r.top + r.height / 2 - a.top };
}
const flowCurve = (a, b) => {
  const bend = Math.max(40, Math.abs(b.x - a.x) / 2);
  return `M${a.x},${a.y} C${a.x + bend},${a.y} ${b.x - bend},${b.y} ${b.x},${b.y}`;
};

function drawFlowWires(extra = '') {
  const svg = $('#flow-wires');
  if (!svg || !flowUi.flow) return;
  const parts = flowUi.flow.graph.edges.map((e, i) => {
    const from = $(`.flow-node[data-id="${CSS.escape(e.from)}"] .fn-port[data-port="${e.port}"] i`);
    const to = $(`.flow-node[data-id="${CSS.escape(e.to)}"] .fn-in`);
    if (!from || !to) return '';
    const d = flowCurve(flowPoint(from), flowPoint(to));
    return `<path class="fw p-${e.port}" d="${d}"></path><path class="fw-hit" data-i="${i}" d="${d}"><title>Click to remove this wire</title></path>`;
  });
  svg.innerHTML = parts.join('') + (typeof extra === 'string' ? extra : '');
  $$('.fw-hit', svg).forEach((p) => p.addEventListener('click', () => {
    flowUi.flow.graph.edges.splice(Number(p.dataset.i), 1);
    drawFlowWires();
    scheduleFlowSave();
  }));
}

// Drag the empty canvas to move the view: what is under the pointer follows it, like a map.
function panFlowCanvas(e) {
  if (e.button !== 0 && e.button !== 1) return;
  const canvas = $('#flow-canvas');
  if (!canvas) return;
  e.preventDefault();   // no text selection, no middle-button autoscroll
  const sx = e.clientX, sy = e.clientY, left = canvas.scrollLeft, top = canvas.scrollTop;
  let moved = false;
  const move = (ev) => {
    if (!moved && Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) < 3) return;   // a click, not a drag
    moved = true;
    canvas.classList.add('panning');
    canvas.scrollLeft = left - (ev.clientX - sx);
    canvas.scrollTop = top - (ev.clientY - sy);
  };
  const up = () => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    window.removeEventListener('pointercancel', up);
    canvas.classList.remove('panning');
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
  window.addEventListener('pointercancel', up);
}

// the drawing area grows with the blocks, so there is always room to scroll past the last one
function fitFlowArea() {
  const area = $('#flow-area');
  if (!area || !flowUi.flow) return;
  const nodes = flowUi.flow.graph.nodes || [];
  const w = Math.max(3200, ...nodes.map((n) => n.x + 900));
  const h = Math.max(2000, ...nodes.map((n) => n.y + 700));
  area.style.width = w + 'px';
  area.style.height = h + 'px';
}

function dragFlowBlock(e, node, el) {
  if (e.button !== 0) return;
  e.preventDefault();
  const sx = e.clientX, sy = e.clientY, ox = node.x, oy = node.y;
  let moved = false;
  const move = (ev) => {
    moved = true;
    node.x = Math.max(0, Math.round(ox + ev.clientX - sx));
    node.y = Math.max(0, Math.round(oy + ev.clientY - sy));
    el.style.left = node.x + 'px';
    el.style.top = node.y + 'px';
    drawFlowWires();
  };
  const up = () => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    if (moved) { fitFlowArea(); scheduleFlowSave(); }
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}

function dragFlowWire(e, node, port) {
  if (e.button !== 0) return;
  e.preventDefault();
  e.stopPropagation();
  const start = flowPoint($('i', port));
  const area = $('#flow-area');
  const move = (ev) => {
    const a = area.getBoundingClientRect();
    drawFlowWires(`<path class="fw fw-new" d="${flowCurve(start, { x: ev.clientX - a.left, y: ev.clientY - a.top })}"></path>`);
  };
  const up = (ev) => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    const over = document.elementFromPoint(ev.clientX, ev.clientY);
    const target = over && over.closest('.flow-node');
    const g = flowUi.flow.graph;
    if (target && target.dataset.id !== node.id && flowNode(target.dataset.id).type !== 'start') {
      g.edges = g.edges.filter((x) => !(x.from === node.id && x.port === port.dataset.port));   // one wire per port
      g.edges.push({ from: node.id, port: port.dataset.port, to: target.dataset.id });
      scheduleFlowSave();
    }
    drawFlowWires();
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}

// ----------------------------------------------------- one block's settings
// Which messages a block means: all of / any of up to four "contains" texts, and a way to
// try them on what is in the inbox now.
function flowMatchFields(c) {
  const rows = FLOW_MATCH.map(([key, label, placeholder]) =>
    `<label class="flow-match-row"><span class="fm-label">${label}</span><span class="fm-op muted">contains</span>
      <input data-k="${key}" class="mono" value="${esc(c[key] ?? '')}" placeholder="${esc(placeholder)}" autocomplete="off"></label>`).join('');
  return `<div class="flow-match">
      <div class="flow-match-mode">Take a message that fits
        <select data-k="mode" aria-label="All or any of the conditions">
          <option value="all" ${c.mode !== 'any' ? 'selected' : ''}>ALL of these (and)</option>
          <option value="any" ${c.mode === 'any' ? 'selected' : ''}>ANY of these (or)</option>
        </select></div>
      ${rows}
      <p class="muted small">Leave a line empty to ignore it. Capital letters do not matter. All lines empty: any message.</p>
      <p id="flow-match-reads" class="flow-match-reads small"></p>
      ${flowUi.mail ? '<button class="link small" id="flow-match-check">Try it on my inbox</button><div id="flow-match-result" class="flow-match-result small"></div>' : ''}
    </div>`;
}

function showFlowMatchReads(c) {
  const el = $('#flow-match-reads');
  if (el) el.innerHTML = `Reads as: <strong>${esc(flowMatchText(c))}</strong>`;
}

async function checkFlowMatch(c) {
  const box = $('#flow-match-result');
  if (!box) return;
  box.innerHTML = '<span class="spin"></span>';
  let data;
  try {
    data = await api('/flows/mail-check', { method: 'POST', body: { match: Object.fromEntries([...FLOW_MATCH.map(([key]) => [key, c[key] || '']), ['mode', c.mode || 'all']]) } });
  } catch (err) { box.textContent = err.message; return; }
  if (!data.messages.length) { box.innerHTML = '<span class="muted">The inbox is empty: send a test message to it, then try again.</span>'; return; }
  const hits = data.messages.filter((m) => m.fits).length;
  box.innerHTML = `<div><strong>${hits} of ${data.messages.length}</strong> message${data.messages.length === 1 ? '' : 's'} in the inbox ${hits === 1 ? 'fits' : 'fit'}.</div>`
    + data.messages.slice(0, 6).map((m) => `<div class="fm-msg ${m.fits ? 'yes' : 'no'}"><span class="fm-mark">${icon(m.fits ? 'check' : 'x')}</span>
        <span><span class="fm-subj">${esc(m.subject || '(no subject)')}</span> <span class="muted">${esc(m.from || '')}</span>${m.fits ? '' : `<br><span class="fm-why">${esc(m.why)}</span>`}</span></div>`).join('');
}

function renderFlowConfig() {
  const box = $('#flow-config');
  if (!box) return;
  const node = flowUi.selected && flowNode(flowUi.selected);
  if (!node) {
    box.innerHTML = `<p class="muted small"><strong>Click a block</strong> to set it up. Add blocks from the bar above: with a block selected, the new one is wired to it.</p>
      <p class="muted small">Values can use <code>{{variable}}</code>, <code>{{prev.status}}</code>, <code>{{prev.json.id}}</code> from the last request${flowUi.mail ? ', and <code>{{email.code}}</code>, <code>{{email.link}}</code> from the last email' : ''}.</p>`;
    return;
  }
  const c = node.config;
  const text = (key, label, placeholder = '', mono = true) =>
    `<label class="flow-f">${label}<input data-k="${key}" class="${mono ? 'mono' : ''}" value="${esc(c[key] ?? '')}" placeholder="${esc(placeholder)}" autocomplete="off"></label>`;
  const noMail = flowNoMail('this block waits for mail sent to it');
  let fields = '';
  if (node.type === 'start') {
    // starting by itself needs something that is always running: pyhttp on the web
    const here = flowUi.emailStart;
    fields = `<label class="flow-f">This flow runs<select data-k="trigger" data-rerender="1">
        <option value="manual" ${c.trigger !== 'email' ? 'selected' : ''}>when I press Run</option>
        <option value="email" ${c.trigger === 'email' ? 'selected' : ''} ${here || c.trigger === 'email' ? '' : 'disabled'}>by itself, when an email arrives${here ? '' : ' (on app.pyhttp.com)'}</option></select></label>`;
    if (c.trigger === 'email') {
      fields += (here
        ? (flowUi.mail ? `<p class="small">Every message to <code>${esc(flowUi.mail)}</code> that fits starts a run, with the message as <code>{{email.…}}</code>. Run still starts it by hand.</p>` : noMail)
        : '<div class="flow-nomail"><strong>Starts by itself on app.pyhttp.com</strong>, where this flow is synced to: something has to be running when the mail arrives. In this app, press Run.</div>')
        + flowMatchFields(c);
    } else if (!here) {
      fields += '<p class="muted small">A flow can also start by itself when an email arrives: in pyhttp on the web (app.pyhttp.com), which is always running.</p>';
    }
  } else if (node.type === 'request') {
    const options = (state.requests || []).map((r) => `<option value="${r.id}" ${r.id === c.request_id ? 'selected' : ''}>${esc(r.method)} ${esc(r.name)}</option>`).join('');
    fields = `<label class="flow-f">Request<select data-k="request_id"><option value="">Pick a saved request…</option>${options}</select></label>
      <p class="muted small">Its tests, extractions and scripts run as usual. With several bodies, the first one switched on is sent.</p>`;
  } else if (node.type === 'email') {
    fields = (flowUi.mail
      ? `<p class="small">Waits for a message to <code>${esc(flowUi.mail)}</code> that arrives after the flow started (or after the last email block).</p>`
      : noMail)
      + flowMatchFields(c)
      + `<label class="flow-f">Wait up to (seconds)<input data-k="timeout_s" type="number" min="1" max="${Math.round(flowUi.limits.wait_seconds || 600)}" value="${esc(c.timeout_s ?? 60)}"></label>`;
  } else if (node.type === 'set') {
    fields = text('name', 'Variable name', 'e.g. otp') + text('value', 'Value', 'e.g. {{email.text}} or {{prev.json.id}}')
      + text('pattern', 'Keep only what matches (optional pattern)', 'e.g. code is (\\d+)')
      + `<label class="flow-check"><input type="checkbox" data-k="save" ${c.save ? 'checked' : ''}> Also save it as a variable of the project</label>`;
  } else if (node.type === 'if') {
    fields = text('left', 'Value', 'e.g. {{prev.status}}')
      + `<label class="flow-f">Is<select data-k="op">${FLOW_OPS.map(([v, l]) => `<option value="${v}" ${v === c.op ? 'selected' : ''}>${l}</option>`).join('')}</select></label>`
      + text('right', 'Compared with', 'e.g. 200');
  } else if (node.type === 'delay') {
    fields = `<label class="flow-f">Seconds<input data-k="seconds" type="number" min="0" step="0.5" value="${esc(c.seconds ?? 1)}"></label>`;
  } else if (node.type === 'log') {
    fields = text('message', 'Line to write', 'e.g. Signed up as {{email.to}}', false);
  } else if (node.type === 'view') {
    const most = Math.round(flowUi.limits.input_seconds || 1800);
    fields = text('data', 'Data to show', 'empty: what the block before handed on, else the list in the last response')
      + '<p class="muted small">E.g. <code>{{prev.json.items}}</code> or <code>{{prev.json.data.users}}</code>. A list of objects shows as a table to tick.</p>'
      + text('title', 'Title', 'Pick items', false)
      + `<div class="flow-row2"><label class="flow-f">Pick<select data-k="multiple" data-bool="1" data-rerender="1">
          <option value="true" ${c.multiple !== false ? 'selected' : ''}>several</option><option value="false" ${c.multiple === false ? 'selected' : ''}>one</option></select></label>`
      + (c.multiple !== false ? `<label class="flow-f">At least<input data-k="min" type="number" min="0" value="${esc(c.min ?? 0)}"></label>
          <label class="flow-f">At most<input data-k="max" type="number" min="0" value="${esc(c.max ?? 0)}" title="0: no limit"></label>` : '') + '</div>'
      + text('field', 'Hand on only this field (optional)', 'e.g. id: [3, 7, 9] instead of whole items')
      + `<div class="flow-row2">${text('name', 'Keep as variable', 'selected')}<label class="flow-f">Wait up to (s)<input data-k="timeout_s" type="number" min="5" max="${most}" value="${esc(c.timeout_s ?? 300)}"></label></div>`
      + `<details class="flow-more" ${c.html || c.js ? 'open' : ''}><summary>Own look: HTML + JavaScript</summary>
          <p class="muted small">Leave both empty for the built-in list. Your page gets <code>pyhttp.data</code> (the list) and hands on what is picked with <code>pyhttp.submit(value)</code>. <code>pyhttp.picker(element)</code> puts the built-in list anywhere in your page. It runs sandboxed: no access to the app or your login.</p>
          <label class="flow-f">HTML<textarea data-k="html" class="flow-code" rows="6" spellcheck="false" placeholder="&lt;div id=&quot;cards&quot;&gt;&lt;/div&gt;">${esc(c.html || '')}</textarea></label>
          <label class="flow-f">JavaScript<textarea data-k="js" class="flow-code" rows="8" spellcheck="false" placeholder="pyhttp.data.forEach(...)">${esc(c.js || '')}</textarea></label>
          <div class="flow-btns"><button class="small" id="flow-view-example">Insert an example (cards)</button></div>
        </details>
        <details class="flow-more"><summary>Sample data for Preview</summary>
          <label class="flow-f">JSON<textarea data-k="sample" class="flow-code" rows="4" spellcheck="false" placeholder="empty: 15 made-up items">${esc(c.sample || '')}</textarea></label>
        </details>
        <div class="flow-btns"><button class="small" id="flow-view-preview">${icon('eye')} Preview</button></div>`;
  } else if (node.type === 'script') {
    fields = text('input', 'Input (optional)', 'empty: what the block before handed on, else the last response JSON')
      + `<label class="flow-f">JavaScript<textarea data-k="code" class="flow-code" rows="12" spellcheck="false">${esc(c.code || '')}</textarea></label>`
      + `<p class="muted small"><code>input</code>, <code>vars</code>, <code>prev</code> (<code>prev.json</code>, <code>prev.status</code>), <code>email</code>.
          <code>set('payload', {...})</code> makes <code>{{payload}}</code> for the next request's body; <code>return</code> hands a value on; <code>log()</code>, <code>fail('why')</code>. <code>await</code> works.</p>`
      + text('name', 'Keep what it returns as', 'result')
      + `<div class="flow-btns"><button class="small" id="flow-script-try">${icon('play')} Try it</button>
          <select id="flow-script-examples" class="small" aria-label="Insert an example"><option value="">Insert an example…</option>
            ${FLOW_SCRIPT_EXAMPLES.map(([label], i) => `<option value="${i}">${esc(label)}</option>`).join('')}</select></div>
        <details class="flow-more"><summary>Input to try it with</summary>
          <textarea id="flow-script-sample" class="flow-code" rows="4" spellcheck="false" placeholder='e.g. [{"id": 3}, {"id": 7}]'>${esc(flowUi.trySample || '')}</textarea></details>
        <pre id="flow-script-out" class="flow-script-out" hidden></pre>`;
  } else if (node.type === 'stop') {
    fields = `<label class="flow-f">End the flow as<select data-k="result"><option value="pass" ${c.result !== 'fail' ? 'selected' : ''}>passed</option><option value="fail" ${c.result === 'fail' ? 'selected' : ''}>failed</option></select></label>`
      + text('message', 'Message (optional)', '', false);
  }
  box.innerHTML = `<div class="flow-config-head"><strong>${esc(FLOW_BLOCKS[node.type].title)}</strong></div>
    <p class="muted small">${esc(FLOW_BLOCKS[node.type].hint)}</p>${fields}`;
  showFlowMatchReads(c);
  bindFlowSignIn(box);
  const check = $('#flow-match-check');
  if (check) check.addEventListener('click', () => checkFlowMatch(c));
  $$('[data-k]', box).forEach((input) => {
    const apply = () => {
      const key = input.dataset.k;
      c[key] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value)
        : input.dataset.bool ? input.value === 'true' : input.value;
      const body = $(`.flow-node[data-id="${CSS.escape(node.id)}"] .fn-body`);
      if (body) body.innerHTML = flowSummary(node);
      showFlowMatchReads(c);
      drawFlowWires();   // a longer summary can move the ports
      scheduleFlowSave();
      if (input.dataset.rerender) { renderFlowConfig(); renderFlowList(); }
    };
    input.addEventListener(input.tagName === 'SELECT' || input.type === 'checkbox' ? 'change' : 'input', apply);
  });
  bindFlowCodeFields(node, box);
}

// the code fields of the view and script blocks: an editor, examples, Preview and Try it
function bindFlowCodeFields(node, box) {
  const c = node.config;
  const langs = { html: 'html', js: 'javascript', code: 'javascript', sample: 'json' };
  const editor = (ta, lang) => {
    if (typeof enhanceTextarea !== 'function' || !window.CM) return;
    try { enhanceTextarea(ta, { lang }); } catch { /* the plain textarea still works */ }
  };
  $$('textarea.flow-code[data-k]', box).forEach((ta) => editor(ta, langs[ta.dataset.k] || 'text'));
  const sample = $('#flow-script-sample', box);
  if (sample) editor(sample, 'json');
  const setField = (key, value) => {
    c[key] = value;
    const ta = $(`textarea[data-k="${key}"]`, box);
    if (ta) { if (ta._enhanced) ta._enhanced.setValue(value); else ta.value = value; }
    const body = $(`.flow-node[data-id="${CSS.escape(node.id)}"] .fn-body`);
    if (body) body.innerHTML = flowSummary(node);
    scheduleFlowSave();
  };
  const example = $('#flow-view-example', box);
  if (example) example.addEventListener('click', async () => {
    if ((c.html || c.js) && !await confirmDialog('Replace your HTML and JavaScript?', 'The example takes the place of what is in both fields.', { okLabel: 'Replace' })) return;
    setField('html', FLOW_VIEW_EXAMPLE.html);
    setField('js', FLOW_VIEW_EXAMPLE.js);
  });
  const preview = $('#flow-view-preview', box);
  if (preview) preview.addEventListener('click', () => previewFlowView(node));
  const pick = $('#flow-script-examples', box);
  if (pick) pick.addEventListener('change', async () => {
    const chosen = FLOW_SCRIPT_EXAMPLES[Number(pick.value)];
    pick.value = '';
    if (!chosen) return;
    if (String(c.code || '').trim() && c.code !== FLOW_SCRIPT_EXAMPLE && !await confirmDialog('Replace the script?', `“${esc(chosen[0])}” takes the place of what you wrote.`, { okLabel: 'Replace' })) return;
    setField('code', chosen[1]);
  });
  const tryIt = $('#flow-script-try', box);
  if (tryIt) tryIt.addEventListener('click', async () => {
    const out = $('#flow-script-out', box);
    flowUi.trySample = sample ? sample.value : '';
    let input;
    if (flowUi.trySample.trim()) {
      try { input = JSON.parse(flowUi.trySample); } catch { input = flowUi.trySample; }
    } else input = flowDemoItems().slice(0, 3);
    out.hidden = false;
    out.className = 'flow-script-out';
    out.textContent = 'Running…';
    let res;
    try { res = await api('/flows/try-script', { method: 'POST', body: { code: c.code || '', input } }); } catch (err) { out.textContent = err.message; return; }
    const lines = [];
    if (!flowUi.trySample.trim()) lines.push('// input: 3 made-up items (put your own under “Input to try it with”)');
    res.logs.forEach((l) => lines.push('log: ' + l));
    Object.entries(res.vars || {}).forEach(([k, v]) => lines.push(`{{${k}}} = ${JSON.stringify(v)}`));
    if (res.error) lines.push('Error: ' + res.error);
    else if (res.failed !== null && res.failed !== undefined) lines.push('fail: ' + res.failed);
    else lines.push('returns: ' + JSON.stringify(res.value, null, 2));
    out.classList.toggle('bad', !res.ok);
    out.textContent = lines.join('\n');
  });
}

// ------------------------------------------------- view and script blocks
// A view block's page runs in a sandboxed frame (scripts only: no access to this page, the
// app's cookies or its API). It talks to us with postMessage: submit, cancel. The data, the
// settings and the built-in picker (pyhttp.picker) are put in the page before the user's code.
const FLOW_SCRIPT_EXAMPLE = `// input: what the block before handed on (e.g. the items picked)
// Build what the next request sends: its body can be {{payload}}
const ids = (Array.isArray(input) ? input : [input]).map((item) => item.id ?? item);
set('payload', { ids, count: ids.length });
log('prepared', ids.length, 'ids');
return ids;`;

const FLOW_SCRIPT_EXAMPLES = [
  ['Picked items → request body', FLOW_SCRIPT_EXAMPLE],
  ['Unwrap a list from the last response', `// the list inside the last response, for a view block to show
const list = prev.json.items || prev.json.data || prev.json.results || prev.json;
if (!Array.isArray(list)) fail('No list in the response');
return list;`],
  ['Filter and sort', `// keep active items, newest first
return input
  .filter((item) => item.active !== false)
  .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));`],
  ['One request per item: first of the rest', `// hand on the first item, keep the rest for a loop back into this block
const rest = Array.isArray(vars.queue) ? vars.queue : input;
if (!rest.length) fail('Nothing left');   // wire "fail" to where the loop ends
set('item', rest[0]);
set('queue', rest.slice(1));
return rest[0];`],
  ['Check the response', `// fail the flow when the response is not what you expect
if (prev.status !== 200) fail('Status was ' + prev.status);
if (!prev.json || !prev.json.id) fail('No id in the answer');
set('order_id', prev.json.id);
return prev.json.id;`],
];

const FLOW_VIEW_EXAMPLE = {
  html: `<h2 class="title"></h2>
<div class="cards" id="cards"></div>
<div class="bar">
  <span id="count"></span>
  <button id="go" class="primary">Use these</button>
</div>`,
  js: `// pyhttp.data is the list; pyhttp.submit(value) hands value on
const picked = new Set();
document.querySelector('.title').textContent = pyhttp.title;
const cards = document.getElementById('cards');
pyhttp.data.forEach((item, i) => {
  const card = document.createElement('button');
  card.className = 'card';
  card.innerHTML = '<b></b><small></small>';
  card.querySelector('b').textContent = item.name || item.title || ('Item ' + (i + 1));
  card.querySelector('small').textContent = item.id !== undefined ? '#' + item.id : '';
  card.onclick = () => {
    picked.has(i) ? picked.delete(i) : picked.add(i);
    card.classList.toggle('on', picked.has(i));
    document.getElementById('count').textContent = picked.size + ' picked';
  };
  cards.appendChild(card);
});
document.getElementById('go').onclick = () =>
  pyhttp.submit([...picked].map((i) => pyhttp.data[i]));`,
};

function flowDemoItems() {
  const names = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot', 'Golf', 'Hotel', 'India', 'Juliet', 'Kilo', 'Lima', 'Mike', 'November', 'Oscar'];
  return names.map((name, i) => ({ id: i + 1, name, price: Math.round((i + 1) * 7.5 * 100) / 100, active: i % 4 !== 3 }));
}

const FLOW_LS = new RegExp('[' + String.fromCharCode(0x2028, 0x2029) + ']', 'g');   // breaks a script literal
const flowScriptJson = (v) => JSON.stringify(v === undefined ? null : v).replace(/</g, '\\u003c')
  .replace(FLOW_LS, (ch) => '\\u' + ch.charCodeAt(0).toString(16));

// the frame's own runtime: pyhttp.submit/cancel and the built-in list to tick
const FLOW_VIEW_RUNTIME = `(function () {
  var W = window.__W, T = window.__T;
  function send(m) { m.pyhttp = T; parent.postMessage(m, '*'); }
  function shape(items) { return W.field ? items.map(function (i) { return i && typeof i === 'object' ? i[W.field] : i; }) : items; }
  function text(v) { if (v === null || v === undefined) return ''; return typeof v === 'object' ? JSON.stringify(v) : String(v); }
  function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt !== undefined) e.textContent = txt; return e; }
  function picker(where) {
    where = where || document.body;
    var data = W.data, box = el('div', 'pk');
    where.appendChild(box);
    if (!Array.isArray(data)) {
      box.appendChild(el('p', 'muted', 'This is not a list, so there is nothing to tick. Hand it on as it is, or point the block at the list inside it.'));
      box.appendChild(el('pre', 'raw', JSON.stringify(data, null, 2)));
      var one = el('button', 'primary', 'Hand this on'); one.onclick = function () { send({ type: 'submit', value: data }); };
      box.appendChild(one);
      return { selected: function () { return data; } };
    }
    var objs = data.length && data.every(function (d) { return d && typeof d === 'object' && !Array.isArray(d); });
    var cols = ['value'];
    if (objs) {
      var seen = {}, order = [];
      data.slice(0, 50).forEach(function (d) { Object.keys(d).forEach(function (k) { if (!seen[k]) { seen[k] = 0; order.push(k); } if (d[k] === null || typeof d[k] !== 'object') seen[k]++; }); });
      cols = order.filter(function (k) { return seen[k] > 0; }).slice(0, 6);
      if (!cols.length) cols = order.slice(0, 4);
    }
    var chosen = {}, shown = data.map(function (_, i) { return i; });
    var top = el('div', 'pk-top');
    var find = el('input', 'pk-find'); find.type = 'search'; find.placeholder = 'Search ' + data.length + ' items…';
    var all = el('button', '', W.multiple ? 'Tick all shown' : '');
    var count = el('span', 'pk-count');
    var go = el('button', 'primary', W.multiple ? 'Hand on the ticked' : 'Hand on the chosen');
    top.appendChild(find); if (W.multiple) top.appendChild(all); top.appendChild(count); top.appendChild(go);
    box.appendChild(top);
    var wrap = el('div', 'pk-wrap'), table = el('table', 'pk-table');
    wrap.appendChild(table); box.appendChild(wrap);
    var head = el('tr'); head.appendChild(el('th', 'pk-c')); head.appendChild(el('th', 'pk-n', '#'));
    cols.forEach(function (c) { head.appendChild(el('th', '', objs ? c : '')); });
    var thead = el('thead'); thead.appendChild(head); table.appendChild(thead);
    var body = el('tbody'); table.appendChild(body);
    function picked() { return data.filter(function (_, i) { return chosen[i]; }); }
    function check() {
      var n = picked().length, why = '';
      if (!W.multiple && n !== 1) why = 'Choose one';
      else if (W.min && n < W.min) why = 'Tick at least ' + W.min;
      else if (W.max && n > W.max) why = 'Tick at most ' + W.max;
      count.textContent = n + ' of ' + data.length + (W.multiple ? ' ticked' : ' chosen') + (why ? ' · ' + why : '');
      count.className = 'pk-count' + (why && n ? ' bad' : '');
      go.disabled = !!why;
    }
    function draw() {
      body.innerHTML = '';
      shown.forEach(function (i) {
        var d = data[i], tr = el('tr', chosen[i] ? 'on' : '');
        var td = el('td', 'pk-c'), box2 = el('input'); box2.type = W.multiple ? 'checkbox' : 'radio'; box2.name = 'pk'; box2.checked = !!chosen[i];
        td.appendChild(box2); tr.appendChild(td); tr.appendChild(el('td', 'pk-n', String(i + 1)));
        cols.forEach(function (c) { var v = objs ? d[c] : d; var cell = el('td', '', text(v).slice(0, 200)); cell.title = text(v); tr.appendChild(cell); });
        tr.onclick = function () {
          if (!W.multiple) chosen = {};
          chosen[i] = !chosen[i];
          draw(); check();
        };
        body.appendChild(tr);
      });
    }
    find.oninput = function () {
      var q = find.value.trim().toLowerCase();
      shown = data.map(function (_, i) { return i; }).filter(function (i) { return !q || JSON.stringify(data[i]).toLowerCase().indexOf(q) >= 0; });
      draw();
    };
    all.onclick = function () { var every = shown.every(function (i) { return chosen[i]; }); shown.forEach(function (i) { chosen[i] = !every; }); draw(); check(); };
    go.onclick = function () { var p = picked(); send({ type: 'submit', value: W.multiple ? shape(p) : shape(p)[0] }); };
    draw(); check();
    return { selected: function () { return W.multiple ? shape(picked()) : shape(picked())[0]; } };
  }
  window.pyhttp = { data: W.data, title: W.title, multiple: W.multiple, min: W.min, max: W.max, field: W.field,
    submit: function (v) { send({ type: 'submit', value: v === undefined ? null : v }); },
    cancel: function () { send({ type: 'cancel' }); },
    picker: picker };
  window.addEventListener('error', function (e) { send({ type: 'error', message: e.message + (e.lineno ? ' (line ' + e.lineno + ')' : '') }); });
})();`;

function flowViewDoc(w, token) {
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (css.getPropertyValue(name) || '').trim() || fallback;
  const theme = `:root{--bg:${v('--bg', '#111')};--bg-2:${v('--bg-2', '#1a1a1a')};--bg-3:${v('--bg-3', '#222')};--text:${v('--text', '#eee')};
    --muted:${v('--muted', '#999')};--border:${v('--border', '#333')};--primary:${v('--primary', '#3b82f6')};--ok:${v('--ok', '#22c55e')};--danger:${v('--danger', '#ef4444')};}`;
  const base = `${theme}
    *{box-sizing:border-box} html,body{margin:0;background:var(--bg);color:var(--text);font:13px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
    body{padding:12px} button{font:inherit;color:var(--text);background:var(--bg-3);border:1px solid var(--border);border-radius:6px;padding:5px 11px;cursor:pointer}
    button:hover{border-color:var(--primary)} button.primary{background:var(--primary);border-color:var(--primary);color:#fff} button:disabled{opacity:.5;cursor:default}
    input,select,textarea{font:inherit;color:var(--text);background:var(--bg-2);border:1px solid var(--border);border-radius:6px;padding:5px 8px}
    .muted{color:var(--muted)} pre.raw{background:var(--bg-2);border:1px solid var(--border);border-radius:6px;padding:8px;max-height:50vh;overflow:auto}
    .pk-top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px;position:sticky;top:0;background:var(--bg);padding-bottom:6px;z-index:1}
    .pk-find{flex:1;min-width:140px} .pk-count{color:var(--muted);margin-left:auto} .pk-count.bad{color:var(--danger)}
    .pk-table{width:100%;border-collapse:collapse} .pk-table th{text-align:left;color:var(--muted);font-weight:500;font-size:12px;border-bottom:1px solid var(--border);padding:4px 8px;white-space:nowrap}
    .pk-table td{padding:5px 8px;border-bottom:1px solid var(--border);max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .pk-table tr{cursor:pointer} .pk-table tbody tr:hover{background:var(--bg-2)} .pk-table tr.on{background:color-mix(in srgb,var(--primary) 16%,transparent)}
    .pk-c{width:28px} .pk-n{width:36px;color:var(--muted)}
    .title{font-size:15px;margin:0 0 10px} .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
    .card{display:flex;flex-direction:column;align-items:flex-start;gap:2px;text-align:left;padding:10px} .card.on{border-color:var(--primary);background:color-mix(in srgb,var(--primary) 18%,var(--bg-3))}
    .card small{color:var(--muted)} .bar{display:flex;gap:10px;align-items:center;justify-content:flex-end;margin-top:12px}`;
  const safe = (code) => String(code || '').replace(/<\/script/gi, '<\\/script');
  const settings = { data: w.data, title: w.title || 'Pick', multiple: w.multiple !== false, min: Number(w.min) || 0, max: Number(w.max) || 0, field: w.field || '' };
  const own = String(w.html || '').trim() || String(w.js || '').trim();
  return `<!doctype html><html><head><meta charset="utf-8"><style>${base}</style>
    <script>window.__W=${flowScriptJson(settings)};window.__T=${flowScriptJson(token)};</script>
    <script>${FLOW_VIEW_RUNTIME}</script></head><body>
    ${own ? String(w.html || '') : ''}
    <script>${own ? safe(w.js) : 'pyhttp.picker(document.body);'}</script></body></html>`;
}

function flowViewFrame(w, token) {
  const frame = document.createElement('iframe');
  frame.className = 'flow-ask-frame';
  frame.setAttribute('sandbox', 'allow-scripts');
  frame.setAttribute('title', w.title || 'Pick');
  frame.srcdoc = flowViewDoc(w, token);
  return frame;
}

const flowToken = () => Math.random().toString(36).slice(2) + Date.now().toString(36);

// messages from a view's frame: the run waiting on it, or the Preview
window.addEventListener('message', async (e) => {
  const m = e.data;
  if (!m || typeof m !== 'object' || !m.pyhttp) return;
  const target = [flowUi.ask, flowUi.preview].find((t) => t && t.frame && e.source === t.frame.contentWindow && m.pyhttp === t.token);
  if (!target) return;
  if (m.type === 'error') { target.say(`Error in the view's JavaScript: ${m.message}`, true); return; }
  if (target === flowUi.preview) {
    if (m.type === 'submit') target.say('Would hand on as {{' + (target.name || 'selected') + '}}:\n' + JSON.stringify(m.value, null, 2));
    if (m.type === 'cancel') closeModal();
    return;
  }
  const body = m.type === 'cancel' ? { cancel: true } : { value: m.value };
  try {
    await api(`/flow-runs/${target.runId}/answer`, { method: 'POST', body });
    target.say(m.type === 'cancel' ? 'Closed.' : 'Handed on. The flow goes on…');
  } catch (err) { target.say(err.message, true); }
});

// the run waits on a view block: show its page over the canvas
function showFlowAsk(run) {
  const main = $('.flow-main');
  let box = $('#flow-ask');
  const w = run && run.state === 'running' ? run.waiting : null;
  if (!w || !main) {
    if (box) box.remove();
    if (flowUi.ask) clearInterval(flowUi.ask.clock);
    flowUi.ask = null;
    return;
  }
  const key = `${run.id}:${w.node}:${w.until}`;
  if (flowUi.ask && flowUi.ask.key === key && box) return;
  if (box) box.remove();
  if (flowUi.ask) clearInterval(flowUi.ask.clock);
  const token = flowToken();
  box = document.createElement('div');
  box.id = 'flow-ask';
  box.className = 'flow-ask';
  box.innerHTML = `<div class="flow-ask-head"><span class="badge running">waiting for you</span><strong>${esc(w.title || 'Pick')}</strong>
      <span class="muted small">${Array.isArray(w.data) ? `${w.data.length} items` : ''}${w.multiple !== false && (w.min || w.max) ? ` · tick ${w.min || 0}${w.max ? '–' + w.max : '+'}` : ''}</span>
      <span class="grow"></span><span class="muted small" id="flow-ask-left"></span>
      <button class="small" id="flow-ask-close" title="The block takes “timeout”">Close without picking</button></div>
    <div class="flow-ask-say small" id="flow-ask-say" hidden></div>`;
  const frame = flowViewFrame(w, token);
  box.appendChild(frame);
  main.appendChild(box);
  const say = (text, bad = false) => {
    const el = $('#flow-ask-say');
    if (!el) return;
    el.hidden = !text;
    el.textContent = text;
    el.classList.toggle('bad', bad);
  };
  const left = () => {
    const s = Math.max(0, Math.round(w.until - Date.now() / 1000));
    const el = $('#flow-ask-left');
    if (el) el.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} left`;
  };
  left();
  flowUi.ask = { key, runId: run.id, token, frame, say, clock: setInterval(left, 1000) };
  $('#flow-ask-close').addEventListener('click', async () => {
    try { await api(`/flow-runs/${run.id}/answer`, { method: 'POST', body: { cancel: true } }); } catch (err) { say(err.message, true); }
  });
  if (document.hidden || !document.hasFocus()) toast(`“${flowUi.flow ? flowUi.flow.name : 'A flow'}” waits for you to pick`, 'ok');
}

// Preview: the block's page with sample data, in a dialog; nothing is sent to a run
function previewFlowView(node) {
  const c = node.config;
  let data = flowDemoItems();
  if (String(c.sample || '').trim()) {
    try { data = JSON.parse(c.sample); } catch (err) { toast('The sample data is not JSON: ' + err.message, 'error'); return; }
  }
  const w = { ...c, data, multiple: c.multiple !== false, title: c.title || 'Pick items' };
  const token = flowToken();
  openModal(`Preview: ${w.title}`, `<p class="muted small">With ${String(c.sample || '').trim() ? 'your sample data' : '15 made-up items (put your own under “Sample data for Preview”)'}. Handing on shows here what the next block would get.</p>
    <div id="flow-preview-host" class="flow-preview-host"></div><pre id="flow-preview-out" class="flow-script-out" hidden></pre>`,
  [{ label: 'Done', cls: 'primary', onClick: closeModal }]);
  $('#modal .modal').classList.add('wide');
  const frame = flowViewFrame(w, token);
  $('#flow-preview-host').appendChild(frame);
  flowUi.preview = { token, frame, name: c.name, say: (text, bad = false) => {
    const out = $('#flow-preview-out');
    if (!out) return;
    out.hidden = false;
    out.textContent = text;
    out.classList.toggle('bad', bad);
  } };
}

// ------------------------------------------------------------------ a run
function markFlowBlock(id, mark) {
  flowUi.marks[id] = mark;
  const el = $(`.flow-node[data-id="${CSS.escape(id)}"]`);
  if (el) el.className = el.className.replace(/ r-\w+/g, '') + ` r-${mark}`;
}

function showFlowEvents(events) {
  const log = $('#flow-log');
  let last = 0;
  for (const ev of events) {
    last = ev.n;
    if (ev.status === 'running') { if (ev.node) markFlowBlock(ev.node, 'running'); continue; }
    // "input": a view block waits for someone; "info": a line a script logged (the block goes on)
    if (ev.node && ev.status !== 'info') markFlowBlock(ev.node, ev.status === 'fail' ? 'fail' : ev.status === 'input' ? 'running' : 'ok');
    const node = ev.node && flowNode(ev.node);
    const line = document.createElement('div');
    line.className = 'flow-line ' + (ev.status === 'fail' ? 'bad' : ev.status === 'input' ? 'wait' : ev.status === 'info' ? 'info' : 'good') + (ev.node ? '' : ' end');
    line.innerHTML = `<span class="fl-what">${node ? esc(FLOW_BLOCKS[node.type].title) : ''}${ev.port ? ` <span class="fl-port">→ ${esc(ev.port)}</span>` : ''}</span>
      <span class="fl-text">${esc(ev.text)}</span><span class="fl-ms muted">${ev.ms != null ? fmtMs(ev.ms) : ''}</span>`;
    if (ev.node) line.addEventListener('click', () => selectFlowBlock(ev.node));
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  }
  return last;
}

function showFlowState(run) {
  const state = $('#flow-state');
  if (!state) return;
  const cls = run.state === 'passed' ? 'ok' : run.state === 'failed' ? 'failed' : run.state === 'running' ? 'running' : 'cancelled';
  state.innerHTML = `<span class="badge ${cls}">${esc(run.state)}</span>`;
  $('#flow-run-by').textContent = run.started_by ? `started by ${run.started_by}` : run.ended_at ? fmtDate(run.started_at) : '';
}

async function runFlow() {
  const flow = flowUi.flow;
  if (flowUi.saveTimer === null && flowUi.saving) await flowUi.saving;
  await flushFlowSave();
  let run;
  try { run = await api('/flow-runs', { method: 'POST', body: { flow_id: flow.id } }); } catch (err) { toast(err.message, 'error'); return; }
  followFlowRun(flow, run);
}

// read a run's log as it grows, until it ends or the user opens another flow
async function followFlowRun(flow, run) {
  flowUi.run = run.id;
  flowUi.marks = {};
  $$('.flow-node').forEach((el) => { el.className = el.className.replace(/ r-\w+/g, ''); });
  $('#flow-log').innerHTML = '';
  $('#flow-run').disabled = true;
  $('#flow-stop').hidden = false;
  showTab($('#flow-log-tabs'), 'log');
  showFlowState({ ...run, state: 'running' });
  renderFlowList();
  let after = 0;
  while (flowUi.run === run.id && flowUi.flow === flow && $('#flow-log')) {
    let data;
    try { data = await api(`/flow-runs/${run.id}?after=${after}&wait=20`); } catch (err) { toast(err.message, 'error'); break; }
    if (flowUi.flow !== flow) break;
    after = showFlowEvents(data.events) || after;
    showFlowAsk(data);
    if (data.state !== 'running') { showFlowState(data); flow.last_status = data.state; flow.last_run_at = data.started_at; break; }
  }
  if (flowUi.flow === flow) showFlowAsk(null);
  if (flowUi.run === run.id) flowUi.run = null;
  if ($('#flow-run') && flowUi.flow === flow) {
    $('#flow-run').disabled = false;
    $('#flow-stop').hidden = true;
  }
  renderFlowList();
  if (flowUi.flow === flow) loadFlowHistory(flow);        // the run that just ended is in the flow's history
  if (typeof loadHistory === 'function') loadHistory();   // the requests it sent are in the request history
}

function initFlows() {
  const tabs = $('#side-tabs');
  if (!tabs) return;
  bindTabs(tabs, $('#side-panels'));
  $$('button[data-tab]', tabs).forEach((b) => b.addEventListener('click', () => {
    setFlowMode(b.dataset.tab === 'flows');
    // the rail stays when the sidebar is hidden: picking a section there brings its list back
    if ($('.layout').classList.contains('no-side') && $('#btn-sidebar')) $('#btn-sidebar').click();
  }));
  $('#btn-flow-new').addEventListener('click', () => newFlow(''));
  bindFlowTabs();
  bindFlowListDrop();
  $('#flow-search').addEventListener('input', (e) => { flowUi.search = e.target.value; renderFlowList(); });
  window.addEventListener('resize', () => { if ($('#flow-wires') && flowUi.flow) drawFlowWires(); });
  window.addEventListener('beforeunload', () => { if (flowUi.saveTimer !== null) flushFlowSave(); });
  renderFlowView();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initFlows);
else initFlows();
