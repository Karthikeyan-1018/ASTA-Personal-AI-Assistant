import { getDatabase } from './db';
import { FocusSession, FocusSessionType } from '../types/focus';

interface RawFocusRow {
  id: string;
  task_id: string | null;
  task_title: string | null;
  duration_minutes: number;
  completed_at: string;
  session_type: string;
}

export async function recordFocusSession(
  durationMinutes: number,
  sessionType: FocusSessionType = 'pomodoro',
  taskId?: string | null,
  taskTitle?: string | null
): Promise<FocusSession> {
  const db = await getDatabase();
  const now = new Date().toISOString();
  const id = `focus-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

  await db.runAsync(
    `INSERT INTO focus_sessions (id, task_id, task_title, duration_minutes, completed_at, session_type)
     VALUES (?, ?, ?, ?, ?, ?)`,
    id,
    taskId || null,
    taskTitle || null,
    durationMinutes,
    now,
    sessionType
  );

  return {
    id,
    task_id: taskId || null,
    task_title: taskTitle || null,
    duration_minutes: durationMinutes,
    completed_at: now,
    session_type: sessionType,
  };
}

export async function getRecentFocusSessions(limit = 20): Promise<FocusSession[]> {
  const db = await getDatabase();
  const rows = await db.getAllAsync<RawFocusRow>(
    'SELECT * FROM focus_sessions ORDER BY completed_at DESC LIMIT ?',
    [limit]
  );
  return rows.map((r) => ({
    id: r.id,
    task_id: r.task_id,
    task_title: r.task_title,
    duration_minutes: r.duration_minutes,
    completed_at: r.completed_at,
    session_type: r.session_type as FocusSessionType,
  }));
}

export async function getFocusStats(): Promise<{
  totalMinutes: number;
  totalSessions: number;
  todayMinutes: number;
}> {
  const db = await getDatabase();
  const todayPrefix = new Date().toISOString().slice(0, 10);

  const total = await db.getFirstAsync<{ sum_min: number | null; count: number }>(
    'SELECT SUM(duration_minutes) as sum_min, COUNT(*) as count FROM focus_sessions'
  );

  const today = await db.getFirstAsync<{ sum_min: number | null }>(
    'SELECT SUM(duration_minutes) as sum_min FROM focus_sessions WHERE completed_at LIKE ?',
    [`${todayPrefix}%`]
  );

  return {
    totalMinutes: total?.sum_min || 0,
    totalSessions: total?.count || 0,
    todayMinutes: today?.sum_min || 0,
  };
}
