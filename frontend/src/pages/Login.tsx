import React, { useState } from 'react';
import {
  ShieldCheck,
  User,
  Lock,
  KeyRound,
  Loader2,
  AlertCircle,
  ArrowRight,
} from 'lucide-react';
import { useAuthStore } from '../stores/auth.store';

type Mode = 'login' | 'setup';

const INPUT_CLASS =
  'h-11 w-full rounded-xl border border-slate-800 bg-slate-900/80 pl-10 pr-3 text-sm text-slate-100 placeholder-slate-500 transition-colors focus:border-indigo-500/60 focus:bg-slate-900 focus:outline-none focus:ring-1 focus:ring-indigo-500/30';

export const Login: React.FC = () => {
  const login = useAuthStore((s) => s.login);
  const setupOwner = useAuthStore((s) => s.setupOwner);
  const isLoading = useAuthStore((s) => s.isLoading);
  const storeError = useAuthStore((s) => s.error);
  const clearError = useAuthStore((s) => s.clearError);

  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [setupToken, setSetupToken] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);

  const error = localError || storeError;

  const switchMode = (next: Mode) => {
    setMode(next);
    setLocalError(null);
    clearError();
    setPassword('');
    setConfirmPassword('');
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setLocalError(null);
    clearError();

    const trimmedUsername = username.trim();

    if (!trimmedUsername || !password) {
      setLocalError('Enter your username and password.');
      return;
    }

    if (mode === 'setup') {
      if (password !== confirmPassword) {
        setLocalError('The passwords do not match.');
        return;
      }

      if (!setupToken.trim()) {
        setLocalError(
          'Enter the owner setup token from AUTH_OWNER_SETUP_TOKEN in .env.'
        );
        return;
      }

      await setupOwner(trimmedUsername, password, setupToken.trim());
      return;
    }

    await login({ username: trimmedUsername, password });
  };

  return (
    <div className="relative flex h-screen w-screen items-center justify-center overflow-hidden bg-slate-950 px-4">
      {/* Ambient background */}
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -top-40 left-1/2 h-96 w-96 -translate-x-1/2 rounded-full bg-indigo-600/20 blur-3xl" />
        <div className="absolute bottom-0 right-0 h-80 w-80 rounded-full bg-cyan-500/10 blur-3xl" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(79,70,229,0.10),transparent_55%)]" />
      </div>

      <div className="relative w-full max-w-md">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-indigo-500/30 bg-indigo-500/10 shadow-lg shadow-indigo-900/30">
            <ShieldCheck className="h-7 w-7 text-indigo-400" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white">ASTA</h1>
          <p className="mt-1 text-sm text-slate-400">
            {mode === 'login'
              ? 'Sign in to your personal assistant'
              : 'Create the single owner account'}
          </p>
        </div>

        <div className="rounded-3xl border border-slate-800 bg-slate-950/80 p-6 shadow-2xl shadow-black/60 backdrop-blur-xl sm:p-8">
          {/* Mode switch */}
          <div className="mb-6 grid grid-cols-2 gap-1 rounded-xl border border-slate-800 bg-slate-900/60 p-1">
            <button
              type="button"
              onClick={() => switchMode('login')}
              className={`rounded-lg px-3 py-2 text-xs font-semibold transition ${
                mode === 'login'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Sign in
            </button>
            <button
              type="button"
              onClick={() => switchMode('setup')}
              className={`rounded-lg px-3 py-2 text-xs font-semibold transition ${
                mode === 'setup'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              First-time setup
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="asta-username"
                className="mb-1.5 block text-xs font-medium text-slate-300"
              >
                Username
              </label>
              <div className="relative">
                <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                <input
                  id="asta-username"
                  type="text"
                  autoComplete="username"
                  autoFocus
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="owner"
                  className={INPUT_CLASS}
                  disabled={isLoading}
                />
              </div>
            </div>

            <div>
              <label
                htmlFor="asta-password"
                className="mb-1.5 block text-xs font-medium text-slate-300"
              >
                Password
              </label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                <input
                  id="asta-password"
                  type="password"
                  autoComplete={
                    mode === 'login' ? 'current-password' : 'new-password'
                  }
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="••••••••••••"
                  className={INPUT_CLASS}
                  disabled={isLoading}
                />
              </div>
            </div>

            {mode === 'setup' && (
              <>
                <div>
                  <label
                    htmlFor="asta-confirm-password"
                    className="mb-1.5 block text-xs font-medium text-slate-300"
                  >
                    Confirm password
                  </label>
                  <div className="relative">
                    <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                    <input
                      id="asta-confirm-password"
                      type="password"
                      autoComplete="new-password"
                      value={confirmPassword}
                      onChange={(event) =>
                        setConfirmPassword(event.target.value)
                      }
                      placeholder="••••••••••••"
                      className={INPUT_CLASS}
                      disabled={isLoading}
                    />
                  </div>
                </div>

                <div>
                  <label
                    htmlFor="asta-setup-token"
                    className="mb-1.5 block text-xs font-medium text-slate-300"
                  >
                    Owner setup token
                  </label>
                  <div className="relative">
                    <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                    <input
                      id="asta-setup-token"
                      type="password"
                      autoComplete="off"
                      value={setupToken}
                      onChange={(event) => setSetupToken(event.target.value)}
                      placeholder="AUTH_OWNER_SETUP_TOKEN"
                      className={INPUT_CLASS}
                      disabled={isLoading}
                    />
                  </div>
                  <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
                    Setup is only available when{' '}
                    <code className="rounded bg-slate-800/80 px-1 py-0.5 text-slate-300">
                      AUTH_OWNER_SETUP_TOKEN
                    </code>{' '}
                    is set in the backend{' '}
                    <code className="rounded bg-slate-800/80 px-1 py-0.5 text-slate-300">
                      .env
                    </code>
                    . Alternatively create the owner from a terminal with{' '}
                    <code className="rounded bg-slate-800/80 px-1 py-0.5 text-slate-300">
                      python scripts/create_owner.py
                    </code>
                    .
                  </p>
                </div>
              </>
            )}

            {error && (
              <div className="flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2.5 text-xs text-rose-200">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
                <span>{error}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={isLoading}
              className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 text-sm font-semibold text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isLoading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {mode === 'login' ? 'Signing in…' : 'Creating owner…'}
                </>
              ) : (
                <>
                  {mode === 'login' ? 'Sign in' : 'Create owner & sign in'}
                  <ArrowRight className="h-4 w-4" />
                </>
              )}
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-[11px] text-slate-500">
          Sessions are stored in an HttpOnly cookie. Your password is never kept
          in the browser.
        </p>
      </div>
    </div>
  );
};

export default Login;
