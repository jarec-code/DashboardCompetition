import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "e2e-heslo-12345";

async function login(page: Page, path = "/produkty") {
  await page.goto(path);
  await page.getByLabel("E-mail").fill("admin@e2e.test");
  await page.getByLabel("Heslo").fill(PASSWORD);
  await page.getByRole("button", { name: "Prihlásiť sa" }).click();
  await expect(page.getByRole("button", { name: "Odhlásiť" })).toBeVisible();
}

/** Calls the API with the logged-in session (for test setup only). */
async function apiPost(page: Page, path: string, body: unknown) {
  return page.evaluate(async ([p, b]) => {
    const token = (await (await fetch("/api/auth/me")).json()).csrf_token;
    const r = await fetch(`/api${p}`, { method: "POST", body: JSON.stringify(b),
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token } });
    return r.json();
  }, [path, body] as const);
}

const rows = (page: Page) => page.getByRole("table").locator("tbody tr");

test.describe.serial("Produkty a ponuky", () => {
  test("pridanie produktu s cenami pre dva trhy", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "Pridať produkt" }).click();
    const dialog = page.getByRole("dialog", { name: "Nový produkt" });
    await dialog.getByLabel("Názov").fill("Hravá matematika 2 – pracovní učebnice – 1. díl");
    await dialog.getByLabel("ISBN").fill("978-0-306-40615-8");
    await dialog.getByLabel("Predmet").fill("matematika");
    await dialog.getByLabel("Ročník").fill("2");
    await dialog.getByRole("textbox", { name: "Cena" }).fill("4,90");
    await dialog.getByRole("button", { name: "Pridať trh" }).click();
    await dialog.getByRole("textbox", { name: "Cena" }).nth(1).fill("114");
    await dialog.getByRole("button", { name: "Pridať", exact: true }).click();
    await expect(dialog.getByText(/Neplatné ISBN/)).toBeVisible();

    await dialog.getByLabel("ISBN").fill("978-0-306-40615-7");
    await dialog.getByRole("button", { name: "Pridať", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Hravá matematika 2 – pracovní učebnice – 1. díl" })).toBeVisible();
    await expect(page.getByText("9780306406157")).toBeVisible();
    await expect(page.getByText(/SK:\s*4,90\s*€/)).toBeVisible();
    await expect(page.getByText(/CZ:\s*114,00\s*(Kč|CZK)/)).toBeVisible();
  });

  test("import produktov z CSV s náhľadom", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "Import", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Import vlastných produktov" });
    const csv = "name;price;currency\nNaše vlast – pracovní listy;44,00;CZK\nDetektiv Písmenko 5;159;CZK\nBez ceny;;CZK\n";
    await dialog.locator("input[type=file]").setInputFiles({ name: "produkty.csv", mimeType: "text/csv",
                                                            buffer: Buffer.from(csv) });
    await dialog.getByRole("button", { name: "Načítať náhľad" }).click();
    await expect(dialog.getByText("v poriadku: 2")).toBeVisible();
    await expect(dialog.getByText("chyba: 1")).toBeVisible();
    await expect(dialog.getByText("Chýba cena.")).toBeVisible();
    const list = page.getByRole("tabpanel", { name: "Vlastné produkty" }).locator("tbody tr");
    await expect(list).toHaveCount(1);                        // nothing saved before confirming
    await dialog.getByRole("button", { name: "Importovať 2 riadky" }).click();
    await expect(dialog.getByText(/Importované:\s*2/)).toBeVisible();
    await dialog.getByRole("button", { name: "Zavrieť" }).click();
    await expect(rows(page)).toHaveCount(3);

    await page.getByLabel("Hľadať").fill("písmenko");
    await expect(rows(page)).toHaveCount(1);
    await page.getByLabel("Hľadať").fill("0-306-40615-2");  // ISBN-10 of the first product
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText("Hravá matematika 2");
  });

  test("pridanie URL ponúk konkurenta", async ({ page }) => {
    await login(page);
    const competitor = await apiPost(page, "/competitors", { name: "Fraus", base_url: "https://www.fraus.cz", country: "CZ" });
    await page.goto(`/konkurencia/${competitor.id}`);
    await page.getByRole("tab", { name: "Sledované produkty" }).click();
    await expect(page.getByText("Zatiaľ nie sú sledované žiadne produktové URL.")).toBeVisible();

    await page.getByRole("button", { name: "Pridať URL" }).click();
    await page.getByLabel("URL produktových stránok").fill([
      "https://www.fraus.cz/produkt/nase-vlast", "https://www.fraus.cz/produkt/matematika-2",
      "https://www.fraus.cz/produkt/nase-vlast", "https://www.alter.cz/produkt/1",
    ].join("\n"));
    await page.getByRole("button", { name: "Pridať (4)" }).click();
    await expect(page.getByText(/Pridané:\s*2, duplicity:\s*1,\s*neplatné:\s*1/)).toBeVisible();
    await expect(page.getByText(/alter\.cz\/produkt\/1: URL nepatrí doméne konkurenta/)).toBeVisible();
    await expect(rows(page)).toHaveCount(2);
    await expect(rows(page).first()).toContainText("zatiaľ nekontrolované");
    await expect(rows(page).first()).toContainText("Nepriradená");
  });

  test("ručné priradenie, ignorovanie a párovanie", async ({ page }) => {
    await login(page, "/konkurencia");
    await page.getByRole("link", { name: "Fraus", exact: true }).click();
    await page.getByRole("tab", { name: "Sledované produkty" }).click();

    const row = rows(page).filter({ hasText: "/produkt/matematika-2" });
    await row.getByRole("button", { name: "Priradiť k produktu" }).click();
    const dialog = page.getByRole("dialog", { name: "Priradiť k vlastnému produktu" });
    await dialog.getByLabel("Hľadať produkt").fill("matematika 2");
    await dialog.getByRole("row", { name: /Hravá matematika 2/ }).getByRole("button", { name: "Priradiť" }).click();
    await expect(row.getByRole("link", { name: /Hravá matematika 2/ })).toBeVisible();
    await expect(row.getByText("ručne")).toBeVisible();

    const other = rows(page).filter({ hasText: "/produkt/nase-vlast" });
    await other.getByRole("button", { name: "Ďalšie akcie" }).click();
    await page.getByRole("menuitem", { name: "Ignorovať (nepárovať)" }).click();
    await expect(other.getByText("Ignorovaná")).toBeVisible();

    await row.getByRole("link", { name: /Hravá matematika 2/ }).click();
    await expect(page.getByRole("heading", { name: /Konkurenčné ponuky \(1\)/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "Fraus" })).toBeVisible();

    await page.goto("/produkty?tab=parovanie");
    await page.getByRole("combobox", { name: "Zobraziť" }).click();
    await page.getByRole("option", { name: "Ignorované" }).click();
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText("/produkt/nase-vlast");
  });
});
