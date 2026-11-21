import * as SQLite from 'expo-sqlite';
import { LocalTask, TaskCategory, TaskPriority, TaskStatus, SyncState, SubTask } from '../types/task';
import { SyncQueueItem, SyncOperation, SyncEntityType } from '../types/sync';
import { LocalNote } from '../types/notes';
import { FocusSession } from '../types/focus';

let dbInstance: SQLite.SQLiteDatabase | null = null;

export async function getDatabase(): Promise<SQLite.SQLiteDatabase> {
  if (dbInstance) {
    return dbInstance;
  }

  const db = await SQLite.openDatabaseAsync('asta-mobile.db');
  await db.execAsync('PRAGMA journal_mode = WAL;');

  // 1. Create mobile_tasks table with rich schema
  await db.execAsync(`
    CREATE TABLE IF NOT EXISTS mobile_tasks (
      local_id TEXT PRIMARY KEY NOT NULL,
      server_id INTEGER UNIQUE,
      title TEXT NOT NULL,
      description TEXT,
      category TEXT NOT NULL DEFAULT 'other',
      priority TEXT NOT NULL DEFAULT 'medium',
      status TEXT NOT NULL DEFAULT 'pending',
      is_completed INTEGER NOT NULL DEFAULT 0,
      due_date TEXT,
      due_time TEXT,
      timezone TEXT,
      tags_json TEXT DEFAULT '[]',
      subtasks_json TEXT DEFAULT '[]',
      notes TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      completed_at TEXT,
      sync_state TEXT NOT NULL DEFAULT 'pending_sync',
      sync_error TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_mobile_tasks_created ON mobile_tasks(created_at DESC);
    CREATE INDEX IF NOT EXISTS ix_mobile_tasks_status ON mobile_tasks(status);
    CREATE INDEX IF NOT EXISTS ix_mobile_tasks_due ON mobile_tasks(due_date);
    CREATE INDEX IF NOT EXISTS ix_mobile_tasks_sync ON mobile_tasks(sync_state);
  `);

  // Migrate columns in case an older mobile_tasks schema is present
  const tableInfo = await db.getAllAsync<{ name: string }>('PRAGMA table_info(mobile_tasks)');
  const existingCols = new Set(tableInfo.map((col) => col.name));

  if (!existingCols.has('is_completed')) {
    await db.execAsync('ALTER TABLE mobile_tasks ADD COLUMN is_completed INTEGER NOT NULL DEFAULT 0;');
  }
  if (!existingCols.has('due_time')) {
    await db.execAsync('ALTER TABLE mobile_tasks ADD COLUMN due_time TEXT;');
  }
  if (!existingCols.has('timezone')) {
    await db.execAsync('ALTER TABLE mobile_tasks ADD COLUMN timezone TEXT;');
  }
  if (!existingCols.has('tags_json')) {
    await db.execAsync("ALTER TABLE mobile_tasks ADD COLUMN tags_json TEXT DEFAULT '[]';");
  }
  if (!existingCols.has('subtasks_json')) {
    await db.execAsync("ALTER TABLE mobile_tasks ADD COLUMN subtasks_json TEXT DEFAULT '[]';");
  }
  if (!existingCols.has('notes')) {
    await db.execAsync('ALTER TABLE mobile_tasks ADD COLUMN notes TEXT;');
  }
  if (!existingCols.has('updated_at')) {
    await db.execAsync('ALTER TABLE mobile_tasks ADD COLUMN updated_at TEXT;');
    await db.execAsync('UPDATE mobile_tasks SET updated_at = created_at WHERE updated_at IS NULL;');
  }
  if (!existingCols.has('completed_at')) {
    await db.execAsync('ALTER TABLE mobile_tasks ADD COLUMN completed_at TEXT;');
  }
  if (!existingCols.has('sync_error')) {
    await db.execAsync('ALTER TABLE mobile_tasks ADD COLUMN sync_error TEXT;');
  }

  // Older mobile builds constrained sync_state to ('queued', 'synced').
  // Rebuild that legacy table so the current task repository can persist
  // pending_sync and sync_failed states without discarding existing tasks.
  const taskTableDefinition = await db.getFirstAsync<{ sql: string | null }>(
    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'mobile_tasks'"
  );
  const legacySyncStateConstraint =
    taskTableDefinition?.sql &&
    /CHECK\s*\(\s*sync_state\b/i.test(taskTableDefinition.sql) &&
    !/['\"]pending_sync['\"]/i.test(taskTableDefinition.sql);

  if (legacySyncStateConstraint) {
    await db.withTransactionAsync(async () => {
      await db.execAsync('ALTER TABLE mobile_tasks RENAME TO mobile_tasks_legacy;');
      await db.execAsync(`
        CREATE TABLE mobile_tasks (
          local_id TEXT PRIMARY KEY NOT NULL,
          server_id INTEGER UNIQUE,
          title TEXT NOT NULL,
          description TEXT,
          category TEXT NOT NULL DEFAULT 'other',
          priority TEXT NOT NULL DEFAULT 'medium',
          status TEXT NOT NULL DEFAULT 'pending',
          is_completed INTEGER NOT NULL DEFAULT 0,
          due_date TEXT,
          due_time TEXT,
          timezone TEXT,
          tags_json TEXT DEFAULT '[]',
          subtasks_json TEXT DEFAULT '[]',
          notes TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          completed_at TEXT,
          sync_state TEXT NOT NULL DEFAULT 'pending_sync',
          sync_error TEXT
        );
        INSERT INTO mobile_tasks (
          local_id, server_id, title, description, category, priority, status,
          is_completed, due_date, due_time, timezone, tags_json, subtasks_json,
          notes, created_at, updated_at, completed_at, sync_state, sync_error
        )
        SELECT
          local_id, server_id, title, description, category, priority, status,
          is_completed, due_date, due_time, timezone, tags_json, subtasks_json,
          notes, created_at, updated_at, completed_at,
          CASE
            WHEN sync_state = 'synced' THEN 'synced'
            WHEN sync_state = 'local_only' THEN 'local_only'
            WHEN sync_state = 'sync_failed' THEN 'sync_failed'
            ELSE 'pending_sync'
          END,
          sync_error
        FROM mobile_tasks_legacy;
        DROP TABLE mobile_tasks_legacy;
        CREATE INDEX IF NOT EXISTS ix_mobile_tasks_created ON mobile_tasks(created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_mobile_tasks_status ON mobile_tasks(status);
        CREATE INDEX IF NOT EXISTS ix_mobile_tasks_due ON mobile_tasks(due_date);
        CREATE INDEX IF NOT EXISTS ix_mobile_tasks_sync ON mobile_tasks(sync_state);
      `);
    });
  }

  // 2. Create sync_queue table
  await db.execAsync(`
    CREATE TABLE IF NOT EXISTS sync_queue (
      id TEXT PRIMARY KEY NOT NULL,
      entity_type TEXT NOT NULL,
      entity_id TEXT NOT NULL,
      operation TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      retry_count INTEGER NOT NULL DEFAULT 0,
      last_attempt TEXT,
      status TEXT NOT NULL DEFAULT 'queued',
      error_message TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_sync_queue_status ON sync_queue(status, created_at ASC);
  `);

  await db.runAsync("UPDATE sync_queue SET status = 'queued' WHERE status = 'processing'");

  // 3. Create mobile_notes table
  await db.execAsync(`
    CREATE TABLE IF NOT EXISTS mobile_notes (
      id TEXT PRIMARY KEY NOT NULL,
      title TEXT NOT NULL,
      content TEXT NOT NULL,
      category TEXT NOT NULL DEFAULT 'general',
      is_pinned INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_mobile_notes_pinned ON mobile_notes(is_pinned DESC, updated_at DESC);
  `);

  // 4. Create focus_sessions table
  await db.execAsync(`
    CREATE TABLE IF NOT EXISTS focus_sessions (
      id TEXT PRIMARY KEY NOT NULL,
      task_id TEXT,
      task_title TEXT,
      duration_minutes INTEGER NOT NULL,
      completed_at TEXT NOT NULL,
      session_type TEXT NOT NULL DEFAULT 'pomodoro'
    );
    CREATE INDEX IF NOT EXISTS ix_focus_sessions_completed ON focus_sessions(completed_at DESC);
  `);

  dbInstance = db;
  return db;
}
