/**
 * Daily pruning of the control folder (a time trigger; build spec section 6.4).
 *
 * The control folder would otherwise grow by about 300 heartbeats a day. Files older than 30
 * days in outbox/, receipts/, logs/, backups/ and support/ go, and heartbeats older than 2 days,
 * with each old day folder they leave empty. So do files older than 30 days in shadow/<folder>/,
 * with each old job folder they leave empty, since the shadow run's copies hold client PDFs and
 * must not outlive the run by long. Age is when the file reached Drive. Files go to Drive's trash
 * (recoverable for 30 days), never straight to deletion, and only files named as NG Runner and
 * this script name them: anything else is left alone and counted in the log. fab/ and pause/ are
 * never touched. The marks Outbox.gs keeps per message are dropped after 31 days, so Script
 * Properties stay small; a message replayed after that is still refused by its sequence number.
 *
 * Google may refuse to trash a file: its reference says only the owner may, and NG Runner's files
 * reach Drive under Jim's account (README.md, Setup step 6). A refused item stays and is counted,
 * the run carries on with the rest, and the next run tries it again. Any error from setTrashed
 * counts, since Google's words for a refusal are not known yet. The last run's count goes in the
 * log and in state.prune.refused, and checkSetup says it in plain words. Refused files pile up, so
 * the marks go first, and a run out of time stops walking the folders: otherwise Google would end
 * it at 6 minutes, before it could log or keep the count.
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
var PRUNE_REFUSED = 'prune.refused';   // state: what Google refused on the last run
var PRUNE_RULES = [
  { folder: 'outbox', name: /^[0-9a-f]{32}\.json$/ },
  { folder: 'receipts', name: /^[0-9a-f]{32}\.json$/ },
  { folder: 'logs', name: /^\d{8}\.txt$/ },
  { folder: 'backups', name: /^\d{8}\.ngrbak$/ },
  { folder: 'support', name: /^support-\d{8}-\d{6}\.zip$/ },
  { folder: 'shadow', jobFolders: true },
];

function pruneControlFolder() {
  var now = now_().getTime();
  var before = now - PRUNE_DAYS * PRUNE_DAY_MS;
  var report = { started: now, trashed: 0, stopped: false, odd: {}, refused: {}, refusal: null };
  pruneMarks_(now - PRUNE_MARK_DAYS * PRUNE_DAY_MS);
  PRUNE_RULES.forEach(function (rule) {
    if (!withinBudget_(report)) return;
    var folder = controlChild_(rule.folder, false);
    if (!folder) return;
    if (rule.jobFolders) pruneJobFolders_(folder, rule.folder, before, report);
    else pruneFiles_(folder, rule.folder, rule.name, before, report);
  });
  pruneHeartbeats_(now, report);
  logPrune_(report);
  keepRefused_(report);
  return report;
}

/** Trashes the files named by nameRe that reached Drive before the given time. */
function pruneFiles_(folder, label, nameRe, before, report) {
  listFolders_(folder).forEach(function () { countOdd_(report, label); });
  listFiles_(folder).forEach(function (file) {
    if (!nameRe.test(file.getName())) {
      countOdd_(report, label);
    } else if (file.getDateCreated().getTime() < before && withinBudget_(report)) {
      if (trash_(file, label, report)) report.trashed += 1;
    }
  });
}

/** Puts one file or folder in the trash; whether Google let it. A refusal is counted, never raised. */
function trash_(item, label, report) {
  try {
    item.setTrashed(true);
    return true;
  } catch (err) {
    report.refused[label] = (report.refused[label] || 0) + 1;
    if (report.refusal === null) report.refusal = err.message;
    return false;
  }
}

/** Each job folder under root: its own files from before the time, then the folder if now empty. */
function pruneJobFolders_(root, label, before, report) {
  listFiles_(root).forEach(function () { countOdd_(report, label); });
  listFolders_(root).forEach(function (job) {
    if (!withinBudget_(report)) return;
    pruneFiles_(job, label, jobFileRe_(job.getName()), before, report);
    var empty = !listFiles_(job).length && !listFolders_(job).length;
    var old = job.getDateCreated().getTime() < before;
    if (empty && old && withinBudget_(report)) trash_(job, label, report);
  });
}

/** What commit_write names in a job folder (ngrunner/writes.py), or a crash's temp file of one. */
function jobFileRe_(folderName) {
  var name = folderName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp('^' + name + '_(?:summary\\.pdf|NGRunner\\.json|NGRunner_log\\.txt)'
    + '(?:\\.[0-9a-f]{8}\\.ngr-tmp)?$');
}

function pruneHeartbeats_(now, report) {
  if (!withinBudget_(report)) return;
  var root = controlChild_('heartbeat', false);
  if (!root) return;
  var before = now - PRUNE_HEARTBEAT_DAYS * PRUNE_DAY_MS;
  var oldDay = pacificDay_(new Date(before));
  listFiles_(root).forEach(function () { countOdd_(report, 'heartbeat'); });
  listFolders_(root).forEach(function (day) {
    if (!withinBudget_(report)) return;
    if (!DAY_FOLDER_RE.test(day.getName())) {
      countOdd_(report, 'heartbeat');
      return;
    }
    pruneFiles_(day, 'heartbeat', HEARTBEAT_NAME_RE, before, report);
    var empty = !listFiles_(day).length && !listFolders_(day).length;
    if (day.getName() < oldDay && empty && withinBudget_(report)) trash_(day, 'heartbeat', report);
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
  var total = countTotal_(report.odd);
  var text = 'pruned ' + report.trashed + ' files to the trash';
  if (report.stopped) text += '; pruning stopped after 4 minutes, and the rest goes on the next run';
  if (total) {
    text += '; left alone ' + total + ' items it does not recognise (' + countList_(report.odd) + ')';
  }
  log_('info', text);
  if (countTotal_(report.refused)) {
    log_('warn', refusedText_(report.refused) + '; the first time it said: ' + report.refusal);
  }
}

/**
 * Keeps what Google refused for checkSetup, or clears it after a run with no refusal. A run out of
 * time did not reach everything, so it can raise the count but not lower it.
 */
function keepRefused_(report) {
  var count = countTotal_(report.refused);
  var kept = state_(PRUNE_REFUSED);
  if (report.stopped && kept && kept.count >= count) return;
  if (count) setState_(PRUNE_REFUSED, { count: count, at: report.started, where: report.refused });
  else clearState_(PRUNE_REFUSED);
}

/** For checkSetup: what Google refused on the last run, in plain words; empty when it refused nothing. */
function pruneProblems_() {
  var kept = state_(PRUNE_REFUSED);
  if (!kept) return [];
  return ['pruning on ' + pacificDayClock_(new Date(kept.at)) + ': ' + refusedText_(kept.where)
    + ' (see README.md, Setup step 6)'];
}

function refusedText_(where) {
  var count = countTotal_(where);
  return 'Google refused to trash ' + count + ' old item' + (count === 1 ? '' : 's')
    + ' (' + countList_(where) + '), and what it refuses stays in the control folder';
}

function countTotal_(counts) {
  return Object.keys(counts).reduce(function (n, k) { return n + counts[k]; }, 0);
}

function countList_(counts) {
  return Object.keys(counts).map(function (k) { return k + ' ' + counts[k]; }).join(', ');
}
