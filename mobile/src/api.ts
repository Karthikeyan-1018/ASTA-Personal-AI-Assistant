export type TaskPayload = {
  title: string;
  description: string | null;
  category: 'other';
  priority: 'low' | 'medium' | 'high' | 'urgent';
  status: 'pending';
  due_date: string | null;
};

export type ServerTask = {
  id: number;
  title: string;
  description: string | null;
  category: string;
  priority: string;
  status: string;
  due_date: string | null;
  created_at: string;
};

export type LoginResponse = {
  access_token: string;
  token_type: 'bearer';
  expires_at: string;
  owner: { id: number; username: string; created_at: string };
};

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
  }
}

async function request<T>(
  baseUrl: string,
  path: string,
  token: string | null,
  init: RequestInit = {}
): Promise<T> {
  const url = `${baseUrl.replace(/\/+$/, '')}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
  } catch {
    throw new ApiError('ASTA is unreachable. The task is saved on this phone.');
  }

  if (!response.ok) {
    let detail = `ASTA returned ${response.status}.`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') detail = body.detail;
    } catch {
      // Keep the status message if the server did not return JSON.
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  login: (baseUrl: string, username: string, password: string) =>
    request<LoginResponse>(baseUrl, '/api/auth/mobile/login', null, {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: (baseUrl: string, token: string) =>
    request<void>(baseUrl, '/api/auth/mobile/logout', token, { method: 'POST' }),
  listTasks: (baseUrl: string, token: string) =>
    request<{ tasks: ServerTask[] }>(
      baseUrl,
      '/api/tasks/?limit=100&sort_by=created_at&sort_order=desc',
      token
    ),
  createTask: (baseUrl: string, token: string, task: TaskPayload) =>
    request<ServerTask>(baseUrl, '/api/tasks/', token, {
      method: 'POST',
      body: JSON.stringify(task),
    }),
};
