import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { AppState } from 'react-native';
import {
  LocalTask,
  CreateTaskInput,
  UpdateTaskInput,
  TaskFilter,
  TaskCategory,
  TaskSortOption,
} from '../types/task';
import {
  getAllTasks,
  createTask,
  updateTask,
  toggleTaskCompletion,
  deleteTask,
  getFilteredTasks,
} from '../database/taskRepository';
import { syncService } from '../sync/syncService';
import { SyncStatusInfo } from '../types/sync';

interface TasksContextType {
  tasks: LocalTask[];
  isLoading: boolean;
  syncStatus: SyncStatusInfo;
  refreshTasks: () => Promise<void>;
  addNewTask: (input: CreateTaskInput) => Promise<LocalTask>;
  modifyTask: (localId: string, updates: UpdateTaskInput) => Promise<LocalTask>;
  toggleTask: (localId: string) => Promise<LocalTask>;
  removeTask: (localId: string) => Promise<void>;
  filterTasks: (
    filter: TaskFilter,
    category?: TaskCategory | null,
    search?: string,
    sort?: TaskSortOption
  ) => Promise<LocalTask[]>;
  triggerSync: () => Promise<{ success: boolean; message: string }>;
}

const TasksContext = createContext<TasksContextType | null>(null);

export const TasksProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [tasks, setTasks] = useState<LocalTask[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [syncStatus, setSyncStatus] = useState<SyncStatusInfo>(syncService.getStatusInfoSync());

  const refresh = useCallback(async () => {
    try {
      const data = await getAllTasks();
      setTasks(data);
      const s = await syncService.getStatusInfo();
      setSyncStatus(s);
    } catch (err) {
      console.warn('Failed to refresh tasks:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void refresh();
    const unsubscribeSync = syncService.subscribe((status) => {
      setSyncStatus(status);
      // The synchronous snapshot omits SQLite queue counts; refresh them so
      // failed and queued changes are visible in Settings during background sync.
      void syncService.getStatusInfo().then((detailedStatus) => {
        if (active) setSyncStatus(detailedStatus);
      }).catch(() => {});
      // Auto reload tasks on sync completion
      if (!status.isSyncing) {
        getAllTasks().then(setTasks).catch(() => {});
      }
    });
    return () => {
      active = false;
      unsubscribeSync();
    };
  }, [refresh]);

  useEffect(() => {
    let disposed = false;

    const checkAndSync = async (loadCredentials = false) => {
      if (disposed) return;
      try {
        if (loadCredentials) await syncService.initCredentials();
        const current = syncService.getStatusInfoSync();
        if (!current.serverUrl || current.syncMode !== 'live' || current.isSyncing) return;

        const reachable = await syncService.checkReachability();
        const latest = syncService.getStatusInfoSync();
        if (!disposed && reachable && latest.isAuthenticated && !latest.isSyncing) {
          await syncService.triggerSync();
        }
      } catch {
        // Offline capture remains available; the next foreground/interval check retries.
      }
    };

    void checkAndSync(true);
    const interval = setInterval(() => void checkAndSync(), 30_000);
    const appStateSubscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') void checkAndSync();
    });

    return () => {
      disposed = true;
      clearInterval(interval);
      appStateSubscription.remove();
    };
  }, []);

  const addNewTask = async (input: CreateTaskInput): Promise<LocalTask> => {
    const created = await createTask(input);
    await refresh();
    // Try immediately; if the laptop is offline, the local queue is retained
    // and the foreground/interval retry will send it when ASTA becomes reachable.
    syncService.triggerSync().catch(() => {});
    return created;
  };

  const modifyTask = async (localId: string, updates: UpdateTaskInput): Promise<LocalTask> => {
    const updated = await updateTask(localId, updates);
    await refresh();
    // Try immediately; offline changes stay queued for automatic retry.
    syncService.triggerSync().catch(() => {});
    return updated;
  };

  const toggleTask = async (localId: string): Promise<LocalTask> => {
    const toggled = await toggleTaskCompletion(localId);
    await refresh();
    // Try immediately; offline changes stay queued for automatic retry.
    syncService.triggerSync().catch(() => {});
    return toggled;
  };

  const removeTask = async (localId: string): Promise<void> => {
    await deleteTask(localId);
    await refresh();
    // Try immediately; offline changes stay queued for automatic retry.
    syncService.triggerSync().catch(() => {});
  };

  const filterTasks = async (
    filter: TaskFilter,
    category?: TaskCategory | null,
    search?: string,
    sort?: TaskSortOption
  ): Promise<LocalTask[]> => {
    return getFilteredTasks(tasks, filter, category, search, sort);
  };

  const triggerSync = async () => {
    const res = await syncService.triggerSync();
    await refresh();
    return res;
  };

  return (
    <TasksContext.Provider
      value={{
        tasks,
        isLoading,
        syncStatus,
        refreshTasks: refresh,
        addNewTask,
        modifyTask,
        toggleTask,
        removeTask,
        filterTasks,
        triggerSync,
      }}
    >
      {children}
    </TasksContext.Provider>
  );
};

export function useTasks(): TasksContextType {
  const context = useContext(TasksContext);
  if (!context) {
    throw new Error('useTasks must be used within a TasksProvider');
  }
  return context;
}
