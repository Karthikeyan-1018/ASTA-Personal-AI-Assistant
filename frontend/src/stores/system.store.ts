import { create } from 'zustand';
import systemApi from '../services/api/system.api';
import type { ToolMetricsResponse } from '../types/api.types';
import type {
  HistoryPoint,
  SystemMonitorStatus,
  SystemSnapshot,
} from '../types/system.types';
import {
  appendHistory,
  mapMonitorSnapshot,
  mapProviderHealth,
  mapToolRuntime,
  snapshotHasRealData,
} from '../utils/systemMetrics';
import type {
  ProviderHealthResponse,
  SystemMonitorWire,
} from '../services/api/system.api';

/** Default cadence; the authenticated backend snapshot can configure 1-10 seconds. */
export const SYSTEM_MONITOR_REFRESH_MS = 2_000;

export type RefreshReason = 'initial' | 'auto' | 'manual' | 'focus' | 'retry';

interface RefreshOptions {
  reason?: RefreshReason;
}

interface SystemStoreState {
  snapshot: SystemSnapshot | null;
  history: HistoryPoint[];
  status: SystemMonitorStatus;
  isLoading: boolean;
  isRefreshing: boolean;
  isPolling: boolean;
  isBackendConnected: boolean;
  error: string | null;
  lastSuccessfulAt: string | null;
  lastRefreshReason: RefreshReason | null;
  refreshIntervalMs: number;
  refresh: (options?: RefreshOptions) => Promise<boolean>;
  retry: () => Promise<boolean>;
  refreshIfStale: () => Promise<boolean>;
  startPolling: () => void;
  stopPolling: () => void;
}

let inFlight = false;
let requestSequence = 0;
let lastAncillaryRefreshAt = 0;
const ANCILLARY_REFRESH_MS = 20_000;
/** Incremented on unmount so an in-flight response cannot update dead UI. */
let sessionGeneration = 0;
let pollTimer: ReturnType<typeof setInterval> | null = null;

function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

function settledValue<T>(
  result: PromiseSettledResult<T>,
  label: string,
  errors: string[]
): T | null {
  if (result.status === 'fulfilled') return result.value;
  errors.push(`${label}: ${getErrorMessage(result.reason, 'request failed')}`);
  return null;
}

export const useSystemStore = create<SystemStoreState>((set, get) => ({
  snapshot: null,
  history: [],
  status: 'idle',
  isLoading: false,
  isRefreshing: false,
  isPolling: false,
  isBackendConnected: false,
  error: null,
  lastSuccessfulAt: null,
  lastRefreshReason: null,
  refreshIntervalMs: SYSTEM_MONITOR_REFRESH_MS,

  refresh: async (options = {}) => {
    // One shared guard prevents overlapping manual, focus and interval writes.
    if (inFlight) return false;

    inFlight = true;
    const sequence = ++requestSequence;
    const generation = sessionGeneration;
    const hadSnapshot = get().snapshot !== null;
    const reason = options.reason || 'manual';
    const refreshAncillary =
      reason !== 'auto' || Date.now() - lastAncillaryRefreshAt >= ANCILLARY_REFRESH_MS;

    set({
      isLoading: !hadSnapshot,
      isRefreshing: hadSnapshot,
      isBackendConnected: false,
      error: null,
      status: hadSnapshot ? 'refreshing' : 'loading',
      lastRefreshReason: reason,
    });

    try {
      const [providerResult, runtimeResult, snapshotResult] =
        await Promise.allSettled([
          refreshAncillary ? systemApi.getProviderHealth() : Promise.resolve(null),
          refreshAncillary ? systemApi.getToolMetrics() : Promise.resolve(null),
          systemApi.getSnapshot(),
        ]);

      // Discard a superseded response; only the latest request may write state.
      if (sequence !== requestSequence || generation !== sessionGeneration) {
        return false;
      }

      const errors: string[] = [];
      const providerWire = refreshAncillary
        ? settledValue<ProviderHealthResponse>(providerResult as PromiseSettledResult<ProviderHealthResponse>, 'Provider health', errors)
        : null;
      const runtimeWire = refreshAncillary
        ? settledValue<ToolMetricsResponse>(runtimeResult as PromiseSettledResult<ToolMetricsResponse>, 'Tool metrics', errors)
        : null;
      const snapshotWire = settledValue<SystemMonitorWire>(
        snapshotResult,
        'System monitor',
        errors
      );

      const summary = snapshotWire
        ? mapMonitorSnapshot(snapshotWire)
        : mapMonitorSnapshot({
            collected_at: new Date().toISOString(),
            refresh_interval_seconds: SYSTEM_MONITOR_REFRESH_MS / 1000,
            cpu: { available: false, reason: 'System Monitor endpoint unavailable.' },
            memory: { available: false, reason: 'System Monitor endpoint unavailable.' },
            disk: { available: false, reason: 'System Monitor endpoint unavailable.' },
            network: { available: false, reason: 'System Monitor endpoint unavailable.' },
            gpu: { available: false, reason: 'System Monitor endpoint unavailable.' },
            system: { available: false, reason: 'System Monitor endpoint unavailable.' },
            processes: { available: false, reason: 'System Monitor endpoint unavailable.' },
          });
      const snapshot: SystemSnapshot = {
        ...summary,
        providers: refreshAncillary
          ? mapProviderHealth(providerWire)
          : get().snapshot?.providers ?? null,
        toolRuntime: refreshAncillary
          ? mapToolRuntime(runtimeWire)
          : get().snapshot?.toolRuntime ?? null,
      };
      if (refreshAncillary) lastAncillaryRefreshAt = Date.now();
      const newInterval = snapshotWire
        ? Math.max(1_000, Math.min(10_000, snapshotWire.refresh_interval_seconds * 1000))
        : get().refreshIntervalMs;
      const hasData = snapshotHasRealData(snapshot);

      // Every source failed: this is a failed refresh, not an empty success.
      // Keep the last confirmed snapshot instead of replacing it with nulls.
      if (!hasData && errors.length > 0) {
        set((state) => ({
          isLoading: false,
          isRefreshing: false,
          isBackendConnected: false,
          error: errors.join('; '),
          status: state.snapshot ? 'stale' : 'unavailable',
          refreshIntervalMs: newInterval,
        }));
        return false;
      }

      // The backend responded but confirmed no measurements at all.
      if (!hasData) {
        set((state) => ({
          snapshot: null,
          history: state.history,
          isLoading: false,
          isRefreshing: false,
          isBackendConnected: true,
          error: null,
          lastSuccessfulAt: state.lastSuccessfulAt,
          status: 'unavailable',
          refreshIntervalMs: newInterval,
        }));
        return false;
      }

      set((state) => ({
        snapshot,
        history: appendHistory(state.history, snapshot),
        isLoading: false,
        isRefreshing: false,
        isBackendConnected: true,
        error: errors.length > 0 ? errors.join('; ') : null,
        lastSuccessfulAt: snapshot.collectedAt,
        status: 'connected',
        refreshIntervalMs: newInterval,
      }));
      if (newInterval !== get().refreshIntervalMs && pollTimer !== null) {
        globalThis.clearInterval(pollTimer);
        pollTimer = globalThis.setInterval(() => {
          void get().refresh({ reason: 'auto' });
        }, newInterval);
      }

      return true;
    } catch (err: unknown) {
      if (sequence !== requestSequence || generation !== sessionGeneration) {
        return false;
      }
      // Keep the last confirmed snapshot and history exactly as they were.
      set((state) => ({
        isLoading: false,
        isRefreshing: false,
        isBackendConnected: false,
        error: getErrorMessage(err, 'Could not refresh system metrics.'),
        status: state.snapshot ? 'stale' : 'unavailable',
      }));
      return false;
    } finally {
      inFlight = false;
    }
  },

  retry: () => get().refresh({ reason: 'retry' }),

  refreshIfStale: () => {
    const { lastSuccessfulAt, status } = get();
    const parsed = lastSuccessfulAt ? Date.parse(lastSuccessfulAt) : Number.NaN;
    const stale =
      !Number.isFinite(parsed) ||
      status === 'unavailable' ||
      status === 'stale' ||
      Date.now() - parsed >= SYSTEM_MONITOR_REFRESH_MS;

    return stale ? get().refresh({ reason: 'focus' }) : Promise.resolve(false);
  },

  startPolling: () => {
    if (pollTimer !== null) return;
    set({ isPolling: true });
    pollTimer = globalThis.setInterval(() => {
      void get().refresh({ reason: 'auto' });
    }, get().refreshIntervalMs);
  },

  stopPolling: () => {
    if (pollTimer !== null) {
      globalThis.clearInterval(pollTimer);
      pollTimer = null;
    }
    // Invalidate any response still in flight when the page unmounts.
    sessionGeneration += 1;
    set({ isPolling: false });
  },
}));

export default useSystemStore;
