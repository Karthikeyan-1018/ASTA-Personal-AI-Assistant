import { create } from 'zustand';
import authApi from '../services/api/auth.api';
import type { LoginRequest } from '../types/api.types';

export interface UserProfile {
  id: string | number;
  name: string;
  email: string;
  role: string;
  status: 'online' | 'busy' | 'away';
  isAuthenticated: boolean;
}

interface AuthStoreState {
  user: UserProfile;
  isAuthenticated: boolean;
  isCheckingAuth: boolean;
  isLoading: boolean;
  error: string | null;
  checkAuth: () => Promise<boolean>;
  login: (credentials: LoginRequest) => Promise<boolean>;
  setupOwner: (
    username: string,
    password: string,
    setupToken: string
  ) => Promise<boolean>;
  clearSession: () => void;
  clearError: () => void;
  logout: () => Promise<void>;
  updateStatus: (status: UserProfile['status']) => void;
  updateName: (name: string) => void;
}

const DEFAULT_USER: UserProfile = {
  id: 0,
  name: 'Owner',
  email: 'owner@asta.local',
  role: 'Primary Operator',
  status: 'online',
  isAuthenticated: false,
};

function profileFromOwner(owner: { id: number; username: string }): UserProfile {
  return {
    id: owner.id,
    name: owner.username,
    email: `${owner.username}@asta.local`,
    role: 'Primary Operator (Owner)',
    status: 'online',
    isAuthenticated: true,
  };
}

function messageFromError(err: unknown): string {
  if (err instanceof Error && err.message) {
    return err.message;
  }
  return 'Unable to authenticate. Please try again.';
}

export const useAuthStore = create<AuthStoreState>((set, get) => ({
  user: DEFAULT_USER,
  isAuthenticated: false,
  isCheckingAuth: true,
  isLoading: false,
  error: null,

  checkAuth: async () => {
    set({ isCheckingAuth: true });

    try {
      const me = await authApi.getMe();
      set({
        user: profileFromOwner(me.owner),
        isAuthenticated: true,
        isCheckingAuth: false,
        error: null,
      });
      return true;
    } catch {
      // Unauthenticated is an expected state: show the login view rather than
      // surfacing a backend error on first load.
      set({
        user: DEFAULT_USER,
        isAuthenticated: false,
        isCheckingAuth: false,
      });
      return false;
    }
  },

  login: async (credentials: LoginRequest) => {
    try {
      set({ isLoading: true, error: null });
      const res = await authApi.login(credentials);
      set({
        user: profileFromOwner(res.owner),
        isAuthenticated: true,
        isCheckingAuth: false,
        isLoading: false,
        error: null,
      });
      return true;
    } catch (err) {
      set({
        isAuthenticated: false,
        isLoading: false,
        error: messageFromError(err),
      });
      return false;
    }
  },

  setupOwner: async (username, password, setupToken) => {
    try {
      set({ isLoading: true, error: null });
      await authApi.setupOwner({ username, password }, setupToken);

      // Setup only creates the account; establish a session immediately so the
      // user lands in the app instead of having to type the password twice.
      return await get().login({ username, password });
    } catch (err) {
      set({
        isLoading: false,
        error: messageFromError(err),
      });
      return false;
    }
  },

  clearSession: () => {
    set({
      user: DEFAULT_USER,
      isAuthenticated: false,
      isCheckingAuth: false,
      isLoading: false,
    });
  },

  clearError: () => set({ error: null }),

  logout: async () => {
    try {
      await authApi.logout();
    } catch {
      // The local session is cleared even if the network call fails.
    } finally {
      get().clearSession();
    }
  },

  updateStatus: (status) =>
    set((state) => ({ user: { ...state.user, status } })),

  updateName: (name) =>
    set((state) => ({ user: { ...state.user, name } })),
}));

export default useAuthStore;
