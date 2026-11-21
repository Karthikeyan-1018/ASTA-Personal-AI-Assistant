import { getDatabase } from '../database/db';
import { LocalTask } from '../types/task';
import { getPendingSyncQueue, markSyncItemSuccess } from './syncQueue';

export interface MockSyncResult {
  success: boolean;
  uploadedCount: number;
  downloadedCount: number;
  message: string;
  timestamp: string;
}

export class MockSyncAdapter {
  private static simulatedLatencyMs = 1200;
  private static shouldFailNext = false;

  static setSimulatedFailure(shouldFail: boolean) {
    this.shouldFailNext = shouldFail;
  }

  static async executeMockSync(): Promise<MockSyncResult> {
    // 1. Simulate network transit
    await new Promise((resolve) => setTimeout(resolve, this.simulatedLatencyMs));

    if (this.shouldFailNext) {
      this.shouldFailNext = false;
      throw new Error('Simulated network timeout connecting to ASTA laptop backend.');
    }

    const db = await getDatabase();
    const queue = await getPendingSyncQueue();
    let uploaded = 0;

    // Process local queue items
    for (const item of queue) {
      if (item.entity_type === 'task') {
        const payload = JSON.parse(item.payload_json);
        const pseudoServerId = Math.floor(1000 + Math.random() * 9000);

        if (item.operation === 'CREATE') {
          await db.runAsync(
            "UPDATE mobile_tasks SET server_id = ?, sync_state = 'synced', sync_error = NULL WHERE local_id = ?",
            pseudoServerId,
            item.entity_id
          );
        } else if (item.operation === 'UPDATE') {
          await db.runAsync(
            "UPDATE mobile_tasks SET sync_state = 'synced', sync_error = NULL WHERE local_id = ?",
            item.entity_id
          );
        }
        await markSyncItemSuccess(item.id);
        uploaded++;
      }
    }

    const timestamp = new Date().toISOString();
    return {
      success: true,
      uploadedCount: uploaded,
      downloadedCount: 0,
      message: `Mock sync complete: ${uploaded} changes pushed to simulated server.`,
      timestamp,
    };
  }
}
