import assert from "node:assert/strict";
import { test } from "node:test";
import {
  count,
  dateOnly,
  money,
  multiple,
  pct,
  signedPct,
  toNum,
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
});
