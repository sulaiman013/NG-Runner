'use strict';
// Watchdog.gs: NOT CHECKED from the cloud (P1b build spec 5.7). With NG Runner silent nothing on Jim's
// PC can tell George a nest went unchecked, so this does. It does nothing until the role george and
// NG_JOBS_FOLDER_ID are set, so deploying it changes nothing for the live shadow run. All mocked:
// whether the script's account can read NG Jobs, and what a folder's time means on real Drive, is
// UNVERIFIED.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { load, plain, hexToSigned, KEY_HEX } = require('./harness');

const KEY = hexToSigned(KEY_HEX);
const GEORGE = { ROLE_GEORGE_EMAIL: 'george@example.com' };
const RISK = 'Not checked: Testerson_101426 3/4 Classic White, posted 11:40 AM';
const OTHER = 'DO NOT CUT Fakeley_102026 3/4 Classic White (trial): 8 cam holes are doubled in program FAKS01';
const NOON = '2026-10-30T19:00:00Z';                            // 12:00 PM PDT
const THEN = '2026-10-30T18:00:00Z';                            // 11:00 AM PDT, stale at noon
const SUBJECT = 'NOT CHECKED · NG Runner is offline';

function beat(h, seq, at, risks = []) {
  const issued = new Date(at);
  const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Los_Angeles', year: 'numeric', month: '2-digit', day: '2-digit' })
    .format(issued).replace(/-/g, '');
  const text = h.gs.sealEnvelope_('heartbeat', { app_version: '0.0.1', paused: false, open_risks: risks,
    last_fab_seq: 12, list_synced_at: null, pending: 0, dev_open: 0 },
  { key: KEY, seq, issuedAt: issued, id: crypto.randomBytes(16).toString('hex') });
  h.control.put(`heartbeat/${day}/${seq}.json`, text, { createdMinutesAgo: (h.clock.ms - issued.getTime()) / 60000 });
}

/** A made-up NG Jobs on Drive, its id in NG_JOBS_FOLDER_ID: {job: {material: when it last changed}}. */
function ngJobs(h, tree = {}) {
  const root = h.drive.root.createFolder('NG Jobs');
  h.props.setProperty('NG_JOBS_FOLDER_ID', root.getId());
  grow(root, tree);
  return root;
}

function grow(root, tree) {
  for (const [job, materials] of Object.entries(tree)) {
    const folder = root.folders().find((f) => f.name === job) || root.createFolder(job);
    folder.updated = Date.parse('2026-10-01T00:00:00Z');
    for (const [material, at] of Object.entries(materials)) {
      const made = folder.folders().find((f) => f.name === material) || folder.createFolder(material);
      made.updated = Date.parse(at);
    }
  }
}

const toGeorge = (h) => h.mail.sent.filter((m) => m.to === 'george@example.com');
const toSulaiman = (h) => h.mail.sent.filter((m) => m.to === 'dev@example.com');

/** Each call to read a folder's folders, so a test can tell whether NG Jobs was walked at all. */
function countWalks(h) {
  const seen = [];
  const real = h.drive.getFolderById.bind(h.drive);
  h.drive.getFolderById = (id) => { seen.push(id); return real(id); };
  return seen;
}

// ----- it does nothing until it is set up

test('with neither george nor the folder set nothing changes: this is what the live shadow has', () => {
  const h = load({ now: NOON });
  const walks = countWalks(h);
  beat(h, 100, THEN, [RISK]);
  const first = h.gs.runWatchdog();
  assert.equal(first.george, null);
  assert.deepEqual(toGeorge(h), []);
  assert.deepEqual(toSulaiman(h).map((m) => m.subject), ['NG Runner is not checking in']);
  assert.equal(walks.filter((id) => id !== h.props.getProperty('CONTROL_FOLDER_ID')).length, 0);
});

test('with the folder set and no george it never looks in NG Jobs and tells nobody', () => {
  const h = load({ now: NOON });
  ngJobs(h, { Testerson_101426: { '3-4 Classic White': '2026-10-30T18:40:00Z' } });
  const walks = countWalks(h);
  beat(h, 100, THEN, [RISK]);
  assert.equal(h.gs.runWatchdog().george, null);
  assert.equal(walks.filter((id) => id === h.props.getProperty('NG_JOBS_FOLDER_ID')).length, 0);
  assert.equal(h.mail.sent.length, 1);                          // Sulaiman's alert, as ever
});

test('george set with no folder: the heartbeat\'s Not checked lines go, and no walk', () => {
  const h = load({ now: NOON, props: GEORGE });
  beat(h, 100, THEN, [OTHER, RISK, 'Not checked: 3 more materials are on the board']);
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h).map((m) => [m.subject, m.body]), [[SUBJECT,
    `1. ${RISK}\n2. Not checked: 3 more materials are on the board`]]);
});

// ----- once per gap, and only in a gap

test('a fresh heartbeat tells George nothing, whatever it says is open', () => {
  const h = load({ now: NOON, props: GEORGE });
  ngJobs(h, { Testerson_101426: { '3-4 Classic White': NOON } });
  const walks = countWalks(h);
  beat(h, 100, '2026-10-30T18:30:00Z', [RISK]);
  assert.equal(h.gs.runWatchdog().george, null);
  assert.deepEqual(toGeorge(h), []);
  assert.equal(walks.filter((id) => id === h.props.getProperty('NG_JOBS_FOLDER_ID')).length, 0);
});

test('George hears once per gap, and a new gap tells him again', () => {
  const h = load({ now: NOON, props: GEORGE });
  beat(h, 100, THEN, [RISK]);
  h.gs.runWatchdog();
  for (let i = 0; i < 6; i++) { h.clock.advance(5); h.gs.runWatchdog(); }
  assert.equal(toGeorge(h).length, 1);
  beat(h, 101, h.clock.now().toISOString(), [RISK]);            // it is back: the gap is over
  h.gs.runWatchdog();
  assert.equal(toGeorge(h).length, 1);
  h.clock.advance(50);
  h.gs.runWatchdog();
  assert.equal(toGeorge(h).length, 2);
});

test('outside the hours the PC is expected on there is no gap yet; it comes when they begin', () => {
  const h = load({ now: '2026-10-30T06:30:00Z', props: GEORGE });   // 11:30 PM PDT on 10/29
  beat(h, 100, '2026-10-30T05:00:00Z', [RISK]);                 // 10:00 PM
  h.gs.runWatchdog();
  h.clock.set('2026-10-30T12:55:00Z');                          // 5:55 AM
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h), []);
  h.clock.set('2026-10-30T13:00:00Z');                          // 6:00 AM
  h.gs.runWatchdog();
  assert.equal(toGeorge(h).length, 1);
});

test('a PC that has never checked in gives George nothing to go on', () => {
  const h = load({ now: NOON, props: GEORGE });
  ngJobs(h, { Testerson_101426: { '3-4 Classic White': NOON } });
  assert.equal(h.gs.runWatchdog().george, null);
  assert.deepEqual(toGeorge(h), []);
});

test('with nothing open and nothing posted there is no message', () => {
  const h = load({ now: NOON, props: GEORGE });
  ngJobs(h, { Testerson_101426: { '3-4 Classic White': '2026-10-01T00:00:00Z' } });
  beat(h, 100, THEN, [OTHER]);
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h), []);
});

test('open risks that could not be read cost only those lines', () => {
  const h = load({ now: NOON, props: GEORGE });
  ngJobs(h, { Testerson_101426: { '3-4 Classic White': '2026-10-30T18:40:00Z' } });
  const issued = new Date(THEN);
  const text = h.gs.sealEnvelope_('heartbeat', { app_version: '0.0.1', open_risks: 'not a list' },
    { key: KEY, seq: 100, issuedAt: issued, id: crypto.randomBytes(16).toString('hex') });
  h.control.put('heartbeat/20261030/100.json', text, { createdMinutesAgo: 60 });
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h).map((m) => m.body), [
    '1. Testerson_101426: NOT CHECKED 3/4 Classic White, posted 11:40 AM. NG Runner is offline.']);
});

// ----- what was posted while NG Runner was silent

test('a material folder updated after the last beat is reported with its time', () => {
  const h = load({ now: NOON, props: GEORGE });
  ngJobs(h, {
    Testerson_101426: { '3-4 Egger Classic White': '2026-10-30T18:40:00Z', '1-4 Egger Classic White': '2026-10-30T17:00:00Z' },
    Fakeley_102026: { 'Tafisa Spring Blossom_L543': '2026-10-30T18:50:00Z' },
  });
  beat(h, 100, THEN);
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h).map((m) => [m.subject, m.body]), [[SUBJECT,
    '1. Testerson_101426: NOT CHECKED 3/4 Egger Classic White, posted 11:40 AM. NG Runner is offline.\n'
    + '2. Fakeley_102026: NOT CHECKED Tafisa Spring Blossom L543, posted 11:50 AM. NG Runner is offline.']]);
});

test('Old is not watched, a job folder\'s own time means nothing, and the trash is no post', () => {
  const h = load({ now: NOON, props: GEORGE });
  const root = ngJobs(h, {
    Old: { '3-4 Classic White': '2026-10-30T18:40:00Z' },
    old: { '3-4 Classic White': '2026-10-30T18:40:00Z' },
    Testerson_101426: { Binned: '2026-10-30T18:40:00Z' },
  });
  root.folders().find((f) => f.name === 'Testerson_101426').updated = Date.parse('2026-10-30T18:40:00Z');
  root.folders().find((f) => f.name === 'Testerson_101426').folders()[0].trashed = true;
  beat(h, 100, THEN);
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h), []);
});

test('the heartbeat\'s lines and what was posted go in one message', () => {
  const h = load({ now: NOON, props: GEORGE });
  ngJobs(h, { Fakeley_102026: { '3-4 Classic White': '2026-10-30T18:40:00Z' } });
  beat(h, 100, THEN, [RISK]);
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h).map((m) => m.body), [
    `1. ${RISK}\n2. Fakeley_102026: NOT CHECKED 3/4 Classic White, posted 11:40 AM. NG Runner is offline.`]);
});

test('NG Jobs is walked at most every 30 minutes, and a new post is told by itself, once', () => {
  const h = load({ now: NOON, props: GEORGE });
  const root = ngJobs(h, { Testerson_101426: { '3-4 Classic White': '2026-10-30T18:40:00Z' } });
  const walks = countWalks(h);
  const id = h.props.getProperty('NG_JOBS_FOLDER_ID');
  beat(h, 100, THEN);
  h.gs.runWatchdog();
  assert.equal(walks.filter((w) => w === id).length, 1);
  grow(root, { Fakeley_102026: { '3-4 Classic White': '2026-10-30T19:10:00Z' } });
  for (let i = 0; i < 5; i++) { h.clock.advance(5); h.gs.runWatchdog(); }       // 12:25 PM
  assert.equal(walks.filter((w) => w === id).length, 1);
  assert.equal(toGeorge(h).length, 1);
  h.clock.advance(5);                                           // 12:30 PM
  h.gs.runWatchdog();
  assert.equal(walks.filter((w) => w === id).length, 2);
  assert.deepEqual(toGeorge(h).map((m) => m.body).at(-1),
    '1. Fakeley_102026: NOT CHECKED 3/4 Classic White, posted 12:10 PM. NG Runner is offline.');
  h.clock.advance(30);
  h.gs.runWatchdog();
  assert.equal(toGeorge(h).length, 2);                          // nothing newer: nothing more
});

test('the same material posted again in the same gap is told again', () => {
  const h = load({ now: NOON, props: GEORGE });
  const root = ngJobs(h, { Testerson_101426: { '3-4 Classic White': '2026-10-30T18:40:00Z' } });
  beat(h, 100, THEN);
  h.gs.runWatchdog();
  grow(root, { Testerson_101426: { '3-4 Classic White': '2026-10-30T19:10:00Z' } });
  h.clock.advance(30);
  h.gs.runWatchdog();
  assert.equal(toGeorge(h).length, 2);
});

test('NG Jobs walked only in the hours the PC is expected on: a night off costs no quota', () => {
  const h = load({ now: '2026-10-30T06:30:00Z', props: GEORGE });   // 11:30 PM PDT on 10/29
  ngJobs(h, { Testerson_101426: { '3-4 Classic White': '2026-10-30T06:10:00Z' } });
  const walks = countWalks(h);
  beat(h, 100, '2026-10-30T05:00:00Z');
  for (let i = 0; i < 12; i++) { h.clock.advance(30); h.gs.runWatchdog(); }
  assert.equal(walks.filter((w) => w === h.props.getProperty('NG_JOBS_FOLDER_ID')).length, 0);
  h.clock.set('2026-10-30T13:00:00Z');                          // 6:00 AM: it finds what came overnight
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h).map((m) => m.body), [
    '1. Testerson_101426: NOT CHECKED 3/4 Classic White, posted 11:10 PM. NG Runner is offline.']);
});

test('many posts are held to a list a person can read, and what it keeps fits Script Properties', () => {
  const h = load({ now: NOON, props: GEORGE });
  const tree = {};
  for (let i = 0; i < 300; i++) {
    tree[`Testerson_${String(100000 + i)}`] = { [`3-4 A Long Colour Name Of Some Kind ${i}`]: '2026-10-30T18:40:00Z' };
  }
  ngJobs(h, tree);
  beat(h, 100, THEN);
  h.gs.runWatchdog();
  const lines = toGeorge(h)[0].body.split('\n');
  assert.equal(lines.length, 21);
  assert.match(lines[20], /^21\. 280 more were posted while NG Runner was offline\.$/);
  h.clock.advance(30);
  h.gs.runWatchdog();                                           // no Script Properties limit was hit
  assert.equal(toGeorge(h).length, 1);
});

// ----- when things go wrong

test('an account that can\'t read NG Jobs still tells George what the heartbeat said, and logs why', () => {
  const h = load({ now: NOON, props: { ...GEORGE, NG_JOBS_FOLDER_ID: 'no-such-folder' } });
  beat(h, 100, THEN, [RISK]);
  const result = h.gs.runWatchdog();
  assert.equal(result.alerted, true);
  assert.deepEqual(toGeorge(h).map((m) => m.body), [`1. ${RISK}`]);
  assert.ok(h.logs.some((l) => /NG Jobs could not be read/.test(l)), plain(h.logs).join('\n'));
});

test('a walk that breaks part way costs only the walk, and what it found still goes', () => {
  const h = load({ now: NOON, props: GEORGE });
  const root = ngJobs(h, {
    Fakeley_102026: { '3-4 Classic White': '2026-10-30T18:40:00Z' },
    Testerson_101426: { '3-4 Classic White': '2026-10-30T18:40:00Z' },
  });
  root.folders().find((f) => f.name === 'Testerson_101426').getFolders = () => { throw new Error('Drive is busy'); };
  beat(h, 100, THEN);
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h).map((m) => m.body), [
    '1. Fakeley_102026: NOT CHECKED 3/4 Classic White, posted 11:40 AM. NG Runner is offline.']);
  assert.ok(h.logs.some((l) => /NG Jobs/.test(l)));
});

test('a walk that runs long stops and says so, so the trigger never runs out', () => {
  const h = load({ now: NOON, props: GEORGE });
  const root = ngJobs(h, {
    Fakeley_102026: { '3-4 Classic White': '2026-10-30T18:40:00Z' },
    Testerson_101426: { '3-4 Classic White': '2026-10-30T18:40:00Z' },
  });
  const first = root.folders()[0];
  const real = first.getFolders.bind(first);
  first.getFolders = () => { h.clock.advance(4); return real(); };      // Drive being slow
  beat(h, 100, THEN);
  h.gs.runWatchdog();
  assert.equal(toGeorge(h).length, 1);
  assert.equal(toGeorge(h)[0].body.split('\n').length, 1);
  assert.ok(h.logs.some((l) => /ran out of time/.test(l)));
});

test('a message that can\'t go is tried again, and nothing is marked told', () => {
  const h = load({ now: NOON, props: GEORGE });
  beat(h, 100, THEN, [RISK]);
  h.mail.failure = 'Service unavailable';
  assert.equal(h.gs.runWatchdog().george.ok, false);
  h.mail.failure = null;
  h.clock.advance(5);
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h).map((m) => m.body), [`1. ${RISK}`]);
});

test('with George\'s email not reaching him a walk\'s findings wait, and are not lost', () => {
  const h = load({ now: NOON, props: GEORGE });
  ngJobs(h, { Testerson_101426: { '3-4 Classic White': '2026-10-30T18:40:00Z' } });
  beat(h, 100, THEN);
  h.mail.failure = 'Service unavailable';
  h.gs.runWatchdog();
  h.mail.failure = null;
  h.clock.advance(30);
  h.gs.runWatchdog();
  assert.equal(toGeorge(h).length, 1);
});

// ----- the shadow run: everything to Sulaiman, marked

test('in the shadow run it all goes to the alert role marked SHADOW, and never to George', () => {
  const h = load({ now: NOON, props: { ...GEORGE, SHADOW: 'true' } });
  ngJobs(h, { Fakeley_102026: { '3-4 Classic White': '2026-10-30T18:40:00Z' } });
  beat(h, 100, THEN, [RISK]);
  h.gs.runWatchdog();
  assert.deepEqual(toGeorge(h), []);
  assert.deepEqual(toSulaiman(h).map((m) => [m.subject, m.body]).filter(([s]) => s.startsWith('SHADOW')), [[`SHADOW ${SUBJECT}`,
    `1. ${RISK}\n2. Fakeley_102026: NOT CHECKED 3/4 Classic White, posted 11:40 AM. NG Runner is offline.`]]);
  h.clock.advance(5);
  h.gs.runWatchdog();
  assert.equal(toSulaiman(h).filter((m) => m.subject.startsWith('SHADOW')).length, 1);
});

test('in the shadow run with no george set it still does nothing, so deploying it changes nothing', () => {
  const h = load({ now: NOON, props: { SHADOW: 'true' } });
  ngJobs(h, { Fakeley_102026: { '3-4 Classic White': '2026-10-30T18:40:00Z' } });
  beat(h, 100, THEN, [RISK]);
  assert.equal(h.gs.runWatchdog().george, null);
  assert.deepEqual(toSulaiman(h).map((m) => m.subject), ['NG Runner is not checking in']);
});

// ----- the setup check

test('checkSetup says when NG_JOBS_FOLDER_ID can\'t be opened, which is how Sulaiman finds out', () => {
  const h = load({ props: { NG_JOBS_FOLDER_ID: 'no-such-folder' } });
  assert.deepEqual(Array.from(h.gs.checkSetup()),
    ['NG Jobs could not be opened with NG_JOBS_FOLDER_ID, so a post while NG Runner is offline can\'t be found']);
  const fine = load();
  ngJobs(fine);
  assert.deepEqual(Array.from(fine.gs.checkSetup()), []);
  assert.deepEqual(Array.from(load().gs.checkSetup()), []);     // not set: nothing to check
});

test('checkSetup asks for George and Will only once GUARDS_ON is set', () => {
  assert.deepEqual(Array.from(load().gs.checkSetup()), []);
  const on = load({ props: { GUARDS_ON: 'true' } });
  assert.deepEqual(Array.from(on.gs.checkSetup()), [
    'george has no email set up (Script Property ROLE_GEORGE_EMAIL)',
    'will has no email set up (Script Property ROLE_WILL_EMAIL)']);
  const both = load({ props: { GUARDS_ON: 'true', ...GEORGE, ROLE_WILL_EMAIL: 'will@example.com' } });
  assert.deepEqual(Array.from(both.gs.checkSetup()), []);
});
