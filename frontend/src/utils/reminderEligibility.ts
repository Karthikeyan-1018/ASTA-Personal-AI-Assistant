/**
 * Shared reminder-eligibility rule for the store and the UI.
 *
 * The backend enforces the same rule in `reminder_service._ensure_open_task`,
 * so this helper prevents avoidable invalid requests while the backend remains
 * the final authority. Eligibility is always derived from the last status the
 * backend confirmed, never from an optimistic display value.
 */
import type { TaskItem } from '../types/task.types';
import { confirmedTaskStatus, isOpenTaskStatus } from './taskStatus';

export const REMINDER_ELIGIBILITY_RULE =
  'Reminders can only be scheduled for pending or in-progress tasks.';

export interface ReminderEligibility {
  eligible: boolean;
  /** Null only when the task is eligible. */
  message: string | null;
}

export function getTaskReminderEligibility(
  task: Pick<TaskItem, 'backendId' | 'status' | 'backendStatus'> | undefined
): ReminderEligibility {
  if (!task || typeof task.backendId !== 'number') {
    return {
      eligible: false,
      message: 'Choose a saved task before creating a reminder.',
    };
  }

  const status = confirmedTaskStatus(task);

  if (isOpenTaskStatus(status)) {
    return { eligible: true, message: null };
  }

  if (status === 'completed') {
    return {
      eligible: false,
      message: `This task is completed. ${REMINDER_ELIGIBILITY_RULE}`,
    };
  }

  if (status === 'cancelled') {
    return {
      eligible: false,
      message: `This task is cancelled. ${REMINDER_ELIGIBILITY_RULE}`,
    };
  }

  return {
    eligible: false,
    message:
      'This task has an unrecognised status. Refresh tasks before scheduling a reminder.',
  };
}

export function isTaskReminderEligible(
  task: Pick<TaskItem, 'backendId' | 'status' | 'backendStatus'> | undefined
): boolean {
  return getTaskReminderEligibility(task).eligible;
}
