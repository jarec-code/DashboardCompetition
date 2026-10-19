import { expect, test, type Page } from "@playwright/test";

async function login(page: Page, path: string) {
  await page.goto(path);
  await page.getByLabel("E-mail").fill("admin@e2e.test");
  await page.getByLabel("Heslo").fill("e2e-heslo-12345");
  await page.getByRole("button", { name: "Prihlásiť sa" }).click();
  await expect(page.getByRole("button", { name: "Odhlásiť" })).toBeVisible();
}

// The fake shop serves a sample of the etaktik.cz Heureka feed (8 products, one with price 0).
const FEED = "https://katalog.e2e.test/export/heureka.php";

test("vlastné produkty z feedu e-shopu: synchronizácia a opakovaný beh", async ({ page }) => {
  await login(page, "/nastavenia");
  const section = page.locator("form").filter({ hasText: "Vlastné produkty z feedu e-shopu" });
  await section.getByLabel("Adresa feedu").fill(FEED);
  await section.getByRole("button", { name: "Uložiť" }).click();
  await expect(page.getByText("Nastavenie feedu bolo uložené.")).toBeVisible();

  await section.getByRole("button", { name: "Synchronizovať teraz" }).click();
  await expect(section.getByText("úspešný")).toBeVisible({ timeout: 20_000 });
  await expect(section.getByText(/nové\s*8\s*·\s*aktualizované\s*0/)).toBeVisible();

  await page.goto("/produkty");
  await page.getByLabel("Hľadať").fill("Keep calm");
  await page.getByRole("link", { name: "Keep calm & draw - animals" }).click();
  await expect(page.getByText("katalog.e2e.test (feed)")).toBeVisible();
  await expect(page.getByRole("link", { name: "etaktik.cz/keep-calm-draw-animals/" })).toBeVisible();
  await expect(page.getByText(/CZ:\s*156,00/)).toBeVisible();

  await page.goto("/nastavenia");  // the same feed again: no duplicates, nothing changes
  await section.getByRole("button", { name: "Synchronizovať teraz" }).click();
  await expect(section.getByText(/nové\s*0\s*·\s*aktualizované\s*0\s*·\s*bez zmeny\s*8/)).toBeVisible({ timeout: 20_000 });
});
