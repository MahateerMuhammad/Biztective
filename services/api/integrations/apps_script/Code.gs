/**
 * ROOT Sheets bridge (AMD-19).
 *
 * Paste into Extensions → Apps Script of the spreadsheet ROOT should write to, set TOKEN to
 * the value of SHEETS_BRIDGE_TOKEN, then Deploy → New deployment → Web app
 * (Execute as: Me, Who has access: Anyone). ROOT sends SHEETS_BRIDGE_TOKEN in the request
 * body; requests without it are refused. The script only works on this spreadsheet, never
 * evaluates cell contents (every written cell is plain text) and never deletes anything.
 */
const TOKEN = 'PASTE_SHEETS_BRIDGE_TOKEN_HERE';

const RANGE = /^'([^']{1,100})'!([A-Z])(\d*)(?::([A-Z]))?$/;

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return reply({ ok: false, status: 400, error: 'bad_json' });
  }
  if (!body || typeof body.token !== 'string' || TOKEN.length < 32 || !sameText(body.token, TOKEN)) {
    return reply({ ok: false, status: 401, error: 'bad_token' });
  }
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  if (body.spreadsheet_id !== ss.getId()) {
    return reply({ ok: false, status: 403, error: 'spreadsheet_not_allowed' });
  }
  const op = OPS[body.op];
  if (!op) return reply({ ok: false, status: 400, error: 'unknown_op' });
  const lock = LockService.getDocumentLock();
  if (!lock.tryLock(20000)) return reply({ ok: false, status: 503, error: 'busy' });
  try {
    return reply({ ok: true, result: op(ss, body.args || {}) });
  } catch (err) {
    if (err && err.rootStatus) return reply({ ok: false, status: err.rootStatus, error: String(err.message) });
    return reply({ ok: false, status: 500, error: 'script_error' });
  } finally {
    lock.releaseLock();
  }
}

const OPS = {
  titles: function (ss) {
    return ss.getSheets().map(function (s) { return s.getName(); });
  },
  add_tabs: function (ss, args) {
    const titles = list(args.titles);
    titles.forEach(function (t) {
      if (typeof t !== 'string' || !t || ss.getSheetByName(t)) fail(400, 'tab_exists_or_invalid');
    });
    titles.forEach(function (t) { ss.insertSheet(t); });
    return {};
  },
  put_rows: function (ss, args) {
    list(args.data).forEach(function (item) {
      const r = parse(ss, item.range);
      write(r.sheet, r.row || 1, rows(item.values));
    });
    return {};
  },
  read: function (ss, args) {
    return list(args.ranges).map(function (ref) {
      const r = parse(ss, ref);
      const last = r.sheet.getLastRow();
      if (r.row) {
        if (r.row > last) return [];
        return trim(r.sheet.getRange(r.row, r.first, 1, 1).getDisplayValues());
      }
      if (last === 0) return [];
      return trim(r.sheet.getRange(1, r.first, last, r.width).getDisplayValues());
    });
  },
  append: function (ss, args) {
    const r = parse(ss, args.range);
    const values = rows(args.rows);
    const start = r.sheet.getLastRow() + 1;
    write(r.sheet, start, values);
    const end = String.fromCharCode(64 + values[0].length);
    return { updatedRange: "'" + r.sheet.getName() + "'!A" + start + ':' + end + (start + values.length - 1) };
  },
};

function parse(ss, ref) {
  const m = RANGE.exec(String(ref));
  if (!m) fail(400, 'bad_range');
  const sheet = ss.getSheetByName(m[1]);
  if (!sheet) fail(400, 'unknown_tab');
  const first = m[2].charCodeAt(0) - 64;
  const last = m[4] ? m[4].charCodeAt(0) - 64 : first;
  return { sheet: sheet, first: first, width: last - first + 1, row: m[3] ? Number(m[3]) : 0 };
}

function write(sheet, row, values) {
  // Plain-text format first: nothing written by ROOT is ever parsed as a formula or number.
  const range = sheet.getRange(row, 1, values.length, values[0].length);
  range.setNumberFormat('@');
  range.setValues(values);
}

function rows(values) {
  const out = list(values);
  if (!out.length || out.length > 5000) fail(400, 'bad_rows');
  const width = list(out[0]).length;
  if (!width || width > 26) fail(400, 'bad_rows');
  return out.map(function (row) {
    const cells = list(row);
    if (cells.length !== width) fail(400, 'ragged_rows');
    return cells.map(function (c) { return String(c).slice(0, 5000); });
  });
}

function trim(values) {
  const out = values.map(function (row) {
    const cells = row.slice();
    while (cells.length && cells[cells.length - 1] === '') cells.pop();
    return cells;
  });
  while (out.length && !out[out.length - 1].length) out.pop();
  return out;
}

function list(value) {
  if (!Array.isArray(value)) fail(400, 'bad_args');
  return value;
}

function fail(status, message) {
  const err = new Error(message);
  err.rootStatus = status;
  throw err;
}

function sameText(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function reply(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
