import { create } from 'zustand';
import type { MemoryRecord, PendingMemoryItem, MemoryCategory } from '../types/memory.types';
import type { MemoryResponse } from '../types/api.types';
import { memoryApi } from '../services/api/memory.api';

const INITIAL_MEMORIES: MemoryRecord[] = [
  {
    id: 'mem-1',
    key: 'Core Assistant Persona',
    value: 'ASTA acts as an expert desktop co-pilot, concise, proactive, with strict local sandbox validation.',
    category: 'System Profile',
    tag: 'Identity',
    confidence: 1.0,
    createdAt: '2026-11-10T12:00:00Z',
    updatedAt: '2026-11-14T08:00:00Z',
    source: 'system_default',
  },
  {
    id: 'mem-2',
    key: 'Coding Style Preference',
    value: 'TypeScript strict mode, React 19 functional components with hooks, Tailwind utility styling, and explicit error handling.',
    category: 'Coding Preferences',
    tag: 'Frontend',
    confidence: 0.98,
    createdAt: '2026-11-11T14:30:00Z',
    updatedAt: '2026-11-14T09:20:00Z',
    source: 'user_explicit',
  },
  {
    id: 'mem-3',
    key: 'Backend Architecture',
    value: 'FastAPI with PostgreSQL 18, SQLAlchemy Async, pgvector semantic search, and hybrid routing between Groq and local Ollama.',
    category: 'Project Context',
    tag: 'Backend',
    confidence: 0.95,
    createdAt: '2026-11-12T10:00:00Z',
    updatedAt: '2026-11-13T16:45:00Z',
    source: 'user_explicit',
  },
  {
    id: 'mem-4',
    key: 'Tool Execution Policy',
    value: 'Requires interactive user confirmation for any elevated or modifying shell / filesystem commands.',
    category: 'Preferences',
    tag: 'Security',
    confidence: 1.0,
    createdAt: '2026-11-13T11:00:00Z',
    updatedAt: '2026-11-14T11:00:00Z',
    source: 'system_default',
  },
  {
    id: 'mem-5',
    key: 'Working Hours & Focus Mode',
    value: 'Typical active coding hours are 09:00 - 22:00. Prefers minimal decorative animations during focused work.',
    category: 'Habits',
    tag: 'Schedule',
    confidence: 0.88,
    createdAt: '2026-11-14T07:15:00Z',
    updatedAt: '2026-11-14T07:15:00Z',
    source: 'assistant_extracted',
  },
];

const INITIAL_PENDING_MEMORIES: PendingMemoryItem[] = [
  {
    id: 'pending-1',
    suggestedKey: 'Default Terminal Shell',
    suggestedValue: 'User preferred PowerShell 7 over standard Command Prompt for automated background tasks.',
    category: 'Preferences',
    reason: 'Observed repeated invocations using PowerShell modules in recent development turns.',
    timestamp: '2026-11-14T16:30:00Z',
    confidence: 0.87,
  },
  {
    id: 'pending-2',
    suggestedKey: 'Documentation Format',
    suggestedValue: 'Prefers Mermaid diagrams for sequence flows and Markdown tables for API contracts.',
    category: 'Coding Preferences',
    reason: 'Extracted from user preference during Phase 13 backend review.',
    timestamp: '2026-11-14T17:15:00Z',
    confidence: 0.92,
  },
];

const VALID_CATEGORIES: MemoryCategory[] = [
  'Preferences',
  'System Profile',
  'Coding Preferences',
  'Habits',
  'Project Context',
];

function sanitizeCategory(cat?: string): MemoryCategory {
  if (!cat) return 'Preferences';
  const found = VALID_CATEGORIES.find(
    (c) => c.toLowerCase() === cat.toLowerCase()
  );
  return found || 'Preferences';
}

function parseMemoryContent(content: string): { key: string; value: string } {
  const splitIdx = content.indexOf(': ');
  if (splitIdx > 0) {
    return {
      key: content.slice(0, splitIdx).trim(),
      value: content.slice(splitIdx + 2).trim(),
    };
  }
  return {
    key: 'Note',
    value: content.trim(),
  };
}

function mapBackendRecord(item: MemoryResponse): MemoryRecord {
  const { key, value } = parseMemoryContent(item.content);
  return {
    id: `mem-backend-${item.id}`,
    backendId: item.id,
    key,
    value,
    category: sanitizeCategory(item.category),
    tag: item.category || 'General',
    confidence: 1.0,
    createdAt: item.created_at || new Date().toISOString(),
    updatedAt: item.updated_at || new Date().toISOString(),
    source: 'user_explicit',
  };
}

function mapBackendPending(item: MemoryResponse): PendingMemoryItem {
  const { key, value } = parseMemoryContent(item.content);
  return {
    id: `pending-backend-${item.id}`,
    backendId: item.id,
    suggestedKey: key,
    suggestedValue: value,
    category: sanitizeCategory(item.category),
    reason: 'Extracted by assistant and pending owner confirmation',
    timestamp: item.created_at || new Date().toISOString(),
    confidence: 0.9,
  };
}

interface MemoryStoreState {
  memories: MemoryRecord[];
  pendingMemories: PendingMemoryItem[];
  searchResults: MemoryRecord[] | null;
  isLoading: boolean;
  isSearching: boolean;
  error: string | null;
  isBackendConnected: boolean;
  fetchMemories: () => Promise<void>;
  searchMemories: (query: string, category?: string) => Promise<void>;
  clearSearch: () => void;
  addMemory: (memory: Omit<MemoryRecord, 'id' | 'createdAt' | 'updatedAt'>) => Promise<void>;
  updateMemory: (id: string, updates: Partial<MemoryRecord>) => Promise<void>;
  deleteMemory: (id: string) => Promise<void>;
  approvePendingMemory: (id: string) => Promise<void>;
  rejectPendingMemory: (id: string) => Promise<void>;
}

export const useMemoryStore = create<MemoryStoreState>((set, get) => ({
  memories: INITIAL_MEMORIES,
  pendingMemories: INITIAL_PENDING_MEMORIES,
  searchResults: null,
  isLoading: false,
  isSearching: false,
  error: null,
  isBackendConnected: false,

  fetchMemories: async () => {
    set({ isLoading: true, error: null });
    try {
      const data = await memoryApi.list();
      if (Array.isArray(data)) {
        const approved: MemoryRecord[] = [];
        const pending: PendingMemoryItem[] = [];

        for (const item of data) {
          if (item.status === 'pending') {
            pending.push(mapBackendPending(item));
          } else if (item.status === 'approved') {
            approved.push(mapBackendRecord(item));
          }
        }

        if (approved.length > 0 || pending.length > 0) {
          set({
            memories: approved,
            pendingMemories: pending,
            isBackendConnected: true,
            isLoading: false,
          });
        } else {
          set({ isBackendConnected: true, isLoading: false });
        }
      }
    } catch (err: any) {
      set({
        isLoading: false,
        isBackendConnected: false,
        error: err?.message || 'Could not connect to memory service',
      });
    }
  },

  searchMemories: async (query, category) => {
    if (!query.trim()) {
      set({ searchResults: null, isSearching: false });
      return;
    }

    set({ isSearching: true });
    try {
      const catParam = category && category !== 'All' ? category : undefined;
      const res = await memoryApi.retrieve({
        query: query.trim(),
        category: catParam,
        mode: 'semantic',
        limit: 10,
      });

      if (res && res.memories && res.memories.length > 0) {
        const mapped: MemoryRecord[] = res.memories.map((m, idx) => {
          const { key, value } = parseMemoryContent(m.content);
          return {
            id: `search-res-${idx}`,
            key,
            value,
            category: sanitizeCategory(m.category),
            tag: 'Semantic Match',
            confidence: m.relevance_score,
            relevanceScore: m.relevance_score,
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
            source: 'assistant_extracted',
          };
        });
        set({ searchResults: mapped, isSearching: false });
      } else {
        set({ searchResults: [], isSearching: false });
      }
    } catch {
      // Backend semantic search unavailable, clear search results so UI falls back to local filter
      set({ searchResults: null, isSearching: false });
    }
  },

  clearSearch: () => {
    set({ searchResults: null, isSearching: false });
  },

  addMemory: async (data) => {
    const tempId = `mem-${Date.now()}`;
    const now = new Date().toISOString();
    const optimistic: MemoryRecord = {
      ...data,
      id: tempId,
      createdAt: now,
      updatedAt: now,
    };

    set((state) => ({
      memories: [optimistic, ...state.memories],
    }));

    try {
      const content = `${data.key}: ${data.value}`;
      const res = await memoryApi.create({
        content,
        category: data.category.toLowerCase(),
      });

      set((state) => ({
        memories: state.memories.map((m) =>
          m.id === tempId
            ? {
                ...m,
                id: `mem-backend-${res.id}`,
                backendId: res.id,
              }
            : m
        ),
      }));
    } catch {
      // Retain optimistic memory entry
    }
  },

  updateMemory: async (id, updates) => {
    const current = get().memories.find((m) => m.id === id);
    set((state) => ({
      memories: state.memories.map((m) =>
        m.id === id ? { ...m, ...updates, updatedAt: new Date().toISOString() } : m
      ),
    }));

    if (current?.backendId) {
      try {
        const nextKey = updates.key || current.key;
        const nextVal = updates.value || current.value;
        const nextCat = updates.category || current.category;
        await memoryApi.update(current.backendId, {
          content: `${nextKey}: ${nextVal}`,
          category: nextCat.toLowerCase(),
        });
      } catch {
        // Handled
      }
    }
  },

  deleteMemory: async (id) => {
    const target = get().memories.find((m) => m.id === id);
    set((state) => ({
      memories: state.memories.filter((m) => m.id !== id),
    }));

    if (target?.backendId) {
      try {
        await memoryApi.delete(target.backendId);
      } catch {
        // Handled
      }
    }
  },

  approvePendingMemory: async (id) => {
    const target = get().pendingMemories.find((p) => p.id === id);
    if (!target) return;

    const now = new Date().toISOString();
    const newMemory: MemoryRecord = {
      id: target.backendId ? `mem-backend-${target.backendId}` : `mem-${Date.now()}`,
      backendId: target.backendId,
      key: target.suggestedKey,
      value: target.suggestedValue,
      category: target.category,
      tag: 'Approved',
      confidence: target.confidence,
      createdAt: now,
      updatedAt: now,
      source: 'assistant_extracted',
    };

    set((state) => ({
      memories: [newMemory, ...state.memories],
      pendingMemories: state.pendingMemories.filter((p) => p.id !== id),
    }));

    if (target.backendId) {
      try {
        await memoryApi.approve(target.backendId);
      } catch {
        // Handled
      }
    }
  },

  rejectPendingMemory: async (id) => {
    const target = get().pendingMemories.find((p) => p.id === id);
    set((state) => ({
      pendingMemories: state.pendingMemories.filter((p) => p.id !== id),
    }));

    if (target?.backendId) {
      try {
        await memoryApi.reject(target.backendId);
      } catch {
        // Handled
      }
    }
  },
}));
