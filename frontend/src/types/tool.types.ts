/**
 * View models for the backend tool engine.
 *
 * Every field here is derived from a tool API response. The backend never
 * returns sample output, execution payloads in history, or a per-user
 * enable/disable control, so none of those are modelled or invented.
 */

/** Risk classification returned by the backend tool registry. */
export type ToolCategory = 'read_only' | 'reversible' | 'sensitive';

export type ToolParameterType = 'string' | 'number' | 'boolean' | 'path' | 'object' | 'array';

export interface ToolParameterDef {
  name: string;
  type: ToolParameterType;
  description: string;
  required: boolean;
  default?: string | number | boolean;
  enumValues?: Array<string | number | boolean>;
}

export interface ToolDefinition {
  /** Stable UI key derived from the backend tool name. */
  id: string;
  /** Display label derived from the backend tool name. */
  name: string;
  /** Exact backend tool identifier used for execution. */
  backendName: string;
  /** Exact backend risk category value. */
  category: string;
  description: string;
  confirmationRequired: boolean;
  /** Availability is owned by server configuration; never toggled locally. */
  enabled: boolean;
  allowedInPrivate: boolean;
  parameters: ToolParameterDef[];
  outputSchema: Record<string, unknown>;
}

/** Exact confirmation record returned by the backend. */
export interface ToolConfirmation {
  id: number;
  toolName: string;
  category: string;
  status: string;
  argumentsPreview: string;
  createdAt: string;
  expiresAt: string;
  resolvedAt?: string | null;
  conversationId?: number | null;
  executionId?: number | null;
  isExpired: boolean;
}

/**
 * One execution attempt. History rows are metadata-only, so
 * `outputAvailable` stays false for them. `durationMs` is null when the
 * backend did not record a duration; it is never estimated locally.
 */
export interface ToolExecutionLog {
  id: string;
  executionId?: number | null;
  toolName: string;
  category?: string | null;
  executedAt: string;
  startedAt?: string | null;
  completedAt?: string | null;
  durationMs: number | null;
  /** Exact backend status value (success, error, timeout, denied, ...). */
  status: string;
  lifecycleStatus?: string | null;
  success: boolean;
  confirmationStatus?: string | null;
  /** Argument field names only; the backend never returns argument values. */
  argumentKeys: string[];
  output: string | null;
  outputAvailable: boolean;
  errorMessage?: string | null;
  errorCode?: string | null;
  errorCategory?: string | null;
  provider?: string | null;
  resultBytes?: number | null;
  truncated?: boolean;
  retryCount?: number;
  confirmationId?: number;
  conversationId?: number | null;
}

export function toolCategoryLabel(category: string): string {
  if (category === 'read_only') return 'Read only';
  if (category === 'reversible') return 'Reversible';
  if (category === 'sensitive') return 'Sensitive';
  return category;
}
