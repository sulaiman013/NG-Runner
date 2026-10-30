/**
 * Sheet1 to fab/export.json, every 5 minutes (a time trigger; design section 3.1).
 *
 * NG Runner can't read the native Google Sheet from Jim's PC (G: holds only a .gsheet stub), so
 * this exports what Barbara sees: the display values of Job Name, Designer, the two Install Date
 * cells and Fab Date, with each row's number (NG Runner normalises them; build spec section 3).
 * Columns are found by their headers rather than by letter, so an inserted column moves nothing.
 * A header row it does not recognise stops the export instead of letting it guess: the last
 * export stays, and NG Runner's "Fab export is over 30 minutes old" alarm says so. A sheet that
 * returns no rows with a job name stops it the same way, since read as an export it would make
 * every row look gone, and every row look new when they came back. The file is rewritten every
 * run even when nothing changed, because its time is how NG Runner knows this bridge is alive.
 * The workbook is only read, never changed (risk C3).
 */

var FAB_HEADERS = { job: 'job name', designer: 'designer', install: 'install date', fabDate: 'fab date' };
var FAB_HEADER_SEARCH_ROWS = 10;

function exportFab() {
  return withScriptLock_(exportFab_, 30000);
}

function exportFab_() {
  var sheet = fabSheet_();
  if (!sheet) return skipExport_('the workbook has no ' + FAB_SHEET);
  var layout = fabLayout_(sheet);
  if (layout.problem) return skipExport_(layout.problem);
  var rows = fabRows_(sheet, layout);
  var named = rows.filter(function (row) { return row.job.trim() !== ''; });
  if (!named.length) return skipExport_(FAB_SHEET + ' returned no rows with a job name');
  var payload = { sheet: FAB_SHEET, exported_at: isoUtc_(now_()), rows: rows };
  return writeSignedFile_(controlChild_('fab', true), 'export.json', 'fab_export', payload, 'replace');
}

function skipExport_(reason) {
  log_('warn', 'Fab export skipped: ' + reason);
  return null;
}

/** Sheet1, or null when the workbook has none. */
function fabSheet_() {
  return SpreadsheetApp.openById(requiredSetting_('SHEET_ID')).getSheetByName(FAB_SHEET);
}

/**
 * Where Sheet1's columns are, from its header row: {headerRow, job, designer, install: [c, c+1],
 * fabDate, width}, 1-based; or {problem} in plain words. Install Date is one header merged over
 * two cells, so the cell beside it in the header row must be blank.
 */
function fabLayout_(sheet) {
  var lastRow = sheet.getLastRow();
  var lastColumn = sheet.getLastColumn();
  if (lastRow < 1 || lastColumn < 1) return { problem: FAB_SHEET + ' is empty' };
  var top = sheet.getRange(1, 1, Math.min(FAB_HEADER_SEARCH_ROWS, lastRow), lastColumn).getDisplayValues();
  for (var r = 0; r < top.length; r++) {
    var cells = top[r].map(headerText_);
    if (cells.indexOf(FAB_HEADERS.job) >= 0) return layoutFromHeader_(cells, r + 1);
  }
  return { problem: 'no Job Name header in the first ' + FAB_HEADER_SEARCH_ROWS + ' rows of ' + FAB_SHEET };
}

function layoutFromHeader_(cells, headerRow) {
  var layout = { headerRow: headerRow };
  var names = Object.keys(FAB_HEADERS);
  for (var i = 0; i < names.length; i++) {
    var want = FAB_HEADERS[names[i]];
    var found = [];
    cells.forEach(function (text, c) { if (text === want) found.push(c + 1); });
    if (found.length !== 1) {
      return { problem: (found.length ? 'more than one' : 'no') + ' "' + want + '" header in row ' + headerRow + ' of ' + FAB_SHEET };
    }
    layout[names[i]] = found[0];
  }
  var beside = cells[layout.install];   // the header cell right of Install Date (0-based index)
  if (beside !== undefined && beside !== '') {
    return { problem: 'the header beside Install Date in row ' + headerRow + ' is not blank, so the date is not two cells' };
  }
  layout.install = [layout.install, layout.install + 1];
  layout.width = Math.max(layout.job, layout.designer, layout.install[1], layout.fabDate);
  return layout;
}

function headerText_(text) {
  return String(text).replace(/\s+/g, ' ').trim().toLowerCase();
}

/** Every row below the header with anything in the exported cells, as shown. */
function fabRows_(sheet, layout) {
  var first = layout.headerRow + 1;
  var last = sheet.getLastRow();
  if (last < first) return [];
  var values = sheet.getRange(first, 1, last - first + 1, layout.width).getDisplayValues();
  var rows = [];
  values.forEach(function (cells, i) {
    var row = {
      sheet_row: first + i,
      job: String(cells[layout.job - 1]),
      designer: String(cells[layout.designer - 1]),
      install: [String(cells[layout.install[0] - 1]), String(cells[layout.install[1] - 1])],
      fab_date: String(cells[layout.fabDate - 1]),
    };
    var shown = [row.job, row.designer, row.install[0], row.install[1], row.fab_date];
    if (shown.some(function (v) { return v.trim() !== ''; })) rows.push(row);
  });
  return rows;
}
