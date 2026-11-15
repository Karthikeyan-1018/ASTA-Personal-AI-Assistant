import React, { useState } from 'react';
import {
  Wrench,
  Play,
  Shield,
  ShieldAlert,
  X,
  Terminal,
  Check,
} from 'lucide-react';
import { useToolStore } from '../stores/tool.store';
import type { ToolDefinition, ToolCategory, ToolExecutionLog } from '../types/tool.types';

export const Tools: React.FC = () => {
  const tools = useToolStore((s) => s.tools);
  const executionLogs = useToolStore((s) => s.executionLogs);
  const pendingConfirmation = useToolStore((s) => s.pendingConfirmation);
  const isBackendConnected = useToolStore((s) => s.isBackendConnected);
  const emergencyStopActive = useToolStore((s) => s.emergencyStopActive);
  const toggleEmergencyStop = useToolStore((s) => s.toggleEmergencyStop);
  const toggleToolEnabled = useToolStore((s) => s.toggleToolEnabled);
  const executeToolSimulated = useToolStore((s) => s.executeToolSimulated);
  const approvePendingConfirmation = useToolStore((s) => s.approvePendingConfirmation);
  const rejectPendingConfirmation = useToolStore((s) => s.rejectPendingConfirmation);
  const clearLogs = useToolStore((s) => s.clearLogs);

  const [selectedCategory, setSelectedCategory] = useState<'All' | ToolCategory>('All');
  const [activeRunnerTool, setActiveRunnerTool] = useState<ToolDefinition | null>(null);
  const [runnerParams, setRunnerParams] = useState<Record<string, any>>({});
  const [isRunning, setIsRunning] = useState(false);
  const [runnerResult, setRunnerResult] = useState<ToolExecutionLog | null>(null);

  const [viewLogDetail, setViewLogDetail] = useState<ToolExecutionLog | null>(null);

  const categories: ToolCategory[] = [
    'Information',
    'Filesystem',
    'Application',
    'Process',
    'Desktop',
    'Background Tasks',
  ];

  const filteredTools = tools.filter(
    (t) => selectedCategory === 'All' || t.category === selectedCategory
  );

  const openRunner = (tool: ToolDefinition) => {
    setActiveRunnerTool(tool);
    setRunnerResult(null);
    const initialParams: Record<string, any> = {};
    tool.parameters.forEach((p) => {
      initialParams[p.name] = p.default !== undefined ? p.default : '';
    });
    setRunnerParams(initialParams);
  };

  const handleExecuteRunner = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeRunnerTool) return;

    setIsRunning(true);
    setRunnerResult(null);

    try {
      const result = await executeToolSimulated(activeRunnerTool.id, runnerParams);
      setRunnerResult(result);
    } catch {
      // Handled
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
            Tool Execution & Sandbox
          </h1>
          <p className="text-xs sm:text-sm text-slate-400">
            Audited OS tools, process inspectors, application bridges, and background tasks.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={toggleEmergencyStop}
            className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
              emergencyStopActive
                ? 'bg-rose-600 text-white shadow-lg shadow-rose-600/30'
                : 'border border-slate-700 bg-slate-800 text-slate-300 hover:border-rose-500/50 hover:text-rose-400'
            }`}
          >
            <ShieldAlert className="h-4 w-4" />
            <span>{emergencyStopActive ? 'Emergency Stop Engaged' : 'Emergency Stop'}</span>
          </button>

          <div
            className={`inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 text-xs font-semibold ${
              isBackendConnected
                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
                : 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300'
            }`}
          >
            <Shield className="h-4 w-4" />
            <span>{isBackendConnected ? 'Audited Pipeline Live' : 'Interactive Sandbox Demo'}</span>
          </div>
        </div>
      </div>

      {/* Security Disclaimer Banner */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4 text-xs text-slate-400">
        <div className="flex items-start gap-3">
          <Shield className="h-5 w-5 text-indigo-400 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold text-slate-200">
              Zero System Impact Execution Environment
            </p>
            <p className="mt-0.5 leading-relaxed text-slate-400">
              This module simulates execution state, parameters, confirmation safeguards, and
              telemetry outputs. No host processes or filesystem records outside this web client
              are altered.
            </p>
          </div>
        </div>
      </div>

      {/* Pending Confirmation Alert / Modal */}
      {pendingConfirmation && (
        <div className="rounded-3xl border border-rose-500/40 bg-rose-500/10 p-5 shadow-2xl">
          <div className="flex items-center gap-2.5 text-rose-300 font-bold text-sm">
            <ShieldAlert className="h-5 w-5 text-rose-400 animate-pulse" />
            <span>Elevated Security Confirmation Required</span>
          </div>

          <div className="mt-3 rounded-2xl border border-slate-800 bg-slate-950 p-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <p className="text-xs font-bold text-white">{pendingConfirmation.toolName}</p>
                <p className="text-[11px] text-slate-400">
                  Target action requires user authorization before execution.
                </p>
              </div>
              <span className="rounded bg-rose-500/20 px-2.5 py-0.5 font-mono text-[10px] font-bold text-rose-300">
                PENDING APPROVAL
              </span>
            </div>

            <div className="mt-3 rounded-xl bg-slate-900 p-2.5 font-mono text-xs text-slate-300">
              <pre className="text-[11px] whitespace-pre-wrap">
                {JSON.stringify(pendingConfirmation.parameters, null, 2)}
              </pre>
            </div>
          </div>

          <div className="mt-4 flex justify-end gap-3">
            <button
              type="button"
              onClick={rejectPendingConfirmation}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-700 hover:text-rose-400"
            >
              <X className="h-4 w-4" />
              <span>Reject Execution</span>
            </button>
            <button
              type="button"
              onClick={approvePendingConfirmation}
              className="inline-flex items-center gap-1.5 rounded-xl bg-rose-600 px-4 py-2 text-xs font-semibold text-white shadow-lg shadow-rose-600/30 hover:bg-rose-500"
            >
              <Check className="h-4 w-4" />
              <span>Approve & Execute</span>
            </button>
          </div>
        </div>
      )}

      {/* Category Tabs */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-800 pb-3">
        <button
          type="button"
          onClick={() => setSelectedCategory('All')}
          className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
            selectedCategory === 'All'
              ? 'bg-cyan-600 text-white shadow-sm'
              : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
          }`}
        >
          All Categories
        </button>
        {categories.map((cat) => (
          <button
            key={cat}
            type="button"
            onClick={() => setSelectedCategory(cat)}
            className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
              selectedCategory === cat
                ? 'bg-cyan-600 text-white shadow-sm'
                : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
            }`}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Tool Cards Grid */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {filteredTools.map((tool) => (
          <div
            key={tool.id}
            className={`flex flex-col justify-between rounded-2xl border p-5 transition shadow-lg ${
              tool.enabled
                ? 'border-slate-800/90 bg-slate-900/40 hover:border-cyan-500/40 hover:bg-slate-900/70'
                : 'border-slate-800/40 bg-slate-950/40 opacity-50'
            }`}
          >
            <div>
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-2.5">
                  <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-cyan-500/10 text-cyan-400">
                    <Wrench className="h-4 w-4" />
                  </div>
                  <div>
                    <h3 className="text-xs sm:text-sm font-bold text-white truncate">{tool.name}</h3>
                    <span className="text-[10px] text-slate-500 font-mono">{tool.category}</span>
                  </div>
                </div>

                {/* Enabled Toggle Switch */}
                <button
                  type="button"
                  onClick={() => toggleToolEnabled(tool.id)}
                  className={`h-5 w-9 rounded-full transition-colors relative p-0.5 ${
                    tool.enabled ? 'bg-cyan-600' : 'bg-slate-800'
                  }`}
                  title={tool.enabled ? 'Disable tool' : 'Enable tool'}
                >
                  <span
                    className={`block h-4 w-4 rounded-full bg-white transition-transform ${
                      tool.enabled ? 'translate-x-4' : 'translate-x-0'
                    }`}
                  />
                </button>
              </div>

              <p className="mt-3 text-xs text-slate-400 leading-relaxed">
                {tool.description}
              </p>

              {/* Permission & Confirmation Badges */}
              <div className="mt-3.5 flex flex-wrap items-center gap-2">
                <span
                  className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                    tool.permission === 'read_only'
                      ? 'border border-emerald-500/30 bg-emerald-500/15 text-emerald-300'
                      : tool.permission === 'elevated'
                      ? 'border border-amber-500/30 bg-amber-500/15 text-amber-300'
                      : 'border border-rose-500/30 bg-rose-500/15 text-rose-300'
                  }`}
                >
                  {tool.permission.replace('_', ' ')}
                </span>

                {tool.confirmationRequired && (
                  <span className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
                    Confirmation Required
                  </span>
                )}
              </div>
            </div>

            {/* Run button */}
            <div className="mt-5 border-t border-slate-800/80 pt-3">
              <button
                type="button"
                disabled={!tool.enabled}
                onClick={() => openRunner(tool)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-slate-800 px-3 py-2 text-xs font-semibold text-cyan-300 transition hover:bg-slate-700 disabled:opacity-40"
              >
                <Play className="h-3.5 w-3.5 fill-current" />
                <span>Simulate Execution</span>
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Execution History Table */}
      <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
        <div className="flex items-center justify-between pb-4 border-b border-slate-800/80">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-cyan-400" />
            <h2 className="text-sm font-bold text-white uppercase tracking-wider">
              Tool Execution Audit Log
            </h2>
          </div>
          {executionLogs.length > 0 && (
            <button
              type="button"
              onClick={clearLogs}
              className="text-xs text-slate-500 hover:text-slate-300"
            >
              Clear Log
            </button>
          )}
        </div>

        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-[11px] font-semibold text-slate-400">
                <th className="py-2.5 px-3">Timestamp</th>
                <th className="py-2.5 px-3">Tool Name</th>
                <th className="py-2.5 px-3">Status</th>
                <th className="py-2.5 px-3">Duration</th>
                <th className="py-2.5 px-3">Confirmed By</th>
                <th className="py-2.5 px-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {executionLogs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-slate-500">
                    No executions recorded. Run any tool to populate the audit log.
                  </td>
                </tr>
              ) : (
                executionLogs.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-900/50 transition">
                    <td className="py-2.5 px-3 font-mono text-slate-400 text-[11px]">
                      {new Date(log.executedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </td>
                    <td className="py-2.5 px-3 font-medium text-slate-200">
                      {log.toolName}
                    </td>
                    <td className="py-2.5 px-3">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                          log.status === 'success'
                            ? 'bg-emerald-500/15 text-emerald-300'
                            : log.status === 'cancelled'
                            ? 'bg-rose-500/15 text-rose-300'
                            : 'bg-amber-500/15 text-amber-300'
                        }`}
                      >
                        {log.status.replace('_', ' ')}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 font-mono text-slate-400 text-[11px]">
                      {log.durationMs}ms
                    </td>
                    <td className="py-2.5 px-3 text-slate-400">
                      {log.confirmedBy || 'Automated'}
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      <button
                        type="button"
                        onClick={() => setViewLogDetail(log)}
                        className="text-cyan-400 hover:text-cyan-300 font-medium"
                      >
                        Details
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Runner Modal */}
      {activeRunnerTool && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-lg rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <div>
                <h2 className="text-base font-bold text-white">{activeRunnerTool.name}</h2>
                <p className="text-xs text-slate-400 font-mono">
                  {activeRunnerTool.permission.toUpperCase()} · {activeRunnerTool.category}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setActiveRunnerTool(null)}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={handleExecuteRunner} className="mt-4 space-y-4">
              {activeRunnerTool.parameters.map((param) => (
                <div key={param.name}>
                  <label className="block text-xs font-medium text-slate-300">
                    {param.name} {param.required && <span className="text-rose-400">*</span>}
                  </label>
                  <p className="text-[11px] text-slate-500">{param.description}</p>
                  <input
                    type={param.type === 'number' ? 'number' : 'text'}
                    value={runnerParams[param.name] ?? ''}
                    onChange={(e) =>
                      setRunnerParams({ ...runnerParams, [param.name]: e.target.value })
                    }
                    required={param.required}
                    className="mt-1 h-9 w-full rounded-xl border border-slate-800 bg-slate-900 px-3 text-xs text-white focus:border-cyan-500/60 focus:outline-none"
                  />
                </div>
              ))}

              <div className="mt-6 flex justify-end gap-2.5 border-t border-slate-800 pt-4">
                <button
                  type="button"
                  onClick={() => setActiveRunnerTool(null)}
                  className="rounded-xl border border-slate-800 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800"
                >
                  Close
                </button>
                <button
                  type="submit"
                  disabled={isRunning}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-cyan-600 px-4 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50"
                >
                  {isRunning ? (
                    <span>Simulating...</span>
                  ) : (
                    <>
                      <Play className="h-3.5 w-3.5 fill-current" />
                      <span>Run Tool</span>
                    </>
                  )}
                </button>
              </div>
            </form>

            {/* Simulated Result Viewer */}
            {runnerResult && (
              <div className="mt-4 rounded-2xl border border-slate-800 bg-slate-900/80 p-3.5">
                <div className="flex items-center justify-between text-xs font-semibold text-slate-300">
                  <span>Execution Output</span>
                  <span className="font-mono text-[10px] text-emerald-400">
                    {runnerResult.durationMs}ms
                  </span>
                </div>
                <pre className="mt-2 max-h-40 overflow-y-auto rounded-lg bg-slate-950 p-2.5 font-mono text-[11px] text-slate-300 whitespace-pre-wrap">
                  {runnerResult.output || runnerResult.errorMessage || 'No output recorded'}
                </pre>
              </div>
            )}
          </div>
        </div>
      )}

      {/* View Log Detail Modal */}
      {viewLogDetail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-lg rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <h2 className="text-sm font-bold text-white">Execution Audit #{viewLogDetail.id}</h2>
              <button
                type="button"
                onClick={() => setViewLogDetail(null)}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-3 space-y-3 text-xs">
              <div>
                <span className="text-slate-400">Tool:</span>{' '}
                <span className="font-semibold text-white">{viewLogDetail.toolName}</span>
              </div>
              <div>
                <span className="text-slate-400">Executed At:</span>{' '}
                <span className="font-mono text-slate-300">{viewLogDetail.executedAt}</span>
              </div>
              <div>
                <span className="text-slate-400">Parameters:</span>
                <pre className="mt-1 rounded-lg bg-slate-900 p-2 font-mono text-[11px] text-slate-300">
                  {JSON.stringify(viewLogDetail.parameters, null, 2)}
                </pre>
              </div>
              <div>
                <span className="text-slate-400">Output Payload:</span>
                <pre className="mt-1 max-h-48 overflow-y-auto rounded-lg bg-slate-900 p-2 font-mono text-[11px] text-slate-300">
                  {viewLogDetail.output || viewLogDetail.errorMessage}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Tools;
