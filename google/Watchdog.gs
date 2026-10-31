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
 * During the shadow run (the Script Property SHADOW) NG Runner sends everything to Sulaiman, so
 * the digest waited for is the alert role's, and no digest by 7:00 AM tells the alert role, never
 * Jim, who expects no list yet.
 * Ages are real elapsed time; "7:00 AM" and the expected hours are Pacific wall time, so both
 * hold across the clock changes. A heartbeat dated in the future counts from when it was first
 * seen, so a PC clock that runs fast can't look alive for hours.
 *
 * NOT CHECKED from the cloud (P1b build spec 5.7, design 4). With NG Runner silent, nothing on Jim's
 * PC can tell George a nest went unchecked, so in a gap this does: once per gap, the last heartbeat's
 * risks that start "Not checked", and, with NG_JOBS_FOLDER_ID set, any material folder in NG Jobs
 * updated since that heartbeat. It does nothing until the role george has an address, so deploying
 * this changes nothing for a run that hasn't set it. In the shadow run it goes to the alert role,
 * marked, never to George. A gap only counts in the hours the PC is expected on, as for Sulaiman: a PC
 * off overnight is no gap, or George would hear of it every night.
 * UNVERIFIED on real Drive: whether this account can read NG Jobs (checkSetup says), and that a folder's
 * time moves when a program lands in it or is overwritten. A folder walked and not moved says nothing,
 * so a re-post over an old file may be missed here; the digest says so when the PC returns.
 */

var SHADOW_TITLE = 'SHADOW ';           // as NG Runner marks what it sends Sulaiman in Jim's place
var WATCHDOG_STALE_MS = 45 * 60 * 1000;
var WATCHDOG_NO_LIST_FROM = 7 * 60;     // 7:00 AM Pacific
var WATCHDOG_NO_LIST_UNTIL = 12 * 60;   // the "no list" message is a morning message
var WATCHDOG_DAYS = 3;                  // heartbeat day folders looked at, newest first
var WATCHDOG_TRIES = 5;                 // newest heartbeat files tried before giving up
var WATCHDOG_RISKS = 20;
var WATCHDOG_WALK_EVERY_MS = 30 * 60 * 1000;   // a walk costs a Drive call per job, and quota is daily
var WATCHDOG_WALK_BUDGET_MS = 3 * 60 * 1000;   // an Apps Script run has 6 minutes in all
var WATCHDOG_NOT_CHECKED_LINES = 20;
var NOT_CHECKED_SUBJECT = 'NOT CHECKED \u00b7 NG Runner is offline';
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
  var inGap = stale && expectedOn_(now);
  var alerted = inGap ? alertGap_(beat, now) : null;
  var george = inGap ? notCheckedCheck_(beat, now) : null;
  var noList = noListCheck_(now, beat, stale, alerted);
  return { stale: stale, alerted: alerted, noList: noList, george: george };
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
  var role = alertRole_();
  var channel = setting_('ROLE_' + role.toUpperCase() + '_CHANNEL', 'email');
  var result = sendTo_(role, channel, subject, lines, true);
  if (!result.ok) log_('warn', 'the watchdog could not alert ' + role + ': ' + result.error);
  return result;
}

// ----- telling George: NOT CHECKED, while NG Runner can't

/** What this run has for him; {ok, error} when there was something to send, else null. */
function notCheckedCheck_(beat, now) {
  if (!beat || !roleAddress_('george', 'EMAIL')) return null;
  var gap = 'beat ' + beat.seq;
  var kept = state_('watchdog.george');
  var told = kept && kept.gap === gap ? kept : { gap: gap, risks: false, through: beat.at, walked: 0 };
  var risks = told.risks ? [] : notCheckedRisks_(beat);
  var walk = walkIsDue_(told, now) ? postedSince_(told.through, now) : null;
  if (walk) told.walked = now.getTime();
  var posted = walk ? walk.posted : [];
  var lines = notCheckedLines_(risks, posted, now);
  var result = lines.length ? tellGeorge_(lines) : null;
  if (!result || result.ok) {
    told.risks = true;
    if (walk && walk.whole) told.through = newestPost_(posted, told.through);
  }
  setState_('watchdog.george', told);
  return result;
}

function notCheckedRisks_(beat) {
  return (beat.risks || []).filter(function (risk) { return /^Not checked/.test(risk); });
}

function walkIsDue_(told, now) {
  return setting_('NG_JOBS_FOLDER_ID') !== null
    && now.getTime() - told.walked >= WATCHDOG_WALK_EVERY_MS;
}

/** One line each, numbered, as a person reads them; a long list is held to a few and a count. */
function notCheckedLines_(risks, posted, now) {
  var lines = risks.concat(posted.map(function (post) {
    return post.job + ': NOT CHECKED ' + materialWords_(post.material) + ', posted ' + when_(post.at, now)
      + '. NG Runner is offline.';
  }));
  var shown = lines.slice(0, WATCHDOG_NOT_CHECKED_LINES);
  if (lines.length > shown.length) {
    shown.push((lines.length - shown.length) + ' more were posted while NG Runner was offline.');
  }
  return shown.map(function (line, i) { return (i + 1) + '. ' + line; });
}

function tellGeorge_(lines) {
  if (shadowRun_()) return alert_(SHADOW_TITLE + NOT_CHECKED_SUBJECT, lines);
  var result = sendTo_('george', setting_('ROLE_GEORGE_CHANNEL', 'email'), NOT_CHECKED_SUBJECT, lines, true);
  if (!result.ok) log_('warn', 'the watchdog could not tell george: ' + result.error);
  return result;
}

/**
 * The material folders in NG Jobs updated after a time, oldest first: {posted, whole}, or null when
 * NG Jobs can't be read at all, which is logged and costs George only these lines. Old isn't watched,
 * as on Jim's PC. whole: it got through all of them. One that didn't says what it found, and the next
 * walk looks again from the same time, so a line may be said twice and is never lost.
 */
function postedSince_(afterMs, now) {
  var root;
  try {
    root = DriveApp.getFolderById(setting_('NG_JOBS_FOLDER_ID'));
  } catch (err) {
    log_('warn', 'NG Jobs could not be read, so posts while NG Runner is offline are not looked for: ' + err.message);
    return null;
  }
  var posted = [];
  var whole = true;
  var began = now_().getTime();
  try {
    listFolders_(root).forEach(function (job) {
      if (job.getName().toLowerCase() === 'old') return;
      if (now_().getTime() - began >= WATCHDOG_WALK_BUDGET_MS) throw new Error('the walk ran out of time');
      listFolders_(job).forEach(function (material) {
        var at = material.getLastUpdated().getTime();
        if (at > afterMs) posted.push({ job: job.getName(), material: material.getName(), at: at });
      });
    });
  } catch (err) {
    whole = false;
    log_('warn', 'a walk of NG Jobs stopped early: ' + err.message);
  }
  posted.sort(function (a, b) { return a.at - b.at; });
  return { posted: posted, whole: whole };
}

function newestPost_(posted, through) {
  return posted.reduce(function (newest, post) { return Math.max(newest, post.at); }, through);
}

/** A material folder as Jim reads it, as NG Runner's own messages say it: 3/4 Classic White. */
function materialWords_(name) {
  return String(name)
    .replace(/([A-Za-z0-9])[-_](?=[A-Za-z])|_/g, function (match, before) { return before ? before + ' ' : ' '; })
    .split(/\s+/).join(' ').trim()
    .replace(/^([0-9])-([0-9])(?= )/, '$1/$2');
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
  if (shadowRun_()) return shadowNoList_(beat, now, stale, day);
  if (!stale) alerted = alertNoList_(beat, now, day);
  else if (alerted === null) alerted = alertGap_(beat, now);
  var message = noListMessage_(beat, now, stale, alerted);
  var channel = setting_('ROLE_JIM_CHANNEL', 'email');
  var result = sendTo_('jim', channel, message.subject, message.lines, true);
  if (result.ok) setState_('watchdog.nolist', day);
  else log_('warn', 'Jim could not be told there is no list today: ' + result.error);
  return result;
}

/** The shadow run's morning with no list: the alert role hears it, once a day; Jim never does. */
function shadowNoList_(beat, now, stale, day) {
  var lines = [noListLead_(beat, now, stale, "Jim's PC")].concat(riskLines_(beat));
  var result = alert_(SHADOW_TITLE + 'No list today', lines);
  if (result.ok) setState_('watchdog.nolist', day);
  return result;
}

/** Design section 4's words: what is known, what it may mean, and who knows. */
function noListMessage_(beat, now, stale, alerted) {
  var name = setting_('ALERT_NAME', 'Sulaiman');
  var lead = noListLead_(beat, now, stale, 'Your PC');
  lead += alerted ? ' ' + name + ' has been alerted.' : ' ' + name + ' could not be alerted.';
  return { subject: 'No list today', lines: [lead].concat(riskLines_(beat)) };
}

/** What is known and what it may mean, naming the PC as this reader would. */
function noListLead_(beat, now, stale, pc) {
  var mayBe = ' ' + pc + ' may be asleep, off or signed out.';
  if (!beat) return 'NG Runner has not checked in, so there is no list today.' + mayBe;
  if (!stale) return 'NG Runner checked in at ' + when_(beat.at, now) + ' but sent no list today.';
  return 'NG Runner has not checked in since ' + when_(beat.at, now)
    + ', so there is no list today.' + mayBe;
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
