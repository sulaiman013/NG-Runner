/**
 * NG Runner's Google side: settings, the control folder, the lock and the clock.
 *
 * This project runs under ICLA's automation account (design section 3.1). It reads the NG Fab
 * Schedule and never modifies it (risk C3), and it talks to NG Runner on Jim's PC only through
 * signed files in the control folder (Signing.gs). Everything that differs per install, and
 * every address, token and key, lives in Script Properties (README.md lists them), never in
 * this code, so the code can be reviewed and shared without naming anyone. Time is Pacific
 * (appsscript.json), as Jim reads it.
 *
 * Files load in any order into one global scope, so nothing runs at load time: this file only
 * declares constants and functions. Functions ending in "_" are private (Apps Script hides them
 * from the Run menu and from triggers); the public ones are the trigger handlers and setup.
 */

var TIME_ZONE = 'America/Los_Angeles';
var FAB_SHEET = 'Sheet1';
var STATE_PREFIX = 'state.';

// ----- the clock (the tests replace now_), and Pacific time as Jim reads it

function now_() {
  return new Date();
}

/** The Pacific calendar day, as yyyyMMdd. */
function pacificDay_(date) {
  return Utilities.formatDate(date, TIME_ZONE, 'yyyyMMdd');
}

/** Minutes since Pacific midnight on the wall clock (so 7:00 AM is 420 on either side of DST). */
function pacificMinutes_(date) {
  var hm = Utilities.formatDate(date, TIME_ZONE, 'HH:mm').split(':');
  return Number(hm[0]) * 60 + Number(hm[1]);
}

/** Jim's form of a time: 9:42 PM. */
function pacificClock_(date) {
  return Utilities.formatDate(date, TIME_ZONE, 'h:mm a');
}

/** Jim's form of a day and time: Fri 10/30 9:42 PM. */
function pacificDayClock_(date) {
  return Utilities.formatDate(date, TIME_ZONE, 'EEE M/d h:mm a');
}

// ----- settings: Script Properties, set by Sulaiman at install

function setting_(name, fallback) {
  var value = PropertiesService.getScriptProperties().getProperty(name);
  if (value === null || String(value).trim() === '') return fallback === undefined ? null : fallback;
  return String(value).trim();
}

function requiredSetting_(name) {
  var value = setting_(name);
  if (value === null) throw new Error('the Script Property ' + name + ' is not set (see README.md)');
  return value;
}

function flagSetting_(name) {
  return /^(1|true|yes|on)$/i.test(setting_(name, ''));
}

/** Who the watchdog alerts: sulaiman, or a backup contact while he is away. */
function alertRole_() {
  return setting_('ALERT_ROLE', 'sulaiman').toLowerCase();
}

/** The shadow run (README.md): NG Runner sends everything to Sulaiman, so Jim expects no list. */
function shadowRun_() {
  return flagSetting_('SHADOW');
}

/** Who gets the morning list, so whose digest the watchdog waits for. */
function listRole_() {
  return shadowRun_() ? alertRole_() : 'jim';
}

/** What a role's address is on one channel: ROLE_<ROLE>_EMAIL, _PUSHOVER or _SLACK. */
function roleAddress_(role, what) {
  return setting_('ROLE_' + String(role).toUpperCase() + '_' + what);
}

// ----- state the script keeps for itself (Script Properties under "state.", as JSON)

function state_(name) {
  var raw = PropertiesService.getScriptProperties().getProperty(STATE_PREFIX + name);
  if (raw === null) return null;
  try {
    return JSON.parse(raw);
  } catch (err) {
    log_('warn', 'state ' + name + ' was not readable, so it starts again');
    return null;
  }
}

function setState_(name, value) {
  PropertiesService.getScriptProperties().setProperty(STATE_PREFIX + name, JSON.stringify(value));
}

function clearState_(name) {
  PropertiesService.getScriptProperties().deleteProperty(STATE_PREFIX + name);
}

// ----- the control folder

function controlFolder_() {
  return DriveApp.getFolderById(requiredSetting_('CONTROL_FOLDER_ID'));
}

/** The control folder's child folder name, made when create is true; null when absent. */
function controlChild_(name, create) {
  return childFolder_(controlFolder_(), name, create);
}

function childFolder_(parent, name, create) {
  var found = listFolders_(parent).filter(function (f) { return f.getName() === name; });
  if (found.length > 1) log_('warn', 'the control folder holds ' + found.length + ' folders named ' + name + '; using the oldest');
  if (found.length) {
    found.sort(function (a, b) { return a.getDateCreated().getTime() - b.getDateCreated().getTime(); });
    return found[0];
  }
  return create ? parent.createFolder(name) : null;
}

/** The folder's files that are not in the trash (DriveApp's iterators include trashed ones). */
function listFiles_(folder) {
  var out = [];
  var it = folder.getFiles();
  while (it.hasNext()) {
    var file = it.next();
    if (!file.isTrashed()) out.push(file);
  }
  return out;
}

function listFolders_(folder) {
  var out = [];
  var it = folder.getFolders();
  while (it.hasNext()) {
    var child = it.next();
    if (!child.isTrashed()) out.push(child);
  }
  return out;
}

function filesNamed_(folder, name) {
  return listFiles_(folder).filter(function (f) { return f.getName() === name; });
}

// ----- one run at a time

/** Runs fn holding the script lock, or skips it (returns null) while another run holds it. */
function withScriptLock_(fn, waitMs) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(waitMs || 5000)) {
    log_('info', 'skipped: another run holds the lock');
    return null;
  }
  try {
    return fn();
  } finally {
    lock.releaseLock();
  }
}

// ----- the log (Apps Script's execution log; never an address, a token or a file's content)

function log_(level, text) {
  var write = console[level] || console.log;
  write.call(console, 'NG Runner: ' + scrub_(text));
}

/** Text with anything shaped like an email address taken out, for logs and receipts. */
function scrub_(text) {
  return String(text).replace(/[^\s@<>()"',;:]+@[^\s@<>()"',;:]+/g, '<address>');
}

// ----- setup: run these two from the editor once the Script Properties are set (README.md)

var TRIGGER_HANDLERS = ['exportFab', 'processOutbox', 'runWatchdog', 'pruneControlFolder', 'onFabEdit'];

/**
 * Makes this project's triggers, replacing its own (any others are left alone): the Fab export
 * and the watchdog every 5 minutes, the outbox every minute, pruning daily at 3 AM Pacific, and
 * the edit log on the workbook's edits. Refuses while the setup has a problem.
 */
function installTriggers() {
  var problems = setupProblems_();
  if (problems.length) throw new Error('fix the setup first: ' + problems.join('; '));
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (TRIGGER_HANDLERS.indexOf(trigger.getHandlerFunction()) >= 0) ScriptApp.deleteTrigger(trigger);
  });
  ScriptApp.newTrigger('exportFab').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('processOutbox').timeBased().everyMinutes(1).create();
  ScriptApp.newTrigger('runWatchdog').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('pruneControlFolder').timeBased().everyDays(1).atHour(3).inTimezone(TIME_ZONE).create();
  ScriptApp.newTrigger('onFabEdit').forSpreadsheet(requiredSetting_('SHEET_ID')).onEdit().create();
  log_('info', 'triggers installed: ' + TRIGGER_HANDLERS.join(', '));
  return TRIGGER_HANDLERS.length;
}

/**
 * What is missing or wrong, one plain line each (never a value); empty when all is set. It also
 * says what Google refused to trash on the last prune, which never holds up installTriggers:
 * pruning carries on around a refusal, and only a later prune can clear the line.
 */
function checkSetup() {
  var problems = setupProblems_().concat(pruneProblems_());
  problems.forEach(function (p) { log_('warn', 'setup: ' + p); });
  if (!problems.length) log_('info', 'setup: everything NG Runner needs is set');
  return problems;
}

/** What must be right before the triggers can run. */
function setupProblems_() {
  var problems = [];
  if (Session.getScriptTimeZone() !== TIME_ZONE) {
    problems.push('the project time zone is ' + Session.getScriptTimeZone() + ', not ' + TIME_ZONE + ' (see appsscript.json)');
  }
  try {
    signingKey_();
  } catch (err) {
    problems.push(err.message);
  }
  try {
    controlFolder_();
  } catch (err) {
    problems.push('the control folder could not be opened: ' + err.message);
  }
  return problems.concat(sheetProblems_(), channelProblems_(), hoursProblems_());
}

function sheetProblems_() {
  try {
    var sheet = fabSheet_();
    if (!sheet) return ['the workbook has no ' + FAB_SHEET];
    var layout = fabLayout_(sheet);
    return layout.problem ? [layout.problem] : [];
  } catch (err) {
    return ['the NG Fab Schedule could not be opened: ' + err.message];
  }
}

/** The alert role's channel must be able to send, and Jim's, except during the shadow run. */
function channelProblems_() {
  var wanted = [[alertRole_(), 'email']];
  if (!shadowRun_()) wanted.unshift(['jim', 'email']);
  var problems = [];
  wanted.forEach(function (pair) {
    var role = pair[0];
    var upper = role.toUpperCase();
    var channel = setting_('ROLE_' + upper + '_CHANNEL', pair[1]);
    var needs = { email: ['ROLE_' + upper + '_EMAIL'], pushover: ['ROLE_' + upper + '_PUSHOVER', 'PUSHOVER_TOKEN'],
      slack: ['ROLE_' + upper + '_SLACK', 'SLACK_TOKEN'] }[channel];
    if (!needs) {
      problems.push(role + "'s channel " + channel + ' is not email, slack or pushover');
    } else if (needs.some(function (name) { return setting_(name) === null; })) {
      problems.push(role + ' has no ' + channel + ' set up (Script Propert' + (needs.length > 1 ? 'ies ' : 'y ') + needs.join(' and ') + ')');
    }
  });
  return problems;
}

function hoursProblems_() {
  return ['EXPECTED_ON_FROM', 'EXPECTED_ON_UNTIL'].filter(function (name) {
    return setting_(name) !== null && wallMinutes_(setting_(name)) === null;
  }).map(function (name) { return name + ' is not a time like 06:00'; });
}
