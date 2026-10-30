'use strict';
// Outbox.gs: NG Runner's signed messages are sent once each, by channel, and every one gets a receipt.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { load, plain, hexToSigned, outDir, pythonEnvelopes, KEY_HEX } = require('./harness');

const KEY = hexToSigned(KEY_HEX);

function message(over = {}) {
  return { channel: 'email', to: 'barbara', subject: 'Filed as Testerson_070126',
    lines: ['1. Testerson_070126: filed the IC summary.'], urgent: false, ref: 'filed:Testerson_070126', ...over };
}

/** NG Runner drops a signed outbox file, as Drive for desktop would bring it up from Jim's PC. */
function drop(h, payload, { seq, id = crypto.randomBytes(16).toString('hex'), kind = 'outbox', key = KEY, name } = {}) {
  const text = h.gs.sealEnvelope_(kind, payload, { key, seq, issuedAt: h.clock.now(), id });
  h.control.put(`outbox/${name || id}.json`, text);
  return id;
}

function receipt(h, id) {
  return plain(h.gs.openEnvelope_(h.control.text(`receipts/${id}.json`), KEY, 'receipt').payload);
}

test('an email goes to the address set for its role, and a signed receipt comes back', () => {
  const h = load();
  const id = drop(h, message(), { seq: 7 });
  h.gs.processOutbox();
  assert.deepEqual(h.mail.sent, [{ to: 'barbara@example.com', subject: 'Filed as Testerson_070126',
    body: '1. Testerson_070126: filed the IC summary.', name: 'NG Runner' }]);
  assert.deepEqual(receipt(h, id), { outbox_id: id, sent_at: '2026-10-30T13:30:00+00:00', channel: 'email', ok: true, error: null });
  const out = outDir();
  if (out) fs.writeFileSync(path.join(out, 'receipt.json'), h.control.text(`receipts/${id}.json`), 'utf8');
});

test('Pushover gets the title, the lines and high priority only when urgent', () => {
  const h = load();
  drop(h, message({ channel: 'pushover', to: 'jim', subject: 'DO NOT CUT', lines: ['1. a', '2. b'], urgent: true }), { seq: 1 });
  drop(h, message({ channel: 'pushover', to: 'jim', subject: 'NG Runner · Fri 10/30', lines: ['1. c'] }), { seq: 2 });
  h.gs.processOutbox();
  assert.deepEqual(h.fetch.requests.map((r) => [r.url, r.params.method, r.params.muteHttpExceptions, r.params.payload]), [
    ['https://api.pushover.net/1/messages.json', 'post', true,
      { token: 'pushover-app-token', user: 'pushover-user-jim', title: 'DO NOT CUT', message: '1. a\n2. b', priority: '1' }],
    ['https://api.pushover.net/1/messages.json', 'post', true,
      { token: 'pushover-app-token', user: 'pushover-user-jim', title: 'NG Runner · Fri 10/30', message: '1. c', priority: '0' }],
  ]);
});

test('Slack gets a bot message to the role\'s channel, and a refusal comes back as a failed receipt', () => {
  const h = load();
  const sent = drop(h, message({ channel: 'slack', to: 'jim' }), { seq: 1 });
  h.gs.processOutbox();
  const [request] = h.fetch.requests;
  assert.equal(request.url, 'https://slack.com/api/chat.postMessage');
  assert.equal(request.params.headers.Authorization, 'Bearer slack-bot-token');
  assert.equal(request.params.contentType, 'application/json; charset=utf-8');
  assert.deepEqual(JSON.parse(request.params.payload),
    { channel: 'D0JIM', text: 'Filed as Testerson_070126\n1. Testerson_070126: filed the IC summary.' });
  assert.equal(receipt(h, sent).ok, true);
  h.fetch.reply = () => ({ code: 200, body: '{"ok":false,"error":"channel_not_found"}' });
  const refused = drop(h, message({ channel: 'slack', to: 'jim' }), { seq: 2 });
  h.gs.processOutbox();
  assert.deepEqual(receipt(h, refused), { outbox_id: refused, sent_at: '2026-10-30T13:30:00+00:00', channel: 'slack',
    ok: false, error: 'Slack refused the message (channel_not_found)' });
});

test('an id is never sent twice, even when its receipt could not be written at first', () => {
  const h = load();
  const id = drop(h, message(), { seq: 3 });
  const receipts = h.control.folder('receipts', true);
  const create = receipts.createFile.bind(receipts);
  receipts.createFile = () => { throw new Error('Drive is busy'); };
  h.gs.processOutbox();
  assert.equal(h.mail.sent.length, 1);
  assert.deepEqual(h.control.names('receipts'), []);
  receipts.createFile = create;
  h.clock.advance(1);
  h.gs.processOutbox();
  h.gs.processOutbox();
  assert.equal(h.mail.sent.length, 1);
  assert.equal(receipt(h, id).ok, true);
  assert.equal(receipt(h, id).sent_at, '2026-10-30T13:30:00+00:00');   // when it was sent
});

test('while receipts can\'t be written, at most 30 are owed, so the outbox keeps sending', () => {
  const h = load();
  const receipts = h.control.folder('receipts', true);
  receipts.createFile = () => { throw new Error('Drive is busy'); };
  for (let seq = 1; seq <= 35; seq++) drop(h, message({ subject: `m${seq}` }), { seq });
  h.gs.processOutbox();
  assert.equal(h.mail.sent.length, 35);
  assert.ok(h.logs.some(([, text]) => /oldest owed receipt was dropped/.test(text)));
  delete receipts.createFile;
  h.gs.processOutbox();
  assert.equal(h.control.names('receipts').length, 30);
});

test('a run that dies after sending never sends the message again', () => {
  const h = load();
  drop(h, message(), { seq: 3 });
  const receipts = h.control.folder('receipts', true);
  receipts.createFile = () => { throw new Error('Drive is busy'); };
  const set = h.props.setProperty.bind(h.props);
  h.props.setProperty = (k, v) => { if (k === 'state.receipts.owed') throw new Error('quota'); return set(k, v); };
  assert.throws(() => h.gs.processOutbox(), /quota/);
  assert.equal(h.mail.sent.length, 1);
  h.props.setProperty = set;
  h.gs.processOutbox();
  assert.equal(h.mail.sent.length, 1);
});

test('a message waits briefly for an earlier one still on its way, then goes in order', () => {
  const h = load();
  drop(h, message({ subject: 'one' }), { seq: 6 });
  h.gs.processOutbox();
  drop(h, message({ subject: 'three' }), { seq: 8 });
  h.gs.processOutbox();
  h.clock.advance(1);
  h.gs.processOutbox();
  assert.deepEqual(h.mail.sent.map((m) => m.subject), ['one']);
  drop(h, message({ subject: 'two' }), { seq: 7 });
  h.clock.advance(0.5);
  h.gs.processOutbox();
  assert.deepEqual(h.mail.sent.map((m) => m.subject), ['one', 'two', 'three']);
});

test('after two minutes a gap is given up, and the late message is refused, never sent', () => {
  const h = load();
  drop(h, message({ subject: 'one' }), { seq: 6 });
  h.gs.processOutbox();
  drop(h, message({ subject: 'three' }), { seq: 8 });
  h.gs.processOutbox();
  h.clock.advance(1);
  h.gs.processOutbox();
  h.clock.advance(1);
  h.gs.processOutbox();
  assert.deepEqual(h.mail.sent.map((m) => m.subject), ['one', 'three']);
  const late = drop(h, message({ subject: 'two' }), { seq: 7 });
  h.gs.processOutbox();
  assert.deepEqual(h.mail.sent.map((m) => m.subject), ['one', 'three']);
  assert.deepEqual(h.control.names('receipts').includes(`${late}.json`), false);
});

test('foreign, edited, replayed and stray files are never sent, and each is logged once', () => {
  const h = load();
  drop(h, message({ subject: 'first' }), { seq: 5 });
  h.gs.processOutbox();
  const replay = drop(h, message({ subject: 'replayed' }), { seq: 5 });
  drop(h, message({ subject: 'other key' }), { seq: 9, key: hexToSigned('11'.repeat(32)) });
  drop(h, message({ subject: 'wrong kind' }), { seq: 10, kind: 'heartbeat' });
  drop(h, message({ subject: 'renamed' }), { seq: 11, name: 'ab'.repeat(16) });
  h.control.put('outbox/notes.txt', 'not an envelope');
  h.control.put(`outbox/${'cd'.repeat(16)}.json`, '{"body": "{}", "sig": "00"}');
  h.gs.processOutbox();
  h.clock.advance(1);
  h.gs.processOutbox();
  assert.deepEqual(h.mail.sent.map((m) => m.subject), ['first']);
  assert.deepEqual(h.control.names('receipts').length, 1);
  const refusals = h.logs.filter(([level]) => level === 'warn').map(([, text]) => text);
  assert.equal(refusals.length, 5, refusals.join('\n'));
  assert.ok(refusals.some((t) => t.includes(replay) && /sequence number is not above/.test(t)));
});

test('a role with no address, or a message in the wrong form, gets a failed receipt in plain words', () => {
  const h = load();
  const nobody = drop(h, message({ to: 'george' }), { seq: 1 });
  const odd = drop(h, { ...message(), lines: 'one line' }, { seq: 2 });
  const unknown = drop(h, message({ channel: 'fax' }), { seq: 3 });
  h.gs.processOutbox();
  assert.equal(receipt(h, nobody).error, 'no email address is set for george');
  assert.match(receipt(h, odd).error, /^the message was not in the expected form/);
  assert.equal(receipt(h, unknown).channel, 'fax');
  assert.equal(receipt(h, unknown).ok, false);
  assert.equal(h.mail.sent.length, 0);
});

test('a failed send says why without naming an address or a token', () => {
  const h = load();
  h.mail.failure = 'Invalid email: barbara@example.com';
  const id = drop(h, message(), { seq: 1 });
  h.fetch.reply = () => ({ code: 400, body: '{"status":0,"errors":["user key pushover-user-jim is invalid"]}' });
  const push = drop(h, message({ channel: 'pushover', to: 'jim' }), { seq: 2 });
  h.gs.processOutbox();
  assert.deepEqual([receipt(h, id).error, receipt(h, push).error],
    ['the email could not be sent', 'Pushover refused the message (HTTP 400)']);
  const everything = JSON.stringify(h.logs) + JSON.stringify(h.drive.log);
  for (const secret of ['barbara@example.com', 'pushover-user-jim', 'pushover-app-token', KEY_HEX]) {
    assert.ok(!everything.includes(secret), secret);
  }
});

test('an unexpected error while sending still ends in a failed receipt, never a resend', () => {
  const h = load();
  h.mail.getRemainingDailyQuota = () => { throw new Error('Service invoked too many times'); };
  const id = drop(h, message(), { seq: 1 });
  h.gs.processOutbox();
  assert.deepEqual([receipt(h, id).ok, receipt(h, id).error], [false, 'the message could not be sent (an unexpected error)']);
  h.gs.processOutbox();
  assert.equal(h.mail.sent.length, 0);
});

test('when the daily email limit is used up the receipt says so', () => {
  const h = load();
  h.mail.quota = 0;
  const id = drop(h, message(), { seq: 1 });
  h.gs.processOutbox();
  assert.equal(receipt(h, id).error, 'the daily email limit is used up');
});

test('a Pushover message over its 1024-character limit is cut at a whole line', () => {
  const h = load();
  const lines = Array.from({ length: 40 }, (_, i) => `${i + 1}. Testerson_0701${String(i).padStart(2, '0')}: filed the IC summary.`);
  drop(h, message({ channel: 'pushover', to: 'jim', lines }), { seq: 1 });
  h.gs.processOutbox();
  const sent = h.fetch.requests[0].params.payload.message.split('\n');
  assert.ok(sent.join('\n').length <= 1024);
  assert.equal(sent.at(-1), 'The rest is on the board.');
  assert.deepEqual(sent.slice(0, -1), lines.slice(0, sent.length - 1));
});

test('nothing is sent while another run holds the lock', () => {
  const h = load();
  drop(h, message(), { seq: 1 });
  h.lockState.busy = true;
  h.gs.processOutbox();
  assert.equal(h.mail.sent.length, 0);
});

test('an outbox message sealed by NG Runner in Python is sent, and its receipt left for Python', (t) => {
  const python = pythonEnvelopes();
  if (!python) { t.skip('run through pytest (tests/test_fab.py) to get envelopes sealed by Python'); return; }
  const h = load({ props: { SIGNING_KEY: python.key_hex } });
  const sealed = python.envelopes.find((e) => e.kind === 'outbox');
  h.control.put(`outbox/${sealed.id}.json`, sealed.text);
  h.gs.processOutbox();
  assert.deepEqual(h.mail.sent.map((m) => [m.to, m.subject, m.body]),
    [['barbara@example.com', sealed.payload.subject, sealed.payload.lines.join('\n')]]);
  const out = outDir();
  if (out) fs.writeFileSync(path.join(out, 'python-receipt.json'), h.control.text(`receipts/${sealed.id}.json`), 'utf8');
});
