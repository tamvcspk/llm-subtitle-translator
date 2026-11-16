import { markSessionExpired } from "./session";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  /** Sent as JSON. */
  body?: unknown;
  signal?: AbortSignal;
}

async function errorMessage(response: Response): Promise<string> {
  const fallback = `Request failed (${response.status})`;
  try {
    const data: unknown = await response.json();
    if (data && typeof data === "object" && "error" in data && typeof data.error === "string") return data.error;
  } catch {
    // Not JSON: the status line is all there is to say.
  }
  return fallback;
}

/** Call the worker's own API. A `401` raises the session overlay (UX §7) and still rejects, so no caller carries on. */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
    signal,
  });
  if (response.status === 401) {
    markSessionExpired();
    throw new ApiError(401, "Your session expired.");
  }
  if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
