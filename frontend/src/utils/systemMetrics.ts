/**
 * Pure mapping/validation helpers for the system monitor.
 *
 * The backend is the only source of measurements. Every conversion below
 * preserves absence as `null`; no zero, average or demo value is substituted.
 */
import type { ToolResultResponse } from '../types/api.types';
import type {
  CpuInfo,
  DiskDriveMetric,
  DiskMetric,
  HistoryPoint,
  HostInfo,
  MemoryMetric,
  ProcessEntry,
  ProcessMetric,
  ProviderHealth,
  SystemSnapshot,
  ToolRuntimeMetrics,
} from '../types/system.types';
import type { ProviderHealthResponse as ProviderHealthWire } from '../services/api/system.api';
import type { ToolMetricsResponse } from '../types/api.types';

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

export function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function integer(value: unknown): number | null {
  const number = finiteNumber(value);
  return number !== null && Number.isInteger(number) ? number : null;
}

export function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

/** Percentages are displayed and bounded exactly once, from real values. */
export function percent(value: unknown): number | null {
  const number = finiteNumber(value);
  if (number === null) return null;
  return Math.round(Math.min(100, Math.max(0, number)) * 10) / 10;
}

export function toolUnavailableReason(
  result: ToolResultResponse | null | undefined
): string | null {
  if (!result) return 'The backend returned no response.';
  if (result.success === false) {
    if (result.error_category === 'disabled') {
      return 'Disabled by deployment policy.';
    }
    return text(result.error_message) || 'The backend tool reported an error.';
  }
  if (result.data === null || result.data === undefined) {
    return 'The backend returned no measurement.';
  }
  return null;
}

function mapCpu(data: unknown, unavailableReason: string | null): CpuInfo {
  const raw = asRecord(data);
  const available = raw?.available === true && unavailableReason === null;

  return {
    available,
    model: text(raw?.model),
    logicalProcessors: integer(raw?.logical_processors),
    physicalCores: integer(raw?.physical_cores),
    architecture: text(raw?.architecture),
    reason: available ? null : unavailableReason || 'CPU information is unavailable.',
  };
}

function mapMemory(data: unknown, unavailableReason: string | null): MemoryMetric {
  const raw = asRecord(data);
  const available = raw?.available === true && unavailableReason === null;

  return {
    available,
    usagePercent: available ? percent(raw?.usage_percent) : null,
    usedGib: available ? finiteNumber(raw?.used_gib) : null,
    totalGib: available ? finiteNumber(raw?.total_gib) : null,
    availableGib: available ? finiteNumber(raw?.available_gib) : null,
    reason: available
      ? null
      : unavailableReason || 'Memory information is unavailable.',
  };
}

function mapDrive(value: unknown): DiskDriveMetric | null {
  const raw = asRecord(value);
  if (!raw) return null;

  const root = text(raw.root);
  if (!root) return null;

  const available = raw.available === true;

  return {
    root,
    label: text(raw.label),
    available,
    usagePercent: available ? percent(raw.usage_percent) : null,
    totalGib: available ? finiteNumber(raw.total_gib) : null,
    usedGib: available ? finiteNumber(raw.used_gib) : null,
    availableGib: available ? finiteNumber(raw.available_gib) : null,
    error: text(raw.error),
  };
}

function mapDisk(data: unknown, unavailableReason: string | null): DiskMetric {
  const raw = asRecord(data);
  const rawDrives = Array.isArray(raw?.drives) ? raw.drives : [];
  const drives = rawDrives
    .map(mapDrive)
    .filter((drive): drive is DiskDriveMetric => drive !== null);
  const available =
    raw?.available === true && drives.some((drive) => drive.available) && unavailableReason === null;

  return {
    available,
    drives,
    reason: available
      ? null
      : unavailableReason || 'Disk information is unavailable.',
  };
}

function mapProcess(value: unknown): ProcessEntry | null {
  const raw = asRecord(value);
  if (!raw) return null;

  const pid = integer(raw.pid);
  const name = text(raw.name);
  if (pid === null || name === null) return null;

  const cpu = asRecord(raw.cpu);
  const cpuAvailable = cpu?.available === true;
  const memory = asRecord(raw.memory);
  const memoryAvailable = memory?.available === true;

  return {
    pid,
    name,
    threadCount: integer(raw.thread_count),
    cpuPercent: cpuAvailable ? percent(cpu?.percent) : null,
    cpuAvailable,
    memoryMib: memoryAvailable ? finiteNumber(memory?.mib) : null,
    memoryAvailable,
  };
}

function mapProcesses(
  data: unknown,
  unavailableReason: string | null
): ProcessMetric {
  const raw = asRecord(data);
  const rawProcesses = Array.isArray(raw?.processes) ? raw.processes : [];
  const processes = rawProcesses
    .map(mapProcess)
    .filter((process): process is ProcessEntry => process !== null);
  const available = raw?.available === true && unavailableReason === null;

  return {
    available,
    count: available ? integer(raw?.count) : null,
    totalConsidered: available ? integer(raw?.total_considered) : null,
    truncated: available && typeof raw?.truncated === 'boolean' ? raw.truncated : null,
    cpuSampleMs: available ? integer(raw?.cpu_sample_ms) : null,
    processes: available ? processes : [],
    reason: available ? null : unavailableReason || 'Process information is unavailable.',
  };
}

function mapHostInfo(sectionsValue: unknown): HostInfo {
  const sections = asRecord(sectionsValue);
  const os = asRecord(sections?.os);
  const hostname = asRecord(sections?.hostname);
  const python = asRecord(sections?.python);

  return {
    os: text(os?.platform) || text(os?.system) || null,
    hostname: text(hostname?.hostname),
    python: text(python?.version),
  };
}

export function mapSystemSummary(result: ToolResultResponse | null | undefined) {
  const reason = toolUnavailableReason(result);
  const data = reason === null ? asRecord(result?.data) : null;
  const sections = asRecord(data?.sections);

  return {
    cpu: mapCpu(sections?.cpu, reason),
    memory: mapMemory(sections?.memory, reason),
    disk: mapDisk(sections?.disk, reason),
    hostInfo: mapHostInfo(sections),
  };
}

export function mapProcessSummary(
  result: ToolResultResponse | null | undefined
): ProcessMetric {
  const reason = toolUnavailableReason(result);
  return mapProcesses(reason === null ? result?.data : null, reason);
}

export function mapProviderHealth(
  response: ProviderHealthWire | null | undefined
): ProviderHealth | null {
  if (!response || typeof response.overall_status !== 'string') return null;

  return {
    available: true,
    groq: text(response.groq),
    ollama: text(response.ollama),
    overallStatus: text(response.overall_status),
    reason: null,
  };
}

export function mapToolRuntime(
  response: ToolMetricsResponse | null | undefined
): ToolRuntimeMetrics | null {
  if (!response || !Number.isFinite(response.executions_total)) return null;

  return {
    available: true,
    executionsTotal: integer(response.executions_total),
    successTotal: integer(response.success_total),
    failureTotal: integer(response.failure_total),
    averageDurationMs: finiteNumber(response.average_duration_ms),
    maxDurationMs: finiteNumber(response.max_duration_ms),
    since: text(response.since),
    reason: null,
  };
}

export function snapshotHasRealData(snapshot: SystemSnapshot | null): boolean {
  if (!snapshot) return false;
  return (
    snapshot.cpu.available ||
    snapshot.memory.available ||
    snapshot.disk.available ||
    snapshot.processes.available ||
    (snapshot.providers?.available ?? false) ||
    (snapshot.toolRuntime?.available ?? false)
  );
}

export function firstAvailableDiskPercent(snapshot: SystemSnapshot): number | null {
  for (const drive of snapshot.disk.drives) {
    if (drive.available && drive.usagePercent !== null) return drive.usagePercent;
  }
  return null;
}

/** History contains only measurements that were actually confirmed by a refresh. */
export function appendHistory(
  history: HistoryPoint[],
  snapshot: SystemSnapshot
): HistoryPoint[] {
  const diskPercent = firstAvailableDiskPercent(snapshot);
  const processCount = snapshot.processes.available
    ? snapshot.processes.totalConsidered ?? snapshot.processes.count
    : null;
  const memoryPercent = snapshot.memory.available
    ? snapshot.memory.usagePercent
    : null;

  if (memoryPercent === null && diskPercent === null && processCount === null) {
    return history;
  }

  return [
    ...history,
    {
      collectedAt: snapshot.collectedAt,
      memoryPercent,
      diskPercent,
      processCount,
    },
  ].slice(-40);
}

