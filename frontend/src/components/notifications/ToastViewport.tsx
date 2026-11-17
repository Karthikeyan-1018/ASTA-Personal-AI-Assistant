import React, { useEffect } from 'react';
import { Bell, Info, X } from 'lucide-react';
import { useNotificationStore } from '../../stores/notification.store';
import type { NotificationToast } from '../../types/notification.types';

const TOAST_DURATION_MS = 6_000;

const ToastCard: React.FC<{
  toast: NotificationToast;
  onDismiss: (key: string) => void;
}> = ({ toast, onDismiss }) => {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.key), TOAST_DURATION_MS);
    return () => clearTimeout(timer);
  }, [toast.key, onDismiss]);

  const Icon = toast.type === 'reminder' ? Bell : Info;

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-auto w-80 rounded-2xl border border-indigo-500/30 bg-slate-950/95 p-3 shadow-2xl shadow-black/60 backdrop-blur-xl"
    >
      <div className="flex items-start gap-2.5">
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-300">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold text-white">{toast.title}</p>
          <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-slate-400">
            {toast.message}
          </p>
        </div>
        <button
          type="button"
          onClick={() => onDismiss(toast.key)}
          title="Dismiss notification"
          className="rounded p-1 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
};

/**
 * Real notification toast viewport.
 *
 * Toasts are produced only by newly delivered, unread backend notifications
 * observed through polling. The store dedupes by notification id plus delivery
 * marker, so re-renders and repeated polls cannot duplicate a toast.
 */
export const ToastViewport: React.FC = () => {
  const toasts = useNotificationStore((s) => s.toasts);
  const dismissToast = useNotificationStore((s) => s.dismissToast);

  if (toasts.length === 0) return null;

  return (
    <div className="pointer-events-none fixed right-4 top-20 z-[70] flex flex-col gap-2">
      {toasts.map((toast) => (
        <ToastCard key={toast.key} toast={toast} onDismiss={dismissToast} />
      ))}
    </div>
  );
};

export default ToastViewport;
