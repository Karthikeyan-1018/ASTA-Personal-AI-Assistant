import { create } from 'zustand';
import type {
  ToolCategory,
  ToolDefinition,
  ToolExecutionLog,
  ToolParameterDef,
} from '../types/tool.types';
import type {
  ToolDefinitionResponse,
  ToolExecutionResponse,
} from '../types/api.types';
import { toolsApi } from '../services/api/tools.api';

const INITIAL_TOOLS: ToolDefinition[] = [
  {
    id: 'tool-sysinfo',
    backendName: 'system_info',
    name: 'System Information Inspector',
    category: 'Information',
    description: 'Inspect OS kernel version, CPU topology, available memory, and system uptime.',
    permission: 'read_only',
    confirmationRequired: false,
    enabled: true,
    parameters: [
      { name: 'includeNetwork', type: 'boolean', description: 'Query network adapter telemetry', required: false, default: true },
    ],
    sampleOutput: JSON.stringify({ os: 'Windows 11 Pro 64-bit', cpu: '16 Cores @ 3.4GHz', ram_gb: 32, uptime: '74h 12m' }, null, 2),
  },
  {
    id: 'tool-fs-read',
    backendName: 'read_file',
    name: 'Safe Filesystem Reader',
    category: 'Filesystem',
    description: 'Read file contents, inspect code repositories, and stream markdown documents.',
    permission: 'read_only',
    confirmationRequired: false,
    enabled: true,
    parameters: [
      { name: 'path', type: 'path', description: 'Relative or absolute project file path', required: true, default: 'frontend/package.json' },
    ],
    sampleOutput: '{"name": "asta-desktop", "version": "1.4.0", "private": true}',
  },
  {
    id: 'tool-fs-write',
    backendName: 'write_file',
    name: 'Filesystem Writer & Patch',
    category: 'Filesystem',
    description: 'Write, append, or patch files inside audited workspace paths.',
    permission: 'elevated',
    confirmationRequired: true,
    enabled: true,
    parameters: [
      { name: 'path', type: 'path', description: 'Target destination file', required: true },
      { name: 'content', type: 'string', description: 'File content to write', required: true },
    ],
    sampleOutput: 'File written successfully: 1,428 bytes synced to disk.',
  },
  {
    id: 'tool-process-list',
    backendName: 'list_processes',
    name: 'Process Table Inspector',
    category: 'Process',
    description: 'List active processes, CPU utilization, thread count, and parent process IDs.',
    permission: 'read_only',
    confirmationRequired: false,
    enabled: true,
    parameters: [
      { name: 'filter', type: 'string', description: 'Process name filter (optional)', required: false, default: 'python' },
    ],
    sampleOutput: JSON.stringify([
      { pid: 14220, name: 'python.exe', cpu_pct: 1.8, memory_mb: 280, cmd: 'uvicorn app.main:app' },
      { pid: 8940, name: 'node.exe', cpu_pct: 0.4, memory_mb: 165, cmd: 'vite' },
    ], null, 2),
  },
  {
    id: 'tool-app-launch',
    backendName: 'launch_app',
    name: 'Desktop App Launcher',
    category: 'Application',
    description: 'Launch approved local desktop applications, editors, and terminal consoles.',
    permission: 'elevated',
    confirmationRequired: true,
    enabled: true,
    parameters: [
      { name: 'appName', type: 'string', description: 'Application alias or executable', required: true, default: 'code' },
      { name: 'workingDir', type: 'path', description: 'Initial directory path', required: false, default: 'C:\\Asta' },
    ],
    sampleOutput: 'Application launched successfully: process spawned with PID 18402.',
  },
  {
    id: 'tool-desktop-notify',
    backendName: 'desktop_notify',
    name: 'Desktop Notification Dispatcher',
    category: 'Desktop',
    description: 'Trigger system notification toasts with sound cues and action links.',
    permission: 'read_only',
    confirmationRequired: false,
    enabled: true,
    parameters: [
      { name: 'title', type: 'string', description: 'Notification title', required: true, default: 'ASTA Alert' },
      { name: 'message', type: 'string', description: 'Notification message body', required: true, default: 'Task execution completed' },
    ],
    sampleOutput: 'Notification delivered to Windows notification center.',
  },
  {
    id: 'tool-bg-indexer',
    backendName: 'vector_indexer',
    name: 'Vector Store Background Indexer',
    category: 'Background Tasks',
    description: 'Run background embedding re-indexing worker over modified documents.',
    permission: 'elevated',
    confirmationRequired: false,
    enabled: true,
    parameters: [
      { name: 'batchSize', type: 'number', description: 'Batch embedding size', required: false, default: 32 },
    ],
    sampleOutput: 'Embedding indexer started: 48 documents queued for vectorization.',
  },
];

const INITIAL_LOGS: ToolExecutionLog[] = [
  {
    id: 'log-1',
    toolId: 'tool-sysinfo',
    toolName: 'System Information Inspector',
    executedAt: '2026-11-14T18:30:10Z',
    durationMs: 45,
    status: 'success',
    parameters: { includeNetwork: true },
    output: JSON.stringify({ os: 'Windows 11 Pro 64-bit', cpu: '16 Cores @ 3.4GHz', ram_gb: 32, uptime: '74h 12m' }, null, 2),
  },
  {
    id: 'log-2',
    toolId: 'tool-fs-read',
    toolName: 'Safe Filesystem Reader',
    executedAt: '2026-11-14T17:45:22Z',
    durationMs: 18,
    status: 'success',
    parameters: { path: 'frontend/package.json' },
    output: '{"name": "frontend", "version": "0.0.0", "dependencies": {...}}',
  },
  {
    id: 'log-3',
    toolId: 'tool-fs-write',
    toolName: 'Filesystem Writer & Patch',
    executedAt: '2026-11-14T16:10:00Z',
    durationMs: 120,
    status: 'success',
    parameters: { path: 'frontend/src/index.css', content: '@import "tailwindcss";' },
    output: 'File written successfully: 840 bytes synced to disk.',
    confirmedBy: 'Owner',
  },
];

function sanitizeToolCategory(cat: string): ToolCategory {
  const lower = cat.toLowerCase();
  if (lower.includes('info')) return 'Information';
  if (lower.includes('file')) return 'Filesystem';
  if (lower.includes('proc')) return 'Process';
  if (lower.includes('app')) return 'Application';
  if (lower.includes('desk')) return 'Desktop';
  return 'Background Tasks';
}

function parseParameters(schema: Record<string, any>): ToolParameterDef[] {
  if (!schema || typeof schema !== 'object') return [];
  const props = schema.properties || {};
  const requiredList: string[] = Array.isArray(schema.required) ? schema.required : [];
  return Object.entries(props).map(([propName, propDef]: [string, any]) => {
    let paramType: 'string' | 'number' | 'boolean' | 'path' = 'string';
    if (propDef.type === 'number' || propDef.type === 'integer') paramType = 'number';
    else if (propDef.type === 'boolean') paramType = 'boolean';
    else if (propName.toLowerCase().includes('path') || propDef.format === 'path') paramType = 'path';

    return {
      name: propName,
      type: paramType,
      description: propDef.description || `Parameter ${propName}`,
      required: requiredList.includes(propName),
      default: propDef.default,
    };
  });
}

function mapBackendTool(bt: ToolDefinitionResponse): ToolDefinition {
  const params = parseParameters(bt.parameters);
  return {
    id: `tool-${bt.name}`,
    backendName: bt.name,
    name: bt.name.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' '),
    category: sanitizeToolCategory(bt.category),
    description: bt.description,
    permission: bt.requires_confirmation ? 'elevated' : 'read_only',
    confirmationRequired: bt.requires_confirmation,
    enabled: bt.enabled,
    parameters: params.length > 0 ? params : [
      { name: 'payload', type: 'string', description: 'Execution payload arguments', required: false },
    ],
    sampleOutput: 'Audited execution output from ASTA core sandbox pipeline.',
  };
}

function mapBackendExecution(be: ToolExecutionResponse): ToolExecutionLog {
  let mappedStatus: 'success' | 'failed' | 'cancelled' | 'pending_approval' = 'success';
  if (be.confirmation_status === 'pending') {
    mappedStatus = 'pending_approval';
  } else if (!be.success) {
    mappedStatus = be.confirmation_status === 'rejected' ? 'cancelled' : 'failed';
  }

  return {
    id: `log-backend-${be.id}`,
    executionId: be.id,
    toolId: `tool-${be.tool_name}`,
    toolName: be.tool_name.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' '),
    executedAt: be.started_at || be.created_at || new Date().toISOString(),
    durationMs: be.duration_ms || 35,
    status: mappedStatus,
    parameters: be.argument_keys ? Object.fromEntries(be.argument_keys.map((k: string) => [k, '***'])) : {},
    output: be.success ? 'Execution completed via audited backend pipeline.' : undefined,
    errorMessage: !be.success ? (be.error_code || 'Execution error') : undefined,
  };
}

interface ToolStoreState {
  tools: ToolDefinition[];
  executionLogs: ToolExecutionLog[];
  pendingConfirmation: ToolExecutionLog | null;
  isLoading: boolean;
  error: string | null;
  isBackendConnected: boolean;
  emergencyStopActive: boolean;
  fetchTools: () => Promise<void>;
  toggleToolEnabled: (id: string) => void;
  executeToolSimulated: (toolId: string, params: Record<string, unknown>) => Promise<ToolExecutionLog>;
  approvePendingConfirmation: () => Promise<void>;
  rejectPendingConfirmation: () => Promise<void>;
  toggleEmergencyStop: () => Promise<void>;
  clearLogs: () => void;
}

export const useToolStore = create<ToolStoreState>((set, get) => ({
  tools: INITIAL_TOOLS,
  executionLogs: INITIAL_LOGS,
  pendingConfirmation: null,
  isLoading: false,
  error: null,
  isBackendConnected: false,
  emergencyStopActive: false,

  fetchTools: async () => {
    set({ isLoading: true, error: null });
    try {
      const data = await toolsApi.list();
      if (data && Array.isArray(data.tools) && data.tools.length > 0) {
        const mappedTools = data.tools.map(mapBackendTool);
        set({ tools: mappedTools, isBackendConnected: true, isLoading: false });
      } else {
        set({ isBackendConnected: true, isLoading: false });
      }

      // Check emergency stop
      try {
        const eStop = await toolsApi.getEmergencyStop();
        if (eStop) {
          set({ emergencyStopActive: eStop.engaged });
        }
      } catch {
        // Handled
      }

      // Fetch recent execution history
      try {
        const history = await toolsApi.listExecutions({ limit: 20 });
        if (history && Array.isArray(history.executions) && history.executions.length > 0) {
          const mappedLogs = history.executions.map(mapBackendExecution);
          set({ executionLogs: mappedLogs });
        }
      } catch {
        // Handled
      }

      // Fetch pending confirmations
      try {
        const confs = await toolsApi.listConfirmations();
        if (confs && Array.isArray(confs.confirmations) && confs.confirmations.length > 0) {
          const firstPending = confs.confirmations.find(
            (c) => c.status === 'pending' && !c.is_expired
          );
          if (firstPending) {
            const pendingLog: ToolExecutionLog = {
              id: `log-conf-${firstPending.id}`,
              confirmationId: firstPending.id,
              executionId: firstPending.execution_id ?? undefined,
              toolId: `tool-${firstPending.tool_name}`,
              toolName: firstPending.tool_name,
              executedAt: firstPending.created_at,
              durationMs: 0,
              status: 'pending_approval',
              parameters:
                typeof firstPending.arguments_preview === 'object' && firstPending.arguments_preview !== null
                  ? (firstPending.arguments_preview as Record<string, unknown>)
                  : { preview: firstPending.arguments_preview || '' },
            };
            set({ pendingConfirmation: pendingLog });
          }
        }
      } catch {
        // Handled
      }
    } catch (err: any) {
      set({
        isLoading: false,
        isBackendConnected: false,
        error: err?.message || 'Could not connect to tools service',
      });
    }
  },

  toggleToolEnabled: (id) =>
    set((state) => ({
      tools: state.tools.map((t) => (t.id === id ? { ...t, enabled: !t.enabled } : t)),
    })),

  executeToolSimulated: async (toolId, params) => {
    const tool = get().tools.find((t) => t.id === toolId);
    if (!tool) throw new Error(`Tool not found: ${toolId}`);

    const targetToolName = tool.backendName || tool.id.replace(/^tool-/, '');
    const startTime = Date.now();

    // Attempt real backend execution if available
    try {
      const idempotencyKey = `exec-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const res = await toolsApi.execute(targetToolName, params as Record<string, any>, idempotencyKey);

      const durationMs = res.duration_ms ? Math.round(res.duration_ms) : Date.now() - startTime;

      if (res.confirmation_required) {
        const confLog: ToolExecutionLog = {
          id: `log-conf-${res.confirmation_id || Date.now()}`,
          confirmationId: res.confirmation_id ?? undefined,
          executionId: res.execution_id ?? undefined,
          toolId: tool.id,
          toolName: tool.name,
          executedAt: new Date().toISOString(),
          durationMs,
          status: 'pending_approval',
          parameters: params,
        };
        set((state) => ({
          pendingConfirmation: confLog,
          executionLogs: [confLog, ...state.executionLogs],
        }));
        return confLog;
      }

      const outText =
        typeof res.data === 'string'
          ? res.data
          : res.data !== null && res.data !== undefined
          ? JSON.stringify(res.data, null, 2)
          : 'Execution completed successfully.';

      const successLog: ToolExecutionLog = {
        id: `log-exec-${res.execution_id || Date.now()}`,
        executionId: res.execution_id ?? undefined,
        toolId: tool.id,
        toolName: tool.name,
        executedAt: new Date().toISOString(),
        durationMs,
        status: res.success ? 'success' : 'failed',
        parameters: params,
        output: res.success ? outText : undefined,
        errorMessage: !res.success ? (res.error_message || 'Tool execution rejected') : undefined,
      };

      set((state) => ({
        executionLogs: [successLog, ...state.executionLogs],
      }));
      return successLog;
    } catch {
      // Offline fallback: simulated execution
      const newLog: ToolExecutionLog = {
        id: `log-${Date.now()}`,
        toolId: tool.id,
        toolName: tool.name,
        executedAt: new Date().toISOString(),
        durationMs: Math.floor(Math.random() * 80) + 25,
        status: tool.confirmationRequired ? 'pending_approval' : 'success',
        parameters: params,
        output: tool.confirmationRequired ? undefined : tool.sampleOutput,
      };

      if (tool.confirmationRequired) {
        set((state) => ({
          pendingConfirmation: newLog,
          executionLogs: [newLog, ...state.executionLogs],
        }));
        return newLog;
      }

      await new Promise((resolve) => setTimeout(resolve, 350));
      set((state) => ({
        executionLogs: [newLog, ...state.executionLogs],
      }));
      return newLog;
    }
  },

  approvePendingConfirmation: async () => {
    const pending = get().pendingConfirmation;
    if (!pending) return;

    if (pending.confirmationId) {
      try {
        await toolsApi.confirm(pending.confirmationId);
      } catch {
        // Handled
      }
    }

    set((state) => {
      const targetId = pending.id;
      const tool = state.tools.find((t) => t.id === pending.toolId);
      return {
        pendingConfirmation: null,
        executionLogs: state.executionLogs.map((log) =>
          log.id === targetId
            ? {
                ...log,
                status: 'success',
                output: tool?.sampleOutput || 'Action executed successfully after owner confirmation.',
                confirmedBy: 'Owner (Manual Approval)',
              }
            : log
        ),
      };
    });
  },

  rejectPendingConfirmation: async () => {
    const pending = get().pendingConfirmation;
    if (!pending) return;

    if (pending.confirmationId) {
      try {
        await toolsApi.reject(pending.confirmationId);
      } catch {
        // Handled
      }
    }

    set((state) => {
      const targetId = pending.id;
      return {
        pendingConfirmation: null,
        executionLogs: state.executionLogs.map((log) =>
          log.id === targetId
            ? {
                ...log,
                status: 'cancelled',
                errorMessage: 'Execution rejected by owner policy confirmation.',
              }
            : log
        ),
      };
    });
  },

  toggleEmergencyStop: async () => {
    try {
      const res = await toolsApi.engageEmergencyStop();
      set({ emergencyStopActive: res.engaged });
    } catch {
      // Toggle locally if backend unavailable
      set((state) => ({ emergencyStopActive: !state.emergencyStopActive }));
    }
  },

  clearLogs: () => set({ executionLogs: [] }),
}));
