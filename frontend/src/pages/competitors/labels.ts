import type { CompetitorStatus, CompetitorType, ScrapeRun, SourceType, VatMode } from "../../api/competitors";

export const STATUS_LABELS: Record<CompetitorStatus, { label: string; color: string }> = {
  active: { label: "Aktívny", color: "green" },
  paused: { label: "Pozastavený", color: "yellow" },
  archived: { label: "Archivovaný", color: "gray" },
};

export const TYPE_LABELS: Record<CompetitorType, string> = {
  direct: "Priamy konkurent",
  distributor: "Distribútor",
  marketplace: "Marketplace",
  other: "Iný",
};

export const SOURCE_LABELS: Record<SourceType, string> = { api: "API", feed: "Produktový feed", html: "HTML stránka" };

export const VAT_LABELS: Record<VatMode, string> = {
  with_vat: "Ceny s DPH",
  without_vat: "Ceny bez DPH",
  unknown: "Neznáme",
};

export const RUN_STATUS_LABELS: Record<ScrapeRun["status"], { label: string; color: string }> = {
  running: { label: "Prebieha", color: "blue" },
  succeeded: { label: "Úspešný", color: "green" },
  partial: { label: "Čiastočne", color: "yellow" },
  failed: { label: "Neúspešný", color: "red" },
};

export const COUNTRY_OPTIONS = [
  { value: "SK", label: "SK – Slovensko" },
  { value: "CZ", label: "CZ – Česko" },
  { value: "PL", label: "PL – Poľsko" },
  { value: "HU", label: "HU – Maďarsko" },
  { value: "AT", label: "AT – Rakúsko" },
  { value: "DE", label: "DE – Nemecko" },
];

export const CSS_FIELDS: { key: "price" | "name" | "original_price" | "currency" | "availability" | "identifier";
                          label: string; placeholder: string }[] = [
  { key: "price", label: "Cena (povinné)", placeholder: ".product-price" },
  { key: "name", label: "Názov produktu", placeholder: "h1" },
  { key: "original_price", label: "Pôvodná cena", placeholder: ".price-before" },
  { key: "currency", label: "Mena", placeholder: "[itemprop=priceCurrency]" },
  { key: "availability", label: "Dostupnosť", placeholder: ".stock-status" },
  { key: "identifier", label: "EAN / ISBN / SKU", placeholder: ".product-code" },
];
