import React, { useState } from 'react';
import {
  AlertTriangle,
  Bell,
  Check,
  CheckCheck,
  Clock,
  Info,
  Loader2,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-react';
import { useNotificationStore } from '../../stores/notification.store';
import { formatRelativeTime } from '../../utils/formatDate';
import type { NotificationItem } from '../../types/notification.types';

const SNOOZE_OPTIONS = [
  { label: '10 minutes', minutes: 10 },
  { label: '30 minutes', minutes: 30 },
  { label: '1 hour', minutes: 60 },
  { label: 'Until tomorrow', minutes: 1440 },
];

function DeliveryBadge({ item }: { item: NotificationItem }) {
  const styles =
    item.deliveryStatus === 'delivered'
      ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
      : item.deliveryStatus === 'failed'
        ? 'border-rose-500/30 bg-rose-500/10 text-rose-300'
        : 'border-amber-500/30 bg-amber-500/10 text-amber-300';

  return (
    <span className={`rounded-full border px-1.5 py-0.5 text-[9px] font-semibold capitalize ${styles}`}>
      {item.deliveryStatus}
    </span>
  );
}

export const NotificationPanel: React.FC = () => {
  const notifications = useNotificationStore((s) => s.notifications);
  const unreadCount = useNotificationStore((s) => s.unreadCount);
  const unreadCapped = useNotificationStore((s) => s.unreadCapped);
  const isLoading = useNotificationStore((s) => s.isLoading);
  const isRefreshing = useNotificationStore((s) => s.isRefreshing);
  const isMarkingAllRead = useNotificationStore((s) => s.isMarkingAllRead);
  const mutatingIds = useNotificationStore((s) => s.mutatingIds);
  const error = useNotificationStore((s) => s.error);
  const fetchNotifications = useNotificationStore((s) => s.fetchNotifications);
  const markRead = useNotificationStore((s) => s.markRead);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const snoozeNotification = useNotificationStore((s) => s.snoozeNotification);
  const deleteNotification = useNotificationStore((s) => s.deleteNotification);

  const [snoozeMenuId, setSnoozeMenuId] = useState<number | null>(null);

  const unreadLabel = unreadCapped ? `${unreadCount}+` : String(unreadCount);
  const hasUnread = unreadCount > 0;

  return (
    <div
      className="absolute right-0 top-12 z-50 w-80 rounded-2xl border border-slate-800 bg-slate-950 p-4 shadow-2xl shadow-black/80 backdrop-blur-xl sm:w-96"
      role="dialog"
      aria-label="Notifications"
    >
      <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-bold uppercase tracking-wider text-white">
            Notifications
          </h3>
          {hasUnread && (
            <span className="rounded-full bg-indigo-500/20 px-2 py-0.5 text-[10px] font-semibold text-indigo-300">
              {unreadLabel} unread
            </span>
          )}
          {isRefreshing && (
            <Loader2 className="h-3 w-3 animate-spin text-indigo-400" aria-label="Refreshing" />
          )}
        </div>

        {hasUnread && (
          <button
            type="button"
            onClick={() => {
              void markAllRead();
            }}
            disabled={isMarkingAllRead}
            className="inline-flex items-center gap-1 text-[11px] font-medium text-indigo-400 transition hover:text-indigo-300 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isMarkingAllRead ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <CheckCheck className="h-3 w-3" />
            )}
            <span>{isMarkingAllRead ? 'Saving…' : 'Mark all read'}</span>
          </button>
        )}
      </div>

      {error && (
        <div
          role="alert"
          className="mt-3 rounded-xl border border-rose-500/30 bg-rose-500/10 p-2.5"
        >
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
            <div className="min-w-0 flex-1">
              <p className="text-[11px] font-semibold text-rose-200">
                Notification service error
              </p>
              <p className="mt-0.5 text-[10px] leading-relaxed text-rose-200/80">
                {error}
              </p>
              <p className="mt-0.5 text-[9px] text-rose-200/60">
                Last confirmed notifications are preserved.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              void fetchNotifications();
            }}
            disabled={isLoading || isRefreshing}
            className="mt-2 inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-[10px] font-semibold text-rose-200 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className={`h-3 w-3 ${isLoading || isRefreshing ? 'animate-spin' : ''}`} />
            <span>Retry</span>
          </button>
        </div>
      )}

      <div className="max-h-80 divide-y divide-slate-800/50 overflow-y-auto py-2">
        {isLoading && notifications.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-8 text-[11px] text-slate-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-400" />
            <span>Loading notifications…</span>
          </div>
        ) : notifications.length === 0 ? (
          error ? null : (
            <div className="py-8 text-center">
              <Bell className="mx-auto h-8 w-8 text-slate-700" />
              <p className="mt-2 text-xs font-semibold text-slate-300">
                No notifications
              </p>
              <p className="mt-0.5 text-[10px] text-slate-500">
                Reminder alerts delivered by ASTA will appear here.
              </p>
            </div>
          )
        ) : (
          notifications.map((item) => {
            const isMutating = mutatingIds.includes(item.id);
            const Icon = item.type === 'reminder' ? Bell : Info;

            return (
              <div
                key={item.id}
                className={`group rounded-xl p-2.5 transition ${
                  item.isRead
                    ? 'opacity-70 hover:bg-slate-900/60 hover:opacity-100'
                    : 'border border-indigo-500/20 bg-indigo-500/5'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Icon className="h-3.5 w-3.5 shrink-0 text-indigo-400" />
                      <p className="min-w-0 flex-1 truncate text-xs font-semibold text-slate-200">
                        {item.title}
                      </p>
                      {!item.isRead && (
                        <span
                          className="h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400"
                          title="Unread"
                          aria-label="Unread"
                        />
                      )}
                    </div>

                    <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-slate-400">
                      {item.message}
                    </p>

                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[9px] text-slate-500">
                      <span className="font-mono">{formatRelativeTime(item.createdAt)}</span>
                      <span className="capitalize">· {item.type}</span>
                      <DeliveryBadge item={item} />
                      {item.snoozedUntil && (
                        <span className="text-amber-300/80">
                          · snoozed until {new Date(item.snoozedUntil).toLocaleString()}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex shrink-0 items-center gap-0.5">
                    {!item.isRead && (
                      <button
                        type="button"
                        onClick={() => {
                          void markRead(item.id);
                        }}
                        disabled={isMutating}
                        title="Mark as read"
                        aria-label={`Mark notification ${item.title} as read`}
                        className="rounded p-1 text-slate-400 transition hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {isMutating ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Check className="h-3.5 w-3.5" />
                        )}
                      </button>
                    )}

                    {item.deliveryStatus === 'delivered' && (
                      <div className="relative">
                        <button
                          type="button"
                          onClick={() =>
                            setSnoozeMenuId((current) =>
                              current === item.id ? null : item.id
                            )
                          }
                          disabled={isMutating}
                          title="Snooze"
                          aria-label={`Snooze notification ${item.title}`}
                          className={`rounded p-1 transition hover:bg-slate-800 hover:text-amber-300 disabled:cursor-not-allowed disabled:opacity-50 ${
                            snoozeMenuId === item.id ? 'text-amber-300' : 'text-slate-400'
                          }`}
                        >
                          <Clock className="h-3.5 w-3.5" />
                        </button>

                        {snoozeMenuId === item.id && (
                          <div className="absolute right-0 top-7 z-10 w-36 rounded-xl border border-slate-800 bg-slate-900 p-1 shadow-xl">
                            <div className="flex items-center justify-between px-1.5 py-1">
                              <span className="text-[9px] font-semibold uppercase tracking-wider text-slate-500">
                                Snooze for
                              </span>
                              <button
                                type="button"
                                onClick={() => setSnoozeMenuId(null)}
                                className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
                                title="Close snooze menu"
                                aria-label="Close snooze menu"
                              >
                                <X className="h-3 w-3" />
                              </button>
                            </div>
                            {SNOOZE_OPTIONS.map((option) => (
                              <button
                                key={option.minutes}
                                type="button"
                                onClick={() => {
                                  setSnoozeMenuId(null);
                                  void snoozeNotification(item.id, option.minutes);
                                }}
                                disabled={isMutating}
                                className="block w-full rounded-lg px-2 py-1.5 text-left text-[11px] text-slate-300 transition hover:bg-slate-800 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                {option.label}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    <button
                      type="button"
                      onClick={() => {
                        setSnoozeMenuId(null);
                        void deleteNotification(item.id);
                      }}
                      disabled={isMutating}
                      title="Delete"
                      aria-label={`Delete notification ${item.title}`}
                      className="rounded p-1 text-slate-400 transition hover:bg-slate-800 hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default NotificationPanel;
