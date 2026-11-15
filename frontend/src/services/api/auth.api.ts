import apiClient, { setActiveCsrfToken } from './client';
import type {
  LoginRequest,
  LoginResponse,
  MeResponse,
  OwnerResponse,
  OwnerSetupRequest,
} from '../../types/api.types';

export const authApi = {
  async login(payload: LoginRequest): Promise<LoginResponse> {
    const response = await apiClient.post<LoginResponse>('/api/auth/login', payload);
    if (response.data.csrf_token) {
      setActiveCsrfToken(response.data.csrf_token);
    }
    return response.data;
  },

  async logout(): Promise<void> {
    try {
      await apiClient.post('/api/auth/logout');
    } finally {
      setActiveCsrfToken(null);
    }
  },

  async getMe(): Promise<MeResponse> {
    const response = await apiClient.get<MeResponse>('/api/auth/me');
    return response.data;
  },

  async setupOwner(payload: OwnerSetupRequest, setupToken: string): Promise<OwnerResponse> {
    const response = await apiClient.post<OwnerResponse>('/api/auth/setup', payload, {
      headers: {
        'X-Setup-Token': setupToken,
      },
    });
    return response.data;
  },
};

export default authApi;
