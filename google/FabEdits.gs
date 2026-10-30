/**
 * Barbara's edits to Sheet1's Job Name, Designer and Install Date cells, kept as fab/edits.json
 * (an installable edit trigger on the workbook, made by installTriggers in Config.gs).
 *
 * Sheet1 has no row IDs, so when Barbara corrects a name in place NG Runner must work out that
 * the corrected row is the old row, not a new job (ngrunner/fab.py, diff). This log is a hint
 * for that, never the only evidence: Google gives the old text only for a one-cell edit, row
 * numbers shift when rows are inserted, and an edit lost here costs nothing but the hint. It
 * keeps the last 500 edits, oldest first, and only ever reads the workbook.
 */

var EDITS_KEEP = 500;
var EDITS_MAX_ROWS = 20;   // a bigger block is a sort or a bulk paste, with no old text to give

function onFabEdit(e) {
  if (!e || !e.range) return null;
  var range = e.range;
  var sheet = range.getSheet();
  if (sheet.getName() !== FAB_SHEET) return null;
  if (range.getNumRows() > EDITS_MAX_ROWS) {
    log_('info', 'an edit of ' + range.getNumRows() + ' rows at once was not logged cell by cell');
    return null;
  }
  var layout = fabLayout_(sheet);
  if (layout.problem) {
    log_('warn', 'a Fab edit was not logged: ' + layout.problem);
    return null;
  }
  var entries = editEntries_(e, layout);
  if (!entries.length) return null;
  return withScriptLock_(function () { return appendEdits_(entries); }, 30000);
}

/** One entry per watched cell of the edit, below the header; old text only for a one-cell edit. */
function editEntries_(e, layout) {
  var range = e.range;
  var columns = {};
  columns[layout.job] = 'job';
  columns[layout.designer] = 'designer';
  columns[layout.install[0]] = 'install';
  columns[layout.install[1]] = 'install';
  var firstRow = range.getRow();
  var firstColumn = range.getColumn();
  var single = range.getNumRows() === 1 && range.getNumColumns() === 1;
  var at = isoUtc_(now_());
  var out = [];
  range.getDisplayValues().forEach(function (cells, r) {
    if (firstRow + r <= layout.headerRow) return;
    cells.forEach(function (text, c) {
      var column = columns[firstColumn + c];
      if (!column) return;
      var entry = { at: at, sheet_row: firstRow + r, column: column,
        old: single ? cellText_(e.oldValue) : null, new: cellText_(text) };
      if (single && entry.old === entry.new) return;   // retyped as it was
      out.push(entry);
    });
  });
  return out;
}

function cellText_(value) {
  if (value === undefined || value === null) return null;
  var text = String(value);
  return text.trim() === '' ? null : text;
}

/** The kept log with entries added, rewritten in place; a log that fails its check starts again. */
function appendEdits_(entries) {
  var folder = controlChild_('fab', true);
  var edits = [];
  var files = filesNamed_(folder, 'edits.json');
  if (files.length) {
    files.sort(function (a, b) { return b.getLastUpdated().getTime() - a.getLastUpdated().getTime(); });
    try {
      var payload = readSignedFile_(files[0], 'fab_edits').payload;
      if (!Array.isArray(payload.edits)) throw new Error('it holds no list of edits');
      edits = payload.edits;
    } catch (err) {
      log_('warn', 'the old edit log could not be read (' + err.message + '), so the edit log starts again');
    }
  }
  var kept = edits.concat(entries).slice(-EDITS_KEEP);
  return writeSignedFile_(folder, 'edits.json', 'fab_edits', { edits: kept }, 'replace');
}
