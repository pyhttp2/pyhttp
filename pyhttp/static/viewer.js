'use strict';
// Response viewer: Pretty (collapsible JSON tree / coloured XML), Raw, Preview,
// wrap toggle, expand/collapse, JSON path filter and in-body search.
// mountViewer(container, resp, opts) renders into any element.

const VIEWER_MAX_TREE = 2_000_000;   // chars: above this, XML/HTML are shown as plain text (JSON is always a tree)
const VIEWER_INITIAL_DEPTH = 3;
const VIEWER_NODE_BUDGET = 3000;     // nodes rendered before deeper levels start collapsed

function detectLang(resp) {
  const ct = (resp && resp.content_type || '').toLowerCase();
  if (resp && resp.is_json) return 'json';
  if (ct.includes('html')) return 'html';
  if (ct.includes('xml')) return 'xml';
  if (ct.includes('json')) return 'json';
  const t = (resp && resp.body_text || '').trimStart();
  if (t.startsWith('{') || t.startsWith('[')) { try { JSON.parse(t); return 'json'; } catch { /* no */ } }
  if (t.startsWith('<!DOCTYPE html') || t.startsWith('<html')) return 'html';
  if (t.startsWith('<?xml') || t.startsWith('<')) return 'xml';
  return 'text';
}

// ------------------------------------------------------------- JSON path
// Dotted path with [index], [*] and * wildcards: data.items[0].name, data.items[*].id, *.name
function jsonPathGet(value, path) {
  const p = (path || '').trim().replace(/^\$\.?/, '');
  if (!p) return { ok: true, value };
  const segs = [];
  p.split('.').forEach((seg) => {
    const m = /^([^[\]]*)((?:\[[^\]]*\])*)$/.exec(seg);
    if (!m) { segs.push({ key: seg }); return; }
    if (m[1]) segs.push({ key: m[1] });
    (m[2].match(/\[[^\]]*\]/g) || []).forEach((b) => segs.push({ idx: b.slice(1, -1).trim() }));
  });
  let current = [value];
  for (const s of segs) {
    const next = [];
    for (const v of current) {
      if (s.key !== undefined) {
        if (s.key === '*') { if (Array.isArray(v)) next.push(...v); else if (v && typeof v === 'object') next.push(...Object.values(v)); }
        else if (v && typeof v === 'object' && !Array.isArray(v) && Object.prototype.hasOwnProperty.call(v, s.key)) next.push(v[s.key]);
        else if (v && typeof v === 'object' && !Array.isArray(v)) {
          const k = Object.keys(v).find((x) => x.toLowerCase() === s.key.toLowerCase());
          if (k !== undefined) next.push(v[k]);
        }
      } else {
        if (s.idx === '*' || s.idx === '') { if (Array.isArray(v)) next.push(...v); }
        else if (Array.isArray(v) && /^-?\d+$/.test(s.idx)) { const i = Number(s.idx) < 0 ? v.length + Number(s.idx) : Number(s.idx); if (v[i] !== undefined) next.push(v[i]); }
        else if (v && typeof v === 'object' && s.idx.replace(/^["']|["']$/g, '') in v) next.push(v[s.idx.replace(/^["']|["']$/g, '')]);
      }
    }
    current = next;
    if (!current.length) return { ok: false };
  }
  const wild = segs.some((s) => s.key === '*' || s.idx === '*' || s.idx === '');
  return { ok: true, value: wild ? current : current[0] };
}

// --------------------------------------------------------------- JSON tree
function jsonTree(root, opts = {}) {
  const box = document.createElement('div');
  box.className = 'jv';
  let rendered = 0;
  const onPath = opts.onPath || null;

  const typeOf = (v) => v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v;
  const isUrl = (s) => /^https?:\/\/\S+$/i.test(s) && s.length < 2000;

  function primitive(v) {
    const t = typeOf(v);
    const span = document.createElement('span');
    if (t === 'string') {
      span.className = 'jv-s';
      const text = JSON.stringify(v);
      if (isUrl(v)) {
        const a = document.createElement('a');
        a.href = v; a.target = '_blank'; a.rel = 'noopener'; a.textContent = text;
        span.appendChild(a);
      } else span.textContent = text;
    } else if (t === 'number') { span.className = 'jv-n'; span.textContent = String(v); }
    else if (t === 'boolean') { span.className = 'jv-b'; span.textContent = String(v); }
    else { span.className = 'jv-null'; span.textContent = 'null'; }
    return span;
  }

  function node(key, value, path, depth, last) {
    const el = document.createElement('div');
    el.className = 'jv-node';
    el.dataset.path = path;
    rendered++;
    const line = document.createElement('div');
    line.className = 'jv-line';
    const t = typeOf(value);
    const container = t === 'object' || t === 'array';
    if (container) {
      const tg = document.createElement('span');
      tg.className = 'jv-toggle';
      tg.textContent = '▾';
      line.appendChild(tg);
    } else {
      const sp = document.createElement('span');
      sp.className = 'jv-toggle blank';
      line.appendChild(sp);
    }
    if (key !== null) {
      const k = document.createElement('span');
      k.className = 'jv-k';
      k.textContent = typeof key === 'number' ? String(key) : JSON.stringify(key);
      if (typeof key === 'number') k.classList.add('idx');
      k.title = 'Click to copy the path ' + path;
      k.addEventListener('click', (e) => { e.stopPropagation(); if (onPath) onPath(path); });
      line.appendChild(k);
      line.appendChild(document.createTextNode(': '));
    }
    if (container) {
      const entries = t === 'array' ? value.map((v, i) => [i, v]) : Object.entries(value);
      const open = document.createElement('span');
      open.className = 'jv-p';
      open.textContent = t === 'array' ? '[' : '{';
      line.appendChild(open);
      const summary = document.createElement('span');
      summary.className = 'jv-summary';
      summary.textContent = entries.length === 0 ? '' : t === 'array' ? `${entries.length} item${entries.length === 1 ? '' : 's'}` : `${entries.length} key${entries.length === 1 ? '' : 's'}`;
      line.appendChild(summary);
      const closeInline = document.createElement('span');
      closeInline.className = 'jv-p jv-close-inline';
      closeInline.textContent = (t === 'array' ? ']' : '}') + (last ? '' : ',');
      line.appendChild(closeInline);
      el.appendChild(line);
      if (entries.length === 0) {
        el.classList.add('leaf');
        summary.remove();
        $('.jv-toggle', line).className = 'jv-toggle blank';
      } else {
        const kids = document.createElement('div');
        kids.className = 'jv-children';
        el.appendChild(kids);
        const close = document.createElement('div');
        close.className = 'jv-closing';
        close.innerHTML = `<span class="jv-toggle blank"></span><span class="jv-p">${t === 'array' ? ']' : '}'}${last ? '' : ','}</span>`;
        el.appendChild(close);
        el._entries = entries;
        el._path = path;
        el._depth = depth;
        el._type = t;
        const expandNow = depth < VIEWER_INITIAL_DEPTH && rendered < VIEWER_NODE_BUDGET;
        if (expandNow) expand(el); else el.classList.add('collapsed');
        line.addEventListener('click', (e) => {
          if (e.target.closest('.jv-k, a')) return;
          toggle(el);
        });
      }
    } else {
      line.appendChild(primitive(value));
      if (!last) line.appendChild(document.createTextNode(','));
      el.appendChild(line);
      el.classList.add('leaf');
    }
    return el;
  }
  function expand(el) {
    const kids = $('.jv-children', el);
    if (!kids) return;
    if (!el._rendered) {
      el._rendered = true;
      const frag = document.createDocumentFragment();
      const entries = el._entries;
      entries.forEach(([k, v], i) => {
        const childPath = el._type === 'array' ? `${el._path}[${k}]` : (el._path ? `${el._path}.${k}` : String(k));
        frag.appendChild(node(k, v, childPath, el._depth + 1, i === entries.length - 1));
      });
      kids.appendChild(frag);
    }
    el.classList.remove('collapsed');
  }
  function toggle(el) { if (el.classList.contains('collapsed')) expand(el); else el.classList.add('collapsed'); }
  function expandAll(limit = 60000) {
    rendered = 0;
    let guard = 0;
    const walk = (el) => {
      if (guard++ > limit) return;
      if (el._entries) { expand(el); $$(':scope > .jv-children > .jv-node', el).forEach(walk); }
    };
    $$(':scope > .jv-node', box).forEach(walk);
    return guard <= limit;
  }
  function collapseAll() {
    $$('.jv-node', box).forEach((el) => { if (el._entries && el._depth > 0) el.classList.add('collapsed'); });
  }
  box.appendChild(node(null, root, '', 0, true));
  box._expandAll = expandAll;
  box._collapseAll = collapseAll;
  return box;
}

// ------------------------------------------------- JSON tree, windowed
// For big documents: the same tree, but only the rows on screen exist in the DOM. The tree is a
// flat list of rows built from what is expanded; scrolling swaps the rows in a window. Rows have
// a fixed height, so they do not wrap (Raw shows long values in full). Search runs over the whole
// document, not over the DOM: a match in a collapsed branch is found, and opened when stepped to.
const VIEWER_VIRTUAL_NODES = 4000;   // more nodes than this: draw the tree as a window of rows
const VROW = 20;                     // px, the height of one row
const VROWS_MAX = 700000;            // rows one scroll area can hold (browsers cap an element's height)
const VSEARCH_MAX = 5000;

// How many values a document holds, counted up to `limit` (cheap for the common small response).
function jsonNodeCount(root, limit) {
  let n = 0;
  const stack = [root];
  while (stack.length && n <= limit) {
    const v = stack.pop();
    n++;
    if (v === null || typeof v !== 'object') continue;
    const kids = Array.isArray(v) ? v : Object.values(v);
    n += kids.length;
    for (let i = 0; i < kids.length && n <= limit; i++) if (kids[i] !== null && typeof kids[i] === 'object') stack.push(kids[i]);
  }
  return n;
}

function jsonTreeVirtual(root, opts = {}) {
  const box = document.createElement('div');
  box.className = 'jv jv-virtual';
  const win = document.createElement('div');
  win.className = 'jv-window';
  box.appendChild(win);
  const onPath = opts.onPath || null;
  const isBox = (v) => v !== null && typeof v === 'object';
  const kidsOf = (v) => (Array.isArray(v) ? v : Object.values(v));
  const isUrl = (s) => /^https?:\/\/\S+$/i.test(s) && s.length < 2000;
  const open = new Set();   // expanded containers, by identity
  let rows = [];            // the visible rows, top to bottom
  let query = '';
  let matches = [];         // paths (as segments) of the rows that hold the search text
  let currentPath = null;   // path of the current match
  let from = -1;
  let to = -1;

  function build() {
    rows = [];
    const walk = (key, value, path, depth, last) => {
      if (rows.length >= VROWS_MAX) return;
      if (!isBox(value)) { rows.push({ kind: 'leaf', key, value, path, depth, last }); return; }
      const arr = Array.isArray(value);
      const keys = arr ? null : Object.keys(value);
      const size = arr ? value.length : keys.length;
      const isOpen = size > 0 && open.has(value);
      rows.push({ kind: 'box', key, value, path, depth, last, arr, size, open: isOpen });
      if (!isOpen) return;
      if (arr) for (let i = 0; i < size; i++) walk(i, value[i], `${path}[${i}]`, depth + 1, i === size - 1);
      else keys.forEach((k, i) => walk(k, value[k], path ? `${path}.${k}` : String(k), depth + 1, i === size - 1));
      rows.push({ kind: 'close', path, depth, last, arr });
    };
    walk(null, root, '', 0, true);
    box.style.height = rows.length * VROW + 'px';
    draw(true);
  }

  // text into `parent`, with the search text marked
  function put(parent, cls, text) {
    const span = document.createElement('span');
    if (cls) span.className = cls;
    const low = query ? text.toLowerCase() : '';
    if (!query || !low.includes(query)) { span.textContent = text; parent.appendChild(span); return span; }
    let i = 0;
    let at;
    while ((at = low.indexOf(query, i)) !== -1) {
      if (at > i) span.appendChild(document.createTextNode(text.slice(i, at)));
      const mark = document.createElement('mark');
      mark.className = 'rv-m';
      mark.textContent = text.slice(at, at + query.length);
      span.appendChild(mark);
      i = at + query.length;
    }
    if (i < text.length) span.appendChild(document.createTextNode(text.slice(i)));
    parent.appendChild(span);
    return span;
  }
  function leafText(v) {
    if (typeof v !== 'string') return String(v);
    const text = JSON.stringify(v);
    return text.length > 600 ? `${text.slice(0, 600)}… (${v.length} characters, see Raw)` : text;
  }
  function rowEl(row, i) {
    const el = document.createElement('div');
    el.className = 'jv-vrow' + (row.kind !== 'close' && row.path === currentPath ? ' cur' : '');
    el.dataset.i = i;
    el.style.paddingLeft = row.depth * 16 + 2 + 'px';
    const tg = document.createElement('span');
    tg.className = 'jv-toggle' + (row.kind === 'box' && row.size ? (row.open ? '' : ' shut') : ' blank');
    tg.textContent = '▾';
    el.appendChild(tg);
    if (row.kind === 'close') { put(el, 'jv-p', (row.arr ? ']' : '}') + (row.last ? '' : ',')); return el; }
    if (row.key !== null) {
      const k = put(el, 'jv-k' + (typeof row.key === 'number' ? ' idx' : ''), typeof row.key === 'number' ? String(row.key) : JSON.stringify(row.key));
      k.title = 'Click to copy the path ' + row.path;
      el.appendChild(document.createTextNode(': '));
    }
    if (row.kind === 'leaf') {
      const v = row.value;
      const cls = v === null ? 'jv-null' : typeof v === 'string' ? 'jv-s' : typeof v === 'number' ? 'jv-n' : 'jv-b';
      if (typeof v === 'string' && isUrl(v)) {
        const span = document.createElement('span');
        span.className = 'jv-s';
        const a = document.createElement('a');
        a.href = v; a.target = '_blank'; a.rel = 'noopener';
        put(a, '', JSON.stringify(v));
        span.appendChild(a);
        el.appendChild(span);
      } else put(el, cls, leafText(v));
      if (!row.last) el.appendChild(document.createTextNode(','));
      return el;
    }
    el.classList.add('box');
    put(el, 'jv-p', row.arr ? '[' : '{');
    if (!row.open) {
      if (row.size) put(el, 'jv-summary', row.arr ? `${row.size} item${row.size === 1 ? '' : 's'}` : `${row.size} key${row.size === 1 ? '' : 's'}`);
      put(el, 'jv-p', (row.arr ? ']' : '}') + (row.last ? '' : ','));
    }
    return el;
  }
  function draw(force = false) {
    if (!box.isConnected) return;
    const top = box.getBoundingClientRect().top;
    const first = Math.max(0, Math.floor(-top / VROW) - 20);
    const last = Math.min(rows.length, Math.ceil((window.innerHeight - top) / VROW) + 20);
    if (!force && first === from && last === to) return;
    from = first;
    to = last;
    win.style.transform = `translateY(${first * VROW}px)`;
    const frag = document.createDocumentFragment();
    for (let i = first; i < last; i++) frag.appendChild(rowEl(rows[i], i));
    win.replaceChildren(frag);
  }
  win.addEventListener('click', (e) => {
    const el = e.target.closest('.jv-vrow');
    const row = el && rows[Number(el.dataset.i)];
    if (!row || e.target.closest('a')) return;
    if (e.target.closest('.jv-k')) { if (onPath) onPath(row.path); return; }
    if (row.kind !== 'box' || !row.size) return;
    if (open.has(row.value)) open.delete(row.value); else open.add(row.value);
    build();
  });

  // any ancestor may be the one that scrolls: listen to them all, and stop when the tree is gone
  let raf = 0;
  const onScroll = () => {
    if (!box.isConnected) { detach(); return; }
    if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); });
  };
  function detach() {
    document.removeEventListener('scroll', onScroll, true);
    window.removeEventListener('resize', onScroll);
  }
  box._attach = () => {
    document.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    draw(true);
  };
  box._virtual = true;
  box._rowCount = () => rows.length;
  box._expandAll = () => {
    const stack = [root];
    while (stack.length) {
      const v = stack.pop();
      if (!isBox(v)) continue;
      open.add(v);
      for (const c of kidsOf(v)) if (isBox(c)) stack.push(c);
    }
    build();
    return rows.length < VROWS_MAX;
  };
  box._collapseAll = () => { open.clear(); if (isBox(root)) open.add(root); build(); };
  // Every row of the document (open or not) whose key or value holds `q`; returns how many.
  box._search = (q) => {
    query = (q || '').toLowerCase();
    matches = [];
    currentPath = null;
    if (query) {
      const segs = [];
      const visit = (key, v) => {
        if (matches.length >= VSEARCH_MAX) return;
        const text = (typeof key === 'string' ? JSON.stringify(key) + ' ' : '') + (isBox(v) ? '' : leafText(v));
        if (text.toLowerCase().includes(query)) matches.push(segs.slice());
        if (!isBox(v)) return;
        if (Array.isArray(v)) for (let i = 0; i < v.length && matches.length < VSEARCH_MAX; i++) { segs.push(i); visit(i, v[i]); segs.pop(); }
        else for (const k of Object.keys(v)) { segs.push(k); visit(k, v[k]); segs.pop(); }
      };
      visit(null, root);
    }
    draw(true);
    return matches.length;
  };
  // Open the branch of match `i` and bring its row to the middle of the screen.
  box._reveal = (i) => {
    const segs = matches[i];
    if (!segs) return;
    let v = root;
    let path = '';
    segs.forEach((seg) => {
      if (isBox(v)) open.add(v);
      path = typeof seg === 'number' ? `${path}[${seg}]` : (path ? `${path}.${seg}` : String(seg));
      v = v[seg];
    });
    currentPath = path;
    build();
    const at = rows.findIndex((r) => r.kind !== 'close' && r.path === path);
    if (at === -1) return;
    const pin = document.createElement('div');
    pin.style.cssText = `position:absolute;left:0;top:${at * VROW}px;height:${VROW}px;width:1px`;
    box.appendChild(pin);
    pin.scrollIntoView({ block: 'center' });
    pin.remove();
    draw(true);
  };

  // start like the plain tree, with the first levels open. Rows off screen cost nothing here, so
  // the budget is generous: it only keeps the first build of an enormous document quick.
  if (isBox(root)) {
    open.add(root);
    let budget = 200000 - kidsOf(root).length;
    let level = kidsOf(root).filter(isBox);
    for (let depth = 1; depth < VIEWER_INITIAL_DEPTH && level.length; depth++) {
      const next = [];
      for (const v of level) {
        const n = kidsOf(v).length;
        if (budget - n < 0) continue;
        budget -= n;
        open.add(v);
        for (const c of kidsOf(v)) if (isBox(c)) next.push(c);
      }
      level = next;
    }
  }
  build();
  return box;
}

// ------------------------------------------------------------ XML / HTML
function highlightXml(text) {
  const out = [];
  const re = /(<!--[\s\S]*?-->)|(<!\[CDATA\[[\s\S]*?\]\]>)|(<\?[\s\S]*?\?>)|(<!DOCTYPE[^>]*>)|(<\/?)([A-Za-z][\w:.-]*)([^>]*?)(\/?>)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    out.push(esc(text.slice(last, m.index)));
    last = re.lastIndex;
    if (m[1]) out.push(`<span class="x-c">${esc(m[1])}</span>`);
    else if (m[2]) out.push(`<span class="x-d">${esc(m[2])}</span>`);
    else if (m[3] || m[4]) out.push(`<span class="x-pi">${esc(m[3] || m[4])}</span>`);
    else {
      const attrs = m[7].replace(/([^\s=]+)(\s*=\s*)("[^"]*"|'[^']*'|[^\s"']+)?/g, (a, n, eq, v) =>
        `<span class="x-a">${esc(n)}</span>${eq ? esc(eq) : ''}${v ? `<span class="x-v">${esc(v)}</span>` : ''}`);
      out.push(`<span class="x-p">${esc(m[5])}</span><span class="x-t">${esc(m[6])}</span>${attrs}<span class="x-p">${esc(m[8])}</span>`);
    }
  }
  out.push(esc(text.slice(last)));
  return out.join('');
}

// ------------------------------------------------------------------ search
function clearMarks(root) {
  $$('mark.rv-m', root).forEach((m) => {
    const parent = m.parentNode;
    parent.replaceChild(document.createTextNode(m.textContent), m);
    parent.normalize();
  });
}
function markMatches(root, query) {
  clearMarks(root);
  if (!query) return [];
  const q = query.toLowerCase();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => n.nodeValue.toLowerCase().includes(q) && !n.parentNode.closest('.rv-bar') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP,
  });
  const nodes = [];
  let n;
  while ((n = walker.nextNode())) nodes.push(n);
  const marks = [];
  nodes.forEach((textNode) => {
    const text = textNode.nodeValue;
    const frag = document.createDocumentFragment();
    let i = 0, idx;
    const lower = text.toLowerCase();
    while ((idx = lower.indexOf(q, i)) !== -1) {
      if (idx > i) frag.appendChild(document.createTextNode(text.slice(i, idx)));
      const mark = document.createElement('mark');
      mark.className = 'rv-m';
      mark.textContent = text.slice(idx, idx + q.length);
      frag.appendChild(mark);
      marks.push(mark);
      i = idx + q.length;
    }
    if (i < text.length) frag.appendChild(document.createTextNode(text.slice(i)));
    textNode.parentNode.replaceChild(frag, textNode);
  });
  return marks;
}

// ---------------------------------------------------------------- viewer
function mountViewer(container, resp, opts = {}) {
  container.innerHTML = '';
  container.classList.add('rv');
  const text = resp ? (resp.body_text || '') : '';
  const lang = detectLang(resp);
  const state = { view: opts.view || (lang === 'text' && !resp.body_base64 ? 'raw' : 'pretty'), wrap: opts.wrap !== false, filter: '', q: '', marks: [], cur: -1, json: undefined, filtered: null };
  container._viewerState = state;
  container._viewerText = () => text;
  const huge = text.length > VIEWER_MAX_TREE;
  const ct = (resp && resp.content_type || '').toLowerCase();
  const previewable = /html/.test(ct) || (resp && resp.is_binary && (ct.startsWith('image/') || ct === 'application/pdf')) || lang === 'html';

  const bar = document.createElement('div');
  bar.className = 'rv-bar';
  bar.innerHTML = `
    <div class="seg tiny rv-views" role="tablist">
      <button data-view="pretty" ${resp && resp.is_binary ? 'disabled' : ''}>Pretty</button>
      <button data-view="raw" ${resp && resp.is_binary ? 'disabled' : ''}>Raw</button>
      <button data-view="preview" ${previewable ? '' : 'disabled'} title="${previewable ? 'Render HTML or show the image' : 'No preview for this content type'}">Preview</button>
    </div>
    <span class="rv-lang muted small">${esc(lang.toUpperCase())}${resp && resp.is_binary ? ' · binary' : ''}</span>
    <button class="rv-btn rv-wrap ${state.wrap ? 'on' : ''}" title="Wrap long lines">Wrap</button>
    <button class="rv-btn rv-expand" title="Expand every node">Expand all</button>
    <button class="rv-btn rv-collapse" title="Collapse to the top level">Collapse</button>
    <input class="rv-filter mono" placeholder="Filter path, e.g. data.items[0].name or items[*].id" title="Show only this part of the JSON. Wildcards: items[*].id, *.name" spellcheck="false">
    <span class="grow"></span>
    <span class="rv-search-box">
      <input class="rv-search" placeholder="Search (Ctrl+F)" spellcheck="false">
      <span class="rv-count muted small"></span>
      <button class="rv-btn rv-prev" title="Previous match (Shift+Enter)">▲</button>
      <button class="rv-btn rv-next" title="Next match (Enter)">▼</button>
    </span>`;
  const body = document.createElement('div');
  body.className = 'rv-body' + (state.wrap ? ' wrap' : '');
  container.appendChild(bar);
  container.appendChild(body);

  function parseJson() {
    if (state.json !== undefined) return state.json;
    try { state.json = lang === 'json' ? JSON.parse(text) : null; } catch { state.json = null; }
    return state.json;
  }
  function copyPath(path) {
    const full = `{{prev.json.${path}}}`;
    copyText(path).then((ok) => toast(ok ? `Copied path: ${path}  (use ${full} to chain)` : 'Could not copy', ok ? 'ok' : 'error'));
  }
  function render() {
    $$('.rv-views button', bar).forEach((b) => b.classList.toggle('on', b.dataset.view === state.view));
    state.tree = null;
    const isJsonTree = state.view === 'pretty' && lang === 'json' && parseJson() !== null;
    $('.rv-expand', bar).hidden = !isJsonTree;
    $('.rv-collapse', bar).hidden = !isJsonTree;
    $('.rv-filter', bar).hidden = !(lang === 'json' && state.view !== 'preview');
    body.innerHTML = '';
    body.className = 'rv-body' + (state.wrap ? ' wrap' : '') + ' v-' + state.view;
    if (!resp) { body.innerHTML = '<div class="rv-empty muted">No response</div>'; return; }
    if (state.view === 'preview') { renderPreview(); return; }
    if (resp.is_binary) {
      body.innerHTML = `<div class="rv-empty muted">Binary content (${esc(resp.content_type || 'unknown type')}, ${fmtBytes(resp.size)}). Use Preview for images, or save the body to a file.</div>`;
      return;
    }
    if (!text) { body.innerHTML = '<div class="rv-empty muted">Empty body</div>'; return; }
    let shown = text;
    if (state.view === 'pretty' && lang === 'json') {
      const data = parseJson();
      if (data === null && lang === 'json') { pre(text); if (resp.truncated) truncNote(); applySearch(); return; }   // not valid JSON, usually because it was cut
      let value = data;
      if (state.filter) {
        const r = jsonPathGet(data, state.filter);
        if (!r.ok) { body.innerHTML = `<div class="rv-empty muted">Nothing matches <code>${esc(state.filter)}</code></div>`; return; }
        value = r.value;
      }
      // a big document is drawn as a window of rows (only what is on screen is in the DOM)
      const big = jsonNodeCount(value, VIEWER_VIRTUAL_NODES) > VIEWER_VIRTUAL_NODES;
      const tree = big ? jsonTreeVirtual(value, { onPath: copyPath }) : jsonTree(value, { onPath: copyPath });
      body.appendChild(tree);
      body.classList.toggle('v-virtual', big);
      if (tree._attach) tree._attach();
      state.tree = tree;
      if (resp.truncated) truncNote();
      applySearch();
      return;
    }
    if (state.view === 'raw' && lang === 'json' && state.filter) {
      const r = jsonPathGet(parseJson(), state.filter);
      shown = r.ok ? JSON.stringify(r.value, null, 2) : '';
      if (!r.ok) { body.innerHTML = `<div class="rv-empty muted">Nothing matches <code>${esc(state.filter)}</code></div>`; return; }
    }
    if (state.view === 'pretty' && (lang === 'xml' || lang === 'html') && !huge) {
      const p = document.createElement('pre');
      p.className = 'rv-pre mono x';
      p.innerHTML = highlightXml(typeof formatXml === 'function' && text.length < 300000 && !/\n\s*</.test(text) ? formatXml(text) : text);
      body.appendChild(p);
      if (resp.truncated) truncNote();
      applySearch();
      return;
    }
    pre(shown);
    if (resp.truncated) truncNote();
    applySearch();
  }
  function pre(t) {
    const p = document.createElement('pre');
    p.className = 'rv-pre mono';
    p.textContent = t;
    body.appendChild(p);
  }
  function note(msg) {
    const d = document.createElement('div');
    d.className = 'rv-note muted small';
    d.textContent = msg;
    body.appendChild(d);
    return d;
  }
  // The viewer holds the first part of a large body. When the whole body was kept (opts.fullBody
  // fetches it), a button loads it into this viewer; the note sits on top so it is not missed.
  function truncNote() {
    const d = note(`Only the first ${fmtBytes(text.length)} of this body (${fmtBytes(resp.size)}) is shown.`);
    body.insertBefore(d, body.firstChild);
    d.classList.add('top');
    if (typeof opts.fullBody !== 'function') { d.textContent += ' The full body is saved with runs.'; return; }
    const b = document.createElement('button');
    b.className = 'link small rv-load-all';
    b.textContent = 'Load the whole body';
    b.addEventListener('click', async () => {
      b.disabled = true;
      b.textContent = 'Loading…';
      try {
        const full = await opts.fullBody();
        mountViewer(container, { ...resp, body_text: full, truncated: false }, { ...opts, view: state.view, wrap: state.wrap, fullBody: null });
      } catch (e) { b.disabled = false; b.textContent = 'Could not load it: ' + e.message; }
    });
    d.appendChild(document.createTextNode(' '));
    d.appendChild(b);
  }
  function renderPreview() {
    if (resp.is_binary && resp.body_base64) {
      if (ct.startsWith('image/')) {
        const img = document.createElement('img');
        img.className = 'rv-img';
        img.src = `data:${ct.split(';')[0]};base64,${resp.body_base64}`;
        img.alt = 'Response image';
        body.appendChild(img);
        return;
      }
      if (ct === 'application/pdf') {
        const fr = document.createElement('iframe');
        fr.className = 'rv-frame';
        fr.src = `data:application/pdf;base64,${resp.body_base64}`;
        body.appendChild(fr);
        return;
      }
    }
    if (resp.is_binary) { body.innerHTML = '<div class="rv-empty muted">Too large to preview inline. Save the body to a file instead.</div>'; return; }
    const fr = document.createElement('iframe');
    fr.className = 'rv-frame';
    fr.setAttribute('sandbox', '');  // no scripts, no navigation, no forms
    fr.srcdoc = text;
    fr.title = 'HTML preview';
    body.appendChild(fr);
  }
  const virtualTree = () => (state.tree && state.tree._virtual && state.tree.isConnected ? state.tree : null);
  function applySearch() {
    const q = state.q.trim();
    const count = $('.rv-count', bar);
    if (virtualTree()) {   // the windowed tree searches the document itself, not the rows on screen
      state.marks = [];
      state.found = virtualTree()._search(q);
      state.cur = state.found ? 0 : -1;
      if (!q) { count.textContent = ''; return; }
      showCurrent();
      return;
    }
    state.found = 0;
    if (!q) { clearMarks(body); state.marks = []; state.cur = -1; count.textContent = ''; return; }
    if (state.tree) state.tree._expandAll(20000);
    state.marks = markMatches(body, q);
    state.cur = state.marks.length ? 0 : -1;
    showCurrent();
  }
  function showCurrent() {
    const count = $('.rv-count', bar);
    if (virtualTree()) {
      count.textContent = state.found ? `${state.cur + 1}/${state.found}${state.found >= VSEARCH_MAX ? '+' : ''}` : 'no match';
      count.classList.toggle('none', !state.found);
      if (state.cur >= 0) virtualTree()._reveal(state.cur);
      return;
    }
    state.marks.forEach((m, i) => m.classList.toggle('cur', i === state.cur));
    count.textContent = state.marks.length ? `${state.cur + 1}/${state.marks.length}` : 'no match';
    count.classList.toggle('none', !state.marks.length);
    if (state.cur >= 0) state.marks[state.cur].scrollIntoView({ block: 'center' });
  }
  function step(d) {
    const n = virtualTree() ? state.found : state.marks.length;
    if (!n) return;
    state.cur = (state.cur + d + n) % n;
    showCurrent();
  }

  $$('.rv-views button', bar).forEach((b) => b.addEventListener('click', () => { if (b.disabled) return; state.view = b.dataset.view; try { localStorage.setItem('pyhttp.rv.view', state.view); } catch { /* ignore */ } render(); }));
  $('.rv-wrap', bar).addEventListener('click', (e) => { state.wrap = !state.wrap; e.currentTarget.classList.toggle('on', state.wrap); body.classList.toggle('wrap', state.wrap); try { localStorage.setItem('pyhttp.rv.wrap', state.wrap ? '1' : '0'); } catch { /* ignore */ } });
  $('.rv-expand', bar).addEventListener('click', () => { if (state.tree && !state.tree._expandAll()) toast(state.tree._virtual ? 'Very large document: the tree stops after 700 000 rows (filter by path to see the rest)' : 'Very large document: only the first 60 000 nodes were expanded'); });
  $('.rv-collapse', bar).addEventListener('click', () => state.tree && state.tree._collapseAll());
  let ft = null;
  $('.rv-filter', bar).addEventListener('input', (e) => { clearTimeout(ft); ft = setTimeout(() => { state.filter = e.target.value.trim(); render(); }, 150); });
  const search = $('.rv-search', bar);
  let st = null;
  search.addEventListener('input', () => { clearTimeout(st); st = setTimeout(() => { state.q = search.value; applySearch(); }, 150); });
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
    if (e.key === 'Escape') { search.value = ''; state.q = ''; applySearch(); search.blur(); }
  });
  $('.rv-prev', bar).addEventListener('click', () => step(-1));
  $('.rv-next', bar).addEventListener('click', () => step(1));
  container._focusSearch = () => { search.focus(); search.select(); };
  if (state.view === 'preview' && !previewable) state.view = 'pretty';
  if (resp && resp.is_binary) state.view = previewable ? 'preview' : 'raw';
  render();
  return container;
}
