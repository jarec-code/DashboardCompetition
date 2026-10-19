import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, query } from "./client";
import type { Page } from "./competitors";

export type RuleType = "price_drop" | "cheaper_than_us" | "unavailable" | "stale" | "scraper_failing";
export type AlertStatus = "open" | "acknowledged" | "resolved";

export interface AlertItem {
  id: number;
  rule_id: number;
  alert_type: RuleType;
  status: AlertStatus;
  message: string;
  details: Record<string, unknown>;
  created_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  offer_id: number;
  product_url: string;
  offer_title: string | null;
  competitor_id: number;
  competitor_name: string;
  product_id: number | null;
  product_name: string | null;
}

export interface AlertRule {
  id: number;
  rule_type: RuleType;
  params: Record<string, number>;
  competitor_id: number | null;
  competitor_name: string | null;
  product_id: number | null;
  product_name: string | null;
  enabled: boolean;
  open_alerts: number;
}

export interface RuleInput {
  rule_type: RuleType;
  params: Record<string, number>;
  competitor_id: number | null;
  product_id: number | null;
  enabled: boolean;
}

export const RULE_LABELS: Record<RuleType, { label: string; param?: { key: string; label: string; suffix: string } }> = {
  price_drop: { label: "Cena konkurenta klesla", param: { key: "percent", label: "o viac ako", suffix: "%" } },
  cheaper_than_us: { label: "Konkurent je lacnejší ako my", param: { key: "percent", label: "o viac ako", suffix: "%" } },
  unavailable: { label: "Produkt prestal byť dostupný" },
  stale: { label: "Cenu sa nepodarilo aktualizovať v intervale" },
  scraper_failing: { label: "Kontrola opakovane zlyháva", param: { key: "count", label: "aspoň", suffix: "× za sebou" } },
};

function useInvalidate() {
  const queryClient = useQueryClient();
  return () => ["alerts", "alert-rules", "dashboard"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
}

export function useAlerts(params: { status?: string; alert_type?: string; page: number; page_size?: number }) {
  return useQuery({ queryKey: ["alerts", "list", params],
                    queryFn: () => api<Page<AlertItem>>(`/alerts${query({ ...params })}`),
                    placeholderData: keepPreviousData, refetchInterval: 60_000 });
}

export function useAlertCount() {
  return useQuery({ queryKey: ["alerts", "count"], queryFn: () => api<{ open: number }>("/alerts/count"),
                    refetchInterval: 60_000 });
}

export function useChangeAlert() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, action }: { id: number; action: "acknowledge" | "resolve" | "reopen" }) =>
      api(`/alerts/${id}/${action}`, { method: "POST" }),
    onSuccess: invalidate,
  });
}

export function useAlertRules() {
  return useQuery({ queryKey: ["alert-rules"], queryFn: () => api<AlertRule[]>("/alert-rules") });
}

export function useSaveRule() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, ...data }: RuleInput & { id?: number }) => id
      ? api<AlertRule>(`/alert-rules/${id}`, { method: "PUT", body: data })
      : api<AlertRule>("/alert-rules", { method: "POST", body: data }),
    onSuccess: invalidate,
  });
}

export function useDeleteRule() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (id: number) => api<void>(`/alert-rules/${id}`, { method: "DELETE" }),
                       onSuccess: invalidate });
}
