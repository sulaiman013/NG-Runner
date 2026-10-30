/**
 * Daily pruning of the control folder (a time trigger; build spec section 6.4).
 *
 * The control folder would otherwise grow by about 300 heartbeats a day. Files older than 30
 * days in outbox/, receipts/, logs/ and backups/ go, and heartbeats older than 2 days, with each
 * old day folder they leave empty. Age is when the file reached Drive. Files go to Drive's trash
 * (recoverable for 30 days), never straight to deletion, and only files named as NG Runner and
 * this script name them: anything else is left alone and counted in the log. fab/ and pause/ are
 * never touched. The marks Outbox.gs keeps per message are dropped after 31 days, so Script
 * Properties stay small; a message replayed after that is still refused by its sequence number.
 *
 * Pruning takes no lock: it touches only files and marks too old for the other runs to read, so
 * it never holds up the one-minute outbox. It stops after 4 minutes (Apps Script allows 6), and
 * the next day's run carries on.
 */

var PRUNE_DAY_MS = 24 * 3600 * 1000;
var PRUNE_DAYS = 30;
var PRUNE_HEARTBEAT_DAYS = 2;
var PRUNE_MARK_DAYS = 31;
var PRUNE_BUDGET_MS = 4 * 60 * 1000;
var PRUNE_RULES = [
  { folder: 'outbox', name: /^[0-9a-f]{32}\.json$/ },
  { folder: 'receipts', name: /^[0-9a-f]{32}\.json$/ },
  { folder: 'logs', name: /^\d{8}\.txt$/ },
  { folder: 'backups', name: /^\d{8}\.ngrbak$/ },
];

function pruneControlFolder() {
  var now = now_().getTime();
  var report = { started: now, trashed: 0, stopped: false, odd: {} };
  PRUNE_RULES.forEach(function (rule) {
    var folder = controlChild_(rule.folder, false);
    if (folder) pruneFiles_(folder, rule.folder, rule.name, now - PRUNE_DAYS * PRUNE_DAY_MS, report);
  });
  pruneHeartbeats_(now, report);
  pruneMarks_(now - PRUNE_MARK_DAYS * PRUNE_DAY_MS);
  logPrune_(report);
  return report;
}

/** Trashes the files named by nameRe that reached Drive before the given time. */
function pruneFiles_(folder, label, nameRe, before, report) {
  listFolders_(folder).forEach(function () { countOdd_(report, label); });
  listFiles_(folder).forEach(function (file) {
    if (!nameRe.test(file.getName())) {
      countOdd_(report, label);
    } else if (file.getDateCreated().getTime() < before && withinBudget_(report)) {
      file.setTrashed(true);
      report.trashed += 1;
    }
  });
}

function pruneHeartbeats_(now, report) {
  var root = controlChild_('heartbeat', false);
  if (!root) return;
  var before = now - PRUNE_HEARTBEAT_DAYS * PRUNE_DAY_MS;
  var oldDay = pacificDay_(new Date(before));
  listFiles_(root).forEach(function () { countOdd_(report, 'heartbeat'); });
  listFolders_(root).forEach(function (day) {
    if (!DAY_FOLDER_RE.test(day.getName())) {
      countOdd_(report, 'heartbeat');
      return;
    }
    pruneFiles_(day, 'heartbeat', HEARTBEAT_NAME_RE, before, report);
    var empty = !listFiles_(day).length && !listFolders_(day).length;
    if (day.getName() < oldDay && empty && withinBudget_(report)) day.setTrashed(true);
  });
}

function pruneMarks_(before) {
  var props = PropertiesService.getScriptProperties();
  var all = props.getProperties();
  Object.keys(all).forEach(function (key) {
    var mark = key.indexOf(OUTBOX_DONE) === 0 || key.indexOf(OUTBOX_REFUSED) === 0;
    if (mark && Number(all[key]) < before) props.deleteProperty(key);
  });
}

function withinBudget_(report) {
  if (!report.stopped && now_().getTime() - report.started >= PRUNE_BUDGET_MS) report.stopped = true;
  return !report.stopped;
}

function countOdd_(report, label) {
  report.odd[label] = (report.odd[label] || 0) + 1;
}

function logPrune_(report) {
  var odd = Object.keys(report.odd);
  var total = odd.reduce(function (n, k) { return n + report.odd[k]; }, 0);
  var text = 'pruned ' + report.trashed + ' files to the trash';
  if (report.stopped) text += '; pruning stopped after 4 minutes, and the rest goes on the next run';
  if (total) {
    text += '; left alone ' + total + ' items it does not recognise ('
      + odd.map(function (k) { return k + ' ' + report.odd[k]; }).join(', ') + ')';
  }
  log_('info', text);
}
