import { expect, test, type Page } from "@playwright/test";

const SHOP = "http://127.0.0.1:8011";
const PRODUCT = "Prvouka 1 – průvodce (história)";

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

async function check(page: Page, offerId: number) {
  const job = await api(page, "POST", `/offers/${offerId}/check`);
  await expect(async () => {
    expect((await api(page, "GET", `/jobs/${job.id}`)).status).toBe("succeeded");
  }).toPass({ timeout: 15_000 });
}

test.describe.serial("Cenová história a upozornenia", () => {
  let productId: number;

  test("príprava: dve kontroly s poklesom ceny", async ({ page, request }) => {
    await login(page, "/");
    const competitor = await api(page, "POST", "/competitors", {
      name: "Alter (história)", base_url: "https://hist.alter.e2e.test", country: "CZ",
      scrape_config: { min_interval_seconds: 1 } });
    await api(page, "PUT", `/competitors/${competitor.id}/scrape-config`, {
      source_type: "html", parser_type: "alter_cz", parser_config: {}, timeout_seconds: 10, max_concurrency: 1,
      max_retries: 0, min_interval_seconds: 1, prices_vat_mode: "with_vat", enabled: true });
    productId = (await api(page, "POST", "/products", { name: PRODUCT, prices: [{ market: "CZ", price: "80" }] })).id;
    const offer = (await api(page, "POST", `/competitors/${competitor.id}/offers`,
                             { urls: ["https://hist.alter.e2e.test/produkt/716"], variant: "" })).created[0];
    await api(page, "POST", `/offers/${offer.id}/assignment`, { action: "assign", product_id: productId });
    await request.get(`${SHOP}/__set_price?id=716&value=65,00`);
    await check(page, offer.id);
    await request.get(`${SHOP}/__set_price?id=716&value=50,00`);
    await check(page, offer.id);
  });

  test("graf a tabuľka vývoja ceny", async ({ page }) => {
    await login(page, `/historia?product=${productId}`);
    await expect(page.getByText(`${PRODUCT} · trh CZ`)).toBeVisible();
    const chart = page.getByRole("img", { name: /Vývoj ceny produktu/ });
    await expect(chart.locator(".recharts-line")).toHaveCount(1);
    await expect(page.getByLabel("Legenda").getByText("Alter (história)")).toBeVisible();
    await expect(page.getByLabel("Legenda").getByText("Vaša cena")).toBeVisible();
    await expect(chart.locator("circle.change-dot")).toHaveCount(2);  // one dot per recorded change
    await page.locator("label", { hasText: "Tabuľka" }).click();
    const table = page.locator("table").first();
    await expect(table.locator("tbody tr")).toHaveCount(2);
    await expect(table.locator("tbody tr").first()).toContainText("50,00");
    const changes = page.getByRole("heading", { name: "Zaznamenané zmeny" }).locator("..").locator("..");
    await expect(changes.getByText("cena ↓").first()).toBeVisible();
  });

  test("upozornenia: lacnejší konkurent a pokles ceny", async ({ page }) => {
    await login(page, "/upozornenia");
    const rows = page.getByRole("table").locator("tbody tr").filter({ hasText: PRODUCT });
    await expect(rows.filter({ hasText: "Cena konkurenta klesla" })).toContainText("Cena klesla o 23.1 %");
    await expect(rows.filter({ hasText: "Konkurent je lacnejší ako my" })).toContainText("lacnejší o 37.5 %");
    await expect(page.getByRole("link", { name: /Upozornenia \(\d+ otvorených\)/ })).toBeVisible();

    const drop = rows.filter({ hasText: "Cena konkurenta klesla" });
    await drop.getByRole("button", { name: "Potvrdiť" }).click();
    await expect(drop.getByText("Potvrdené")).toBeVisible();
    await drop.getByRole("button", { name: "Vyriešiť" }).click();
    await expect(rows.filter({ hasText: "Cena konkurenta klesla" })).toHaveCount(0);

    // our own price drops below the competitor's: the condition alert resolves by itself
    await api(page, "PATCH", `/products/${productId}`, { prices: [{ market: "CZ", price: "45" }] });
    await page.reload();
    await expect(page.getByRole("table").locator("tbody tr").filter({ hasText: PRODUCT })).toHaveCount(0);
  });

  test("pravidlá upozornení v nastaveniach", async ({ page }) => {
    await login(page, "/nastavenia");
    const row = page.getByRole("row", { name: /Cena konkurenta klesla/ });
    const threshold = row.getByRole("textbox", { name: "o viac ako" });
    await threshold.fill("25");
    await threshold.blur();
    await expect(async () => {
      const rules = await api(page, "GET", "/alert-rules");
      expect(rules.find((r: { rule_type: string }) => r.rule_type === "price_drop").params.percent).toBe(25);
    }).toPass({ timeout: 5_000 });
    await page.reload();
    await expect(page.getByRole("row", { name: /Cena konkurenta klesla/ }).getByRole("textbox", { name: "o viac ako" }))
      .toHaveValue("25");
  });
});
