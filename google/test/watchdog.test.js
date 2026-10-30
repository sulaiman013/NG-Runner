'use strict';
// Watchdog.gs: a silent PC is noticed. Sulaiman hears of a stale heartbeat; Jim hears there is no list.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { load, hexToSigned, pythonEnvelopes, KEY_HEX } = require('./harness');

const KEY = hexToSigned(KEY_HEX);
const RISK = 'Not checked: Mockford 3/4 Classic White, posted 11:40 PM';

/** NG Runner's heartbeat file, as it would reach Drive at the time it was issued. */
function beat(h, seq, at, { risks = [], key = KEY, name, payload } = {}) {
  const issued = new Date(at);
  const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Los_Angeles', year: 'numeric', month: '2-digit', day: '2-digit' })
    .format(issued).replace(/-/g, '');
  const body = payload || { app_version: '0.0.1', paused: false, open_risks: risks, last_fab_seq: 12,
    list_synced_at: null, pending: 0, dev_open: 0 };
  const text = h.gs.sealEnvelope_('heartbeat', body, { key, seq, issuedAt: issued, id: crypto.randomBytes(16).toString('hex') });
  return h.control.put(`heartbeat/${day}/${name || seq}.json`, text, { createdMinutesAgo: (h.clock.ms - issued.getTime()) / 60000 });
}

const alerts = (h) => h.mail.sent.filter((m) => m.to === 'dev@example.com');
const toJim = (h) => h.fetch.requests.filter((r) => r.params.payload && r.params.payload.user === 'pushover-user-jim')
  .map((r) => r.params.payload);

test('a heartbeat 45 minutes old is fine; past that Sulaiman is alerted once per gap', () => {
  const h = load({ now: '2026-10-30T20:00:00Z' });              // 1:00 PM PDT
  beat(h, 100, '2026-10-30T19:15:00Z');
  h.gs.runWatchdog();
  assert.equal(alerts(h).length, 0);
  h.clock.advance(5);
  h.gs.runWatchdog();
  assert.deepEqual(alerts(h).map((m) => [m.subject, m.body]), [['NG Runner is not checking in',
    'NG Runner has not checked in since 12:15 PM (50 minutes ago).']]);
  for (let i = 0; i < 6; i++) { h.clock.advance(5); h.gs.runWatchdog(); }
  assert.equal(alerts(h).length, 1);
  beat(h, 101, h.clock.now().toISOString());                    // it is back: the gap is over
  h.gs.runWatchdog();
  h.clock.advance(50);
  h.gs.runWatchdog();
  assert.equal(alerts(h).length, 2);                            // a new gap, a new alert
});

test('outside the hours the PC is expected on there is no alert; it comes when they begin', () => {
  const h = load({ now: '2026-10-30T06:30:00Z' });              // 11:30 PM PDT on 10/29
  beat(h, 100, '2026-10-30T05:00:00Z', { risks: [RISK] });      // 10:00 PM
  h.gs.runWatchdog();
  h.clock.set('2026-10-30T12:55:00Z');                          // 5:55 AM
  h.gs.runWatchdog();
  assert.equal(alerts(h).length, 0);
  h.clock.set('2026-10-30T13:00:00Z');                          // 6:00 AM
  h.gs.runWatchdog();
  assert.deepEqual(alerts(h).map((m) => m.body), [
    `NG Runner has not checked in since 10:00 PM (8 hours ago).\n1. ${RISK}`]);
});

test('the 45 minutes are real minutes when the clocks go back in autumn', () => {
  const h = load({ now: '2026-11-01T09:10:00Z', props: { EXPECTED_ON_FROM: '00:00', EXPECTED_ON_UNTIL: '00:00' } });
  beat(h, 100, '2026-11-01T08:30:00Z');                         // 1:30 AM PDT; it is now 1:10 AM PST
  h.gs.runWatchdog();
  assert.equal(alerts(h).length, 0);                            // 40 minutes, though the wall clock went back
  h.clock.set('2026-11-01T09:16:00Z');
  h.gs.runWatchdog();
  assert.deepEqual(alerts(h).map((m) => m.body), ['NG Runner has not checked in since 1:30 AM (46 minutes ago).']);
});

test('the 45 minutes are real minutes when the clocks go forward in spring', () => {
  const h = load({ now: '2027-03-14T10:30:00Z', props: { EXPECTED_ON_FROM: '00:00', EXPECTED_ON_UNTIL: '00:00' } });
  beat(h, 100, '2027-03-14T09:50:00Z');                         // 1:50 AM PST; it is now 3:30 AM PDT
  h.gs.runWatchdog();
  assert.equal(alerts(h).length, 0);                            // 40 minutes, though the wall clock says 100
  h.clock.set('2027-03-14T10:36:00Z');
  h.gs.runWatchdog();
  assert.equal(alerts(h).length, 1);
});

test('with no digest by 7:00 AM Jim hears there is no list, with the open risks', () => {
  const h = load({ now: '2026-10-30T13:55:00Z' });              // 6:55 AM PDT
  beat(h, 100, '2026-10-30T06:42:00Z', { risks: [RISK] });      // 11:42 PM PDT on 10/29
  h.gs.runWatchdog();
  assert.deepEqual(toJim(h), []);
  h.clock.set('2026-10-30T14:00:00Z');                          // 7:00 AM
  h.gs.runWatchdog();
  assert.deepEqual(toJim(h), [{ token: 'pushover-app-token', user: 'pushover-user-jim', title: 'No list today',
    message: 'NG Runner has not checked in since 11:42 PM, so there is no list today. Your PC may be asleep, off'
      + ` or signed out. Sulaiman has been alerted.\n1. ${RISK}`, priority: '1' }]);
  assert.equal(alerts(h).length, 1);
  for (let i = 0; i < 4; i++) { h.clock.advance(5); h.gs.runWatchdog(); }
  assert.equal(toJim(h).length, 1);
  assert.ok(!JSON.stringify(toJim(h)).includes('Nothing is lost'));
});

test('7:00 AM is Pacific wall time on both sides of each clock change', () => {
  for (const [before, at] of [['2026-11-01T14:30:00Z', '2026-11-01T15:00:00Z'],     // PST from 11/1
    ['2026-11-02T14:55:00Z', '2026-11-02T15:00:00Z'],
    ['2027-03-14T13:55:00Z', '2027-03-14T14:00:00Z'],                                // PDT from 3/14
    ['2026-10-31T13:55:00Z', '2026-10-31T14:00:00Z']]) {
    const h = load({ now: before });
    h.gs.runWatchdog();
    assert.equal(toJim(h).length, 0, before);
    h.clock.set(at);
    h.gs.runWatchdog();
    assert.equal(toJim(h).length, 1, at);
    assert.match(toJim(h)[0].message, /^NG Runner has not checked in, so there is no list today\./);
  }
});

test('a digest sent by 7:00 AM means no message, even one still waiting in the outbox', () => {
  const sent = load({ now: '2026-10-30T13:30:00Z' });           // 6:30 AM
  const digest = { channel: 'pushover', to: 'jim', subject: 'NG Runner · Fri 10/30', lines: ['1. Quiet night.'],
    urgent: false, ref: 'digest:2026-10-30' };
  const put = (h, seq) => {
    const id = crypto.randomBytes(16).toString('hex');
    h.control.put(`outbox/${id}.json`, h.gs.sealEnvelope_('outbox', digest, { key: KEY, seq, issuedAt: h.clock.now(), id }));
  };
  put(sent, 1);
  sent.gs.processOutbox();
  beat(sent, 100, '2026-10-30T13:30:00Z');
  sent.clock.set('2026-10-30T14:00:00Z');
  sent.gs.runWatchdog();
  assert.deepEqual(toJim(sent).map((p) => p.title), ['NG Runner · Fri 10/30']);
  const waiting = load({ now: '2026-10-30T14:00:00Z' });        // the outbox trigger has not run yet
  beat(waiting, 100, '2026-10-30T13:59:00Z');
  put(waiting, 1);
  waiting.gs.runWatchdog();
  assert.deepEqual(toJim(waiting).map((p) => p.title), ['NG Runner · Fri 10/30']);
});

test('a digest that went out counts even when its receipt could not be written', () => {
  const h = load({ now: '2026-10-30T13:30:00Z' });
  const receipts = h.control.folder('receipts', true);
  receipts.createFile = () => { throw new Error('Drive is busy'); };
  const set = h.props.setProperty.bind(h.props);
  h.props.setProperty = (k, v) => { if (k === 'state.receipts.owed') throw new Error('quota'); return set(k, v); };
  const id = crypto.randomBytes(16).toString('hex');
  const digest = { channel: 'pushover', to: 'jim', subject: 'NG Runner · Fri 10/30', lines: ['1. Quiet night.'],
    urgent: false, ref: 'digest:2026-10-30' };
  h.control.put(`outbox/${id}.json`, h.gs.sealEnvelope_('outbox', digest, { key: KEY, seq: 1, issuedAt: h.clock.now(), id }));
  assert.throws(() => h.gs.processOutbox(), /quota/);
  h.props.setProperty = set;
  beat(h, 100, '2026-10-30T13:59:00Z');
  h.clock.set('2026-10-30T14:00:00Z');
  h.gs.runWatchdog();
  assert.deepEqual(toJim(h).map((p) => p.title), ['NG Runner · Fri 10/30']);
});

test('when the PC checked in but no list came, Jim and Sulaiman are told just that', () => {
  const h = load({ now: '2026-10-30T14:00:00Z' });
  beat(h, 100, '2026-10-30T13:55:00Z');                         // 6:55 AM
  h.gs.runWatchdog();
  assert.deepEqual(toJim(h).map((p) => p.message), [
    'NG Runner checked in at 6:55 AM but sent no list today. Sulaiman has been alerted.']);
  assert.deepEqual(alerts(h).map((m) => [m.subject, m.body]), [['No list reached Jim',
    'NG Runner checked in at 6:55 AM, but no list reached Jim by 7:00 AM.']]);
});

test('Jim is never told Sulaiman was alerted when he could not be', () => {
  const h = load({ now: '2026-10-30T14:00:00Z', props: { ROLE_SULAIMAN_EMAIL: null } });
  beat(h, 100, '2026-10-30T06:42:00Z');
  h.gs.runWatchdog();
  assert.match(toJim(h)[0].message, /signed out\. Sulaiman could not be alerted\.$/);
});

test('a PC declared to sleep at night gets no morning alarm', () => {
  const h = load({ now: '2026-10-30T14:00:00Z', props: { PC_SLEEPS_AT_NIGHT: 'true' } });
  beat(h, 100, '2026-10-30T06:42:00Z');
  h.gs.runWatchdog();
  assert.deepEqual(toJim(h), []);
});

test('heartbeats that fail their check never count as the PC being on', () => {
  const h = load({ now: '2026-10-30T20:00:00Z' });
  beat(h, 100, '2026-10-30T19:00:00Z');
  beat(h, 101, '2026-10-30T19:55:00Z', { key: hexToSigned('11'.repeat(32)) });     // another key
  beat(h, 999, '2026-10-30T19:56:00Z', { name: '102' });                            // name and seq differ
  const edited = beat(h, 103, '2026-10-30T19:57:00Z');
  edited.content = edited.content.replace('0.0.1', '0.0.2');
  h.gs.runWatchdog();
  assert.deepEqual(alerts(h).map((m) => m.body), ['NG Runner has not checked in since 12:00 PM (60 minutes ago).']);
});

test('a heartbeat dated in the future counts only from when it was first seen', () => {
  const h = load({ now: '2026-10-30T20:00:00Z' });
  beat(h, 100, '2026-10-30T23:00:00Z');                         // the PC's clock is 3 hours ahead
  h.gs.runWatchdog();
  h.clock.advance(46);
  h.gs.runWatchdog();
  assert.equal(alerts(h).length, 1);
});

test('the newest heartbeat is found across the last day folders by its sequence number', () => {
  const h = load({ now: '2026-10-31T07:10:00Z' });              // 12:10 AM PDT on 10/31
  beat(h, 200, '2026-10-31T06:50:00Z');                         // 11:50 PM on 10/30
  beat(h, 201, '2026-10-31T07:05:00Z');                         // 12:05 AM on 10/31
  h.gs.runWatchdog();
  h.clock.set('2026-10-31T13:00:00Z');                          // 6:00 AM
  h.gs.runWatchdog();
  assert.deepEqual(alerts(h).map((m) => m.body), ['NG Runner has not checked in since 12:05 AM (6 hours ago).']);
});

test('a heartbeat sealed by NG Runner in Python is read', (t) => {
  const python = pythonEnvelopes();
  if (!python) { t.skip('run through pytest (tests/test_fab.py) to get envelopes sealed by Python'); return; }
  const sealed = python.envelopes.find((e) => e.kind === 'heartbeat');
  const h = load({ now: '2026-10-30T13:59:00Z', props: { SIGNING_KEY: python.key_hex } });
  h.control.put(`heartbeat/20261030/${sealed.seq}.json`, sealed.text, { createdMinutesAgo: 4 });
  h.clock.set('2026-10-30T14:00:00Z');
  h.gs.runWatchdog();
  assert.deepEqual(toJim(h).map((p) => p.message), [
    `NG Runner checked in at 6:55 AM but sent no list today. Sulaiman has been alerted.\n1. ${sealed.payload.open_risks[0]}`]);
});
