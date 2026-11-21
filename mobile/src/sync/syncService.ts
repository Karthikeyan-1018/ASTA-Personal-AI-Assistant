import * as SecureStore from 'expo-secure-store';
import { getDatabase } from '../database/db';
import {
  apiClient,
  ApiError,
  ServerTask,
  TaskPayload,
  TOKEN_STORAGE_KEY,
  URL_STORAGE_KEY,
  USER_STORAGE_KEY,
} from '../api/apiClient';
import {
  getPendingSyncQueue,
  markSyncItemFailed,
  markSyncItemProcessing,
  markSyncItemSuccess,
  getPendingSyncCount,
} from './syncQueue';
import { MockSyncAdapter } from './mockSyncAdapter';
import { SyncMode, SyncStatusInfo } from '../types/sync';
import { ConflictResolver } from './conflictResolver';
import { LocalTask, TaskCategory, TaskPriority, TaskStatus } from '../types/task';

type SyncListener = (status: SyncStatusInfo) => void;

// Mobile has more specific labels than the laptop API's category enum.
function toBackendCategory(category: string | null | undefined): string {
  switch (category) {
    case 'assignments':
    case 'exams':
    case 'college':
      return 'college';
    case 'projects':
    case 'project':
      return 'project';
    case 'meetings':
    case 'learning':
    case 'career':
      return 'career';
    case 'personal':
    case 'fitness':
      return 'personal';
    default:
      return 'other';
  }
}

function toMobileCategory(category: string | null | undefined): TaskCategory {
  switch (category) {
    case 'college': return 'college';
    case 'career': return 'learning';
    case 'fitness':
    case 'personal': return 'personal';
    case 'project': return 'projects';
    default: return 'other';
  }
}

function normalizeBackendTime(value: string | null | undefined): string | null | undefined {
  if (value == null || value.trim() === '') return value;
  const trimmed = value.trim();
  const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(trimmed);
  if (!match) return trimmed;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  const second = match[3] == null ? null : Number(match[3]);
  if (hour > 23 || minute > 59 || (second != null && second > 59)) return trimmed;
  return `${String(hour).padStart(2, '0')}:${match[2]}${match[3] == null ? '' : `:${match[3]}`}`;
}

class SyncService {
  private listeners: Set<SyncListener> = new Set();
  private isSyncing = false;
  private isOnline = false;
  private lastSyncTimestamp: string | null = null;
  private syncMode: SyncMode = 'live';
  private serverUrl = '';
  private token: string | null = null;
  private username: string | null = null;
  private statusMessage = 'Ready. Offline storage active.';

  constructor() {
    this.initCredentials();
  }

  async initCredentials() {
    try {
      const [url, tok, user] = await Promise.all([
        SecureStore.getItemAsync(URL_STORAGE_KEY),
        SecureStore.getItemAsync(TOKEN_STORAGE_KEY),
        SecureStore.getItemAsync(USER_STORAGE_KEY),
      ]);
      this.serverUrl = url || '';
      this.token = tok || null;
      this.username = user || null;
      this.notify();
    } catch {
      // ignore
    }
  }

  setSyncMode(mode: SyncMode) {
    this.syncMode = mode;
    this.statusMessage = mode === 'mock' ? 'Mock sync mode enabled' : 'Live sync mode enabled';
    this.notify();
  }

  subscribe(listener: SyncListener): () => void {
    this.listeners.add(listener);
    listener(this.getStatusInfoSync());
    return () => this.listeners.delete(listener);
  }

  private notify() {
    const info = this.getStatusInfoSync();
    this.listeners.forEach((l) => l(info));
  }

  getStatusInfoSync(): SyncStatusInfo {
    return {
      isOnline: this.isOnline,
      isSyncing: this.isSyncing,
      lastSyncTimestamp: this.lastSyncTimestamp,
      pendingCount: 0, // updated asynchronously
      failedCount: 0,
      syncMode: this.syncMode,
      statusMessage: this.statusMessage,
      serverUrl: this.serverUrl,
      isAuthenticated: Boolean(this.token),
      username: this.username,
    };
  }

  async getStatusInfo(): Promise<SyncStatusInfo> {
    const pendingCount = await getPendingSyncCount();
    return {
      isOnline: this.isOnline,
      isSyncing: this.isSyncing,
      lastSyncTimestamp: this.lastSyncTimestamp,
      pendingCount,
      failedCount: 0,
      syncMode: this.syncMode,
      statusMessage: this.statusMessage,
      serverUrl: this.serverUrl,
      isAuthenticated: Boolean(this.token),
      username: this.username,
    };
  }

  async setServerCredentials(url: string, token: string | null, username: string | null) {
    this.serverUrl = url;
    this.token = token;
    this.username = username;
    if (url) await SecureStore.setItemAsync(URL_STORAGE_KEY, url);
    if (token) await SecureStore.setItemAsync(TOKEN_STORAGE_KEY, token);
    else await SecureStore.deleteItemAsync(TOKEN_STORAGE_KEY);
    if (username) await SecureStore.setItemAsync(USER_STORAGE_KEY, username);
    this.notify();
  }

  async checkReachability(): Promise<boolean> {
    if (!this.serverUrl) {
      this.isOnline = false;
      this.notify();
      return false;
    }
    const reachable = await apiClient.checkReachability(this.serverUrl);
    this.isOnline = reachable;
    this.notify();
    return reachable;
  }

  async triggerSync(): Promise<{ success: boolean; message: string }> {
    if (this.isSyncing) {
      return { success: false, message: 'Sync already in progress' };
    }

    this.isSyncing = true;
    this.statusMessage = 'Synchronizing tasks…';
    this.notify();

    try {
      // 1. If in mock mode, execute mock adapter
      if (this.syncMode === 'mock') {
        const mockRes = await MockSyncAdapter.executeMockSync();
        this.lastSyncTimestamp = mockRes.timestamp;
        this.statusMessage = mockRes.message;
        this.isOnline = true;
        this.isSyncing = false;
        this.notify();
        return { success: true, message: mockRes.message };
      }

      // 2. Live mode: require server URL and auth token
      if (!this.serverUrl) {
        throw new Error('Configure laptop server URL in Settings before syncing.');
      }
      if (!this.token) {
        throw new Error('Sign in to ASTA on your laptop to enable cloud sync.');
      }

      // Check reachability
      const isReachable = await apiClient.checkReachability(this.serverUrl);
      this.isOnline = isReachable;
      if (!isReachable) {
        throw new ApiError('Cannot reach ' + this.serverUrl + '. Use the laptop LAN HTTPS address, bind the backend to 0.0.0.0, allow its port on the trusted private network, and install a trusted certificate.');
      }

      const db = await getDatabase();
      const queue = await getPendingSyncQueue();

      // Process pending local queue mutations
      const failedOperations: string[] = [];
      for (const item of queue) {
        await markSyncItemProcessing(item.id);
        try {
          if (item.entity_type !== 'task') {
            throw new ApiError('ASTA backend sync for ' + item.entity_type + ' items is not available yet. This change remains on this phone.');
          }
          if (item.entity_type === 'task') {
            const payload = JSON.parse(item.payload_json);
            if (item.operation === 'CREATE') {
              const res = await apiClient.createTask(this.serverUrl, this.token, {
                title: payload.title,
                description: payload.description,
                category: toBackendCategory(payload.category),
                priority: payload.priority || 'medium',
                status: payload.status || 'pending',
                due_date: payload.due_date,
                due_time: normalizeBackendTime(payload.due_time),
                timezone: payload.timezone,
              });
              await db.runAsync(
                "UPDATE mobile_tasks SET server_id = ?, sync_state = 'synced', sync_error = NULL WHERE local_id = ?",
                res.id,
                item.entity_id
              );
            } else if (item.operation === 'UPDATE') {
              const localRow = await db.getFirstAsync<{ server_id: number | null }>(
                'SELECT server_id FROM mobile_tasks WHERE local_id = ?',
                [item.entity_id]
              );
              if (!localRow?.server_id) {
                throw new ApiError('Task update is waiting for its create operation to sync.');
              }
              if (localRow?.server_id) {
                await apiClient.updateTask(this.serverUrl, this.token, localRow.server_id, {
                  title: payload.title,
                  description: payload.description,
                  category: toBackendCategory(payload.category),
                  priority: payload.priority,
                  status: payload.status,
                  due_date: payload.due_date,
                  due_time: normalizeBackendTime(payload.due_time),
                  timezone: payload.timezone,
                });
              }
              await db.runAsync(
                "UPDATE mobile_tasks SET sync_state = 'synced', sync_error = NULL WHERE local_id = ?",
                item.entity_id
              );
            } else if (item.operation === 'DELETE') {
              if (payload.server_id) {
                try {
                  await apiClient.deleteTask(this.serverUrl, this.token, payload.server_id);
                } catch (delErr: any) {
                  // If 404, it was already deleted on server
                  if (delErr?.status !== 404) throw delErr;
                }
              }
            }
          }
          await markSyncItemSuccess(item.id);
        } catch (itemErr: any) {
          const errorMessage = itemErr?.message || 'Operation failed';
          failedOperations.push(errorMessage);
          await markSyncItemFailed(item.id, errorMessage);
          await db.runAsync(
            "UPDATE mobile_tasks SET sync_state = 'sync_failed', sync_error = ? WHERE local_id = ?",
            itemErr?.message || 'Sync failed',
            item.entity_id
          );
        }
      }

      // Download latest tasks from ASTA backend
      const remoteRes = await apiClient.listTasks(this.serverUrl, this.token);
      for (const rTask of remoteRes.tasks) {
        // Check if exists locally by server_id
        const existing = await db.getFirstAsync<any>(
          'SELECT * FROM mobile_tasks WHERE server_id = ?',
          [rTask.id]
        );

        if (!existing) {
          const localId = `server-${rTask.id}`;
          await db.runAsync(
            `INSERT INTO mobile_tasks (
              local_id, server_id, title, description, category, priority, status,
              is_completed, due_date, due_time, timezone, tags_json, subtasks_json,
              notes, created_at, updated_at, completed_at, sync_state, sync_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', NULL, ?, ?, ?, 'synced', NULL)
            ON CONFLICT(local_id) DO UPDATE SET
              title = excluded.title, description = excluded.description,
              category = excluded.category, priority = excluded.priority,
              status = excluded.status, is_completed = excluded.is_completed,
              due_date = excluded.due_date, sync_state = 'synced'`,
            localId,
            rTask.id,
            rTask.title,
            rTask.description,
            toMobileCategory(rTask.category),
            rTask.priority || 'medium',
            rTask.status || 'pending',
            rTask.status === 'completed' || rTask.is_completed ? 1 : 0,
            rTask.due_date,
            rTask.due_time || null,
            rTask.timezone || null,
            rTask.created_at,
            rTask.updated_at || rTask.created_at,
            rTask.status === 'completed' ? rTask.updated_at || rTask.created_at : null
          );
        }
      }

      this.lastSyncTimestamp = new Date().toISOString();
      const pendingCount = await getPendingSyncCount();
      this.statusMessage = pendingCount > 0
        ? 'Connected to ASTA, but ' + pendingCount + ' change(s) remain unsynced. ' + (failedOperations[0] || 'Retry sync after resolving pending changes.')
        : 'Successfully synced with ASTA laptop.';
      this.isOnline = true;
      this.isSyncing = false;
      this.notify();
      return { success: pendingCount === 0, message: this.statusMessage };
    } catch (err: any) {
      this.isSyncing = false;
      const msg = err instanceof ApiError ? err.message : err?.message || 'Sync failed';
      this.statusMessage = msg;
      this.notify();
      return { success: false, message: msg };
    }
  }
}

export const syncService = new SyncService();
