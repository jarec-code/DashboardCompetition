import type { MatchStatus } from "../../api/offers";
import type { Priority } from "../../api/products";

export const PRIORITY_LABELS: Record<Priority, { label: string; color: string; description: string }> = {
  strategic: { label: "Strategický", color: "grape", description: "kontrola každých 6–12 hodín" },
  regular: { label: "Bežný", color: "blue", description: "kontrola raz denne" },
  stable: { label: "Stabilný", color: "gray", description: "kontrola každé 3–7 dní" },
};

export const MATCH_LABELS: Record<MatchStatus, { label: string; color: string }> = {
  matched: { label: "Priradená", color: "green" },
  pending_review: { label: "Čaká na kontrolu", color: "orange" },
  unmatched: { label: "Nepriradená", color: "gray" },
  ignored: { label: "Ignorovaná", color: "dark" },
};

export const METHOD_LABELS: Record<string, string> = {
  identifier: "ISBN / EAN / SKU",
  name: "podľa názvu",
  manual: "ručne",
  ai: "AI",
  attributes: "automaticky (porovnateľný titul)",
};

export const AVAILABILITY_LABELS: Record<string, { label: string; color: string }> = {
  in_stock: { label: "Skladom", color: "green" },
  out_of_stock: { label: "Nedostupné", color: "red" },
  preorder: { label: "Predobjednávka", color: "blue" },
  unknown: { label: "Neznáma", color: "gray" },
};
