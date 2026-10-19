import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, query } from "./client";
import type { Page } from "./competitors";

export type MatchStatus = "matched" | "pending_review" | "unmatched" | "ignored";
export type OfferStatus = "active" | "paused" | "archived";

export interface CurrentPrice {
  price: string | null;
  original_price: string | null;
  currency: string | null;
  availability: "in_stock" | "out_of_stock" | "preorder" | "unknown";
  vat_mode: string;
  title: string | null;
  last_checked_at: string | null;
  last_success_at: string | null;
  is_stale: boolean;
  last_error: string | null;
}

export interface Offer {
  id: number;
  competitor_id: number;
  competitor_name: string;
  product_url: string;
  variant: string;
  external_product_id: string | null;
  product: { id: number; name: string } | null;
  matching_method: "identifier" | "name" | "manual" | "ai" | "attributes" | null;
  match_status: MatchStatus;
  match_score: string | null;
  price_source: "page" | "listing";
  status: OfferStatus;
  custom_frequency_hours: number | null;
  next_check_at: string | null;
  current: CurrentPrice | null;
  created_at: string;
  updated_at: string;
}

export interface OffersCreateResult {
  created: Offer[];
  duplicates: string[];
  invalid: { url: string; reason: string }[];
}

export interface Candidate {
  product: { id: number; name: string };
  isbn: string | null;
  ean: string | null;
  score: number;
  method: "identifier" | "name";
}

export interface OfferListParams {
  q?: string;
  match_status?: string;
  status?: string;
  product_id?: number;
  competitor_id?: number;
  page?: number;
  page_size?: number;
}

const KEY = "offers";

/** Offers of one competitor, or across competitors when `competitorId` is omitted. */
export function useOffers(params: OfferListParams, competitorId?: number) {
  const path = competitorId ? `/competitors/${competitorId}/offers` : "/offers";
  return useQuery({
    queryKey: [KEY, "list", competitorId ?? null, params],
    queryFn: () => api<Page<Offer>>(`${path}${query({ ...params })}`),
    placeholderData: keepPreviousData,
  });
}

export function useCandidates(offerId: number | null, search: string) {
  return useQuery({
    queryKey: [KEY, "candidates", offerId, search],
    queryFn: () => api<Candidate[]>(`/offers/${offerId}/candidates${query({ q: search || null })}`),
    enabled: offerId !== null,
  });
}

function useInvalidate() {
  const queryClient = useQueryClient();
  // Offer changes also change counts shown for competitors and products.
  return () => Promise.all(["offers", "competitors", "products"].map((k) =>
    queryClient.invalidateQueries({ queryKey: [k] })));
}

export function useAddOffers(competitorId: number) {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (data: { urls: string[]; variant: string }) =>
      api<OffersCreateResult>(`/competitors/${competitorId}/offers`, { method: "POST", body: data }),
    onSuccess: invalidate,
  });
}

export function useUpdateOffer() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, ...data }: { id: number; status?: OfferStatus; variant?: string;
                                    custom_frequency_hours?: number | null }) =>
      api<Offer>(`/offers/${id}`, { method: "PATCH", body: data }),
    onSuccess: invalidate,
  });
}

export function useAssignOffer() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, action, product_id }: { id: number; action: "assign" | "ignore" | "auto"; product_id?: number }) =>
      api<Offer>(`/offers/${id}/assignment`, { method: "POST", body: { action, product_id } }),
    onSuccess: invalidate,
  });
}

export function useRemoveOffer() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => api<{ result: "deleted" | "archived" }>(`/offers/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}
