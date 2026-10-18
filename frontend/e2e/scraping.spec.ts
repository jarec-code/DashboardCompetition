import { expect, test, type Page } from "@playwright/test";

// The e2e server serves saved alter.cz pages for *.e2e.test hosts (backend/tests/e2e_server.py).
const SHOP = "http://127.0.0.1:8011";
const OFFER_URL = "https://www.alter.e2e.test/produkt/1111";

async function login(page: Page) {
  await page.goto("/konkurencia");
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

const rows = (page: Page) => page.getByRole("table").locator("tbody tr");

test.describe.serial("Scraping a cenová história", () => {
  let competitorId: number;

  test("príprava: konkurent s parserom alter.cz, ponuka a produkt s EAN", async ({ page }) => {
    await login(page);
    const competitor = await api(page, "POST", "/competitors", {
      name: "Alter (test)", base_url: "https://www.alter.e2e.test", country: "CZ",
      scrape_config: { min_interval_seconds: 1 },
    });
    competitorId = competitor.id;
    await api(page, "PUT", `/competitors/${competitorId}/scrape-config`, {
      source_type: "html", parser_type: "alter_cz", parser_config: {}, timeout_seconds: 10, max_concurrency: 1,
      max_retries: 1, min_interval_seconds: 1, prices_vat_mode: "with_vat", enabled: true,
    });
    await api(page, "POST", "/products", { name: "Naše vlast – pracovní listy", ean: "8594165541796",
                                           prices: [{ market: "CZ", price: "49" }] });
    const added = await api(page, "POST", `/competitors/${competitorId}/offers`, { urls: [OFFER_URL], variant: "" });
    expect(added.created).toHaveLength(1);
  });

  test("testovacia kontrola ukáže extrahované údaje", async ({ page }) => {
    await login(page);
    await page.goto(`/konkurencia/${competitorId}`);
    await page.getByRole("tab", { name: "Nastavenia scrapingu" }).click();
    await expect(page.getByRole("combobox", { name: "Parser" })).toHaveValue("alter.cz");
    await page.getByRole("button", { name: "Testovacia kontrola" }).click();
    const dialog = page.getByRole("dialog", { name: "Testovacia kontrola" });
    await expect(dialog.getByLabel("URL produktovej stránky")).toHaveValue(OFFER_URL);
    await dialog.getByRole("button", { name: "Spustiť test" }).click();
    await expect(dialog.getByText("Extrakcia funguje.")).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("PRACOVNÍ LISTY k učebnici NAŠE VLAST (nové vydání)")).toBeVisible();
    await expect(dialog.getByText(/44,00/)).toBeVisible();
    await expect(dialog.getByText("EAN 8594165541796")).toBeVisible();
    await expect(dialog.getByText("Skladom")).toBeVisible();

    await dialog.getByLabel("Variant (voliteľné)").fill("elektronická");
    await dialog.getByRole("button", { name: "Spustiť test" }).click();
    await expect(dialog.getByText(/Variant „elektronická“ sa na stránke nenašiel/)).toBeVisible({ timeout: 15_000 });
  });

  test("kontrola uloží cenu, spáruje podľa EAN a zmena ceny vytvorí históriu", async ({ page, request }) => {
    await login(page);
    await page.goto(`/konkurencia/${competitorId}`);
    await page.getByRole("tab", { name: "Sledované produkty" }).click();
    const row = rows(page).first();
    await expect(row).toContainText("zatiaľ nekontrolované");

    await row.getByRole("button", { name: "Skontrolovať teraz" }).click();
    await expect(page.getByText(/prvá zaznamenaná cena/)).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText("44,00");
    await expect(row).toContainText("Skladom");
    await expect(row.getByRole("link", { name: "Naše vlast – pracovní listy" })).toBeVisible();
    await expect(row.getByText("ISBN / EAN / SKU")).toBeVisible();

    // unchanged page: no new history row
    await row.getByRole("button", { name: "Skontrolovať teraz" }).click();
    await expect(page.getByText(/bez zmeny/)).toBeVisible({ timeout: 15_000 });

    await request.get(`${SHOP}/__set_price?id=1111&value=39,00`);
    await row.getByRole("button", { name: "Skontrolovať teraz" }).click();
    await expect(page.getByText(/cena klesla/)).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText("39,00");

    await row.getByRole("button", { name: "Ďalšie akcie" }).click();
    await page.getByRole("menuitem", { name: "História zmien" }).click();
    const history = page.getByRole("dialog", { name: "História zmien ponuky" });
    await expect(history.locator("tbody tr")).toHaveCount(2);
    await expect(history.getByText("cena ↓")).toBeVisible();
    await expect(history.getByText("prvý záznam")).toBeVisible();
  });

  test("monitoring zobrazí behy kontrol", async ({ page }) => {
    await login(page);
    await page.goto(`/konkurencia/${competitorId}`);
    await page.getByRole("tab", { name: "Monitoring" }).click();
    await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(3);
    await expect(page.getByText("Úspešný").first()).toBeVisible();
  });

  test("chyba stránky ponechá poslednú platnú cenu", async ({ page }) => {
    await login(page);
    const missing = await api(page, "POST", `/competitors/${competitorId}/offers`,
                              { urls: ["https://www.alter.e2e.test/produkt/9999"], variant: "" });
    await page.goto(`/konkurencia/${competitorId}`);
    await page.getByRole("tab", { name: "Sledované produkty" }).click();
    const row = rows(page).filter({ hasText: "/produkt/9999" });
    await row.getByRole("button", { name: "Skontrolovať teraz" }).click();
    await expect(page.getByText(/Stránka neexistuje \(HTTP 404\)/).first()).toBeVisible({ timeout: 15_000 });
    expect(missing.created).toHaveLength(1);
    await expect(rows(page).filter({ hasText: "/produkt/1111" })).toContainText("39,00");
  });
});
