import { expect, test, type Page } from "@playwright/test";

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

const rows = (page: Page) => page.getByRole("table").locator("tbody tr");

test.describe.serial("Import ponúk a načítanie zo stránky kategórie", () => {
  let competitorId: number;

  test("import ponúk zo súboru s priradením podľa EAN", async ({ page }) => {
    await login(page, "/konkurencia");
    competitorId = (await api(page, "POST", "/competitors", { name: "Alter (import)", base_url: "https://imp.alter.e2e.test",
                                                             country: "CZ", scrape_config: { min_interval_seconds: 1 } })).id;
    await api(page, "POST", "/products", { name: "Sprievodca prvoukou (import)", ean: "8594165541536",
                                           prices: [{ market: "CZ", price: "189" }] });
    await page.goto(`/konkurencia/${competitorId}`);
    await page.getByRole("tab", { name: "Sledované produkty" }).click();
    await page.getByRole("button", { name: "Import zo súboru" }).click();
    const dialog = page.getByRole("dialog", { name: "Import ponúk zo súboru" });
    const csv = "url,product_ean\nhttps://imp.alter.e2e.test/produkt/716,8594165541536\n" +
                "https://imp.alter.e2e.test/produkt/1111,\nhttps://www.fraus.cz/p/1,\n";
    await dialog.locator("input[type=file]").setInputFiles({ name: "ponuky.csv", mimeType: "text/csv",
                                                            buffer: Buffer.from(csv) });
    await dialog.getByRole("button", { name: "Načítať náhľad" }).click();
    await expect(dialog.getByText("nový: 2")).toBeVisible();
    await expect(dialog.getByText(/nepatrí žiadnemu evidovanému konkurentovi/)).toBeVisible();
    await expect(dialog.getByText(/→ Sprievodca prvoukou \(import\)/)).toBeVisible();
    await dialog.getByRole("button", { name: "Uložiť 2 riadky" }).click();
    await expect(dialog.getByText(/nové:\s*2/)).toBeVisible();
    await dialog.getByRole("button", { name: "Zavrieť" }).click();
    await expect(rows(page)).toHaveCount(2);
    await expect(rows(page).filter({ hasText: "/produkt/716" }).getByRole("link", { name: "Sprievodca prvoukou (import)" }))
      .toBeVisible();
  });

  test("odkazy zo stránky kategórie", async ({ page }) => {
    await login(page, `/konkurencia/${competitorId}`);
    await page.getByRole("tab", { name: "Sledované produkty" }).click();
    await page.getByRole("button", { name: "Pridať URL" }).click();
    await page.getByRole("tab", { name: "Zo stránky kategórie" }).click();
    await page.getByLabel("Stránka kategórie alebo výpis produktov").fill("https://imp.alter.e2e.test/novinky");
    await page.getByRole("button", { name: "Načítať odkazy" }).click();
    await expect(page.getByText(/Nájdené odkazy: 2/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("checkbox", { name: /\/produkt\/1111 · už sledované/ })).toBeDisabled();
    await expect(page.getByText("vybrané: 1")).toBeVisible();
    await page.getByRole("button", { name: "Pridať vybrané (1)" }).click();
    await expect(page.getByText(/Pridané: 1, duplicity: 0/)).toBeVisible();
    await expect(rows(page)).toHaveCount(3);
  });
});
