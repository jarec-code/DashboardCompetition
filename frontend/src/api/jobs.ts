import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "./client";

export interface Extracted {
  price: string | null;
  currency: string | null;
  original_price: string | null;
  availability: string;
  vat_mode: string;
  title: string | null;
  identifiers: Record<string, string>;
  variant: string | null;
  available_variants: string[];
  source: string;
}

export interface JobResult {
  url?: string;
  final_url?: string;
  http_status?: number;
  elapsed_ms?: number;
  parser?: string;
  parser_config_version?: number;
  variant?: string;
  extracted?: Extracted;
  change_types?: string[];
  not_modified?: boolean;
}

export interface Job {
  id: number;
  kind: "scheduled" | "manual" | "test";
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  competitor_id: number;
  competitor_product_id: number | null;
  url: string | null;
  attempt_count: number;
  scheduled_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
  error_kind: "temporary" | "permanent" | null;
  result: JobResult | null;
  created_at: string;
}

export interface ParserInfo {
  name: string;
  label: string;
  description: string;
  version: number;
  config_kind: "none" | "css" | "xpath";
}

export interface HistoryRow {
  observed_at: string;
  price: string | null;
  original_price: string | null;
  currency: string | null;
  availability: string;
  vat_mode: string;
  change_types: string[];
}

export const isOpen = (job?: Job | null) => job?.status === "pending" || job?.status === "running";

/** Polls a job every second while it is waiting or running. */
export function useJob(id: number | null) {
  return useQuery({
    queryKey: ["jobs", id],
    queryFn: () => api<Job>(`/jobs/${id}`),
    enabled: id !== null,
    refetchInterval: (query) => (isOpen(query.state.data) || !query.state.data ? 1000 : false),
  });
}

/** Starts a job with `start`, follows it until it ends and then calls `onDone` once. */
export function useJobRunner(start: () => Promise<Job>, onDone?: (job: Job) => void) {
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId);
  const reported = useRef<number | null>(null);
  const mutation = useMutation({ mutationFn: start, onSuccess: (j) => { reported.current = null; setJobId(j.id); } });

  useEffect(() => {
    const data = job.data;
    if (data && !isOpen(data) && reported.current !== data.id) {
      reported.current = data.id;
      ["offers", "competitors", "products"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
      onDone?.(data);
    }
  }, [job.data]);

  const running = mutation.isPending || (jobId !== null && (!job.data || isOpen(job.data)));
  return { run: () => mutation.mutate(), running, job: job.data ?? null, error: mutation.error,
           reset: () => { setJobId(null); mutation.reset(); } };
}

export function useParsers() {
  return useQuery({ queryKey: ["parsers"], queryFn: () => api<ParserInfo[]>("/parsers"), staleTime: Infinity });
}

export function useOfferHistory(offerId: number | null) {
  return useQuery({
    queryKey: ["offers", "history", offerId],
    queryFn: () => api<HistoryRow[]>(`/offers/${offerId}/history`),
    enabled: offerId !== null,
  });
}

export const checkOffer = (offerId: number) => api<Job>(`/offers/${offerId}/check`, { method: "POST" });
export const testCompetitor = (competitorId: number, url: string, variant: string) =>
  api<Job>(`/competitors/${competitorId}/test`, { method: "POST", body: { url, variant } });
