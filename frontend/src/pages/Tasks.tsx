import React, { useState } from 'react';
import {
  Plus,
  Search,
  CheckCircle2,
  Circle,
  Trash2,
  Edit2,
  Calendar,
  X,
  CheckSquare,
  BarChart3,
} from 'lucide-react';
import { useTaskStore } from '../stores/task.store';
import type { TaskItem, TaskPriority, TaskCategory, TaskStatus } from '../types/task.types';

export const Tasks: React.FC = () => {
  const tasks = useTaskStore((s) => s.tasks);
  const filter = useTaskStore((s) => s.filter);
  const setFilter = useTaskStore((s) => s.setFilter);
  const resetFilter = useTaskStore((s) => s.resetFilter);
  const addTask = useTaskStore((s) => s.addTask);
  const updateTask = useTaskStore((s) => s.updateTask);
  const deleteTask = useTaskStore((s) => s.deleteTask);
  const toggleTaskCompleted = useTaskStore((s) => s.toggleTaskCompleted);
  const clearCompleted = useTaskStore((s) => s.clearCompleted);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<TaskItem | null>(null);

  // Form state
  const [formTitle, setFormTitle] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formCategory, setFormCategory] = useState<TaskCategory>('General');
  const [formPriority, setFormPriority] = useState<TaskPriority>('medium');
  const [formStatus, setFormStatus] = useState<TaskStatus>('pending');
  const [formDueDate, setFormDueDate] = useState('');

  const openCreateModal = () => {
    setEditingTask(null);
    setFormTitle('');
    setFormDesc('');
    setFormCategory('General');
    setFormPriority('medium');
    setFormStatus('pending');
    setFormDueDate('');
    setModalOpen(true);
  };

  const openEditModal = (task: TaskItem) => {
    setEditingTask(task);
    setFormTitle(task.title);
    setFormDesc(task.description || '');
    setFormCategory(task.category);
    setFormPriority(task.priority);
    setFormStatus(task.status);
    setFormDueDate(task.dueDate || '');
    setModalOpen(true);
  };

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formTitle.trim()) return;

    if (editingTask) {
      updateTask(editingTask.id, {
        title: formTitle.trim(),
        description: formDesc.trim() || undefined,
        category: formCategory,
        priority: formPriority,
        status: formStatus,
        dueDate: formDueDate || undefined,
      });
    } else {
      addTask({
        title: formTitle.trim(),
        description: formDesc.trim() || undefined,
        category: formCategory,
        priority: formPriority,
        status: formStatus,
        dueDate: formDueDate || undefined,
      });
    }

    setModalOpen(false);
  };

  // Filter logic
  const filteredTasks = tasks.filter((t) => {
    if (filter.status !== 'all' && t.status !== filter.status) return false;
    if (filter.priority !== 'all' && t.priority !== filter.priority) return false;
    if (filter.category !== 'all' && t.category !== filter.category) return false;
    if (filter.search.trim()) {
      const q = filter.search.toLowerCase();
      const matchTitle = t.title.toLowerCase().includes(q);
      const matchDesc = t.description?.toLowerCase().includes(q);
      const matchTag = t.tags?.some((tag) => tag.toLowerCase().includes(q));
      if (!matchTitle && !matchDesc && !matchTag) return false;
    }
    return true;
  });

  // Task Statistics
  const totalCount = tasks.length;
  const pendingCount = tasks.filter((t) => t.status === 'pending').length;
  const inProgressCount = tasks.filter((t) => t.status === 'in_progress').length;
  const completedCount = tasks.filter((t) => t.completed).length;
  const criticalCount = tasks.filter((t) => t.priority === 'critical' && !t.completed).length;
  const completionRate = totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0;

  return (
    <div className="space-y-6 pb-8">
      {/* Header with Title & Action */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
            Task Management
          </h1>
          <p className="text-xs sm:text-sm text-slate-400">
            Track, prioritize, and execute automated and assistant-directed tasks.
          </p>
        </div>

        <button
          type="button"
          onClick={openCreateModal}
          className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-xs sm:text-sm font-semibold text-white shadow-lg shadow-indigo-600/25 transition hover:bg-indigo-500 active:scale-[0.98]"
        >
          <Plus className="h-4 w-4" />
          <span>New Task</span>
        </button>
      </div>

      {/* Task Statistics Bar */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5 sm:gap-4">
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs text-slate-400 font-medium">Total Tasks</p>
          <p className="mt-2 text-2xl font-bold text-white">{totalCount}</p>
        </div>

        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs text-amber-400 font-medium">Pending</p>
          <p className="mt-2 text-2xl font-bold text-white">{pendingCount}</p>
        </div>

        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs text-cyan-400 font-medium">In Progress</p>
          <p className="mt-2 text-2xl font-bold text-white">{inProgressCount}</p>
        </div>

        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <p className="text-xs text-emerald-400 font-medium">Completed</p>
          <p className="mt-2 text-2xl font-bold text-white">{completedCount}</p>
        </div>

        <div className="col-span-2 sm:col-span-1 rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4">
          <div className="flex items-center justify-between">
            <p className="text-xs text-indigo-400 font-medium">Completion Rate</p>
            <BarChart3 className="h-4 w-4 text-indigo-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-1">
            <span className="text-2xl font-bold text-white">{completionRate}%</span>
            {criticalCount > 0 && (
              <span className="text-[10px] text-rose-400 font-semibold ml-1">
                ({criticalCount} critical)
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Filters and Controls */}
      <div className="flex flex-col gap-3 rounded-2xl border border-slate-800/80 bg-slate-900/30 p-4 lg:flex-row lg:items-center lg:justify-between">
        {/* Status Tabs */}
        <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-800/80 pb-3 lg:border-b-0 lg:pb-0">
          {(['all', 'pending', 'in_progress', 'completed'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setFilter({ status: tab })}
              className={`rounded-xl px-3 py-1.5 text-xs font-semibold capitalize transition ${
                filter.status === tab
                  ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-600/30'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              {tab.replace('_', ' ')}
            </button>
          ))}
        </div>

        {/* Search & Dropdown Filters */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Search */}
          <div className="relative flex-1 sm:w-48">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              value={filter.search}
              onChange={(e) => setFilter({ search: e.target.value })}
              placeholder="Search tasks..."
              className="h-8 w-full rounded-xl border border-slate-800 bg-slate-950/80 pl-8 pr-3 text-xs text-slate-200 placeholder-slate-500 focus:border-indigo-500/50 focus:outline-none"
            />
          </div>

          {/* Priority dropdown */}
          <select
            value={filter.priority}
            onChange={(e) => setFilter({ priority: e.target.value as any })}
            className="h-8 rounded-xl border border-slate-800 bg-slate-950/80 px-2.5 text-xs text-slate-300 focus:outline-none"
          >
            <option value="all">All Priorities</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>

          {/* Category dropdown */}
          <select
            value={filter.category}
            onChange={(e) => setFilter({ category: e.target.value as any })}
            className="h-8 rounded-xl border border-slate-800 bg-slate-950/80 px-2.5 text-xs text-slate-300 focus:outline-none"
          >
            <option value="all">All Categories</option>
            <option value="Security">Security</option>
            <option value="Memory">Memory</option>
            <option value="System">System</option>
            <option value="Development">Development</option>
            <option value="General">General</option>
          </select>

          {(filter.status !== 'all' ||
            filter.priority !== 'all' ||
            filter.category !== 'all' ||
            filter.search) && (
            <button
              type="button"
              onClick={resetFilter}
              className="rounded-xl border border-slate-800 p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              title="Reset filters"
            >
              <X className="h-4 w-4" />
            </button>
          )}

          {completedCount > 0 && (
            <button
              type="button"
              onClick={clearCompleted}
              className="rounded-xl border border-slate-800 bg-slate-900/60 px-2.5 py-1.5 text-xs text-slate-400 hover:border-rose-500/40 hover:text-rose-300"
            >
              Clear Done
            </button>
          )}
        </div>
      </div>

      {/* Task Cards List */}
      <div className="space-y-3">
        {filteredTasks.length === 0 ? (
          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-12 text-center">
            <CheckSquare className="mx-auto h-12 w-12 text-slate-600" />
            <p className="mt-3 text-sm font-semibold text-slate-300">No tasks found</p>
            <p className="mt-1 text-xs text-slate-500">
              Try adjusting your search query or create a new objective.
            </p>
            <button
              type="button"
              onClick={openCreateModal}
              className="mt-4 inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
            >
              <Plus className="h-3.5 w-3.5" />
              <span>Create Task</span>
            </button>
          </div>
        ) : (
          filteredTasks.map((task) => (
            <div
              key={task.id}
              className={`group flex flex-col justify-between gap-4 rounded-2xl border p-4 transition sm:flex-row sm:items-center ${
                task.completed
                  ? 'border-slate-800/60 bg-slate-950/40 opacity-70'
                  : 'border-slate-800/90 bg-slate-900/50 hover:border-indigo-500/40 hover:bg-slate-900/80 shadow-md'
              }`}
            >
              {/* Checkbox & Task details */}
              <div className="flex items-start gap-3.5 min-w-0 flex-1">
                <button
                  type="button"
                  onClick={() => toggleTaskCompleted(task.id)}
                  className="mt-0.5 shrink-0 text-slate-500 hover:text-emerald-400 transition"
                  title={task.completed ? 'Mark incomplete' : 'Mark complete'}
                >
                  {task.completed ? (
                    <CheckCircle2 className="h-5 w-5 text-emerald-400" />
                  ) : (
                    <Circle className="h-5 w-5" />
                  )}
                </button>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`text-sm font-semibold truncate ${
                        task.completed ? 'text-slate-500 line-through' : 'text-slate-100'
                      }`}
                    >
                      {task.title}
                    </span>

                    {/* Priority badge */}
                    <span
                      className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                        task.priority === 'critical'
                          ? 'border border-rose-500/40 bg-rose-500/15 text-rose-300'
                          : task.priority === 'high'
                          ? 'border border-amber-500/40 bg-amber-500/15 text-amber-300'
                          : task.priority === 'medium'
                          ? 'border border-indigo-500/30 bg-indigo-500/10 text-indigo-300'
                          : 'border border-slate-700 bg-slate-800 text-slate-400'
                      }`}
                    >
                      {task.priority}
                    </span>

                    {/* Status badge */}
                    <span
                      className={`rounded-md px-2 py-0.5 text-[10px] font-medium capitalize ${
                        task.status === 'in_progress'
                          ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30'
                          : task.status === 'completed'
                          ? 'bg-emerald-500/15 text-emerald-300'
                          : 'bg-slate-800 text-slate-400'
                      }`}
                    >
                      {task.status.replace('_', ' ')}
                    </span>
                  </div>

                  {task.description && (
                    <p className="mt-1 text-xs text-slate-400 leading-relaxed line-clamp-2">
                      {task.description}
                    </p>
                  )}

                  {/* Tags and Due Date footer */}
                  <div className="mt-2.5 flex flex-wrap items-center gap-3 text-[11px] text-slate-500">
                    <span className="rounded bg-slate-800/80 px-2 py-0.5 font-medium text-slate-300">
                      {task.category}
                    </span>

                    {task.dueDate && (
                      <span className="flex items-center gap-1 font-mono">
                        <Calendar className="h-3 w-3 text-slate-400" />
                        Due {task.dueDate}
                      </span>
                    )}

                    {task.tags?.map((t) => (
                      <span key={t} className="font-mono text-slate-500">
                        #{t}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center justify-end gap-1.5 shrink-0 border-t border-slate-800/60 pt-2 sm:border-t-0 sm:pt-0">
                <button
                  type="button"
                  onClick={() => openEditModal(task)}
                  className="rounded-xl p-2 text-slate-400 transition hover:bg-slate-800 hover:text-slate-200"
                  title="Edit task"
                >
                  <Edit2 className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => deleteTask(task.id)}
                  className="rounded-xl p-2 text-slate-400 transition hover:bg-slate-800 hover:text-rose-400"
                  title="Delete task"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Modal: Create / Edit Task */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-lg rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <h2 className="text-base font-bold text-white">
                {editingTask ? 'Edit Task' : 'Create New Task'}
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
                <label className="block text-xs font-medium text-slate-300">Task Title *</label>
                <input
                  type="text"
                  required
                  value={formTitle}
                  onChange={(e) => setFormTitle(e.target.value)}
                  placeholder="e.g. Audit filesystem sandbox boundaries"
                  className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-3 text-xs text-white placeholder-slate-500 focus:border-indigo-500/60 focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300">Description</label>
                <textarea
                  rows={3}
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  placeholder="Additional context or requirements..."
                  className="mt-1 w-full rounded-xl border border-slate-800 bg-slate-900 p-3 text-xs text-white placeholder-slate-500 focus:border-indigo-500/60 focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-300">Category</label>
                  <select
                    value={formCategory}
                    onChange={(e) => setFormCategory(e.target.value as TaskCategory)}
                    className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:outline-none"
                  >
                    <option value="General">General</option>
                    <option value="Security">Security</option>
                    <option value="Memory">Memory</option>
                    <option value="System">System</option>
                    <option value="Development">Development</option>
                    <option value="Research">Research</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-300">Priority</label>
                  <select
                    value={formPriority}
                    onChange={(e) => setFormPriority(e.target.value as TaskPriority)}
                    className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:outline-none"
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="critical">Critical</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-300">Status</label>
                  <select
                    value={formStatus}
                    onChange={(e) => setFormStatus(e.target.value as TaskStatus)}
                    className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:outline-none"
                  >
                    <option value="pending">Pending</option>
                    <option value="in_progress">In Progress</option>
                    <option value="completed">Completed</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-300">Due Date</label>
                  <input
                    type="date"
                    value={formDueDate}
                    onChange={(e) => setFormDueDate(e.target.value)}
                    className="mt-1 h-10 w-full rounded-xl border border-slate-800 bg-slate-900 px-2.5 text-xs text-slate-200 focus:outline-none"
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
                  className="rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
                >
                  {editingTask ? 'Save Changes' : 'Create Task'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default Tasks;
