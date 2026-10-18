import { expect, test, type Page } from "@playwright/test";

// The e2e server ticks the scheduler every 2 s; automatic checks start disabled and are switched on here only.

async function login(page: Page, path = "/konkurencia") {
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

const SETTINGS = { scheduler_enabled: true, priority_interval_hours: { strategic: 8, regular: 24, stable: 120 },
                   window_start_hour: null, window_end_hour: null, batch_size: 100 };

test.describe.serial("Automatické aktualizácie", () => {
  let competitorId: number;

  test.afterAll(async ({ browser }) => {
    const page = await browser.newPage();
    await login(page);
    await api(page, "PUT", "/settings/scheduling", { ...SETTINGS, scheduler_enabled: false });
    await page.close();
  });

  test("scheduler sám skontroluje novú ponuku", async ({ page }) => {
    await login(page);
    const competitor = await api(page, "POST", "/competitors", {
      name: "Alter (plánovanie)", base_url: "https://plan.alter.e2e.test", country: "CZ",
      scrape_config: { min_interval_seconds: 1 } });
    competitorId = competitor.id;
    await api(page, "PUT", `/competitors/${competitorId}/scrape-config`, {
      source_type: "html", parser_type: "alter_cz", parser_config: {}, timeout_seconds: 10, max_concurrency: 1,
      max_retries: 1, min_interval_seconds: 1, prices_vat_mode: "unknown", enabled: true });
    await api(page, "POST", `/competitors/${competitorId}/offers`,
              { urls: ["https://plan.alter.e2e.test/produkt/716"], variant: "" });
    await api(page, "PUT", "/settings/scheduling", SETTINGS);

    await page.goto(`/konkurencia/${competitorId}`);
    await page.getByRole("tab", { name: "Sledované produkty" }).click();
    const row = page.getByRole("table").locator("tbody tr").first();
    await expect(async () => {
      await page.reload();
      await page.getByRole("tab", { name: "Sledované produkty" }).click();
      await expect(row).toContainText("65,00", { timeout: 1000 });
    }).toPass({ timeout: 20_000 });
    await expect(row).toContainText("ďalšia kontrola");
  });

  test("stránka Aktualizácie a logy ukazuje stav, behy a úlohy", async ({ page }) => {
    await login(page, "/aktualizacie");
    await expect(page.getByRole("heading", { name: "Aktualizácie a logy" })).toBeVisible();
    await expect(page.getByText("plánuje")).toBeVisible();
    await expect(page.getByText("beží", { exact: true })).toBeVisible();

    const runRow = page.getByRole("table").locator("tbody tr").filter({ hasText: "Alter (plánovanie)" }).first();
    await expect(runRow).toContainText("automaticky");
    await expect(runRow).toContainText("Úspešný");
    await expect(runRow).toContainText("1 / 1");

    await page.getByRole("tab", { name: "Úlohy" }).click();
    await page.getByRole("combobox", { name: "Druh" }).click();
    await page.getByRole("option", { name: "automatická" }).click();
    const jobRow = page.getByRole("table").locator("tbody tr").filter({ hasText: "/produkt/716" }).first();
    await expect(jobRow).toContainText("Úspešná");
  });

  test("nastavenie intervalov a časového okna", async ({ page }) => {
    await login(page, "/nastavenia");
    await page.getByLabel("Strategické").fill("6");
    await page.getByLabel("Spúšťať kontroly iba v časovom okne (miestny čas)").check();
    await page.getByRole("button", { name: "Uložiť plánovanie" }).click();
    await expect(page.getByText("Plánovanie bolo uložené.")).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("Strategické")).toHaveValue("6");
    await expect(page.getByRole("combobox", { name: "Od" })).toHaveValue("01:00");
    await expect(page.getByRole("combobox", { name: "Do" })).toHaveValue("05:00");
  });
});
