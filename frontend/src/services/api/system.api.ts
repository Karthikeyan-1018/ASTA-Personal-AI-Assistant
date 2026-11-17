import apiClient from './client';
import toolsApi from './tools.api';
import type { ToolMetricsResponse, ToolResultResponse } from '../../types/api.types';

/**
 * Frontend-only access to the backend capabilities that genuinely exist.
 *
 * There is no dedicated host-telemetry endpoint. Host information is exposed
 * through the existing read-only system tools and is returned as a structured
 * tool result. A disabled or failed tool is reported as such; it is never
 * replaced with synthetic values.
 */

export interface ProviderHealthResponse {
  groq: string;
  ollama: string;
  overall_status: string;
}

export const systemApi = {
  /** Real provider reachability reported by the backend. */
  async getProviderHealth(): Promise<ProviderHealthResponse> {
    const response = await apiClient.get<ProviderHealthResponse>(
      '/api/health/providers'
    );
    return response.data;
  },

  /** Real process-local tool counters reported by the backend. */
  async getToolMetrics(): Promise<ToolMetricsResponse> {
    const response = await apiClient.get<ToolMetricsResponse>(
      '/api/tools/metrics'
    );
    return response.data;
  },

  /** Real host information from the existing read-only system tool. */
  async getSystemSummary(): Promise<ToolResultResponse> {
    return toolsApi.execute('system_summary', {});
  },

  /** Real process summary from the existing read-only inspection tool. */
  async getProcessSummary(): Promise<ToolResultResponse> {
    return toolsApi.execute('system_resource_summary', { limit: 5 });
  },
};

export default systemApi;
