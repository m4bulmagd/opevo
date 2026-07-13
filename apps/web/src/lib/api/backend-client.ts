import "server-only";

import { selectFirstNonblank } from "@/lib/auth/clerk-config";
import { getServerSessionState } from "@/lib/auth/server-session";

const API_BASE_URL =
  selectFirstNonblank(process.env.API_BASE_URL, process.env.NEXT_PUBLIC_API_BASE_URL) ?? "http://localhost:8000";

export class BackendApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "BackendApiError";
    this.status = status;
  }
}

type BackendFetchOptions = RequestInit & {
  allow404?: boolean;
};

export async function backendFetch<T>(path: string, options: BackendFetchOptions = {}) {
  const { allow404 = false, headers, ...init } = options;
  const session = await getServerSessionState();

  if (!session.isAuthenticated) {
    throw new BackendApiError("Missing authenticated session", 401);
  }

  const token = await session.getToken();
  if (!token) {
    throw new BackendApiError("Missing session token", 401);
  }

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
    let detail = response.statusText;

    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ?? detail;
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
