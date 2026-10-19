import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, query } from "./client";
import type { Page } from "./competitors";

export interface HistoryPoint {
  t: string;
  price: string | null;
  availability: string;
  change_types: string[];
  synthetic: boolean;
}

export interface HistorySeries {
  offer_id: number;
  competitor_id: number;
  competitor_name: string;
  slot: number;
  currency: string | null;
  plottable: boolean;
  points: HistoryPoint[];
}

export interface HistoryChart {
  product_id: number;
  product_name: string;
  market: string | null;
  markets: string[];
  axis_currency: string | null;
  own_price: string | null;
  own_currency: string | null;
  period_from: string;
  period_to: string;
  series: HistorySeries[];
}

export interface HistoryChange {
  id: number;
  observed_at: string;
  offer_id: number;
  product_url: string;
  offer_title: string | null;
  competitor_id: number;
  competitor_name: string;
  product_id: number | null;
  product_name: string | null;
  price: string | null;
  previous_price: string | null;
  currency: string | null;
  availability: string;
  change_types: string[];
}

export function useHistoryChart(params: { product_id: number | null; market?: string; from?: string }) {
  return useQuery({
    queryKey: ["history", "chart", params],
    queryFn: () => api<HistoryChart>(`/history/chart${query({ ...params })}`),
    enabled: params.product_id !== null,
    placeholderData: keepPreviousData,
  });
}

export function useHistoryChanges(params: { product_id?: number; competitor_id?: number; from?: string;
                                            change_type?: string; page: number }) {
  return useQuery({
    queryKey: ["history", "changes", params],
    queryFn: () => api<Page<HistoryChange>>(`/history/changes${query({ ...params, page_size: 25 })}`),
    placeholderData: keepPreviousData,
  });
}
