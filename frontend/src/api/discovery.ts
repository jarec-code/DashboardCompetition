import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, query } from "./client";
import type { Page } from "./competitors";

export type DiscoveryMode = "off" | "shadow" | "auto";
export type SourceType = "sitemap" | "feed" | "listing" | "adapter" | "crawl";
export type ItemMatch = "new" | "matched" | "review" | "unrelated" | "ignored";

export interface DiscoverySource { type: SourceType; urls: string[]; pattern: string | null; adapter: string | null;
                                   detected: boolean }

export interface DiscoverySettings {
  mode: DiscoveryMode;
  terms_confirmed_at: string | null;
  terms_confirmed_by: string | null;
  interval_days: number;
  budget_per_night: number;
  spacing_seconds: number;
  max_pages: number;
  max_depth: number;
  max_run_days: number;
  source: DiscoverySource | null;
  strategy: Strategy;
  feed_url: string | null;
  entry_urls: string[];
  extra_domains: string[];
  include_patterns: string[];
  exclude_patterns: string[];
  product_pattern: string | null;
  drop_params: string[];
  diagnosis: Diagnosis | Record<string, never>;
  run_requested: boolean;
  next_run_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  paused_until: string | null;
  shadow_ok_runs: number;
  budget_used: number;
  budget_date: string | null;
}

export type Strategy = "auto" | "adapter" | "feed" | "sitemap" | "crawl";

export interface MethodQuality {
  found: number; read: number; priced: number; titled: number; plausible: number; sufficient: boolean;
  reason: string | null;
}

export interface Diagnosis {
  at: string;
  chosen: SourceType[];
  plan: SourceType[];
  methods: Partial<Record<SourceType, MethodQuality>>;
  suggestions: string[];
  quality: MethodQuality;
  stale: boolean;
}

export interface Recipe {
  strategy: Strategy;
  feed_url: string | null;
  entry_urls: string[];
  extra_domains: string[];
  include_patterns: string[];
  exclude_patterns: string[];
  product_pattern: string | null;
  drop_params: string[];
}

export interface DiscoveryRun {
  id: number;
  kind: "discovery" | "prices";
  mode: DiscoveryMode;
  status: "running" | "succeeded" | "partial" | "failed" | "stopped";
  phase: string;
  source_type: SourceType | null;
  requests: number;
  stats: Record<string, number | string | boolean | null> & {
    attempts?: { what: string; result: string }[];
    templates?: Record<string, { pages: number; products: number; listings: number }>;
    incomplete?: string[];
  };
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface DiscoveryState {
  settings: DiscoverySettings;
  runs: DiscoveryRun[];
  counts: Partial<Record<ItemMatch | "gone", number>>;
}

export interface DiscoverySettingsInput {
  mode: DiscoveryMode;
  terms_confirmed: boolean;
  interval_days: number;
  budget_per_night: number;
  spacing_seconds: number;
  max_pages: number;
  max_depth: number;
  max_run_days: number;
  source?: { type: SourceType; urls: string[]; pattern?: string | null } | null;
  recipe?: Recipe;
  redetect?: boolean;
}

export interface CatalogItem {
  id: number;
  key: string;
  url: string;
  title: string | null;
  price: string | null;
  currency: string | null;
  price_from_listing: boolean;
  hints: Record<string, string>;
  attributes: { grades?: string[]; subjects?: string[]; type?: string | null; parts?: string[]; bundle?: boolean;
                note?: string | null };
  status: "active" | "gone";
  match_status: ItemMatch;
  match_method: "identifier" | "attributes" | "manual" | "existing" | null;
  product_id: number | null;
  product_name: string | null;
  offer_id: number | null;
  candidates: { product_id: number; name: string; exact: boolean; score: number }[];
  enrich_error: string | null;
  found_by: SourceType | null;
  parsed_by: string | null;
  fetch_status: "ok" | "no_price" | "not_product" | "failed" | "skipped" | null;
  fetched_at: string | null;
  canonical_url: string | null;
  category: string | null;
  completeness: number | null;
  first_seen_at: string;
  last_seen_at: string;
}

const key = (id: number) => ["discovery", id];

export function useDiscovery(competitorId: number) {
  return useQuery({
    queryKey: key(competitorId),
    queryFn: () => api<DiscoveryState>(`/competitors/${competitorId}/discovery`),
    refetchInterval: (q) => (q.state.data?.runs[0]?.status === "running" || q.state.data?.settings.run_requested
      ? 5_000 : false),
  });
}

export function useSaveDiscovery(competitorId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: DiscoverySettingsInput) =>
      api<DiscoveryState>(`/competitors/${competitorId}/discovery`, { method: "PUT", body: data }),
    onSuccess: (state) => queryClient.setQueryData(key(competitorId), state),
  });
}

export function useRunDiscovery(competitorId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api<DiscoveryState>(`/competitors/${competitorId}/discovery/run`, { method: "POST" }),
    onSuccess: (state) => queryClient.setQueryData(key(competitorId), state),
  });
}

export function useCatalog(competitorId: number, params: { match_status?: ItemMatch; item_status?: "active" | "gone";
                                                            q?: string; incomplete?: boolean; page: number;
                                                            page_size?: number }) {
  return useQuery({
    queryKey: ["catalog", competitorId, params],
    queryFn: () => api<Page<CatalogItem>>(`/competitors/${competitorId}/catalog${query({
      ...params, incomplete: params.incomplete ? "true" : undefined })}`),
    placeholderData: keepPreviousData,
  });
}

export function useItemAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, productId }: { id: number; action: "assign" | "ignore" | "reset"; productId?: number }) =>
      api<CatalogItem>(`/catalog-items/${id}/${action}`, {
        method: "POST", body: action === "assign" ? { product_id: productId } : undefined }),
    onSuccess: () => ["catalog", "discovery", "offers", "comparison"].forEach((k) =>
      queryClient.invalidateQueries({ queryKey: [k] })),
  });
}

export interface UrlsState {
  by_status: Record<string, number>;
  by_kind: Record<string, number>;
  problems: { template: string; status: "failed" | "skipped"; reason: string | null; count: number; examples: string[] }[];
}

export function useDiscoveryUrls(competitorId: number, enabled: boolean) {
  return useQuery({ queryKey: ["catalog", competitorId, "urls"], enabled,
                    queryFn: () => api<UrlsState>(`/competitors/${competitorId}/discovery/urls`) });
}
