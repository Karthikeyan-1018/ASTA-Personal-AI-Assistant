import React, { useEffect } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  HardDrive,
  MemoryStick,
  RefreshCw,
  Server,
  Users,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useSystemStore } from '../stores/system.store';
import type {
  HistoryPoint,
  SystemMonitorStatus,
  SystemSnapshot,
} from '../types/system.types';
import { firstAvailableDiskPercent } from '../utils/systemMetrics';

const STATUS_LABELS: Record<SystemMonitorStatus, string> = {
  idle: 'Idle',
  loading: 'Loading',
  refreshing: 'Refreshing',
  connected: 'Connected',
  stale: 'Stale',
  unavailable: 'Unavailable',
};

const STATUS_TONES: Record<SystemMonitorStatus, string> = {
  idle: 'border-slate-700 bg-slate-800 text-slate-300',
  loading: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-300',
  refreshing: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
  connected: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  stale: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  unavailable: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
};

const UNSUPPORTED_METRICS = [
  { label: 'Host CPU utilization', reason: 'No backend endpoint reports a system-wide CPU percentage.' },
  { label: 'Network throughput', reason: 'No backend endpoint reports network counters.' },
  { label: 'System uptime', reason: 'No backend endpoint reports host boot time.' },
  { label: 'Temperature', reason: 'No backend endpoint reports thermal sensors.' },
  { label: 'GPU utilization / memory', reason: 'No backend endpoint reports GPU measurements.' },
];

function formatPercent(value: number | null): string {
  return value === null ? 'Unavailable' : `${value}%`;
}

function formatGib(value: number | null): string {
  return value === null ? 'Unavailable' : `${value.toFixed(1)} GiB`;
}

function formatMib(value: number | null): string {
  return value === null ? 'Unavailable' : `${value.toFixed(0)} MiB`;
}

function formatCount(value: number | null): string {
  return value === null ? 'Unavailable' : String(value);
}

function formatDateTime(value: string | null): string {
  if (!value) return 'Unavailable';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatClock(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
}

function barWidth(value: number | null): number {
  if (value === null) return 0;
  return Math.min(100, Math.max(0, value));
}

function Card({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-5 shadow-lg">
      <div className="flex items-center gap-2 text-slate-300">
        {icon}
        <h3 className="text-xs font-bold uppercase tracking-wider">{title}</h3>
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}

function Unavailable({ reason }: { reason?: string | null }) {
  return (
    <div className="space-y-1">
      <p className="text-sm font-semibold text-slate-300">Unavailable</p>
      {reason && <p className="text-[11px] leading-relaxed text-slate-500">{reason}</p>}
    </div>
  );
}

function ResourceBar({ value }: { value: number | null }) {
  if (value === null) {
    return <div className="mt-3 h-2 w-full rounded-full bg-slate-800" />;
  }
  return (
    <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-800">
      <div
        className="h-full rounded-full bg-indigo-500"
        style={{ width: `${barWidth(value)}%` }}
      />
    </div>
  );
}

function HostMetricCards({ snapshot }: { snapshot: SystemSnapshot }) {
  const diskPercent = firstAvailableDiskPercent(snapshot);

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
      <Card title="CPU" icon={<Cpu className="h-4 w-4 text-cyan-400" />}>
        {snapshot.cpu.available ? (
          <div className="space-y-1.5">
            <p className="text-sm font-semibold text-slate-100">
              {snapshot.cpu.model || 'Processor model unavailable'}
            </p>
            <p className="text-[11px] text-slate-400">
              {formatCount(snapshot.cpu.logicalProcessors)} logical processors ·{' '}
              {formatCount(snapshot.cpu.physicalCores)} physical cores
            </p>
            <p className="text-[11px] text-amber-300/80">
              Utilization unavailable — the backend does not report host CPU load.
            </p>
          </div>
        ) : (
          <Unavailable reason={snapshot.cpu.reason} />
        )}
        <ResourceBar value={null} />
      </Card>

      <Card title="Memory" icon={<MemoryStick className="h-4 w-4 text-indigo-400" />}>
        {snapshot.memory.available ? (
          <>
            <p className="text-2xl font-bold text-white">
              {formatPercent(snapshot.memory.usagePercent)}
            </p>
            <p className="mt-1 text-[11px] text-slate-400">
              {formatGib(snapshot.memory.usedGib)} of {formatGib(snapshot.memory.totalGib)} used
            </p>
            <p className="text-[11px] text-slate-500">
              {formatGib(snapshot.memory.availableGib)} available
            </p>
            <ResourceBar value={snapshot.memory.usagePercent} />
          </>
        ) : (
          <Unavailable reason={snapshot.memory.reason} />
        )}
      </Card>

      <Card title="Disk" icon={<HardDrive className="h-4 w-4 text-emerald-400" />}>
        {snapshot.disk.available ? (
          <div className="space-y-3">
            <p className="text-2xl font-bold text-white">{formatPercent(diskPercent)}</p>
            {snapshot.disk.drives.map((drive) => (
              <div key={drive.root} className="border-t border-slate-800 pt-2 first:border-0 first:pt-0">
                <p className="text-[11px] font-semibold text-slate-200">
                  {drive.label || drive.root} · {drive.root}
                </p>
                {drive.available ? (
                  <>
                    <p className="text-[11px] text-slate-400">
                      {formatGib(drive.usedGib)} of {formatGib(drive.totalGib)} used
                    </p>
                    <p className="text-[11px] text-slate-500">
                      {formatGib(drive.availableGib)} available
                    </p>
                    <ResourceBar value={drive.usagePercent} />
                  </>
                ) : (
                  <p className="text-[11px] text-slate-500">
                    Unavailable{drive.error ? ` — ${drive.error}` : ''}
                  </p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <Unavailable reason={snapshot.disk.reason} />
        )}
      </Card>

      <Card title="Processes" icon={<Users className="h-4 w-4 text-amber-400" />}>
        {snapshot.processes.available ? (
          <div className="space-y-2">
            <p className="text-2xl font-bold text-white">
              {formatCount(snapshot.processes.totalConsidered ?? snapshot.processes.count)}
            </p>
            <p className="text-[11px] text-slate-500">
              Read-only sample · {formatCount(snapshot.processes.count)} shown
              {snapshot.processes.truncated ? ' (truncated)' : ''}
            </p>
            <p className="text-[11px] text-slate-500">
              CPU sample {formatCount(snapshot.processes.cpuSampleMs)} ms
            </p>
          </div>
        ) : (
          <Unavailable reason={snapshot.processes.reason} />
        )}
      </Card>
    </div>
  );
}

function BackendHealthCards({ snapshot }: { snapshot: SystemSnapshot }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card title="Provider health" icon={<Server className="h-4 w-4 text-emerald-400" />}>
        {snapshot.providers?.available ? (
          <div className="space-y-2">
            <p className="text-sm font-semibold capitalize text-slate-100">
              {snapshot.providers.overallStatus || 'Unavailable'}
            </p>
            <div className="flex flex-wrap gap-2 text-[11px]">
              <span className="rounded bg-slate-800 px-2 py-1 text-slate-300">
                Groq: {snapshot.providers.groq || 'Unavailable'}
              </span>
              <span className="rounded bg-slate-800 px-2 py-1 text-slate-300">
                Ollama: {snapshot.providers.ollama || 'Unavailable'}
              </span>
            </div>
            <p className="text-[10px] text-slate-500">
              Reported separately from host measurements.
            </p>
          </div>
        ) : (
          <Unavailable reason="Provider health could not be confirmed by the backend." />
        )}
      </Card>

      <Card title="Tool runtime" icon={<Activity className="h-4 w-4 text-indigo-400" />}>
        {snapshot.toolRuntime?.available ? (
          <div className="grid grid-cols-2 gap-3 text-[11px] text-slate-400">
            <p>Executions: <span className="text-slate-100">{formatCount(snapshot.toolRuntime.executionsTotal)}</span></p>
            <p>Succeeded: <span className="text-slate-100">{formatCount(snapshot.toolRuntime.successTotal)}</span></p>
            <p>Failed: <span className="text-slate-100">{formatCount(snapshot.toolRuntime.failureTotal)}</span></p>
            <p>Average: <span className="text-slate-100">{snapshot.toolRuntime.averageDurationMs === null ? 'Unavailable' : `${snapshot.toolRuntime.averageDurationMs} ms`}</span></p>
            <p className="col-span-2">
              Maximum: <span className="text-slate-100">{snapshot.toolRuntime.maxDurationMs === null ? 'Unavailable' : `${snapshot.toolRuntime.maxDurationMs} ms`}</span>
            </p>
            <p className="col-span-2" title={snapshot.toolRuntime.since || undefined}>
              Since: <span className="text-slate-100">{formatDateTime(snapshot.toolRuntime.since)}</span>
            </p>
          </div>
        ) : (
          <Unavailable reason="Tool runtime counters could not be confirmed by the backend." />
        )}
      </Card>
    </div>
  );
}

function SessionHistory({ points }: { points: HistoryPoint[] }) {
  if (points.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-800 bg-slate-950/40 p-6 text-center">
        <p className="text-xs font-semibold text-slate-300">No session history yet</p>
        <p className="mt-1 text-[11px] text-slate-500">
          Trends are built only from confirmed backend measurements collected during this session.
        </p>
      </div>
    );
  }

  const data = points.map((point) => ({
    time: formatClock(point.collectedAt),
    memory: point.memoryPercent,
    disk: point.diskPercent,
  }));

  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="monitorMemoryGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#818cf8" stopOpacity={0.4} />
              <stop offset="95%" stopColor="#818cf8" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="monitorDiskGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#34d399" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#34d399" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
          <XAxis dataKey="time" stroke="#64748b" fontSize={10} />
          <YAxis domain={[0, 100]} stroke="#64748b" fontSize={10} unit="%" />
          <Tooltip
            contentStyle={{
              backgroundColor: '#020617',
              borderColor: '#334155',
              borderRadius: '0.75rem',
              fontSize: '11px',
            }}
          />
          <Area
            type="monotone"
            dataKey="memory"
            name="Memory %"
            stroke="#818cf8"
            fill="url(#monitorMemoryGradient)"
            connectNulls={false}
          />
          <Area
            type="monotone"
            dataKey="disk"
            name="Disk %"
            stroke="#34d399"
            fill="url(#monitorDiskGradient)"
            connectNulls={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export const SystemMonitor: React.FC = () => {
  const snapshot = useSystemStore((s) => s.snapshot);
  const history = useSystemStore((s) => s.history);
  const status = useSystemStore((s) => s.status);
  const isLoading = useSystemStore((s) => s.isLoading);
  const isRefreshing = useSystemStore((s) => s.isRefreshing);
  const isPolling = useSystemStore((s) => s.isPolling);
  const error = useSystemStore((s) => s.error);
  const lastSuccessfulAt = useSystemStore((s) => s.lastSuccessfulAt);
  const refresh = useSystemStore((s) => s.refresh);
  const retry = useSystemStore((s) => s.retry);
  const refreshIfStale = useSystemStore((s) => s.refreshIfStale);
  const startPolling = useSystemStore((s) => s.startPolling);
  const stopPolling = useSystemStore((s) => s.stopPolling);

  useEffect(() => {
    void refresh({ reason: 'initial' });
    startPolling();

    const handleFocus = () => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') {
        void refreshIfStale();
      }
    };

    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleFocus);

    return () => {
      stopPolling();
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleFocus);
    };
  }, [refresh, refreshIfStale, startPolling, stopPolling]);

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
            System Monitor
          </h1>
          <p className="mt-1 text-xs text-slate-400">
            Read-only measurements reported by the ASTA backend. No estimated values are shown.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[11px] font-semibold ${STATUS_TONES[status]}`}>
            {status === 'connected' ? (
              <CheckCircle2 className="h-3.5 w-3.5" />
            ) : status === 'unavailable' || status === 'stale' ? (
              <AlertTriangle className="h-3.5 w-3.5" />
            ) : (
              <RefreshCw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
            )}
            {STATUS_LABELS[status]}
          </span>
          <button
            type="button"
            onClick={() => { void refresh({ reason: 'manual' }); }}
            disabled={isRefreshing || isLoading}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-800 px-3 py-2 text-xs font-semibold text-slate-300 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
        <span>Auto-refresh: every 20s</span>
        <span>{isPolling ? 'Polling active' : 'Polling stopped'}</span>
        <span>Last successful update: {formatDateTime(lastSuccessfulAt)}</span>
        {status === 'stale' && (
          <span className="text-amber-300/80">
            System metrics are currently unavailable. Displaying the last confirmed
            measurements; refresh failed.
          </span>
        )}
      </div>

      {error && (
        <div
          role="alert"
          className="flex flex-col justify-between gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 sm:flex-row sm:items-center"
        >
          <div>
            <p className="text-xs font-semibold text-rose-200">Metrics refresh failed</p>
            <p className="mt-1 text-[11px] text-rose-200/80">{error}</p>
          </div>
          <button
            type="button"
            onClick={() => { void retry(); }}
            disabled={isRefreshing || isLoading}
            className="shrink-0 rounded-xl bg-rose-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Retry
          </button>
        </div>
      )}

      {isLoading && (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-6 text-center">
          <p className="text-sm font-semibold text-slate-200">Loading system metrics…</p>
          <p className="mt-1 text-[11px] text-slate-500">
            Waiting for the backend to confirm measurements.
          </p>
        </div>
      )}

      {!isLoading && !snapshot && status === 'unavailable' && (
        <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-6 text-center">
          <p className="text-sm font-semibold text-rose-200">
            System metrics are currently unavailable.
          </p>
          <p className="mt-1 text-[11px] text-rose-200/70">
            No backend measurement could be confirmed. Retry to request them again.
          </p>
        </div>
      )}

      {snapshot && (
        <>
          <HostMetricCards snapshot={snapshot} />

          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-5 shadow-lg">
            <h2 className="text-sm font-bold uppercase tracking-wider text-white">
              Top processes (read-only)
            </h2>
            {snapshot.processes.available && snapshot.processes.processes.length > 0 ? (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left text-[11px]">
                  <thead className="text-slate-500">
                    <tr>
                      <th className="py-2 pr-3">PID</th>
                      <th className="py-2 pr-3">Name</th>
                      <th className="py-2 pr-3">CPU</th>
                      <th className="py-2 pr-3">Memory</th>
                      <th className="py-2">Threads</th>
                    </tr>
                  </thead>
                  <tbody className="text-slate-300">
                    {snapshot.processes.processes.map((process) => (
                      <tr key={process.pid} className="border-t border-slate-800">
                        <td className="py-2 pr-3 font-mono">{process.pid}</td>
                        <td className="py-2 pr-3">{process.name}</td>
                        <td className="py-2 pr-3">
                          {process.cpuAvailable ? formatPercent(process.cpuPercent) : 'Unavailable'}
                        </td>
                        <td className="py-2 pr-3">
                          {process.memoryAvailable ? formatMib(process.memoryMib) : 'Unavailable'}
                        </td>
                        <td className="py-2">{formatCount(process.threadCount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="mt-2 text-[11px] text-slate-500">
                {snapshot.processes.reason || 'No process measurements were reported.'}
              </p>
            )}
            <p className="mt-3 text-[10px] text-slate-500">
              The backend exposes read-only process inspection only; termination is not available.
            </p>
          </div>

          <BackendHealthCards snapshot={snapshot} />

          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-5 shadow-lg">
            <h2 className="text-sm font-bold uppercase tracking-wider text-white">
              Unsupported measurements
            </h2>
            <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
              {UNSUPPORTED_METRICS.map((item) => (
                <div key={item.label} className="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
                  <p className="text-[11px] font-semibold text-slate-300">{item.label}: Unavailable</p>
                  <p className="mt-1 text-[10px] leading-relaxed text-slate-500">{item.reason}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-5 shadow-lg">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-bold uppercase tracking-wider text-white">
                Session history
              </h2>
              <span className="text-[10px] text-slate-500">Real measurements only</span>
            </div>
            <div className="mt-3">
              <SessionHistory points={history} />
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default SystemMonitor;
