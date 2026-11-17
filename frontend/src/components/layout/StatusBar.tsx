import React, { useEffect, useState } from 'react';
import { AlertTriangle, Clock, Layers, Shield, Sparkles } from 'lucide-react';
import { useSettingsStore } from '../../stores/settings.store';
import apiClient from '../../services/api/client';

export interface StatusBarProps {
  activeSectionLabel?: string;
}

export const StatusBar: React.FC<StatusBarProps> = ({
  activeSectionLabel = 'Home',
}) => {
  const [clock, setClock] = useState<string>(() =>
    new Date().toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  );
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const settings = useSettingsStore((s) => s.settings);

  useEffect(() => {
    let mounted = true;
    const probeHealth = async () => {
      try {
        await apiClient.get('/health', { timeout: 3000 });
        if (mounted) setBackendOnline(true);
      } catch {
        if (mounted) setBackendOnline(false);
      }
    };

    probeHealth();
    const interval = window.setInterval(probeHealth, 25000);
    return () => {
      mounted = false;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setClock(
        new Date().toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      );
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  const backendLabel =
    backendOnline === null
      ? 'CHECKING BACKEND'
      : backendOnline
        ? 'BACKEND ONLINE'
        : 'BACKEND UNAVAILABLE';
  const runtimeLabel =
    backendOnline === null
      ? 'Checking backend connection'
      : backendOnline
        ? 'Backend connection confirmed'
        : 'No backend connection confirmed';

  return (
    <footer className="flex h-7 shrink-0 items-center justify-between border-t border-slate-800/80 bg-slate-950 px-3 text-[11px] font-medium text-slate-400 select-none sm:px-4">
      {/* Backend connectivity is reported from the real health probe. */}
      <div className="flex min-w-0 items-center gap-3">
        <div
          className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 ring-1 ${
            backendOnline === true
              ? 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/25'
              : backendOnline === false
                ? 'bg-rose-500/10 text-rose-300 ring-rose-500/25'
                : 'bg-indigo-500/10 text-indigo-300 ring-indigo-500/20'
          }`}
        >
          {backendOnline === false ? (
            <AlertTriangle className="h-3 w-3 text-rose-400" aria-hidden="true" />
          ) : (
            <Sparkles className="h-3 w-3 text-indigo-400" aria-hidden="true" />
          )}
          <span
            className="font-mono text-[10px] font-semibold tracking-wider"
            role="status"
          >
            {backendLabel}
          </span>
        </div>

        <span className="hidden text-slate-700 sm:inline">|</span>

        <div className="hidden items-center gap-1.5 text-slate-300 sm:inline-flex">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              backendOnline === true
                ? 'bg-emerald-400 shadow-sm shadow-emerald-400/50'
                : backendOnline === false
                  ? 'bg-rose-400 shadow-sm shadow-rose-400/50'
                  : 'bg-amber-400 shadow-sm shadow-amber-400/50'
            }`}
            aria-hidden="true"
          />
          <span className="text-[11px] font-medium">{runtimeLabel}</span>
        </div>

        <span className="hidden text-slate-700 md:inline">|</span>

        <span className="hidden truncate text-slate-400 md:inline">
          View: <span className="text-slate-200 font-medium">{activeSectionLabel}</span>
        </span>
      </div>

      {/* Tool policy, configured model and clock are shown without claiming extra telemetry. */}
      <div className="flex items-center gap-3.5">
        <div className="hidden items-center gap-1.5 text-slate-400 lg:inline-flex">
          <Shield className="h-3 w-3 text-indigo-400" aria-hidden="true" />
          <span>Server-Enforced Tool Policy</span>
        </div>

        <span className="hidden text-slate-700 lg:inline">|</span>

        <div className="hidden items-center gap-1.5 text-slate-400 xl:inline-flex">
          <Layers className="h-3 w-3 text-cyan-400" aria-hidden="true" />
          <span className="font-mono text-[10px]">{settings.defaultModel}</span>
        </div>

        <span className="hidden text-slate-700 sm:inline">|</span>

        <span className="font-mono text-[10px] text-slate-400">ASTA Desktop</span>

        <span className="hidden text-slate-700 sm:inline">|</span>

        <div className="flex items-center gap-1 font-mono text-[10px] text-slate-400">
          <Clock className="h-3 w-3 text-slate-400" aria-hidden="true" />
          <span>{clock}</span>
        </div>
      </div>
    </footer>
  );
};

export default StatusBar;
