'use strict';
/**
 * A harness for the Apps Script files in google/. It loads every .gs file into one node vm
 * context, as Apps Script loads a project into one global scope, beside in-memory fakes of the
 * Google services those files use. Only node built-ins are used, so the tests run wherever node
 * does, with no network and no Google account.
 *
 * The fakes are strict where a mistake would matter on Google: the workbook allows reads only
 * (any other call fails the test), HMAC takes signed byte arrays only (so the key bytes are
 * exact), a range with no rows is refused as Google refuses it, iterators include trashed files
 * as DriveApp's do, and Script Properties keep Google's size limits.
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

const GOOGLE_DIR = path.resolve(__dirname, '..');
const REPO_DIR = path.resolve(GOOGLE_DIR, '..');
const KEY_HEX = '00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff';

// ----- the clock

class Clock {
  constructor(iso) { this.ms = Date.parse(iso); }
  now() { return new Date(this.ms); }
  set(iso) { this.ms = Date.parse(iso); }
  advance(minutes) { this.ms += minutes * 60000; }
}

// ----- bytes and HMAC (Utilities)

function toSigned(buffer) {
  return Array.from(buffer, (b) => (b > 127 ? b - 256 : b));
}

function fromSigned(bytes, what) {
  const ok = Array.isArray(bytes) && bytes.every((b) => Number.isInteger(b) && b >= -128 && b <= 127);
  if (!ok) throw new Error(`fake Utilities: ${what} must be a Byte[] (whole numbers -128 to 127)`);
  return Buffer.from(bytes.map((b) => b & 0xff));
}

function formatDate(date, timeZone, pattern) {
  if (Object.prototype.toString.call(date) !== '[object Date]' || Number.isNaN(date.getTime())) {
    throw new Error('Exception: Invalid argument: date');
  }
  const p = {};
  const options = { timeZone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', weekday: 'long' };
  for (const part of new Intl.DateTimeFormat('en-US', options).formatToParts(date)) p[part.type] = part.value;
  const hour = Number(p.hour);
  return pattern.replace(/'([^']*)'|yyyy|MM|M|dd|d|HH|H|hh|h|mm|ss|a|EEEE|EEE/g, (token, quoted) => {
    if (quoted !== undefined) return quoted;
    switch (token) {
      case 'yyyy': return p.year;
      case 'MM': return p.month;
      case 'M': return String(Number(p.month));
      case 'dd': return p.day;
      case 'd': return String(Number(p.day));
      case 'HH': return p.hour;
      case 'H': return String(hour);
      case 'hh': return String(hour % 12 || 12).padStart(2, '0');
      case 'h': return String(hour % 12 || 12);
      case 'mm': return p.minute;
      case 'ss': return p.second;
      case 'a': return hour < 12 ? 'AM' : 'PM';
      case 'EEEE': return p.weekday;
      default: return p.weekday.slice(0, 3);   // EEE
    }
  });
}

function fakeUtilities() {
  return {
    computeHmacSha256Signature(value, key) {
      const mac = crypto.createHmac('sha256', fromSigned(key, 'the key'));
      return toSigned(mac.update(fromSigned(value, 'the value')).digest());
    },
    getUuid() { return crypto.randomUUID(); },
    formatDate,
    sleep() {},
  };
}

// ----- Script Properties and the script lock

class FakeProperties {
  constructor(values) { this.values = { ...values }; }
  getProperty(key) { return Object.hasOwn(this.values, key) ? this.values[key] : null; }
  setProperty(key, value) {
    const text = String(value);
    if (Buffer.byteLength(key) + Buffer.byteLength(text) > 9 * 1024) {
      throw new Error('Exception: Argument too large: value');
    }
    this.values[key] = text;
    const total = Object.entries(this.values)
      .reduce((n, [k, v]) => n + Buffer.byteLength(k) + Buffer.byteLength(v), 0);
    if (total > 500 * 1024) throw new Error('Exception: You have exceeded the property storage quota.');
    return this;
  }
  deleteProperty(key) { delete this.values[key]; return this; }
  getProperties() { return { ...this.values }; }
  getKeys() { return Object.keys(this.values); }
}

class FakeLock {
  constructor(state) { this.state = state; }
  tryLock() { if (this.state.busy) return false; this.state.held = true; return true; }
  waitLock() {
    if (this.state.busy) throw new Error('Exception: Lock timeout: another process was holding the lock for too long.');
    this.state.held = true;
  }
  releaseLock() { this.state.held = false; }
  hasLock() { return this.state.held; }
}

// ----- Drive

function iterator(items) {
  const list = items.slice();
  let i = 0;
  return {
    hasNext: () => i < list.length,
    next: () => {
      if (i >= list.length) throw new Error('Exception: Cannot retrieve the next object: iterator has reached the end.');
      return list[i++];
    },
  };
}

class FakeItem {
  constructor(drive, parent, name) {
    this.drive = drive;
    this.parent = parent;
    this.name = name;
    this.id = `drive-${++drive.count}`;
    this.trashed = false;
    this.created = drive.clock.ms;
    this.updated = drive.clock.ms;
    drive.byId.set(this.id, this);
  }
  getId() { return this.id; }
  getName() { return this.name; }
  isTrashed() { return this.trashed; }
  setTrashed(trashed) {
    this.trashed = Boolean(trashed);
    this.drive.log.push(`${trashed ? 'trash' : 'untrash'} ${this.path()}`);
    return this;
  }
  getDateCreated() { return new Date(this.created); }
  getLastUpdated() { return new Date(this.updated); }
  getParents() { return iterator(this.parent ? [this.parent] : []); }
  path() { return this.parent && this.parent.parent ? `${this.parent.path()}/${this.name}` : this.name; }
}

class FakeFolder extends FakeItem {
  constructor(drive, parent, name) { super(drive, parent, name); this.children = []; }
  folders() { return this.children.filter((c) => c instanceof FakeFolder); }
  files() { return this.children.filter((c) => c instanceof FakeFile); }
  getFolders() { return iterator(this.folders()); }
  getFoldersByName(name) { return iterator(this.folders().filter((f) => f.name === name)); }
  getFiles() { return iterator(this.files()); }
  getFilesByName(name) { return iterator(this.files().filter((f) => f.name === name)); }
  createFolder(name) {
    const folder = new FakeFolder(this.drive, this, name);
    this.children.push(folder);
    this.drive.log.push(`mkdir ${folder.path()}`);
    return folder;
  }
  createFile(name, content, mimeType) {
    if (typeof content !== 'string') throw new Error('fake Drive: createFile takes text here');
    const file = new FakeFile(this.drive, this, name, content, mimeType || 'text/plain');
    this.children.push(file);
    this.drive.log.push(`create ${file.path()}`);
    return file;
  }
}

class FakeFile extends FakeItem {
  constructor(drive, parent, name, content, mimeType) {
    super(drive, parent, name);
    this.content = content;
    this.mimeType = mimeType;
  }
  getMimeType() { return this.mimeType; }
  getSize() { return Buffer.byteLength(this.content); }
  getBlob() {
    const content = this.content;
    return {
      getDataAsString(charset) {
        if (charset !== undefined && String(charset).toUpperCase() !== 'UTF-8') throw new Error('fake Drive: UTF-8 only');
        return content;
      },
      getBytes() { return toSigned(Buffer.from(content, 'utf8')); },
    };
  }
  setContent(content) {
    if (typeof content !== 'string') throw new Error('fake Drive: setContent takes text');
    this.content = content;
    this.updated = this.drive.clock.ms;
    this.drive.log.push(`update ${this.path()}`);
    return this;
  }
}

class FakeDrive {
  constructor(clock) {
    this.clock = clock;
    this.count = 0;
    this.byId = new Map();
    this.log = [];
    this.root = new FakeFolder(this, null, 'My Drive');
  }
  getFolderById(id) {
    const item = this.byId.get(id);
    if (!(item instanceof FakeFolder)) throw new Error('Exception: No item with the given ID could be found.');
    return item;
  }
  getFileById(id) {
    const item = this.byId.get(id);
    if (!(item instanceof FakeFile)) throw new Error('Exception: No item with the given ID could be found.');
    return item;
  }
}

/** Test helpers over a folder: paths like 'outbox/<id>.json', made as a person or a sync would. */
function folderHelpers(drive, root) {
  function folder(parts, create) {
    let here = root;
    for (const part of parts) {
      let next = here.folders().find((f) => f.name === part && !f.trashed);
      if (!next) {
        if (!create) return null;
        next = new FakeFolder(drive, here, part);
        here.children.push(next);
      }
      here = next;
    }
    return here;
  }
  return {
    folder: (p, create = false) => folder(p ? p.split('/') : [], create),
    put(p, content, { createdMinutesAgo = 0 } = {}) {
      const parts = p.split('/');
      const parent = folder(parts.slice(0, -1), true);
      const file = new FakeFile(drive, parent, parts.at(-1), content, 'application/json');
      file.created = file.updated = drive.clock.ms - createdMinutesAgo * 60000;
      parent.children.push(file);
      return file;
    },
    files(p) {
      const parts = p.split('/');
      const parent = folder(parts.slice(0, -1), false);
      return parent ? parent.files().filter((f) => f.name === parts.at(-1)) : [];
    },
    live(p) { return this.files(p).filter((f) => !f.trashed); },
    text(p) {
      const found = this.live(p);
      if (found.length !== 1) throw new Error(`expected one live file at ${p}, found ${found.length}`);
      return found[0].content;
    },
    names(p) {
      const f = folder(p ? p.split('/') : [], false);
      return f ? f.children.filter((c) => !c.trashed).map((c) => c.name).sort() : [];
    },
  };
}

// ----- the workbook (read-only)

const BOOK_READS = new Set(['getId', 'getName', 'getSheetByName', 'getSheets']);
const SHEET_READS = new Set(['getName', 'getLastRow', 'getLastColumn', 'getMaxRows', 'getMaxColumns',
  'getRange', 'getDataRange', 'getSheetId', 'getIndex', 'getParent']);
const RANGE_READS = new Set(['getRow', 'getColumn', 'getNumRows', 'getNumColumns', 'getLastRow',
  'getLastColumn', 'getSheet', 'getDisplayValues', 'getDisplayValue', 'getValues', 'getValue', 'getA1Notation']);

/** Only the listed read methods exist; anything else is recorded and fails the test. */
function readOnly(target, reads, what, attempts) {
  return new Proxy(target, {
    get(obj, prop) {
      if (typeof prop === 'symbol') return undefined;
      if (!reads.has(prop)) {
        attempts.push(`${what}.${prop}`);
        throw new Error(`the workbook is read-only: ${what}.${prop} is not a read`);
      }
      return obj[prop].bind(obj);
    },
    set(obj, prop) {
      attempts.push(`${what}.${String(prop)} =`);
      throw new Error('the workbook is read-only');
    },
    defineProperty() { attempts.push(`${what} defineProperty`); throw new Error('the workbook is read-only'); },
    deleteProperty() { attempts.push(`${what} delete`); throw new Error('the workbook is read-only'); },
  });
}

class FakeSheet {
  constructor(book, name, grid) { this.book = book; this.name = name; this.grid = grid.map((r) => r.slice()); }
  cell(row, column) { const r = this.grid[row - 1]; return r && r[column - 1] !== undefined ? String(r[column - 1]) : ''; }
  getName() { return this.name; }
  getLastRow() {
    for (let r = this.grid.length; r >= 1; r--) if (this.grid[r - 1].some((v) => String(v) !== '')) return r;
    return 0;
  }
  getLastColumn() {
    let last = 0;
    for (const row of this.grid) row.forEach((v, i) => { if (String(v) !== '') last = Math.max(last, i + 1); });
    return last;
  }
  getMaxRows() { return this.grid.length + 50; }
  getMaxColumns() { return Math.max(26, this.getLastColumn()); }
  getSheetId() { return 0; }
  getIndex() { return 1; }
  getParent() { return this.book.proxy; }
  getRange(row, column, numRows = 1, numColumns = 1) {
    if (typeof row !== 'number') throw new Error('fake sheet: getRange takes numbers here');
    if (row < 1 || column < 1) throw new Error('Exception: The starting row or column of the range is too small.');
    if (numRows < 1) throw new Error('Exception: The number of rows in the range must be at least 1.');
    if (numColumns < 1) throw new Error('Exception: The number of columns in the range must be at least 1.');
    if (row + numRows - 1 > this.getMaxRows() || column + numColumns - 1 > this.getMaxColumns()) {
      throw new Error('Exception: Those rows are out of bounds.');
    }
    this.book.reads.push([row, column, numRows, numColumns]);
    return readOnly(new FakeRange(this, row, column, numRows, numColumns), RANGE_READS, 'Range', this.book.attempts);
  }
  getDataRange() { return this.getRange(1, 1, Math.max(1, this.getLastRow()), Math.max(1, this.getLastColumn())); }
}

class FakeRange {
  constructor(sheet, row, column, numRows, numColumns) {
    Object.assign(this, { sheet, row, column, numRows, numColumns });
  }
  getRow() { return this.row; }
  getColumn() { return this.column; }
  getNumRows() { return this.numRows; }
  getNumColumns() { return this.numColumns; }
  getLastRow() { return this.row + this.numRows - 1; }
  getLastColumn() { return this.column + this.numColumns - 1; }
  getSheet() { return this.sheet.proxy; }
  getDisplayValues() {
    const out = [];
    for (let r = 0; r < this.numRows; r++) {
      const line = [];
      for (let c = 0; c < this.numColumns; c++) line.push(this.sheet.cell(this.row + r, this.column + c));
      out.push(line);
    }
    return out;
  }
  getDisplayValue() { return this.sheet.cell(this.row, this.column); }
  getValues() { return this.getDisplayValues(); }
  getValue() { return this.getDisplayValue(); }
  getA1Notation() { return `R${this.row}C${this.column}:R${this.getLastRow()}C${this.getLastColumn()}`; }
}

class FakeBook {
  constructor(id, sheets) {
    this.id = id;
    this.reads = [];
    this.attempts = [];
    this.proxy = readOnly(this, BOOK_READS, 'Spreadsheet', this.attempts);
    this.sheets = Object.entries(sheets).map(([name, grid]) => {
      const sheet = new FakeSheet(this, name, grid);
      sheet.proxy = readOnly(sheet, SHEET_READS, 'Sheet', this.attempts);
      return sheet;
    });
  }
  getId() { return this.id; }
  getName() { return 'NG Fab Schedule'; }
  getSheetByName(name) { const s = this.sheets.find((x) => x.name === name); return s ? s.proxy : null; }
  getSheets() { return this.sheets.map((s) => s.proxy); }
  sheet(name) { return this.sheets.find((x) => x.name === name); }
  /** Barbara types into one cell (or pastes a block); returns the onEdit event Google would send. */
  edit(name, row, column, values) {
    const sheet = this.sheet(name);
    const block = Array.isArray(values) ? values : [[values]];
    const old = sheet.cell(row, column);
    block.forEach((line, r) => line.forEach((v, c) => {
      while (sheet.grid.length < row + r) sheet.grid.push([]);
      const target = sheet.grid[row + r - 1];
      while (target.length < column + c) target.push('');
      target[column + c - 1] = v;
    }));
    const single = block.length === 1 && block[0].length === 1;
    const event = { range: sheet.getRange(row, column, block.length, block[0].length),
      source: this.proxy, authMode: 'FULL', triggerUid: 'trigger-1' };
    if (single) {
      if (old !== '') event.oldValue = old;
      if (String(block[0][0]) !== '') event.value = String(block[0][0]);
    }
    return event;
  }
}

// ----- mail, fetch, triggers, log

class FakeMail {
  constructor() { this.sent = []; this.quota = 100; this.failure = null; }
  sendEmail(a, subject, body, options) {
    if (this.failure) throw new Error(this.failure);
    const message = typeof a === 'object' ? { ...a } : { to: a, subject, body, ...(options || {}) };
    if (!message.to || typeof message.subject !== 'string' || typeof message.body !== 'string') {
      throw new Error('Exception: Invalid argument: recipient');
    }
    this.quota -= 1;
    this.sent.push(message);
  }
  getRemainingDailyQuota() { return this.quota; }
}

class FakeFetch {
  constructor() { this.requests = []; this.reply = () => ({ code: 200, body: '{"status":1,"ok":true}' }); }
  fetch(url, params = {}) {
    this.requests.push({ url, params: JSON.parse(JSON.stringify(params)) });
    const reply = this.reply(url, params);
    if (reply.code >= 400 && !params.muteHttpExceptions) {
      throw new Error(`Exception: Request failed for ${url} returned code ${reply.code}`);
    }
    return { getResponseCode: () => reply.code, getContentText: () => reply.body, getHeaders: () => ({}) };
  }
}

class FakeScriptApp {
  constructor() { this.triggers = []; }
  getProjectTriggers() { return this.triggers.slice(); }
  deleteTrigger(trigger) { this.triggers = this.triggers.filter((t) => t !== trigger); }
  newTrigger(handler) {
    const triggers = this.triggers;
    const spec = { handler };
    const made = () => {
      const trigger = { spec: { ...spec }, getHandlerFunction: () => handler, getUniqueId: () => `t${triggers.length}` };
      triggers.push(trigger);
      return trigger;
    };
    const clock = {
      everyMinutes(n) {
        if (![1, 5, 10, 15, 30].includes(n)) throw new Error('Exception: everyMinutes takes 1, 5, 10, 15 or 30');
        spec.everyMinutes = n; return clock;
      },
      everyHours(n) { spec.everyHours = n; return clock; },
      everyDays(n) { spec.everyDays = n; return clock; },
      atHour(h) { spec.atHour = h; return clock; },
      nearMinute(m) { spec.nearMinute = m; return clock; },
      inTimezone(tz) { spec.timeZone = tz; return clock; },
      create: made,
    };
    return {
      timeBased() { spec.type = 'clock'; return clock; },
      forSpreadsheet(id) {
        spec.type = 'spreadsheet'; spec.spreadsheet = id;
        return { onEdit() { spec.event = 'edit'; return { create: made }; } };
      },
    };
  }
}

function fakeConsole(logs) {
  const record = (level) => (...args) => logs.push([level, args.map(String).join(' ')]);
  return { log: record('log'), info: record('info'), warn: record('warn'), error: record('error') };
}

// ----- loading the project

/** The Sheet1 the tests start from: synthetic rows in the shapes the real sheet holds. */
function sheet1() {
  return [
    ['', 'Job Name', 'Designer', 'Install Date', '', 'Designed', 'Opt', 'Paperwork', 'Fab Date', 'Last touch',
      'Product', 'Sale Cost', 'Cost', 'Delta', 'Product Ordered', 'Notes'],
    ['', 'Testerson', 'PE', 'Wednesday', ' 7 /1', 'x', '', '', '6/24/26', '', 'Closet', '$1,200', '', '', '', 'call first'],
    ['', 'Fakeley_070826', 'QA', 'Wednesday', ' 7 /8', '', '', '', '', '', '', '', '', '', '', ''],
    ['', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''],
    ['', 'Mockford JD', 'ZT', '7/15/26', '7/15/26', 'x', 'x', '', '7/10/26', '', '', '', '', '', '', ''],
    ['', 'Dummitt garage', 'Avery', 'Friday', ' 7 /17', '', '', '', '', '', '', '', '', '', '', ''],
    ['', 'Fakeley_07126', 'QA', 'Monday', ' 7 /20', '', '', '', '', '', '', '', '', '', '', 'five-digit date'],
  ];
}

function defaultProperties(controlId) {
  return {
    SIGNING_KEY: KEY_HEX,
    CONTROL_FOLDER_ID: controlId,
    SHEET_ID: 'sheet-test-1',
    ROLE_JIM_EMAIL: 'jim@example.com',
    ROLE_JIM_PUSHOVER: 'pushover-user-jim',
    ROLE_JIM_SLACK: 'D0JIM',
    ROLE_BARBARA_EMAIL: 'barbara@example.com',
    ROLE_SULAIMAN_EMAIL: 'dev@example.com',
    PUSHOVER_TOKEN: 'pushover-app-token',
    SLACK_TOKEN: 'slack-bot-token',
  };
}

function load(options = {}) {
  const clock = new Clock(options.now || '2026-10-30T13:30:00Z');
  const drive = new FakeDrive(clock);
  const controlFolder = drive.root.createFolder('NG Runner control');
  drive.log.length = 0;
  const book = new FakeBook('sheet-test-1', options.sheets || { Dashboard: [['Oct 27, 2026']], Sheet1: sheet1(), Archive: [] });
  const props = new FakeProperties({ ...defaultProperties(controlFolder.getId()), ...(options.props || {}) });
  for (const [k, v] of Object.entries(options.props || {})) if (v === null) delete props.values[k];
  const lockState = { busy: false, held: false };
  const mail = new FakeMail();
  const fetch = new FakeFetch();
  const scriptApp = new FakeScriptApp();
  const logs = [];
  const context = vm.createContext({
    console: fakeConsole(logs),
    Utilities: fakeUtilities(),
    DriveApp: drive,
    SpreadsheetApp: {
      openById(id) {
        if (id !== book.id) throw new Error('Exception: Unexpected error while getting the method or property openById');
        return book.proxy;
      },
    },
    MailApp: mail,
    UrlFetchApp: fetch,
    PropertiesService: { getScriptProperties: () => props },
    LockService: { getScriptLock: () => new FakeLock(lockState) },
    Session: { getScriptTimeZone: () => options.timeZone || 'America/Los_Angeles' },
    ScriptApp: scriptApp,
  });
  const files = fs.readdirSync(GOOGLE_DIR).filter((n) => n.endsWith('.gs')).sort();
  if (options.reverse) files.reverse();
  for (const name of files) {
    vm.runInContext(fs.readFileSync(path.join(GOOGLE_DIR, name), 'utf8'), context, { filename: name });
  }
  context.now_ = () => clock.now();   // the scripts' one clock
  return {
    gs: context, clock, drive, book, props, mail, fetch, scriptApp, logs, lockState, files,
    control: folderHelpers(drive, controlFolder), controlFolder, keyHex: KEY_HEX,
  };
}

// ----- shared helpers for the tests

/** A value from the vm realm as plain test-realm data, so deepStrictEqual compares content. */
function plain(value) { return value === undefined ? undefined : JSON.parse(JSON.stringify(value)); }

function hexToSigned(hex) { return toSigned(Buffer.from(hex, 'hex')); }

function vector() {
  return JSON.parse(fs.readFileSync(path.join(REPO_DIR, 'tests', 'vectors', 'envelope_v1.json'), 'utf8'));
}

/** Where the pytest bridge wants files the Python side reads back (unset when node runs alone). */
function outDir() { return process.env.NGR_JS_OUT || null; }

function pythonEnvelopes() {
  const file = process.env.NGR_PY_ENVELOPES;
  return file ? JSON.parse(fs.readFileSync(file, 'utf8')) : null;
}

module.exports = { load, plain, hexToSigned, vector, outDir, pythonEnvelopes, sheet1, KEY_HEX, GOOGLE_DIR, REPO_DIR };
