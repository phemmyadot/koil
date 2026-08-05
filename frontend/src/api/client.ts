// Thin fetch wrapper shared by every api/*.ts file. No auth today (this app has none), so this
// stays intentionally minimal: JSON in/out, consistent error shape.

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? body;
    } catch {
      // no JSON body -- fall back to statusText already set above
    }
    throw new ApiError(res.status, detail);
  }
  // 204 / empty-body responses (rare here, but don't blow up if one shows up)
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path);
  return handle<T>(res);
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return handle<T>(res);
}

// For endpoints that take a file upload (multipart/form-data) rather than JSON -- e.g. the
// daily review chatbot's philosophy-document upload. No Content-Type header set explicitly:
// the browser sets it (including the multipart boundary) automatically for a FormData body.
export async function apiPostFormData<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(path, { method: "POST", body: formData });
  return handle<T>(res);
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<T>(res);
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: "DELETE" });
  return handle<T>(res);
}

// For endpoints that return a binary blob (PDF/CSV export) with a Content-Disposition filename,
// rather than JSON.
export async function apiPostBlob(
  path: string,
  body: unknown,
): Promise<{ blob: Blob; filename: string | null }> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json())?.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  const disposition = res.headers.get("Content-Disposition");
  const match = disposition?.match(/filename="?([^"]+)"?/);
  return { blob: await res.blob(), filename: match?.[1] ?? null };
}
