export type MemoryCategory =
  | 'Preferences'
  | 'System Profile'
  | 'Coding Preferences'
  | 'Habits'
  | 'Project Context';

export interface MemoryRecord {
  id: string;
  backendId?: number;
  key: string;
  value: string;
  category: MemoryCategory;
  tag: string;
  confidence: number; // 0 to 1
  createdAt: string;
  updatedAt: string;
  source: 'user_explicit' | 'assistant_extracted' | 'system_default';
  relevanceScore?: number;
}

export interface PendingMemoryItem {
  id: string;
  backendId?: number;
  suggestedKey: string;
  suggestedValue: string;
  category: MemoryCategory;
  reason: string;
  timestamp: string;
  confidence: number;
}
