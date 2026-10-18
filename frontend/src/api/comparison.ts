import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, query } from "./client";

export interface OfferCell {
  offer_id: number;
  competitor_id: number;
  competitor_name: string;
  product_url: string;
  price: string | null;
  currency: string | null;
  availability: string;
  vat_mode: string;
  last_success_at: string | null;
  is_stale: boolean;
  last_error: string | null;
  comparable: boolean;
  reason: "no_price" | "no_own_price" | "currency_mismatch" | "vat_mismatch" | null;
  vat_unverified: boolean;
  diff_abs: string | null;
  diff_pct: string | null;
}

export interface ComparisonRow {
  product_id: number;
  product_name: string;
  priority: string;
  market: string;
  own_price: string | null;
  own_currency: string | null;
  own_vat_mode: string | null;
  offers: OfferCell[];
  min_price: string | null;
  max_price: string | null;
  cheaper_count: number;
  position: "cheapest" | "not_cheapest" | "unknown";
  max_undercut_pct: string | null;
}

export interface Comparison {
  items: ComparisonRow[];
  total: number;
  page: number;
  page_size: number;
  markets: string[];
  competitors: { id: number; name: string }[];
  summary: { cheapest: number; not_cheapest: number; unknown: number };
}

export interface ComparisonParams {
  market?: string;
  competitor_id?: number;
  q?: string;
  position?: string;
  sort?: string;
  page?: number;
  page_size?: number;
}

export interface ChangeItem {
  observed_at: string;
  offer_id: number;
  competitor_id: number;
  competitor_name: string;
  product_id: number | null;
  product_name: string | null;
  title: string | null;
  price: string | null;
  previous_price: string | null;
  currency: string | null;
  change_pct: string | null;
  change_types: string[];
}

export interface Dashboard {
  competitors_total: number;
  competitors_active: number;
  offers_tracked: number;
  checks_24h: number;
  failed_checks_24h: number;
  missing_or_stale: number;
  price_changes_24h: number;
  price_changes_7d: number;
  cheaper_competitor_rows: number;
  comparable_rows: number;
  biggest_drops: ChangeItem[];
  recent_changes: ChangeItem[];
  recent_runs: { id: number; competitor_id: number; competitor_name: string; trigger: "scheduled" | "manual";
                 status: "running" | "succeeded" | "partial" | "failed"; started_at: string; success_count: number;
                 total_count: number; failure_count: number }[];
}

export function useComparison(params: ComparisonParams) {
  return useQuery({
    queryKey: ["comparison", params],
    queryFn: () => api<Comparison>(`/comparison${query({ ...params })}`),
    placeholderData: keepPreviousData,
  });
}

export function comparisonExportUrl(params: ComparisonParams): string {
  const { market, competitor_id, q, position } = params;
  return `/api/comparison/export.csv${query({ market, competitor_id, q, position })}`;
}

export function useDashboard() {
  return useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/dashboard"), refetchInterval: 60_000 });
}
