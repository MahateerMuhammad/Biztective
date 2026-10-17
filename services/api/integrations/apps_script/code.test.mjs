// Runs the real Code.gs against in-memory stand-ins for the Apps Script services.
// node --test integrations/apps_script/code.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const TOKEN = "t".repeat(40);
const SOURCE = readFileSync(new URL("./Code.gs", import.meta.url), "utf8").replace(
  "PASTE_SHEETS_BRIDGE_TOKEN_HERE",
  TOKEN,
);

function makeSheet(name) {
  const cells = []; // rows of strings
  const formats = [];
  return {
    cells,
    formats,
    getName: () => name,
    getLastRow: () => {
      let n = cells.length;
      while (n && !(cells[n - 1] || []).some((c) => c !== "")) n--;
      return n;
    },
    getRange: (row, col, nRows, nCols) => ({
      setNumberFormat: (f) => formats.push([row, col, nRows, nCols, f]),
      setValues: (values) =>
        values.forEach((r, i) => {
          const target = (cells[row - 1 + i] ??= []);
          r.forEach((v, j) => (target[col - 1 + j] = v));
        }),
      getDisplayValues: () =>
        Array.from({ length: nRows }, (_, i) =>
          Array.from({ length: nCols }, (_, j) => String((cells[row - 1 + i] || [])[col - 1 + j] ?? "")),
        ),
    }),
  };
}

function load() {
  const sheets = [makeSheet("Sheet1")];
  const book = {
    getId: () => "sheet-id",
    getSheets: () => sheets,
    getSheetByName: (n) => sheets.find((s) => s.getName() === n) || null,
    insertSheet: (n) => sheets.push(makeSheet(n)),
  };
  let locked = false;
  const context = {
    SpreadsheetApp: { getActiveSpreadsheet: () => book },
    LockService: {
      getDocumentLock: () => ({ tryLock: () => !locked && (locked = true), releaseLock: () => (locked = false) }),
    },
    ContentService: {
      MimeType: { JSON: "json" },
      createTextOutput: (text) => ({ setMimeType: () => JSON.parse(text) }),
    },
  };
  vm.createContext(context);
  vm.runInContext(SOURCE, context);
  const call = (body) => context.doPost({ postData: { contents: JSON.stringify(body) } });
  const rpc = (op, args = {}, overrides = {}) =>
    call({ token: TOKEN, op, spreadsheet_id: "sheet-id", args, ...overrides });
  return { rpc, call, book, context };
}

test("refuses bad tokens, other spreadsheets, unknown operations and bad JSON", () => {
  const { rpc, context } = load();
  assert.equal(rpc("titles", {}, { token: "x".repeat(40) }).status, 401);
  assert.equal(rpc("titles", {}, { token: 7 }).status, 401);
  assert.equal(rpc("titles", {}, { spreadsheet_id: "someone-else" }).status, 403);
  assert.equal(rpc("drop_everything").status, 400);
  assert.equal(context.doPost({ postData: { contents: "{" } }).status, 400);
});

test("refuses to run with the placeholder token", () => {
  const context = {
    SpreadsheetApp: { getActiveSpreadsheet: () => ({ getId: () => "sheet-id" }) },
    ContentService: { MimeType: {}, createTextOutput: (t) => ({ setMimeType: () => JSON.parse(t) }) },
  };
  vm.createContext(context);
  vm.runInContext(readFileSync(new URL("./Code.gs", import.meta.url), "utf8"), context);
  const body = { token: "PASTE_SHEETS_BRIDGE_TOKEN_HERE", op: "titles", spreadsheet_id: "sheet-id" };
  assert.equal(context.doPost({ postData: { contents: JSON.stringify(body) } }).status, 401);
});

test("creates tabs, writes headers, appends as plain text and reads back trimmed rows", () => {
  const { rpc, book } = load();
  assert.deepEqual(rpc("titles").result, ["Sheet1"]);
  assert.equal(rpc("add_tabs", { titles: ["ROOT Review Tasks"] }).ok, true);
  assert.equal(rpc("add_tabs", { titles: ["ROOT Review Tasks"] }).status, 400); // exists
  rpc("put_rows", { data: [{ range: "'ROOT Review Tasks'!A1", values: [["k", "a", "b"]] }] });
  const appended = rpc("append", { range: "'ROOT Review Tasks'!A:C", rows: [["k-1", "'=1+1", ""], ["k-2", "x", "y"]] });
  assert.deepEqual(appended.result, { updatedRange: "'ROOT Review Tasks'!A2:C3" });
  const tab = book.getSheetByName("ROOT Review Tasks");
  assert.ok(tab.formats.every((f) => f[4] === "@")); // every write is plain text
  const read = rpc("read", { ranges: ["'ROOT Review Tasks'!A:C", "'Sheet1'!A:C"] }).result;
  assert.deepEqual(read, [[["k", "a", "b"], ["k-1", "'=1+1"], ["k-2", "x", "y"]], []]);
});

test("reads a single verification cell", () => {
  const { rpc } = load();
  rpc("add_tabs", { titles: ["ROOT_VERIFY"] });
  assert.deepEqual(rpc("read", { ranges: ["'ROOT_VERIFY'!A1"] }).result, [[]]);
  rpc("put_rows", { data: [{ range: "'ROOT_VERIFY'!A1", values: [["ROOT-ABCDEFGHIJ"]] }] });
  assert.deepEqual(rpc("read", { ranges: ["'ROOT_VERIFY'!A1"] }).result, [[["ROOT-ABCDEFGHIJ"]]]);
});

test("rejects malformed ranges, unknown tabs and ragged or oversized rows", () => {
  const { rpc } = load();
  assert.equal(rpc("read", { ranges: ["Sheet1!A:C"] }).status, 400);
  assert.equal(rpc("read", { ranges: ["'Nope'!A:C"] }).status, 400);
  assert.equal(rpc("append", { range: "'Sheet1'!A:C", rows: [["a", "b"], ["c"]] }).status, 400);
  assert.equal(rpc("append", { range: "'Sheet1'!A:C", rows: [] }).status, 400);
  assert.equal(rpc("append", { range: "'Sheet1'!A:C", rows: "x" }).status, 400);
  const long = rpc("append", { range: "'Sheet1'!A:A", rows: [["z".repeat(9000)]] });
  assert.equal(long.ok, true);
  assert.equal(rpc("read", { ranges: ["'Sheet1'!A:A"] }).result[0][0][0].length, 5000);
});

test("a held lock answers busy instead of interleaving writes", () => {
  const { rpc, context } = load();
  context.LockService.getDocumentLock = () => ({ tryLock: () => false, releaseLock: () => {} });
  assert.equal(rpc("titles").status, 503);
});
