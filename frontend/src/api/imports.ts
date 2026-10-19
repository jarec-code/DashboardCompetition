import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, getCsrfToken } from "./client";

export type RowStatus = "ok" | "warning" | "duplicate" | "error";

export interface ImportRow {
  row: number;
  status: RowStatus;
  reason: string | null;
  data: Record<string, unknown>;
}

export interface ImportBatch {
  id: number;
  kind: "products" | "offers";
  status: "preview" | "committed" | "discarded";
  file_name: string;
  file_format: string;
  competitor_id: number | null;
  rows: ImportRow[];
  stats: { ok: number; warning: number; duplicate: number; error: number };
  created_at: string;
}

export interface CommitResult {
  imported: number;
  duplicates: number;
  errors: number;
  skipped_at_commit: { row: number; reason: string }[];
}

/** Multipart upload; returns the validated preview. Nothing is saved until commit. */
export function useUploadImport() {
  return useMutation({
    mutationFn: async ({ file, kind, competitorId }: { file: File; kind: "products" | "offers"; competitorId?: number }) => {
      const body = new FormData();
      body.append("file", file);
      body.append("kind", kind);
      if (competitorId !== undefined) body.append("competitor_id", String(competitorId));
      const response = await fetch("/api/imports", {
        method: "POST", body, credentials: "same-origin", headers: { "X-CSRF-Token": getCsrfToken() ?? "" },
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = data?.detail;
        throw new ApiError(response.status, typeof detail === "string" ? detail : `Import zlyhal (${response.status}).`);
      }
      return data as ImportBatch;
    },
  });
}

export function useCommitImport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<CommitResult>(`/imports/${id}/commit`, { method: "POST" }),
    onSuccess: () => ["products", "offers", "competitors", "comparison"].forEach((k) =>
      queryClient.invalidateQueries({ queryKey: [k] })),
  });
}

export function useDiscardImport() {
  return useMutation({ mutationFn: (id: number) => api<void>(`/imports/${id}/discard`, { method: "POST" }) });
}
