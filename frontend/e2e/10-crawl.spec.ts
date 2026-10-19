import { expect, test, type Page } from "@playwright/test";

// A website like prometheus-nakl.cz (saved pages under /prom/): no sitemap, no structured data, products behind
// query URLs (index.php?zobraz=detail&id_katalog=306), a menu of images and a catalogue split into series.

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
    const r = await fetch(`/api${p}`, { method: m as string, body: b ? JSON.stringify(b) : undefined,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token } });
    return r.json();
  }, [method, path, body] as const);
}

test("web bez sitemap a štruktúrovaných dát sa prejde crawlerom", async ({ page }) => {
  test.setTimeout(90_000);
  await login(page, "/konkurencia");
  const competitor = await api(page, "POST", "/competitors", {
    name: "Prometheus (e2e)", base_url: "https://prom.e2e.test/prom/", country: "CZ",
    scrape_config: { min_interval_seconds: 1 } });
  await api(page, "POST", "/products", { name: "Stereometrie (vlastná)", isbn: "978-80-7196-523-7",
                                         prices: [{ market: "CZ", price: "250" }] });
  await page.goto(`/konkurencia/${competitor.id}`);
  await page.getByRole("tab", { name: "Katalóg konkurenta" }).click();
  await page.getByRole("combobox", { name: "Režim" }).click();
  await page.getByRole("option", { name: /Tieňový režim/ }).click();
  await page.getByLabel(/Skontroloval\(a\) som obchodné podmienky/).check();
  await page.getByLabel("Stránok na beh").fill("25");
  await page.getByRole("button", { name: "Uložiť" }).click();
  await expect(page.getByText("Nastavenie objavovania bolo uložené.")).toBeVisible();
  await page.getByRole("button", { name: "Spustiť teraz" }).click();

  await expect(page.getByText("čiastočne", { exact: true })).toBeVisible({ timeout: 60_000 });  // page limit
  await expect(page.getByText(/prechádzanie webu \(nájdený automaticky\)/)).toBeVisible();
  const diagnostics = page.locator("div").filter({ has: page.getByRole("heading", { name: "Diagnostika zdroja" }) }).last();
  await expect(diagnostics.getByText(/katalóg: https:\/\/prom\.e2e\.test\/prom\/index\.php\?zobraz=katalog/)).toBeVisible();
  await expect(diagnostics.getByText("/prom/index.php?id_katalog={N}&zobraz=detail")).toBeVisible();
  await expect(diagnostics.getByText(/dosiahnutý limit 25 stránok/).first()).toBeVisible();

  await page.getByText(/^Spárované \(\d+\)$/).click();
  const row = page.getByRole("table").last().locator("tbody tr").filter({ hasText: "Stereometrie – také E-KNIHA" });
  await expect(row.getByText("Stereometrie (vlastná)")).toBeVisible();
  await expect(row.getByText(/prechádzanie webu · načítané · parser heuristic · úplnosť 85 %/)).toBeVisible();
});
