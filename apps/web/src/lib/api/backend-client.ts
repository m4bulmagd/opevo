import "server-only";

import { selectFirstNonblank } from "@/lib/auth/clerk-config";
import { requireServerSession } from "@/lib/auth/server-session";

const API_BASE_URL =
  selectFirstNonblank(process.env.API_BASE_URL, process.env.NEXT_PUBLIC_API_BASE_URL) ?? "http://localhost:8000";

export class BackendApiError extends Error {
  status: number;
  detail: string | { code?: string; [key: string]: unknown };

  constructor(detail: string | { code?: string; [key: string]: unknown }, status: number) {
    const message =
      typeof detail === "string"
        ? detail
        : typeof detail.code === "string" && detail.code.trim()
          ? detail.code
          : `Backend request failed (${status})`;
    super(message);
    this.name = "BackendApiError";
    this.status = status;
    this.detail = detail;
  }
}

type BackendFetchOptions = RequestInit & {
  allow404?: boolean;
};

export async function backendFetch<T>(path: string, options: BackendFetchOptions = {}) {
  const { allow404 = false, headers, ...init } = options;
  const { token } = await requireServerSession();

  const requestHeaders = new Headers(headers);
  requestHeaders.set("Authorization", `Bearer ${token}`);

  if (init.body && !requestHeaders.has("Content-Type")) {
    requestHeaders.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: requestHeaders,
    cache: "no-store",
  });

  if (allow404 && response.status === 404) {
    return null as T;
  }

  if (!response.ok) {
    let detail: string | { code?: string; [key: string]: unknown } =
      response.statusText || `Backend request failed (${response.status})`;

    try {
      const payload = (await response.json()) as { detail?: unknown };
      const candidate = payload.detail;

      if (typeof candidate === "string") {
        detail = candidate;
      } else if (
        candidate !== null &&
        typeof candidate === "object" &&
        !Array.isArray(candidate) &&
        (!("code" in candidate) || candidate.code === undefined || typeof candidate.code === "string")
      ) {
        detail = candidate as { code?: string; [key: string]: unknown };
      }
    } catch {
      // Preserve the default detail message when the backend error body is absent or invalid JSON.
    }

    throw new BackendApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
