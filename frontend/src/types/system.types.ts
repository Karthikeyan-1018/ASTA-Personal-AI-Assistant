/**
 * Domain types for the system monitor.
 *
 * Every measurement is nullable. A missing backend value stays null and is
 * rendered as "Unavailable"; it is never coerced into zero or a demo value.
 */

export type SystemMonitorStatus =
  | 'idle'
  | 'loading'
  | 'refreshing'
  | 'connected'
  | 'stale'
  | 'unavailable';

export interface CpuInfo {
  available: boolean;
  model: string | null;
  logicalProcessors: number | null;
  physicalCores: number | null;
  architecture: string | null;
  reason: string | null;
}

export interface MemoryMetric {
  available: boolean;
  usagePercent: number | null;
  usedGib: number | null;
  totalGib: number | null;
  availableGib: number | null;
  reason: string | null;
}

export interface DiskDriveMetric {
  root: string;
  label: string | null;
  available: boolean;
  usagePercent: number | null;
  totalGib: number | null;
  usedGib: number | null;
  availableGib: number | null;
  error: string | null;
}

export interface DiskMetric {
  available: boolean;
  drives: DiskDriveMetric[];
  reason: string | null;
}

export interface ProcessEntry {
  pid: number;
  name: string;
  threadCount: number | null;
  cpuPercent: number | null;
  cpuAvailable: boolean;
  memoryMib: number | null;
  memoryAvailable: boolean;
}

export interface ProcessMetric {
  available: boolean;
  count: number | null;
  totalConsidered: number | null;
  truncated: boolean | null;
  cpuSampleMs: number | null;
  processes: ProcessEntry[];
  reason: string | null;
}

export interface ProviderHealth {
  available: boolean;
  groq: string | null;
  ollama: string | null;
  overallStatus: string | null;
  reason: string | null;
}

export interface ToolRuntimeMetrics {
  available: boolean;
  executionsTotal: number | null;
  successTotal: number | null;
  failureTotal: number | null;
  averageDurationMs: number | null;
  maxDurationMs: number | null;
  since: string | null;
  reason: string | null;
}

export interface HostInfo {
  os: string | null;
  hostname: string | null;
  python: string | null;
}

export interface SystemSnapshot {
  /** Local time at which this confirmed backend response was received. */
  collectedAt: string;
  cpu: CpuInfo;
  memory: MemoryMetric;
  disk: DiskMetric;
  processes: ProcessMetric;
  providers: ProviderHealth | null;
  toolRuntime: ToolRuntimeMetrics | null;
  hostInfo: HostInfo;
}

export interface HistoryPoint {
  collectedAt: string;
  memoryPercent: number | null;
  diskPercent: number | null;
  processCount: number | null;
}
