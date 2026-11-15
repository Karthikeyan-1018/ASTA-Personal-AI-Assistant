export type ChatRole = 'user' | 'assistant' | 'system';

export type StreamPhase = 'idle' | 'starting' | 'streaming' | 'error';

export interface ChatRequestPayload {
  message: string;
  private?: boolean;
  conversation_id?: number | null;
}

export interface ToolExecutionSummary {
  success: boolean;
  status?: string | null;
  tool?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  error_category?: string | null;
  confirmation_required: boolean;
  confirmation_id?: number | null;
  confirmation_status?: string | null;
  truncated: boolean;
  duration_ms?: number | null;
  result_bytes?: number | null;
  provider?: string | null;
}

export interface PendingConfirmationItem {
  confirmation_id?: number | null;
  tool?: string | null;
  category?: string | null;
  preview?: string | null;
  expires_at?: string | null;
  resolution?: 'pending' | 'approving' | 'rejecting' | 'executed' | 'failed' | 'rejected' | 'expired';
}

export interface ChatResponsePayload {
  provider: string;
  model: string;
  response: string;
  conversation_id?: number | null;
  tool_calling?: boolean | null;
  tool_executions?: ToolExecutionSummary[] | null;
  pending_confirmations?: PendingConfirmationItem[] | null;
  limit_reached?: boolean | null;
  provider_failure?: boolean | null;
  confirmation_id?: number | null;
  confirmation_status?: string | null;
}

export interface SSEStartEventData {
  conversation_id?: number | null;
  provider?: string;
  model?: string;
}

export interface SSEDeltaEventData {
  text?: string;
  delta?: string;
  content?: string;
}

export interface SSEDoneEventData {
  conversation_id?: number | null;
  provider?: string;
  model?: string;
  finish_reason?: string | null;
  duration_ms?: number | null;
  fallback_used?: boolean;
  tool_executions?: ToolExecutionSummary[] | null;
  pending_confirmations?: PendingConfirmationItem[] | null;
}

export interface SSEErrorEventData {
  code?: string;
  message?: string;
  conversation_id?: number | null;
}

export interface ConversationMessage {
  id: string;
  role: ChatRole;
  content: string;
  timestamp: string;
  provider?: string;
  model?: string;
  isPrivate?: boolean;
  toolExecutions?: ToolExecutionSummary[];
  pendingConfirmations?: PendingConfirmationItem[];
}