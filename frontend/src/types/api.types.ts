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
  | 'cancelled'
  | 'suppressed';

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
  deduplicated?: boolean;
  superseded?: boolean;
}

export interface MemoryRetrievalRequest {
  query: string;
  category?: string | null;
  limit?: number;
  mode?: string;
  min_score?: number | null;
  allow_keyword_fallback?: boolean;
}

export interface RetrievedMemoryItem {
  id: number | null;
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
  result?: ToolResultResponse | null;
}

export interface ToolMetricsResponse {
  executions_total: number;
  success_total: number;
  failure_total: number;
  timeout_total: number;
  cancelled_total: number;
  provider_failure_total: number;
  retry_total: number;
  confirmation_approved_total: number;
  confirmation_rejected_total: number;
  duplicate_prevented_total: number;
  recovery_total: number;
  duration_samples: number;
  average_duration_ms?: number | null;
  max_duration_ms?: number | null;
  by_tool?: Record<string, Record<string, number>>;
  since?: string | null;
}

export interface EmergencyStopResponse {
  engaged: boolean;
  engaged_at?: string | null;
  engaged_by_user_id?: number | null;
  recovery_configured: boolean;
}

// ==========================================
// Notifications
// ==========================================
export type BackendNotificationType = 'reminder' | 'system';
export type BackendNotificationDeliveryStatus =
  | 'pending'
  | 'delivered'
  | 'failed';

export interface NotificationResponse {
  id: number;
  user_id: number;
  task_id: number | null;
  reminder_id: number | null;
  notification_type: BackendNotificationType;
  delivery_status: BackendNotificationDeliveryStatus;
  title: string;
  body: string;
  task_title: string | null;
  reminder_at: string | null;
  is_read: boolean;
  read_at: string | null;
  snoozed_at: string | null;
  snoozed_until: string | null;
  snooze_count: number;
  delivered_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationSnoozeRequest {
  /** Exactly one of duration_minutes and snooze_until. */
  duration_minutes?: number;
  snooze_until?: string; // ISO 8601 with timezone offset
}

export interface NotificationReadAllResponse {
  updated: number;
}

export interface NotificationListQueryParams {
  unread_only?: boolean;
  limit?: number;
  offset?: number;
}

export interface NotificationUnreadQueryParams {
  limit?: number;
  offset?: number;
}

export interface NotificationPreferenceUpdate {
  in_app_enabled?: boolean;
  quiet_hours_enabled?: boolean;
  quiet_hours_start?: string; // local wall-clock HH:MM[:SS]
  quiet_hours_end?: string; // local wall-clock HH:MM[:SS]
  timezone?: string;
}

export interface NotificationPreferenceResponse {
  id: number | null;
  user_id: number;
  in_app_enabled: boolean;
  quiet_hours_enabled: boolean;
  quiet_hours_start: string; // HH:MM:SS, no timezone
  quiet_hours_end: string; // HH:MM:SS, no timezone
  timezone: string;
  quiet_hours_active: boolean;
  quiet_hours_ends_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

// ==========================================
// Recurrences
// ==========================================
export type BackendRecurrenceFrequency = 'daily' | 'weekly' | 'monthly';

export interface RecurrenceCreate {
  frequency: BackendRecurrenceFrequency;
  interval?: number; // 1..365
  weekdays?: number[] | null; // 0 = Monday .. 6 = Sunday, weekly only
  start_date?: string | null; // YYYY-MM-DD, defaults to the task due date
  end_date?: string | null; // YYYY-MM-DD
  timezone?: string | null;
}

export interface RecurrenceUpdate {
  frequency?: BackendRecurrenceFrequency | null;
  interval?: number | null;
  weekdays?: number[] | null;
  end_date?: string | null;
  timezone?: string | null;
  is_active?: boolean | null;
}

export interface RecurrenceResponse {
  id: number;
  user_id: number;
  task_id: number;
  frequency: BackendRecurrenceFrequency;
  interval: number;
  weekdays: number[];
  start_date: string; // YYYY-MM-DD
  end_date: string | null;
  timezone: string | null;
  anchor_time: string | null;
  is_active: boolean;
  last_generated_date: string | null;
  next_generation_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RecurrenceOccurrenceResponse {
  id: number;
  user_id: number;
  recurrence_id: number | null;
  task_id: number;
  occurrence_date: string; // YYYY-MM-DD
  created_at: string;
}

export interface RecurrenceListQueryParams {
  task_id?: number;
  include_inactive?: boolean;
  limit?: number;
  offset?: number;
}

export interface RecurrenceOccurrenceQueryParams {
  limit?: number;
  offset?: number;
}

// ==========================================
// Background Tasks
// ==========================================
export type BackendBackgroundTaskStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled';

/**
 * Exact public task representation from `app/api/background_tasks.py`.
 * Stored handler parameters are deliberately absent, and the backend does not
 * expose `updated_at` or progress fields.
 */
export interface BackgroundTaskResponse {
  id: number;
  task_type: string;
  status: BackendBackgroundTaskStatus;
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface BackgroundTaskListResponse {
  tasks: BackgroundTaskResponse[];
  total: number;
}

export interface BackgroundTaskListQueryParams {
  status?: BackendBackgroundTaskStatus;
  limit?: number;
  offset?: number;
}

// ==========================================
// Local Text-to-Speech (Phase 18 Stage 1)
// ==========================================
export interface TTSStatusResponse {
  enabled: boolean;
  available: boolean;
  voice: string;
  voices: string[];
  speed: number;
  max_text_chars: number;
  max_audio_bytes: number;
}

export interface TTSSettingsResponse {
  voice: string;
  speed: number;
  default_voice: string;
  installed_voices: string[];
  enabled: boolean;
  available: boolean;
  max_text_chars: number;
  max_audio_bytes: number;
}

export interface TTSSettingsUpdate {
  voice?: string;
  speed?: number;
  reset?: boolean;
}

export interface TTSSynthesizeRequest {
  text: string;
}

// ==========================================
// Local Speech-to-Text (Phase 18 Stage 2)
// ==========================================
export interface STTTranscribeResponse {
  success: boolean;
  text: string;
  language: string | null;
  processing_ms: number | null;
}

export interface STTStatusResponse {
  enabled: boolean;
  available: boolean;
  model: string;
  device: string;
  compute_type: string;
  max_upload_bytes: number;
  max_duration_seconds: number;
  supported_media_types: string[];
}

// ==========================================
// Local Voice Activity Detection (Phase 18 Stage 3)
// ==========================================
export interface VADStatusResponse {
  enabled: boolean;
  available: boolean;
  engine: string;
  engine_version: string;
  model_name: string;
  device: string;
  sample_rate: number;
  frame_samples: number;
  speech_threshold: number;
  silence_threshold: number;
  min_speech_duration_seconds: number;
  silence_duration_seconds: number;
  max_utterance_seconds: number;
  max_session_seconds: number;
  max_session_count: number;
  max_upload_bytes: number;
}

export interface VADSessionResponse {
  session_id: string;
  state: string;
  sample_rate: number;
  frame_samples: number;
  sample_position: number;
}

export interface VADSegmentSummary {
  segment_id: string;
  start_seconds: number;
  end_seconds: number;
  duration_seconds: number;
  reason: string;
  peak_probability: number;
}

export interface VADFrameResponse {
  session_id: string;
  state: string;
  speech_probability: number;
  speech_detected: boolean;
  speech_duration_seconds: number;
  events: string[];
  completed_segments: VADSegmentSummary[];
  sample_position: number;
  processing_ms: number;
}

// ==========================================
// Local Voice Conversation Manager (Phase 18 Stage 4)
// ==========================================
export type VoiceConversationState =
  | 'idle'
  | 'listening'
  | 'speech_detected'
  | 'transcribing'
  | 'processing'
  | 'speaking'
  | 'interrupted'
  | 'follow_up_listening'
  | 'stopping'
  | 'error';

export type VoiceConversationEvent =
  | 'speech_start'
  | 'barge_in'
  | 'playback_complete'
  | 'playback_failed';

export interface VoiceStatusResponse {
  enabled: boolean;
  available: boolean;
  components: Record<string, boolean>;
  states: VoiceConversationState[];
  active_session_id: string | null;
  state: VoiceConversationState;
  follow_up_timeout_seconds: number;
  max_sessions: number;
  max_turn_audio_bytes: number;
  barge_in_min_speech_seconds: number;
  barge_in_speech_threshold: number;
}

export interface VoiceSessionResponse {
  session_id: string;
  state: VoiceConversationState;
  conversation_id: number | null;
  created_at: string;
  last_activity: string;
  follow_up_timeout_seconds: number;
  follow_up_expires_at: string | null;
  stop_reason: string | null;
  error: string | null;
}

export interface VoicePendingConfirmation {
  confirmation_id: number | null;
  tool: string | null;
  category: string | null;
  expires_at: string | null;
}

export interface VoiceTurnResponse extends VoiceSessionResponse {
  transcript: string | null;
  assistant_response: string | null;
  provider: string | null;
  model: string | null;
  tts_available: boolean;
  audio_base64: string | null;
  audio_media_type: string | null;
  pending_confirmations: VoicePendingConfirmation[];
  tts_error: string | null;
}

export interface VoiceEventRequest {
  event: VoiceConversationEvent;
  vad_session_id?: string | null;
}

export interface VoiceStopResponse {
  session_id: string;
  stopped: boolean;
  state: VoiceConversationState;
}
