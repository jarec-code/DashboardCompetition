import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, query } from "./client";

export type CompetitorStatus = "active" | "paused" | "archived";
export type CompetitorType = "direct" | "distributor" | "marketplace" | "other";
export type SourceType = "api" | "feed" | "html";
export type ParserType = "auto" | "jsonld" | "microdata" | "opengraph" | "css";
export type VatMode = "with_vat" | "without_vat" | "unknown";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface CompetitorListItem {
  id: number;
  name: string;
  base_url: string;
  domain: string;
  country: string | null;
  market: string | null;
  competitor_type: CompetitorType;
  status: CompetitorStatus;
  default_frequency_hours: number | null;
  offer_count: number;
  last_success_at: string | null;
  last_run_error_count: number;
  updated_at: string;
}

export interface CssSelectors {
  price: string;
  name?: string;
  original_price?: string;
  currency?: string;
  availability?: string;
  identifier?: string;
}

export interface ScrapeConfigInput {
  source_type: SourceType;
  parser_type: ParserType;
  parser_config: Partial<CssSelectors>;
  timeout_seconds: number;
  max_concurrency: number;
  max_retries: number;
  min_interval_seconds: number;
  prices_vat_mode: VatMode;
  enabled: boolean;
}

export interface ScrapeConfig extends ScrapeConfigInput {
  parser_version: number;
  last_test_status: string | null;
  last_test_at: string | null;
  updated_at: string;
}

export interface Competitor {
  id: number;
  name: string;
  base_url: string;
  domain: string;
  duplicate_domain_reason: string | null;
  country: string | null;
  market: string | null;
  competitor_type: CompetitorType;
  preferred_source: SourceType;
  notes: string | null;
  status: CompetitorStatus;
  default_frequency_hours: number | null;
  created_at: string;
  updated_at: string;
  scrape_config: ScrapeConfig;
}

export interface CompetitorInput {
  name: string;
  base_url: string;
  country: string | null;
  market: string | null;
  competitor_type: CompetitorType;
  preferred_source: SourceType;
  notes: string | null;
  status: CompetitorStatus;
  default_frequency_hours: number | null;
  duplicate_domain_reason?: string | null;
  scrape_config?: Partial<ScrapeConfigInput>;
}

export interface ScrapeRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: "running" | "succeeded" | "partial" | "failed";
  total_count: number;
  success_count: number;
  failure_count: number;
  price_change_count: number;
  error_summary: string | null;
}

export interface CompetitorListParams {
  q?: string;
  status?: string;
  country?: string;
  sort?: string;
  page?: number;
  page_size?: number;
}

const KEY = "competitors";

export function useCompetitors(params: CompetitorListParams) {
  return useQuery({
    queryKey: [KEY, "list", params],
    queryFn: () => api<Page<CompetitorListItem>>(`/competitors${query({ ...params })}`),
    placeholderData: keepPreviousData,
  });
}

export function useCompetitorFilters() {
  return useQuery({
    queryKey: [KEY, "filters"],
    queryFn: () => api<{ countries: string[] }>("/competitors/filters"),
  });
}

export function useCompetitor(id: number) {
  return useQuery({ queryKey: [KEY, "detail", id], queryFn: () => api<Competitor>(`/competitors/${id}`) });
}

export function useCompetitorRuns(id: number) {
  return useQuery({ queryKey: [KEY, "runs", id], queryFn: () => api<ScrapeRun[]>(`/competitors/${id}/runs`) });
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: [KEY] });
}

export function useCreateCompetitor() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (data: CompetitorInput) => api<Competitor>("/competitors", { method: "POST", body: data }),
    onSuccess: invalidate,
  });
}

export function useUpdateCompetitor(id: number) {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (data: Partial<CompetitorInput>) => api<Competitor>(`/competitors/${id}`, { method: "PATCH", body: data }),
    onSuccess: invalidate,
  });
}

export function useUpdateScrapeConfig(id: number) {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (data: ScrapeConfigInput) =>
      api<ScrapeConfig>(`/competitors/${id}/scrape-config`, { method: "PUT", body: data }),
    onSuccess: invalidate,
  });
}

export function useDeleteCompetitor() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => api<void>(`/competitors/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}
