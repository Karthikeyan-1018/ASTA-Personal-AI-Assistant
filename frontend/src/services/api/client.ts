import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios';

export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/+$/, '') || 'http://localhost:8000';

let inMemoryCsrfToken: string | null = null;

export function getCsrfTokenFromCookie(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(/(?:^|;\s*)asta_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export function getActiveCsrfToken(): string | null {
  return inMemoryCsrfToken || getCsrfTokenFromCookie();
}

export function setActiveCsrfToken(token: string | null): void {
  inMemoryCsrfToken = token;
}

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true, // Crucial for sending asta_session HttpOnly cookie
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 30000,
});

// Request interceptor: Inject X-CSRF-Token for state-changing requests
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const method = config.method?.toUpperCase();
    if (method && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
      const csrfToken = getActiveCsrfToken();
      if (csrfToken && !config.headers['X-CSRF-Token']) {
        config.headers['X-CSRF-Token'] = csrfToken;
      }
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor: Extract structured errors
apiClient.interceptors.response.use(
  (response) => {
    // If backend sets or returns CSRF token in header or body
    const headerCsrf = response.headers['x-csrf-token'];
    if (headerCsrf) {
      setActiveCsrfToken(headerCsrf);
    }
    return response;
  },
  (error: AxiosError<{ detail?: string | Record<string, any> }>) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail;

    // A 401 from a normal API call means the session expired or was revoked.
    // Login/setup 401s are ordinary credential failures and must not trigger
    // the global sign-out path.
    if (status === 401 && typeof window !== 'undefined') {
      const url = error.config?.url || '';
      const isAuthEndpoint =
        url.includes('/api/auth/login') || url.includes('/api/auth/setup');

      if (!isAuthEndpoint) {
        window.dispatchEvent(new CustomEvent('asta:unauthorized'));
      }
    }

    let message = 'An unexpected network error occurred';
    if (typeof detail === 'string') {
      message = detail;
    } else if (Array.isArray(detail)) {
      message = detail.map((d: any) => d.msg || JSON.stringify(d)).join('; ');
    } else if (error.message) {
      message = error.message;
    }

    // Attach human-readable message to error object
    const enhancedError = new Error(message) as Error & {
      status?: number;
      originalError: AxiosError;
    };
    enhancedError.status = status;
    enhancedError.originalError = error;

    return Promise.reject(enhancedError);
  }
);

export default apiClient;
