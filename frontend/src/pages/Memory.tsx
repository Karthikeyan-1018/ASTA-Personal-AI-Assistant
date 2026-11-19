import React, { useEffect, useState } from 'react';
import {
  AlertCircle,
  Brain,
  Check,
  Edit2,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { useMemoryStore } from '../stores/memory.store';
import type { MemoryRecord } from '../types/memory.types';
import {
  MEMORY_CATEGORIES,
  memoryCategoryLabel,
  normaliseMemoryCategory,
} from '../types/memory.types';

function formatTimestamp(value: string | undefined): string {
  if (!value) return '—';

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;

  return parsed.toLocaleString();
}

export const Memory: React.FC = () => {
  const memories = useMemoryStore((s) => s.memories);
  const pendingMemories = useMemoryStore((s) => s.pendingMemories);
  const searchResults = useMemoryStore((s) => s.searchResults);
  const isSearching = useMemoryStore((s) => s.isSearching);
  const isLoading = useMemoryStore((s) => s.isLoading);
  const error = useMemoryStore((s) => s.error);
  const notice = useMemoryStore((s) => s.notice);
  const isBackendConnected = useMemoryStore((s) => s.isBackendConnected);
  const fetchMemories = useMemoryStore((s) => s.fetchMemories);
  const addMemory = useMemoryStore((s) => s.addMemory);
  const updateMemory = useMemoryStore((s) => s.updateMemory);
  const deleteMemory = useMemoryStore((s) => s.deleteMemory);
  const approvePendingMemory = useMemoryStore((s) => s.approvePendingMemory);
  const rejectPendingMemory = useMemoryStore((s) => s.rejectPendingMemory);
  const searchMemories = useMemoryStore((s) => s.searchMemories);
  const clearSearch = useMemoryStore((s) => s.clearSearch);
  const clearNotice = useMemoryStore((s) => s.clearNotice);

  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('All');
  const [modalOpen, setModalOpen] = useState(false);
  const [editingMemory, setEditingMemory] = useState<MemoryRecord | null>(null);
  const [formContent, setFormContent] = useState('');
  const [formCategory, setFormCategory] = useState('general');

  useEffect(() => {
    const timer = setTimeout(() => {
      if (searchQuery.trim().length >= 2) {
        void searchMemories(searchQuery, selectedCategory);
      } else {
        clearSearch();
      }
    }, 400);

    return () => clearTimeout(timer);
  }, [searchQuery, selectedCategory, searchMemories, clearSearch]);

  const matchesCategory = (category: string) =>
    selectedCategory === 'All' ||
    normaliseMemoryCategory(category) === selectedCategory;

  const visiblePending = pendingMemories.filter((item) =>
    matchesCategory(item.category)
  );

  const visibleMemories =
    searchResults !== null
      ? searchResults
      : memories.filter((item) => matchesCategory(item.category));

  const categoriesInUse = Array.from(
    new Set(
      [...memories, ...pendingMemories].map((item) =>
        normaliseMemoryCategory(item.category)
      )
    )
  );

  const openCreateModal = () => {
    setEditingMemory(null);
    setFormContent('');
    setFormCategory('general');
    setModalOpen(true);
  };

  const openEditModal = (memory: MemoryRecord) => {
    setEditingMemory(memory);
    setFormContent(memory.content);
    setFormCategory(normaliseMemoryCategory(memory.category));
    setModalOpen(true);
  };

  const handleFormSubmit = async (event: React.FormEvent) => {
    event.preventDefault();

    const content = formContent.trim();
    if (!content) return;

    if (editingMemory) {
      await updateMemory(editingMemory.id, {
        content,
        category: normaliseMemoryCategory(formCategory),
      });
    } else {
      await addMemory({
        content,
        category: normaliseMemoryCategory(formCategory),
      });
    }

    setModalOpen(false);
  };

  const clearSearchAndFilter = () => {
    setSearchQuery('');
    setSelectedCategory('All');
    clearSearch();
  };

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
              Long-Term Memory
            </h1>
            <span
              className={`rounded-full px-2.5 py-0.5 text-[10px] font-semibold font-mono ${
                isBackendConnected
                  ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                  : 'bg-slate-500/15 text-slate-300 border border-slate-500/30'
              }`}
            >
              {isBackendConnected ? 'Backend connected' : 'Backend unavailable'}
            </span>
          </div>
          <p className="text-xs sm:text-sm text-slate-400">
            Owner-controlled facts and preferences used across new conversations.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void fetchMemories()}
            disabled={isLoading}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-900 px-3 py-2.5 text-xs font-semibold text-slate-300 transition hover:bg-slate-800 disabled:opacity-50"
            title="Refresh from backend"
          >
            <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
          <button
            type="button"
            onClick={openCreateModal}
            disabled={!isBackendConnected}
            className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2.5 text-xs sm:text-sm font-semibold text-white shadow-lg shadow-violet-600/25 transition hover:bg-violet-500 active:scale-[0.98] disabled:opacity-50"
          >
            <Plus className="h-4 w-4" />
            <span>Add Memory</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-start justify-between gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-xs text-rose-200">
          <div className="flex items-start gap-2">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={() => void fetchMemories()}
            className="shrink-0 font-semibold underline hover:text-white"
          >
            Retry
          </button>
        </div>
      )}

      {notice && (
        <div className="flex items-start justify-between gap-3 rounded-2xl border border-emerald-500/25 bg-emerald-500/10 p-4 text-xs text-emerald-200">
          <span>{notice}</span>
          <button
            type="button"
            onClick={clearNotice}
            className="shrink-0 rounded p-0.5 hover:text-white"
            title="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-slate-400">Approved memories</p>
          <p className="mt-2 text-2xl font-bold text-white">{memories.length}</p>
          <p className="mt-1 text-[11px] text-slate-500">Loaded from backend</p>
        </div>

        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-amber-400">Pending review</p>
          <p className="mt-2 text-2xl font-bold text-white">
            {pendingMemories.length}
          </p>
          <p className="mt-1 text-[11px] text-slate-500">Awaiting approval</p>
        </div>

        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-violet-400">Categories in use</p>
          <p className="mt-2 text-2xl font-bold text-white">
            {categoriesInUse.length}
          </p>
          <p className="mt-1 text-[11px] text-slate-500">Across saved records</p>
        </div>

        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-emerald-400">Short-term context</p>
          <p className="mt-2 text-xl font-bold text-white">Conversation only</p>
          <p className="mt-1 text-[11px] text-slate-500">
            Managed by the chat session
          </p>
        </div>
      </div>

      {visiblePending.length > 0 && (
        <div className="rounded-3xl border border-amber-500/30 bg-amber-500/5 p-5 shadow-xl">
          <div className="flex items-center justify-between border-b border-amber-500/20 pb-3">
            <h2 className="text-sm font-bold text-amber-200 uppercase tracking-wider">
              Pending review ({visiblePending.length})
            </h2>
            <span className="text-[11px] text-amber-400/80">
              Stored but not yet used as long-term context
            </span>
          </div>

          <div className="mt-4 space-y-3">
            {visiblePending.map((item) => (
              <div
                key={item.id}
                className="flex flex-col justify-between gap-3 rounded-2xl border border-amber-500/20 bg-slate-950/70 p-4 sm:flex-row sm:items-center"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                      {memoryCategoryLabel(item.category)}
                    </span>
                    <span className="text-[10px] font-mono text-slate-500">
                      {formatTimestamp(item.created_at)}
                    </span>
                  </div>
                  <p className="mt-2 text-xs text-slate-300 leading-relaxed">
                    {item.content}
                  </p>
                </div>

                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    disabled={isLoading}
                    onClick={() => void approvePendingMemory(item.id)}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-50"
                  >
                    <Check className="h-3.5 w-3.5" />
                    Approve
                  </button>
                  <button
                    type="button"
                    disabled={isLoading}
                    onClick={() => void rejectPendingMemory(item.id)}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:bg-slate-700 hover:text-rose-400 disabled:opacity-50"
                  >
                    <X className="h-3.5 w-3.5" />
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-3 rounded-2xl border border-slate-800/80 bg-slate-900/30 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => setSelectedCategory('All')}
            className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
              selectedCategory === 'All'
                ? 'bg-violet-600 text-white shadow-sm'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
            }`}
          >
            All
          </button>
          {MEMORY_CATEGORIES.map((category) => (
            <button
              key={category}
              type="button"
              onClick={() => setSelectedCategory(category)}
              className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
                selectedCategory === category
                  ? 'bg-violet-600 text-white shadow-sm'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              {memoryCategoryLabel(category)}
            </button>
          ))}
        </div>

        <div className="relative sm:w-64">
          <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Search saved memories..."
            className="h-8 w-full rounded-xl border border-slate-800 bg-slate-950/80 pl-9 pr-8 text-xs text-slate-200 placeholder-slate-500 focus:border-violet-500/50 focus:outline-none"
          />
          {isSearching && (
            <Loader2 className="absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin text-violet-400" />
          )}
        </div>
      </div>

      {searchResults !== null && (
        <div className="flex items-center justify-between rounded-xl border border-violet-500/25 bg-violet-500/10 px-4 py-2.5 text-xs text-violet-300">
          <span>
            Backend retrieval returned <strong>{searchResults.length}</strong>{' '}
            relevant memory record{searchResults.length === 1 ? '' : 's'}.
          </span>
          <button
            type="button"
            onClick={clearSearchAndFilter}
            className="font-medium underline hover:text-white"
          >
            Show all
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {isLoading ? (
          <div className="col-span-full rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-center">
            <Loader2 className="mx-auto h-8 w-8 animate-spin text-violet-500" />
            <p className="mt-3 text-xs text-slate-400">Loading memories from backend...</p>
          </div>
        ) : visibleMemories.length === 0 ? (
          <div className="col-span-full rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-center">
            <Brain className="mx-auto h-12 w-12 text-slate-600" />
            <p className="mt-3 text-sm font-semibold text-slate-300">
              No memory records found
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Add a memory, approve a pending record, or clear the current filter.
            </p>
          </div>
        ) : (
          visibleMemories.map((memory) => (
            <div
              key={memory.id}
              className="flex flex-col justify-between rounded-2xl border border-slate-800/90 bg-slate-900/40 p-5 shadow-lg transition hover:border-violet-500/40 hover:bg-slate-900/70"
            >
              <div>
                <div className="flex items-start justify-between gap-3">
                  <span className="rounded-full bg-violet-500/15 px-2.5 py-0.5 text-[10px] font-semibold text-violet-300 shrink-0">
                    {memoryCategoryLabel(memory.category)}
                  </span>
                  {memory.status && (
                    <span className="text-[10px] font-mono text-slate-500">
                      #{memory.id}
                    </span>
                  )}
                </div>
                <p className="mt-3 text-xs text-slate-300 leading-relaxed">
                  {memory.content}
                </p>
              </div>

              <div className="mt-4 flex items-center justify-between border-t border-slate-800/60 pt-3 text-[11px] text-slate-500">
                <div className="flex flex-col gap-0.5">
                  <span className="font-mono text-[10px]">
                    Updated {formatTimestamp(memory.updated_at)}
                  </span>
                  {searchResults !== null && (
                    <span className="font-mono text-[10px] text-emerald-400">
                      Relevant match
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => openEditModal(memory)}
                    disabled={isLoading}
                    className="rounded p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200 disabled:opacity-50"
                    title="Edit memory"
                  >
                    <Edit2 className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => void deleteMemory(memory.id)}
                    disabled={isLoading}
                    className="rounded p-1.5 text-slate-400 hover:bg-slate-800 hover:text-rose-400 disabled:opacity-50"
                    title="Delete memory"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-lg rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <h2 className="text-base font-bold text-white">
                {editingMemory ? 'Edit Memory' : 'Add Memory'}
              </h2>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={handleFormSubmit} className="mt-4 space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-300">
                  Memory content *
                </label>
                <textarea
                  rows={4}
                  required
                  value={formContent}
                  onChange={(event) => setFormContent(event.target.value)}
                  placeholder="A stable preference, project detail, or recurring requirement..."
                  className="mt-1 w-full rounded-xl border border-slate-800 bg-slate-900 p-3 text-xs text-white placeholder-slate-500 focus:border-violet-500/60 focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300">
                  Category
                </label>
                <select
                  value={formCategory}
                  onChange={(event) => setFormCategory(event.target.value)}
                  className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:outline-none"
                >
                  {MEMORY_CATEGORIES.map((category) => (
                    <option key={category} value={category}>
                      {memoryCategoryLabel(category)}
                    </option>
                  ))}
                </select>
              </div>

              <div className="mt-6 flex justify-end gap-2.5 border-t border-slate-800 pt-4">
                <button
                  type="button"
                  onClick={() => setModalOpen(false)}
                  className="rounded-xl border border-slate-800 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isLoading}
                  className="rounded-xl bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
                >
                  {editingMemory ? 'Save Changes' : 'Save Memory'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default Memory;
