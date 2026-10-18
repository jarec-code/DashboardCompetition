import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, query } from "./client";
import type { Page } from "./competitors";

export interface Monitoring {
  now: string;
  timezone: string;
  scheduler_enabled: boolean;
  in_window: boolean;
  scheduler_last_tick_at: string | null;
  worker_last_seen_at: string | null;
  pending_jobs: number;
  running_jobs: number;
  due_offers: number;
  stale_prices: number;
  checks_24h: number;
  failures_24h: number;
  price_changes_24h: number;
  problem_competitors: { id: number; name: string; failed_runs_in_row: number; last_error: string | null }[];
  problem_offers: { id: number; competitor_id: number; competitor_name: string; product_url: string;
                    consecutive_failures: number; last_error: string | null; last_success_at: string | null }[];
}

export interface RunItem {
  id: number;
  competitor_id: number;
  competitor_name: string;
  trigger: "scheduled" | "manual";
  status: "running" | "succeeded" | "partial" | "failed";
  started_at: string;
  finished_at: string | null;
  total_count: number;
  success_count: number;
  failure_count: number;
  price_change_count: number;
  error_summary: string | null;
}

export interface JobItem {
  id: number;
  kind: "scheduled" | "manual" | "test";
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  competitor_id: number;
  competitor_name: string;
  competitor_product_id: number | null;
  url: string | null;
  attempt_count: number;
  scheduled_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
  error_kind: "temporary" | "permanent" | null;
}

export interface SchedulingSettings {
  scheduler_enabled: boolean;
  priority_interval_hours: { strategic: number; regular: number; stable: number };
  window_start_hour: number | null;
  window_end_hour: number | null;
  batch_size: number;
}

export function useMonitoring(refetchSeconds = 15) {
  return useQuery({ queryKey: ["monitoring"], queryFn: () => api<Monitoring>("/monitoring"),
                    refetchInterval: refetchSeconds * 1000 });
}

export function useRuns(params: { competitor_id?: number; status?: string; trigger?: string; page: number }) {
  return useQuery({ queryKey: ["runs", params], queryFn: () => api<Page<RunItem>>(`/runs${query({ ...params })}`),
                    placeholderData: keepPreviousData, refetchInterval: 15_000 });
}

export function useJobs(params: { status?: string; kind?: string; competitor_id?: number; page: number }) {
  return useQuery({ queryKey: ["jobs-list", params], queryFn: () => api<Page<JobItem>>(`/jobs${query({ ...params })}`),
                    placeholderData: keepPreviousData, refetchInterval: 15_000 });
}

export function useSchedulingSettings() {
  return useQuery({ queryKey: ["settings", "scheduling"],
                    queryFn: () => api<SchedulingSettings>("/settings/scheduling") });
}

export function useSaveSchedulingSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: SchedulingSettings) =>
      api<SchedulingSettings>("/settings/scheduling", { method: "PUT", body: data }),
    onSuccess: () => ["settings", "monitoring", "offers"].forEach((k) =>
      queryClient.invalidateQueries({ queryKey: [k] })),
  });
}
