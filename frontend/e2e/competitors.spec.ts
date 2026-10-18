import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "e2e-heslo-12345";

async function login(page: Page, email: string) {
  await page.goto("/konkurencia");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Heslo").fill(PASSWORD);
  await page.getByRole("button", { name: "Prihlásiť sa" }).click();
  await expect(page.getByRole("heading", { name: "Konkurencia" })).toBeVisible();
}

async function addCompetitor(page: Page, name: string, url: string, country?: string) {
  await page.getByRole("button", { name: "Pridať konkurenta" }).click();
  const dialog = page.getByRole("dialog", { name: "Nový konkurent" });
  await dialog.getByLabel("Názov").fill(name);
  await dialog.getByLabel("Základná URL").fill(url);
  if (country) {
    await dialog.getByRole("combobox", { name: "Krajina" }).click();
    await page.getByRole("option", { name: new RegExp(`^${country}`) }).click();
  }
  await dialog.getByRole("button", { name: "Pridať" }).click();
}

function rows(page: Page) {
  return page.getByRole("table").locator("tbody tr");
}

test.describe.serial("Konkurencia", () => {
  test("pridanie konkurenta a zobrazenie detailu", async ({ page }) => {
    await login(page, "admin@e2e.test");
    await expect(page.getByText("Zatiaľ nie sú evidovaní žiadni konkurenti.")).toBeVisible();

    await addCompetitor(page, "Alter", "www.alter.cz", "CZ");
    await expect(page.getByRole("heading", { name: "Alter" })).toBeVisible();
    await expect(page.getByText("Aktívny").first()).toBeVisible();
    await expect(page.getByRole("link", { name: /https:\/\/www\.alter\.cz\// })).toBeVisible();
    await expect(page.getByText("CZ / CZ")).toBeVisible();
  });

  test("úprava konkurenta", async ({ page }) => {
    await login(page, "admin@e2e.test");
    await page.getByRole("link", { name: "Alter", exact: true }).click();
    await page.getByRole("button", { name: "Upraviť" }).click();
    const dialog = page.getByRole("dialog", { name: "Upraviť konkurenta" });
    await dialog.getByLabel("Interná poznámka").fill("Vydavateľstvo učebníc pre 1. stupeň");
    await dialog.getByRole("button", { name: "Uložiť" }).click();
    await expect(page.getByText("Vydavateľstvo učebníc pre 1. stupeň")).toBeVisible();
  });

  test("duplicitná doména vyžaduje dôvod", async ({ page }) => {
    await login(page, "admin@e2e.test");
    await addCompetitor(page, "Alter B2B", "https://alter.cz/skoly");
    const dialog = page.getByRole("dialog", { name: "Nový konkurent" });
    await expect(dialog.getByText("Doména je už evidovaná")).toBeVisible();
    await expect(dialog.getByRole("link", { name: "Otvoriť Alter" })).toBeVisible();
    await dialog.getByLabel("Dôvod samostatného záznamu").fill("Samostatný obchod pre školy");
    await dialog.getByRole("button", { name: "Pridať" }).click();
    await expect(page.getByRole("heading", { name: "Alter B2B" })).toBeVisible();
    await expect(page.getByText("Samostatný obchod pre školy")).toBeVisible();
  });

  test("vyhľadávanie a filtrovanie", async ({ page }) => {
    await login(page, "admin@e2e.test");
    await addCompetitor(page, "Didaktis", "https://www.didaktis.sk", "SK");
    await page.goto("/konkurencia");
    await expect(rows(page)).toHaveCount(3);

    await page.getByLabel("Hľadať").fill("didaktis");
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText("didaktis.sk");
    await expect(page).toHaveURL(/q=didaktis/);

    await page.getByLabel("Hľadať").fill("");
    await expect(rows(page)).toHaveCount(3);
    await page.getByRole("combobox", { name: "Krajina" }).click();
    await page.getByRole("option", { name: "CZ" }).click();
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText("Alter");
  });

  test("pozastavenie a archivácia", async ({ page }) => {
    await login(page, "admin@e2e.test");
    const alterRow = rows(page).filter({ has: page.getByRole("link", { name: "Alter", exact: true }) });
    await alterRow.getByRole("button", { name: "Pozastaviť" }).click();
    await expect(alterRow.getByText("Pozastavený")).toBeVisible();

    const b2bRow = rows(page).filter({ hasText: "Alter B2B" });
    await b2bRow.getByRole("button", { name: "Archivovať" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Archivovať" }).click();
    await expect(rows(page).filter({ hasText: "Alter B2B" })).toHaveCount(0);

    await page.getByRole("combobox", { name: "Stav" }).click();
    await page.getByRole("option", { name: "Archivovaný" }).click();
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText("Alter B2B");

    await page.getByRole("combobox", { name: "Stav" }).click();
    await page.getByRole("option", { name: "Pozastavený" }).click();
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText("Alter");
  });

  test("nastavenie CSS parsera", async ({ page }) => {
    await login(page, "admin@e2e.test");
    await page.getByRole("link", { name: "Alter", exact: true }).click();
    await page.getByRole("tab", { name: "Nastavenia scrapingu" }).click();
    await expect(page.getByText("Verzia parsera 1")).toBeVisible();
    await page.getByRole("combobox", { name: "Parser" }).click();
    await page.getByRole("option", { name: "CSS selektory" }).click();
    await page.getByLabel("Cena (povinné)").fill(".vatPricePD");
    await page.getByLabel("Názov produktu").fill("h1");
    await page.getByRole("button", { name: "Uložiť nastavenia" }).click();
    await expect(page.getByText("Verzia parsera 2")).toBeVisible();

    await page.getByLabel("Cena (povinné)").fill("div[[");
    await page.getByRole("button", { name: "Uložiť nastavenia" }).click();
    await expect(page.getByText(/nie je platný CSS selektor/)).toBeVisible();
  });

  test("čitateľ nemôže meniť", async ({ page }) => {
    await login(page, "viewer@e2e.test");
    await expect(rows(page).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Pridať konkurenta" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Pozastaviť" })).toHaveCount(0);
    await page.getByRole("link", { name: "Didaktis", exact: true }).click();
    await expect(page.getByRole("button", { name: "Upraviť" })).toHaveCount(0);
  });
});
