const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "../ui/app.js"), "utf8");

function ui(fetch) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) {
      nodes.set(id, { id, dataset: {}, classList: { toggle() {} } });
    }
    return nodes.get(id);
  };
  const document = {
    getElementById: node,
    querySelector: node,
    querySelectorAll: () => [],
  };
  const context = { document, fetch, setTimeout() {} };
  runInNewContext(source, context, { filename: "app.js" });
  return { node, action: context.action, render: context.render, poll: context.poll };
}

const prepared = {
  installed: true,
  prepared: true,
  running: true,
  docker: true,
  os: "linux",
  edition: "backend",
  version: "1",
  latest: "2",
};

test("a prepared backend shows management and offers the available update", () => {
  const page = ui(async () => ({ ok: true, json: async () => prepared }));
  page.render(prepared);
  assert.equal(page.node("setup").hidden, true);
  assert.equal(page.node("management").hidden, false);
  assert.equal(page.node("open").textContent, "Abrir a API");
  assert.equal(page.node("update-notice").textContent, "Nova versão disponível: 2");
  assert.equal(page.node("claude").hidden, true);
});

test("an action forwards the selected edition and surfaces manager errors", async () => {
  const calls = [];
  const page = ui(async (url, options) => {
    if (url === "status") return { ok: true, json: async () => prepared };
    calls.push(JSON.parse(options.body));
    return { ok: false, text: async () => "Docker indisponível" };
  });
  await page.poll();
  page.node('[name="edition"]:checked').value = "full";
  await page.action("upgrade");
  assert.deepEqual(calls, [{ action: "upgrade", edition: "full", client: "" }]);
  assert.equal(page.node("progress").hidden, false);
  assert.equal(page.node("error").textContent, "Docker indisponível");
});
