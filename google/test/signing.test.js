'use strict';
// Signing.gs: the Apps Script seals and opens exactly the envelopes ngrunner/signing.py does.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { load, plain, hexToSigned, vector, outDir, pythonEnvelopes, KEY_HEX } = require('./harness');

const KEY = hexToSigned(KEY_HEX);
const refused = (e) => e && e.name === 'EnvelopeError';

/** A body signed with the right key, so only its fields can be wrong. */
function signedBody(body) {
  const text = JSON.stringify(body);
  const sig = crypto.createHmac('sha256', Buffer.from(KEY_HEX, 'hex')).update(text, 'utf8').digest('hex');
  return JSON.stringify({ body: text, sig });
}

const GOOD_BODY = { v: 1, kind: 'heartbeat', id: 'ab'.repeat(16), seq: 1,
  issued_at: '2026-10-30T13:30:00+00:00', payload: {} };

test('the shared test vector is reproduced exactly, byte for byte', () => {
  const { gs } = load();
  const v = vector();
  const sealed = gs.sealEnvelope_(v.kind, v.payload,
    { key: hexToSigned(v.key_hex), seq: v.seq, issuedAt: new Date(v.issued_at), id: v.id });
  assert.deepEqual(plain(JSON.parse(sealed)), { body: v.body, sig: v.sig });
  // The same outer form Python writes: json.dumps({"body": ..., "sig": ...}, ensure_ascii=False).
  assert.equal(sealed, `{"body": ${JSON.stringify(v.body)}, "sig": "${v.sig}"}`);
});

test('the vector file, sealed by Python, opens with its fields', () => {
  const { gs } = load();
  const v = vector();
  const env = gs.openEnvelope_(JSON.stringify({ body: v.body, sig: v.sig }), hexToSigned(v.key_hex), 'fab_export');
  assert.deepEqual(plain({ kind: env.kind, id: env.id, seq: env.seq, payload: env.payload }),
    { kind: v.kind, id: v.id, seq: v.seq, payload: v.payload });
  assert.equal(env.issuedAt.getTime(), Date.parse(v.issued_at));
});

test('canonical JSON matches Python: escapes, raw non-ASCII, keys in code point order', () => {
  const { gs } = load();
  const value = { b: 1, a: ['\x1f\n"\\é \u007f\b\f\r\t', true, null, -7],
    '\u{1F600}k': 'astral', '～k': 'bmp-high', A: {} };
  // json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True), from Python.
  const python = "{\"A\":{},\"a\":[\"\\u001f\\n\\\"\\\\é \u007f\\b\\f\\r\\t\",true,null,-7],\"b\":1,\"～k\":\"bmp-high\",\"😀k\":\"astral\"}";
  assert.equal(gs.pyJson_(value), python);
});

test('a payload Python could not write the same way is refused before it is signed', () => {
  const { gs } = load();
  for (const bad of [{ x: 1.5 }, { x: NaN }, { x: undefined }, { x: 2 ** 60 }, { x: () => 1 }, { x: new Date() }]) {
    assert.throws(() => gs.pyJson_(bad), /whole number|cannot be written|not JSON/);
  }
});

test('an edited, re-signed or foreign file is refused', () => {
  const { gs } = load();
  const v = vector();
  const file = JSON.stringify({ body: v.body, sig: v.sig });
  const cases = [
    [file.replace('\\"seq\\":42', '\\"seq\\":43'), /signature/],
    [file.replace(v.sig, v.sig.replace(/^./, (c) => (c === '0' ? '1' : '0'))), /signature/],
    [file.replace('fab_export', 'fab_edits'), /signature/],
  ];
  for (const [text, why] of cases) assert.throws(() => gs.openEnvelope_(text, KEY), why);
  assert.throws(() => gs.openEnvelope_(file, hexToSigned('11'.repeat(32))), /signature/);
  assert.throws(() => gs.openEnvelope_(file, KEY, 'outbox'), /it is a fab_export file, not a outbox file/);
});

test('anything that is not an envelope is refused', () => {
  const { gs } = load();
  for (const text of ['', 'not json', '[]', '{"body": "{}"}', '{"body": 1, "sig": "00"}',
    '{"body": "{}", "sig": "00", "extra": 1}', ' '.repeat(4 * 1024 * 1024 + 1)]) {
    assert.throws(() => gs.openEnvelope_(text, KEY), refused);
  }
});

test('a signed body with bad fields is still refused', () => {
  const { gs } = load();
  const bad = [{ seq: true }, { seq: 0 }, { seq: '1' }, { seq: 1.5 }, { v: 2 }, { id: 'x' },
    { id: 'AB'.repeat(16) }, { kind: 'Heart beat' }, { payload: [] }, { payload: null },
    { issued_at: '2026-10-30T13:30:00' }, { issued_at: '2026-02-30T13:30:00+00:00' }, { extra: 1 }];
  for (const change of bad) {
    assert.throws(() => gs.openEnvelope_(signedBody({ ...GOOD_BODY, ...change }), KEY), refused,
      JSON.stringify(change));
  }
  assert.equal(gs.openEnvelope_(signedBody(GOOD_BODY), KEY).seq, 1);
});

test('times with fractions and any offset are read as Python writes them', () => {
  const { gs } = load();
  const body = { ...GOOD_BODY, issued_at: '2026-10-30T06:30:00.123456-07:00' };
  assert.equal(gs.openEnvelope_(signedBody(body), KEY).issuedAt.toISOString(), '2026-10-30T13:30:00.123Z');
  assert.equal(gs.isoUtc_(new Date('2026-10-30T13:30:00.999Z')), '2026-10-30T13:30:00+00:00');
});

test('envelopes sealed by NG Runner in Python open here, and edited ones are refused', (t) => {
  const python = pythonEnvelopes();
  if (!python) { t.skip('run through pytest (tests/test_fab.py) to get envelopes sealed by Python'); return; }
  const { gs } = load();
  const key = hexToSigned(python.key_hex);
  for (const sealed of python.envelopes) {
    const env = gs.openEnvelope_(sealed.text, key, sealed.kind);
    assert.deepEqual(plain(env.payload), sealed.payload);
    assert.equal(env.seq, sealed.seq);
    assert.equal(env.id, sealed.id);
    const tampered = sealed.text.replace(`\\"seq\\":${sealed.seq}`, `\\"seq\\":${sealed.seq + 1}`);
    assert.notEqual(tampered, sealed.text);
    assert.throws(() => gs.openEnvelope_(tampered, key), /signature/);
  }
});

test('envelopes sealed here are left for Python to open', () => {
  const { gs } = load();
  const payloads = [
    ['receipt', { outbox_id: 'cd'.repeat(16), sent_at: '2026-10-30T13:31:00+00:00', channel: 'email', ok: true, error: null }],
    ['fab_export', { sheet: 'Sheet1', exported_at: '2026-10-30T13:30:00+00:00',
      rows: [{ sheet_row: 3, job: 'Testerson   closet ', designer: 'PE', install: ['Friday', ' 11 /6'], fab_date: 'café — 3/4"' }] }],
  ];
  const sealed = payloads.map(([kind, payload], i) => ({
    kind, payload, seq: 41 + i, id: String(i).repeat(32),
    text: gs.sealEnvelope_(kind, payload, { key: KEY, seq: 41 + i, issuedAt: new Date('2026-10-30T13:30:00Z'), id: String(i).repeat(32) }),
  }));
  const out = outDir();
  if (out) fs.writeFileSync(path.join(out, 'sealed.json'), JSON.stringify(sealed), 'utf8');
  for (const s of sealed) assert.deepEqual(plain(gs.openEnvelope_(s.text, KEY, s.kind).payload), s.payload);
});
