'use strict';
// Prune.gs: old control-folder files go to Drive's trash on schedule; nothing else is touched.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { load, hexToSigned, KEY_HEX } = require('./harness');

const DAY = 24 * 60;
const hex = () => crypto.randomBytes(16).toString('hex');

test('outbox, receipt, log and backup files older than 30 days go to the trash; newer ones stay', () => {
  const h = load();
  const old = [`outbox/${hex()}.json`, `receipts/${hex()}.json`, 'logs/20260928.txt', 'backups/20260928.ngrbak'];
  const kept = [`outbox/${hex()}.json`, `receipts/${hex()}.json`, 'logs/20261002.txt', 'backups/20261002.ngrbak'];
  old.forEach((p) => h.control.put(p, 'x', { createdMinutesAgo: 31 * DAY }));
  kept.forEach((p) => h.control.put(p, 'x', { createdMinutesAgo: 29 * DAY }));
  h.gs.pruneControlFolder();
  for (const p of old) assert.deepEqual([h.control.files(p).length, h.control.live(p).length], [1, 0], p);   // trashed, not deleted
  for (const p of kept) assert.equal(h.control.live(p).length, 1, p);
});

test('heartbeats older than 2 days go, and so does each old day folder they leave empty', () => {
  const h = load({ now: '2026-10-30T20:00:00Z' });              // 10/30 in Pacific time
  h.control.put('heartbeat/20261027/1.json', 'x', { createdMinutesAgo: 3 * DAY });
  h.control.put('heartbeat/20261028/2.json', 'x', { createdMinutesAgo: 2 * DAY + 60 });
  h.control.put('heartbeat/20261028/3.json', 'x', { createdMinutesAgo: 2 * DAY - 60 });
  h.control.put('heartbeat/20261030/4.json', 'x', { createdMinutesAgo: 5 });
  h.control.folder('heartbeat/20261029', true);                 // just made by NG Runner, still empty
  h.gs.pruneControlFolder();
  assert.deepEqual(h.control.names('heartbeat'), ['20261028', '20261029', '20261030']);
  assert.deepEqual(h.control.names('heartbeat/20261028'), ['3.json']);
  assert.deepEqual(h.control.names('heartbeat/20261030'), ['4.json']);
});

test('fab, pause and anything it does not recognise are never touched, however old', () => {
  const h = load();
  const untouched = ['fab/export.json', 'fab/edits.json', `pause/${hex()}.json`, 'outbox/notes.txt',
    'logs/yesterday.txt', 'backups/20261001.zip', 'heartbeat/latest.json', 'heartbeat/old/1.json', 'readme.txt'];
  untouched.forEach((p) => h.control.put(p, 'x', { createdMinutesAgo: 400 * DAY }));
  h.gs.pruneControlFolder();
  for (const p of untouched) assert.equal(h.control.live(p).length, 1, p);
  assert.deepEqual(h.drive.log.filter((line) => line.startsWith('trash')), []);
  assert.ok(h.logs.some(([, text]) => /left alone 5 items it does not recognise/.test(text)), JSON.stringify(h.logs));
});

test('a message pruned long ago and dropped back in is still never sent again', () => {
  const h = load();
  const KEY = hexToSigned(KEY_HEX);
  const id = hex();
  const payload = { channel: 'email', to: 'barbara', subject: 'Filed as Testerson_070126', lines: ['1. x'], urgent: false, ref: 'x' };
  const text = h.gs.sealEnvelope_('outbox', payload, { key: KEY, seq: 5, issuedAt: h.clock.now(), id });
  h.control.put(`outbox/${id}.json`, text);
  h.gs.processOutbox();
  h.clock.advance(40 * DAY);
  h.gs.pruneControlFolder();
  assert.deepEqual(h.props.getKeys().filter((k) => /^(done|refused)\./.test(k)), []);   // the marks do not pile up
  h.control.put(`outbox/${id}.json`, text);
  h.gs.processOutbox();
  assert.equal(h.mail.sent.length, 1);
});

test('pruning never waits for the other runs, nor holds them up', () => {
  const h = load();
  const old = h.control.put(`outbox/${hex()}.json`, 'x', { createdMinutesAgo: 31 * DAY });
  h.lockState.busy = true;
  h.gs.pruneControlFolder();
  assert.equal(old.isTrashed(), true);
  assert.equal(h.lockState.held, false);
});

test('a long prune stops after 4 minutes and leaves the rest for the next day', () => {
  const h = load();
  const files = Array.from({ length: 300 }, () => h.control.put(`receipts/${hex()}.json`, 'x', { createdMinutesAgo: 31 * DAY }));
  const trash = Object.getPrototypeOf(Object.getPrototypeOf(files[0])).setTrashed;
  for (const f of files) f.setTrashed = function (v) { h.clock.advance(1 / 60); return trash.call(this, v); };   // 1 s each
  h.gs.pruneControlFolder();
  assert.equal(files.filter((f) => f.isTrashed()).length, 240);
  assert.ok(h.logs.some(([, text]) => /stopped after 4 minutes/.test(text)));
});
