/**
 * Signed envelopes: the same files ngrunner/signing.py writes and reads (its docstring is the
 * contract, and tests/vectors/envelope_v1.json proves both sides agree).
 *
 * Every file in the control folder goes through here. A file is
 * {"body": "<JSON text>", "sig": "<64 hex: HMAC-SHA256 of the body's UTF-8 bytes>"}; the body
 * holds v, kind, id, seq, issued_at and payload. The signature covers the body string exactly,
 * so the verifier checks those bytes before it parses them and the two languages never have to
 * agree on how to write JSON. This file still writes bodies exactly as Python does (sorted keys,
 * no spaces, non-ASCII as is), so one test vector checks both. The key is 32 bytes, kept as 64
 * hex characters in the Script Property SIGNING_KEY; it is never logged.
 *
 * Replay is the receiver's job: a stream's seq must rise, and an id is acted on once (Outbox.gs,
 * Watchdog.gs). For its own streams this script starts a new one at the current Unix second, so
 * a re-created project still sends sequence numbers above any it sent before.
 */

var ENVELOPE_VERSION = 1;
var ENVELOPE_MAX_BYTES = 4 * 1024 * 1024;   // a larger file is refused unread, as in Python
var ENVELOPE_BODY_KEYS = ['id', 'issued_at', 'kind', 'payload', 'seq', 'v'];
var ENVELOPE_KIND_RE = /^[a-z][a-z0-9_]{0,39}$/;
var ENVELOPE_ID_RE = /^[0-9a-f]{32}$/;
var ISO_TIME_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/;

/** The error every refusal raises; its message says why in plain words. */
function EnvelopeError_(message) {
  var error = new Error(message);
  error.name = 'EnvelopeError';
  return error;
}

// ----- sealing and opening

/** The text of one signed file. options: {key: Byte[], seq, issuedAt: Date, id (optional)}. */
function sealEnvelope_(kind, payload, options) {
  var id = options.id || newId_();
  checkEnvelopeFields_(kind, id, options.seq);
  if (!isPlainObject_(payload)) throw EnvelopeError_('the payload is not an object');
  var body = pyJson_({ v: ENVELOPE_VERSION, kind: kind, id: id, seq: options.seq,
    issued_at: isoUtc_(options.issuedAt), payload: payload });
  return '{"body": ' + JSON.stringify(body) + ', "sig": "' + hmacHex_(options.key, body) + '"}';
}

/** The envelope in text: signature first, then the body's fields. Throws EnvelopeError. */
function openEnvelope_(text, key, kind) {
  if (typeof text !== 'string') throw EnvelopeError_('the file is not text');
  if (utf8Length_(text) > ENVELOPE_MAX_BYTES) throw EnvelopeError_("the file is too large to be one of NG Runner's");
  var outer = parseJson_(text, 'the file');
  if (!isPlainObject_(outer) || !sameKeys_(outer, ['body', 'sig'])
      || typeof outer.body !== 'string' || typeof outer.sig !== 'string') {
    throw EnvelopeError_('the file is not a signed envelope');
  }
  if (!sameText_(hmacHex_(key, outer.body), outer.sig.toLowerCase())) {
    throw EnvelopeError_('the signature does not match');
  }
  return readEnvelopeBody_(parseJson_(outer.body, 'the body'), kind);
}

function readEnvelopeBody_(body, kind) {
  if (!isPlainObject_(body) || !sameKeys_(body, ENVELOPE_BODY_KEYS) || body.v !== ENVELOPE_VERSION) {
    throw EnvelopeError_("the body is not version 1 of NG Runner's envelope");
  }
  if (typeof body.seq !== 'number' || !Number.isSafeInteger(body.seq)) {
    throw EnvelopeError_('its sequence number is not a whole number');
  }
  if (typeof body.kind !== 'string' || typeof body.id !== 'string') throw EnvelopeError_('its kind or id is not text');
  checkEnvelopeFields_(body.kind, body.id, body.seq);
  if (kind && body.kind !== kind) throw EnvelopeError_('it is a ' + body.kind + ' file, not a ' + kind + ' file');
  if (!isPlainObject_(body.payload)) throw EnvelopeError_('its payload is not an object');
  var issuedAt = parseIso_(body.issued_at);
  if (!issuedAt) throw EnvelopeError_('its issue time is not a full time with an offset');
  return { kind: body.kind, id: body.id, seq: body.seq, issuedAt: issuedAt, payload: body.payload };
}

function checkEnvelopeFields_(kind, id, seq) {
  if (typeof kind !== 'string' || !ENVELOPE_KIND_RE.test(kind)) throw EnvelopeError_(JSON.stringify(kind) + ' is not a valid kind');
  if (typeof id !== 'string' || !ENVELOPE_ID_RE.test(id)) throw EnvelopeError_('its id is not 32 hex characters');
  if (typeof seq !== 'number' || !Number.isSafeInteger(seq) || seq < 1) throw EnvelopeError_('its sequence number is below 1');
}

// ----- files in the control folder

/** The signing key from SIGNING_KEY, as bytes. Never logged, never in an error message. */
function signingKey_() {
  var hex = String(setting_('SIGNING_KEY', '')).toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(hex)) {
    throw EnvelopeError_('the Script Property SIGNING_KEY is not set to the 64-hex-character signing key');
  }
  return hexToBytes_(hex);
}

/** The envelope in a control-folder file, checked; a file too large is refused unread. */
function readSignedFile_(file, kind) {
  if (file.getSize() > ENVELOPE_MAX_BYTES) throw EnvelopeError_("the file is too large to be one of NG Runner's");
  return openEnvelope_(file.getBlob().getDataAsString('UTF-8'), signingKey_(), kind);
}

/**
 * Seals payload with the next sequence number of its kind and writes it as folder/name.
 * mode 'replace': the script's own file is replaced in place (other copies of it, which only a
 * sync conflict could make, are trashed). mode 'new': the file must not exist yet.
 */
function writeSignedFile_(folder, name, kind, payload, mode) {
  var existing = filesNamed_(folder, name);
  if (mode === 'new' && existing.length) throw new Error(name + ' already exists, so it was not written again');
  var envelope = { kind: kind, id: newId_(), seq: nextSeq_(kind), issuedAt: now_() };
  var text = sealEnvelope_(kind, payload, { key: signingKey_(), seq: envelope.seq, issuedAt: envelope.issuedAt, id: envelope.id });
  if (existing.length) {
    existing.sort(function (a, b) { return b.getLastUpdated().getTime() - a.getLastUpdated().getTime(); });
    existing[0].setContent(text);
    existing.slice(1).forEach(function (copy) { copy.setTrashed(true); });
  } else {
    folder.createFile(name, text, 'application/json');
  }
  return envelope;
}

// ----- sequences: one per kind for this script's files, and the last accepted per stream it reads

function nextSeq_(kind) {
  var name = 'seq.out.' + kind;
  var last = state_(name);
  var next = typeof last === 'number' && last > 0 ? last + 1 : Math.floor(now_().getTime() / 1000);
  setState_(name, next);
  return next;
}

function lastSeqIn_(kind) {
  var last = state_('seq.in.' + kind);
  return typeof last === 'number' ? last : 0;
}

function acceptSeqIn_(kind, seq) {
  setState_('seq.in.' + kind, seq);
}

// ----- JSON exactly as Python writes it (json.dumps sort_keys, no spaces, ensure_ascii=False)

function pyJson_(value) {
  if (value === null) return 'null';
  if (value === true) return 'true';
  if (value === false) return 'false';
  if (typeof value === 'string') return JSON.stringify(value);   // the same escapes as Python's
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) throw EnvelopeError_('a number in the payload is not a whole number both sides write the same way');
    return String(value);
  }
  if (Array.isArray(value)) return '[' + value.map(pyJson_).join(',') + ']';
  if (isPlainObject_(value)) {
    return '{' + Object.keys(value).sort(compareCodePoints_).map(function (key) {
      if (value[key] === undefined) throw EnvelopeError_('a value in the payload cannot be written as JSON');
      return JSON.stringify(key) + ':' + pyJson_(value[key]);
    }).join(',') + '}';
  }
  throw EnvelopeError_('a value in the payload cannot be written as JSON');
}

/** Python sorts keys by code point; JavaScript's own sort goes by UTF-16 unit, which differs. */
function compareCodePoints_(a, b) {
  var i = 0;
  var j = 0;
  while (i < a.length && j < b.length) {
    var ca = a.codePointAt(i);
    var cb = b.codePointAt(j);
    if (ca !== cb) return ca < cb ? -1 : 1;
    i += ca > 0xffff ? 2 : 1;
    j += cb > 0xffff ? 2 : 1;
  }
  if (i < a.length) return 1;
  if (j < b.length) return -1;
  return 0;
}

function parseJson_(text, what) {
  try {
    return JSON.parse(text);
  } catch (err) {
    throw EnvelopeError_(what + ' is not readable JSON');
  }
}

function isPlainObject_(value) {
  return Object.prototype.toString.call(value) === '[object Object]';
}

function sameKeys_(object, keys) {
  return Object.keys(object).sort().join('\n') === keys.slice().sort().join('\n');
}

// ----- bytes, HMAC and times

function hmacHex_(key, text) {
  if (!Array.isArray(key) || key.length !== 32) throw EnvelopeError_('the signing key is not 32 bytes');
  return bytesToHex_(Utilities.computeHmacSha256Signature(utf8Bytes_(text), key));
}

/** Constant-time comparison of two hex strings, so a wrong signature reveals nothing by timing. */
function sameText_(a, b) {
  if (a.length !== b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** text's UTF-8 bytes, as Apps Script's signed Byte[]; half a character is refused, as Python refuses it. */
function utf8Bytes_(text) {
  var out = [];
  for (var i = 0; i < text.length; i++) {
    var c = text.codePointAt(i);
    if (c > 0xffff) i++;
    else if (c >= 0xd800 && c <= 0xdfff) throw EnvelopeError_('the text holds half of a character');
    if (c < 0x80) out.push(c);
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
  }
  return out.map(function (b) { return b > 127 ? b - 256 : b; });
}

function utf8Length_(text) {
  var n = 0;
  for (var i = 0; i < text.length; i++) {
    var c = text.charCodeAt(i);
    if (c < 0x80) n += 1;
    else if (c < 0x800) n += 2;
    else if (c >= 0xd800 && c <= 0xdbff) { n += 4; i++; }
    else n += 3;
  }
  return n;
}

function hexToBytes_(hex) {
  var out = [];
  for (var i = 0; i < hex.length; i += 2) {
    var b = parseInt(hex.substr(i, 2), 16);
    out.push(b > 127 ? b - 256 : b);
  }
  return out;
}

function bytesToHex_(bytes) {
  return bytes.map(function (b) { return ((b & 0xff) < 16 ? '0' : '') + (b & 0xff).toString(16); }).join('');
}

function newId_() {
  var id = Utilities.getUuid().replace(/-/g, '').toLowerCase();
  if (!ENVELOPE_ID_RE.test(id)) throw new Error('Utilities.getUuid gave an unexpected id');
  return id;
}

/** How Python's to_iso() writes a time: UTC, whole seconds, +00:00. */
function isoUtc_(date) {
  function two(n) { return (n < 10 ? '0' : '') + n; }
  return date.getUTCFullYear() + '-' + two(date.getUTCMonth() + 1) + '-' + two(date.getUTCDate())
    + 'T' + two(date.getUTCHours()) + ':' + two(date.getUTCMinutes()) + ':' + two(date.getUTCSeconds()) + '+00:00';
}

/** A full ISO time with an offset (fractions allowed) as a Date, or null; no offset means null. */
function parseIso_(text) {
  if (typeof text !== 'string') return null;
  var m = ISO_TIME_RE.exec(text);
  if (!m) return null;
  var y = Number(m[1]);
  var mo = Number(m[2]);
  var d = Number(m[3]);
  var h = Number(m[4]);
  var mi = Number(m[5]);
  var s = Number(m[6]);
  if (h > 23 || mi > 59 || s > 59) return null;
  var ms = m[7] ? Math.floor(Number('0.' + m[7]) * 1000) : 0;
  var wall = Date.UTC(y, mo - 1, d, h, mi, s, ms);
  var check = new Date(wall);
  if (check.getUTCFullYear() !== y || check.getUTCMonth() !== mo - 1 || check.getUTCDate() !== d) return null;
  var offset = 0;
  if (m[8] !== 'Z') {
    var oh = Number(m[8].slice(1, 3));
    var om = Number(m[8].slice(4, 6));
    if (oh > 23 || om > 59) return null;
    offset = (m[8][0] === '-' ? -1 : 1) * (oh * 60 + om);
  }
  return new Date(wall - offset * 60000);
}
