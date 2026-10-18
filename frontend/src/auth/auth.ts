import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, setCsrfToken } from "../api/client";
import type { SessionInfo } from "../api/types";

export const SESSION_KEY = ["session"] as const;

/** Current session, or null when not logged in. */
export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: async () => {
      try {
        const session = await api<SessionInfo>("/auth/me");
        setCsrfToken(session.csrf_token);
        return session;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          setCsrfToken(null);
          return null;
        }
        throw error;
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: { email: string; password: string }) =>
      api<SessionInfo>("/auth/login", { method: "POST", body: data }),
    onSuccess: (session) => {
      setCsrfToken(session.csrf_token);
      queryClient.setQueryData(SESSION_KEY, session);
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("/auth/logout", { method: "POST" }),
    onSettled: () => {
      setCsrfToken(null);
      queryClient.setQueryData(SESSION_KEY, null);
      // Drop cached data of the previous user, but keep the (now empty) session query observed.
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== SESSION_KEY[0] });
    },
  });
}
