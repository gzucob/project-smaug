import assert from "node:assert/strict";
import { test } from "node:test";
import {
  count,
  dateOnly,
  dateTime,
  money,
  monthYear,
  multiple,
  pct,
  price,
  signedPct,
  toNum,
  yearOf,
} from "../src/lib/format.ts";

test("API decimal strings and numbers render alike in PT-BR", () => {
  for (const input of ["0.184", 0.184]) {
    assert.equal(pct(input), "18,4%");
    assert.equal(signedPct(input), "+18,4%");
    assert.equal(multiple(input), "0,18×");
  }
  assert.equal(money("-1240000000"), "−R$ 1,24 bi");
  assert.equal(count("5710000000"), "5,71 bi");
});

test("missing or malformed decimals stay distinct from zero", () => {
  for (const input of [null, undefined, "", "not available", Infinity]) {
    assert.equal(toNum(input), null);
    assert.equal(pct(input), "-");
  }
  assert.equal(pct("0"), "0,0%");
});

test("filing dates retain the reported calendar day", () => {
  assert.equal(dateOnly("2026-11-26T02:00:00Z"), "26/11/2026");
  assert.equal(dateOnly("invalid"), "-");
  assert.equal(dateOnly(null), "-");
  assert.equal(yearOf("2026-11-26"), "2026");
  assert.match(monthYear("2026-11-26"), /nov.*2026/i);
  assert.match(dateTime("2026-11-26T15:00:00Z"), /26.*nov.*2026/i);
  assert.equal(monthYear("not a date"), "not a date");
  assert.equal(dateTime("not a date"), "not a date");
});

test("negative, zero and missing values retain their distinct labels", () => {
  assert.equal(signedPct(-0.25), "-25,0%");
  assert.equal(price("38.2"), "R$ 38,20");
  for (const render of [signedPct, multiple, price, money, count]) {
    assert.equal(render(null), "-");
  }
  for (const [value, expected] of [
    ["0", "0"],
    ["-800", "−800"],
    ["1250", "1,25 mil"],
    ["1500000", "1,5 mi"],
    ["2000000000", "2 bi"],
  ]) {
    assert.equal(count(value), expected);
    const amount = expected.startsWith("−")
      ? `−R$ ${expected.slice(1)}`
      : `R$ ${expected}`;
    assert.equal(money(value), amount);
  }
  assert.equal(money("1200000000000"), "R$ 1,2 tri");
});
