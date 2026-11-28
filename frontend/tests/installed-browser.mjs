import assert from "node:assert/strict";
import { chromium } from "playwright";

const [origin, mode] = process.argv.slice(2);
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  page.setDefaultTimeout(30_000);
  if (mode === "offline") {
    await page.goto(`${origin}/ticker/PETR4`);
    await page.getByRole("heading", { name: "Não foi possível carregar a análise" }).waitFor();
  } else {
    await page.goto(origin);
    await page.getByRole("combobox", { name: "Buscar por ticker ou nome da empresa" }).fill("PETR4");
    await page.getByRole("option", { name: /PETR4/ }).click();
    await page.waitForURL("**/ticker/PETR4");
    await page.getByText("n/d", { exact: true }).first().waitFor();
    await page.getByRole("button", { name: "Sobre ROE", exact: true }).first().click();
    const detail = page.getByRole("dialog");
    const close = detail.getByRole("button", { name: "Fechar", exact: true });
    await close.waitFor();
    assert.match(await detail.innerText(), /ROE/);
    await close.click();
    await close.waitFor({ state: "hidden" });
  }
} finally {
  await browser.close();
}
