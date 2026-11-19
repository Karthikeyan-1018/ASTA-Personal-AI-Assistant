import { create } from 'zustand';
import type {
  MemoryRecord,
  PendingMemoryItem,
} from '../types/memory.types';
import { normaliseMemoryCategory } from '../types/memory.types';
import type {
  MemoryCreate,
  MemoryResponse,
  MemoryUpdate,
} from '../types/api.types';
import { memoryApi } from '../services/api/memory.api';

interface MemoryStoreState {
  memories: MemoryRecord[];
  pendingMemories: PendingMemoryItem[];
  searchResults: MemoryRecord[] | null;
  isLoading: boolean;
  isSearching: boolean;
  error: string | null;
  notice: string | null;
  isBackendConnected: boolean;
  fetchMemories: () => Promise<void>;
  searchMemories: (query: string, category?: string) => Promise<void>;
  clearSearch: () => void;
  addMemory: (payload: MemoryCreate) => Promise<void>;
  updateMemory: (id: number, updates: MemoryUpdate) => Promise<void>;
  deleteMemory: (id: number) => Promise<void>;
  approvePendingMemory: (id: number) => Promise<void>;
  rejectPendingMemory: (id: number) => Promise<void>;
  clearNotice: () => void;
}

function errorMessage(error: unknown, fallback: string): string {
  const candidate = error as {
    response?: { data?: { detail?: unknown } };
    message?: unknown;
  } | null;

  const detail = candidate?.response?.data?.detail;

  if (typeof detail === 'string' && detail.trim()) return detail;
  if (typeof candidate?.message === 'string' && candidate.message.trim()) {
    return candidate.message;
  }

  return fallback;
}

function toMemoryRecord(item: MemoryResponse): MemoryRecord {
  return {
    id: item.id,
    content: item.content,
    category: normaliseMemoryCategory(item.category),
    status: item.status,
    created_at: item.created_at || '',
    updated_at: item.updated_at || '',
  };
}

export const useMemoryStore = create<MemoryStoreState>((set, get) => ({
  memories: [],
  pendingMemories: [],
  searchResults: null,
  isLoading: false,
  isSearching: false,
  error: null,
  notice: null,
  isBackendConnected: false,

  fetchMemories: async () => {
    set({ isLoading: true, error: null });

    try {
      const data = await memoryApi.list();

      if (!Array.isArray(data)) {
        throw new Error('The memory service returned an invalid response.');
      }

      set({
        memories: data
          .filter((item) => item.status === 'approved')
          .map(toMemoryRecord),
        pendingMemories: data
          .filter((item) => item.status === 'pending')
          .map(toMemoryRecord),
        isBackendConnected: true,
        isLoading: false,
      });
    } catch (error) {
      set({
        isBackendConnected: false,
        isLoading: false,
        error: errorMessage(error, 'Could not load memories.'),
      });
    }
  },

  searchMemories: async (query, category) => {
    const cleaned = query.trim();

    if (cleaned.length < 2) {
      set({ searchResults: null, isSearching: false });
      return;
    }

    set({ isSearching: true, error: null });

    try {
      const response = await memoryApi.retrieve({
        query: cleaned,
        category: category && category !== 'All' ? category : undefined,
        limit: 10,
        mode: 'hybrid',
        allow_keyword_fallback: true,
      });

      const results: MemoryRecord[] = (response.memories || []).flatMap(
        (item) => {
          if (typeof item.id !== 'number') return [];

          return [{
            id: item.id,
            content: item.content,
            category: normaliseMemoryCategory(item.category),
            status: 'approved' as const,
            created_at: '',
            updated_at: '',
          }];
        }
      );

      set({ searchResults: results, isSearching: false });
    } catch (error) {
      set({
        searchResults: null,
        isSearching: false,
        error: errorMessage(error, 'Could not search memories.'),
      });
    }
  },

  clearSearch: () => {
    set({ searchResults: null, isSearching: false });
  },

  addMemory: async (payload) => {
    set({ isLoading: true, error: null, notice: null });

    try {
      const result = await memoryApi.create({
        content: payload.content.trim(),
        category: normaliseMemoryCategory(payload.category || 'general'),
      });

      await get().fetchMemories();

      if (result.deduplicated) {
        set({ notice: 'A matching memory already exists; no duplicate was created.' });
      } else if (result.superseded) {
        set({ notice: 'The existing memory was updated with the newer value.' });
      } else {
        set({ notice: 'Memory saved for owner review.' });
      }
    } catch (error) {
      set({
        error: errorMessage(error, 'Could not save the memory.'),
        isLoading: false,
      });
    }
  },

  updateMemory: async (id, updates) => {
    set({ isLoading: true, error: null, notice: null });

    try {
      await memoryApi.update(id, updates);
      await get().fetchMemories();
      set({ notice: 'Memory updated.' });
    } catch (error) {
      set({
        error: errorMessage(error, 'Could not update the memory.'),
        isLoading: false,
      });
    }
  },

  deleteMemory: async (id) => {
    set({ isLoading: true, error: null, notice: null });

    try {
      await memoryApi.delete(id);
      await get().fetchMemories();
      set({ notice: 'Memory deleted.' });
    } catch (error) {
      set({
        error: errorMessage(error, 'Could not delete the memory.'),
        isLoading: false,
      });
    }
  },

  approvePendingMemory: async (id) => {
    set({ isLoading: true, error: null, notice: null });

    try {
      await memoryApi.approve(id);
      await get().fetchMemories();
      set({ notice: 'Memory approved.' });
    } catch (error) {
      set({
        error: errorMessage(error, 'Could not approve the memory.'),
        isLoading: false,
      });
    }
  },

  rejectPendingMemory: async (id) => {
    set({ isLoading: true, error: null, notice: null });

    try {
      await memoryApi.reject(id);
      await get().fetchMemories();
      set({ notice: 'Memory rejected.' });
    } catch (error) {
      set({
        error: errorMessage(error, 'Could not reject the memory.'),
        isLoading: false,
      });
    }
  },

  clearNotice: () => {
    set({ notice: null });
  },
}));
