export type MemoryCategory = string;

export type MemoryStatus = 'pending' | 'approved' | 'rejected';

/** The backend memory record. There is no client-only memory model. */
export interface MemoryRecord {
  id: number;
  content: string;
  category: string;
  status: MemoryStatus;
  created_at: string;
  updated_at: string;
}

export interface PendingMemoryItem extends MemoryRecord {}

export const MEMORY_CATEGORIES: string[] = [
  'general',
  'personal',
  'preferences',
  'education',
  'career',
  'projects',
  'technical',
  'goals',
];

export function normaliseMemoryCategory(value: string): string {
  const cleaned = value.trim();
  if (!cleaned) return 'general';

  return (
    MEMORY_CATEGORIES.find(
      (category) => category.toLowerCase() === cleaned.toLowerCase()
    ) || cleaned
  );
}

export function memoryCategoryLabel(value: string): string {
  const normalised = normaliseMemoryCategory(value);
  return normalised.charAt(0).toUpperCase() + normalised.slice(1);
}
