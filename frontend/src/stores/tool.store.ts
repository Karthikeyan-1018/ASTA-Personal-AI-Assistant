import { create } from 'zustand';
import { toolsApi } from '../services/api/tools.api';
import type {
  ToolConfirmation,
  ToolDefinition,
  ToolExecutionLog,
  ToolParameterDef,
  ToolParameterType,
} from '../types/tool.types';
import type {
  EmergencyStopResponse,
  ToolConfirmationResponse,
  ToolDefinitionResponse,
  ToolExecutionResponse,
  ToolMetricsResponse,
  ToolResultResponse,
} from '../types/api.types';

export type ToolSectionStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface ToolExecutionResult {
  ok: boolean;
  record: ToolExecutionLog | null;
  error: string | null;
}

interface ToolStoreState {
  tools: ToolDefinition[];
  toolsStatus: ToolSectionStatus;
  toolsError: string | null;
  toolsLoaded: boolean;

  executionLogs: ToolExecutionLog[];
  historyStatus: ToolSectionStatus;
  historyError: string | null;
  historyLoaded: boolean;
  historyTotal: number;
  historyViewCleared: boolean;

  pendingConfirmations: ToolConfirmation[];
  confirmationsStatus: ToolSectionStatus;
  confirmationsError: string | null;
  confirmationsLoaded: boolean;
  confirmationPendingIds: number[];

  emergencyStop: EmergencyStopResponse | null;
  emergencyStopStatus: ToolSectionStatus;
  emergencyStopError: string | null;
  emergencyStopPending: boolean;

  metrics: ToolMetricsResponse | null;
  metricsStatus: ToolSectionStatus;
  metricsError: string | null;

  executionPending: boolean;
  executionError: string | null;
  lastActionResult: ToolExecutionLog | null;
  actionNotice: string | null;

  loadToolWorkspace: () => Promise<void>;
  fetchTools: () => Promise<boolean>;
  fetchHistory: (silent?: boolean) => Promise<boolean>;
  fetchExecutionDetail: (
    executionId: number
  ) => Promise<ToolExecutionLog | null>;
  fetchConfirmations: (silent?: boolean) => Promise<boolean>;
  fetchEmergencyStop: (silent?: boolean) => Promise<boolean>;
  fetchMetrics: (silent?: boolean) => Promise<boolean>;

  executeTool: (
    toolId: string,
    params: Record<string, unknown>
  ) => Promise<ToolExecutionResult>;
  approvePendingConfirmation: (confirmationId: number) => Promise<boolean>;
  rejectPendingConfirmation: (confirmationId: number) => Promise<boolean>;
  engageEmergencyStop: () => Promise<boolean>;
  recoverEmergencyStop: (recoveryToken: string) => Promise<boolean>;

  clearHistoryView: () => void;
  clearToolsError: () => void;
  clearHistoryError: () => void;
  clearConfirmationsError: () => void;
  clearEmergencyStopError: () => void;
  clearMetricsError: () => void;
  clearExecutionError: () => void;
  clearActionNotice: () => void;
}

let historyInFlight = false;
let confirmationsInFlight = false;
let emergencyStopInFlight = false;
let metricsInFlight = false;
let localRecordCounter = 0;
let executionCounter = 0;

function nextIdempotencyKey(toolName: string): string {
  executionCounter += 1;
  return `exec-${toolName}-${Date.now()}-${executionCounter}`;
}

function nextLocalId(prefix: string): string {
  localRecordCounter += 1;
  return `${prefix}-${Date.now()}-${localRecordCounter}`;
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) return error.message.trim();
  if (typeof error === 'string' && error.trim()) return error.trim();
  return fallback;
}

function humanizeToolName(name: string): string {
  return name
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function normalizeParamType(value: unknown): ToolParameterType {
  const raw = Array.isArray(value)
    ? value.find((entry) => typeof entry === 'string' && entry !== 'null')
    : value;

  if (raw === 'integer' || raw === 'number') return 'number';
  if (raw === 'boolean') return 'boolean';
  if (raw === 'object') return 'object';
  if (raw === 'array') return 'array';
  return 'string';
}

interface JsonSchemaProperty {
  type?: unknown;
  format?: unknown;
  description?: unknown;
  default?: unknown;
  enum?: unknown;
  anyOf?: unknown;
  oneOf?: unknown;
}

function schemaPropertyDefinition(property: JsonSchemaProperty): JsonSchemaProperty {
  const union = Array.isArray(property.anyOf)
    ? property.anyOf
    : Array.isArray(property.oneOf)
    ? property.oneOf
    : null;

  if (!union) return property;

  const nonNull = union.find(
    (entry) =>
      entry &&
      typeof entry === 'object' &&
      (entry as JsonSchemaProperty).type !== 'null'
  ) as JsonSchemaProperty | undefined;

  return nonNull ?? property;
}

/**
 * Convert the backend JSON parameter schema into display fields. An empty
 * schema yields an empty list: no payload field is invented.
 */
function parseParameters(schema: Record<string, unknown> | null | undefined): ToolParameterDef[] {
  if (!schema || typeof schema !== 'object') return [];

  const properties = schema.properties;
  if (!properties || typeof properties !== 'object') return [];

  const requiredList = Array.isArray(schema.required)
    ? schema.required.filter((value): value is string => typeof value === 'string')
    : [];

  return Object.entries(properties as Record<string, JsonSchemaProperty>).map(
    ([name, rawProperty]) => {
      const property = schemaPropertyDefinition(rawProperty ?? {});
      const enumValues = Array.isArray(property.enum)
        ? property.enum.filter(
            (value): value is string | number | boolean =>
              typeof value === 'string' ||
              typeof value === 'number' ||
              typeof value === 'boolean'
          )
        : undefined;

      const definition: ToolParameterDef = {
        name,
        type:
          property.format === 'path' ? 'path' : normalizeParamType(property.type),
        description:
          typeof property.description === 'string' ? property.description : '',
        required: requiredList.includes(name),
      };

      if (property.default !== undefined) {
        if (
          typeof property.default === 'string' ||
          typeof property.default === 'number' ||
          typeof property.default === 'boolean'
        ) {
          definition.default = property.default;
        }
      }

      if (enumValues && enumValues.length > 0) {
        definition.enumValues = enumValues;
      }

      return definition;
    }
  );
}

function mapBackendTool(tool: ToolDefinitionResponse): ToolDefinition {
  return {
    id: `tool-${tool.name}`,
    name: humanizeToolName(tool.name),
    backendName: tool.name,
    category: tool.category,
    description: tool.description,
    confirmationRequired: tool.requires_confirmation,
    enabled: tool.enabled,
    allowedInPrivate: tool.allowed_in_private,
    parameters: parseParameters(tool.parameters),
    outputSchema:
      tool.output_schema && typeof tool.output_schema === 'object'
        ? tool.output_schema
        : {},
  };
}

function mapBackendExecution(execution: ToolExecutionResponse): ToolExecutionLog {
  return {
    id: `execution-${execution.id}`,
    executionId: execution.id,
    toolName: execution.tool_name,
    category: execution.category,
    executedAt:
      execution.started_at || execution.completed_at || execution.created_at,
    startedAt: execution.started_at ?? null,
    completedAt: execution.completed_at ?? null,
    durationMs:
      typeof execution.duration_ms === 'number' ? execution.duration_ms : null,
    status: execution.status,
    lifecycleStatus: execution.lifecycle_status,
    success: execution.success === true,
    confirmationStatus: execution.confirmation_status,
    argumentKeys: Array.isArray(execution.argument_keys)
      ? execution.argument_keys
      : [],
    // Execution history is metadata-only. Raw output is never retained.
    output: null,
    outputAvailable: false,
    errorCode: execution.error_code ?? null,
    errorCategory: execution.error_category ?? null,
    provider: execution.provider ?? null,
    resultBytes:
      typeof execution.result_bytes === 'number'
        ? execution.result_bytes
        : null,
    truncated: execution.truncated === true,
    retryCount: execution.retry_count ?? 0,
    conversationId: execution.conversation_id ?? null,
  };
}

function mapConfirmation(confirmation: ToolConfirmationResponse): ToolConfirmation {
  return {
    id: confirmation.id,
    toolName: confirmation.tool_name,
    category: confirmation.category,
    status: confirmation.status,
    argumentsPreview: confirmation.arguments_preview ?? '',
    createdAt: confirmation.created_at,
    expiresAt: confirmation.expires_at ?? '',
    resolvedAt: confirmation.resolved_at ?? null,
    conversationId: confirmation.conversation_id ?? null,
    executionId: confirmation.execution_id ?? null,
    isExpired: confirmation.is_expired === true,
  };
}

function presentOutput(data: unknown): { output: string | null; outputAvailable: boolean } {
  if (data === null || data === undefined) {
    return { output: null, outputAvailable: false };
  }

  if (typeof data === 'string') {
    return data.length > 0
      ? { output: data, outputAvailable: true }
      : { output: null, outputAvailable: false };
  }

  try {
    return { output: JSON.stringify(data, null, 2), outputAvailable: true };
  } catch {
    return { output: String(data), outputAvailable: true };
  }
}

function mapResultRecord(
  result: ToolResultResponse,
  options: {
    toolName?: string;
    category?: string | null;
    confirmationId?: number;
    confirmationStatus?: string | null;
    executedAt?: string;
    argumentKeys?: string[];
  } = {}
): ToolExecutionLog {
  const { output, outputAvailable } = presentOutput(result.data);

  return {
    id:
      typeof result.execution_id === 'number'
        ? `execution-${result.execution_id}`
        : nextLocalId('result'),
    executionId: result.execution_id ?? null,
    toolName: options.toolName ?? result.tool ?? 'Tool',
    category: options.category ?? null,
    executedAt: options.executedAt ?? new Date().toISOString(),
    startedAt: null,
    completedAt: null,
    durationMs:
      typeof result.duration_ms === 'number' ? result.duration_ms : null,
    status:
      result.status ||
      (result.success ? 'success' : result.confirmation_required ? 'confirmation_required' : 'error'),
    lifecycleStatus: null,
    success: result.success === true,
    confirmationStatus:
      result.confirmation_status ?? options.confirmationStatus ?? null,
    argumentKeys: options.argumentKeys ?? [],
    output,
    outputAvailable,
    errorMessage: result.error_message ?? null,
    errorCode: result.error_code ?? null,
    errorCategory: result.error_category ?? null,
    provider: result.provider ?? null,
    resultBytes:
      typeof result.result_bytes === 'number' ? result.result_bytes : null,
    truncated: result.truncated === true,
    retryCount: result.retry_count ?? 0,
    confirmationId: result.confirmation_id ?? options.confirmationId,
  };
}

function mapConfirmationActionRecord(
  confirmation: ToolConfirmationResponse,
  result: ToolResultResponse | null | undefined
): ToolExecutionLog {
  if (result) {
    return mapResultRecord(result, {
      toolName: confirmation.tool_name,
      category: confirmation.category,
      confirmationId: confirmation.id,
      confirmationStatus: confirmation.status,
      executedAt: confirmation.resolved_at ?? confirmation.created_at,
    });
  }

  // The server resolved the confirmation but did not return a result payload.
  return {
    id: confirmation.execution_id
      ? `execution-${confirmation.execution_id}`
      : nextLocalId('confirmation'),
    executionId: confirmation.execution_id ?? null,
    toolName: confirmation.tool_name,
    category: confirmation.category,
    executedAt: confirmation.resolved_at ?? confirmation.created_at,
    startedAt: null,
    completedAt: null,
    durationMs: null,
    status: confirmation.status,
    lifecycleStatus: null,
    success: confirmation.status === 'executed',
    confirmationStatus: confirmation.status,
    argumentKeys: [],
    output: null,
    outputAvailable: false,
    confirmationId: confirmation.id,
  };
}

const EMPTY_SECTIONS = {
  tools: [] as ToolDefinition[],
  toolsStatus: 'idle' as ToolSectionStatus,
  toolsError: null as string | null,
  toolsLoaded: false,
  executionLogs: [] as ToolExecutionLog[],
  historyStatus: 'idle' as ToolSectionStatus,
  historyError: null as string | null,
  historyLoaded: false,
  historyTotal: 0,
  historyViewCleared: false,
  pendingConfirmations: [] as ToolConfirmation[],
  confirmationsStatus: 'idle' as ToolSectionStatus,
  confirmationsError: null as string | null,
  confirmationsLoaded: false,
  confirmationPendingIds: [] as number[],
  emergencyStop: null as EmergencyStopResponse | null,
  emergencyStopStatus: 'idle' as ToolSectionStatus,
  emergencyStopError: null as string | null,
  emergencyStopPending: false,
  metrics: null as ToolMetricsResponse | null,
  metricsStatus: 'idle' as ToolSectionStatus,
  metricsError: null as string | null,
  executionPending: false,
  executionError: null as string | null,
  lastActionResult: null as ToolExecutionLog | null,
  actionNotice: null as string | null,
};

export const useToolStore = create<ToolStoreState>((set, get) => ({
  ...EMPTY_SECTIONS,

  loadToolWorkspace: async () => {
    // Each section is independent so one failing API never blocks the others.
    await Promise.all([
      get().fetchTools(),
      get().fetchHistory(),
      get().fetchConfirmations(),
      get().fetchEmergencyStop(),
      get().fetchMetrics(),
    ]);
  },

  fetchTools: async () => {
    if (get().toolsStatus === 'loading') return false;

    set({ toolsStatus: 'loading', toolsError: null });

    try {
      const response = await toolsApi.list();

      if (!response || !Array.isArray(response.tools)) {
        throw new Error('The backend did not return a tool catalogue.');
      }

      set({
        tools: response.tools.map(mapBackendTool),
        toolsStatus: 'ready',
        toolsLoaded: true,
        toolsError: null,
      });
      return true;
    } catch (error) {
      // Keep the last confirmed catalogue visible on a failed refresh.
      set((state) => ({
        toolsStatus: state.toolsLoaded ? 'ready' : 'error',
        toolsError: getErrorMessage(
          error,
          'The tool catalogue could not be loaded.'
        ),
      }));
      return false;
    }
  },

  fetchHistory: async (silent = false) => {
    if (historyInFlight) return false;
    historyInFlight = true;

    if (!silent) {
      set({ historyStatus: 'loading', historyError: null });
    }

    try {
      const response = await toolsApi.listExecutions({ limit: 50, offset: 0 });

      if (!response || !Array.isArray(response.executions)) {
        throw new Error('The backend did not return an execution history list.');
      }

      set({
        executionLogs: response.executions.map(mapBackendExecution),
        historyTotal:
          typeof response.total === 'number'
            ? response.total
            : response.executions.length,
        historyStatus: 'ready',
        historyLoaded: true,
        historyError: null,
        historyViewCleared: false,
      });
      return true;
    } catch (error) {
      set((state) => ({
        historyStatus: state.historyLoaded ? 'ready' : 'error',
        historyError: getErrorMessage(
          error,
          'Execution history could not be loaded.'
        ),
      }));
      return false;
    } finally {
      historyInFlight = false;
    }
  },

  fetchExecutionDetail: async (executionId) => {
    try {
      const response = await toolsApi.getExecution(executionId);

      if (!response || typeof response.id !== 'number') {
        throw new Error('The backend did not return that execution record.');
      }

      const record = mapBackendExecution(response);

      set((state) => ({
        executionLogs: state.executionLogs.map((entry) =>
          entry.executionId === executionId ? record : entry
        ),
        historyError: null,
      }));

      return record;
    } catch (error) {
      set({
        historyError: getErrorMessage(
          error,
          'That execution record could not be loaded.'
        ),
      });
      return null;
    }
  },

  fetchConfirmations: async (silent = false) => {
    if (confirmationsInFlight) return false;
    confirmationsInFlight = true;

    if (!silent) {
      set({ confirmationsStatus: 'loading', confirmationsError: null });
    }

    try {
      const response = await toolsApi.listConfirmations();

      if (!response || !Array.isArray(response.confirmations)) {
        throw new Error(
          'The backend did not return a pending confirmation list.'
        );
      }

      set({
        pendingConfirmations: response.confirmations.map(mapConfirmation),
        confirmationsStatus: 'ready',
        confirmationsLoaded: true,
        confirmationsError: null,
      });
      return true;
    } catch (error) {
      set((state) => ({
        confirmationsStatus: state.confirmationsLoaded ? 'ready' : 'error',
        confirmationsError: getErrorMessage(
          error,
          'Pending confirmations could not be loaded.'
        ),
      }));
      return false;
    } finally {
      confirmationsInFlight = false;
    }
  },

  fetchEmergencyStop: async (silent = false) => {
    if (emergencyStopInFlight) return false;
    emergencyStopInFlight = true;

    if (!silent) {
      set({ emergencyStopStatus: 'loading', emergencyStopError: null });
    }

    try {
      const response = await toolsApi.getEmergencyStop();

      if (!response || typeof response.engaged !== 'boolean') {
        throw new Error(
          'The backend did not return an emergency-stop state.'
        );
      }

      set({
        emergencyStop: response,
        emergencyStopStatus: 'ready',
        emergencyStopError: null,
      });
      return true;
    } catch (error) {
      // Preserve the last confirmed server state; never guess.
      set((state) => ({
        emergencyStopStatus: state.emergencyStop ? 'ready' : 'error',
        emergencyStopError: getErrorMessage(
          error,
          'Emergency-stop status could not be loaded.'
        ),
      }));
      return false;
    } finally {
      emergencyStopInFlight = false;
    }
  },

  fetchMetrics: async (silent = false) => {
    if (metricsInFlight) return false;
    metricsInFlight = true;

    if (!silent) {
      set({ metricsStatus: 'loading', metricsError: null });
    }

    try {
      const response = await toolsApi.getMetrics();

      if (!response || typeof response.executions_total !== 'number') {
        throw new Error('The backend did not return tool metrics.');
      }

      set({
        metrics: response,
        metricsStatus: 'ready',
        metricsError: null,
      });
      return true;
    } catch (error) {
      set((state) => ({
        metricsStatus: state.metrics ? 'ready' : 'error',
        metricsError: getErrorMessage(
          error,
          'Tool execution metrics could not be loaded.'
        ),
      }));
      return false;
    } finally {
      metricsInFlight = false;
    }
  },

  executeTool: async (toolId, params) => {
    if (get().executionPending) {
      return {
        ok: false,
        record: null,
        error: 'Another tool execution is already in progress.',
      };
    }

    const tool = get().tools.find((entry) => entry.id === toolId);

    if (!tool) {
      const error = 'That tool is not in the backend catalogue.';
      set({ executionError: error });
      return { ok: false, record: null, error };
    }

    if (!tool.enabled) {
      const error = 'This tool is disabled by server configuration.';
      set({ executionError: error });
      return { ok: false, record: null, error };
    }

    set({
      executionPending: true,
      executionError: null,
      actionNotice: null,
      lastActionResult: null,
    });

    const argumentKeys = Object.keys(params);

    try {
      const result = await toolsApi.execute(
        tool.backendName,
        params,
        nextIdempotencyKey(tool.backendName)
      );

      if (!result || typeof result.success !== 'boolean') {
        throw new Error('The backend did not return an execution result.');
      }

      const record = mapResultRecord(result, {
        toolName: tool.backendName,
        category: tool.category,
        confirmationId: result.confirmation_id ?? undefined,
        argumentKeys,
      });

      if (result.confirmation_required) {
        // A confirmation is not a success. Refresh the server-owned lists so
        // the approval UI shows the stored preview and persisted execution.
        set({
          executionPending: false,
          lastActionResult: record,
          actionNotice:
            'The backend requires confirmation before this action can run.',
        });
        await Promise.all([
          get().fetchConfirmations(true),
          get().fetchHistory(true),
        ]);
        return { ok: false, record, error: null };
      }

      const failureMessage = result.success
        ? null
        : result.error_message ??
          result.error_code ??
          'The backend reported that the execution did not succeed.';

      set((state) => ({
        executionPending: false,
        executionError: failureMessage,
        lastActionResult: record,
        executionLogs: [record, ...state.executionLogs],
      }));

      // Replace the immediate record with the server's audited history when
      // it is available. A failed refresh preserves the record above.
      await Promise.all([get().fetchHistory(true), get().fetchMetrics(true)]);

      return {
        ok: result.success === true,
        record,
        error: failureMessage,
      };
    } catch (error) {
      const message = getErrorMessage(
        error,
        'The tool could not be executed.'
      );
      set({ executionPending: false, executionError: message });
      return { ok: false, record: null, error: message };
    }
  },

  approvePendingConfirmation: async (confirmationId) => {
    if (get().confirmationPendingIds.includes(confirmationId)) return false;

    set((state) => ({
      confirmationPendingIds: [...state.confirmationPendingIds, confirmationId],
      confirmationsError: null,
      executionError: null,
      actionNotice: null,
    }));

    try {
      const response = await toolsApi.confirm(confirmationId);

      if (!response || !response.confirmation) {
        throw new Error('The backend did not return the resolved confirmation.');
      }

      const record = mapConfirmationActionRecord(
        response.confirmation,
        response.result
      );

      set((state) => ({
        confirmationPendingIds: state.confirmationPendingIds.filter(
          (id) => id !== confirmationId
        ),
        pendingConfirmations: state.pendingConfirmations.filter(
          (entry) => entry.id !== confirmationId
        ),
        lastActionResult: record,
        executionError: record.success
          ? null
          : record.errorMessage ??
            record.errorCode ??
            'The backend reported that the approved action did not succeed.',
        actionNotice: record.success
          ? 'The backend executed the approved action.'
          : 'The backend resolved the confirmation.',
      }));

      await Promise.all([
        get().fetchHistory(true),
        get().fetchConfirmations(true),
        get().fetchMetrics(true),
      ]);

      return true;
    } catch (error) {
      // Keep the pending confirmation so the operator can retry.
      set((state) => ({
        confirmationPendingIds: state.confirmationPendingIds.filter(
          (id) => id !== confirmationId
        ),
        confirmationsError: getErrorMessage(
          error,
          'The confirmation could not be approved.'
        ),
      }));
      return false;
    }
  },

  rejectPendingConfirmation: async (confirmationId) => {
    if (get().confirmationPendingIds.includes(confirmationId)) return false;

    set((state) => ({
      confirmationPendingIds: [...state.confirmationPendingIds, confirmationId],
      confirmationsError: null,
      executionError: null,
      actionNotice: null,
    }));

    try {
      const response = await toolsApi.reject(confirmationId);

      if (!response || !response.confirmation) {
        throw new Error('The backend did not return the resolved confirmation.');
      }

      set((state) => ({
        confirmationPendingIds: state.confirmationPendingIds.filter(
          (id) => id !== confirmationId
        ),
        pendingConfirmations: state.pendingConfirmations.filter(
          (entry) => entry.id !== confirmationId
        ),
        lastActionResult: null,
        actionNotice: `The backend marked the confirmation "${response.confirmation.status}".`,
      }));

      await Promise.all([
        get().fetchHistory(true),
        get().fetchConfirmations(true),
        get().fetchMetrics(true),
      ]);

      return true;
    } catch (error) {
      set((state) => ({
        confirmationPendingIds: state.confirmationPendingIds.filter(
          (id) => id !== confirmationId
        ),
        confirmationsError: getErrorMessage(
          error,
          'The confirmation could not be rejected.'
        ),
      }));
      return false;
    }
  },

  engageEmergencyStop: async () => {
    if (get().emergencyStopPending) return false;

    set({ emergencyStopPending: true, emergencyStopError: null });

    try {
      const response = await toolsApi.engageEmergencyStop();

      if (!response || typeof response.engaged !== 'boolean') {
        throw new Error(
          'The backend did not return an emergency-stop state.'
        );
      }

      set({
        emergencyStop: response,
        emergencyStopStatus: 'ready',
        emergencyStopPending: false,
        emergencyStopError: null,
        actionNotice: response.engaged
          ? 'The backend confirmed the emergency stop.'
          : 'The backend reported the emergency stop is not engaged.',
      });

      await Promise.all([
        get().fetchConfirmations(true),
        get().fetchHistory(true),
      ]);

      return true;
    } catch (error) {
      // Preserve the last confirmed state; do not flip locally.
      set({
        emergencyStopPending: false,
        emergencyStopError: getErrorMessage(
          error,
          'The emergency stop could not be engaged.'
        ),
      });
      return false;
    }
  },

  recoverEmergencyStop: async (recoveryToken) => {
    if (get().emergencyStopPending) return false;

    const token = recoveryToken.trim();

    if (!token) {
      set({
        emergencyStopError:
          'A server recovery token is required to clear the emergency stop.',
      });
      return false;
    }

    set({ emergencyStopPending: true, emergencyStopError: null });

    try {
      const response = await toolsApi.recoverEmergencyStop(token);

      if (!response || typeof response.engaged !== 'boolean') {
        throw new Error(
          'The backend did not return an emergency-stop state.'
        );
      }

      set({
        emergencyStop: response,
        emergencyStopStatus: 'ready',
        emergencyStopPending: false,
        emergencyStopError: null,
        actionNotice: 'The backend confirmed the emergency stop was cleared.',
      });

      await get().fetchConfirmations(true);
      return true;
    } catch (error) {
      set({
        emergencyStopPending: false,
        emergencyStopError: getErrorMessage(
          error,
          'The emergency stop could not be recovered.'
        ),
      });
      return false;
    }
  },

  clearHistoryView: () =>
    set({ historyViewCleared: true, historyError: null }),

  clearToolsError: () => set({ toolsError: null }),
  clearHistoryError: () => set({ historyError: null }),
  clearConfirmationsError: () => set({ confirmationsError: null }),
  clearEmergencyStopError: () => set({ emergencyStopError: null }),
  clearMetricsError: () => set({ metricsError: null }),
  clearExecutionError: () => set({ executionError: null }),
  clearActionNotice: () => set({ actionNotice: null }),
}));

export default useToolStore;
