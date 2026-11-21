import * as SQLite from 'expo-sqlite';

export type LocalTask = {
  local_id: string;
  server_id: number | null;
  title: string;
  description: string | null;
  category: string;
  priority: string;
  status: string;
  due_date: string | null;
  created_at: string;
  sync_state: 'queued' | 'synced';
};

let database: SQLite.SQLiteDatabase | null = null;

export async function getDatabase() {
  if (!database) {
    database = await SQLite.openDatabaseAsync('asta-mobile.db');
    await database.execAsync(`
      PRAGMA journal_mode = WAL;
      CREATE TABLE IF NOT EXISTS mobile_tasks (
        local_id TEXT PRIMARY KEY NOT NULL,
        server_id INTEGER UNIQUE,
        title TEXT NOT NULL,
        description TEXT,
        category TEXT NOT NULL,
        priority TEXT NOT NULL,
        status TEXT NOT NULL,
        due_date TEXT,
        created_at TEXT NOT NULL,
        sync_state TEXT NOT NULL CHECK(sync_state IN ('queued', 'synced'))
      );
      CREATE INDEX IF NOT EXISTS mobile_tasks_created_at
        ON mobile_tasks(created_at DESC);
    `);
  }
  return database;
}

export async function readTasks(): Promise<LocalTask[]> {
  const db = await getDatabase();
  return db.getAllAsync<LocalTask>(
    'SELECT * FROM mobile_tasks ORDER BY created_at DESC'
  );
}
