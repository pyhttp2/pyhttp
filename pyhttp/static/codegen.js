'use strict';
// Code snippets from a resolved request ({method, url, params, headers, body, body_kind, files, form}).

const SNIPPET_LANGS = [
  ['curl', 'cURL'], ['python', 'Python (requests)'], ['httpx', 'Python (httpx)'], ['fetch', 'JavaScript (fetch)'],
  ['node', 'Node.js (axios)'], ['powershell', 'PowerShell'], ['http', 'HTTP (raw)'], ['go', 'Go'],
];

function fullUrl(req) {
  const params = req.params || [];
  if (!params.length) return req.url;
  const qs = params.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
  return req.url + (req.url.includes('?') ? '&' : '?') + qs;
}
const shQuote = (s) => `'${String(s).replace(/'/g, `'\\''`)}'`;
const psQuote = (s) => `'${String(s).replace(/'/g, "''")}'`;
const pyStr = (s) => JSON.stringify(String(s));
const headersOf = (req) => (req.headers || []).filter(([k]) => !['content-length', 'host'].includes(k.toLowerCase()));
const bodyOf = (req) => (req.body_kind === 'text' || req.body_kind === 'form') ? (req.body || '') : '';
const isJsonBody = (req) => req.body_kind === 'text' && headersOf(req).some(([k, v]) => k.toLowerCase() === 'content-type' && /json/i.test(v));

function snippet(lang, req) {
  const url = fullUrl(req);
  const method = req.method || 'GET';
  const headers = headersOf(req);
  const body = bodyOf(req);
  const multipart = req.body_kind === 'multipart';
  const hdrs = multipart ? headers.filter(([k]) => k.toLowerCase() !== 'content-type') : headers;
  switch (lang) {
    case 'curl': {
      const lines = [`curl -X ${method} ${shQuote(url)}`];
      hdrs.forEach(([k, v]) => lines.push(`  -H ${shQuote(`${k}: ${v}`)}`));
      if (multipart) {
        (req.form || []).forEach(([k, v]) => lines.push(`  -F ${shQuote(`${k}=${v}`)}`));
        (req.files || []).forEach((f) => lines.push(`  -F ${shQuote(`${f.field}=@${f.filename}`)}`));
      } else if (req.body_kind === 'binary') lines.push(`  --data-binary @file`);
      else if (body) lines.push(`  -d ${shQuote(body)}`);
      return lines.join(' \\\n');
    }
    case 'python': {
      const out = ['import requests', '', `url = ${pyStr(url)}`];
      out.push(`headers = {${hdrs.map(([k, v]) => `\n    ${pyStr(k)}: ${pyStr(v)},`).join('')}${hdrs.length ? '\n' : ''}}`);
      let call = `requests.request(${pyStr(method)}, url, headers=headers`;
      if (multipart) {
        out.push(`data = {${(req.form || []).map(([k, v]) => `${pyStr(k)}: ${pyStr(v)}`).join(', ')}}`);
        out.push(`files = {${(req.files || []).map((f) => `${pyStr(f.field)}: open(${pyStr(f.filename)}, "rb")`).join(', ')}}`);
        call += ', data=data, files=files';
      } else if (isJsonBody(req)) {
        let parsed = null;
        try { parsed = JSON.parse(body); } catch { parsed = null; }
        if (parsed !== null) { out.push(`payload = ${pyLiteral(parsed, 0)}`); call += ', json=payload'; }
        else { out.push(`payload = ${pyStr(body)}`); call += ', data=payload'; }
      } else if (body) { out.push(`payload = ${pyStr(body)}`); call += ', data=payload'; }
      out.push('', `response = ${call})`, 'print(response.status_code)', 'print(response.text)');
      return out.join('\n');
    }
    case 'httpx': {
      const out = ['import httpx', '', `url = ${pyStr(url)}`];
      out.push(`headers = {${hdrs.map(([k, v]) => `\n    ${pyStr(k)}: ${pyStr(v)},`).join('')}${hdrs.length ? '\n' : ''}}`);
      let call = `httpx.request(${pyStr(method)}, url, headers=headers`;
      if (multipart) {
        out.push(`files = {${(req.files || []).map((f) => `${pyStr(f.field)}: open(${pyStr(f.filename)}, "rb")`).join(', ')}}`);
        out.push(`data = {${(req.form || []).map(([k, v]) => `${pyStr(k)}: ${pyStr(v)}`).join(', ')}}`);
        call += ', data=data, files=files';
      } else if (body) { out.push(`content = ${pyStr(body)}`); call += ', content=content'; }
      out.push('', `response = ${call}, timeout=30)`, 'print(response.status_code, response.text)');
      return out.join('\n');
    }
    case 'fetch': {
      const out = [];
      const opts = [`  method: ${JSON.stringify(method)}`];
      if (hdrs.length) opts.push(`  headers: {${hdrs.map(([k, v]) => `\n    ${JSON.stringify(k)}: ${JSON.stringify(v)},`).join('')}\n  }`);
      if (multipart) {
        out.push('const form = new FormData();');
        (req.form || []).forEach(([k, v]) => out.push(`form.append(${JSON.stringify(k)}, ${JSON.stringify(v)});`));
        (req.files || []).forEach((f) => out.push(`form.append(${JSON.stringify(f.field)}, fileInput.files[0]); // ${f.filename}`));
        opts.push('  body: form');
      } else if (body) opts.push(`  body: ${JSON.stringify(body)}`);
      out.push(`const response = await fetch(${JSON.stringify(url)}, {\n${opts.join(',\n')}\n});`, 'console.log(response.status);', 'console.log(await response.text());');
      return out.join('\n');
    }
    case 'node': {
      const out = ["const axios = require('axios');", ''];
      const cfg = [`  method: ${JSON.stringify(method.toLowerCase())}`, `  url: ${JSON.stringify(url)}`];
      if (hdrs.length) cfg.push(`  headers: {${hdrs.map(([k, v]) => `\n    ${JSON.stringify(k)}: ${JSON.stringify(v)},`).join('')}\n  }`);
      if (body) cfg.push(`  data: ${JSON.stringify(body)}`);
      out.push(`axios({\n${cfg.join(',\n')}\n}).then((r) => console.log(r.status, r.data)).catch((e) => console.error(e.message));`);
      return out.join('\n');
    }
    case 'powershell': {
      const out = [];
      out.push(`$headers = @{${hdrs.map(([k, v]) => `\n    ${psQuote(k)} = ${psQuote(v)}`).join('')}${hdrs.length ? '\n' : ''}}`);
      let call = `Invoke-RestMethod -Method ${method} -Uri ${psQuote(url)} -Headers $headers`;
      if (body) { out.push(`$body = @'\n${body}\n'@`); call += ' -Body $body'; }
      out.push(`$response = ${call}`, '$response | ConvertTo-Json -Depth 10');
      return out.join('\n');
    }
    case 'http': {
      let host = '';
      try { host = new URL(url).host; } catch { host = ''; }
      let pathq = url;
      try { const u = new URL(url); pathq = u.pathname + u.search; } catch { /* keep */ }
      const lines = [`${method} ${pathq} HTTP/1.1`, `Host: ${host}`];
      headers.forEach(([k, v]) => { if (k.toLowerCase() !== 'host') lines.push(`${k}: ${v}`); });
      lines.push('');
      if (body) lines.push(body);
      return lines.join('\n');
    }
    case 'go': {
      const out = ['package main', '', 'import (', '\t"fmt"', '\t"io"', '\t"net/http"', '\t"strings"', ')', '', 'func main() {'];
      out.push(`\turl := ${JSON.stringify(url)}`);
      if (body) out.push(`\tpayload := strings.NewReader(${JSON.stringify(body)})`, `\treq, _ := http.NewRequest(${JSON.stringify(method)}, url, payload)`);
      else out.push(`\treq, _ := http.NewRequest(${JSON.stringify(method)}, url, nil)`);
      hdrs.forEach(([k, v]) => out.push(`\treq.Header.Set(${JSON.stringify(k)}, ${JSON.stringify(v)})`));
      out.push('\tres, err := http.DefaultClient.Do(req)', '\tif err != nil {', '\t\tpanic(err)', '\t}', '\tdefer res.Body.Close()',
        '\tbody, _ := io.ReadAll(res.Body)', '\tfmt.Println(res.StatusCode)', '\tfmt.Println(string(body))', '}');
      return out.join('\n');
    }
    default:
      return '';
  }
}

function pyLiteral(v, depth) {
  const pad = '    '.repeat(depth + 1), end = '    '.repeat(depth);
  if (v === null) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') return pyStr(v);
  if (Array.isArray(v)) return v.length ? `[\n${v.map((x) => pad + pyLiteral(x, depth + 1)).join(',\n')}\n${end}]` : '[]';
  const keys = Object.keys(v);
  return keys.length ? `{\n${keys.map((k) => `${pad}${pyStr(k)}: ${pyLiteral(v[k], depth + 1)}`).join(',\n')}\n${end}}` : '{}';
}
