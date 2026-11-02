// Postman-compatible `pm` runtime for pyhttp scripts. Runs inside an embedded
// JavaScript engine (QuickJS or V8) before the user's script. State comes in
// as __STATE (JSON from Python) and goes out through __result().
//
// Covered: pm.test / pm.expect (the chai subset Postman scripts use),
// pm.response (code, status, headers, json(), text(), to.have.status(...) ...),
// pm.request (method, url, headers, body; editable in pre-request scripts),
// pm.variables / pm.environment / pm.globals / pm.collectionVariables /
// pm.iterationData, pm.info, pm.cookies, console.*, setTimeout, btoa/atob,
// and the legacy `tests`, `postman.*`, `responseBody`, `responseCode` globals.
// Not available: pm.sendRequest, require(), lodash, CryptoJS (they need Node).
// (No 'use strict' here on purpose: the declarations must become globals that the
// user's script, evaluated separately, can see.)

var __S = __STATE;
var __R = { tests: [], logs: [], error: null, vars: { local: {}, env: {}, globals: {}, collection: {} },
            unset: { env: [], globals: [], collection: [] }, request: null, nextRequest: undefined, timers: 0 };

// ----------------------------------------------------------------- helpers
function __type(v) {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  if (v instanceof RegExp) return 'regexp';
  if (v instanceof Date) return 'date';
  return typeof v;
}
function __inspect(v, depth) {
  depth = depth || 0;
  var t = __type(v);
  if (t === 'string') return depth ? JSON.stringify(v) : v;
  if (t === 'undefined') return 'undefined';
  if (t === 'function') return '[Function' + (v.name ? ': ' + v.name : '') + ']';
  if (t === 'number' || t === 'boolean' || t === 'null') return String(v);
  if (t === 'regexp' || t === 'date') return String(v);
  if (v instanceof Error) return (v.stack || String(v));
  try { return JSON.stringify(v, null, depth ? 0 : 2); } catch (e) { return String(v); }
}
function __fmt(args) {
  var out = [];
  for (var i = 0; i < args.length; i++) out.push(__inspect(args[i], 0));
  return out.join(' ');
}
function __deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== 'object') return (a !== a && b !== b);
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (a instanceof Date && b instanceof Date) return a.getTime() === b.getTime();
  var ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (var i = 0; i < ka.length; i++) {
    if (!Object.prototype.hasOwnProperty.call(b, ka[i]) || !__deepEqual(a[ka[i]], b[ka[i]])) return false;
  }
  return true;
}
function __getPath(obj, path) {
  if (path === undefined || path === null || path === '') return obj;
  var parts = String(path).replace(/\[(\d+)\]/g, '.$1').split('.').filter(Boolean);
  var cur = obj;
  for (var i = 0; i < parts.length; i++) {
    if (cur === null || cur === undefined) return undefined;
    cur = cur[parts[i]];
  }
  return cur;
}
function __hasPath(obj, path) {
  var parts = String(path).replace(/\[(\d+)\]/g, '.$1').split('.').filter(Boolean);
  var cur = obj;
  for (var i = 0; i < parts.length; i++) {
    if (cur === null || cur === undefined || typeof cur !== 'object' || !(parts[i] in cur)) return false;
    cur = cur[parts[i]];
  }
  return true;
}
function __str(v) { return v === undefined || v === null ? '' : String(v); }

// --------------------------------------------------------------- console
var console = {};
['log', 'info', 'warn', 'error', 'debug'].forEach(function (k) {
  console[k] = function () { __R.logs.push((k === 'log' || k === 'info' || k === 'debug' ? '' : k + ': ') + __fmt(arguments)); };
});

// ------------------------------------------------------ chai-like expect
function AssertionError(message) { this.name = 'AssertionError'; this.message = message; this.stack = 'AssertionError: ' + message; }
AssertionError.prototype = Object.create(Error.prototype);
AssertionError.prototype.constructor = AssertionError;

function Assertion(obj, flags) {
  this._obj = obj;
  this._f = flags || { not: false, deep: false, any: false, all: true, nested: false };
}
Assertion.prototype._assert = function (cond, msg, negMsg) {
  var ok = this._f.not ? !cond : cond;
  if (!ok) throw new AssertionError(this._f.not ? negMsg : msg);
  return this;
};
Assertion.prototype._eq = function (a, b) { return this._f.deep ? __deepEqual(a, b) : a === b; };
// language chains
['to', 'be', 'been', 'is', 'that', 'which', 'and', 'has', 'have', 'with', 'at', 'of', 'same', 'but', 'does', 'still', 'also'].forEach(function (w) {
  Object.defineProperty(Assertion.prototype, w, { get: function () { return this; } });
});
Object.defineProperty(Assertion.prototype, 'not', { get: function () { this._f.not = !this._f.not; return this; } });
Object.defineProperty(Assertion.prototype, 'deep', { get: function () { this._f.deep = true; return this; } });
Object.defineProperty(Assertion.prototype, 'nested', { get: function () { this._f.nested = true; return this; } });
Object.defineProperty(Assertion.prototype, 'any', { get: function () { this._f.any = true; this._f.all = false; return this; } });
Object.defineProperty(Assertion.prototype, 'all', { get: function () { this._f.all = true; this._f.any = false; return this; } });
Object.defineProperty(Assertion.prototype, 'own', { get: function () { return this; } });
Object.defineProperty(Assertion.prototype, 'ordered', { get: function () { this._f.ordered = true; return this; } });
// property-style assertions
Object.defineProperty(Assertion.prototype, 'ok', { get: function () { return this._assert(!!this._obj, 'expected ' + __inspect(this._obj, 1) + ' to be truthy', 'expected ' + __inspect(this._obj, 1) + ' to be falsy'); } });
Object.defineProperty(Assertion.prototype, 'true', { get: function () { return this._assert(this._obj === true, 'expected ' + __inspect(this._obj, 1) + ' to be true', 'expected ' + __inspect(this._obj, 1) + ' to not be true'); } });
Object.defineProperty(Assertion.prototype, 'false', { get: function () { return this._assert(this._obj === false, 'expected ' + __inspect(this._obj, 1) + ' to be false', 'expected ' + __inspect(this._obj, 1) + ' to not be false'); } });
Object.defineProperty(Assertion.prototype, 'null', { get: function () { return this._assert(this._obj === null, 'expected ' + __inspect(this._obj, 1) + ' to be null', 'expected ' + __inspect(this._obj, 1) + ' to not be null'); } });
Object.defineProperty(Assertion.prototype, 'undefined', { get: function () { return this._assert(this._obj === undefined, 'expected ' + __inspect(this._obj, 1) + ' to be undefined', 'expected ' + __inspect(this._obj, 1) + ' to not be undefined'); } });
Object.defineProperty(Assertion.prototype, 'NaN', { get: function () { return this._assert(this._obj !== this._obj, 'expected ' + __inspect(this._obj, 1) + ' to be NaN', 'expected ' + __inspect(this._obj, 1) + ' to not be NaN'); } });
Object.defineProperty(Assertion.prototype, 'exist', { get: function () { return this._assert(this._obj !== null && this._obj !== undefined, 'expected ' + __inspect(this._obj, 1) + ' to exist', 'expected ' + __inspect(this._obj, 1) + ' to not exist'); } });
Object.defineProperty(Assertion.prototype, 'empty', { get: function () {
  var o = this._obj, isEmpty;
  if (typeof o === 'string' || Array.isArray(o)) isEmpty = o.length === 0;
  else if (o && typeof o === 'object') isEmpty = Object.keys(o).length === 0;
  else isEmpty = o === null || o === undefined;
  return this._assert(isEmpty, 'expected ' + __inspect(o, 1) + ' to be empty', 'expected ' + __inspect(o, 1) + ' not to be empty');
} });
Object.defineProperty(Assertion.prototype, 'arguments', { get: function () { return this; } });
function __lengthFn(self) {
  var len = function (n) { var l = self._obj == null ? undefined : self._obj.length; return self._assert(l === n, 'expected ' + __inspect(self._obj, 1) + ' to have a length of ' + n + ' but got ' + l, 'expected ' + __inspect(self._obj, 1) + ' to not have a length of ' + n); };
  var l = self._obj == null ? undefined : self._obj.length;
  var la = new Assertion(l, self._f);
  len.above = function (n) { la.above(n); return self; }; len.gt = len.greaterThan = len.above;
  len.below = function (n) { la.below(n); return self; }; len.lt = len.lessThan = len.below;
  len.least = function (n) { la.least(n); return self; }; len.gte = len.least;
  len.most = function (n) { la.most(n); return self; }; len.lte = len.most;
  len.within = function (a, b) { la.within(a, b); return self; };
  return len;
}
Object.defineProperty(Assertion.prototype, 'length', { get: function () { return __lengthFn(this); } });
Object.defineProperty(Assertion.prototype, 'lengthOf', { get: function () { return __lengthFn(this); } });
// method-style assertions
Assertion.prototype.equal = Assertion.prototype.equals = Assertion.prototype.eq = function (v) {
  return this._assert(this._eq(this._obj, v), 'expected ' + __inspect(this._obj, 1) + ' to ' + (this._f.deep ? 'deeply ' : '') + 'equal ' + __inspect(v, 1), 'expected ' + __inspect(this._obj, 1) + ' to not equal ' + __inspect(v, 1));
};
Assertion.prototype.eql = Assertion.prototype.eqls = function (v) {
  return this._assert(__deepEqual(this._obj, v), 'expected ' + __inspect(this._obj, 1) + ' to deeply equal ' + __inspect(v, 1), 'expected ' + __inspect(this._obj, 1) + ' to not deeply equal ' + __inspect(v, 1));
};
Assertion.prototype.above = Assertion.prototype.gt = Assertion.prototype.greaterThan = function (n) { return this._assert(this._obj > n, 'expected ' + __inspect(this._obj, 1) + ' to be above ' + n, 'expected ' + __inspect(this._obj, 1) + ' to be at most ' + n); };
Assertion.prototype.below = Assertion.prototype.lt = Assertion.prototype.lessThan = function (n) { return this._assert(this._obj < n, 'expected ' + __inspect(this._obj, 1) + ' to be below ' + n, 'expected ' + __inspect(this._obj, 1) + ' to be at least ' + n); };
Assertion.prototype.least = Assertion.prototype.gte = function (n) { return this._assert(this._obj >= n, 'expected ' + __inspect(this._obj, 1) + ' to be at least ' + n, 'expected ' + __inspect(this._obj, 1) + ' to be below ' + n); };
Assertion.prototype.most = Assertion.prototype.lte = function (n) { return this._assert(this._obj <= n, 'expected ' + __inspect(this._obj, 1) + ' to be at most ' + n, 'expected ' + __inspect(this._obj, 1) + ' to be above ' + n); };
Assertion.prototype.within = function (a, b) { return this._assert(this._obj >= a && this._obj <= b, 'expected ' + __inspect(this._obj, 1) + ' to be within ' + a + '..' + b, 'expected ' + __inspect(this._obj, 1) + ' to not be within ' + a + '..' + b); };
Assertion.prototype.closeTo = Assertion.prototype.approximately = function (n, d) { return this._assert(Math.abs(this._obj - n) <= d, 'expected ' + this._obj + ' to be close to ' + n + ' +/- ' + d, 'expected ' + this._obj + ' not to be close to ' + n + ' +/- ' + d); };
Assertion.prototype.a = Assertion.prototype.an = function (type) {
  var t = __type(this._obj);
  var want = String(type).toLowerCase();
  var ok = t === want || (want === 'object' && t === 'object');
  return this._assert(ok, 'expected ' + __inspect(this._obj, 1) + ' to be ' + (/^[aeiou]/.test(want) ? 'an ' : 'a ') + want + ' but got ' + t, 'expected ' + __inspect(this._obj, 1) + ' not to be ' + (/^[aeiou]/.test(want) ? 'an ' : 'a ') + want);
};
Assertion.prototype.include = Assertion.prototype.includes = Assertion.prototype.contain = Assertion.prototype.contains = function (v) {
  var o = this._obj, ok = false, self = this;
  if (typeof o === 'string') ok = o.indexOf(String(v)) !== -1;
  else if (Array.isArray(o)) ok = o.some(function (x) { return self._eq(x, v); });
  else if (o && typeof o === 'object') {
    if (v && typeof v === 'object') ok = Object.keys(v).every(function (k) { return k in o && self._eq(o[k], v[k]); });
    else ok = Object.keys(o).some(function (k) { return self._eq(o[k], v); });
  }
  return this._assert(ok, 'expected ' + __inspect(o, 1) + ' to include ' + __inspect(v, 1), 'expected ' + __inspect(o, 1) + ' to not include ' + __inspect(v, 1));
};
Assertion.prototype.string = function (s) { return this._assert(typeof this._obj === 'string' && this._obj.indexOf(s) !== -1, 'expected ' + __inspect(this._obj, 1) + ' to contain ' + __inspect(s, 1), 'expected ' + __inspect(this._obj, 1) + ' to not contain ' + __inspect(s, 1)); };
Assertion.prototype.match = Assertion.prototype.matches = function (re) { if (typeof re === 'string') re = new RegExp(re); return this._assert(re.test(String(this._obj)), 'expected ' + __inspect(this._obj, 1) + ' to match ' + re, 'expected ' + __inspect(this._obj, 1) + ' not to match ' + re); };
Assertion.prototype.property = function (name, value) {
  var has = this._f.nested ? __hasPath(this._obj, name) : (this._obj !== null && this._obj !== undefined && name in Object(this._obj));
  var val = this._f.nested ? __getPath(this._obj, name) : (has ? this._obj[name] : undefined);
  if (arguments.length > 1) {
    this._assert(has && this._eq(val, value), 'expected ' + __inspect(this._obj, 1) + ' to have property ' + JSON.stringify(name) + ' of ' + __inspect(value, 1) + ', but got ' + __inspect(val, 1), 'expected ' + __inspect(this._obj, 1) + ' to not have property ' + JSON.stringify(name) + ' of ' + __inspect(value, 1));
  } else {
    this._assert(has, 'expected ' + __inspect(this._obj, 1) + ' to have property ' + JSON.stringify(name), 'expected ' + __inspect(this._obj, 1) + ' to not have property ' + JSON.stringify(name));
  }
  if (!this._f.not) { var a = new Assertion(val); return a; }
  return this;
};
Assertion.prototype.ownProperty = Assertion.prototype.haveOwnProperty = Assertion.prototype.property;
Assertion.prototype.keys = Assertion.prototype.key = function () {
  var want = Array.isArray(arguments[0]) ? arguments[0] : (arguments.length === 1 && arguments[0] && typeof arguments[0] === 'object') ? Object.keys(arguments[0]) : Array.prototype.slice.call(arguments);
  var have = this._obj && typeof this._obj === 'object' ? Object.keys(this._obj) : [];
  var ok;
  if (this._f.any) ok = want.some(function (k) { return have.indexOf(k) !== -1; });
  else if (this._f.contains) ok = want.every(function (k) { return have.indexOf(k) !== -1; });
  else ok = want.every(function (k) { return have.indexOf(k) !== -1; }) && (this._f.containsKeys || have.length === want.length);
  return this._assert(ok, 'expected ' + __inspect(this._obj, 1) + ' to have keys ' + want.join(', '), 'expected ' + __inspect(this._obj, 1) + ' to not have keys ' + want.join(', '));
};
Assertion.prototype.members = function (arr) {
  var o = this._obj || [], self = this;
  var ok = Array.isArray(o) && arr.every(function (x) { return o.some(function (y) { return self._eq(x, y); }); }) && (this._f.includeMembers || o.length === arr.length);
  return this._assert(ok, 'expected ' + __inspect(o, 1) + ' to have the same members as ' + __inspect(arr, 1), 'expected ' + __inspect(o, 1) + ' to not have the same members as ' + __inspect(arr, 1));
};
Assertion.prototype.oneOf = function (list) { var self = this; return this._assert(list.some(function (x) { return self._eq(self._obj, x); }), 'expected ' + __inspect(this._obj, 1) + ' to be one of ' + __inspect(list, 1), 'expected ' + __inspect(this._obj, 1) + ' to not be one of ' + __inspect(list, 1)); };
Assertion.prototype.instanceOf = Assertion.prototype.instanceof = function (ctor) { return this._assert(this._obj instanceof ctor, 'expected ' + __inspect(this._obj, 1) + ' to be an instance of ' + (ctor && ctor.name), 'expected ' + __inspect(this._obj, 1) + ' to not be an instance of ' + (ctor && ctor.name)); };
Assertion.prototype.satisfy = Assertion.prototype.satisfies = function (fn) { return this._assert(!!fn(this._obj), 'expected ' + __inspect(this._obj, 1) + ' to satisfy ' + (fn.name || 'function'), 'expected ' + __inspect(this._obj, 1) + ' to not satisfy ' + (fn.name || 'function')); };
Assertion.prototype.throw = Assertion.prototype.throws = function (expected) {
  var threw = false, err = null;
  try { this._obj(); } catch (e) { threw = true; err = e; }
  var ok = threw;
  if (threw && expected !== undefined) {
    if (typeof expected === 'string') ok = String(err && err.message || err).indexOf(expected) !== -1;
    else if (expected instanceof RegExp) ok = expected.test(String(err && err.message || err));
    else if (typeof expected === 'function') ok = err instanceof expected;
  }
  return this._assert(ok, 'expected function to throw' + (expected ? ' ' + __inspect(expected, 1) : ''), 'expected function to not throw');
};
Assertion.prototype.respondTo = function (m) { return this._assert(this._obj && typeof this._obj[m] === 'function', 'expected object to respond to ' + m, 'expected object to not respond to ' + m); };
Assertion.prototype.finite = function () { return this._assert(isFinite(this._obj), 'expected ' + this._obj + ' to be finite', 'expected ' + this._obj + ' to not be finite'); };
Object.defineProperty(Assertion.prototype, 'include', { get: function () { var self = this; var fn = function (v) { return Assertion.prototype.includes.call(self, v); }; fn.keys = function () { self._f.contains = true; return Assertion.prototype.keys.apply(self, arguments); }; fn.members = function (arr) { self._f.includeMembers = true; return Assertion.prototype.members.call(self, arr); }; return fn; } });
Object.defineProperty(Assertion.prototype, 'contain', { get: function () { return this.include; } });

// --------------------------------------------------------- property lists
function PropertyList(items, keyName, valueName) {
  // items: [[key, value], ...]
  this._items = items.map(function (p) { return { key: p[0], value: p[1] }; });
  this._k = keyName || 'key'; this._v = valueName || 'value';
}
PropertyList.prototype._find = function (key) { var k = String(key).toLowerCase(); for (var i = 0; i < this._items.length; i++) if (String(this._items[i].key).toLowerCase() === k) return i; return -1; };
PropertyList.prototype.get = function (key) { var i = this._find(key); return i === -1 ? undefined : this._items[i].value; };
PropertyList.prototype.has = function (key, value) { var i = this._find(key); return i !== -1 && (value === undefined || String(this._items[i].value) === String(value)); };
PropertyList.prototype.add = function (item) { this._items.push({ key: item.key !== undefined ? item.key : item[this._k], value: item.value !== undefined ? item.value : item[this._v] }); };
PropertyList.prototype.upsert = function (item) { var k = item.key !== undefined ? item.key : item[this._k]; var v = item.value !== undefined ? item.value : item[this._v]; var i = this._find(k); if (i === -1) this._items.push({ key: k, value: v }); else this._items[i].value = v; };
PropertyList.prototype.remove = function (key) { if (typeof key === 'function') { this._items = this._items.filter(function (it) { return !key(it); }); return; } var i = this._find(key); if (i !== -1) this._items.splice(i, 1); };
PropertyList.prototype.clear = function () { this._items = []; };
PropertyList.prototype.each = function (fn) { this._items.forEach(function (it) { fn({ key: it.key, value: it.value }); }); };
PropertyList.prototype.map = function (fn) { return this._items.map(function (it) { return fn({ key: it.key, value: it.value }); }); };
PropertyList.prototype.filter = function (fn) { return this._items.filter(function (it) { return fn({ key: it.key, value: it.value }); }).map(function (it) { return { key: it.key, value: it.value }; }); };
PropertyList.prototype.all = function () { return this._items.map(function (it) { return { key: it.key, value: it.value }; }); };
PropertyList.prototype.count = function () { return this._items.length; };
PropertyList.prototype.idx = function (i) { var it = this._items[i]; return it ? { key: it.key, value: it.value } : undefined; };
PropertyList.prototype.toObject = function () { var o = {}; this._items.forEach(function (it) { o[it.key] = it.value; }); return o; };
PropertyList.prototype.toJSON = function () { return this.all(); };
PropertyList.prototype.toString = function () { return this._items.map(function (it) { return it.key + ': ' + it.value; }).join('\n'); };
PropertyList.prototype._pairs = function () { return this._items.map(function (it) { return [it.key, it.value]; }); };

// ----------------------------------------------------------- variables
var __V = __S.variables || {};
var __local = Object.assign({}, __V.local || {}), __env = Object.assign({}, __V.env || {}), __globals = Object.assign({}, __V.globals || {}),
    __coll = Object.assign({}, __V.collection || {}), __data = Object.assign({}, __V.data || {});
function __scope(store, name) {
  return {
    get: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : undefined; },
    set: function (k, v) { var t = (v === null || v === undefined) ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v)); store[k] = t; if (name) { __R.vars[name][k] = t; var u = __R.unset[name]; if (u) { var i = u.indexOf(k); if (i !== -1) u.splice(i, 1); } } },
    has: function (k) { return Object.prototype.hasOwnProperty.call(store, k); },
    unset: function (k) { delete store[k]; if (name) { if (__R.vars[name]) delete __R.vars[name][k]; if (__R.unset[name]) __R.unset[name].push(k); } },
    clear: function () { var self = this; Object.keys(store).forEach(function (k) { self.unset(k); }); },
    toObject: function () { return Object.assign({}, store); },
    toJSON: function () { return Object.assign({}, store); },
    replaceIn: function (s) { return __replaceIn(s); },
    name: __S.environmentName || '',
  };
}
function __resolve(k) {
  var stores = [__local, __data, __env, __coll, __globals];
  for (var i = 0; i < stores.length; i++) if (Object.prototype.hasOwnProperty.call(stores[i], k)) return stores[i][k];
  return undefined;
}
function __replaceIn(s) {
  return String(s).replace(/\{\{\s*([^{}]+?)\s*\}\}/g, function (m, k) { var v = __resolve(k); return v === undefined ? m : String(v); });
}
var __variables = __scope(__local, 'local');
__variables.get = function (k) { return __resolve(k); };
__variables.has = function (k) { return __resolve(k) !== undefined; };
__variables.toObject = function () { return Object.assign({}, __globals, __coll, __env, __data, __local); };

// ------------------------------------------------------------- request
var __req = __S.request || {};
var __headers = new PropertyList(__req.headers || []);
var __query = new PropertyList(__req.params || []);
var __reqState = { method: __req.method || 'GET', url: __req.url || '', body: __req.body || '', bodyType: __req.body_type || 'none', auth: __req.auth || null };
function __urlObj(s) {
  var m = /^([a-z][a-z0-9+.-]*:)?\/\/([^/?#]*)([^?#]*)(\?[^#]*)?(#.*)?$/i.exec(s) || [];
  return { protocol: (m[1] || '').replace(':', ''), host: m[2] || '', path: m[3] || '', query: m[4] || '', hash: m[5] || '' };
}
var __url = {
  toString: function () { return __reqState.url; },
  getHost: function () { return __urlObj(__reqState.url).host.split(':')[0]; },
  getPort: function () { return __urlObj(__reqState.url).host.split(':')[1] || ''; },
  getPath: function () { return __urlObj(__reqState.url).path; },
  getQueryString: function () { return __query._pairs().map(function (p) { return encodeURIComponent(p[0]) + '=' + encodeURIComponent(p[1]); }).join('&'); },
  getRaw: function () { return __reqState.url; },
  update: function (s) { __reqState.url = String(s); },
  query: __query,
  get host() { return __urlObj(__reqState.url).host.split(':')[0].split('.'); },
  get path() { return __urlObj(__reqState.url).path.split('/').filter(Boolean); },
  get protocol() { return __urlObj(__reqState.url).protocol; },
  toJSON: function () { return __reqState.url; },
};
var __body = {
  get mode() { var t = __reqState.bodyType; return t === 'none' ? undefined : (t === 'form' ? 'urlencoded' : t === 'multipart' ? 'formdata' : t === 'binary' ? 'file' : t === 'graphql' ? 'graphql' : 'raw'); },
  get raw() { return __reqState.body; },
  set raw(v) { __reqState.body = String(v); },
  update: function (v) { if (v && typeof v === 'object' && 'raw' in v) __reqState.body = String(v.raw); else __reqState.body = String(v); },
  toString: function () { return __reqState.body; },
  toJSON: function () { return { mode: this.mode, raw: __reqState.body }; },
};
var __request = {
  headers: __headers,
  get method() { return __reqState.method; },
  set method(v) { __reqState.method = String(v).toUpperCase(); },
  get url() { return __url; },
  set url(v) { __reqState.url = String(v && v.toString ? v.toString() : v); },
  body: __body,
  get auth() { return __reqState.auth; },
  name: __S.info ? __S.info.requestName : '',
  id: __S.info ? __S.info.requestId : '',
  addHeader: function (h) { __headers.add(h); },
  removeHeader: function (k) { __headers.remove(k); },
  upsertHeader: function (h) { __headers.upsert(h); },
  addQueryParams: function (p) { (Array.isArray(p) ? p : [p]).forEach(function (x) { __query.add(x); }); },
  removeQueryParams: function (p) { (Array.isArray(p) ? p : [p]).forEach(function (x) { __query.remove(typeof x === 'string' ? x : x.key); }); },
  toJSON: function () { return { method: __reqState.method, url: __reqState.url, header: __headers.all(), body: __body.toJSON() }; },
  to: null,
};

// ------------------------------------------------------------ response
var __resp = __S.response;
var __response = null;
if (__resp) {
  var __respHeaders = new PropertyList(__resp.headers || []);
  var __jsonCache, __jsonParsed = false;
  var __reasons = { 200: 'OK', 201: 'Created', 202: 'Accepted', 204: 'No Content', 301: 'Moved Permanently', 302: 'Found', 304: 'Not Modified', 400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found', 405: 'Method Not Allowed', 409: 'Conflict', 422: 'Unprocessable Entity', 429: 'Too Many Requests', 500: 'Internal Server Error', 502: 'Bad Gateway', 503: 'Service Unavailable' };
  __response = {
    code: __resp.code,
    status: __resp.status || __reasons[__resp.code] || '',
    responseTime: __resp.time_ms,
    responseSize: __resp.size,
    headers: __respHeaders,
    cookies: new PropertyList((__resp.cookies || []).map(function (c) { return [c.name, c.value]; })),
    text: function () { return __resp.body || ''; },
    json: function () {
      if (!__jsonParsed) { __jsonParsed = true; __jsonCache = JSON.parse(__resp.body || ''); }
      return __jsonCache;
    },
    reason: function () { return this.status; },
    dataURI: function () { return 'data:' + (__respHeaders.get('content-type') || 'text/plain') + ';base64,' + btoa(__resp.body || ''); },
    toJSON: function () { return { code: __resp.code, status: this.status, header: __respHeaders.all(), responseTime: __resp.time_ms, body: __resp.body }; },
  };
  // pm.response.to.have.status(...) / to.be.ok etc.
  function __respTo(negate) {
    var self = __response;
    var check = function (cond, msg, neg) { var ok = negate ? !cond : cond; if (!ok) throw new AssertionError(negate ? neg : msg); };
    var have = {
      status: function (s) {
        if (typeof s === 'number') check(self.code === s, 'expected response to have status code ' + s + ' but got ' + self.code, 'expected response to not have status code ' + s);
        else check(String(self.status).toLowerCase() === String(s).toLowerCase(), 'expected response to have status reason ' + JSON.stringify(s) + ' but got ' + JSON.stringify(self.status), 'expected response to not have status reason ' + JSON.stringify(s));
      },
      header: function (k, v) {
        if (v === undefined) check(__respHeaders.has(k), 'expected response to have header with key ' + JSON.stringify(k), 'expected response to not have header with key ' + JSON.stringify(k));
        else check(__respHeaders.has(k) && String(__respHeaders.get(k)) === String(v), 'expected response header ' + JSON.stringify(k) + ' to be ' + JSON.stringify(v) + ' but got ' + JSON.stringify(__respHeaders.get(k)), 'expected response header ' + JSON.stringify(k) + ' to not be ' + JSON.stringify(v));
      },
      body: function (b) {
        if (b === undefined) check(!!(__resp.body && __resp.body.length), 'expected response to have a body', 'expected response to not have a body');
        else if (b instanceof RegExp) check(b.test(__resp.body || ''), 'expected response body to match ' + b, 'expected response body to not match ' + b);
        else check((__resp.body || '') === String(b), 'expected response body to equal ' + JSON.stringify(String(b).slice(0, 80)), 'expected response body to not equal ' + JSON.stringify(String(b).slice(0, 80)));
      },
      jsonBody: function (path, value) {
        var data;
        try { data = self.json(); } catch (e) { check(false, 'expected response body to be valid JSON', ''); return; }
        if (path === undefined) { check(true, '', 'expected response body to not be valid JSON'); return; }
        var has = __hasPath(data, path);
        if (value === undefined) check(has, 'expected response JSON to have property ' + JSON.stringify(path), 'expected response JSON to not have property ' + JSON.stringify(path));
        else check(has && __deepEqual(__getPath(data, path), value), 'expected response JSON property ' + JSON.stringify(path) + ' to equal ' + __inspect(value, 1) + ' but got ' + __inspect(__getPath(data, path), 1), 'expected response JSON property ' + JSON.stringify(path) + ' to not equal ' + __inspect(value, 1));
      },
      jsonSchema: function () { throw new Error('pm.response.to.have.jsonSchema is not supported in pyhttp'); },
    };
    var be = {};
    var classes = { info: [100, 199], success: [200, 299], redirection: [300, 399], clientError: [400, 499], serverError: [500, 599], error: [400, 599] };
    Object.keys(classes).forEach(function (name) {
      Object.defineProperty(be, name, { get: function () { var r = classes[name]; check(self.code >= r[0] && self.code <= r[1], 'expected response code to be ' + name + ' (' + r[0] + '-' + r[1] + ') but got ' + self.code, 'expected response code to not be ' + name); return be; } });
    });
    var codes = { ok: 200, accepted: 202, withoutContent: 204, badRequest: 400, unauthorized: 401, forbidden: 403, notFound: 404, rateLimited: 429 };
    Object.keys(codes).forEach(function (name) {
      Object.defineProperty(be, name, { get: function () { check(self.code === codes[name], 'expected response to be ' + name + ' (' + codes[name] + ') but got ' + self.code, 'expected response to not be ' + name); return be; } });
    });
    return { have: have, be: be, get not() { return __respTo(!negate); } };
  }
  __response.to = __respTo(false);
}

// -------------------------------------------------------------- cookies
var __cookieList = new PropertyList(((__resp && __resp.cookies) || []).map(function (c) { return [c.name, c.value]; }));
var __cookies = { get: function (k) { return __cookieList.get(k); }, has: function (k) { return __cookieList.has(k); }, toObject: function () { return __cookieList.toObject(); }, jar: function () { throw new Error('pm.cookies.jar() is not supported in pyhttp; use the Cookies panel'); } };

// --------------------------------------------------------------- pm
var pm = {
  info: Object.assign({ eventName: __S.eventName || 'test', iteration: 0, iterationCount: 1, requestName: '', requestId: '' }, __S.info || {}),
  variables: __variables,
  environment: __scope(__env, 'env'),
  globals: __scope(__globals, 'globals'),
  collectionVariables: __scope(__coll, 'collection'),
  iterationData: Object.assign(__scope(__data, null), { get: function (k) { return __data[k]; } }),
  request: __request,
  response: __response,
  cookies: __cookies,
  expect: function (v, msg) { return new Assertion(v); },
  test: function (name, fn) {
    var passed = true, note = '';
    try { if (typeof fn === 'function') { var r = fn(); if (r && typeof r.then === 'function') { __R.pending = (__R.pending || []).concat([{ name: String(name), promise: r }]); return; } } else if (fn !== undefined && !fn) { passed = false; note = 'condition was false'; } }
    catch (e) { passed = false; note = (e && e.message) ? String(e.message) : String(e); }
    __R.tests.push({ name: String(name), passed: passed, note: note });
  },
  sendRequest: function () { throw new Error('pm.sendRequest is not supported in pyhttp: put the extra request in the collection and use {{prev.json...}} or variables instead'); },
  execution: { setNextRequest: function (n) { __R.nextRequest = n; }, skipRequest: function () { __R.skip = true; } },
  visualizer: { set: function () {} },
  require: function (m) { throw new Error('require(' + JSON.stringify(m) + ') is not available in pyhttp scripts'); },
};
pm.test.skip = function (name) { __R.tests.push({ name: String(name), passed: true, note: 'skipped' }); };
pm.expect.fail = function (msg) { throw new AssertionError(msg || 'expect.fail()'); };
pm.environment.name = __S.environmentName || '';
if (__response) { pm.response.to = __response.to; }

// ------------------------------------------------------------- legacy
var tests = {};
var postman = {
  setEnvironmentVariable: function (k, v) { pm.environment.set(k, v); },
  getEnvironmentVariable: function (k) { return pm.environment.get(k); },
  clearEnvironmentVariable: function (k) { pm.environment.unset(k); },
  setGlobalVariable: function (k, v) { pm.globals.set(k, v); },
  getGlobalVariable: function (k) { return pm.globals.get(k); },
  clearGlobalVariable: function (k) { pm.globals.unset(k); },
  setNextRequest: function (n) { __R.nextRequest = n; },
  getResponseHeader: function (k) { return __response ? __response.headers.get(k) : undefined; },
};
var responseBody = __resp ? (__resp.body || '') : undefined;
var responseCode = __resp ? { code: __resp.code, name: __response.status, detail: '' } : undefined;
var responseTime = __resp ? __resp.time_ms : undefined;
var responseHeaders = __resp ? __response.headers.toObject() : undefined;
var responseCookies = __cookieList.all();
var request = { url: __reqState.url, method: __reqState.method, headers: __headers.toObject(), data: __reqState.body, name: pm.info.requestName };
var environment = __env, globals = __globals, iteration = pm.info.iteration, data = __data;
var chai = { expect: pm.expect, AssertionError: AssertionError };
var expect = pm.expect;

// ------------------------------------------------------------ platform
var __timers = [];
function setTimeout(fn, ms) { var id = ++__R.timers; __timers.push({ id: id, fn: fn, at: Number(ms) || 0, args: Array.prototype.slice.call(arguments, 2) }); return id; }
function clearTimeout(id) { __timers = __timers.filter(function (t) { return t.id !== id; }); }
function setInterval() { throw new Error('setInterval is not supported in pyhttp scripts'); }
function setImmediate(fn) { return setTimeout(fn, 0); }
var __B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=';
function btoa(input) {
  var str = String(input), out = '', i = 0;
  while (i < str.length) {
    var a = str.charCodeAt(i++), b = str.charCodeAt(i++), c = str.charCodeAt(i++);
    if (a > 255 || b > 255 || c > 255) throw new Error('btoa: string contains characters outside Latin1');
    var n = (a << 16) | ((b || 0) << 8) | (c || 0);
    out += __B64[(n >> 18) & 63] + __B64[(n >> 12) & 63] + (isNaN(b) ? '=' : __B64[(n >> 6) & 63]) + (isNaN(c) ? '=' : __B64[n & 63]);
  }
  return out;
}
function atob(input) {
  var str = String(input).replace(/[^A-Za-z0-9+/]/g, ''), out = '', i = 0;
  while (i < str.length) {
    var a = __B64.indexOf(str[i++]), b = __B64.indexOf(str[i++]), c = __B64.indexOf(str[i++]), d = __B64.indexOf(str[i++]);
    var n = (a << 18) | (b << 12) | ((c & 63) << 6) | (d & 63);
    out += String.fromCharCode((n >> 16) & 255);
    if (c !== -1) out += String.fromCharCode((n >> 8) & 255);
    if (d !== -1) out += String.fromCharCode(n & 255);
  }
  return out;
}

// ------------------------------------------------------------- finish
function __formatError(e) {
  if (e instanceof AssertionError) return 'AssertionError: ' + e.message;
  var s = (e && e.stack) ? String(e.stack) : String(e);
  if (e && e.message && s.indexOf(e.message) === -1) s = (e.name || 'Error') + ': ' + e.message + '\n' + s;
  return s;
}
function __runTimers() {
  var guard = 0;
  while (__timers.length && guard++ < 1000) {
    __timers.sort(function (a, b) { return a.at - b.at; });
    var t = __timers.shift();
    try { t.fn.apply(null, t.args); } catch (e) { __R.error = __R.error || __formatError(e); }
  }
}
// async pm.test callbacks (return a promise): settle them before collecting results
function __settle() {
  var pending = __R.pending || [];
  __R.pending = [];
  pending.forEach(function (p) {
    p.promise.then(function () { __R.tests.push({ name: p.name, passed: true, note: '' }); },
                   function (e) { __R.tests.push({ name: p.name, passed: false, note: (e && e.message) ? String(e.message) : String(e) }); });
  });
}
function __result() {
  __runTimers();
  Object.keys(tests).forEach(function (k) { __R.tests.push({ name: k, passed: !!tests[k], note: tests[k] ? '' : 'legacy tests[] entry was false' }); });
  if (__S.eventName === 'prerequest') {
    __R.request = { method: __reqState.method, url: __reqState.url, headers: __headers._pairs(), params: __query._pairs(), body: __reqState.body };
  }
  return JSON.stringify(__R);
}
