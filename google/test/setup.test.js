'use strict';
// Config.gs and the project as a whole: triggers, the setup check, the manifest, and what the code may hold.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { load, GOOGLE_DIR } = require('./harness');

const triggers = (h) => h.scriptApp.triggers.map((t) => t.spec).sort((a, b) => a.handler.localeCompare(b.handler));

test('installTriggers sets each job its cadence, once, however often it is run', () => {
  const h = load();
  h.scriptApp.newTrigger('somethingElse').timeBased().everyHours(1).create();
  h.gs.installTriggers();
  h.gs.installTriggers();
  assert.deepEqual(triggers(h), [
    { handler: 'exportFab', type: 'clock', everyMinutes: 5 },
    { handler: 'onFabEdit', type: 'spreadsheet', spreadsheet: 'sheet-test-1', event: 'edit' },
    { handler: 'processOutbox', type: 'clock', everyMinutes: 1 },
    { handler: 'pruneControlFolder', type: 'clock', everyDays: 1, atHour: 3, timeZone: 'America/Los_Angeles' },
    { handler: 'runWatchdog', type: 'clock', everyMinutes: 5 },
    { handler: 'somethingElse', type: 'clock', everyHours: 1 },
  ]);
});

test('the setup check names what is missing in plain words, and installTriggers waits for it', () => {
  const h = load({ props: { SIGNING_KEY: 'not hex', CONTROL_FOLDER_ID: null, ROLE_JIM_PUSHOVER: null, EXPECTED_ON_FROM: '6am' },
    timeZone: 'Etc/UTC' });
  const problems = h.gs.checkSetup();
  assert.deepEqual(Array.from(problems), [
    'the project time zone is Etc/UTC, not America/Los_Angeles (see appsscript.json)',
    'the Script Property SIGNING_KEY is not set to the 64-hex-character signing key',
    'the control folder could not be opened: the Script Property CONTROL_FOLDER_ID is not set (see README.md)',
    'jim has no pushover set up (Script Properties ROLE_JIM_PUSHOVER and PUSHOVER_TOKEN)',
    'EXPECTED_ON_FROM is not a time like 06:00',
  ]);
  assert.throws(() => h.gs.installTriggers(), /fix the setup first/);
  assert.deepEqual(h.scriptApp.triggers, []);
  assert.deepEqual(Array.from(load().gs.checkSetup()), []);
});

test('the setup check never shows a secret, even a wrong one', () => {
  const h = load({ props: { SIGNING_KEY: 'deadbeef-not-the-key', PUSHOVER_TOKEN: 'pushover-app-token' } });
  const said = JSON.stringify(Array.from(h.gs.checkSetup())) + JSON.stringify(h.logs);
  for (const secret of ['deadbeef-not-the-key', 'pushover-app-token', 'jim@example.com', 'dev@example.com']) {
    assert.ok(!said.includes(secret), secret);
  }
});

test('the files load in any order and do nothing until a trigger runs', () => {
  const h = load({ reverse: true });
  assert.deepEqual(h.drive.log, []);
  assert.deepEqual(h.book.reads, []);
  assert.equal(h.props.getKeys().filter((k) => k.startsWith('state.')).length, 0);
  assert.equal(typeof h.gs.exportFab, 'function');
  assert.deepEqual(h.files.slice().sort(), ['Config.gs', 'FabEdits.gs', 'FabExport.gs', 'Outbox.gs', 'Prune.gs',
    'Signing.gs', 'Watchdog.gs']);
});

test('the manifest is V8 in Pacific time and asks only for what the scripts use', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(GOOGLE_DIR, 'appsscript.json'), 'utf8'));
  assert.equal(manifest.runtimeVersion, 'V8');
  assert.equal(manifest.timeZone, 'America/Los_Angeles');
  assert.deepEqual(manifest.oauthScopes.slice().sort(), [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/script.external_request',
    'https://www.googleapis.com/auth/script.scriptapp',
    'https://www.googleapis.com/auth/script.send_mail',
    'https://www.googleapis.com/auth/spreadsheets',
  ]);
});

test('the code names no address, token or key, and calls out only to Pushover and Slack', () => {
  for (const name of fs.readdirSync(GOOGLE_DIR).filter((n) => /\.(gs|json|md)$/.test(n))) {
    const text = fs.readFileSync(path.join(GOOGLE_DIR, name), 'utf8');
    assert.doesNotMatch(text, /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}/, `${name} holds an email address`);
    assert.doesNotMatch(text, /\b[0-9a-f]{64}\b/i, `${name} holds a key`);
    assert.doesNotMatch(text, /\bxox[abp]-|\bhooks\.slack\.com/, `${name} holds a Slack token or webhook`);
    if (name.endsWith('.gs')) {
      const urls = (text.match(/https?:\/\/[^\s'"]+/g) || []).sort();
      for (const url of urls) {
        assert.ok(['https://api.pushover.net/1/messages.json', 'https://slack.com/api/chat.postMessage'].includes(url), url);
      }
    }
  }
});
