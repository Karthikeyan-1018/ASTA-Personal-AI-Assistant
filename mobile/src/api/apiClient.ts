import * as SecureStore from 'expo-secure-store';

export const TOKEN_STORAGE_KEY = 'asta.mobile.access-token';
export const URL_STORAGE_KEY = 'asta.mobile.server-url';
export const USER_STORAGE_KEY = 'asta.mobile.username';

export interface ServerTask {
  id: number;
  title: string;
  description: string | null;
  category: string;
  priority: string;
  status: string;
  is_completed?: boolean;
  due_date: string | null;
  due_time?: string | null;
  timezone?: string | null;
  created_at: string;
  updated_at?: string;
}

export interface TaskPayload {
  title: string;
  description: string | null;
  category: string;
  priority: string;
  status: string;
  due_date: string | null;
  due_time?: string | null;
  timezone?: string | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: 'bearer';
  expires_at: string;
  owner: { id: number; username: string; created_at: string };
}

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = 'ApiError';
  }
}

export function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, '');
}

export function isValidBaseUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      (url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname))) &&
      !url.pathname.replace(/\/$/, '')
    );
  } catch {
    return false;
  }
}

async function request<T>(
  baseUrl: string,
  path: string,
  token: string | null,
  init: RequestInit = {}
): Promise<T> {
  const cleanBase = normalizeBaseUrl(baseUrl);
  if (!isValidBaseUrl(cleanBase)) {
    throw new ApiError('Use HTTPS for ASTA over Wi-Fi. Plain HTTP is allowed only for localhost development.');
  }
  const url = `${cleanBase}${path}`;

  let response: Response;
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 7000); // 7s timeout

    response = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
    clearTimeout(timeoutId);
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      throw new ApiError('Connection timed out. ASTA server did not respond in 7s.');
    }
    throw new ApiError('Cannot reach ASTA at ' + cleanBase + '. Check the laptop LAN HTTPS address, server bind, and certificate trust. Tasks remain saved on this phone.');
  }

  if (!response.ok) {
    let detail = `Server returned ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') {
        detail = body.detail;
      } else if (Array.isArray(body.detail)) {
        const issues = body.detail.map((issue: any) => {
          const path = Array.isArray(issue?.loc)
            ? issue.loc.filter((part: unknown) => part !== 'body').join('.')
            : '';
          const message = typeof issue?.msg === 'string' ? issue.msg : 'Invalid value';
          return path ? `${path}: ${message}` : message;
        });
        if (issues.length) detail = issues.join('; ');
      }
    } catch {
      // ignore
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export const apiClient = {
  checkReachability: async (baseUrl: string): Promise<boolean> => {
    if (!isValidBaseUrl(baseUrl)) return false;
    const url = normalizeBaseUrl(baseUrl) + '/health';
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3500);
    try {
      const res = await fetch(url, { signal: controller.signal });
      return res.ok;
    } catch {
      return false;
    } finally {
      clearTimeout(timeoutId);
    }
  },

  login: (baseUrl: string, username: string, password: string): Promise<LoginResponse> =>
    request<LoginResponse>(baseUrl, '/api/auth/mobile/login', null, {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),

  logout: (baseUrl: string, token: string): Promise<void> =>
    request<void>(baseUrl, '/api/auth/mobile/logout', token, { method: 'POST' }),

  listTasks: (baseUrl: string, token: string): Promise<{ tasks: ServerTask[]; total?: number }> =>
    request<{ tasks: ServerTask[]; total?: number }>(
      baseUrl,
      '/api/tasks/?limit=100&sort_by=created_at&sort_order=desc',
      token
    ),

  createTask: (baseUrl: string, token: string, task: TaskPayload): Promise<ServerTask> =>
    request<ServerTask>(baseUrl, '/api/tasks/', token, {
      method: 'POST',
      body: JSON.stringify(task),
    }),

  updateTask: (baseUrl: string, token: string, serverId: number, task: Partial<TaskPayload>): Promise<ServerTask> =>
    request<ServerTask>(baseUrl, `/api/tasks/${serverId}`, token, {
      method: 'PATCH',
      body: JSON.stringify(task),
    }),

  deleteTask: (baseUrl: string, token: string, serverId: number): Promise<void> =>
    request<void>(baseUrl, `/api/tasks/${serverId}`, token, {
      method: 'DELETE',
    }),
};
