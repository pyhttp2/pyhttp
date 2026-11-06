'use strict';
// Code-editor behaviours for plain <textarea>s: Ctrl+/ comments, Tab indentation,
// auto-indent, a line-number gutter, a JSON linter that understands comments,
// and a formatter that keeps comments. No dependencies.

const INDENT = '  ';

// ------------------------------------------------------------ JSON tokenizer
// Tokens: {t: 'punct'|'string'|'number'|'literal'|'comment'|'error', v, line, col, nl (newline before)}
function jsonTokens(text) {
  const toks = [];
  let i = 0, line = 1, col = 1, sawNl = false;
  const n = text.length;
  const push = (t, v, l, c) => { toks.push({ t, v, line: l, col: c, nl: sawNl }); sawNl = false; };
  while (i < n) {
    const ch = text[i];
    if (ch === '\n') { i++; line++; col = 1; sawNl = true; continue; }
    if (ch === ' ' || ch === '\t' || ch === '\r') { i++; col++; continue; }
    const l = line, c = col;
    if (ch === '/' && text[i + 1] === '/') {
      let j = text.indexOf('\n', i);
      if (j === -1) j = n;
      push('comment', text.slice(i, j).replace(/\s+$/, ''), l, c);
      col += j - i; i = j; continue;
    }
    if (ch === '/' && text[i + 1] === '*') {
      let j = text.indexOf('*/', i + 2);
      j = j === -1 ? n : j + 2;
      const v = text.slice(i, j);
      push('comment', v, l, c);
      const nls = v.split('\n');
      if (nls.length > 1) { line += nls.length - 1; col = nls[nls.length - 1].length + 1; } else col += v.length;
      i = j; continue;
    }
    if ('{}[]:,'.includes(ch)) { push('punct', ch, l, c); i++; col++; continue; }
    if (ch === '"') {
      let j = i + 1, ok = false;
      while (j < n) {
        if (text[j] === '\\') { j += 2; continue; }
        if (text[j] === '"') { ok = true; break; }
        if (text[j] === '\n') break;
        j++;
      }
      if (!ok) { push('error', 'Unterminated string', l, c); return toks; }
      const v = text.slice(i, j + 1);
      if (/[\u0000-\u001f]/.test(v)) { push('error', 'Control character in string', l, c); return toks; }
      if (/\\(?!["\\/bfnrtu])/.test(v) || /\\u(?![0-9a-fA-F]{4})/.test(v)) { push('error', 'Bad escape in string', l, c); return toks; }
      push('string', v, l, c); col += v.length; i = j + 1; continue;
    }
    const m = /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i, i + 40));
    if (m && !/[A-Za-z0-9_.]/.test(text[i + m[0].length] || '')) { push('number', m[0], l, c); i += m[0].length; col += m[0].length; continue; }
    const w = /^[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(i, i + 40));
    if (w) {
      if (['true', 'false', 'null'].includes(w[0])) push('literal', w[0], l, c);
      else push('error', `Unexpected word "${w[0]}" (strings need double quotes)`, l, c);
      if (toks[toks.length - 1].t === 'error') return toks;
      i += w[0].length; col += w[0].length; continue;
    }
    if (ch === "'") { push('error', "Single quotes are not valid JSON; use double quotes", l, c); return toks; }
    push('error', `Unexpected character "${ch}"`, l, c);
    return toks;
  }
  return toks;
}

// Validates JSON (comments allowed). Returns {ok, message, line, col, comments}
function jsonLint(text) {
  if (!text.trim()) return { ok: true, empty: true, message: 'Empty', comments: false };
  const toks = jsonTokens(text);
  const comments = toks.some((t) => t.t === 'comment');
  const err = toks.find((t) => t.t === 'error');
  if (err) return { ok: false, message: err.v, line: err.line, col: err.col, comments };
  const ts = toks.filter((t) => t.t !== 'comment');
  let p = 0;
  const last = ts[ts.length - 1];
  const at = (tok) => tok ? { line: tok.line, col: tok.col } : { line: last ? last.line : 1, col: last ? last.col + String(last.v).length : 1 };
  const fail = (msg, tok) => { throw Object.assign(new Error(msg), at(tok)); };
  const peek = () => ts[p];
  function value() {
    const t = peek();
    if (!t) fail('Unexpected end of input');
    if (t.t === 'string' || t.t === 'number' || t.t === 'literal') { p++; return; }
    if (t.t === 'punct' && t.v === '{') { p++; object(); return; }
    if (t.t === 'punct' && t.v === '[') { p++; array(); return; }
    fail(`Unexpected "${t.v}"`, t);
  }
  function object() {
    if (peek() && peek().v === '}') { p++; return; }
    for (;;) {
      const k = peek();
      if (!k) fail('Unexpected end of input: object not closed');
      if (k.v === '}') fail('Trailing comma before }', k);
      if (k.t !== 'string') fail(k.v === ',' ? 'Unexpected comma' : 'Property name must be a double-quoted string', k);
      p++;
      const colon = peek();
      if (!colon || colon.v !== ':') fail('Expected ":" after property name', colon || k);
      p++;
      value();
      const sep = peek();
      if (!sep) fail('Unexpected end of input: object not closed');
      if (sep.v === ',') { p++; continue; }
      if (sep.v === '}') { p++; return; }
      fail(`Expected "," or "}" but found "${sep.v}"`, sep);
    }
  }
  function array() {
    if (peek() && peek().v === ']') { p++; return; }
    for (;;) {
      const t = peek();
      if (t && t.v === ']') fail('Trailing comma before ]', t);
      value();
      const sep = peek();
      if (!sep) fail('Unexpected end of input: array not closed');
      if (sep.v === ',') { p++; continue; }
      if (sep.v === ']') { p++; return; }
      fail(`Expected "," or "]" but found "${sep.v}"`, sep);
    }
  }
  try {
    value();
    if (p < ts.length) fail(`Unexpected "${ts[p].v}" after the end of the document`, ts[p]);
    return { ok: true, message: comments ? 'Valid JSON (comments are removed when sent)' : 'Valid JSON', comments };
  } catch (e) {
    return { ok: false, message: e.message, line: e.line, col: e.col, comments };
  }
}

// Strip comments in JS (mirror of jsonc.py) for the UI.
function stripJsonComments(text) {
  if (!text || (!text.includes('//') && !text.includes('/*'))) return text || '';
  let out = '', i = 0, inStr = false;
  const n = text.length;
  while (i < n) {
    const c = text[i];
    if (inStr) {
      out += c;
      if (c === '\\' && i + 1 < n) { out += text[i + 1]; i += 2; continue; }
      if (c === '"') inStr = false;
      i++; continue;
    }
    if (c === '"') { inStr = true; out += c; i++; continue; }
    if (c === '/' && text[i + 1] === '/') { let j = text.indexOf('\n', i); i = j === -1 ? n : j; continue; }
    if (c === '/' && text[i + 1] === '*') {
      let j = text.indexOf('*/', i + 2); const end = j === -1 ? n : j + 2;
      out += text.slice(i, end).replace(/[^\n]/g, ''); i = end; continue;
    }
    out += c; i++;
  }
  return out;
}

// Pretty-print JSON while keeping comments in place. Falls back to JSON.stringify when the
// document is valid and has no comments. Returns null when the text is not valid JSON(C).
function formatJsonKeepComments(text, indent = INDENT) {
  const lint = jsonLint(text);
  if (!lint.ok || lint.empty) return null;
  if (!lint.comments) return JSON.stringify(JSON.parse(text), null, indent);
  const toks = jsonTokens(text);
  let out = '', depth = 0, lineOpen = false;   // lineOpen: something is written on the current line
  const pad = () => indent.repeat(depth);
  const newline = () => { out = out.replace(/[ \t]+$/, ''); out += '\n'; lineOpen = false; };
  const write = (s) => { if (!lineOpen) { out += pad(); lineOpen = true; } out += s; };
  for (let i = 0; i < toks.length; i++) {
    const t = toks[i];
    const next = toks[i + 1];
    if (t.t === 'comment') {
      if (t.v.startsWith('//')) {
        if (t.nl || !lineOpen) { if (lineOpen) newline(); write(t.v); newline(); }
        else { out += ' ' + t.v; newline(); }
      } else { // block comment
        if (t.nl && lineOpen) newline();
        if (lineOpen) out += ' ';
        write(t.v);
        if (t.v.includes('\n') || (next && next.nl)) newline(); else out += ' ';
      }
      continue;
    }
    if (t.v === '{' || t.v === '[') {
      const close = t.v === '{' ? '}' : ']';
      // empty container
      let j = i + 1;
      while (toks[j] && toks[j].t === 'comment') j++;
      if (toks[j] && toks[j].v === close && j === i + 1) { write(t.v + close); i = j; afterValue(); continue; }
      write(t.v); depth++; newline(); continue;
    }
    if (t.v === '}' || t.v === ']') {
      depth--; if (lineOpen) newline(); write(t.v); afterValue(); continue;
    }
    if (t.v === ':') { out += ': '; continue; }
    if (t.v === ',') { out += ','; const nc = next && next.t === 'comment' && next.v.startsWith('//') && !next.nl; if (!nc) newline(); continue; }
    write(t.v);
    afterValue();
  }
  function afterValue() { /* separators handle line breaks */ }
  return out.replace(/[ \t]+$/gm, '').replace(/\n{3,}/g, '\n\n').trim() + '\n';
}

// ------------------------------------------------------------------ enhance
// Replaces a <textarea> with a CodeMirror 6 editor (line numbers, folding, bracket
// matching, Ctrl+/ comments, search, syntax colours) while keeping the textarea in
// sync, so the rest of the app keeps reading/writing `ta.value` and listening to
// 'input'. opts: { lang: () => 'json'|'javascript'|'python'|'xml'|'html'|'text' }
const CM_JSON_COMMENTS = { line: '//', block: { open: '/*', close: '*/' } };
function cmLanguage(name) {
  const C = window.CM;
  switch (name) {
    case 'json':
    case 'graphql': return [C.json(), C.jsonLanguage.data.of({ commentTokens: CM_JSON_COMMENTS })];
    case 'javascript': return [C.javascript()];
    case 'python': return [C.python()];
    case 'xml': return [C.xml()];
    case 'html': return [C.html()];
    default: return [];
  }
}
let cmThemeCache = null;
function cmTheme() {
  const C = window.CM;
  if (cmThemeCache) return cmThemeCache;
  const t = C.tags;
  const highlight = C.HighlightStyle.define([
    { tag: [t.propertyName, t.attributeName], color: 'var(--cm-key)' },
    { tag: [t.string, t.special(t.string), t.attributeValue], color: 'var(--cm-string)' },
    { tag: [t.number, t.bool, t.null, t.atom], color: 'var(--cm-number)' },
    { tag: [t.keyword, t.operatorKeyword, t.modifier, t.controlKeyword, t.definitionKeyword], color: 'var(--cm-keyword)' },
    { tag: [t.comment, t.lineComment, t.blockComment], color: 'var(--muted)', fontStyle: 'italic' },
    { tag: [t.tagName, t.angleBracket], color: 'var(--cm-key)' },
    { tag: [t.function(t.variableName), t.function(t.propertyName)], color: 'var(--cm-func)' },
    { tag: [t.variableName, t.definition(t.variableName)], color: 'var(--text)' },
    { tag: [t.punctuation, t.separator, t.bracket, t.brace, t.squareBracket, t.paren], color: 'var(--text)' },
    { tag: t.invalid, color: 'var(--danger)' },
  ]);
  const theme = C.EditorView.theme({
    '&': { height: '100%', backgroundColor: 'var(--bg-3)', color: 'var(--text)', fontSize: 'var(--editor-font)' },
    '.cm-scroller': { fontFamily: 'var(--mono)', lineHeight: '1.5', overflow: 'auto' },
    '.cm-content': { caretColor: 'var(--text)', padding: '6px 0' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--text)' },
    '&.cm-focused': { outline: 'none' },
    '.cm-gutters': { backgroundColor: 'var(--bg-2)', color: 'var(--muted)', borderRight: '1px solid var(--border)' },
    '.cm-activeLineGutter': { backgroundColor: 'rgba(59,130,246,.12)' },
    '.cm-activeLine': { backgroundColor: 'rgba(59,130,246,.06)' },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection': { backgroundColor: 'rgba(59,130,246,.3) !important' },
    '.cm-selectionMatch': { backgroundColor: 'rgba(245,158,11,.25)' },
    '.cm-matchingBracket, &.cm-focused .cm-matchingBracket': { backgroundColor: 'rgba(16,185,129,.25)', outline: '1px solid rgba(16,185,129,.5)' },
    '.cm-foldGutter .cm-gutterElement': { cursor: 'pointer', color: 'var(--muted)' },
    '.cm-foldPlaceholder': { backgroundColor: 'var(--bg-4)', border: '1px solid var(--border)', color: 'var(--muted)', padding: '0 6px', margin: '0 3px', borderRadius: '4px' },
    '.cm-lintRange-error': { backgroundImage: 'none', borderBottom: '2px solid var(--danger)' },
    '.cm-tooltip': { backgroundColor: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--text)' },
    '.cm-panels': { backgroundColor: 'var(--bg-2)', color: 'var(--text)', borderColor: 'var(--border)' },
    // the search / replace panel: CodeMirror's defaults are a light gradient button with inherited (light)
    // text, i.e. unreadable on the dark theme; one wrapping row in the app's own controls instead
    '.cm-panel.cm-search': { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '4px 6px', padding: '6px 30px 6px 8px', position: 'relative' },
    '.cm-panel.cm-search br': { display: 'none' },
    '.cm-panel.cm-search input, .cm-panel.cm-search button, .cm-panel.cm-search label': { fontSize: '12px', margin: '0' },
    '.cm-panel.cm-search label': { color: 'var(--muted)', display: 'inline-flex', alignItems: 'center', gap: '3px', whiteSpace: 'nowrap' },
    '.cm-panel.cm-search .cm-textfield': { backgroundColor: 'var(--bg-3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: '6px', padding: '4px 8px', width: '12em', minWidth: '8em', flex: '1 1 8em', maxWidth: '18em', fontFamily: 'var(--mono)' },
    '.cm-panel.cm-search .cm-textfield:focus': { outline: '2px solid var(--primary)', outlineOffset: '1px' },
    '.cm-panel.cm-search .cm-button': { backgroundImage: 'none', backgroundColor: 'var(--bg-4)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: '6px', padding: '4px 9px', cursor: 'pointer', whiteSpace: 'nowrap' },
    '.cm-panel.cm-search .cm-button:hover': { backgroundColor: 'var(--bg-3)' },
    '.cm-panel.cm-search [name=close]': { position: 'absolute', top: '4px', right: '6px', background: 'none', border: 'none', color: 'var(--muted)', fontSize: '16px', lineHeight: '1', padding: '2px 6px', cursor: 'pointer' },
    '.cm-panel.cm-search [name=close]:hover': { color: 'var(--text)' },
    '.cm-placeholder': { color: 'var(--muted)', fontStyle: 'italic' },
  });
  cmThemeCache = [theme, C.syntaxHighlighting(highlight)];
  return cmThemeCache;
}

function enhanceTextarea(ta, opts = {}) {
  if (ta._enhanced) return ta._enhanced;
  const C = window.CM;
  const wrap = document.createElement('div');
  wrap.className = 'code-editor';
  ta.parentNode.insertBefore(wrap, ta);
  ta.hidden = true;
  ta.classList.add('code-input');
  const status = document.createElement('div');
  status.className = 'code-status';
  wrap.after(status);
  const lang = () => (typeof opts.lang === 'function' ? opts.lang() : (opts.lang || 'text'));
  const langComp = new C.Compartment();
  let currentLang = lang();
  let syncing = false;
  let lintTimer = null;
  const api = { wrap, status, lang, refresh, lint, format, setValue, setCursor, focus: () => view.focus(), search: () => C.openSearchPanel(view), view: null };
  ta._enhanced = api;

  const keys = [
    { key: 'Mod-Enter', run: () => { if (typeof sendCurrent === 'function' && typeof state !== 'undefined' && state.current) sendCurrent(); return true; } },
    { key: 'Mod-b', run: () => { format(); return true; } },
    { key: 'Mod-/', run: C.toggleComment },
    { key: 'Shift-Alt-a', run: C.toggleBlockComment },
    C.indentWithTab,
  ];
  // macOS: Mod- means Cmd, but the UI hints say Ctrl — accept both (elsewhere the extra binding is a no-op duplicate)
  keys.push(...keys.filter((k) => k.key && k.key.startsWith('Mod-')).map((k) => ({ ...k, key: k.key.replace('Mod-', 'Ctrl-') })));
  const view = new C.EditorView({
    state: C.EditorState.create({
      doc: ta.value,
      extensions: [
        C.lineNumbers(), C.foldGutter({ openText: '▾', closedText: '▸' }), C.lintGutter(),
        C.highlightActiveLineGutter(), C.highlightActiveLine(), C.highlightSpecialChars(),
        C.history(), C.drawSelection(), C.dropCursor(), C.indentOnInput(), C.bracketMatching(),
        C.closeBrackets(), C.highlightSelectionMatches(), C.search({ top: true }),
        C.indentUnit.of('  '),
        C.placeholder(ta.placeholder || ''),
        C.keymap.of([...keys, ...C.closeBracketsKeymap, ...C.defaultKeymap, ...C.searchKeymap, ...C.historyKeymap, ...C.foldKeymap, ...C.lintKeymap]),
        langComp.of(cmLanguage(currentLang)),
        ...cmTheme(),
        C.EditorView.updateListener.of((u) => {
          if (!u.docChanged || syncing) return;
          syncing = true;
          ta.value = u.state.doc.toString();
          ta.dispatchEvent(new Event('input', { bubbles: true }));
          syncing = false;
          clearTimeout(lintTimer);
          lintTimer = setTimeout(lint, 250);
        }),
      ],
    }),
    parent: wrap,
  });
  api.view = view;
  // the placeholder attribute may change later (language switch): keep CodeMirror's in sync
  new MutationObserver(() => { /* placeholder is read at creation; re-create cheaply */ }).observe(ta, { attributes: true, attributeFilter: ['placeholder'] });

  function docText() { return view.state.doc.toString(); }
  function setValue(text, keepCaret = false) {
    const cur = docText();
    if (cur === text) return;
    syncing = true;
    const sel = keepCaret ? Math.min(view.state.selection.main.head, text.length) : 0;
    view.dispatch({ changes: { from: 0, to: cur.length, insert: text }, selection: { anchor: sel } });
    syncing = false;
    ta.value = text;
    clearTimeout(lintTimer);
    lintTimer = setTimeout(lint, 50);
  }
  function setCursor(anchor, head) {
    view.dispatch({ selection: { anchor, head: head == null ? anchor : head }, scrollIntoView: true });
    view.focus();
  }
  function syncLang() {
    const l = lang();
    if (l === currentLang) return false;
    currentLang = l;
    view.dispatch({ effects: langComp.reconfigure(cmLanguage(l)) });
    return true;
  }
  function refresh() {
    syncLang();
    if (ta.value !== docText()) setValue(ta.value);
    else lint();
    view.requestMeasure();
  }
  function posOf(line, col) {
    const ln = view.state.doc.line(Math.min(Math.max(1, line), view.state.doc.lines));
    return Math.min(ln.from + Math.max(0, (col || 1) - 1), ln.to);
  }
  function lint() {
    syncLang();
    const l = lang();
    status.className = 'code-status';
    status.onclick = null;
    const text = docText();
    if (l !== 'json' && l !== 'graphql') { status.textContent = ''; status.hidden = true; view.dispatch(C.setDiagnostics(view.state, [])); return null; }
    const res = jsonLint(text);
    status.hidden = false;
    if (res.empty) { status.textContent = ''; status.hidden = true; view.dispatch(C.setDiagnostics(view.state, [])); return res; }
    if (res.ok) {
      status.classList.add('ok'); status.textContent = '✓ ' + res.message;
      view.dispatch(C.setDiagnostics(view.state, []));
    } else {
      status.classList.add('err');
      status.textContent = `✕ Line ${res.line}, col ${res.col}: ${res.message}`;
      status.title = 'Click to jump to the error';
      status.onclick = () => jumpTo(res.line, res.col);
      const from = posOf(res.line, res.col);
      const to = Math.min(view.state.doc.lineAt(from).to, from + 1);
      view.dispatch(C.setDiagnostics(view.state, [{ from, to: Math.max(to, from), severity: 'error', message: res.message }]));
    }
    return res;
  }
  function jumpTo(line, col) { setCursor(posOf(line, col)); }
  function format() {
    const l = lang();
    const v = docText();
    if (l === 'json' || l === 'graphql') {
      const out = formatJsonKeepComments(v);
      if (out === null) { const res = jsonLint(v); toast(res.empty ? 'Nothing to format' : `Body is not valid JSON: line ${res.line}: ${res.message}`, 'error'); if (!res.empty) jumpTo(res.line, res.col); return false; }
      if (out !== v) view.dispatch({ changes: { from: 0, to: v.length, insert: out } });
      return true;
    }
    if (l === 'xml' || l === 'html') {
      const out = formatXml(v);
      if (out !== v) view.dispatch({ changes: { from: 0, to: v.length, insert: out } });
      return true;
    }
    toast('Formatting is available for JSON and XML bodies');
    return false;
  }
  lint();
  return api;
}

// Minimal XML pretty printer (tags only; text nodes are trimmed).
function formatXml(xml) {
  const text = xml.replace(/>\s+</g, '><').trim();
  if (!text) return xml;
  let out = '', depth = 0;
  const parts = text.split(/(<[^>]+>)/g).filter(Boolean);
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (/^<\?|^<!/.test(p)) { out += INDENT.repeat(depth) + p + '\n'; continue; }
    if (/^<\//.test(p)) { depth = Math.max(0, depth - 1); out += INDENT.repeat(depth) + p + '\n'; continue; }
    if (/^<[^>]*\/>$/.test(p)) { out += INDENT.repeat(depth) + p + '\n'; continue; }
    if (/^</.test(p)) {
      const nxt = parts[i + 1], close = parts[i + 2];
      // <tag>text</tag> stays on one line
      if (nxt && !nxt.startsWith('<') && close && /^<\//.test(close) && nxt.trim().length < 80) {
        out += INDENT.repeat(depth) + p + nxt.trim() + close + '\n';
        i += 2;
        continue;
      }
      out += INDENT.repeat(depth) + p + '\n';
      depth++;
      continue;
    }
    const t = p.trim();
    if (t) out += INDENT.repeat(depth) + t + '\n';
  }
  return out.trimEnd() + '\n';
}
