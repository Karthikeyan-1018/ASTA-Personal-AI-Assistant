import React, { useEffect } from 'react';
import {
  SafeAreaView,
  StatusBar,
  StyleSheet,
  View,
  Modal,
} from 'react-native';
import { ThemeProvider, useTheme } from './theme/themeContext';
import { NavigationProvider, useNavigation } from './navigation/NavigationContext';
import { TasksProvider } from './hooks/useTasksContext';
import { getDatabase } from './database/db';
import { BottomTabBar } from './navigation/BottomTabBar';
import { FloatingActionButton } from './components/common/FloatingActionButton';
import { DashboardScreen } from './features/dashboard/DashboardScreen';
import { TasksScreen } from './features/tasks/TasksScreen';
import { CalendarScreen } from './features/calendar/CalendarScreen';
import { AssistantScreen } from './features/assistant/AssistantScreen';
import { SettingsScreen } from './features/settings/SettingsScreen';
import { TaskFormBottomSheet } from './components/tasks/TaskFormBottomSheet';
import { TaskDetailsModal } from './components/tasks/TaskDetailsModal';
import { FocusScreen } from './features/focus/FocusScreen';
import { NotesScreen } from './features/notes/NotesScreen';
import { AnalyticsScreen } from './features/analytics/AnalyticsScreen';
import { RemindersScreen } from './features/reminders/RemindersScreen';

function MainAppShell() {
  const { colors, isDark } = useTheme();
  const { currentTab, activeModal, closeModal } = useNavigation();

  useEffect(() => {
    // Ensure SQLite database and tables are initialized
    getDatabase().catch((err) => {
      console.warn('Database initialization warning:', err);
    });
  }, []);

  return (
    <SafeAreaView style={[styles.safeArea, { backgroundColor: colors.background }]}>
      <StatusBar
        barStyle={isDark ? 'light-content' : 'dark-content'}
      />

      <View style={styles.mainContainer}>
        {/* Tab Router */}
        {currentTab === 'home' && <DashboardScreen />}
        {currentTab === 'tasks' && <TasksScreen />}
        {currentTab === 'calendar' && <CalendarScreen />}
        {currentTab === 'asta' && <AssistantScreen />}
        {currentTab === 'profile' && <SettingsScreen />}

        {/* Floating Action Button (visible on Home, Tasks, Calendar) */}
        {(currentTab === 'home' || currentTab === 'tasks' || currentTab === 'calendar') && (
          <FloatingActionButton />
        )}

        {/* Persistent Bottom Tab Bar */}
        <BottomTabBar />

        {/* Global Modals */}
        {/* 1. Quick Task Creation Bottom Sheet */}
        <TaskFormBottomSheet
          visible={activeModal?.type === 'create_task'}
          onClose={closeModal}
          initialDate={activeModal?.type === 'create_task' ? activeModal.initialDate : undefined}
          initialCategory={activeModal?.type === 'create_task' ? activeModal.initialCategory : undefined}
        />

        {/* 2. Task Details & Subtasks Modal */}
        <TaskDetailsModal
          visible={activeModal?.type === 'task_details'}
          taskId={activeModal?.type === 'task_details' ? activeModal.taskId : null}
          onClose={closeModal}
        />

        {/* 3. Focus Timer Modal */}
        <Modal
          visible={activeModal?.type === 'focus_mode'}
          animationType="slide"
          onRequestClose={closeModal}
        >
          <FocusScreen
            initialTaskId={activeModal?.type === 'focus_mode' ? activeModal.taskId : undefined}
            onClose={closeModal}
          />
        </Modal>

        {/* 4. Notes Modal */}
        <Modal
          visible={activeModal?.type === 'notes'}
          animationType="slide"
          onRequestClose={closeModal}
        >
          <NotesScreen onClose={closeModal} />
        </Modal>

        {/* 5. Analytics Modal */}
        <Modal
          visible={activeModal?.type === 'analytics'}
          animationType="slide"
          onRequestClose={closeModal}
        >
          <AnalyticsScreen onClose={closeModal} />
        </Modal>

        {/* 6. Reminders Modal */}
        <Modal
          visible={activeModal?.type === 'reminders'}
          animationType="slide"
          onRequestClose={closeModal}
        >
          <RemindersScreen onClose={closeModal} />
        </Modal>
      </View>
    </SafeAreaView>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <NavigationProvider>
        <TasksProvider>
          <MainAppShell />
        </TasksProvider>
      </NavigationProvider>
    </ThemeProvider>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
  },
  mainContainer: {
    flex: 1,
    position: 'relative',
  },
});
