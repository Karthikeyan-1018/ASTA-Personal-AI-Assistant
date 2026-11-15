import { create } from 'zustand';

export interface AppNotification {
  id: string;
  title: string;
  message: string;
  category: 'system' | 'task' | 'reminder' | 'security';
  timestamp: string;
  read: boolean;
  actionUrl?: string;
}

const INITIAL_NOTIFICATIONS: AppNotification[] = [
  {
    id: 'notif-1',
    title: 'Phase 14 Frontend Initialized',
    message: 'Local desktop shell is running with full mock state and design system.',
    category: 'system',
    timestamp: 'Just now',
    read: false,
  },
  {
    id: 'notif-2',
    title: 'Memory Index Synchronized',
    message: 'Local semantic memory records are loaded and searchable.',
    category: 'system',
    timestamp: '10m ago',
    read: false,
  },
  {
    id: 'notif-3',
    title: 'Reminder Pending: Diagnostic Snapshot',
    message: 'Scheduled routine check set for 20:00 tonight.',
    category: 'reminder',
    timestamp: '1h ago',
    read: true,
  },
];

interface UIStoreState {
  sidebarCollapsed: boolean;
  mobileMenuOpen: boolean;
  globalSearchOpen: boolean;
  searchQuery: string;
  notifications: AppNotification[];
  notificationsOpen: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setMobileMenuOpen: (open: boolean) => void;
  setGlobalSearchOpen: (open: boolean) => void;
  setSearchQuery: (query: string) => void;
  toggleNotifications: () => void;
  setNotificationsOpen: (open: boolean) => void;
  markNotificationRead: (id: string) => void;
  markAllNotificationsRead: () => void;
  dismissNotification: (id: string) => void;
  addNotification: (notif: Omit<AppNotification, 'id' | 'timestamp' | 'read'>) => void;
}

export const useUIStore = create<UIStoreState>((set) => ({
  sidebarCollapsed: false,
  mobileMenuOpen: false,
  globalSearchOpen: false,
  searchQuery: '',
  notifications: INITIAL_NOTIFICATIONS,
  notificationsOpen: false,
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
  setMobileMenuOpen: (open) => set({ mobileMenuOpen: open }),
  setGlobalSearchOpen: (open) => set({ globalSearchOpen: open }),
  setSearchQuery: (searchQuery) => set({ searchQuery }),
  toggleNotifications: () => set((state) => ({ notificationsOpen: !state.notificationsOpen })),
  setNotificationsOpen: (open) => set({ notificationsOpen: open }),
  markNotificationRead: (id) =>
    set((state) => ({
      notifications: state.notifications.map((n) => (n.id === id ? { ...n, read: true } : n)),
    })),
  markAllNotificationsRead: () =>
    set((state) => ({
      notifications: state.notifications.map((n) => ({ ...n, read: true })),
    })),
  dismissNotification: (id) =>
    set((state) => ({
      notifications: state.notifications.filter((n) => n.id !== id),
    })),
  addNotification: (notif) =>
    set((state) => ({
      notifications: [
        {
          ...notif,
          id: `notif-${Date.now()}`,
          timestamp: 'Just now',
          read: false,
        },
        ...state.notifications,
      ],
    })),
}));
