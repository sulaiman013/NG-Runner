/**
 * The cloud watchdog, every 5 minutes (design section 4, "Silence and failure").
 *
 * NG Runner on Jim's PC writes a heartbeat every 5 minutes. A PC that is asleep, off or signed
 * out can't say so itself, so this script says it from outside:
 * - a heartbeat older than 45 minutes, during the hours the PC is expected on, alerts Sulaiman
 *   once per gap (the P1a exit test: a sleep test alerts him within 50 minutes);
 * - no digest receipt by 7:00 AM Pacific sends Jim the "no list today" message, with the open
 *   risks of the last heartbeat. It is sent between 7:00 AM and noon, once a day, never for a PC
 *   declared to sleep at night (its digest comes at first wake). It never says "Nothing is lost",
 *   and it says Sulaiman has been alerted only when he has.
 * Ages are real elapsed time; "7:00 AM" and the expected hours are Pacific wall time, so both
 * hold across the clock changes. A heartbeat dated in the future counts from when it was first
 * seen, so a PC clock that runs fast can't look alive for hours.
 */

var WATCHDOG_STALE_MS = 45 * 60 * 1000;
var WATCHDOG_NO_LIST_FROM = 7 * 60;     // 7:00 AM Pacific
var WATCHDOG_NO_LIST_UNTIL = 12 * 60;   // the "no list" message is a morning message
var WATCHDOG_DAYS = 3;                  // heartbeat day folders looked at, newest first
var WATCHDOG_TRIES = 5;                 // newest heartbeat files tried before giving up
var WATCHDOG_RISKS = 20;
var HEARTBEAT_NAME_RE = /^(\d{1,15})\.json$/;
var DAY_FOLDER_RE = /^\d{8}$/;

function runWatchdog() {
  return withScriptLock_(runWatchdog_, 10000);
}

function runWatchdog_() {
  var now = now_();
  var beat = latestBeat_(now);
  var stale = !beat || now.getTime() - beat.at > WATCHDOG_STALE_MS;
  if (!stale) clearState_('watchdog.alerted');
  var alerted = stale && expectedOn_(now) ? alertGap_(beat, now) : null;
  var noList = noListCheck_(now, beat, stale, alerted);
  return { stale: stale, alerted: alerted, noList: noList };
}

// ----- the heartbeat

/** The last heartbeat accepted, {seq, id, at (ms), risks (null when unreadable)}, or null. */
function latestBeat_(now) {
  var known = state_('watchdog.beat');
  var env = newestHeartbeat_(known ? known.seq : 0);
  if (env) {
    known = { seq: env.seq, id: env.id, at: Math.min(env.issuedAt.getTime(), now.getTime()),
      risks: openRisks_(env.payload) };
    setState_('watchdog.beat', known);
  }
  return known;
}

/** The newest valid heartbeat above aboveSeq, from heartbeat/<YYYYMMDD>/<seq>.json; or null. */
function newestHeartbeat_(aboveSeq) {
  var root = controlChild_('heartbeat', false);
  if (!root) return null;
  var days = listFolders_(root)
    .filter(function (f) { return DAY_FOLDER_RE.test(f.getName()); })
    .sort(function (a, b) { return a.getName() < b.getName() ? 1 : -1; })
    .slice(0, WATCHDOG_DAYS);
  var candidates = [];
  days.forEach(function (day) {
    listFiles_(day).forEach(function (file) {
      var m = HEARTBEAT_NAME_RE.exec(file.getName());
      if (m && Number(m[1]) > aboveSeq) candidates.push({ seq: Number(m[1]), file: file });
    });
  });
  candidates.sort(function (a, b) { return b.seq - a.seq; });
  for (var i = 0; i < Math.min(candidates.length, WATCHDOG_TRIES); i++) {
    try {
      var env = readSignedFile_(candidates[i].file, 'heartbeat');
      if (env.seq === candidates[i].seq) return env;
      log_('warn', 'heartbeat ' + candidates[i].seq + ' was ignored: its name is not its sequence number');
    } catch (err) {
      log_('warn', 'heartbeat ' + candidates[i].seq + ' was ignored: ' + err.message);
    }
  }
  return null;
}

function openRisks_(payload) {
  var risks = payload.open_risks;
  if (!Array.isArray(risks) || !risks.every(function (r) { return typeof r === 'string'; })) return null;
  return risks.slice(0, WATCHDOG_RISKS).map(function (r) { return r.slice(0, 200); });
}

/** Whether Pacific wall time is inside EXPECTED_ON_FROM..EXPECTED_ON_UNTIL (equal: all day). */
function expectedOn_(now) {
  var from = wallMinutes_(setting_('EXPECTED_ON_FROM', '06:00'));
  var until = wallMinutes_(setting_('EXPECTED_ON_UNTIL', '23:00'));
  if (from === null || until === null) {
    log_('error', 'EXPECTED_ON_FROM or EXPECTED_ON_UNTIL is not a time like 06:00, so the PC counts as expected on all day');
    return true;
  }
  if (from === until) return true;
  var wall = pacificMinutes_(now);
  return from < until ? wall >= from && wall < until : wall >= from || wall < until;
}

function wallMinutes_(text) {
  var m = /^(\d{1,2}):(\d{2})$/.exec(String(text));
  if (!m || Number(m[1]) > 24 || Number(m[2]) > 59) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

// ----- telling Sulaiman

/** Alerts Sulaiman about this gap unless he has been already; whether he has been, now or before. */
function alertGap_(beat, now) {
  var gap = beat ? 'beat ' + beat.seq : 'none';
  if (state_('watchdog.alerted') === gap) return true;
  var lead = beat
    ? 'NG Runner has not checked in since ' + when_(beat.at, now) + ' (' + ago_(beat.at, now) + ').'
    : 'NG Runner has not checked in yet.';
  var result = alert_('NG Runner is not checking in', [lead].concat(riskLines_(beat)));
  if (result.ok) setState_('watchdog.alerted', gap);
  return result.ok;
}

function alertNoList_(beat, now, day) {
  if (state_('watchdog.nolist_alerted') === day) return true;
  var result = alert_('No list reached Jim',
    ['NG Runner checked in at ' + when_(beat.at, now) + ', but no list reached Jim by 7:00 AM.']);
  if (result.ok) setState_('watchdog.nolist_alerted', day);
  return result.ok;
}

/** To ALERT_ROLE (sulaiman; a backup contact while he is away), on that role's channel. */
function alert_(subject, lines) {
  var role = setting_('ALERT_ROLE', 'sulaiman');
  var channel = setting_('ROLE_' + role.toUpperCase() + '_CHANNEL', 'email');
  var result = sendTo_(role, channel, subject, lines, true);
  if (!result.ok) log_('warn', 'the watchdog could not alert ' + role + ': ' + result.error);
  return result;
}

// ----- telling Jim

function noListCheck_(now, beat, stale, alerted) {
  if (flagSetting_('PC_SLEEPS_AT_NIGHT')) return null;
  var wall = pacificMinutes_(now);
  if (wall < WATCHDOG_NO_LIST_FROM || wall >= WATCHDOG_NO_LIST_UNTIL) return null;
  var day = pacificDay_(now);
  if (state_('digest.day') === day || state_('watchdog.nolist') === day) return null;
  try {
    processOutbox_();   // a digest may be waiting for the outbox trigger
  } catch (err) {
    log_('warn', 'the outbox could not be checked before telling Jim: ' + err.message);
  }
  if (state_('digest.day') === day) return null;
  if (!stale) alerted = alertNoList_(beat, now, day);
  else if (alerted === null) alerted = alertGap_(beat, now);
  var message = noListMessage_(beat, now, stale, alerted);
  var result = sendTo_('jim', setting_('ROLE_JIM_CHANNEL', 'pushover'), message.subject, message.lines, true);
  if (result.ok) setState_('watchdog.nolist', day);
  else log_('warn', 'Jim could not be told there is no list today: ' + result.error);
  return result;
}

/** Design section 4's words: what is known, what it may mean, and who knows. */
function noListMessage_(beat, now, stale, alerted) {
  var name = setting_('ALERT_NAME', 'Sulaiman');
  var lead;
  if (!beat) {
    lead = 'NG Runner has not checked in, so there is no list today. Your PC may be asleep, off or signed out.';
  } else if (stale) {
    lead = 'NG Runner has not checked in since ' + when_(beat.at, now)
      + ', so there is no list today. Your PC may be asleep, off or signed out.';
  } else {
    lead = 'NG Runner checked in at ' + when_(beat.at, now) + ' but sent no list today.';
  }
  lead += alerted ? ' ' + name + ' has been alerted.' : ' ' + name + ' could not be alerted.';
  return { subject: 'No list today', lines: [lead].concat(riskLines_(beat)) };
}

function riskLines_(beat) {
  if (!beat) return [];
  if (beat.risks === null) return ['1. Its open risks could not be read.'];
  return beat.risks.map(function (risk, i) { return (i + 1) + '. ' + risk; });
}

/** 9:42 PM within the last 12 hours, else Fri 10/30 9:42 PM. */
function when_(atMs, now) {
  var at = new Date(atMs);
  return now.getTime() - atMs < 12 * 3600 * 1000 ? pacificClock_(at) : pacificDayClock_(at);
}

function ago_(atMs, now) {
  var minutes = Math.floor((now.getTime() - atMs) / 60000);
  if (minutes < 120) return minutes + ' minutes ago';
  var hours = Math.round(minutes / 60);
  return hours < 48 ? hours + ' hours ago' : Math.round(hours / 24) + ' days ago';
}
