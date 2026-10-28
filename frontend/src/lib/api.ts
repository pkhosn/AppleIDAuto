export type ApiMeta = {
  page?: number
  per_page?: number
  total?: number
  last_page?: number
  total_pages?: number
}

export type ApiEnvelope<T> = {
  success: boolean
  data: T
  message: string
  errors: Record<string, string[] | string> | string[]
  meta: ApiMeta
}

export class ApiError extends Error {
  status: number
  errors: ApiEnvelope<never>["errors"]

  constructor(message: string, status: number, errors: ApiEnvelope<never>["errors"] = []) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.errors = errors
  }
}

let csrfToken = ""

export function setCsrfToken(token?: string) {
  csrfToken = token ?? ""
}

export function getCsrfToken() {
  return csrfToken
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<ApiEnvelope<T>> {
  const headers = new Headers(init.headers)
  headers.set("Accept", "application/json")
  headers.set("X-Requested-With", "XMLHttpRequest")
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json")
  if (csrfToken && !["GET", "HEAD"].includes((init.method ?? "GET").toUpperCase())) {
    headers.set("X-CSRF-Token", csrfToken)
  }

  const response = await fetch(`/frontend-api/v1/${path.replace(/^\//, "")}`, {
    ...init,
    headers,
    credentials: "include",
  })
  const raw = await response.json().catch(() => null)
  if (!response.ok || !raw?.success) {
    throw new ApiError(raw?.message ?? raw?.msg ?? "请求失败，请稍后重试", response.status, raw?.errors ?? [])
  }
  return raw as ApiEnvelope<T>
}

export function post<T>(path: string, body?: unknown) {
  return api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) })
}

export function withScope(path: string, admin: boolean) {
  const separator = path.includes("?") ? "&" : "?"
  return admin ? `${path}${separator}scope=admin` : path
}
