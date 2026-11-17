import React from 'react';
import {
  Ban,
  CheckCircle2,
  Clock,
  Loader2,
  XCircle,
} from 'lucide-react';
import type { BackgroundTaskStatus } from '../../types/backgroundTask.types';
import { backgroundTaskStatusLabel } from '../../utils/backgroundTask';

const STATUS_STYLES: Record<
  BackgroundTaskStatus,
  { badge: string; icon: React.ComponentType<{ className?: string }> }
> = {
  queued: {
    badge: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    icon: Clock,
  },
  running: {
    badge: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
    icon: Loader2,
  },
  completed: {
    badge: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
    icon: CheckCircle2,
  },
  failed: {
    badge: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
    icon: XCircle,
  },
  cancelled: {
    badge: 'border-slate-600/40 bg-slate-700/30 text-slate-400',
    icon: Ban,
  },
};

export interface BackgroundTaskStatusBadgeProps {
  status: BackgroundTaskStatus;
}

export const BackgroundTaskStatusBadge: React.FC<
  BackgroundTaskStatusBadgeProps
> = ({ status }) => {
  const style = STATUS_STYLES[status];
  const Icon = style.icon;
  const running = status === 'running';

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${style.badge}`}
      title={`Backend status: ${status}`}
    >
      <Icon className={`h-3 w-3 ${running ? 'animate-spin' : ''}`} />
      {backgroundTaskStatusLabel(status)}
    </span>
  );
};

export default BackgroundTaskStatusBadge;
