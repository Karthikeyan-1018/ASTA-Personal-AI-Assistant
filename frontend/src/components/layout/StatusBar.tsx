import React, { useEffect, useState } from 'react';
import { Shield, Sparkles, Clock, Layers } from 'lucide-react';
import { useSettingsStore } from '../../stores/settings.store';
import apiClient from '../../services/api/client';

export interface StatusBarProps {
  activeSectionLabel?: string;
}

export const StatusBar: React.FC<StatusBarProps> = ({
  activeSectionLabel = 'Home',
}) => {
  const [clock, setClock] = useState<string>(() =>
    new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
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
        new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      );
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <footer className="flex h-7 shrink-0 items-center justify-between border-t border-slate-800/80 bg-slate-950 px-3 text-[11px] font-medium text-slate-400 select-none sm:px-4">
      {/* Left: Frontend Demonstration Mode & Architecture Status */}
      <div className="flex min-w-0 items-center gap-3">
        {/* Demonstration Mode Pill */}
        {backendOnline ? (
          <div className="inline-flex items-center gap-1.5 rounded bg-emerald-500/10 px-2 py-0.5 text-emerald-300 ring-1 ring-emerald-500/25">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
            <span className="font-mono text-[10px] font-semibold tracking-wider">
              API CONNECTED
            </span>
          </div>
        ) : (
          <div className="inline-flex items-center gap-1.5 rounded bg-indigo-500/10 px-2 py-0.5 text-indigo-300 ring-1 ring-indigo-500/20">
            <Sparkles className="h-3 w-3 text-indigo-400" />
            <span className="font-mono text-[10px] font-semibold tracking-wider">
              FRONTEND DEMO MODE
            </span>
          </div>
        )}

        <span className="hidden text-slate-700 sm:inline">|</span>

        {/* Runtime State */}
        <div className="hidden items-center gap-1.5 text-slate-300 sm:inline-flex">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              backendOnline ? 'bg-emerald-400 shadow-sm shadow-emerald-400/50' : 'bg-amber-400 shadow-sm shadow-amber-400/50'
            }`}
          />
          <span className="text-[11px] font-medium">
            {backendOnline ? 'FastAPI Backend Active' : 'Local State Runtime Active'}
          </span>
        </div>

        <span className="hidden text-slate-700 md:inline">|</span>

        {/* Active Scope / Section */}
        <span className="hidden truncate text-slate-400 md:inline">
          View: <span className="text-slate-200 font-medium">{activeSectionLabel}</span>
        </span>
      </div>

      {/* Right: Security Sandbox, Version, Model, Clock */}
      <div className="flex items-center gap-3.5">
        <div className="hidden items-center gap-1.5 text-slate-400 lg:inline-flex">
          <Shield className="h-3 w-3 text-indigo-400" />
          <span>Sandbox Guard</span>
        </div>

        <span className="hidden text-slate-700 lg:inline">|</span>

        <div className="hidden items-center gap-1.5 text-slate-400 xl:inline-flex">
          <Layers className="h-3 w-3 text-cyan-400" />
          <span className="font-mono text-[10px]">{settings.defaultModel}</span>
        </div>

        <span className="hidden text-slate-700 sm:inline">|</span>

        <span className="font-mono text-[10px] text-slate-400">
          ASTA Desktop v1.4.0
        </span>

        <span className="hidden text-slate-700 sm:inline">|</span>

        <div className="flex items-center gap-1 font-mono text-[10px] text-slate-400">
          <Clock className="h-3 w-3 text-slate-400" />
          <span>{clock}</span>
        </div>
      </div>
    </footer>
  );
};

export default StatusBar;
