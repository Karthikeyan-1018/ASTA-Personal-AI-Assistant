import React from 'react';
import Sidebar, { type NavSectionId } from './Sidebar';
import Topbar from './Topbar';
import StatusBar from './StatusBar';
import ToastViewport from '../notifications/ToastViewport';
import { useUIStore } from '../../stores/ui.store';

export interface AppLayoutProps {
  children?: React.ReactNode;
  activeSection: NavSectionId;
  onNavigate: (section: NavSectionId) => void;
  pageAction?: React.ReactNode;
}

/** Keep the status bar labels aligned with the navigation labels in Sidebar. */
const SECTION_LABELS: Record<NavSectionId, string> = {
  home: 'Home',
  chat: 'Chat',
  tasks: 'Tasks',
  reminders: 'Reminders',
  recurrences: 'Recurrences',
  'background-tasks': 'Background Tasks',
  memory: 'Memory',
  tools: 'Tools',
  'system-monitor': 'System Monitor',
  settings: 'Settings',
};

export const AppLayout: React.FC<AppLayoutProps> = ({
  children,
  activeSection,
  onNavigate,
  pageAction,
}) => {
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const mobileMenuOpen = useUIStore((s) => s.mobileMenuOpen);
  const setMobileMenuOpen = useUIStore((s) => s.setMobileMenuOpen);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-950 text-slate-100 antialiased selection:bg-indigo-500/30 selection:text-indigo-200">
      {/* Navigation Sidebar */}
      <Sidebar
        activeSection={activeSection}
        onSelectSection={onNavigate}
        collapsed={sidebarCollapsed}
        onToggleCollapse={toggleSidebar}
        mobileOpen={mobileMenuOpen}
        onCloseMobile={() => setMobileMenuOpen(false)}
      />

      {/* Main App Column */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-gradient-to-b from-slate-950 via-slate-900/60 to-slate-950">
        <Topbar
          activeSection={activeSection}
          onOpenMobileMenu={() => setMobileMenuOpen(true)}
          pageAction={pageAction}
        />

        <main className="relative flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
          <div className="mx-auto h-full w-full max-w-7xl">
            {children}
          </div>
        </main>

        <StatusBar activeSectionLabel={SECTION_LABELS[activeSection]} />
      </div>

      <ToastViewport />
    </div>
  );
};

export default AppLayout;
