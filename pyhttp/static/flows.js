'use strict';
// ================================================================== flows
// Saved requests wired into a small program (flows.py runs it). The sidebar has two tabs:
// Requests and Flows. With Flows chosen, the list on the left is the flows and the middle of
// the window is the canvas of the open one: a block leaves through one of its ports, and the
// wire on that port leads to the next block. Drag a block by its title, drag from a port to
// another block to wire them, click a wire to remove it. Changes save as they are made.
const flowUi = { list: [], mail: '', limits: {}, flow: null, selected: null, saveTimer: null, saving: null, run: null, marks: {},
                 loaded: false, poll: null, search: '' };

const FLOW_BLOCKS = {
  start: { title: 'Start', ports: ['out'], hint: 'Where a run begins: when you press Run, or by itself when an email arrives.' },
  request: { title: 'Send request', ports: ['ok', 'fail'], hint: 'Sends a saved request. “fail” is an error, a 4xx/5xx answer or a failed test.' },
  email: { title: 'Wait for email', ports: ['received', 'timeout'], hint: 'Waits for a message to your test inbox, then offers it as {{email.code}}, {{email.link}}, {{email.subject}}, {{email.from}}, {{email.text}}.' },
  set: { title: 'Set variable', ports: ['out'], hint: 'Puts a value in a variable the next blocks and requests can use as {{name}}.' },
  if: { title: 'If', ports: ['true', 'false'], hint: 'Compares two values and takes one of two ways.' },
  delay: { title: 'Wait', ports: ['out'], hint: 'Waits before the next block.' },
  log: { title: 'Log', ports: ['out'], hint: 'Writes a line to the run log.' },
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
  flowUi.mail = data.mail || '';
  flowUi.limits = data.limits || {};
  flowUi.loaded = true;
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
  if (!flowUi.flow) renderFlowView();
}

function flowBadge(f) {
  if (flowUi.flow && flowUi.flow.id === f.id && flowUi.run) return '<span class="flow-dot running" title="Running"></span>';
  if (f.last_status === 'passed') return '<span class="flow-dot ok" title="Last run passed"></span>';
  if (f.last_status === 'failed') return '<span class="flow-dot bad" title="Last run failed"></span>';
  return '<span class="flow-dot" title="Not run yet"></span>';
}

function renderFlowList() {
  const ul = $('#flow-list');
  if (!ul) return;
  const q = flowUi.search.trim().toLowerCase();
  const shown = flowUi.list.filter((f) => !q || f.name.toLowerCase().includes(q));
  if (!shown.length) {
    ul.innerHTML = `<li class="flow-none muted small">${flowUi.list.length ? 'No flow matches the search.' : 'No flows yet. Press “+ New”.'}</li>`;
    return;
  }
  ul.innerHTML = shown.map((f) => {
    const blocks = (f.graph.nodes || []).length;
    const when = f.last_run_at ? ` · ran ${typeof agoText === 'function' ? agoText(f.last_run_at) : fmtDate(f.last_run_at)}` : '';
    return `<li class="flow-item${flowUi.flow && flowUi.flow.id === f.id ? ' active' : ''}" data-id="${f.id}" tabindex="0">
      ${flowBadge(f)}
      <span class="flow-item-text"><span class="flow-item-name">${esc(f.name)}</span>
        <span class="flow-item-meta muted">${flowByEmail(f) ? '<span class="flow-auto" title="Starts by itself when a matching email arrives">✉ on email</span> · ' : ''}${blocks} block${blocks === 1 ? '' : 's'}${when}</span></span>
      <button class="link flow-item-act" data-act="copy" title="Duplicate this flow" aria-label="Duplicate">⧉</button>
      <button class="link flow-item-act danger" data-act="del" title="Delete this flow" aria-label="Delete">✕</button>
    </li>`;
  }).join('');
  $$('.flow-item', ul).forEach((li) => {
    const f = flowUi.list.find((x) => x.id === li.dataset.id);
    const open = () => openFlow(f);
    li.addEventListener('click', (e) => { if (!e.target.closest('.flow-item-act')) open(); });
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target === li) open(); });
    $('[data-act=copy]', li).addEventListener('click', async () => {
      try { await api('/flows', { method: 'POST', body: { name: f.name + ' copy', graph: f.graph } }); } catch (err) { toast(err.message, 'error'); }
      refreshFlows();
    });
    $('[data-act=del]', li).addEventListener('click', async () => {
      if (!await confirmDialog('Delete flow?', `${strong(f.name)} will be removed. The requests it sends stay.`, { okLabel: 'Delete', danger: true })) return;
      try { await api('/flows/' + f.id, { method: 'DELETE' }); } catch (err) { toast(err.message, 'error'); }
      if (flowUi.flow && flowUi.flow.id === f.id) { clearTimeout(flowUi.saveTimer); flowUi.flow = null; flowUi.run = null; renderFlowView(); }
      refreshFlows();
    });
  });
}

function renderFlowInbox() {
  const box = $('#flow-inbox');
  if (!box) return;
  box.hidden = !flowUi.mail;
  if (!flowUi.mail) return;
  box.innerHTML = `<div class="muted small">Your test inbox</div>
    <div class="flow-inbox-row"><code title="${esc(flowUi.mail)}">${esc(flowUi.mail)}</code></div>
    <div class="flow-inbox-row"><button class="link small" id="flow-addr-copy">Copy address</button><button class="link small" id="flow-inbox-open">Open inbox</button></div>`;
  $('#flow-addr-copy').addEventListener('click', async () => { if (await copyText(flowUi.mail)) toast('Address copied', 'ok'); });
  $('#flow-inbox-open').addEventListener('click', openFlowInbox);
}

async function newFlow() {
  try {
    const flow = await api('/flows', { method: 'POST', body: { name: `Flow ${flowUi.list.length + 1}` } });
    await refreshFlows();
    openFlow(flowUi.list.find((f) => f.id === flow.id) || flow);
    const name = $('#flow-name');
    if (name) { name.focus(); name.select(); }
  } catch (err) { toast(err.message, 'error'); }
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
    .map(([type, b]) => `<button class="small flow-add" data-type="${type}" title="${esc(b.hint)}">+ ${esc(b.title)}</button>`).join('');
  view.innerHTML = `
    <div class="flow-bar">
      <input id="flow-name" value="${esc(flow.name)}" aria-label="Flow name" title="Flow name (click to rename)" maxlength="120" spellcheck="false">
      <span class="flow-adds" role="group" aria-label="Add a block">${add}</span>
      <span class="grow"></span>
      <span id="flow-saved" class="save-state"></span>
      <button id="flow-stop" hidden>■ Stop</button>
      <button id="flow-run" class="accent" title="Run this flow now">▶ Run</button>
    </div>
    <div class="flow-main">
      <div id="flow-canvas" class="flow-canvas"><div id="flow-area" class="flow-area"><svg id="flow-wires" class="flow-wires"></svg></div></div>
      <div class="flow-side">
        <div id="flow-config" class="flow-config"></div>
        <div class="flow-log-head"><strong>Run log</strong> <span id="flow-state"></span><span class="grow"></span><span id="flow-run-by" class="muted small"></span></div>
        <div id="flow-log" class="flow-log"><p class="muted small">Press Run. Each block reports here as it finishes.</p></div>
      </div>
    </div>`;
  $('#flow-name').addEventListener('input', (e) => {
    flow.name = e.target.value;
    const label = $(`.flow-item[data-id="${CSS.escape(flow.id)}"] .flow-item-name`);
    if (label) label.textContent = flow.name;
    scheduleFlowSave();
  });
  $$('.flow-add').forEach((b) => b.addEventListener('click', () => addFlowBlock(b.dataset.type)));
  $('#flow-run').addEventListener('click', runFlow);
  $('#flow-stop').addEventListener('click', async () => {
    if (flowUi.run) { try { await api(`/flow-runs/${flowUi.run}/cancel`, { method: 'POST' }); } catch { /* it ended by itself */ } }
  });
  $('#flow-area').addEventListener('pointerdown', (e) => { if (e.target.id === 'flow-area' || e.target.id === 'flow-wires') selectFlowBlock(null); });
  renderFlow();
  renderFlowConfig();
}

async function openFlow(flow) {
  if (flowUi.flow && flowUi.flow.id === flow.id) return;
  await flushFlowSave();
  flowUi.flow = flow;
  flowUi.selected = null;
  flowUi.run = null;
  flowUi.marks = {};
  renderFlowList();
  renderFlowView();
  showLastFlowRun(flow);
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
  return c.trigger === 'email' ? `✉ when an email arrives: ${esc(flowMatchText(c))}` : 'when you press Run';
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
      <div class="fn-head"><span class="fn-title">${esc(b.title)}</span>${node.type === 'start' ? '' : '<button class="fn-del link" title="Remove this block" aria-label="Remove block">✕</button>'}</div>
      <div class="fn-body" title="">${flowSummary(node)}</div>
      <div class="fn-ports">${b.ports.map((p) => `<span class="fn-port p-${p}" data-port="${p}" title="Drag to the block that runs next">${b.ports.length > 1 ? esc(p) : ''}<i></i></span>`).join('')}</div>`;
    area.appendChild(el);
    $('.fn-head', el).addEventListener('pointerdown', (e) => { if (!e.target.closest('.fn-del')) dragFlowBlock(e, node, el); });
    el.addEventListener('pointerdown', (e) => { if (!e.target.closest('.fn-port, .fn-del')) selectFlowBlock(node.id); });
    const del = $('.fn-del', el);
    if (del) del.addEventListener('click', () => removeFlowBlock(node.id));
    $$('.fn-port', el).forEach((port) => port.addEventListener('pointerdown', (e) => dragFlowWire(e, node, port)));
  });
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
    if (moved) scheduleFlowSave();
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
    + data.messages.slice(0, 6).map((m) => `<div class="fm-msg ${m.fits ? 'yes' : 'no'}"><span class="fm-mark">${m.fits ? '✓' : '✕'}</span>
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
  const noMail = '<p class="small err-text">This app has no mail system. Flows receive email in pyhttp on the web (app.pyhttp.com).</p>';
  let fields = '';
  if (node.type === 'start') {
    fields = `<label class="flow-f">This flow runs<select data-k="trigger" data-rerender="1">
        <option value="manual" ${c.trigger !== 'email' ? 'selected' : ''}>when I press Run</option>
        <option value="email" ${c.trigger === 'email' ? 'selected' : ''}>by itself, when an email arrives</option></select></label>`;
    if (c.trigger === 'email') {
      fields += (flowUi.mail ? `<p class="small">Every message to <code>${esc(flowUi.mail)}</code> that fits starts a run, with the message as <code>{{email.…}}</code>. Run still starts it by hand.</p>` : noMail)
        + flowMatchFields(c);
    }
  } else if (node.type === 'request') {
    const options = (state.requests || []).map((r) => `<option value="${r.id}" ${r.id === c.request_id ? 'selected' : ''}>${esc(r.method)} ${esc(r.name)}</option>`).join('');
    fields = `<label class="flow-f">Request<select data-k="request_id"><option value="">Pick a saved request…</option>${options}</select></label>
      <p class="muted small">Its tests, extractions and scripts run as usual. With several bodies, the first one switched on is sent.</p>`;
  } else if (node.type === 'email') {
    fields = (flowUi.mail
      ? `<p class="small">Waits for a message to <code>${esc(flowUi.mail)}</code> that arrives after the flow started (or after the last email block).</p>`
      : noMail.replace('</p>', ' Here the block takes “timeout”.</p>'))
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
  } else if (node.type === 'stop') {
    fields = `<label class="flow-f">End the flow as<select data-k="result"><option value="pass" ${c.result !== 'fail' ? 'selected' : ''}>passed</option><option value="fail" ${c.result === 'fail' ? 'selected' : ''}>failed</option></select></label>`
      + text('message', 'Message (optional)', '', false);
  }
  box.innerHTML = `<div class="flow-config-head"><strong>${esc(FLOW_BLOCKS[node.type].title)}</strong></div>
    <p class="muted small">${esc(FLOW_BLOCKS[node.type].hint)}</p>${fields}`;
  showFlowMatchReads(c);
  const check = $('#flow-match-check');
  if (check) check.addEventListener('click', () => checkFlowMatch(c));
  $$('[data-k]', box).forEach((input) => {
    const apply = () => {
      const key = input.dataset.k;
      c[key] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
      const body = $(`.flow-node[data-id="${CSS.escape(node.id)}"] .fn-body`);
      if (body) body.innerHTML = flowSummary(node);
      showFlowMatchReads(c);
      drawFlowWires();   // a longer summary can move the ports
      scheduleFlowSave();
      if (input.dataset.rerender) { renderFlowConfig(); renderFlowList(); }
    };
    input.addEventListener(input.tagName === 'SELECT' || input.type === 'checkbox' ? 'change' : 'input', apply);
  });
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
    if (ev.node) markFlowBlock(ev.node, ev.status === 'fail' ? 'fail' : 'ok');
    const node = ev.node && flowNode(ev.node);
    const line = document.createElement('div');
    line.className = 'flow-line ' + (ev.status === 'fail' ? 'bad' : 'good') + (ev.node ? '' : ' end');
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
  showFlowState({ ...run, state: 'running' });
  renderFlowList();
  let after = 0;
  while (flowUi.run === run.id && flowUi.flow === flow && $('#flow-log')) {
    let data;
    try { data = await api(`/flow-runs/${run.id}?after=${after}&wait=20`); } catch (err) { toast(err.message, 'error'); break; }
    if (flowUi.flow !== flow) break;
    after = showFlowEvents(data.events) || after;
    if (data.state !== 'running') { showFlowState(data); flow.last_status = data.state; flow.last_run_at = data.started_at; break; }
  }
  if (flowUi.run === run.id) flowUi.run = null;
  if ($('#flow-run') && flowUi.flow === flow) {
    $('#flow-run').disabled = false;
    $('#flow-stop').hidden = true;
  }
  renderFlowList();
  if (typeof loadHistory === 'function') loadHistory();   // the requests it sent are in the history
}

function initFlows() {
  const tabs = $('#side-tabs');
  if (!tabs) return;
  bindTabs(tabs, $('#side-panels'));
  $$('button[data-tab]', tabs).forEach((b) => b.addEventListener('click', () => setFlowMode(b.dataset.tab === 'flows')));
  $('#btn-flow-new').addEventListener('click', newFlow);
  $('#flow-search').addEventListener('input', (e) => { flowUi.search = e.target.value; renderFlowList(); });
  window.addEventListener('resize', () => { if ($('#flow-wires') && flowUi.flow) drawFlowWires(); });
  window.addEventListener('beforeunload', () => { if (flowUi.saveTimer !== null) flushFlowSave(); });
  renderFlowView();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initFlows);
else initFlows();
