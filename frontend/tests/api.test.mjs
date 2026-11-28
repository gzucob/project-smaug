import assert from "node:assert/strict";
import { test } from "node:test";
import { API_BASE, fetchTicker } from "../src/lib/api.ts";

test("ticker reads preserve the FastAPI decimal and null payload", async (t) => {
  const data = { ticker: "PETR4", indicators: { roe: "0.184", p_l: null } };
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, `${API_BASE}/analysis/PETR4`);
    assert.equal(options.cache, "no-store");
    return Response.json(data);
  });
  assert.deepEqual(await fetchTicker("petr4"), { ok: true, data });
});

test("an unavailable API and a missing ticker retain separate states", async (t) => {
  const fetch = t.mock.method(globalThis, "fetch", async () => {
    throw new TypeError("connection refused");
  });
  const unavailable = await fetchTicker("PETR4");
  assert.equal(unavailable.ok, false);
  assert.equal(unavailable.status, 0);

  fetch.mock.mockImplementation(async () => new Response(null, { status: 404 }));
  const missing = await fetchTicker("PETR4");
  assert.equal(missing.ok, false);
  assert.equal(missing.status, 404);
});
