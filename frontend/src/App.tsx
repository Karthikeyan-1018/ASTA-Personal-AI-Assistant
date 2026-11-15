import React, { useEffect } from 'react';
import {
  BrowserRouter,
  Routes,
  Route,
  useNavigate,
  useLocation,
  Navigate,
} from 'react-router-dom';
import AppLayout from './components/layout/AppLayout';
import type { NavSectionId } from './components/layout/Sidebar';
import Home from './pages/Home';
import Chat from './pages/Chat';
import Tasks from './pages/Tasks';
import Reminders from './pages/Reminders';
import Memory from './pages/Memory';
import Tools from './pages/Tools';
import SystemMonitor from './pages/SystemMonitor';
import Settings from './pages/Settings';
import Login from './pages/Login';
import { useAuthStore } from './stores/auth.store';
import { useChatStore } from './stores/chat.store';
import { useTaskStore } from './stores/task.store';
import { useReminderStore } from './stores/reminder.store';
import { useMemoryStore } from './stores/memory.store';
import { useToolStore } from './stores/tool.store';

function pathToSection(pathname: string): NavSectionId {
  const clean = pathname.replace(/^\/+/, '').split('/')[0];
  switch (clean) {
    case 'chat':
      return 'chat';
    case 'tasks':
      return 'tasks';
    case 'reminders':
      return 'reminders';
    case 'memory':
      return 'memory';
    case 'tools':
      return 'tools';
    case 'system-monitor':
      return 'system-monitor';
    case 'settings':
      return 'settings';
    default:
      return 'home';
  }
}

function sectionToPath(section: NavSectionId): string {
  if (section === 'home') return '/';
  return `/${section}`;
}

const AppShell: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const activeSection = pathToSection(location.pathname);

  useEffect(() => {
    // Hydrate domain state once an authenticated session is confirmed.
    useChatStore.getState().loadConversations().catch(() => {});
    useTaskStore.getState().fetchTasks().catch(() => {});
    useReminderStore.getState().fetchReminders().catch(() => {});
    useMemoryStore.getState().fetchMemories().catch(() => {});
    useToolStore.getState().fetchTools().catch(() => {});
  }, []);

  const handleNavigate = (section: NavSectionId) => {
    navigate(sectionToPath(section));
  };

  return (
    <AppLayout activeSection={activeSection} onNavigate={handleNavigate}>
      <Routes>
        <Route path="/" element={<Home onNavigate={handleNavigate} />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/reminders" element={<Reminders />} />
        <Route path="/memory" element={<Memory />} />
        <Route path="/tools" element={<Tools />} />
        <Route path="/system-monitor" element={<SystemMonitor />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  );
};

const AuthGate: React.FC = () => {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isCheckingAuth = useAuthStore((s) => s.isCheckingAuth);
  const checkAuth = useAuthStore((s) => s.checkAuth);
  const clearSession = useAuthStore((s) => s.clearSession);

  useEffect(() => {
    checkAuth().catch(() => {});

    // Any 401 from a protected endpoint (including the SSE client) returns the
    // user to the login view without a full page reload.
    const handleUnauthorized = () => clearSession();
    window.addEventListener('asta:unauthorized', handleUnauthorized);

    return () => {
      window.removeEventListener('asta:unauthorized', handleUnauthorized);
    };
  }, [checkAuth, clearSession]);

  if (isCheckingAuth) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-slate-950">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500/30 border-t-indigo-500" />
          <p className="text-xs text-slate-400">Connecting to ASTA…</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Login />;
  }

  return <AppShell />;
};

export function App() {
  return (
    <BrowserRouter>
      <AuthGate />
    </BrowserRouter>
  );
}

export default App;
