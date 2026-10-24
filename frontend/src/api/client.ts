/** Single fetch wrapper for the FastAPI backend. Every failure surfaces as a typed ApiError — nothing is swallowed. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId?: string
  readonly details?: unknown

  constructor(status: number, code: string, message: string, requestId?: string, details?: unknown) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
    this.requestId = requestId
    this.details = details
  }
}

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? ""
export const API = `${BASE}/api/v1`

type Query = Record<string, string | number | boolean | null | undefined>

function qs(params?: Query): string {
  if (!params) return ""
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") sp.set(k, String(v))
  const s = sp.toString()
  return s ? `?${s}` : ""
}

async function request<T>(method: string, url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, {
      method,
      signal,
      headers: body instanceof FormData ? undefined : body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body instanceof FormData ? body : body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e
    throw new ApiError(0, "network_error", "Cannot reach the MORPHE backend. Is it running on port 8000?")
  }
  const text = await res.text()
  let data: unknown = undefined
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = undefined
    }
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; request_id?: string; details?: unknown } } | undefined)?.error
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? `Request failed (${res.status})`, err?.request_id, err?.details)
  }
  return data as T
}

export const api = {
  get: <T>(path: string, params?: Query, signal?: AbortSignal) => request<T>("GET", `${API}${path}${qs(params)}`, undefined, signal),
  post: <T>(path: string, body?: unknown, params?: Query) => request<T>("POST", `${API}${path}${qs(params)}`, body ?? {}),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", `${API}${path}`, body ?? {}),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", `${API}${path}`, body ?? {}),
  del: <T>(path: string, params?: Query) => request<T>("DELETE", `${API}${path}${qs(params)}`),
  upload: <T>(path: string, form: FormData) => request<T>("POST", `${API}${path}`, form),
  health: () => request<Health>("GET", `${BASE}/health`),
}

export interface Health {
  status: string
  ready: boolean
  checks?: Record<string, { ok: boolean; [k: string]: unknown }>
  architecture_version?: string | null
  environment?: string
  needs_recovery?: unknown
  error?: { code: string; message: string } | null
}
