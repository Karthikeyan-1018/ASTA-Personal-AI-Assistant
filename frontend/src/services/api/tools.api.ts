import apiClient from './client';
import type {
  EmergencyStopResponse,
  ToolConfirmationActionResponse,
  ToolConfirmationListResponse,
  ToolExecuteRequest,
  ToolExecutionListResponse,
  ToolListResponse,
  ToolResultResponse,
} from '../../types/api.types';

export const toolsApi = {
  async list(): Promise<ToolListResponse> {
    const response = await apiClient.get<ToolListResponse>('/api/tools/');
    return response.data;
  },

  async execute(
    toolName: string,
    args: Record<string, any>,
    idempotencyKey?: string
  ): Promise<ToolResultResponse> {
    const payload: ToolExecuteRequest = {
      tool: toolName,
      arguments: args,
    };
    const response = await apiClient.post<ToolResultResponse>(
      '/api/tools/execute',
      payload,
      idempotencyKey
        ? {
            headers: {
              'Idempotency-Key': idempotencyKey,
            },
          }
        : undefined
    );
    return response.data;
  },

  async listExecutions(params?: {
    limit?: number;
    offset?: number;
    tool?: string;
    status?: string;
  }): Promise<ToolExecutionListResponse> {
    const response = await apiClient.get<ToolExecutionListResponse>(
      '/api/tools/executions',
      { params }
    );
    return response.data;
  },

  async listConfirmations(
    conversationId?: number
  ): Promise<ToolConfirmationListResponse> {
    const response = await apiClient.get<ToolConfirmationListResponse>(
      '/api/tools/confirmations',
      {
        params: conversationId ? { conversation_id: conversationId } : undefined,
      }
    );
    return response.data;
  },

  async confirm(confirmationId: number): Promise<ToolConfirmationActionResponse> {
    const response = await apiClient.post<ToolConfirmationActionResponse>(
      `/api/tools/confirmations/${confirmationId}/confirm`
    );
    return response.data;
  },

  async reject(confirmationId: number): Promise<ToolConfirmationActionResponse> {
    const response = await apiClient.post<ToolConfirmationActionResponse>(
      `/api/tools/confirmations/${confirmationId}/reject`
    );
    return response.data;
  },

  async getEmergencyStop(): Promise<EmergencyStopResponse> {
    const response = await apiClient.get<EmergencyStopResponse>(
      '/api/tools/emergency-stop'
    );
    return response.data;
  },

  async engageEmergencyStop(): Promise<EmergencyStopResponse> {
    const response = await apiClient.post<EmergencyStopResponse>(
      '/api/tools/emergency-stop'
    );
    return response.data;
  },
};

export default toolsApi;
