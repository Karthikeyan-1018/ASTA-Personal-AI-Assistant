import { getDatabase } from '../database/db';
import { SyncQueueItem, SyncOperation, SyncEntityType } from '../types/sync';

interface RawQueueRow {
  id: string;
  entity_type: string;
  entity_id: string;
  operation: string;
  payload_json: string;
  created_at: string;
  retry_count: number;
  last_attempt: string | null;
  status: string;
  error_message: string | null;
}

export async function enqueueSyncOperation(
  entityType: SyncEntityType,
  entityId: string,
  operation: SyncOperation,
  payload: any
): Promise<SyncQueueItem> {
  const db = await getDatabase();
  const now = new Date().toISOString();
  const queueId = `sq-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const payloadJson = JSON.stringify(payload);

  // If there's an existing queued operation for the same entity, coalesce or update if needed
  await db.runAsync(
    `INSERT INTO sync_queue (
      id, entity_type, entity_id, operation, payload_json,
      created_at, retry_count, last_attempt, status, error_message
    ) VALUES (?, ?, ?, ?, ?, ?, 0, NULL, 'queued', NULL)`,
    queueId,
    entityType,
    entityId,
    operation,
    payloadJson,
    now
  );

  return {
    id: queueId,
    entity_type: entityType,
    entity_id: entityId,
    operation,
    payload_json: payloadJson,
    created_at: now,
    retry_count: 0,
    last_attempt: null,
    status: 'queued',
    error_message: null,
  };
}

export async function getPendingSyncQueue(): Promise<SyncQueueItem[]> {
  const db = await getDatabase();
  const rows = await db.getAllAsync<RawQueueRow>(
    "SELECT * FROM sync_queue WHERE status IN ('queued', 'failed') ORDER BY created_at ASC"
  );
  return rows.map((r) => ({
    id: r.id,
    entity_type: r.entity_type as SyncEntityType,
    entity_id: r.entity_id,
    operation: r.operation as SyncOperation,
    payload_json: r.payload_json,
    created_at: r.created_at,
    retry_count: r.retry_count,
    last_attempt: r.last_attempt,
    status: r.status as any,
    error_message: r.error_message,
  }));
}

export async function markSyncItemProcessing(queueId: string): Promise<void> {
  const db = await getDatabase();
  const now = new Date().toISOString();
  await db.runAsync(
    "UPDATE sync_queue SET status = 'processing', last_attempt = ? WHERE id = ?",
    now,
    queueId
  );
}

export async function markSyncItemSuccess(queueId: string): Promise<void> {
  const db = await getDatabase();
  await db.runAsync("UPDATE sync_queue SET status = 'done' WHERE id = ?", queueId);
  // Optional cleanup: delete completed queue entries
  await db.runAsync("DELETE FROM sync_queue WHERE status = 'done'");
}

export async function markSyncItemFailed(queueId: string, errorMessage: string): Promise<void> {
  const db = await getDatabase();
  await db.runAsync(
    "UPDATE sync_queue SET status = 'failed', retry_count = retry_count + 1, error_message = ? WHERE id = ?",
    errorMessage,
    queueId
  );
}

export async function getPendingSyncCount(): Promise<number> {
  const db = await getDatabase();
  const result = await db.getFirstAsync<{ count: number }>(
    "SELECT COUNT(*) as count FROM sync_queue WHERE status IN ('queued', 'failed', 'processing')"
  );
  return result?.count || 0;
}
