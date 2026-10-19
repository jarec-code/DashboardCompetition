import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface CatalogSyncSettings {
  enabled: boolean;
  feed_url: string | null;
  market: string;
  interval_hours: number;
}

export interface CatalogSyncState {
  settings: CatalogSyncSettings;
  status: {
    requested_at: string | null;
    last_run_at: string | null;
    last_success_at: string | null;
    last_error: string | null;
    last_batch_id: number | null;
  };
  last_run: {
    batch_id: number;
    file_name: string;
    committed_at: string | null;
    stats: Record<string, number>;
    problems: { row: number; status: string; reason: string; name: string | null; internal_id: string | null }[];
  } | null;
}

const KEY = ["settings", "catalog-sync"];

/** Polls while a requested synchronization waits for the worker. */
export function useCatalogSync() {
  return useQuery({
    queryKey: KEY, queryFn: () => api<CatalogSyncState>("/settings/catalog-sync"),
    refetchInterval: (q) => (q.state.data?.status.requested_at ? 5_000 : false),
  });
}

export function useSaveCatalogSync() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CatalogSyncSettings) =>
      api<CatalogSyncState>("/settings/catalog-sync", { method: "PUT", body: data }),
    onSuccess: (state) => queryClient.setQueryData(KEY, state),
  });
}

export function useRunCatalogSync() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api<CatalogSyncState>("/settings/catalog-sync/run", { method: "POST" }),
    onSuccess: (state) => queryClient.setQueryData(KEY, state),
  });
}
