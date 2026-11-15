export interface SystemResourceMetric {
  timestamp: string;
  cpuPercent: number;
  memoryPercent: number;
  memoryUsedGb: number;
  memoryTotalGb: number;
  diskUsedGb: number;
  diskTotalGb: number;
}

export interface SystemProcess {
  pid: number;
  name: string;
  cpuPercent: number;
  memoryMb: number;
  status: 'running' | 'sleeping' | 'idle';
  user: string;
}

export interface BackendServiceStatus {
  name: string;
  status: 'healthy' | 'degraded' | 'simulated';
  latencyMs: number;
  uptime: string;
  version: string;
  details: string;
}
