import React, { useEffect, useState } from 'react';
import {
  Activity,
  Cpu,
  HardDrive,
  RefreshCw,
  Server,
  Sparkles,
  StopCircle,
} from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts';
import { useSystemStore } from '../stores/system.store';

export const SystemMonitor: React.FC = () => {
  const metrics = useSystemStore((s) => s.metrics);
  const processes = useSystemStore((s) => s.processes);
  const services = useSystemStore((s) => s.services);
  const refreshTelemetry = useSystemStore((s) => s.refreshTelemetry);
  const terminateProcess = useSystemStore((s) => s.terminateProcess);

  const [autoRefresh, setAutoRefresh] = useState(true);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = window.setInterval(() => {
      refreshTelemetry();
    }, 3500);
    return () => window.clearInterval(interval);
  }, [autoRefresh, refreshTelemetry]);

  const latest = metrics[metrics.length - 1] || {
    cpuPercent: 14,
    memoryPercent: 46,
    memoryUsedGb: 14.8,
    memoryTotalGb: 32,
    diskUsedGb: 413,
    diskTotalGb: 1024,
  };

  const diskPercent = Math.round((latest.diskUsedGb / latest.diskTotalGb) * 100);

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">
            System Telemetry & Health
          </h1>
          <p className="text-xs sm:text-sm text-slate-400">
            Real-time simulated telemetry for CPU, memory, background processes, and AI engines.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded border-slate-700 bg-slate-900 text-indigo-500"
            />
            <span>Auto-Stream (3.5s)</span>
          </label>

          <button
            type="button"
            onClick={refreshTelemetry}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-slate-800"
          >
            <RefreshCw className="h-3.5 w-3.5 text-indigo-400" />
            <span>Poll Now</span>
          </button>
        </div>
      </div>

      {/* Demonstration Data Warning */}
      <div className="rounded-2xl border border-indigo-500/30 bg-indigo-500/10 p-4">
        <div className="flex items-center gap-2.5 text-xs text-indigo-300">
          <Sparkles className="h-4 w-4 shrink-0 text-indigo-400" />
          <span>
            <strong>DEMONSTRATION METRICS ONLY:</strong> Telemetry values and process entries are
            simulated for this frontend milestone without reading host OS kernel metrics.
          </span>
        </div>
      </div>

      {/* Primary Resource Gauges */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {/* CPU Card */}
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-5 shadow-lg">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">CPU Load</span>
            <div className="rounded-lg bg-indigo-500/10 p-2 text-indigo-400">
              <Cpu className="h-4 w-4" />
            </div>
          </div>
          <div className="mt-4 flex items-baseline gap-2">
            <span className="text-3xl font-extrabold text-white">{latest.cpuPercent}%</span>
            <span className="text-xs text-slate-500">16 Threads</span>
          </div>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-cyan-400 transition-all duration-500"
              style={{ width: `${latest.cpuPercent}%` }}
            />
          </div>
        </div>

        {/* Memory Card */}
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-5 shadow-lg">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">RAM Allocation</span>
            <div className="rounded-lg bg-emerald-500/10 p-2 text-emerald-400">
              <Activity className="h-4 w-4" />
            </div>
          </div>
          <div className="mt-4 flex items-baseline gap-2">
            <span className="text-3xl font-extrabold text-white">{latest.memoryPercent}%</span>
            <span className="text-xs text-slate-500">
              {latest.memoryUsedGb} / {latest.memoryTotalGb} GB
            </span>
          </div>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-teal-400 transition-all duration-500"
              style={{ width: `${latest.memoryPercent}%` }}
            />
          </div>
        </div>

        {/* Storage Card */}
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-5 shadow-lg">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">NVMe Storage</span>
            <div className="rounded-lg bg-cyan-500/10 p-2 text-cyan-400">
              <HardDrive className="h-4 w-4" />
            </div>
          </div>
          <div className="mt-4 flex items-baseline gap-2">
            <span className="text-3xl font-extrabold text-white">{diskPercent}%</span>
            <span className="text-xs text-slate-500">
              {latest.diskUsedGb} / {latest.diskTotalGb} GB
            </span>
          </div>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-cyan-500 transition-all duration-500"
              style={{ width: `${diskPercent}%` }}
            />
          </div>
        </div>

        {/* Services Status Card */}
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/40 p-5 shadow-lg">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">Engine Services</span>
            <div className="rounded-lg bg-violet-500/10 p-2 text-violet-400">
              <Server className="h-4 w-4" />
            </div>
          </div>
          <div className="mt-4 flex items-baseline gap-2">
            <span className="text-3xl font-extrabold text-white">{services.length} / 5</span>
            <span className="text-xs text-emerald-400 font-semibold">Active Ready</span>
          </div>
          <p className="mt-3 text-xs text-slate-500 truncate">
            FastAPI, pgvector, Groq, Ollama
          </p>
        </div>
      </div>

      {/* Telemetry Charts: CPU and Memory Load Over Time */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* CPU Chart */}
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
          <div className="flex items-center justify-between pb-3 border-b border-slate-800/80">
            <span className="text-xs font-bold uppercase tracking-wider text-white">
              CPU Utilization Trend (%)
            </span>
            <span className="font-mono text-xs text-indigo-400 font-semibold">
              {latest.cpuPercent}% Current
            </span>
          </div>

          <div className="mt-4 h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={metrics}>
                <defs>
                  <linearGradient id="cpuGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="timestamp" stroke="#64748b" fontSize={10} tickLine={false} />
                <YAxis domain={[0, 100]} stroke="#64748b" fontSize={10} tickLine={false} />
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
                  dataKey="cpuPercent"
                  stroke="#6366f1"
                  strokeWidth={2}
                  fillOpacity={1}
                  fill="url(#cpuGradient)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Memory Chart */}
        <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
          <div className="flex items-center justify-between pb-3 border-b border-slate-800/80">
            <span className="text-xs font-bold uppercase tracking-wider text-white">
              Memory Utilization Trend (%)
            </span>
            <span className="font-mono text-xs text-emerald-400 font-semibold">
              {latest.memoryPercent}% Current
            </span>
          </div>

          <div className="mt-4 h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={metrics}>
                <defs>
                  <linearGradient id="memGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="timestamp" stroke="#64748b" fontSize={10} tickLine={false} />
                <YAxis domain={[0, 100]} stroke="#64748b" fontSize={10} tickLine={false} />
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
                  dataKey="memoryPercent"
                  stroke="#10b981"
                  strokeWidth={2}
                  fillOpacity={1}
                  fill="url(#memGradient)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Services Status Table */}
      <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
        <h2 className="text-sm font-bold uppercase tracking-wider text-white mb-3">
          Core Engine & Backend Services
        </h2>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {services.map((svc) => (
            <div
              key={svc.name}
              className="rounded-xl border border-slate-800 bg-slate-950/70 p-4 space-y-2"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-slate-100">{svc.name}</span>
                <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-300">
                  {svc.status}
                </span>
              </div>
              <p className="text-[11px] text-slate-400">{svc.details}</p>
              <div className="flex items-center justify-between border-t border-slate-800/80 pt-2 text-[10px] font-mono text-slate-500">
                <span>{svc.version}</span>
                <span>{svc.latencyMs}ms ping</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Process Table */}
      <div className="rounded-2xl border border-slate-800/80 bg-slate-900/30 p-5 shadow-lg">
        <div className="flex items-center justify-between pb-3 border-b border-slate-800/80">
          <h2 className="text-sm font-bold uppercase tracking-wider text-white">
            Active Workspace Process Table
          </h2>
          <span className="text-xs text-slate-500">{processes.length} processes listed</span>
        </div>

        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-[11px] font-semibold text-slate-400">
                <th className="py-2.5 px-3">PID</th>
                <th className="py-2.5 px-3">Process Name</th>
                <th className="py-2.5 px-3">CPU %</th>
                <th className="py-2.5 px-3">Memory (MB)</th>
                <th className="py-2.5 px-3">User</th>
                <th className="py-2.5 px-3">Status</th>
                <th className="py-2.5 px-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {processes.map((proc) => (
                <tr key={proc.pid} className="hover:bg-slate-900/50 transition">
                  <td className="py-2.5 px-3 font-mono text-slate-400 text-[11px]">{proc.pid}</td>
                  <td className="py-2.5 px-3 font-medium text-slate-200">{proc.name}</td>
                  <td className="py-2.5 px-3 font-mono text-indigo-300">{proc.cpuPercent}%</td>
                  <td className="py-2.5 px-3 font-mono text-slate-300">{proc.memoryMb} MB</td>
                  <td className="py-2.5 px-3 text-slate-400">{proc.user}</td>
                  <td className="py-2.5 px-3">
                    <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-medium capitalize text-slate-300">
                      {proc.status}
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    <button
                      type="button"
                      onClick={() => terminateProcess(proc.pid)}
                      className="inline-flex items-center gap-1 text-[11px] text-rose-400 hover:text-rose-300"
                      title="Simulate process termination"
                    >
                      <StopCircle className="h-3.5 w-3.5" />
                      <span>End</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default SystemMonitor;
