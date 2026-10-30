/**
 * outbox/<id>.json to email, Slack or Pushover, every minute, with a receipt for each (design
 * section 4, "Receipts"; build spec section 3).
 *
 * NG Runner writes each message as a signed file that names a role (jim, barbara, sulaiman...),
 * never an address: this script resolves the role through its own Script Properties, sends, and
 * writes receipts/<outbox id>.json. A message goes out at most once. Its sequence number and id
 * are recorded before it is sent, so a run that dies half-way can lose a message but never
 * repeat one, and a lost one shows on Jim's PC as a missing receipt (the tray turns red within
 * 5 minutes and urgent items go straight to Pushover). Files are taken in sequence order. One
 * that arrives ahead of an earlier one (Drive for desktop uploads in any order) waits up to 2
 * minutes for it; then the gap is given up, and the late one is refused as any replay is.
 * Receipts and logs say what went wrong in plain words, never with an address or a token.
 */

var OUTBOX_CHANNELS = ['email', 'slack', 'pushover'];
var OUTBOX_KEYS = ['channel', 'lines', 'ref', 'subject', 'to', 'urgent'];
var OUTBOX_NAME_RE = /^([0-9a-f]{32})\.json$/;
var OUTBOX_GAP_WAIT_MS = 2 * 60 * 1000;
var OUTBOX_OWED_MAX = 30;          // receipts kept to write later, while Drive refuses them
var OUTBOX_DONE = 'done.';         // Script Property per accepted message: done.<id> = its time
var OUTBOX_REFUSED = 'refused.';   // per refused file: refused.<id> = its time (read, logged once)
var ROLE_RE = /^[a-z][a-z0-9_]{0,39}$/;
var DIGEST_REF_RE = /^digest(:|$)/;   // the digest's ref, so the watchdog knows it went out
var PUSHOVER_URL = 'https://api.pushover.net/1/messages.json';
var PUSHOVER_MAX = 1024;
var PUSHOVER_TAIL = 'The rest is on the board.';
var SLACK_URL = 'https://slack.com/api/chat.postMessage';

function processOutbox() {
  return withScriptLock_(processOutbox_, 5000);
}

function processOutbox_() {
  signingKey_();   // a missing key stops the run here, before any file could be refused for it
  flushOwedReceipts_();
  var outbox = controlChild_('outbox', false);
  if (!outbox) return [];
  var waiting = readOutbox_(outbox);
  var sent = [];
  for (var i = 0; i < waiting.length; i++) {
    var env = waiting[i];
    var last = lastSeqIn_('outbox');
    if (env.seq <= last) {
      refuse_(env.id, 'its sequence number is not above the last one accepted (' + env.seq + ' after ' + last + ')');
      continue;
    }
    if (last > 0 && env.seq > last + 1 && !gapGivenUp_(env.seq)) break;   // an earlier one may be on its way
    accept_(env);
    sent.push(sendOutbox_(env));
  }
  return sent;
}

/** The outbox files not yet accepted or refused, checked, in sequence order. */
function readOutbox_(folder) {
  var marks = PropertiesService.getScriptProperties().getProperties();
  var out = [];
  listFiles_(folder).forEach(function (file) {
    var m = OUTBOX_NAME_RE.exec(file.getName());
    if (!m) return;   // not one of NG Runner's outbox files: never read
    var id = m[1];
    if (marks[OUTBOX_DONE + id] || marks[OUTBOX_REFUSED + id]) return;
    var env;
    try {
      env = readSignedFile_(file, 'outbox');
    } catch (err) {
      refuse_(id, err.message);
      return;
    }
    if (env.id !== id) refuse_(id, 'its file name is not its id');
    else out.push(env);
  });
  return out.sort(function (a, b) { return a.seq - b.seq; });
}

function refuse_(id, reason) {
  PropertiesService.getScriptProperties().setProperty(OUTBOX_REFUSED + id, String(now_().getTime()));
  log_('warn', 'outbox ' + id + ' refused, not sent: ' + reason);
}

/** Whether the gap before seq has waited long enough to be given up. */
function gapGivenUp_(seq) {
  var gap = state_('outbox.gap');
  var now = now_().getTime();
  if (!gap || gap.seq !== seq) {
    setState_('outbox.gap', { seq: seq, since: now });
    return false;
  }
  return now - gap.since >= OUTBOX_GAP_WAIT_MS;
}

/** Recorded before sending: from here on this message is never sent again, whatever happens. */
function accept_(env) {
  acceptSeqIn_('outbox', env.seq);
  PropertiesService.getScriptProperties().setProperty(OUTBOX_DONE + env.id, String(now_().getTime()));
  clearState_('outbox.gap');
}

function sendOutbox_(env) {
  var payload = env.payload;
  var sentAt = now_();
  var result;
  try {
    result = deliver_(payload);
  } catch (err) {
    log_('warn', 'outbox ' + env.id + ' failed unexpectedly: ' + err.message);
    result = { ok: false, error: 'the message could not be sent (an unexpected error)' };
  }
  if (result.ok && payload.to === 'jim' && DIGEST_REF_RE.test(payload.ref)) setState_('digest.day', pacificDay_(sentAt));
  var channel = typeof payload.channel === 'string' ? payload.channel : '';
  writeReceipt_({ outbox_id: env.id, sent_at: isoUtc_(sentAt), channel: channel, ok: result.ok,
    error: result.ok ? null : result.error });
  log_(result.ok ? 'info' : 'warn', 'outbox ' + env.id + ' (' + channel + ' to ' + payload.to + '): '
    + (result.ok ? 'sent' : result.error));
  return { id: env.id, ok: result.ok, error: result.error };
}

// ----- the payload and its channel

function deliver_(payload) {
  var problem = outboxProblem_(payload);
  if (problem) return { ok: false, error: 'the message was not in the expected form: ' + problem };
  return sendTo_(payload.to, payload.channel, payload.subject, payload.lines, payload.urgent);
}

function outboxProblem_(p) {
  if (!sameKeys_(p, OUTBOX_KEYS)) return 'it does not hold exactly channel, to, subject, lines, urgent and ref';
  if (OUTBOX_CHANNELS.indexOf(p.channel) < 0) return 'its channel is not email, slack or pushover';
  if (typeof p.to !== 'string' || !ROLE_RE.test(p.to)) return 'it does not name a role';
  if (typeof p.subject !== 'string') return 'its subject is not text';
  if (!Array.isArray(p.lines) || !p.lines.every(function (l) { return typeof l === 'string'; })) {
    return 'its lines are not a list of text';
  }
  if (typeof p.urgent !== 'boolean') return 'urgent is not true or false';
  if (typeof p.ref !== 'string') return 'its ref is not text';
  return null;
}

/** Sends to a role on one channel: {ok, error}, error in plain words. Also used by Watchdog.gs. */
function sendTo_(role, channel, subject, lines, urgent) {
  if (channel === 'email') return sendEmail_(role, subject, lines.join('\n'));
  if (channel === 'pushover') return sendPushover_(role, subject, lines, urgent);
  if (channel === 'slack') return sendSlack_(role, subject, lines.join('\n'));
  return { ok: false, error: 'there is no channel called ' + channel };
}

function sendEmail_(role, subject, body) {
  var to = roleAddress_(role, 'EMAIL');
  if (!to) return { ok: false, error: 'no email address is set for ' + role };
  if (MailApp.getRemainingDailyQuota() < 1) return { ok: false, error: 'the daily email limit is used up' };
  try {
    MailApp.sendEmail({ to: to, subject: subject, body: body, name: 'NG Runner' });
    return { ok: true, error: null };
  } catch (err) {
    log_('warn', 'an email to ' + role + ' failed: ' + err.message);   // log_ takes addresses out
    return { ok: false, error: 'the email could not be sent' };
  }
}

function sendPushover_(role, title, lines, urgent) {
  var token = setting_('PUSHOVER_TOKEN');
  var user = roleAddress_(role, 'PUSHOVER');
  if (!token || !user) return { ok: false, error: 'Pushover is not set up for ' + role };
  var response;
  try {
    response = UrlFetchApp.fetch(PUSHOVER_URL, { method: 'post', muteHttpExceptions: true,
      payload: { token: token, user: user, title: title.slice(0, 250), message: pushoverText_(lines) || title,
        priority: urgent ? '1' : '0' } });
  } catch (err) {
    return { ok: false, error: 'Pushover could not be reached' };
  }
  var code = response.getResponseCode();
  if (code !== 200) return { ok: false, error: 'Pushover refused the message (HTTP ' + code + ')' };
  var reply = jsonOrNull_(response.getContentText());
  if (!reply || reply.status !== 1) return { ok: false, error: 'Pushover did not confirm the message' };
  return { ok: true, error: null };
}

/** The lines, cut at a whole line to Pushover's 1024 characters, saying where the rest is. */
function pushoverText_(lines) {
  var text = lines.join('\n');
  if (text.length <= PUSHOVER_MAX) return text;
  var kept = [];
  var length = PUSHOVER_TAIL.length;
  for (var i = 0; i < lines.length && length + lines[i].length + 1 <= PUSHOVER_MAX; i++) {
    kept.push(lines[i]);
    length += lines[i].length + 1;
  }
  if (!kept.length) kept.push(lines[0].slice(0, PUSHOVER_MAX - PUSHOVER_TAIL.length - 1));
  kept.push(PUSHOVER_TAIL);
  return kept.join('\n');
}

function sendSlack_(role, subject, text) {
  var token = setting_('SLACK_TOKEN');
  var channel = roleAddress_(role, 'SLACK');
  if (!token || !channel) return { ok: false, error: 'Slack is not set up for ' + role };
  var response;
  try {
    response = UrlFetchApp.fetch(SLACK_URL, { method: 'post', contentType: 'application/json; charset=utf-8',
      headers: { Authorization: 'Bearer ' + token }, muteHttpExceptions: true,
      payload: JSON.stringify({ channel: channel, text: subject ? subject + '\n' + text : text }) });
  } catch (err) {
    return { ok: false, error: 'Slack could not be reached' };
  }
  var code = response.getResponseCode();
  if (code !== 200) return { ok: false, error: 'Slack refused the message (HTTP ' + code + ')' };
  var reply = jsonOrNull_(response.getContentText());
  if (!reply || reply.ok !== true) {
    var why = reply && typeof reply.error === 'string' && /^[a-z_]{1,60}$/.test(reply.error) ? reply.error : 'no reason given';
    return { ok: false, error: 'Slack refused the message (' + why + ')' };
  }
  return { ok: true, error: null };
}

function jsonOrNull_(text) {
  try {
    return JSON.parse(text);
  } catch (err) {
    return null;
  }
}

// ----- receipts

/** Writes the receipt; one that can't be written yet is owed, and the next run writes it. */
function writeReceipt_(payload) {
  try {
    putReceipt_(payload);
    return true;
  } catch (err) {
    log_('warn', 'the receipt for outbox ' + payload.outbox_id + ' could not be written yet: ' + err.message);
    var owed = state_('receipts.owed') || [];
    owed.push(payload);
    while (owed.length > OUTBOX_OWED_MAX) {   // a Script Property holds 9 KB; NG Runner alarms on the lost one
      log_('warn', 'the oldest owed receipt was dropped (outbox ' + owed.shift().outbox_id + ')');
    }
    setState_('receipts.owed', owed);
    return false;
  }
}

function putReceipt_(payload) {
  var folder = controlChild_('receipts', true);
  var name = payload.outbox_id + '.json';
  if (filesNamed_(folder, name).length) return;   // written already; never twice
  writeSignedFile_(folder, name, 'receipt', payload, 'new');
}

function flushOwedReceipts_() {
  var owed = state_('receipts.owed');
  if (!owed || !owed.length) return;
  var still = owed.filter(function (payload) {
    try {
      putReceipt_(payload);
      return false;
    } catch (err) {
      return true;
    }
  });
  if (still.length) setState_('receipts.owed', still);
  else clearState_('receipts.owed');
}
