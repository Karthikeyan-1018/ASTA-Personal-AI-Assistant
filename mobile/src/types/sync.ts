export type SyncOperation = 'CREATE' | 'UPDATE' | 'DELETE';

export type SyncEntityType = 'task' | 'note' | 'focus';

export type SyncItemStatus = 'queued' | 'processing' | 'failed' | 'done';

export interface SyncQueueItem {
  id: string;
  entity_type: SyncEntityType;
  entity_id: string;
  operation: SyncOperation;
  payload_json: string;
  created_at: string;
  retry_count: number;
  last_attempt: string | null;
  status: SyncItemStatus;
  error_message: string | null;
}

export type SyncMode = 'live' | 'mock';

export interface SyncStatusInfo {
  isOnline: boolean;
  isSyncing: boolean;
  lastSyncTimestamp: string | null;
  pendingCount: number;
  failedCount: number;
  syncMode: SyncMode;
  statusMessage: string;
  serverUrl: string;
  isAuthenticated: boolean;
  username: string | null;
}
