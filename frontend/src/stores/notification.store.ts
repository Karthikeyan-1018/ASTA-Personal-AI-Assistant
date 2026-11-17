import { create } from 'zustand';
import type {
  NotificationItem,
  NotificationToast,
} from '../types/notification.types';
import {
  NOTIFICATION_PAGE_LIMIT,
  NOTIFICATION_POLL_INTERVAL_MS,
} from '../types/notification.types';
import type {
  NotificationResponse,
  NotificationSnoozeRequest,
} from '../types/api.types';
import { notificationsApi } from '../services/api/notifications.api';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

function mapNotification(r: NotificationResponse): NotificationItem {
  return {
    id: r.id,
    type: r.notification_type,
    deliveryStatus: r.delivery_status,
    title: r.title,
    message: r.body,
    taskId: r.task_id,
    reminderId: r.reminder_id,
    taskTitle: r.task_title,
    reminderAt: r.reminder_at,
    isRead: r.is_read,
    readAt: r.read_at,
    snoozedAt: r.snoozed_at,
    snoozedUntil: r.snoozed_until,
    snoozeCount: r.snooze_count,
    deliveredAt: r.delivered_at,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

/**
 * Delivery marker used for toast dedupe. A snoozed notification that is
 * re-delivered reuses its row but receives a new delivered_at, which represents
 * a genuinely new delivery event.
 */
function deliveryKey(item: {
  id: number;
  deliveredAt: string | null;
  updatedAt: string;
}): string {
  return `${item.id}:${item.deliveredAt ?? item.updatedAt}`;
}

const UNREAD_LIMIT = NOTIFICATION_PAGE_LIMIT;

// Runtime-only polling/dedupe state. These are deliberately module-local so
// they never leak into persisted UI state.
let pollingHandle: ReturnType<typeof setInterval> | null = null;
let fetchInFlight = false;
let baselineEstablished = false;
let stateVersion = 0;
let runtimeEpoch = 0;
const seenDeliveryKeys = new Set<string>();
const MAX_TOASTS = 3;

function limitToasts(toasts: NotificationToast[]): NotificationToast[] {
  return toasts.slice(0, MAX_TOASTS);
}

export interface FetchNotificationOptions {
  /** Polling/background refresh: keeps the existing list visible. */
  silent?: boolean;
}

interface NotificationStoreState {
  notifications: NotificationItem[];
  unreadCount: number;
  unreadCapped: boolean;
  isLoading: boolean;
  isRefreshing: boolean;
  isMarkingAllRead: boolean;
  mutatingIds: number[];
  error: string | null;
  isBackendConnected: boolean;
  lastFetchedAt: string | null;
  toasts: NotificationToast[];
  fetchNotifications: (options?: FetchNotificationOptions) => Promise<boolean>;
  markRead: (id: number) => Promise<boolean>;
  markAllRead: () => Promise<boolean>;
  snoozeNotification: (
    id: number,
    durationMinutes: number
  ) => Promise<boolean>;
  deleteNotification: (id: number) => Promise<boolean>;
  dismissToast: (key: string) => void;
  startPolling: (intervalMs?: number) => void;
  stopPolling: () => void;
  clearError: () => void;
}

export const useNotificationStore = create<NotificationStoreState>((set, get) => {
  const finishMutation = (id: number) =>
    set((state) => ({
      mutatingIds: state.mutatingIds.filter((value) => value !== id),
    }));

  const isMutating = (id: number) => get().mutatingIds.includes(id);

  return {
    notifications: [],
    unreadCount: 0,
    unreadCapped: false,
    isLoading: false,
    isRefreshing: false,
    isMarkingAllRead: false,
    mutatingIds: [],
    error: null,
    isBackendConnected: false,
    lastFetchedAt: null,
    toasts: [],

    fetchNotifications: async (options) => {
      // Controlled polling: a slow request must never overlap the next tick.
      if (fetchInFlight) return false;

      fetchInFlight = true;
      const silent = options?.silent === true;
      const requestVersion = stateVersion;
      const requestEpoch = runtimeEpoch;

      set((state) => ({
        isLoading: state.notifications.length === 0,
        isRefreshing: state.notifications.length > 0,
        error: silent ? state.error : null,
      }));

      try {
        const [records, unreadRecords] = await Promise.all([
          notificationsApi.list({ limit: UNREAD_LIMIT, offset: 0 }),
          notificationsApi.listUnread({ limit: UNREAD_LIMIT, offset: 0 }),
        ]);

        // A user action completed while this request was in flight. Applying the
        // older snapshot would overwrite newer confirmed state.
        if (requestVersion !== stateVersion) {
          set({ isLoading: false, isRefreshing: false });
          return false;
        }

        // The session ended while this request was in flight. Its records
        // belong to the previous account and must never be applied.
        if (requestEpoch !== runtimeEpoch) {
          set({ isLoading: false, isRefreshing: false });
          return false;
        }

        const mapped = records.map(mapNotification);
        const unreadLength = unreadRecords.length;
        const capped = unreadLength >= UNREAD_LIMIT;

        const newToasts: NotificationToast[] = [];

        for (const item of mapped) {
          const key = deliveryKey(item);

          if (seenDeliveryKeys.has(key)) continue;

          seenDeliveryKeys.add(key);

          // The first successful fetch establishes the baseline. Existing
          // notifications are never replayed as brand-new toasts.
          if (
            baselineEstablished &&
            item.deliveryStatus === 'delivered' &&
            !item.isRead
          ) {
            newToasts.push({
              key,
              notificationId: item.id,
              type: item.type,
              title: item.title,
              message: item.message,
              createdAt: item.deliveredAt || item.createdAt,
            });
          }
        }

        baselineEstablished = true;

        set((state) => ({
          notifications: mapped,
          unreadCount: capped ? UNREAD_LIMIT : unreadLength,
          unreadCapped: capped,
          isLoading: false,
          isRefreshing: false,
          isBackendConnected: true,
          lastFetchedAt: new Date().toISOString(),
          error: null,
          toasts: newToasts.length
            ? limitToasts([...newToasts, ...state.toasts])
            : state.toasts,
        }));

        return true;
      } catch (err: unknown) {
        if (requestEpoch !== runtimeEpoch) {
          set({ isLoading: false, isRefreshing: false });
          return false;
        }

        // Preserve the last confirmed list, unread count, and read flags.
        set({
          isLoading: false,
          isRefreshing: false,
          isBackendConnected: false,
          error: getErrorMessage(
            err,
            'Could not load notifications. Showing the last confirmed data.'
          ),
        });
        return false;
      } finally {
        fetchInFlight = false;
      }
    },

    markRead: async (id) => {
      const state = get();
      const target = state.notifications.find((n) => n.id === id);

      if (!target) return false;
      if (target.isRead) return true;
      if (isMutating(id) || state.isMarkingAllRead) return false;

      stateVersion += 1;

      set((current) => ({
        notifications: current.notifications.map((n) =>
          n.id === id
            ? { ...n, isRead: true, readAt: new Date().toISOString() }
            : n
        ),
        unreadCount: current.unreadCapped
          ? current.unreadCount
          : Math.max(0, current.unreadCount - 1),
        mutatingIds: [...current.mutatingIds, id],
        error: null,
      }));

      try {
        const updated = await notificationsApi.markRead(id);
        const mapped = mapNotification(updated);
        seenDeliveryKeys.add(deliveryKey(mapped));

        set((current) => ({
          notifications: current.notifications.map((n) =>
            n.id === id ? mapped : n
          ),
          isBackendConnected: true,
          error: null,
        }));
        return true;
      } catch (err: unknown) {
        // Roll back only this record so concurrent actions on other
        // notifications are not overwritten.
        set((current) => ({
          notifications: current.notifications.map((n) =>
            n.id === id ? target : n
          ),
          unreadCount:
            current.unreadCapped || target.isRead
              ? current.unreadCount
              : Math.min(100, current.unreadCount + 1),
          error: getErrorMessage(
            err,
            'Notification could not be marked as read. Previous state was restored.'
          ),
        }));
        return false;
      } finally {
        finishMutation(id);
      }
    },

    markAllRead: async () => {
      const state = get();

      if (state.isMarkingAllRead || state.mutatingIds.length > 0) return false;
      if (state.unreadCount === 0) return true;

      const previousNotifications = state.notifications;
      const previousUnread = state.unreadCount;
      const previousCapped = state.unreadCapped;
      const now = new Date().toISOString();

      stateVersion += 1;

      set((current) => ({
        notifications: current.notifications.map((n) =>
          n.isRead ? n : { ...n, isRead: true, readAt: now }
        ),
        unreadCount: 0,
        unreadCapped: false,
        isMarkingAllRead: true,
        error: null,
      }));

      try {
        await notificationsApi.markAllRead();
        set({ isBackendConnected: true, error: null });
        return true;
      } catch (err: unknown) {
        set({
          notifications: previousNotifications,
          unreadCount: previousUnread,
          unreadCapped: previousCapped,
          error: getErrorMessage(
            err,
            'Notifications could not be marked as read. Previous state was restored.'
          ),
        });
        return false;
      } finally {
        set({ isMarkingAllRead: false });
      }
    },

    snoozeNotification: async (id, durationMinutes) => {
      const state = get();
      const target = state.notifications.find((n) => n.id === id);

      if (!target) return false;
      if (isMutating(id) || state.isMarkingAllRead) return false;

      if (!Number.isInteger(durationMinutes)) {
        set({ error: 'Choose a valid snooze duration.' });
        return false;
      }

      if (durationMinutes < 1 || durationMinutes > 525600) {
        set({ error: 'Snooze duration must be between 1 and 525600 minutes.' });
        return false;
      }

      if (target.deliveryStatus !== 'delivered') {
        set({ error: 'Only delivered notifications can be snoozed.' });
        return false;
      }

      const payload: NotificationSnoozeRequest = { duration_minutes: durationMinutes };

      stateVersion += 1;

      set((current) => ({
        mutatingIds: [...current.mutatingIds, id],
        error: null,
      }));

      try {
        const updated = await notificationsApi.snooze(id, payload);
        const mapped = mapNotification(updated);
        seenDeliveryKeys.add(deliveryKey(mapped));

        // Snoozing re-arms the reminder. Whether it also marks the notification
        // read is backend-owned, so the unread count follows the server
        // response instead of assuming a read transition.
        const becameRead = !target.isRead && mapped.isRead;

        set((current) => ({
          notifications: current.notifications.map((n) =>
            n.id === id ? mapped : n
          ),
          unreadCount:
            becameRead && !current.unreadCapped
              ? Math.max(0, current.unreadCount - 1)
              : current.unreadCount,
          isBackendConnected: true,
          error: null,
        }));
        return true;
      } catch (err: unknown) {
        // Snooze is applied only from the server response, so there is no
        // optimistic record to roll back. The last confirmed state remains.
        set({
          error: getErrorMessage(
            err,
            'Notification could not be snoozed. Previous state was preserved.'
          ),
        });
        return false;
      } finally {
        finishMutation(id);
      }
    },

    deleteNotification: async (id) => {
      const state = get();
      const index = state.notifications.findIndex((n) => n.id === id);
      const target = state.notifications[index];

      if (!target) return false;
      if (isMutating(id) || state.isMarkingAllRead) return false;

      stateVersion += 1;

      set((current) => ({
        notifications: current.notifications.filter((n) => n.id !== id),
        unreadCount:
          target.isRead || current.unreadCapped
            ? current.unreadCount
            : Math.max(0, current.unreadCount - 1),
        mutatingIds: [...current.mutatingIds, id],
        error: null,
      }));

      try {
        await notificationsApi.remove(id);
        set({ isBackendConnected: true, error: null });
        return true;
      } catch (err: unknown) {
        // Restore only this record at its previous position. Concurrent actions
        // on other notifications remain intact.
        set((current) => {
          if (current.notifications.some((n) => n.id === id)) {
            return {
              unreadCount: current.unreadCount,
              error: getErrorMessage(
                err,
                'Notification could not be deleted. It was restored.'
              ),
            };
          }

          const restored = [...current.notifications];
          restored.splice(Math.min(index, restored.length), 0, target);

          return {
            notifications: restored,
            unreadCount:
              target.isRead || current.unreadCapped
                ? current.unreadCount
                : Math.min(100, current.unreadCount + 1),
            error: getErrorMessage(
              err,
              'Notification could not be deleted. It was restored.'
            ),
          };
        });
        return false;
      } finally {
        finishMutation(id);
      }
    },

    dismissToast: (key) =>
      set((state) => ({
        toasts: state.toasts.filter((toast) => toast.key !== key),
      })),

    startPolling: (intervalMs = NOTIFICATION_POLL_INTERVAL_MS) => {
      if (pollingHandle !== null) return;

      void get().fetchNotifications();

      pollingHandle = setInterval(() => {
        void get().fetchNotifications({ silent: true });
      }, intervalMs);
    },

    stopPolling: () => {
      if (pollingHandle !== null) {
        clearInterval(pollingHandle);
        pollingHandle = null;
      }

      // End the notification session: invalidate any in-flight response,
      // forget the delivery baseline, and drop account-scoped data so the next
      // authenticated session starts clean and never replays old toasts.
      runtimeEpoch += 1;
      baselineEstablished = false;
      seenDeliveryKeys.clear();

      set({
        notifications: [],
        unreadCount: 0,
        unreadCapped: false,
        isLoading: false,
        isRefreshing: false,
        isMarkingAllRead: false,
        mutatingIds: [],
        error: null,
        lastFetchedAt: null,
        toasts: [],
      });
    },

    clearError: () => set({ error: null }),
  };
});

export default useNotificationStore;
