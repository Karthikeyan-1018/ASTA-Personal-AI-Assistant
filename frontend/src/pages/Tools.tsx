import React, { useEffect, useMemo, useState } from 'react';
import {
  Wrench,
  Play,
  Shield,
  ShieldAlert,
  X,
  Terminal,
  Check,
  Loader2,
  RefreshCw,
  AlertTriangle,
  KeyRound,
  Clock,
  Ban,
} from 'lucide-react';
import { useToolStore } from '../stores/tool.store';
import {
  toolCategoryLabel,
  type ToolDefinition,
  type ToolExecutionLog,
  type ToolParameterDef,
} from '../types/tool.types';

function formatTimestamp(value?: string | null): string {
  if (!value) return 'Not recorded';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function durationLabel(value: number | null): string {
  return typeof value === 'number' ? `${value} ms` : 'Not recorded';
}

function statusTone(status: string, success?: boolean): string {
  if (
    success === true ||
    status === 'success' ||
    status === 'executed' ||
    status === 'succeeded'
  ) {
    return 'border border-emerald-500/30 bg-emerald-500/15 text-emerald-300';
  }

  if (
    status === 'error' ||
    status === 'failed' ||
    status === 'denied' ||
    status === 'timeout' ||
    status === 'timed_out' ||
    status === 'cancelled' ||
    status === 'rejected' ||
    status === 'expired'
  ) {
    return 'border border-rose-500/30 bg-rose-500/15 text-rose-300';
  }

  return 'border border-amber-500/30 bg-amber-500/15 text-amber-300';
}

function categoryTone(category: string): string {
  if (category === 'read_only') {
    return 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-300';
  }
  if (category === 'reversible') {
    return 'border border-sky-500/30 bg-sky-500/10 text-sky-300';
  }
  if (category === 'sensitive') {
    return 'border border-rose-500/30 bg-rose-500/10 text-rose-300';
  }
  return 'border border-slate-700 bg-slate-800/80 text-slate-300';
}

const SectionError: React.FC<{
  title: string;
  message: string;
  onRetry: () => void;
  retrying?: boolean;
  onDismiss?: () => void;
}> = ({ title, message, onRetry, retrying, onDismiss }) => (
  <div
    role="alert"
    className="flex flex-col gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 sm:flex-row sm:items-center sm:justify-between"
  >
    <div className="flex items-start gap-3">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
      <div>
        <p className="text-xs font-semibold text-rose-200">{title}</p>
        <p className="mt-0.5 break-words text-[11px] text-rose-200/80">{message}</p>
      </div>
    </div>
    <div className="flex shrink-0 items-center gap-2">
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="rounded-lg px-2 py-1 text-[11px] text-rose-200/80 transition hover:bg-rose-500/15 hover:text-rose-100"
        >
          Dismiss
        </button>
      )}
      <button
        type="button"
        onClick={onRetry}
        disabled={retrying}
        className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${retrying ? 'animate-spin' : ''}`} />
        <span>Retry</span>
      </button>
    </div>
  </div>
);

const LoadingRow: React.FC<{ label: string }> = ({ label }) => (
  <div className="flex items-center justify-center gap-2 rounded-2xl border border-slate-800/80 bg-slate-900/30 p-8 text-xs text-slate-400">
    <Loader2 className="h-4 w-4 animate-spin text-cyan-400" />
    <span>{label}</span>
  </div>
);

const StatusBadge: React.FC<{ status: string; success?: boolean }> = ({
  status,
  success,
}) => (
  <span
    className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${statusTone(
      status,
      success
    )}`}
  >
    {status.replace(/_/g, ' ')}
  </span>
);

export const Tools: React.FC = () => {
  const tools = useToolStore((s) => s.tools);
  const toolsStatus = useToolStore((s) => s.toolsStatus);
  const toolsError = useToolStore((s) => s.toolsError);
  const toolsLoaded = useToolStore((s) => s.toolsLoaded);

  const executionLogs = useToolStore((s) => s.executionLogs);
  const historyStatus = useToolStore((s) => s.historyStatus);
  const historyError = useToolStore((s) => s.historyError);
  const historyLoaded = useToolStore((s) => s.historyLoaded);
  const historyTotal = useToolStore((s) => s.historyTotal);
  const historyViewCleared = useToolStore((s) => s.historyViewCleared);

  const pendingConfirmations = useToolStore((s) => s.pendingConfirmations);
  const confirmationsStatus = useToolStore((s) => s.confirmationsStatus);
  const confirmationsError = useToolStore((s) => s.confirmationsError);
  const confirmationsLoaded = useToolStore((s) => s.confirmationsLoaded);
  const confirmationPendingIds = useToolStore((s) => s.confirmationPendingIds);

  const emergencyStop = useToolStore((s) => s.emergencyStop);
  const emergencyStopStatus = useToolStore((s) => s.emergencyStopStatus);
  const emergencyStopError = useToolStore((s) => s.emergencyStopError);
  const emergencyStopPending = useToolStore((s) => s.emergencyStopPending);

  const metrics = useToolStore((s) => s.metrics);
  const metricsStatus = useToolStore((s) => s.metricsStatus);
  const metricsError = useToolStore((s) => s.metricsError);

  const executionPending = useToolStore((s) => s.executionPending);
  const executionError = useToolStore((s) => s.executionError);
  const lastActionResult = useToolStore((s) => s.lastActionResult);
  const actionNotice = useToolStore((s) => s.actionNotice);

  const loadToolWorkspace = useToolStore((s) => s.loadToolWorkspace);
  const fetchTools = useToolStore((s) => s.fetchTools);
  const fetchHistory = useToolStore((s) => s.fetchHistory);
  const fetchExecutionDetail = useToolStore((s) => s.fetchExecutionDetail);
  const fetchConfirmations = useToolStore((s) => s.fetchConfirmations);
  const fetchEmergencyStop = useToolStore((s) => s.fetchEmergencyStop);
  const fetchMetrics = useToolStore((s) => s.fetchMetrics);
  const executeTool = useToolStore((s) => s.executeTool);
  const approvePendingConfirmation = useToolStore(
    (s) => s.approvePendingConfirmation
  );
  const rejectPendingConfirmation = useToolStore(
    (s) => s.rejectPendingConfirmation
  );
  const engageEmergencyStop = useToolStore((s) => s.engageEmergencyStop);
  const recoverEmergencyStop = useToolStore((s) => s.recoverEmergencyStop);
  const clearHistoryView = useToolStore((s) => s.clearHistoryView);
  const clearToolsError = useToolStore((s) => s.clearToolsError);
  const clearHistoryError = useToolStore((s) => s.clearHistoryError);
  const clearConfirmationsError = useToolStore((s) => s.clearConfirmationsError);
  const clearEmergencyStopError = useToolStore((s) => s.clearEmergencyStopError);
  const clearMetricsError = useToolStore((s) => s.clearMetricsError);
  const clearExecutionError = useToolStore((s) => s.clearExecutionError);
  const clearActionNotice = useToolStore((s) => s.clearActionNotice);

  const [selectedCategory, setSelectedCategory] = useState<string>('All');
  const [activeRunnerToolId, setActiveRunnerToolId] = useState<string | null>(
    null
  );
  const [runnerParams, setRunnerParams] = useState<
    Record<string, string | boolean>
  >({});
  const [runnerResult, setRunnerResult] = useState<ToolExecutionLog | null>(null);
  const [runnerError, setRunnerError] = useState<string | null>(null);
  const [viewLogDetail, setViewLogDetail] = useState<ToolExecutionLog | null>(
    null
  );
  const [viewLogLoading, setViewLogLoading] = useState(false);
  const [viewLogError, setViewLogError] = useState<string | null>(null);
  const [showRecovery, setShowRecovery] = useState(false);
  const [recoveryToken, setRecoveryToken] = useState('');
  const [recoveryError, setRecoveryError] = useState<string | null>(null);

  const activeRunnerTool = useMemo(
    () => tools.find((tool) => tool.id === activeRunnerToolId) ?? null,
    [tools, activeRunnerToolId]
  );

  // Independent hydration: the store serialises each section and preserves
  // confirmed data when a request fails. The layout may already have loaded
  // the catalogue, so every section is checked before hydrating.
  useEffect(() => {
    const state = useToolStore.getState();
    if (
      state.toolsStatus === 'idle' ||
      state.historyStatus === 'idle' ||
      state.confirmationsStatus === 'idle' ||
      state.emergencyStopStatus === 'idle' ||
      state.metricsStatus === 'idle'
    ) {
      void loadToolWorkspace();
    }
  }, [loadToolWorkspace]);

  // Server-owned state can change from another session. Refresh the
  // emergency stop and pending confirmations on focus and on a slow interval.
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === 'hidden') return;
      void fetchEmergencyStop(true);
      void fetchConfirmations(true);
    };

    const timer = window.setInterval(refresh, 30000);
    window.addEventListener('focus', refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
    };
  }, [fetchEmergencyStop, fetchConfirmations]);

  const categories = useMemo(() => {
    const values: string[] = [];
    for (const tool of tools) {
      if (!values.includes(tool.category)) values.push(tool.category);
    }
    return values;
  }, [tools]);

  // Derive the active filter instead of mutating state in an effect. A
  // category disappears when the backend catalogue changes.
  const activeCategory =
    selectedCategory !== 'All' &&
    toolsLoaded &&
    !tools.some((tool) => tool.category === selectedCategory)
      ? 'All'
      : selectedCategory;

  const filteredTools = tools.filter(
    (tool) => activeCategory === 'All' || tool.category === activeCategory
  );

  const isEmergencyStopped = emergencyStop?.engaged === true;
  const emergencyKnown = emergencyStopStatus === 'ready' && emergencyStop !== null;

  const openRunner = (tool: ToolDefinition) => {
    setActiveRunnerToolId(tool.id);
    setRunnerResult(null);
    setRunnerError(null);
    clearExecutionError();

    const initialParams: Record<string, string | boolean> = {};
    for (const parameter of tool.parameters) {
      if (parameter.type === 'boolean') {
        initialParams[parameter.name] =
          typeof parameter.default === 'boolean' ? parameter.default : false;
      } else {
        initialParams[parameter.name] =
          parameter.default !== undefined ? String(parameter.default) : '';
      }
    }
    setRunnerParams(initialParams);
  };

  const closeRunner = () => {
    setActiveRunnerToolId(null);
    setRunnerParams({});
    setRunnerResult(null);
    setRunnerError(null);
  };

  const buildArguments = (
    parameters: ToolParameterDef[],
    values: Record<string, string | boolean>
  ): { args: Record<string, unknown>; error: string | null } => {
    const args: Record<string, unknown> = {};

    for (const parameter of parameters) {
      const value = values[parameter.name];

      if (parameter.type === 'boolean') {
        args[parameter.name] = value === true || value === 'true';
        continue;
      }

      const text = typeof value === 'string' ? value : '';

      if (text === '') {
        if (parameter.required) {
          return {
            args,
            error: `"${parameter.name}" is required by the backend schema.`,
          };
        }
        continue;
      }

      if (parameter.type === 'number') {
        const parsed = Number(text);
        if (!Number.isFinite(parsed)) {
          return {
            args,
            error: `"${parameter.name}" must be a valid number.`,
          };
        }
        args[parameter.name] = parsed;
        continue;
      }

      args[parameter.name] = text;
    }

    return { args, error: null };
  };

  const handleExecuteRunner = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!activeRunnerTool || executionPending) return;

    const { args, error } = buildArguments(
      activeRunnerTool.parameters,
      runnerParams
    );

    if (error) {
      setRunnerError(error);
      return;
    }

    setRunnerError(null);
    setRunnerResult(null);

    const result = await executeTool(activeRunnerTool.id, args);
    setRunnerResult(result.record);
    setRunnerError(result.error);
  };

  const handleApprove = async (confirmationId: number) => {
    setRunnerError(null);
    await approvePendingConfirmation(confirmationId);
  };

  const handleReject = async (confirmationId: number) => {
    setRunnerError(null);
    await rejectPendingConfirmation(confirmationId);
  };

  const handleRecovery = async (event: React.FormEvent) => {
    event.preventDefault();
    setRecoveryError(null);

    const result = await recoverEmergencyStop(recoveryToken);
    if (result) {
      setRecoveryToken('');
      setShowRecovery(false);
    } else {
      setRecoveryError(
        useToolStore.getState().emergencyStopError ??
          'The emergency stop could not be recovered.'
      );
    }
  };

  const openLogDetail = async (log: ToolExecutionLog) => {
    setViewLogDetail(log);
    setViewLogError(null);

    if (typeof log.executionId !== 'number') return;

    // Re-read the record from the backend so the modal never shows a stale or
    // locally synthesised row.
    setViewLogLoading(true);
    const fresh = await fetchExecutionDetail(log.executionId);
    setViewLogLoading(false);

    if (fresh) {
      setViewLogDetail(fresh);
    } else {
      setViewLogError(
        useToolStore.getState().historyError ??
          'That execution record could not be loaded.'
      );
    }
  };

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white sm:text-2xl">
            Tools &amp; Execution
          </h1>
          <p className="text-xs text-slate-400 sm:text-sm">
            Backend-connected tools with server-controlled availability and
            audited, metadata-only execution history.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {isEmergencyStopped ? (
            <button
              type="button"
              onClick={() => {
                setRecoveryError(null);
                setShowRecovery(true);
              }}
              disabled={emergencyStopPending}
              className="inline-flex items-center gap-1.5 rounded-xl bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white shadow-lg shadow-rose-600/30 transition hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <ShieldAlert className="h-4 w-4" />
              <span>Emergency Stop Engaged</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void engageEmergencyStop()}
              disabled={!emergencyKnown || emergencyStopPending}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-semibold text-slate-300 transition hover:border-rose-500/50 hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
              title={
                emergencyKnown
                  ? 'Engage the server-side OS automation emergency stop'
                  : 'Emergency-stop status is not confirmed by the server'
              }
            >
              {emergencyStopPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ShieldAlert className="h-4 w-4" />
              )}
              <span>Engage Emergency Stop</span>
            </button>
          )}

          <div
            className={`inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 text-xs font-semibold ${
              toolsStatus === 'ready'
                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
                : toolsStatus === 'error'
                ? 'border-rose-500/30 bg-rose-500/10 text-rose-300'
                : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
            }`}
          >
            <Shield className="h-4 w-4" />
            <span>
              {toolsStatus === 'ready'
                ? 'Backend catalogue loaded'
                : toolsStatus === 'error'
                ? 'Backend catalogue unavailable'
                : 'Loading backend catalogue…'}
            </span>
          </div>
        </div>
      </div>

      {/* Factual security note */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4 text-xs text-slate-400">
        <div className="flex items-start gap-3">
          <Shield className="mt-0.5 h-5 w-5 shrink-0 text-indigo-400" />
          <div>
            <p className="font-semibold text-slate-200">
              Server-enforced execution policy
            </p>
            <p className="mt-0.5 leading-relaxed">
              Every execution is validated and authorised by the backend tool
              policy. Tools marked as requiring confirmation must be approved
              here before the server runs them. Execution history is recorded
              by the backend and does not retain tool output or argument values.
            </p>
          </div>
        </div>
      </div>

      {/* Tools section error */}
      {toolsError && (
        <SectionError
          title="Tool catalogue could not be loaded"
          message={toolsError}
          onRetry={() => void fetchTools()}
          retrying={toolsStatus === 'loading'}
          onDismiss={clearToolsError}
        />
      )}

      {/* Emergency-stop section error */}
      {emergencyStopError && (
        <SectionError
          title="Emergency-stop state could not be confirmed"
          message={emergencyStopError}
          onRetry={() => void fetchEmergencyStop()}
          retrying={emergencyStopStatus === 'loading'}
          onDismiss={clearEmergencyStopError}
        />
      )}

      {/* Confirmed emergency-stop details */}
      {isEmergencyStopped && emergencyStop && (
        <div className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-start gap-3">
              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-rose-300" />
              <div>
                <p className="text-sm font-bold text-rose-200">
                  OS automation is blocked by the server
                </p>
                <p className="mt-0.5 text-[11px] text-rose-200/80">
                  Engaged {formatTimestamp(emergencyStop.engaged_at)}
                  {typeof emergencyStop.engaged_by_user_id === 'number'
                    ? ` by user #${emergencyStop.engaged_by_user_id}`
                    : ''}
                  . Tool execution is unavailable until the server confirms
                  recovery.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => {
                setRecoveryError(null);
                setShowRecovery(true);
              }}
              className="inline-flex items-center gap-1.5 rounded-xl border border-rose-400/40 bg-rose-500/15 px-3 py-1.5 text-xs font-semibold text-rose-100 transition hover:bg-rose-500/25"
            >
              <KeyRound className="h-3.5 w-3.5" />
              <span>Recover with server token</span>
            </button>
          </div>
        </div>
      )}

      {/* Execution error */}
      {executionError && (
        <div
          role="alert"
          className="flex items-start justify-between gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
            <div>
              <p className="text-xs font-semibold text-rose-200">
                Execution unavailable or failed
              </p>
              <p className="mt-0.5 break-words text-[11px] text-rose-200/80">
                {executionError}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={clearExecutionError}
            className="rounded-lg p-1 text-rose-300/70 transition hover:bg-rose-500/10 hover:text-rose-200"
            title="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {actionNotice && (
        <div
          role="status"
          className="flex items-center justify-between gap-3 rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-3.5"
        >
          <p className="text-xs font-semibold text-emerald-200">{actionNotice}</p>
          <button
            type="button"
            onClick={clearActionNotice}
            className="rounded-lg p-1 text-emerald-200/70 transition hover:bg-emerald-500/10 hover:text-emerald-100"
            title="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Pending confirmations */}
      {(pendingConfirmations.length > 0 ||
        confirmationsError ||
        (confirmationsStatus === 'loading' && !confirmationsLoaded)) && (
        <div className="rounded-2xl border border-amber-500/30 bg-amber-500/5 p-5 shadow-lg">
          <div className="flex items-center gap-2 border-b border-amber-500/20 pb-3">
            <ShieldAlert className="h-4 w-4 text-amber-300" />
            <h2 className="text-sm font-bold uppercase tracking-wider text-amber-200">
              Pending Confirmations
            </h2>
          </div>

          {confirmationsError && (
            <div className="mt-3">
              <SectionError
                title="Pending confirmations could not be loaded"
                message={confirmationsError}
                onRetry={() => void fetchConfirmations()}
                retrying={confirmationsStatus === 'loading'}
                onDismiss={clearConfirmationsError}
              />
            </div>
          )}

          {confirmationsStatus === 'loading' && !confirmationsLoaded ? (
            <div className="mt-3">
              <LoadingRow label="Loading pending confirmations." />
            </div>
          ) : pendingConfirmations.length === 0 && !confirmationsError ? (
            <p className="mt-3 text-xs text-slate-400">
              No pending confirmations are stored by the server.
            </p>
          ) : (
            <div className="mt-3 space-y-3">
              {pendingConfirmations.map((confirmation) => {
                const isPending = confirmationPendingIds.includes(
                  confirmation.id
                );
                const actionable =
                  confirmation.status === 'pending' && !confirmation.isExpired;

                return (
                  <div
                    key={confirmation.id}
                    className="rounded-2xl border border-slate-800 bg-slate-950/80 p-4"
                  >
                    <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-xs font-bold text-white">
                            {confirmation.toolName}
                          </p>
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${categoryTone(
                              confirmation.category
                            )}`}
                          >
                            {toolCategoryLabel(confirmation.category)}
                          </span>
                          <StatusBadge status={confirmation.status} />
                          {confirmation.isExpired && (
                            <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-rose-300">
                              Expired
                            </span>
                          )}
                        </div>
                        <p className="mt-1 text-[11px] text-slate-500">
                          Created {formatTimestamp(confirmation.createdAt)} ·
                          Expires {formatTimestamp(confirmation.expiresAt)}
                          {confirmation.executionId
                            ? ` · Execution #${confirmation.executionId}`
                            : ''}
                        </p>
                      </div>

                      <div className="flex shrink-0 items-center gap-2">
                        <button
                          type="button"
                          onClick={() => void handleReject(confirmation.id)}
                          disabled={!actionable || isPending}
                          className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:bg-slate-700 hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isPending ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <X className="h-3.5 w-3.5" />
                          )}
                          <span>Reject</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleApprove(confirmation.id)}
                          disabled={!actionable || isPending}
                          className="inline-flex items-center gap-1.5 rounded-xl bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-amber-500 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isPending ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Check className="h-3.5 w-3.5" />
                          )}
                          <span>Approve &amp; execute</span>
                        </button>
                      </div>
                    </div>

                    <div className="mt-3 rounded-xl bg-slate-900 p-2.5 font-mono text-[11px] text-slate-300">
                      <pre className="whitespace-pre-wrap break-words">
                        {confirmation.argumentsPreview ||
                          'The server returned no argument preview.'}
                      </pre>
                    </div>

                    {!actionable && (
                      <p className="mt-2 text-[10px] text-slate-500">
                        This confirmation is no longer actionable. Refresh to
                        read the server&apos;s resolved state.
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Action result from an approval */}
      {lastActionResult && (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
            <div className="flex items-center gap-2">
              <Terminal className="h-4 w-4 text-cyan-400" />
              <h2 className="text-sm font-bold uppercase tracking-wider text-white">
                Server Result
              </h2>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-slate-400">
              <StatusBadge
                status={lastActionResult.status}
                success={lastActionResult.success}
              />
              <span className="font-mono">
                {durationLabel(lastActionResult.durationMs)}
              </span>
            </div>
          </div>
          <p className="mt-3 text-[11px] text-slate-500">
            {lastActionResult.toolName}
            {lastActionResult.confirmationId
              ? ` · Confirmation #${lastActionResult.confirmationId}`
              : ''}
          </p>
          {lastActionResult.errorMessage && (
            <div className="mt-2 rounded-xl border border-rose-500/25 bg-rose-500/10 px-3 py-2">
              <p className="text-[11px] text-rose-200">
                {lastActionResult.errorMessage}
              </p>
            </div>
          )}
          <pre className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950 p-2.5 font-mono text-[11px] text-slate-300">
            {lastActionResult.outputAvailable && lastActionResult.output
              ? lastActionResult.output
              : 'Result not retained'}
          </pre>
        </div>
      )}

      {/* Execution metrics */}
      <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5">
        <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
          <div className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-cyan-400" />
            <h2 className="text-sm font-bold uppercase tracking-wider text-white">
              Process Metrics
            </h2>
          </div>
          <button
            type="button"
            onClick={() => void fetchMetrics()}
            disabled={metricsStatus === 'loading'}
            className="text-[11px] text-slate-500 transition hover:text-slate-300 disabled:opacity-50"
          >
            Refresh
          </button>
        </div>

        {metricsError && (
          <div className="mt-3">
            <SectionError
              title="Tool metrics could not be loaded"
              message={metricsError}
              onRetry={() => void fetchMetrics()}
              retrying={metricsStatus === 'loading'}
              onDismiss={clearMetricsError}
            />
          </div>
        )}

        {metricsStatus === 'loading' && !metrics ? (
          <div className="mt-3">
            <LoadingRow label="Loading tool metrics." />
          </div>
        ) : metrics ? (
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">
                Executions
              </p>
              <p className="mt-1 font-mono text-sm font-semibold text-slate-200">
                {metrics.executions_total}
              </p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">
                Successes
              </p>
              <p className="mt-1 font-mono text-sm font-semibold text-emerald-300">
                {metrics.success_total}
              </p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">
                Failures
              </p>
              <p className="mt-1 font-mono text-sm font-semibold text-rose-300">
                {metrics.failure_total}
              </p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">
                Approved
              </p>
              <p className="mt-1 font-mono text-sm font-semibold text-slate-200">
                {metrics.confirmation_approved_total}
              </p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">
                Rejected
              </p>
              <p className="mt-1 font-mono text-sm font-semibold text-slate-200">
                {metrics.confirmation_rejected_total}
              </p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">
                Average duration
              </p>
              <p className="mt-1 font-mono text-sm font-semibold text-slate-200">
                {typeof metrics.average_duration_ms === 'number'
                  ? `${Math.round(metrics.average_duration_ms)} ms`
                  : 'Not recorded'}
              </p>
            </div>
          </div>
        ) : (
          <p className="mt-3 text-xs text-slate-400">
            Process metrics are not available.
          </p>
        )}
      </div>

      {/* Category filter */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-800 pb-3">
        <button
          type="button"
          onClick={() => setSelectedCategory('All')}
          className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
            activeCategory === 'All'
              ? 'bg-cyan-600 text-white shadow-sm'
              : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
          }`}
        >
          All categories
        </button>
        {categories.map((category) => (
          <button
            key={category}
            type="button"
            onClick={() => setSelectedCategory(category)}
            className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
              activeCategory === category
                ? 'bg-cyan-600 text-white shadow-sm'
                : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
            }`}
          >
            {toolCategoryLabel(category)}
          </button>
        ))}
      </div>

      {/* Tool cards */}
      {toolsStatus === 'loading' && !toolsLoaded ? (
        <LoadingRow label="Loading backend tool catalogue." />
      ) : toolsLoaded && tools.length === 0 ? (
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-10 text-center">
          <Wrench className="mx-auto h-10 w-10 text-slate-600" />
          <p className="mt-3 text-sm font-semibold text-slate-300">
            No tools are registered by the backend
          </p>
          <p className="mt-1 text-xs text-slate-500">
            The server returned an empty catalogue. Tools become available when
            the backend registers them.
          </p>
        </div>
      ) : toolsLoaded && filteredTools.length === 0 ? (
        <p className="py-6 text-center text-xs text-slate-500">
          No backend tools match this category.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {filteredTools.map((tool) => (
              <div
                key={tool.id}
                className={`flex flex-col justify-between rounded-2xl border p-5 shadow-lg transition ${
                  tool.enabled
                    ? 'border-slate-800/90 bg-slate-900/40 hover:border-cyan-500/40 hover:bg-slate-900/70'
                    : 'border-slate-800/40 bg-slate-950/40 opacity-60'
                }`}
              >
                <div>
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2.5">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-cyan-500/10 text-cyan-400">
                        <Wrench className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <h3 className="truncate text-xs font-bold text-white sm:text-sm">
                          {tool.name}
                        </h3>
                        <span className="font-mono text-[10px] text-slate-500">
                          {tool.backendName}
                        </span>
                      </div>
                    </div>

                    <span
                      className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                        tool.enabled
                          ? 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300'
                          : 'border-slate-700 bg-slate-800 text-slate-400'
                      }`}
                      title="Availability is controlled by server configuration"
                    >
                      {tool.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </div>

                  <p className="mt-3 text-xs leading-relaxed text-slate-400">
                    {tool.description}
                  </p>

                  <div className="mt-3.5 flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${categoryTone(
                        tool.category
                      )}`}
                    >
                      {toolCategoryLabel(tool.category)}
                    </span>

                    {tool.confirmationRequired && (
                      <span className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
                        Confirmation required
                      </span>
                    )}

                    {tool.parameters.length === 0 && (
                      <span className="rounded-md border border-slate-700 bg-slate-800/80 px-2 py-0.5 text-[10px] font-semibold text-slate-400">
                        No parameters
                      </span>
                    )}
                  </div>
                </div>

                <div className="mt-5 border-t border-slate-800/80 pt-3">
                  <button
                    type="button"
                    disabled={!tool.enabled || isEmergencyStopped || executionPending}
                    onClick={() => openRunner(tool)}
                    className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-slate-800 px-3 py-2 text-xs font-semibold text-cyan-300 transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Play className="h-3.5 w-3.5 fill-current" />
                    <span>
                      {!tool.enabled
                        ? 'Disabled by server'
                        : isEmergencyStopped
                        ? 'Blocked by emergency stop'
                        : 'Run tool'}
                    </span>
                  </button>
                </div>
              </div>
            ))}
          </div>

          <p className="text-[11px] text-slate-500">
            Tool availability is read-only in this client and is controlled by
            server configuration.
          </p>
        </>
      )}

      {/* Execution history */}
      <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-4">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-cyan-400" />
            <h2 className="text-sm font-bold uppercase tracking-wider text-white">
              Audited Execution History
            </h2>
            {historyLoaded && (
              <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                {historyTotal}
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => void fetchHistory()}
              disabled={historyStatus === 'loading'}
              className="inline-flex items-center gap-1 text-xs text-slate-500 transition hover:text-slate-300 disabled:opacity-50"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${
                  historyStatus === 'loading' ? 'animate-spin' : ''
                }`}
              />
              <span>Refresh</span>
            </button>
            {executionLogs.length > 0 && !historyViewCleared && (
              <button
                type="button"
                onClick={clearHistoryView}
                className="text-xs text-slate-500 transition hover:text-slate-300"
                title="Hide rows in this view only; the backend audit history is unchanged"
              >
                Clear view
              </button>
            )}
          </div>
        </div>

        {historyError && (
          <div className="mt-3">
            <SectionError
              title="Execution history could not be loaded"
              message={historyError}
              onRetry={() => void fetchHistory()}
              retrying={historyStatus === 'loading'}
              onDismiss={clearHistoryError}
            />
            {executionLogs.length > 0 && (
              <p className="mt-2 text-[10px] text-rose-200/60">
                The last confirmed execution history remains visible until a
                refresh succeeds.
              </p>
            )}
          </div>
        )}

        <div className="mt-3 overflow-x-auto">
          {historyViewCleared ? (
            <div className="py-8 text-center text-xs text-slate-500">
              <p>The execution view is cleared locally.</p>
              <p className="mt-1 text-[11px] text-slate-600">
                The backend audit history has not been deleted.
              </p>
              <button
                type="button"
                onClick={() => void fetchHistory()}
                disabled={historyStatus === 'loading'}
                className="mt-3 inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-semibold text-slate-300 transition hover:bg-slate-700 disabled:opacity-50"
              >
                <RefreshCw
                  className={`h-3.5 w-3.5 ${
                    historyStatus === 'loading' ? 'animate-spin' : ''
                  }`}
                />
                <span>Reload history</span>
              </button>
            </div>
          ) : historyStatus === 'loading' && !historyLoaded ? (
            <LoadingRow label="Loading audited execution history." />
          ) : executionLogs.length === 0 ? (
            <div className="py-8 text-center text-xs text-slate-500">
              <Ban className="mx-auto h-8 w-8 text-slate-600" />
              <p className="mt-2">
                No executions have been recorded by the backend.
              </p>
            </div>
          ) : (
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-[11px] font-semibold text-slate-400">
                  <th className="px-3 py-2.5">Timestamp</th>
                  <th className="px-3 py-2.5">Tool</th>
                  <th className="px-3 py-2.5">Status</th>
                  <th className="px-3 py-2.5">Duration</th>
                  <th className="px-3 py-2.5">Confirmation</th>
                  <th className="px-3 py-2.5 text-right">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50">
                {executionLogs.map((log) => (
                  <tr key={log.id} className="transition hover:bg-slate-900/50">
                    <td className="px-3 py-2.5 font-mono text-[11px] text-slate-400">
                      {formatTimestamp(log.executedAt)}
                    </td>
                    <td className="px-3 py-2.5 font-medium text-slate-200">
                      {log.toolName}
                    </td>
                    <td className="px-3 py-2.5">
                      <StatusBadge status={log.status} success={log.success} />
                    </td>
                    <td className="px-3 py-2.5 font-mono text-[11px] text-slate-400">
                      {durationLabel(log.durationMs)}
                    </td>
                    <td className="px-3 py-2.5 text-[11px] text-slate-400">
                      {log.confirmationStatus
                        ? log.confirmationStatus.replace(/_/g, ' ')
                        : 'Not recorded'}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <button
                        type="button"
                        onClick={() => void openLogDetail(log)}
                        className="font-medium text-cyan-400 transition hover:text-cyan-300"
                      >
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Runner modal */}
      {activeRunnerTool && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="relative max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <div className="min-w-0">
                <h2 className="truncate text-base font-bold text-white">
                  {activeRunnerTool.name}
                </h2>
                <p className="font-mono text-xs text-slate-400">
                  {activeRunnerTool.backendName} ·{' '}
                  {toolCategoryLabel(activeRunnerTool.category)}
                </p>
              </div>
              <button
                type="button"
                onClick={closeRunner}
                className="rounded-lg p-1 text-slate-400 transition hover:bg-slate-800 hover:text-slate-200"
                title="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={handleExecuteRunner} className="mt-4 space-y-4">
              {activeRunnerTool.parameters.length === 0 ? (
                <p className="rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-2.5 text-xs text-slate-400">
                  This tool takes no parameters according to the backend schema.
                </p>
              ) : (
                activeRunnerTool.parameters.map((parameter) => (
                  <div key={parameter.name}>
                    <label className="block text-xs font-medium text-slate-300">
                      {parameter.name}{' '}
                      {parameter.required && (
                        <span className="text-rose-400">*</span>
                      )}
                    </label>
                    {parameter.description && (
                      <p className="text-[11px] text-slate-500">
                        {parameter.description}
                      </p>
                    )}

                    {parameter.type === 'boolean' ? (
                      <select
                        value={runnerParams[parameter.name] === true ? 'true' : 'false'}
                        onChange={(event) =>
                          setRunnerParams({
                            ...runnerParams,
                            [parameter.name]: event.target.value === 'true',
                          })
                        }
                        className="mt-1 h-9 w-full rounded-xl border border-slate-800 bg-slate-900 px-3 text-xs text-white focus:border-cyan-500/60 focus:outline-none"
                      >
                        <option value="true">true</option>
                        <option value="false">false</option>
                      </select>
                    ) : parameter.enumValues && parameter.enumValues.length > 0 ? (
                      <select
                        value={String(runnerParams[parameter.name] ?? '')}
                        onChange={(event) =>
                          setRunnerParams({
                            ...runnerParams,
                            [parameter.name]: event.target.value,
                          })
                        }
                        required={parameter.required}
                        className="mt-1 h-9 w-full rounded-xl border border-slate-800 bg-slate-900 px-3 text-xs text-white focus:border-cyan-500/60 focus:outline-none"
                      >
                        <option value="">Select…</option>
                        {parameter.enumValues.map((value) => (
                          <option key={String(value)} value={String(value)}>
                            {String(value)}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type={parameter.type === 'number' ? 'number' : 'text'}
                        value={String(runnerParams[parameter.name] ?? '')}
                        onChange={(event) =>
                          setRunnerParams({
                            ...runnerParams,
                            [parameter.name]: event.target.value,
                          })
                        }
                        required={parameter.required}
                        className="mt-1 h-9 w-full rounded-xl border border-slate-800 bg-slate-900 px-3 text-xs text-white focus:border-cyan-500/60 focus:outline-none"
                      />
                    )}
                  </div>
                ))
              )}

              {activeRunnerTool.confirmationRequired && (
                <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
                  The backend requires confirmation before this action runs.
                </p>
              )}

              {runnerError && (
                <div
                  role="alert"
                  className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200"
                >
                  {runnerError}
                </div>
              )}

              <div className="mt-6 flex justify-end gap-2.5 border-t border-slate-800 pt-4">
                <button
                  type="button"
                  onClick={closeRunner}
                  className="rounded-xl border border-slate-800 px-4 py-2 text-xs font-medium text-slate-300 transition hover:bg-slate-800"
                >
                  Close
                </button>
                <button
                  type="submit"
                  disabled={executionPending || isEmergencyStopped}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-cyan-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {executionPending ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      <span>Executing…</span>
                    </>
                  ) : (
                    <>
                      <Play className="h-3.5 w-3.5 fill-current" />
                      <span>Run tool</span>
                    </>
                  )}
                </button>
              </div>
            </form>

            {runnerResult && (
              <div className="mt-4 rounded-2xl border border-slate-800 bg-slate-900/80 p-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs font-semibold text-slate-300">
                  <span>Backend execution result</span>
                  <div className="flex items-center gap-2">
                    <StatusBadge
                      status={runnerResult.status}
                      success={runnerResult.success}
                    />
                    <span className="font-mono text-[10px] text-slate-400">
                      {durationLabel(runnerResult.durationMs)}
                    </span>
                  </div>
                </div>

                {runnerResult.errorMessage && (
                  <p className="mt-2 rounded-lg border border-rose-500/25 bg-rose-500/10 px-2.5 py-1.5 text-[11px] text-rose-200">
                    {runnerResult.errorMessage}
                  </p>
                )}

                <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950 p-2.5 font-mono text-[11px] text-slate-300">
                  {runnerResult.outputAvailable && runnerResult.output
                    ? runnerResult.output
                    : 'Output not available'}
                </pre>

                {runnerResult.confirmationId &&
                  runnerResult.status === 'confirmation_required' && (
                    <p className="mt-2 text-[10px] text-amber-300">
                      Confirmation #{runnerResult.confirmationId} was created by
                      the server. Review it in the Pending Confirmations panel.
                    </p>
                  )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Recovery modal */}
      {showRecovery && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-md rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <h2 className="text-sm font-bold text-white">
                Recover emergency stop
              </h2>
              <button
                type="button"
                onClick={() => setShowRecovery(false)}
                className="rounded-lg p-1 text-slate-400 transition hover:bg-slate-800 hover:text-slate-200"
                title="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <p className="mt-3 text-xs text-slate-400">
              Clearing the emergency stop requires the recovery token configured
              on the server. Authentication alone is not sufficient.
            </p>

            {emergencyStop && !emergencyStop.recovery_configured && (
              <p className="mt-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
                The server reports that emergency-stop recovery is not
                configured. The recovery request will be rejected.
              </p>
            )}

            <form onSubmit={handleRecovery} className="mt-4 space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-300">
                  Recovery token
                </label>
                <input
                  type="password"
                  value={recoveryToken}
                  onChange={(event) => setRecoveryToken(event.target.value)}
                  autoComplete="off"
                  required
                  className="mt-1 h-9 w-full rounded-xl border border-slate-800 bg-slate-900 px-3 font-mono text-xs text-white focus:border-rose-500/60 focus:outline-none"
                />
              </div>

              {recoveryError && (
                <p
                  role="alert"
                  className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200"
                >
                  {recoveryError}
                </p>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowRecovery(false)}
                  className="rounded-xl border border-slate-800 px-4 py-2 text-xs font-medium text-slate-300 transition hover:bg-slate-800"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={emergencyStopPending || !recoveryToken.trim()}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-rose-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {emergencyStopPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <KeyRound className="h-3.5 w-3.5" />
                  )}
                  <span>Recover</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* History detail modal */}
      {viewLogDetail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="relative max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-3xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div>
                <h2 className="text-sm font-bold text-white">
                  Execution{' '}
                  {typeof viewLogDetail.executionId === 'number'
                    ? `#${viewLogDetail.executionId}`
                    : 'record'}
                </h2>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  Metadata retained by the backend
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setViewLogDetail(null);
                  setViewLogError(null);
                  setViewLogLoading(false);
                }}
                className="rounded-lg p-1 text-slate-400 transition hover:bg-slate-800 hover:text-slate-200"
                title="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-3 space-y-3 text-xs">
              {viewLogLoading && (
                <div className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-2 text-[11px] text-slate-400">
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-400" />
                  <span>Refreshing this record from the backend…</span>
                </div>
              )}

              {viewLogError && (
                <div
                  role="alert"
                  className="flex flex-col gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
                >
                  <p className="text-[11px] text-rose-200">{viewLogError}</p>
                  <button
                    type="button"
                    onClick={() => {
                      if (viewLogDetail) void openLogDetail(viewLogDetail);
                    }}
                    disabled={viewLogLoading}
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-rose-500/40 bg-rose-500/10 px-2.5 py-1 text-[11px] font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <RefreshCw className="h-3 w-3" />
                    <span>Retry</span>
                  </button>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-white">
                  {viewLogDetail.toolName}
                </span>
                {viewLogDetail.category && (
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${categoryTone(
                      viewLogDetail.category
                    )}`}
                  >
                    {toolCategoryLabel(viewLogDetail.category)}
                  </span>
                )}
                <StatusBadge
                  status={viewLogDetail.status}
                  success={viewLogDetail.success}
                />
              </div>

              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <div>
                  <span className="text-slate-400">Started:</span>{' '}
                  <span className="font-mono text-slate-300">
                    {formatTimestamp(viewLogDetail.startedAt)}
                  </span>
                </div>
                <div>
                  <span className="text-slate-400">Completed:</span>{' '}
                  <span className="font-mono text-slate-300">
                    {formatTimestamp(viewLogDetail.completedAt)}
                  </span>
                </div>
                <div>
                  <span className="text-slate-400">Duration:</span>{' '}
                  <span className="font-mono text-slate-300">
                    {durationLabel(viewLogDetail.durationMs)}
                  </span>
                </div>
                <div>
                  <span className="text-slate-400">Lifecycle:</span>{' '}
                  <span className="font-mono text-slate-300">
                    {viewLogDetail.lifecycleStatus
                      ? viewLogDetail.lifecycleStatus.replace(/_/g, ' ')
                      : 'Not recorded'}
                  </span>
                </div>
                <div>
                  <span className="text-slate-400">Confirmation:</span>{' '}
                  <span className="font-mono text-slate-300">
                    {viewLogDetail.confirmationStatus
                      ? viewLogDetail.confirmationStatus.replace(/_/g, ' ')
                      : 'Not recorded'}
                  </span>
                </div>
                <div>
                  <span className="text-slate-400">Provider:</span>{' '}
                  <span className="font-mono text-slate-300">
                    {viewLogDetail.provider || 'Not recorded'}
                  </span>
                </div>
                <div>
                  <span className="text-slate-400">Result size:</span>{' '}
                  <span className="font-mono text-slate-300">
                    {typeof viewLogDetail.resultBytes === 'number'
                      ? `${viewLogDetail.resultBytes} bytes`
                      : 'Not recorded'}
                  </span>
                </div>
                <div>
                  <span className="text-slate-400">Truncated:</span>{' '}
                  <span className="font-mono text-slate-300">
                    {viewLogDetail.truncated ? 'Yes' : 'No'}
                  </span>
                </div>
              </div>

              {(viewLogDetail.errorCode ||
                viewLogDetail.errorCategory ||
                viewLogDetail.errorMessage) && (
                <div className="rounded-xl border border-rose-500/25 bg-rose-500/10 px-3 py-2">
                  {viewLogDetail.errorCategory && (
                    <p className="font-mono text-[10px] font-semibold uppercase text-rose-300">
                      {viewLogDetail.errorCategory}
                    </p>
                  )}
                  {viewLogDetail.errorCode && (
                    <p className="font-mono text-[10px] text-rose-200">
                      {viewLogDetail.errorCode}
                    </p>
                  )}
                  {viewLogDetail.errorMessage && (
                    <p className="mt-0.5 text-[11px] text-rose-200/80">
                      {viewLogDetail.errorMessage}
                    </p>
                  )}
                </div>
              )}

              <div>
                <span className="text-slate-400">Argument fields:</span>
                <pre className="mt-1 whitespace-pre-wrap break-words rounded-lg bg-slate-900 p-2 font-mono text-[11px] text-slate-300">
                  {viewLogDetail.argumentKeys.length > 0
                    ? viewLogDetail.argumentKeys.join(', ')
                    : 'No argument fields recorded'}
                </pre>
                <p className="mt-1 text-[10px] text-slate-600">
                  Argument values are never retained by the backend.
                </p>
              </div>

              <div>
                <span className="text-slate-400">Output:</span>
                <pre className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-slate-900 p-2 font-mono text-[11px] text-slate-300">
                  {viewLogDetail.outputAvailable && viewLogDetail.output
                    ? viewLogDetail.output
                    : 'Result not retained'}
                </pre>
                <p className="mt-1 text-[10px] text-slate-600">
                  Execution history stores metadata only; tool output is not
                  retained by the backend.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Tools;
