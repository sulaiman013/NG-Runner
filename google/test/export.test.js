'use strict';
// FabExport.gs: Sheet1's display values become fab/export.json, signed, replaced in place.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { load, plain, hexToSigned, outDir, sheet1, KEY_HEX } = require('./harness');

const KEY = hexToSigned(KEY_HEX);

function readExport(h) {
  return h.gs.openEnvelope_(h.control.text('fab/export.json'), KEY, 'fab_export');
}

const ROWS = [
  { sheet_row: 2, job: 'Testerson', designer: 'PE', install: ['Wednesday', ' 7 /1'], fab_date: '6/24/26' },
  { sheet_row: 3, job: 'Fakeley_070826', designer: 'QA', install: ['Wednesday', ' 7 /8'], fab_date: '' },
  { sheet_row: 5, job: 'Mockford JD', designer: 'ZT', install: ['7/15/26', '7/15/26'], fab_date: '7/10/26' },
  { sheet_row: 6, job: 'Dummitt garage', designer: 'Avery', install: ['Friday', ' 7 /17'], fab_date: '' },
  { sheet_row: 7, job: 'Fakeley_07126', designer: 'QA', install: ['Monday', ' 7 /20'], fab_date: '' },
];

test("Sheet1's job, designer, install and fab date cells are exported as shown, with their rows", () => {
  const h = load();
  h.gs.exportFab();
  const env = readExport(h);
  assert.deepEqual(plain(env.payload), { sheet: 'Sheet1', exported_at: '2026-10-30T13:30:00+00:00', rows: ROWS });
  assert.equal(env.issuedAt.toISOString(), '2026-10-30T13:30:00.000Z');
  const out = outDir();
  if (out) fs.writeFileSync(path.join(out, 'export.json'), h.control.text('fab/export.json'), 'utf8');
});

test('each export replaces the last one in place, one sequence number higher', () => {
  const h = load();
  h.gs.exportFab();
  const first = readExport(h);
  const fileId = h.control.live('fab/export.json')[0].getId();
  h.clock.advance(5);
  h.gs.exportFab();
  const second = readExport(h);
  assert.equal(h.control.live('fab/export.json').length, 1);
  assert.equal(h.control.live('fab/export.json')[0].getId(), fileId);
  assert.equal(second.seq, first.seq + 1);
  assert.notEqual(second.id, first.id);
  assert.equal(second.payload.exported_at, '2026-10-30T13:35:00+00:00');
  // A new stream starts at the current Unix second, so a re-created project never goes back.
  assert.equal(first.seq, Date.parse('2026-10-30T13:30:00Z') / 1000);
});

test('the workbook is only ever read', () => {
  const h = load();
  h.gs.exportFab();
  h.clock.advance(5);
  h.gs.exportFab();
  assert.deepEqual(h.book.attempts, []);
  assert.ok(h.book.reads.length > 0);
});

test('columns are found by their headers, so an inserted column moves nothing', () => {
  const grid = sheet1().map((row, i) => [...row.slice(0, 3), i === 0 ? 'Room' : `R${i}`, ...row.slice(3)]);
  const h = load({ sheets: { Sheet1: grid } });
  h.gs.exportFab();
  assert.deepEqual(plain(readExport(h).payload.rows), ROWS);
});

test('a header it does not recognise stops the export and leaves the last one alone', () => {
  for (const change of [(g) => { g[0][1] = 'Client'; }, (g) => { g[0][8] = 'Fab'; },
    (g) => { g[0][4] = 'Install Time'; }, (g) => { g[0][9] = 'Designer'; }]) {
    const h = load();
    h.gs.exportFab();
    const before = h.control.text('fab/export.json');
    change(h.book.sheet('Sheet1').grid);
    h.clock.advance(5);
    assert.equal(h.gs.exportFab(), null);
    assert.equal(h.control.text('fab/export.json'), before);
    assert.ok(h.logs.some(([level, text]) => level === 'warn' && /Fab export skipped/.test(text)), JSON.stringify(h.logs));
  }
});

const skipped = (h) => h.logs.filter(([level, text]) => level === 'warn'
  && /Fab export skipped/.test(text));

test('a sheet that returns no rows writes nothing and says why; no Sheet1 is refused', () => {
  const header = sheet1()[0];
  const blank = header.map(() => '');
  const notes = header.map((_, i) => (i === 15 ? 'a note, in no exported column' : ''));
  const spaces = header.map((_, i) => ([1, 2, 3, 4, 8].includes(i) ? '  ' : ''));
  const noJob = header.map((_, i) => ({ 1: ' ', 2: 'PE', 3: 'Friday', 4: ' 7 /17' }[i] || ''));
  // A row with no job name is a blank row to NG Runner (ngrunner/fab.py, diff).
  for (const grid of [[header], [header, blank, blank], [header, notes], [header, spaces],
    [header, noJob]]) {
    const h = load({ sheets: { Sheet1: grid } });
    assert.equal(h.gs.exportFab(), null);
    assert.deepEqual(h.control.names('fab'), []);
    assert.equal(skipped(h).length, 1, JSON.stringify(h.logs));
  }
  const none = load({ sheets: { Dashboard: [['x']] } });
  assert.equal(none.gs.exportFab(), null);
  assert.deepEqual(none.control.names('fab'), []);
});

test('a row with no job name still travels while another row has one', () => {
  const header = sheet1()[0];
  const noJob = header.map((_, i) => ({ 2: 'PE', 3: 'Friday', 4: ' 7 /17' }[i] || ''));
  const h = load({ sheets: { Sheet1: [header, noJob, sheet1()[1]] } });
  h.gs.exportFab();
  assert.deepEqual(plain(readExport(h).payload.rows).map((row) => [row.sheet_row, row.job]),
    [[2, ''], [3, 'Testerson']]);
});

test('a sheet that comes back empty leaves the last export, so the 30-minute alarm says so', () => {
  const h = load();
  h.gs.exportFab();
  const before = h.control.text('fab/export.json');
  const first = readExport(h);
  const whole = h.book.sheet('Sheet1').grid;
  for (const empty of [[whole[0]], whole.map((row, i) => (i === 0 ? row : row.map(() => '')))]) {
    h.book.sheet('Sheet1').grid = empty;
    h.clock.advance(5);
    assert.equal(h.gs.exportFab(), null);
    assert.equal(h.control.text('fab/export.json'), before);
  }
  assert.equal(skipped(h).length, 2, JSON.stringify(h.logs));
  h.book.sheet('Sheet1').grid = whole;                          // the rows are back
  h.clock.advance(5);
  h.gs.exportFab();
  const after = readExport(h);
  assert.equal(after.seq, first.seq + 1);
  assert.equal(after.payload.exported_at, '2026-10-30T13:45:00+00:00');
  assert.deepEqual(plain(after.payload.rows), ROWS);
});

test('the export waits while another run holds the lock', () => {
  const h = load();
  h.lockState.busy = true;
  assert.equal(h.gs.exportFab(), null);
  assert.deepEqual(h.control.names(''), []);
});
