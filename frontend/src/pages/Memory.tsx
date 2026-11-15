import React, { useEffect, useState } from 'react';
import {
  Brain,
  Plus,
  Search,
  Check,
  X,
  Edit2,
  Trash2,
  Sparkles,
  Loader2,
} from 'lucide-react';
import { useMemoryStore } from '../stores/memory.store';
import type { MemoryRecord, MemoryCategory } from '../types/memory.types';

export const Memory: React.FC = () => {
  const memories = useMemoryStore((s) => s.memories);
  const pendingMemories = useMemoryStore((s) => s.pendingMemories);
  const searchResults = useMemoryStore((s) => s.searchResults);
  const isSearching = useMemoryStore((s) => s.isSearching);
  const isLoading = useMemoryStore((s) => s.isLoading);
  const isBackendConnected = useMemoryStore((s) => s.isBackendConnected);
  const addMemory = useMemoryStore((s) => s.addMemory);
  const updateMemory = useMemoryStore((s) => s.updateMemory);
  const deleteMemory = useMemoryStore((s) => s.deleteMemory);
  const approvePendingMemory = useMemoryStore((s) => s.approvePendingMemory);
  const rejectPendingMemory = useMemoryStore((s) => s.rejectPendingMemory);
  const searchMemories = useMemoryStore((s) => s.searchMemories);
  const clearSearch = useMemoryStore((s) => s.clearSearch);

  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<'All' | MemoryCategory>('All');
  const [modalOpen, setModalOpen] = useState(false);
  const [editingMemory, setEditingMemory] = useState<MemoryRecord | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (searchQuery.trim().length >= 2) {
        searchMemories(searchQuery, selectedCategory);
      } else {
        clearSearch();
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [searchQuery, selectedCategory, searchMemories, clearSearch]);

  // Form State
  const [formKey, setFormKey] = useState('');
  const [formValue, setFormValue] = useState('');
  const [formCategory, setFormCategory] = useState<MemoryCategory>('Preferences');
  const [formTag, setFormTag] = useState('');

  const openCreateModal = () => {
    setEditingMemory(null);
    setFormKey('');
    setFormValue('');
    setFormCategory('Preferences');
    setFormTag('');
    setModalOpen(true);
  };

  const openEditModal = (mem: MemoryRecord) => {
    setEditingMemory(mem);
    setFormKey(mem.key);
    setFormValue(mem.value);
    setFormCategory(mem.category);
    setFormTag(mem.tag);
    setModalOpen(true);
  };

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formKey.trim() || !formValue.trim()) return;

    if (editingMemory) {
      updateMemory(editingMemory.id, {
        key: formKey.trim(),
        value: formValue.trim(),
        category: formCategory,
        tag: formTag.trim() || 'General',
      });
    } else {
      addMemory({
        key: formKey.trim(),
        value: formValue.trim(),
        category: formCategory,
        tag: formTag.trim() || 'General',
        confidence: 1.0,
        source: 'user_explicit',
      });
    }

    setModalOpen(false);
  };

  const filteredMemories = memories.filter((m) => {
    if (selectedCategory !== 'All' && m.category !== selectedCategory) return false;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matchKey = m.key.toLowerCase().includes(q);
      const matchVal = m.value.toLowerCase().includes(q);
      const matchTag = m.tag.toLowerCase().includes(q);
      if (!matchKey && !matchVal && !matchTag) return false;
    }
    return true;
  });

  const categories: MemoryCategory[] = [
    'Preferences',
    'System Profile',
    'Coding Preferences',
    'Habits',
    'Project Context',
  ];

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
              Long-Term Semantic Memory
            </h1>
            <span
              className={`rounded-full px-2.5 py-0.5 text-[10px] font-semibold font-mono ${
                isBackendConnected
                  ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                  : 'bg-indigo-500/15 text-indigo-300 border border-indigo-500/25'
              }`}
            >
              {isBackendConnected ? 'pgvector Live' : 'Demo Memory Store'}
            </span>
          </div>
          <p className="text-xs sm:text-sm text-slate-400">
            Persistent user facts, coding styles, persona guidelines, and pending extractions.
          </p>
        </div>

        <button
          type="button"
          onClick={openCreateModal}
          className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2.5 text-xs sm:text-sm font-semibold text-white shadow-lg shadow-violet-600/25 transition hover:bg-violet-500 active:scale-[0.98]"
        >
          <Plus className="h-4 w-4" />
          <span>Add Memory</span>
        </button>
      </div>

      {/* Memory Stats Banner */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-slate-400">Indexed Memories</p>
          <p className="mt-2 text-2xl font-bold text-white">{memories.length}</p>
          <p className="mt-1 text-[11px] text-slate-500 font-mono">pgvector Ready</p>
        </div>

        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-amber-400">Review Queue</p>
          <p className="mt-2 text-2xl font-bold text-white">{pendingMemories.length}</p>
          <p className="mt-1 text-[11px] text-slate-500">Proposed by AI</p>
        </div>

        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-violet-400">Embedding Schema</p>
          <p className="mt-2 text-xl font-bold text-white">384 Dimensions</p>
          <p className="mt-1 text-[11px] text-slate-500 font-mono">all-MiniLM-L6-v2</p>
        </div>

        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs font-medium text-emerald-400">Active Categories</p>
          <p className="mt-2 text-2xl font-bold text-white">{categories.length}</p>
          <p className="mt-1 text-[11px] text-slate-500">Context domains</p>
        </div>
      </div>

      {/* Pending Memories Review Queue */}
      {pendingMemories.length > 0 && (
        <div className="rounded-3xl border border-amber-500/30 bg-amber-500/5 p-5 shadow-xl">
          <div className="flex items-center justify-between pb-3 border-b border-amber-500/20">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-amber-400" />
              <h2 className="text-sm font-bold text-amber-200 uppercase tracking-wider">
                Pending Memory Review Queue ({pendingMemories.length})
              </h2>
            </div>
            <span className="text-[11px] text-amber-400/80">
              Approve or reject context extracted during recent conversations
            </span>
          </div>

          <div className="mt-4 space-y-3">
            {pendingMemories.map((item) => (
              <div
                key={item.id}
                className="flex flex-col justify-between gap-3 rounded-2xl border border-amber-500/20 bg-slate-950/70 p-4 sm:flex-row sm:items-center"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-slate-100">{item.suggestedKey}</span>
                    <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                      {item.category}
                    </span>
                    <span className="text-[10px] font-mono text-slate-400">
                      {Math.round(item.confidence * 100)}% match
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-300 leading-relaxed">
                    {item.suggestedValue}
                  </p>
                  <p className="mt-1.5 text-[11px] text-slate-500 italic">
                    Reason: {item.reason}
                  </p>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => approvePendingMemory(item.id)}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white shadow-md shadow-emerald-600/25 transition hover:bg-emerald-500"
                  >
                    <Check className="h-3.5 w-3.5" />
                    <span>Approve</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => rejectPendingMemory(item.id)}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:bg-slate-700 hover:text-rose-400"
                  >
                    <X className="h-3.5 w-3.5" />
                    <span>Reject</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Search & Category Filter Controls */}
      <div className="flex flex-col gap-3 rounded-2xl border border-slate-800/80 bg-slate-900/30 p-4 sm:flex-row sm:items-center sm:justify-between">
        {/* Category Pills */}
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
          {categories.map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => setSelectedCategory(cat)}
              className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
                selectedCategory === cat
                  ? 'bg-violet-600 text-white shadow-sm'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>

        {/* Search */}
        <div className="relative sm:w-64">
          <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search memory records..."
            className="h-8 w-full rounded-xl border border-slate-800 bg-slate-950/80 pl-9 pr-8 text-xs text-slate-200 placeholder-slate-500 focus:border-violet-500/50 focus:outline-none"
          />
          {isSearching && (
            <Loader2 className="absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin text-violet-400" />
          )}
        </div>
      </div>

      {/* Semantic Search Banner if active */}
      {searchResults !== null && (
        <div className="flex items-center justify-between rounded-xl bg-violet-500/10 border border-violet-500/25 px-4 py-2.5 text-xs text-violet-300">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-400" />
            <span>
              Semantic search retrieved <strong>{searchResults.length}</strong> matching memory record
              {searchResults.length === 1 ? '' : 's'}.
            </span>
          </div>
          <button
            type="button"
            onClick={() => {
              setSearchQuery('');
              clearSearch();
            }}
            className="text-violet-300 hover:text-white font-medium underline"
          >
            Show All
          </button>
        </div>
      )}

      {/* Memory Records Cards */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {isLoading ? (
          <div className="col-span-full rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-center">
            <Loader2 className="mx-auto h-8 w-8 animate-spin text-violet-500" />
            <p className="mt-3 text-xs text-slate-400">Loading vector memories from backend...</p>
          </div>
        ) : (searchResults !== null ? searchResults : filteredMemories).length === 0 ? (
          <div className="col-span-full rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-center">
            <Brain className="mx-auto h-12 w-12 text-slate-600" />
            <p className="mt-3 text-sm font-semibold text-slate-300">No memory records found</p>
            <p className="mt-1 text-xs text-slate-500">
              Add a new memory key-value pair to shape ASTA's long-term understanding.
            </p>
          </div>
        ) : (
          (searchResults !== null ? searchResults : filteredMemories).map((mem) => (
            <div
              key={mem.id}
              className="flex flex-col justify-between rounded-2xl border border-slate-800/90 bg-slate-900/40 p-5 shadow-lg transition hover:border-violet-500/40 hover:bg-slate-900/70"
            >
              <div>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm font-bold text-white truncate">{mem.key}</span>
                  </div>

                  <span className="rounded-full bg-violet-500/15 px-2.5 py-0.5 text-[10px] font-semibold text-violet-300 shrink-0">
                    {mem.category}
                  </span>
                </div>

                <p className="mt-2 text-xs text-slate-300 leading-relaxed">
                  {mem.value}
                </p>
              </div>

              {/* Card Footer: Metadata and Actions */}
              <div className="mt-4 flex items-center justify-between border-t border-slate-800/60 pt-3 text-[11px] text-slate-500">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[10px] text-slate-400">
                    #{mem.tag}
                  </span>
                  <span>·</span>
                  {mem.relevanceScore !== undefined ? (
                    <span className="font-mono text-[10px] text-emerald-400 font-semibold">
                      {Math.round(mem.relevanceScore * 100)}% match
                    </span>
                  ) : (
                    <span className="font-mono text-[10px]">
                      {Math.round(mem.confidence * 100)}% conf
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => openEditModal(mem)}
                    className="rounded p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                    title="Edit"
                  >
                    <Edit2 className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteMemory(mem.id)}
                    className="rounded p-1.5 text-slate-400 hover:bg-slate-800 hover:text-rose-400"
                    title="Delete"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Add / Edit Memory Modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-lg rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <h2 className="text-base font-bold text-white">
                {editingMemory ? 'Edit Memory Record' : 'Add Semantic Memory'}
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
                <label className="block text-xs font-medium text-slate-300">Memory Key / Title *</label>
                <input
                  type="text"
                  required
                  value={formKey}
                  onChange={(e) => setFormKey(e.target.value)}
                  placeholder="e.g. Coding Style Preference"
                  className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-3 text-xs text-white placeholder-slate-500 focus:border-violet-500/60 focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300">Memory Value / Fact *</label>
                <textarea
                  rows={3}
                  required
                  value={formValue}
                  onChange={(e) => setFormValue(e.target.value)}
                  placeholder="e.g. Strict TypeScript mode, React 19 functional components..."
                  className="mt-1 w-full rounded-xl border border-slate-800 bg-slate-900 p-3 text-xs text-white placeholder-slate-500 focus:border-violet-500/60 focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-300">Category</label>
                  <select
                    value={formCategory}
                    onChange={(e) => setFormCategory(e.target.value as MemoryCategory)}
                    className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:outline-none"
                  >
                    {categories.map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-300">Tag</label>
                  <input
                    type="text"
                    value={formTag}
                    onChange={(e) => setFormTag(e.target.value)}
                    placeholder="e.g. Frontend"
                    className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-white placeholder-slate-500 focus:outline-none"
                  />
                </div>
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
                  className="rounded-xl bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-500"
                >
                  {editingMemory ? 'Save Changes' : 'Store Memory'}
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
