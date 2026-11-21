import { LocalTask } from '../types/task';
import { ServerTask } from '../api/apiClient';

export interface ConflictRecord {
  localId: string;
  serverId: number;
  localTask: LocalTask;
  serverTask: ServerTask;
  detectedAt: string;
  resolved: boolean;
}

export type ResolutionStrategy = 'use_local' | 'use_server' | 'fork_both';

export class ConflictResolver {
  private static conflicts: Map<string, ConflictRecord> = new Map();

  static detectConflict(local: LocalTask, remote: ServerTask): boolean {
    // If local was modified while still unsynced, and remote updated_at > local last_synced
    if (local.sync_state === 'pending_sync' && local.server_id === remote.id) {
      const localUpdated = new Date(local.updated_at).getTime();
      const remoteUpdated = remote.updated_at ? new Date(remote.updated_at).getTime() : 0;
      
      const hasContentDiff =
        local.title !== remote.title ||
        local.status !== remote.status ||
        local.priority !== remote.priority ||
        local.due_date !== remote.due_date;

      return hasContentDiff && remoteUpdated > 0 && Math.abs(localUpdated - remoteUpdated) > 1000;
    }
    return false;
  }

  static recordConflict(local: LocalTask, remote: ServerTask): ConflictRecord {
    const record: ConflictRecord = {
      localId: local.local_id,
      serverId: remote.id,
      localTask: local,
      serverTask: remote,
      detectedAt: new Date().toISOString(),
      resolved: false,
    };
    this.conflicts.set(local.local_id, record);
    return record;
  }

  static getActiveConflicts(): ConflictRecord[] {
    return Array.from(this.conflicts.values()).filter((c) => !c.resolved);
  }

  static resolveConflict(localId: string, strategy: ResolutionStrategy): void {
    const record = this.conflicts.get(localId);
    if (!record) return;
    record.resolved = true;
    this.conflicts.set(localId, record);
  }
}
