export type ToolCategory =
  | 'Information'
  | 'Filesystem'
  | 'Application'
  | 'Process'
  | 'Desktop'
  | 'Background Tasks';

export type ToolPermissionLevel = 'read_only' | 'elevated' | 'admin_required';

export interface ToolParameterDef {
  name: string;
  type: 'string' | 'number' | 'boolean' | 'path';
  description: string;
  required: boolean;
  default?: string | number | boolean;
}

export interface ToolDefinition {
  id: string;
  name: string;
  category: ToolCategory;
  description: string;
  permission: ToolPermissionLevel;
  confirmationRequired: boolean;
  enabled: boolean;
  parameters: ToolParameterDef[];
  sampleOutput: string;
  backendName?: string;
}

export interface ToolExecutionLog {
  id: string;
  toolId: string;
  toolName: string;
  executedAt: string;
  durationMs: number;
  status: 'success' | 'failed' | 'cancelled' | 'pending_approval';
  parameters: Record<string, unknown>;
  output?: string;
  errorMessage?: string;
  confirmedBy?: string;
  confirmationId?: number;
  executionId?: number;
}
