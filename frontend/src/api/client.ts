/** Thin fetch wrapper: JSON in/out, CSRF header on state-changing calls, readable errors. */

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export class ApiError extends Error {
  /** `detail` is the server's structured error detail, when it sent an object (e.g. a duplicate domain). */
  constructor(public status: number, message: string, public detail?: Record<string, unknown>) {
    super(message);
  }
}

type ValidationItem = { msg?: string; loc?: (string | number)[] };

function messageFrom(status: number, data: unknown): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) return String(detail.message);
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as ValidationItem;
    const field = first.loc?.slice(1).join(".");
    return field ? `${field}: ${first.msg}` : (first.msg ?? "Neplatné údaje.");
  }
  return status >= 500 ? "Chyba servera. Skúste to znova neskôr." : `Požiadavka zlyhala (${status}).`;
}

export async function api<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "Server je nedostupný. Skontrolujte pripojenie.");
  }
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new ApiError(response.status, messageFrom(response.status, data),
      detail && typeof detail === "object" && !Array.isArray(detail) ? detail as Record<string, unknown> : undefined);
  }
  return data as T;
}

/** Builds a query string, skipping empty values. */
export function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
