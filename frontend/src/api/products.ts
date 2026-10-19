import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, query } from "./client";
import type { Page, VatMode } from "./competitors";

export type Priority = "strategic" | "regular" | "stable";
export type TrackingStatus = "active" | "paused";

export interface ProductPrice {
  market: string;
  price: string;
  currency: string;
  vat_mode: VatMode;
}

export interface ProductAttributes {
  subject?: string | null;
  grade?: string | null;
  type?: string | null;
}

export interface Product {
  id: number;
  internal_id: string | null;
  name: string;
  isbn: string | null;
  ean: string | null;
  sku: string | null;
  brand: string | null;
  category: string | null;
  attributes: ProductAttributes;
  priority: Priority;
  tracking_status: TrackingStatus;
  prices: ProductPrice[];
  offer_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProductInput {
  name: string;
  internal_id: string | null;
  isbn: string | null;
  ean: string | null;
  sku: string | null;
  brand: string | null;
  category: string | null;
  attributes: ProductAttributes;
  priority: Priority;
  tracking_status: TrackingStatus;
  prices: { market: string; price: string; currency: string | null; vat_mode: VatMode }[];
}

export interface ProductListParams {
  q?: string;
  priority?: string;
  tracking_status?: string;
  market?: string;
  sort?: string;
  page?: number;
  page_size?: number;
}

const KEY = "products";

export function useProducts(params: ProductListParams, enabled = true) {
  return useQuery({
    queryKey: [KEY, "list", params],
    queryFn: () => api<Page<Product>>(`/products${query({ ...params })}`),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useProduct(id: number) {
  return useQuery({ queryKey: [KEY, "detail", id], queryFn: () => api<Product>(`/products/${id}`) });
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return () => Promise.all([queryClient.invalidateQueries({ queryKey: [KEY] }),
                            queryClient.invalidateQueries({ queryKey: ["offers"] })]);
}

export function useSaveProduct(id?: number) {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (data: ProductInput) => id
      ? api<Product>(`/products/${id}`, { method: "PATCH", body: data })
      : api<Product>("/products", { method: "POST", body: data }),
    onSuccess: invalidate,
  });
}

export function useDeleteProduct() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => api<void>(`/products/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}
