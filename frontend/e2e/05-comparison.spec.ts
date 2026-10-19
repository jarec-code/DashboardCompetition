import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

async function login(page: Page, path: string) {
  await page.goto(path);
  await page.getByLabel("E-mail").fill("admin@e2e.test");
  await page.getByLabel("Heslo").fill("e2e-heslo-12345");
  await page.getByRole("button", { name: "Prihlásiť sa" }).click();
  await expect(page.getByRole("button", { name: "Odhlásiť" })).toBeVisible();
}

async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ([m, p, b]) => {
    const token = (await (await fetch("/api/auth/me")).json()).csrf_token;
    const r = await fetch(`/api${p}`, { method: m as string, body: b === undefined ? undefined : JSON.stringify(b),
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token } });
    return r.json();
  }, [method, path, body] as const);
}

async function waitForJob(page: Page, id: number) {
  await expect(async () => {
    const job = await api(page, "GET", `/jobs/${id}`);
    expect(job.status).toBe("succeeded");
  }).toPass({ timeout: 15_000 });
}

const PRODUCT = "Prvouka 1 – průvodce pro učitele (porovnanie)";

test.describe.serial("Cenové porovnanie a dashboard", () => {
  let productId: number;

  test("porovnanie ukáže, či je konkurent lacnejší alebo drahší", async ({ page }) => {
    await login(page, "/porovnanie");
    const competitor = await api(page, "POST", "/competitors", {
      name: "Alter (porovnanie)", base_url: "https://cmp.alter.e2e.test", country: "CZ",
      scrape_config: { min_interval_seconds: 1 } });
    await api(page, "PUT", `/competitors/${competitor.id}/scrape-config`, {
      source_type: "html", parser_type: "alter_cz", parser_config: {}, timeout_seconds: 10, max_concurrency: 1,
      max_retries: 0, min_interval_seconds: 1, prices_vat_mode: "with_vat", enabled: true });
    const product = await api(page, "POST", "/products", { name: PRODUCT, priority: "strategic",
                                                           prices: [{ market: "CZ", price: "60" }] });
    productId = product.id;
    const offers = await api(page, "POST", `/competitors/${competitor.id}/offers`,
                             { urls: ["https://cmp.alter.e2e.test/produkt/716"], variant: "" });
    const offerId = offers.created[0].id;
    await api(page, "POST", `/offers/${offerId}/assignment`, { action: "assign", product_id: productId });
    await waitForJob(page, (await api(page, "POST", `/offers/${offerId}/check`)).id);

    await page.goto("/porovnanie?q=porovnanie");
    const row = page.getByRole("table").locator("tbody tr").first();
    await expect(row).toContainText(PRODUCT);
    await expect(row).toContainText("60,00");
    await expect(row).toContainText("65,00");
    await expect(row.getByText(/drahší\s*\+8,3\s*%/)).toBeVisible();

    await api(page, "PATCH", `/products/${productId}`, { prices: [{ market: "CZ", price: "70" }] });
    await page.reload();
    await expect(row.getByText(/lacnejší\s*−7,1\s*%/)).toBeVisible();

    await page.getByRole("combobox", { name: "Pozícia" }).click();
    await page.getByRole("option", { name: "Konkurent je lacnejší" }).click();
    await expect(page).toHaveURL(/position=not_cheapest/);
    await expect(page.getByRole("table").locator("tbody tr").filter({ hasText: PRODUCT })).toHaveCount(1);
    await page.getByRole("combobox", { name: "Pozícia" }).click();
    await page.getByRole("option", { name: "Sme najlacnejší" }).click();
    await expect(page.getByRole("table").locator("tbody tr").filter({ hasText: PRODUCT })).toHaveCount(0);
  });

  test("export CSV obsahuje porovnanie", async ({ page }) => {
    await login(page, "/porovnanie?q=porovnanie");
    const download = page.waitForEvent("download");
    await page.getByRole("link", { name: "Export CSV" }).click();
    const file = await download;
    const text = readFileSync(await file.path(), "utf-8");
    expect(text).toContain("Produkt;Trh;Vlastná cena");
    expect(text).toContain(`${PRODUCT};CZ;70.00;CZK;Alter (porovnanie);65.00;CZK;-5.00;-7.1;in_stock`);
  });

  test("dashboard ukazuje reálne čísla a odkazy", async ({ page }) => {
    await login(page, "/");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByText("Sledované ponuky")).toBeVisible();
    await expect(page.getByText("Konkurent je lacnejší", { exact: true })).toBeVisible();
    await expect(page.getByText("Posledné behy kontrol")).toBeVisible();
    await expect(page.getByRole("link", { name: "Alter (porovnanie)" }).first()).toBeVisible();
    await page.getByRole("link", { name: /z \d+ porovnateľných produktov/ }).click();
    await expect(page).toHaveURL(/\/porovnanie\?position=not_cheapest/);
    await expect(page.getByRole("table").locator("tbody tr").filter({ hasText: PRODUCT })).toHaveCount(1);
  });
});
