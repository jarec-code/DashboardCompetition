import { expect, test, type Page } from "@playwright/test";

// The fake shop announces a sitemap in robots.txt for objav.alter.e2e.test with four saved alter.cz product pages.
// One of them (E-ČESKÝ JAZYK pro 3. ročník) has EAN 8594165540539, the EAN of our product created below.

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

test("objavovanie produktov: tieňový režim, potom automatické sledovanie", async ({ page }) => {
  await login(page, "/konkurencia");
  const competitor = await api(page, "POST", "/competitors", {
    name: "Alter (objavovanie)", base_url: "https://objav.alter.e2e.test", country: "CZ",
    scrape_config: { parser_type: "alter_cz", min_interval_seconds: 1 } });
  await api(page, "POST", "/products", { name: "Český jazyk 3 (objavovanie)", ean: "8594165540539",
                                         prices: [{ market: "CZ", price: "120" }] });
  await page.goto(`/konkurencia/${competitor.id}`);
  await page.getByRole("tab", { name: "Katalóg konkurenta" }).click();
  await expect(page.getByText("Katalóg je prázdny.")).toBeVisible();

  await page.getByRole("combobox", { name: "Režim" }).click();
  await page.getByRole("option", { name: /Tieňový režim/ }).click();
  await page.getByRole("button", { name: "Uložiť" }).click();
  await expect(page.getByText(/až po potvrdení, že podmienky webu/)).toBeVisible();  // terms first
  await page.getByLabel(/Skontroloval\(a\) som obchodné podmienky/).check();
  await page.getByRole("button", { name: "Uložiť" }).click();
  await expect(page.getByText("Nastavenie objavovania bolo uložené.")).toBeVisible();

  await page.getByRole("button", { name: "Spustiť teraz" }).click();
  await expect(page.getByText("dokončený")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/sitemap \(nájdený automaticky\)/)).toBeVisible();
  await page.getByText(/^Spárované \(\d+\)$/).click();  // earlier tests created products with the other EANs too
  const row = page.getByRole("table").last().locator("tbody tr").filter({ hasText: "E-ČESKÝ JAZYK pro 3. ročník" });
  await expect(row.getByText("Český jazyk 3 (objavovanie)")).toBeVisible();
  await expect(row.getByText("ISBN/EAN", { exact: true })).toBeVisible();
  await expect(row.getByText("sleduje sa")).toHaveCount(0);  // shadow mode: nothing tracked

  await page.getByRole("combobox", { name: "Režim" }).click();
  await page.getByRole("option", { name: /Automatický/ }).click();
  await page.getByRole("button", { name: "Uložiť" }).click();
  await page.getByRole("button", { name: "Spustiť teraz" }).click();
  await expect(row.getByText(/sleduje sa/)).toBeVisible({ timeout: 30_000 });

  await page.getByRole("tab", { name: "Sledované produkty" }).click();
  await expect(page.getByRole("link", { name: "Český jazyk 3 (objavovanie)" })).toBeVisible();
});
