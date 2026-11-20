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
import type { ProviderHealthResponse as ProviderHealthWire, SystemMonitorWire } from '../services/api/system.api';
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
    usagePercent: available ? percent(raw?.usage_percent) : null,
    perCorePercent: null,
    frequencyMhz: null,
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
    readBytesPerSecond: null,
    writeBytesPerSecond: null,
    ioAvailable: null,
    ioReason: null,
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
    status: text(raw.status),
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
    architecture: null,
    bootTime: null,
    uptimeSeconds: null,
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
    network: {
      available: false,
      uploadBytesPerSecond: null,
      downloadBytesPerSecond: null,
      bytesSent: null,
      bytesReceived: null,
      interfaces: [],
      reason: 'Network telemetry is unavailable.',
    },
    gpu: {
      available: false,
      model: null,
      usagePercent: null,
      memoryTotalMib: null,
      memoryUsedMib: null,
      memoryAvailableMib: null,
      temperatureCelsius: null,
      powerWatts: null,
      reason: 'GPU telemetry is unavailable.',
    },
    hostInfo: mapHostInfo(sections),
  };
}

function record(value: unknown): UnknownRecord | null {
  return asRecord(value);
}

function sectionUnavailable(section: unknown, fallback: string): string | null {
  const raw = asRecord(section);
  return raw?.available === true ? null : text(raw?.reason) || fallback;
}

/** Maps the live backend snapshot while preserving unsupported values as null. */
export function mapMonitorSnapshot(wire: SystemMonitorWire) {
  const cpu = asRecord(wire.cpu);
  const memory = asRecord(wire.memory);
  const disk = asRecord(wire.disk);
  const network = asRecord(wire.network);
  const gpu = asRecord(wire.gpu);
  const system = asRecord(wire.system);
  const os = asRecord(system?.os);
  const processesRaw = asRecord(wire.processes);
  const drives = Array.isArray(disk?.drives)
    ? disk.drives.map(mapDrive).filter((drive): drive is DiskDriveMetric => drive !== null)
    : [];
  const processes = Array.isArray(processesRaw?.processes)
    ? processesRaw.processes.map(mapProcess).filter((item): item is ProcessEntry => item !== null)
    : [];
  const interfaces = Array.isArray(network?.interfaces)
    ? network.interfaces.flatMap((item) => {
        const raw = record(item);
        const name = text(raw?.name);
        return raw && name ? [{
          name,
          isUp: typeof raw.is_up === 'boolean' ? raw.is_up : null,
          speedMbps: integer(raw.speed_mbps),
          bytesSent: integer(raw.bytes_sent),
          bytesReceived: integer(raw.bytes_received),
          uploadBytesPerSecond: finiteNumber(raw.upload_bytes_per_second),
          downloadBytesPerSecond: finiteNumber(raw.download_bytes_per_second),
        }] : [];
      })
    : [];
  const memoryBytes = (value: unknown) => {
    const bytes = finiteNumber(value);
    return bytes === null ? null : bytes / (1024 ** 3);
  };

  return {
    collectedAt: text(wire.collected_at) || new Date().toISOString(),
    cpu: {
      available: cpu?.available === true,
      model: text(cpu?.model),
      logicalProcessors: integer(cpu?.logical_processors),
      physicalCores: integer(cpu?.physical_cores),
      architecture: text(cpu?.architecture),
      usagePercent: percent(cpu?.usage_percent),
      perCorePercent: Array.isArray(cpu?.per_core_percent)
        ? cpu.per_core_percent.map(percent).filter((item): item is number => item !== null)
        : null,
      frequencyMhz: finiteNumber(cpu?.frequency_mhz),
      reason: sectionUnavailable(wire.cpu, 'CPU information is unavailable.'),
    },
    memory: {
      available: memory?.available === true,
      usagePercent: percent(memory?.usage_percent),
      usedGib: memoryBytes(memory?.used_bytes),
      totalGib: memoryBytes(memory?.total_bytes),
      availableGib: memoryBytes(memory?.available_bytes),
      reason: sectionUnavailable(wire.memory, 'Memory information is unavailable.'),
    },
    disk: {
      available: disk?.available === true && drives.some((item) => item.available),
      drives,
      readBytesPerSecond: finiteNumber(disk?.read_bytes_per_second),
      writeBytesPerSecond: finiteNumber(disk?.write_bytes_per_second),
      ioAvailable: typeof disk?.io_available === 'boolean' ? disk.io_available : null,
      ioReason: text(disk?.io_reason),
      reason: sectionUnavailable(wire.disk, 'Disk information is unavailable.'),
    },
    network: {
      available: network?.available === true,
      uploadBytesPerSecond: finiteNumber(network?.upload_bytes_per_second),
      downloadBytesPerSecond: finiteNumber(network?.download_bytes_per_second),
      bytesSent: integer(network?.bytes_sent),
      bytesReceived: integer(network?.bytes_received),
      interfaces,
      reason: sectionUnavailable(wire.network, 'Network telemetry is unavailable.'),
    },
    gpu: {
      available: gpu?.available === true,
      model: text(gpu?.model),
      usagePercent: percent(gpu?.usage_percent),
      memoryTotalMib: finiteNumber(gpu?.memory_total_mib),
      memoryUsedMib: finiteNumber(gpu?.memory_used_mib),
      memoryAvailableMib: finiteNumber(gpu?.memory_available_mib),
      temperatureCelsius: finiteNumber(gpu?.temperature_celsius),
      powerWatts: finiteNumber(gpu?.power_watts),
      reason: sectionUnavailable(wire.gpu, 'GPU telemetry is unavailable.'),
    },
    processes: {
      available: processesRaw?.available === true,
      count: integer(processesRaw?.count),
      totalConsidered: integer(processesRaw?.total_considered),
      truncated: typeof processesRaw?.truncated === 'boolean' ? processesRaw.truncated : null,
      cpuSampleMs: integer(processesRaw?.cpu_sample_ms),
      processes: processesRaw?.available === true ? processes : [],
      reason: sectionUnavailable(wire.processes, 'Process information is unavailable.'),
    },
    hostInfo: {
      os: text(os?.platform) || text(os?.system),
      hostname: text(system?.hostname),
      python: null,
      architecture: text(system?.architecture),
      bootTime: text(system?.boot_time),
      uptimeSeconds: integer(system?.uptime_seconds),
    },
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
    snapshot.network.available ||
    snapshot.gpu.available ||
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
  const cpuPercent = snapshot.cpu.usagePercent;
  const gpuPercent = snapshot.gpu.usagePercent;

  if (
    memoryPercent === null &&
    diskPercent === null &&
    processCount === null &&
    cpuPercent === null &&
    snapshot.network.uploadBytesPerSecond === null &&
    snapshot.network.downloadBytesPerSecond === null &&
    gpuPercent === null
  ) {
    return history;
  }

  return [
    ...history,
    {
      collectedAt: snapshot.collectedAt,
      memoryPercent,
      diskPercent,
      processCount,
      cpuPercent,
      uploadBytesPerSecond: snapshot.network.uploadBytesPerSecond,
      downloadBytesPerSecond: snapshot.network.downloadBytesPerSecond,
      gpuPercent: snapshot.gpu.usagePercent,
    },
  ].slice(-40);
}

