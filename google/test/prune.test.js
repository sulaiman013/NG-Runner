'use strict';
// Prune.gs: old control-folder files go to Drive's trash on schedule; nothing else is touched.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { load, plain, hexToSigned, pythonEnvelopes, KEY_HEX, GOOGLE_DIR } = require('./harness');

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

// ----- Google may refuse: "Only the owner may trash the File" (README.md, Setup step 6)

const DENIED = 'Exception: Access denied: DriveApp.';   // not verified on Google; nothing relies on these words
const refuseWhen = (test) => (item) => (test(item) ? DENIED : null);
const refusedState = (h) => JSON.parse(h.props.getProperty('state.prune.refused'));
const marks = (h) => h.props.getKeys().filter((k) => /^(done|refused)\./.test(k));
const putOld = (h, p) => h.control.put(p, 'x', { createdMinutesAgo: 31 * DAY });
const slowTrash = (h, file, minutes) => {
  const trash = Object.getPrototypeOf(Object.getPrototypeOf(file)).setTrashed;
  file.setTrashed = function (v) { h.clock.advance(minutes); return trash.call(this, v); };
};

test('a file Google refuses to trash stays and is counted, and the rest of the run carries on', () => {
  const h = load();
  const refused = [putOld(h, `outbox/${hex()}.json`), putOld(h, `receipts/${hex()}.json`)];   // first in their folders
  const others = [putOld(h, `outbox/${hex()}.json`), putOld(h, `receipts/${hex()}.json`), putOld(h, 'logs/20260928.txt'),
    putOld(h, 'shadow/Testerson_070126/Testerson_070126_summary.pdf'), putOld(h, 'heartbeat/20260928/1.json')];
  h.drive.refuseTrash = refuseWhen((item) => refused.includes(item));
  const report = h.gs.pruneControlFolder();
  assert.deepEqual(refused.map((f) => f.isTrashed()), [false, false]);
  assert.deepEqual(others.map((f) => f.isTrashed()), others.map(() => true));
  assert.equal(report.trashed, 5);
  assert.deepEqual(plain(report.refused), { outbox: 1, receipts: 1 });
  assert.ok(h.logs.some(([, text]) => /pruned 5 files to the trash$/.test(text)), JSON.stringify(h.logs));
  assert.ok(h.logs.some(([level, text]) => level === 'warn'
    && /Google refused to trash 2 old items \(outbox 1, receipts 1\).*Access denied/.test(text)), JSON.stringify(h.logs));
  assert.deepEqual(refusedState(h), { count: 2, at: h.clock.now().getTime(), where: { outbox: 1, receipts: 1 } });
});

test('a folder Google refuses to trash stays too; one a refused file keeps full is never tried', () => {
  const h = load();
  h.control.folder('shadow/Testerson_070126', true);            // old and empty: tried, and refused
  h.control.folder('heartbeat/20261001', true);
  const kept = h.control.put('shadow/Fakeley_070826/Fakeley_070826_summary.pdf', 'x');
  h.clock.advance(31 * DAY);
  h.drive.refuseTrash = refuseWhen((item) => item === kept || ['Testerson_070126', '20261001'].includes(item.name));
  const report = h.gs.pruneControlFolder();
  assert.deepEqual(plain(report.refused), { shadow: 2, heartbeat: 1 });
  assert.equal(report.trashed, 0);
  assert.deepEqual(h.control.names('shadow'), ['Fakeley_070826', 'Testerson_070126']);
  assert.deepEqual(h.control.names('heartbeat'), ['20261001']);
});

test('the file Google refuses to trash is tried again on the next run', () => {
  const h = load();
  const file = putOld(h, 'logs/20260928.txt');
  h.drive.refuseTrash = refuseWhen(() => true);
  h.gs.pruneControlFolder();
  assert.equal(file.isTrashed(), false);
  h.drive.refuseTrash = null;
  h.clock.advance(DAY);
  h.gs.pruneControlFolder();
  assert.equal(file.isTrashed(), true);
  assert.equal(h.props.getProperty('state.prune.refused'), null);
});

test('a message whose file Google will not trash still loses its marks, and is never sent again', () => {
  const h = load();
  const id = hex();
  const payload = { channel: 'email', to: 'barbara', subject: 'Filed as Testerson_070126', lines: ['1. x'], urgent: false, ref: 'x' };
  const text = h.gs.sealEnvelope_('outbox', payload, { key: hexToSigned(KEY_HEX), seq: 5, issuedAt: h.clock.now(), id });
  const file = h.control.put(`outbox/${id}.json`, text);
  h.drive.refuseTrash = refuseWhen(() => true);
  h.gs.processOutbox();
  assert.equal(h.mail.sent.length, 1);
  for (let round = 0; round < 2; round++) {
    h.clock.advance(40 * DAY);
    h.gs.pruneControlFolder();
    assert.equal(file.isTrashed(), false);
    assert.deepEqual(marks(h), []);                             // dropped on time, file or no file
    h.gs.processOutbox();
    assert.equal(h.mail.sent.length, 1);                        // refused by its sequence number
    assert.deepEqual(marks(h), [`refused.${id}`]);
  }
  assert.equal(h.control.files(`receipts/${id}.json`).length, 1);
});

test('the marks go first, so a run that fails in the files has still dropped them', () => {
  const h = load();
  h.props.setProperty(`done.${hex()}`, String(h.clock.now().getTime()));
  h.control.folder('outbox', true).getFiles = () => { throw new Error('Exception: Service error: Drive'); };
  h.clock.advance(32 * DAY);
  assert.throws(() => h.gs.pruneControlFolder(), /Service error/);
  assert.deepEqual(marks(h), []);
});

test('the setup check says what Google refused in plain words, and the triggers are not held up', () => {
  const h = load();
  const files = [putOld(h, 'logs/20260928.txt'), putOld(h, 'logs/20260929.txt'),
    putOld(h, 'shadow/Testerson_070126/Testerson_070126_summary.pdf')];
  h.drive.refuseTrash = refuseWhen(() => true);
  h.gs.pruneControlFolder();
  const line = 'pruning on Fri 10/30 6:30 AM: Google refused to trash 3 old items (logs 2, shadow 1),'
    + ' and what it refuses stays in the control folder (see README.md, Setup step 6)';
  assert.deepEqual(Array.from(h.gs.checkSetup()), [line]);
  assert.ok(h.logs.some(([level, text]) => level === 'warn' && text.endsWith(`setup: ${line}`)), JSON.stringify(h.logs));
  assert.equal(h.gs.installTriggers(), 5);
  h.drive.refuseTrash = null;                                   // a shared drive, say: the next run clears it
  h.gs.pruneControlFolder();
  assert.deepEqual(files.map((f) => f.isTrashed()), [true, true, true]);
  assert.equal(h.props.getProperty('state.prune.refused'), null);
  assert.deepEqual(Array.from(h.gs.checkSetup()), []);
});

test('the setup check counts one refusal as one, and a real setup problem still comes first', () => {
  const h = load({ props: { EXPECTED_ON_FROM: '6am' } });
  putOld(h, 'logs/20260928.txt');
  h.drive.refuseTrash = refuseWhen(() => true);
  h.gs.pruneControlFolder();
  const problems = Array.from(h.gs.checkSetup());
  assert.equal(problems[0], 'EXPECTED_ON_FROM is not a time like 06:00');
  assert.match(problems[1], /: Google refused to trash 1 old item \(logs 1\), and/);
  assert.throws(() => h.gs.installTriggers(), /fix the setup first: EXPECTED_ON_FROM is not a time like 06:00$/);
});

test("Google's own words reach the log with any address out, and never the setup check or the count", () => {
  const h = load();
  putOld(h, 'shadow/Testerson_070126/Testerson_070126_summary.pdf');
  h.drive.refuseTrash = () => 'Exception: only owner-person@example.com may trash this';
  h.gs.pruneControlFolder();
  const problems = JSON.stringify(Array.from(h.gs.checkSetup()));
  const stored = h.props.getProperty('state.prune.refused');
  assert.ok(h.logs.some(([, text]) => text.includes('only <address> may trash this')), JSON.stringify(h.logs));
  assert.ok(!JSON.stringify(h.logs).includes('example.com'));
  for (const said of [problems, stored]) {
    assert.ok(!said.includes('may trash this'), said);
    assert.ok(!said.includes('Testerson'), said);               // no job name in the count or the setup line
  }
});

test('a run out of time can raise the count but not lower it, since it did not reach everything', () => {
  const h = load();
  putOld(h, 'logs/20260928.txt');
  putOld(h, 'logs/20260929.txt');
  h.drive.refuseTrash = refuseWhen((item) => /\/logs\//.test(item.path()));
  h.gs.pruneControlFolder();
  const first = refusedState(h);
  assert.equal(first.count, 2);

  slowTrash(h, putOld(h, `receipts/${hex()}.json`), 5);         // receipts come before logs
  h.clock.advance(DAY);
  assert.equal(h.gs.pruneControlFolder().stopped, true);
  assert.deepEqual(refusedState(h), first);

  putOld(h, 'logs/20260930.txt');
  slowTrash(h, putOld(h, 'backups/20260928.ngrbak'), 5);        // backups come after logs
  h.clock.advance(DAY);
  const raised = h.gs.pruneControlFolder();
  assert.equal(raised.stopped, true);
  assert.equal(refusedState(h).count, 3);
  assert.deepEqual(refusedState(h).where, { logs: 3 });

  h.clock.advance(DAY);                                         // a full run says what is true now
  h.drive.refuseTrash = null;
  assert.equal(h.gs.pruneControlFolder().stopped, false);
  assert.equal(h.props.getProperty('state.prune.refused'), null);
});

test('refusals use up the time too; the run still ends with its log, its count and the marks gone', () => {
  const h = load();
  const files = Array.from({ length: 300 }, () => putOld(h, `receipts/${hex()}.json`));
  h.props.setProperty(`done.${hex()}`, String(h.clock.now().getTime() - 40 * DAY * 60000));
  h.drive.refuseTrash = () => { h.clock.advance(1 / 60); return DENIED; };   // 1 s each, and every one refused
  const report = h.gs.pruneControlFolder();
  assert.equal(report.stopped, true);
  assert.deepEqual(plain(report.refused), { receipts: 240 });
  assert.equal(files.filter((f) => f.isTrashed()).length, 0);
  assert.equal(refusedState(h).count, 240);
  assert.deepEqual(marks(h), []);
  assert.ok(h.logs.some(([, text]) => /stopped after 4 minutes/.test(text)));
  assert.ok(h.logs.some(([, text]) => /Google refused to trash 240 old items/.test(text)));
});

test('out of time, it stops walking the folders, so Google does not end the run before it logs', () => {
  const h = load();
  const first = fileJob(h, 'Testerson_070126');
  fileJob(h, 'Fakeley_070826');
  h.control.put('heartbeat/20261030/1.json', 'x');
  const unwalked = () => assert.fail('a folder was listed after the time ran out');
  h.control.folder('shadow/Fakeley_070826').getFiles = unwalked;
  h.control.folder('heartbeat').getFolders = unwalked;
  slowTrash(h, first[0], 5);
  h.clock.advance(31 * DAY);
  const report = h.gs.pruneControlFolder();
  assert.equal(report.stopped, true);
  assert.ok(h.logs.some(([, text]) => /stopped after 4 minutes/.test(text)), JSON.stringify(h.logs));
});

test('a heartbeat day folder is not walked once the time has run out on an earlier one', () => {
  const h = load({ now: '2026-10-30T20:00:00Z' });
  const old = h.control.put('heartbeat/20261020/1.json', 'x', { createdMinutesAgo: 10 * DAY });
  h.control.put('heartbeat/20261021/2.json', 'x', { createdMinutesAgo: 9 * DAY });
  h.control.folder('heartbeat/20261021').getFiles = () => assert.fail('a day folder was listed after the time ran out');
  slowTrash(h, old, 5);
  const report = h.gs.pruneControlFolder();
  assert.equal(report.stopped, true);
  assert.equal(old.isTrashed(), true);
});

test("the README's two-account check makes pruning's own call, and says what to do if refused", () => {
  const readme = fs.readFileSync(path.join(GOOGLE_DIR, 'README.md'), 'utf8');
  const step = /^6\. [\s\S]*?(?=^## )/m.exec(readme)[0];
  assert.match(step, /Only the owner may trash the File/);
  assert.match(step, /shared drive/);
  assert.match(step, /Workspace/);
  assert.match(step, /raise it with Sulaiman/);
  const snippet = /```js\s+(function tryTrash[\s\S]*?)```/.exec(step)[1];
  const h = load();
  const file = h.control.put('trash-check.txt', 'x');
  vm.runInContext(snippet, h.gs);
  h.drive.refuseTrash = refuseWhen(() => true);
  assert.throws(() => h.gs.tryTrash(), /Access denied/);
  assert.equal(file.isTrashed(), false);
  h.drive.refuseTrash = null;
  h.gs.tryTrash();
  assert.equal(file.isTrashed(), true);
  assert.equal(load().gs.tryTrash, undefined);                  // pasted for the check; never part of the project
});
