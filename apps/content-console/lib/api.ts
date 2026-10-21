export const TOKEN_KEY = "ink_token";

export function getToken() {
  return typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export function authHeaders(extra: Record<string, string> = {}) {
  const token = getToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

export async function api<T = Record<string, unknown>>(path: string, init: RequestInit = {}): Promise<T> {
  const url = path.startsWith("/") ? `/backend${path}` : path;
  const response = await fetch(url, {
    ...init,
    headers: authHeaders(init.headers as Record<string, string> | undefined),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data.detail || data.error || data.message || `请求失败 (${response.status})`;
    throw new Error(typeof message === "string" ? message : "请求失败");
  }
  return data as T;
}
