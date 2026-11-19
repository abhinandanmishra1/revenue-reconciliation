export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

type Options = { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal };

/** Same-origin call to the API through the Next.js /api rewrite; the session cookie rides along. */
export async function api<T>(path: string, { method = "GET", body, form, signal }: Options = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      signal,
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "network_error", "Cannot reach the server. Check your connection and try again.");
  }

  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (res.ok) return data as T;

  const error = data?.error ?? {};
  if (res.status === 401 && !path.startsWith("/auth/")) {
    // Session expired or invalid: clear the cookie (httpOnly, so only the API
    // can), then send the user to log in and back here afterwards.
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => undefined);
    window.location.assign(`/login?expired=1&next=${next}`);
  }
  throw new ApiError(res.status, error.code ?? "error", error.message ?? "Something went wrong. Please try again.", error.details);
}

export function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
}

/** Only same-site relative paths, so `next` cannot become an open redirect. */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/imports";
}
