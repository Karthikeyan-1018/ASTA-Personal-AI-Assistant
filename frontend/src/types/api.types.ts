/**
 * Backend API Data Contracts matching FastAPI Pydantic schemas exactly.
 */

// ==========================================
// Authentication
// ==========================================
export interface OwnerResponse {
  id: number;
  username: string;
  created_at: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  owner: OwnerResponse;
  expires_at: string;
  csrf_token: string;
}

export interface MeResponse {
  owner: OwnerResponse;
  session_expires_at: string;
}

export interface OwnerSetupRequest {
  username: string;
  password: string;
}

// ==========================================
// Conversations & Messages
// ==========================================
export type BackendMessageRole = 'user' | 'assistant' | 'system';

export interface ConversationResponse {
  id: number;
  user_id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationCreate {
  title?: string | null;
}

export interface ConversationUpdate {
  title: string;
}

export interface MessageResponse {
  id: number;
  conversation_id: number;
  role: BackendMessageRole;
  content: string;
  provider?: string | null;
  model?: string | null;
  is_private: boolean;
  message_metadata?: Record<string, any> | null;
  created_at: string;
}

export interface MessageListResponse {
  messages: MessageResponse[];
  total: number;
  limit: number;
  offset: number;
}

// ==========================================
// Chat & Streaming
// ==========================================
export interface ChatRequest {
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

export interface PendingConfirmationResponse {
  confirmation_id?: number | null;
  tool?: string | null;
  category?: string | null;
  preview?: string | null;
  expires_at?: string | null;
}

export interface ChatResponse {
  provider: string;
  model: string;
  response: string;
  conversation_id?: number | null;
  tool_calling?: boolean | null;
  tool_executions?: ToolExecutionSummary[] | null;
  pending_confirmations?: PendingConfirmationResponse[] | null;
  limit_reached?: boolean | null;
  provider_failure?: boolean | null;
  confirmation_id?: number | null;
  confirmation_status?: string | null;
}

export interface SSEStartData {
  conversation_id?: number | null;
  provider?: string;
  model?: string;
}

export interface SSEDeltaData {
  text: string;
}

export interface SSEDoneData {
  conversation_id?: number | null;
  provider?: string;
  model?: string;
  finish_reason?: string;
  duration_ms?: number;
  usage?: Record<string, any> | null;
  fallback_used?: boolean;
}

export interface SSEErrorData {
  code: string;
  message: string;
  conversation_id?: number | null;
}

// ==========================================
// Tasks
// ==========================================
export type BackendTaskCategory =
  | 'college'
  | 'career'
  | 'personal'
  | 'fitness'
  | 'project'
  | 'other';

export type BackendTaskPriority = 'low' | 'medium' | 'high' | 'urgent';

export type BackendTaskStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled';

export interface TaskCreate {
  title: string;
  description?: string | null;
  category?: BackendTaskCategory;
  priority?: BackendTaskPriority;
  status?: BackendTaskStatus;
  due_date?: string | null; // YYYY-MM-DD
  due_time?: string | null; // HH:MM[:SS]
  timezone?: string | null;
}

export interface TaskUpdate {
  title?: string | null;
  description?: string | null;
  category?: BackendTaskCategory | null;
  priority?: BackendTaskPriority | null;
  status?: BackendTaskStatus | null;
  due_date?: string | null;
  due_time?: string | null;
  timezone?: string | null;
}

export interface TaskResponse {
  id: number;
  user_id: number;
  title: string;
  description: string | null;
  category: BackendTaskCategory;
  priority: BackendTaskPriority;
  status: BackendTaskStatus;
  due_date: string | null;
  due_time: string | null;
  timezone: string | null;
  is_completed: boolean;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface TaskListResponse {
  tasks: TaskResponse[];
  total: number;
  limit: number;
  offset: number;
}

// ==========================================
// Reminders
// ==========================================
export type BackendReminderStatus =
  | 'pending'
  | 'processing'
  | 'delivered'
  | 'failed'
  | 'cancelled';

export interface ReminderCreate {
  offset_minutes?: number | null;
  remind_at?: string | null; // ISO 8601 with timezone offset
}

export interface ReminderUpdate {
  offset_minutes?: number | null;
  remind_at?: string | null;
}

export interface ReminderResponse {
  id: number;
  user_id: number;
  task_id: number;
  remind_at: string;
  offset_minutes: number | null;
  status: BackendReminderStatus;
  created_at: string;
  updated_at: string;
  delivered_at: string | null;
}

// ==========================================
// Semantic Memory
// ==========================================
export type BackendMemoryStatus = 'pending' | 'approved' | 'rejected';

export interface MemoryCreate {
  content: string;
  category?: string;
}

export interface MemoryUpdate {
  content?: string | null;
  category?: string | null;
}

export interface MemoryResponse {
  id: number;
  user_id: number;
  content: string;
  category: string;
  status: BackendMemoryStatus;
  created_at: string;
  updated_at: string;
}

export interface MemoryRetrievalRequest {
  query: string;
  category?: string | null;
  limit?: number;
  mode?: string;
}

export interface RetrievedMemoryItem {
  content: string;
  category: string;
  relevance_score: number;
}

export interface MemoryRetrievalResponse {
  memories: RetrievedMemoryItem[];
  total: number;
}

export interface MemoryExtractionRequest {
  conversation: string;
  private?: boolean;
}

export interface ExtractedMemoryItem {
  content: string;
  category: string;
  confidence: number;
}

export interface MemoryExtractionResponse {
  candidates: ExtractedMemoryItem[];
}

// ==========================================
// Tools
// ==========================================
export interface ToolDefinitionResponse {
  name: string;
  description: string;
  category: string;
  enabled: boolean;
  requires_confirmation: boolean;
  allowed_in_private: boolean;
  parameters: Record<string, any>;
  output_schema: Record<string, any>;
}

export interface ToolListResponse {
  tools: ToolDefinitionResponse[];
}

export interface ToolExecuteRequest {
  tool: string;
  arguments: Record<string, any>;
}

export interface ToolResultResponse {
  success: boolean;
  status: string;
  tool?: string | null;
  data?: any;
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
  retry_count?: number;
  execution_id?: number | null;
  idempotent_replay?: boolean;
  metadata?: Record<string, any>;
}

export interface ToolExecutionResponse {
  id: number;
  tool_name: string;
  category: string;
  status: string;
  lifecycle_status: string;
  success: boolean;
  confirmation_status: string;
  duration_ms?: number | null;
  retry_count?: number;
  truncated?: boolean;
  error_category?: string | null;
  error_code?: string | null;
  provider?: string | null;
  argument_keys?: string[] | null;
  result_bytes?: number | null;
  conversation_id?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
}

export interface ToolExecutionListResponse {
  executions: ToolExecutionResponse[];
  total: number;
  limit: number;
  offset: number;
}

export interface ToolConfirmationResponse {
  id: number;
  tool_name: string;
  category: string;
  status: string;
  arguments_preview?: string | null;
  created_at: string;
  expires_at?: string | null;
  resolved_at?: string | null;
  conversation_id?: number | null;
  execution_id?: number | null;
  is_expired: boolean;
}

export interface ToolConfirmationListResponse {
  confirmations: ToolConfirmationResponse[];
}

export interface ToolConfirmationActionResponse {
  confirmation: ToolConfirmationResponse;
  result?: Record<string, any> | null;
}

export interface EmergencyStopResponse {
  engaged: boolean;
  engaged_at?: string | null;
  engaged_by_user_id?: number | null;
  recovery_configured: boolean;
}
