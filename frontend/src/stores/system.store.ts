import { create } from 'zustand';
import type { SystemResourceMetric, SystemProcess, BackendServiceStatus } from '../types/system.types';

const INITIAL_METRICS: SystemResourceMetric[] = [
  { timestamp: '18:50', cpuPercent: 12, memoryPercent: 44, memoryUsedGb: 14.1, memoryTotalGb: 32, diskUsedGb: 412, diskTotalGb: 1024 },
  { timestamp: '18:55', cpuPercent: 18, memoryPercent: 45, memoryUsedGb: 14.4, memoryTotalGb: 32, diskUsedGb: 412, diskTotalGb: 1024 },
  { timestamp: '19:00', cpuPercent: 15, memoryPercent: 46, memoryUsedGb: 14.7, memoryTotalGb: 32, diskUsedGb: 412, diskTotalGb: 1024 },
  { timestamp: '19:05', cpuPercent: 28, memoryPercent: 48, memoryUsedGb: 15.3, memoryTotalGb: 32, diskUsedGb: 413, diskTotalGb: 1024 },
  { timestamp: '19:10', cpuPercent: 22, memoryPercent: 47, memoryUsedGb: 15.0, memoryTotalGb: 32, diskUsedGb: 413, diskTotalGb: 1024 },
  { timestamp: '19:15', cpuPercent: 16, memoryPercent: 46, memoryUsedGb: 14.8, memoryTotalGb: 32, diskUsedGb: 413, diskTotalGb: 1024 },
  { timestamp: '19:20', cpuPercent: 14, memoryPercent: 46, memoryUsedGb: 14.8, memoryTotalGb: 32, diskUsedGb: 413, diskTotalGb: 1024 },
];

const INITIAL_PROCESSES: SystemProcess[] = [
  { pid: 14220, name: 'python.exe (FastAPI ASTA Core)', cpuPercent: 1.8, memoryMb: 285, status: 'running', user: 'SYSTEM' },
  { pid: 8940, name: 'node.exe (Vite Dev Server)', cpuPercent: 0.6, memoryMb: 142, status: 'running', user: 'karthik' },
  { pid: 1104, name: 'postgres.exe (PostgreSQL 18)', cpuPercent: 0.4, memoryMb: 340, status: 'idle', user: 'postgres' },
  { pid: 19828, name: 'ollama_llama_server.exe', cpuPercent: 0.2, memoryMb: 4200, status: 'idle', user: 'karthik' },
  { pid: 6512, name: 'pgvector_worker.exe', cpuPercent: 0.1, memoryMb: 118, status: 'sleeping', user: 'postgres' },
  { pid: 4320, name: 'powershell.exe', cpuPercent: 0.0, memoryMb: 82, status: 'idle', user: 'karthik' },
];

const INITIAL_SERVICES: BackendServiceStatus[] = [
  { name: 'FastAPI Core Runtime', status: 'simulated', latencyMs: 14, uptime: '74h 15m', version: 'v1.4.0', details: 'Phase 13 Endpoints defined' },
  { name: 'PostgreSQL 18 + pgvector', status: 'simulated', latencyMs: 4, uptime: '120h 42m', version: 'Postgres 18.1', details: 'HNSW vector index ready' },
  { name: 'Hybrid LLM Router', status: 'simulated', latencyMs: 8, uptime: '74h 15m', version: 'v2.1 Router', details: 'Groq + Ollama switchboard' },
  { name: 'Ollama Engine (Local)', status: 'simulated', latencyMs: 28, uptime: '32h 10m', version: 'v0.5.8', details: 'DeepSeek-R1-Distill-Qwen loaded' },
  { name: 'OS Tool Sandbox Guard', status: 'healthy', latencyMs: 1, uptime: '74h 15m', version: 'ASTA-Sec v1', details: 'Audited execution boundary active' },
];

interface SystemStoreState {
  metrics: SystemResourceMetric[];
  processes: SystemProcess[];
  services: BackendServiceStatus[];
  refreshTelemetry: () => void;
  terminateProcess: (pid: number) => void;
}

export const useSystemStore = create<SystemStoreState>((set) => ({
  metrics: INITIAL_METRICS,
  processes: INITIAL_PROCESSES,
  services: INITIAL_SERVICES,
  refreshTelemetry: () =>
    set((state) => {
      const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const last = state.metrics[state.metrics.length - 1];
      const newCpu = Math.max(8, Math.min(65, Math.floor(last.cpuPercent + (Math.random() * 12 - 6))));
      const newMem = Math.max(40, Math.min(55, Number((last.memoryPercent + (Math.random() * 2 - 1)).toFixed(1))));
      const newMemGb = Number(((newMem / 100) * 32).toFixed(1));

      const updatedHistory = [
        ...state.metrics.slice(1),
        {
          timestamp: now,
          cpuPercent: newCpu,
          memoryPercent: newMem,
          memoryUsedGb: newMemGb,
          memoryTotalGb: 32,
          diskUsedGb: last.diskUsedGb,
          diskTotalGb: last.diskTotalGb,
        },
      ];

      return { metrics: updatedHistory };
    }),
  terminateProcess: (pid) =>
    set((state) => ({
      processes: state.processes.filter((p) => p.pid !== pid),
    })),
}));
