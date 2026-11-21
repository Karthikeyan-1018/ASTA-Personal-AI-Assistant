import { getDatabase } from './db';
import { LocalNote, CreateNoteInput } from '../types/notes';

interface RawNoteRow {
  id: string;
  title: string;
  content: string;
  category: string;
  is_pinned: number;
  created_at: string;
  updated_at: string;
}

function parseNoteRow(row: RawNoteRow): LocalNote {
  return {
    id: row.id,
    title: row.title,
    content: row.content,
    category: row.category,
    is_pinned: Boolean(row.is_pinned),
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

export async function getAllNotes(): Promise<LocalNote[]> {
  const db = await getDatabase();
  const rows = await db.getAllAsync<RawNoteRow>(
    'SELECT * FROM mobile_notes ORDER BY is_pinned DESC, updated_at DESC'
  );
  return rows.map(parseNoteRow);
}

export async function createNote(input: CreateNoteInput): Promise<LocalNote> {
  const db = await getDatabase();
  const now = new Date().toISOString();
  const id = `note-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  const category = input.category || 'general';
  const isPinned = input.is_pinned ? 1 : 0;

  await db.runAsync(
    `INSERT INTO mobile_notes (id, title, content, category, is_pinned, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    id,
    input.title.trim(),
    input.content.trim(),
    category,
    isPinned,
    now,
    now
  );

  return {
    id,
    title: input.title.trim(),
    content: input.content.trim(),
    category,
    is_pinned: Boolean(input.is_pinned),
    created_at: now,
    updated_at: now,
  };
}

export async function updateNote(
  id: string,
  updates: { title?: string; content?: string; category?: string; is_pinned?: boolean }
): Promise<LocalNote | null> {
  const db = await getDatabase();
  const existingRow = await db.getFirstAsync<RawNoteRow>(
    'SELECT * FROM mobile_notes WHERE id = ?',
    [id]
  );
  if (!existingRow) return null;

  const now = new Date().toISOString();
  const title = updates.title !== undefined ? updates.title.trim() : existingRow.title;
  const content = updates.content !== undefined ? updates.content.trim() : existingRow.content;
  const category = updates.category !== undefined ? updates.category : existingRow.category;
  const is_pinned = updates.is_pinned !== undefined ? (updates.is_pinned ? 1 : 0) : existingRow.is_pinned;

  await db.runAsync(
    `UPDATE mobile_notes SET title = ?, content = ?, category = ?, is_pinned = ?, updated_at = ? WHERE id = ?`,
    title,
    content,
    category,
    is_pinned,
    now,
    id
  );

  return {
    id,
    title,
    content,
    category,
    is_pinned: Boolean(is_pinned),
    created_at: existingRow.created_at,
    updated_at: now,
  };
}

export async function deleteNote(id: string): Promise<void> {
  const db = await getDatabase();
  await db.runAsync('DELETE FROM mobile_notes WHERE id = ?', [id]);
}

export async function togglePinNote(id: string): Promise<void> {
  const db = await getDatabase();
  await db.runAsync('UPDATE mobile_notes SET is_pinned = CASE WHEN is_pinned = 1 THEN 0 ELSE 1 END WHERE id = ?', [id]);
}
