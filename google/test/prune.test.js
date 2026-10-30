'use strict';
// Prune.gs: old control-folder files go to Drive's trash on schedule; nothing else is touched.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { load, hexToSigned, pythonEnvelopes, KEY_HEX } = require('./harness');

const DAY = 24 * 60;
const hex = () => crypto.randomBytes(16).toString('hex');

test('outbox, receipt, log, backup and support files over 30 days old go to the trash', () => {
  const h = load();
  const old = [`outbox/${hex()}.json`, `receipts/${hex()}.json`, 'logs/20260928.txt',
    'backups/20260928.ngrbak', 'support/support-20260928-153012.zip'];
  const kept = [`outbox/${hex()}.json`, `receipts/${hex()}.json`, 'logs/20261002.txt',
    'backups/20261002.ngrbak', 'support/support-20261002-080000.zip'];
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
  const job = 'shadow/Testerson_070126';
  const untouched = ['fab/export.json', 'fab/edits.json', `pause/${hex()}.json`,
    'outbox/notes.txt', 'logs/yesterday.txt', 'backups/20261001.zip', 'heartbeat/latest.json',
    'heartbeat/old/1.json', 'readme.txt',
    'support/notes.txt', 'support/support-2026.zip', 'support/old/support-20260928-153012.zip',
    'shadow/readme.txt', `${job}/notes.txt`, `${job}/Fakeley_070826_summary.pdf`,
    `${job}/drawings/page1.pdf`];
  untouched.forEach((p) => h.control.put(p, 'x', { createdMinutesAgo: 400 * DAY }));
  h.clock.advance(31 * DAY);                                    // the folders are that old too
  h.gs.pruneControlFolder();
  for (const p of untouched) assert.equal(h.control.live(p).length, 1, p);
  assert.deepEqual(h.drive.log.filter((line) => line.startsWith('trash')), []);
  assert.ok(h.logs.some(([, text]) => /left alone 12 items it does not recognise/.test(text)),
    JSON.stringify(h.logs));
});

// ----- shadow/<folder>/: the files of the shadow run, which hold client PDFs

const jobFiles = (folder) => [`${folder}_NGRunner.json`, `${folder}_NGRunner_log.txt`,
  `${folder}_summary.pdf`];
const fileJob = (h, folder) => jobFiles(folder)
  .map((name) => h.control.put(`shadow/${folder}/${name}`, 'x'));
const trashedFolders = (h) => h.drive.log.filter((line) => /^trash .*\/shadow\/[^/]+$/.test(line));

test('shadow files over 30 days old go to the trash, with each folder they leave empty', () => {
  const h = load();
  const old = fileJob(h, 'Testerson_070126');
  h.clock.advance(2 * DAY);
  const newer = fileJob(h, 'Fakeley_070826_Gar');
  h.clock.advance(29 * DAY);                                    // 31 days and 29 days old
  h.gs.pruneControlFolder();
  assert.deepEqual(old.map((f) => f.isTrashed()), [true, true, true]);   // trashed, not deleted
  assert.deepEqual(newer.map((f) => f.isTrashed()), [false, false, false]);
  assert.deepEqual(h.control.names('shadow'), ['Fakeley_070826_Gar']);
  assert.deepEqual(h.control.names('shadow/Fakeley_070826_Gar'), jobFiles('Fakeley_070826_Gar'));
  assert.ok(h.logs.some(([, text]) => /pruned 3 files to the trash$/.test(text)),
    JSON.stringify(h.logs));
});

test('a job folder is judged file by file, and stays until the last one has gone', () => {
  const h = load();
  const summary = h.control.put('shadow/Testerson_070126/Testerson_070126_summary.pdf', 'x');
  h.clock.advance(20 * DAY);
  const notes = h.control.put('shadow/Testerson_070126/Testerson_070126_NGRunner.json', 'x');
  h.clock.advance(11 * DAY);
  h.gs.pruneControlFolder();
  assert.deepEqual([summary.isTrashed(), notes.isTrashed()], [true, false]);
  assert.deepEqual(h.control.names('shadow'), ['Testerson_070126']);
  h.clock.advance(20 * DAY);
  h.gs.pruneControlFolder();
  assert.equal(notes.isTrashed(), true);
  assert.deepEqual(h.control.names('shadow'), []);
});

test('a temp file a crash left in a job folder goes after 30 days too: it can hold the PDF', () => {
  const h = load();
  const folder = 'shadow/Testerson_070126';
  const left = h.control.put(`${folder}/Testerson_070126_summary.pdf.1a2b3c4d.ngr-tmp`, 'x');
  const odd = h.control.put(`${folder}/Testerson_070126_summary.pdf.notes.ngr-tmp`, 'x');
  h.clock.advance(29 * DAY);
  h.gs.pruneControlFolder();
  assert.equal(left.isTrashed(), false);
  h.clock.advance(2 * DAY);
  h.gs.pruneControlFolder();
  assert.deepEqual([left.isTrashed(), odd.isTrashed()], [true, false]);
  assert.deepEqual(h.control.names('shadow'), ['Testerson_070126']);
});

test('a job folder name is matched as it is written, whatever characters it holds', () => {
  const h = load();
  const folder = 'Fakeley (1).x+_070126';
  const own = h.control.put(`shadow/${folder}/${folder}_summary.pdf`, 'x');
  const lookalike = h.control.put(`shadow/${folder}/Fakeley (1)Yx+_070126_summary.pdf`, 'x');
  h.clock.advance(31 * DAY);
  h.gs.pruneControlFolder();
  assert.deepEqual([own.isTrashed(), lookalike.isTrashed()], [true, false]);
});

test('a job folder just made, still empty, stays; one empty for over 30 days goes', () => {
  const h = load();
  h.control.folder('shadow/Testerson_070126', true);
  h.clock.advance(31 * DAY);
  h.control.folder('shadow/Fakeley_070826', true);
  h.gs.pruneControlFolder();
  assert.deepEqual(h.control.names('shadow'), ['Fakeley_070826']);
  assert.equal(trashedFolders(h).length, 1, JSON.stringify(h.drive.log));
});

test('pruning that stops for time leaves the job folder, and the next run finishes it', () => {
  const h = load();
  const files = fileJob(h, 'Testerson_070126');
  const trash = Object.getPrototypeOf(Object.getPrototypeOf(files[0])).setTrashed;
  for (const f of files) {
    f.setTrashed = function (v) { h.clock.advance(5); return trash.call(this, v); };   // 5 min each
  }
  h.clock.advance(31 * DAY);
  h.gs.pruneControlFolder();
  assert.deepEqual(files.map((f) => f.isTrashed()), [true, false, false]);
  assert.deepEqual(h.control.names('shadow'), ['Testerson_070126']);
  h.clock.advance(DAY);
  h.gs.pruneControlFolder();
  assert.deepEqual(files.map((f) => f.isTrashed()), [true, true, false]);
  h.clock.advance(DAY);
  h.gs.pruneControlFolder();
  assert.deepEqual(files.map((f) => f.isTrashed()), [true, true, true]);
  assert.deepEqual(h.control.names('shadow'), ['Testerson_070126']);   // out of time here too
  h.clock.advance(DAY);
  h.gs.pruneControlFolder();
  assert.deepEqual(h.control.names('shadow'), []);
});

test('the file names NG Runner makes in Python are the ones pruned', (t) => {
  const python = pythonEnvelopes();
  if (!python) {
    t.skip('run through pytest (tests/test_fab.py) to get the names Python makes');
    return;
  }
  const h = load();
  const folder = 'Testerson_070126';
  const paths = [`support/${python.names.support}`,
    ...python.names.job_suffixes.map((suffix) => `shadow/${folder}/${folder}${suffix}`),
    `shadow/${folder}/${folder}_summary.pdf.1a2b3c4d${python.names.temp_suffix}`];
  const files = paths.map((p) => h.control.put(p, 'x'));
  h.clock.advance(31 * DAY);
  h.gs.pruneControlFolder();
  assert.deepEqual(files.map((f) => f.isTrashed()), files.map(() => true), paths.join('\n'));
  assert.deepEqual(h.control.names('shadow'), []);
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
