import { create } from 'zustand';

/**
 * Layout and transient UI state only.
 *
 * Notification records are owned by the backend-backed notification store;
 * this store intentionally contains no notification data or mock records.
 */
interface UIStoreState {
  sidebarCollapsed: boolean;
  mobileMenuOpen: boolean;
  globalSearchOpen: boolean;
  searchQuery: string;
  notificationsOpen: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setMobileMenuOpen: (open: boolean) => void;
  setGlobalSearchOpen: (open: boolean) => void;
  setSearchQuery: (query: string) => void;
  toggleNotifications: () => void;
  setNotificationsOpen: (open: boolean) => void;
}

export const useUIStore = create<UIStoreState>((set) => ({
  sidebarCollapsed: false,
  mobileMenuOpen: false,
  globalSearchOpen: false,
  searchQuery: '',
  notificationsOpen: false,
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
  setMobileMenuOpen: (open) => set({ mobileMenuOpen: open }),
  setGlobalSearchOpen: (open) => set({ globalSearchOpen: open }),
  setSearchQuery: (searchQuery) => set({ searchQuery }),
  toggleNotifications: () => set((state) => ({ notificationsOpen: !state.notificationsOpen })),
  setNotificationsOpen: (open) => set({ notificationsOpen: open }),
}));
