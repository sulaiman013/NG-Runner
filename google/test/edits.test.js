'use strict';
// FabEdits.gs: Barbara's edits to Job Name, Designer and Install Date become fab/edits.json.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { load, plain, hexToSigned, outDir, KEY_HEX } = require('./harness');

const KEY = hexToSigned(KEY_HEX);

function edits(h) {
  return h.gs.openEnvelope_(h.control.text('fab/edits.json'), KEY, 'fab_edits');
}

test('a corrected job name is logged with its old and new text', () => {
  const h = load();
  h.gs.onFabEdit(h.book.edit('Sheet1', 2, 2, 'Testerson closet'));
  assert.deepEqual(plain(edits(h).payload), { edits: [
    { at: '2026-10-30T13:30:00+00:00', sheet_row: 2, column: 'job', old: 'Testerson', new: 'Testerson closet' }] });
  const out = outDir();
  if (out) fs.writeFileSync(path.join(out, 'edits.json'), h.control.text('fab/edits.json'), 'utf8');
});

test('designer and both install date cells are logged; other columns are not', () => {
  const h = load();
  h.gs.onFabEdit(h.book.edit('Sheet1', 3, 3, 'QX'));
  h.clock.advance(1);
  h.gs.onFabEdit(h.book.edit('Sheet1', 3, 4, 'Thursday'));
  h.gs.onFabEdit(h.book.edit('Sheet1', 3, 5, ' 7 /9'));
  h.gs.onFabEdit(h.book.edit('Sheet1', 3, 6, 'x'));            // Designed
  h.gs.onFabEdit(h.book.edit('Sheet1', 3, 16, 'note'));        // Notes
  assert.deepEqual(plain(edits(h).payload.edits).map((e) => [e.column, e.old, e.new, e.at]), [
    ['designer', 'QA', 'QX', '2026-10-30T13:30:00+00:00'],
    ['install', 'Wednesday', 'Thursday', '2026-10-30T13:31:00+00:00'],
    ['install', ' 7 /8', ' 7 /9', '2026-10-30T13:31:00+00:00'],
  ]);
});

test('edits in other tabs, in the header row, or that change nothing are not logged', () => {
  const h = load({ sheets: { Sheet1: require('./harness').sheet1(), Archive: [['', 'Job Name'], ['', 'Testerson']], Requests: [['x']] } });
  h.gs.onFabEdit(h.book.edit('Archive', 2, 2, 'Testerson 2'));
  h.gs.onFabEdit(h.book.edit('Requests', 1, 1, 'y'));
  h.gs.onFabEdit(h.book.edit('Sheet1', 1, 2, 'Job Name'));
  h.gs.onFabEdit(h.book.edit('Sheet1', 2, 2, 'Testerson'));    // retyped as it was
  h.gs.onFabEdit({});
  assert.deepEqual(h.control.names('fab'), []);
});

test('a pasted block is logged cell by cell, old text unknown, a blank cell as null', () => {
  const h = load();
  h.gs.onFabEdit(h.book.edit('Sheet1', 4, 2, [['Mockford', 'ZT', 'Friday', ' 7 /24'], ['', '', '', '']]));
  assert.deepEqual(plain(edits(h).payload.edits).map((e) => [e.sheet_row, e.column, e.old, e.new]), [
    [4, 'job', null, 'Mockford'], [4, 'designer', null, 'ZT'], [4, 'install', null, 'Friday'],
    [4, 'install', null, ' 7 /24'], [5, 'job', null, null], [5, 'designer', null, null],
    [5, 'install', null, null], [5, 'install', null, null],
  ]);
});

test('a cleared cell is logged with no new text', () => {
  const h = load();
  h.gs.onFabEdit(h.book.edit('Sheet1', 2, 2, ''));
  assert.deepEqual(plain(edits(h).payload.edits).map((e) => [e.old, e.new]), [['Testerson', null]]);
});

test('the log keeps the last 500 edits, replaced in place with a new sequence number each time', () => {
  const h = load();
  let seq = null;
  for (let k = 0; k < 7; k++) {                                 // 7 pastes of 20 rows x 4 watched cells
    const block = Array.from({ length: 20 }, (_, r) => [`Testerson ${k}-${r}`, 'PE', 'Friday', ` 8 /${r + 1}`]);
    h.gs.onFabEdit(h.book.edit('Sheet1', 2, 2, block));
    const env = edits(h);
    if (seq !== null) assert.equal(env.seq, seq + 1);
    seq = env.seq;
  }
  const kept = plain(edits(h).payload.edits);
  assert.equal(kept.length, 500);                               // of 560: the oldest 60 are gone
  assert.deepEqual([kept[0].column, kept[0].new], ['job', 'Testerson 0-15']);
  assert.deepEqual([kept.at(-1).column, kept.at(-1).sheet_row, kept.at(-1).new], ['install', 21, ' 8 /20']);
  assert.equal(h.control.live('fab/edits.json').length, 1);
});

test('an edit log it cannot verify is started again, and the reason logged', () => {
  const h = load();
  h.gs.onFabEdit(h.book.edit('Sheet1', 2, 2, 'Testerson closet'));
  const file = h.control.live('fab/edits.json')[0];
  file.content = file.content.replace('Testerson closet', 'Mockford closet');
  h.gs.onFabEdit(h.book.edit('Sheet1', 3, 2, 'Fakeley'));
  assert.deepEqual(plain(edits(h).payload.edits).map((e) => e.new), ['Fakeley']);
  assert.ok(h.logs.some(([level, text]) => level === 'warn' && /edit log .*starts again/.test(text)));
});

test('a block over 20 rows (a sort, a big paste) is not logged cell by cell', () => {
  const h = load();
  const block = Array.from({ length: 21 }, (_, i) => [`Row ${i}`]);
  h.gs.onFabEdit(h.book.edit('Sheet1', 2, 2, block));
  assert.deepEqual(h.control.names('fab'), []);
  assert.ok(h.logs.some(([, text]) => /21 rows/.test(text)));
});

test('the edit log only ever reads the workbook', () => {
  const h = load();
  h.gs.onFabEdit(h.book.edit('Sheet1', 2, 2, 'Testerson closet'));
  h.gs.onFabEdit(h.book.edit('Sheet1', 4, 2, [['Mockford', 'ZT']]));
  assert.deepEqual(h.book.attempts, []);
});

test('an edit that finds the lock held is dropped with a log line, never written half-way', () => {
  const h = load();
  h.lockState.busy = true;
  h.gs.onFabEdit(h.book.edit('Sheet1', 2, 2, 'Testerson closet'));
  assert.deepEqual(h.control.names('fab'), []);
  assert.ok(h.logs.some(([, text]) => /another run holds the lock/.test(text)));
});
