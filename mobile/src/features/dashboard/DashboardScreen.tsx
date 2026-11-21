import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
} from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useTasks } from '../../hooks/useTasksContext';
import { useNavigation } from '../../navigation/NavigationContext';
import { ProgressRing } from '../../components/common/ProgressRing';
import { TaskCard } from '../../components/tasks/TaskCard';
import { Icon, IconName } from '../../components/common/Icon';
import { Header } from '../../components/common/Header';

export const DashboardScreen: React.FC = () => {
  const { colors, borderRadius, spacing } = useTheme();
  const { tasks, toggleTask, triggerSync } = useTasks();
  const { switchTab, openModal } = useNavigation();

  const todayStr = new Date().toISOString().slice(0, 10);

  // Statistics calculation based on real local SQLite data
  const totalTasks = tasks.length;
  const completedTasks = tasks.filter((t) => t.is_completed || t.status === 'completed').length;
  const pendingTasks = tasks.filter((t) => !t.is_completed && t.status !== 'completed');
  
  const todayTasks = tasks.filter((t) => t.due_date === todayStr);
  const todayCompleted = todayTasks.filter((t) => t.is_completed).length;
  const todayRemaining = todayTasks.length - todayCompleted;
  const todayProgress = todayTasks.length > 0 ? todayCompleted / todayTasks.length : 0;

  const urgentTasks = pendingTasks.filter(
    (t) => t.priority === 'urgent' || t.priority === 'high'
  );

  const upcomingTasks = pendingTasks
    .filter((t) => t.due_date && t.due_date >= todayStr)
    .sort((a, b) => (a.due_date || '').localeCompare(b.due_date || ''))
    .slice(0, 4);

  const quickActions: { label: string; icon: IconName; onPress: () => void }[] = [
    {
      label: 'New Task',
      icon: 'plus',
      onPress: () => openModal({ type: 'create_task' }),
    },
    {
      label: 'Focus Timer',
      icon: 'clock',
      onPress: () => openModal({ type: 'focus_mode' }),
    },
    {
      label: 'Quick Note',
      icon: 'notes',
      onPress: () => openModal({ type: 'notes' }),
    },
    {
      label: 'Analytics',
      icon: 'analytics',
      onPress: () => openModal({ type: 'analytics' }),
    },
  ];

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: 110 }]}
      showsVerticalScrollIndicator={false}
    >
      <Header onSyncPress={() => triggerSync()} />

      {/* Daily Progress Card - Linear & Things 3 styled */}
      <View
        style={[
          styles.progressCard,
          {
            backgroundColor: colors.surface,
            borderColor: colors.cardBorder,
            borderRadius: borderRadius.xl,
          },
        ]}
      >
        <View style={styles.cardLeft}>
          <Text style={[styles.cardEyebrow, { color: colors.accentLight }]}>
            TODAY'S EXECUTION
          </Text>
          <Text style={[styles.cardTitle, { color: colors.textPrimary }]}>
            Your day, organized.
          </Text>
          <Text style={[styles.cardDesc, { color: colors.textSecondary }]}>
            {todayTasks.length === 0
              ? 'No deadlines scheduled for today. Great time for deep work.'
              : `${todayCompleted} of ${todayTasks.length} tasks completed.`}
          </Text>

          <View style={styles.progressStatsRow}>
            <View style={styles.statItem}>
              <Text style={[styles.statNum, { color: colors.textPrimary }]}>{todayTasks.length}</Text>
              <Text style={[styles.statLabel, { color: colors.textMuted }]}>Today</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.statItem}>
              <Text style={[styles.statNum, { color: colors.success }]}>{todayCompleted}</Text>
              <Text style={[styles.statLabel, { color: colors.textMuted }]}>Done</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.statItem}>
              <Text style={[styles.statNum, { color: colors.warning }]}>{todayRemaining}</Text>
              <Text style={[styles.statLabel, { color: colors.textMuted }]}>Remaining</Text>
            </View>
          </View>
        </View>

        <View style={styles.cardRight}>
          <ProgressRing
            progress={todayTasks.length > 0 ? todayProgress : 0}
            size={96}
            strokeWidth={9}
            color={colors.accentLight}
            centerText={`${Math.round((todayTasks.length > 0 ? todayProgress : 0) * 100)}%`}
            subText="DONE"
          />
        </View>
      </View>

      {/* Quick Action Buttons */}
      <View style={styles.quickActionsGrid}>
        {quickActions.map((qa) => (
          <Pressable
            key={qa.label}
            onPress={qa.onPress}
            style={({ pressed }) => [
              styles.actionTile,
              {
                backgroundColor: colors.surface,
                borderColor: colors.border,
                borderRadius: borderRadius.md,
                opacity: pressed ? 0.8 : 1,
              },
            ]}
          >
            <View
              style={[
                styles.actionIconCircle,
                { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.sm },
              ]}
            >
              <Icon name={qa.icon} size={18} color={colors.accentLight} />
            </View>
            <Text style={[styles.actionLabel, { color: colors.textPrimary }]}>{qa.label}</Text>
          </Pressable>
        ))}
      </View>

      {/* Urgent & Important Section (if any) */}
      {urgentTasks.length > 0 ? (
        <View style={styles.sectionWrap}>
          <View style={styles.sectionHeaderRow}>
            <View style={styles.sectionTitleRow}>
              <Text style={styles.fireIcon}>⚡</Text>
              <Text style={[styles.sectionHeading, { color: colors.textPrimary }]}>
                Important & Urgent
              </Text>
            </View>
            <Text style={[styles.sectionCount, { color: colors.priorityUrgent }]}>
              {urgentTasks.length}
            </Text>
          </View>

          {urgentTasks.slice(0, 3).map((task) => (
            <TaskCard key={task.local_id} task={task} onToggle={toggleTask} />
          ))}
        </View>
      ) : null}

      {/* Today's Tasks */}
      <View style={styles.sectionWrap}>
        <View style={styles.sectionHeaderRow}>
          <Text style={[styles.sectionHeading, { color: colors.textPrimary }]}>Today's Agenda</Text>
          <Pressable onPress={() => switchTab('tasks')}>
            <Text style={[styles.viewAllText, { color: colors.accentLight }]}>View all ›</Text>
          </Pressable>
        </View>

        {todayTasks.length === 0 ? (
          <View
            style={[
              styles.emptyStateCard,
              {
                backgroundColor: colors.surface,
                borderColor: colors.border,
                borderRadius: borderRadius.lg,
              },
            ]}
          >
            <Text style={styles.emptyIcon}>✦</Text>
            <Text style={[styles.emptyTitle, { color: colors.textPrimary }]}>Clear Horizon</Text>
            <Text style={[styles.emptySubtitle, { color: colors.textSecondary }]}>
              No tasks due today. Tap the button below to add your next goal.
            </Text>
            <Pressable
              onPress={() => openModal({ type: 'create_task', initialDate: todayStr })}
              style={[
                styles.emptyActionBtn,
                { backgroundColor: colors.accent, borderRadius: borderRadius.sm },
              ]}
            >
              <Text style={styles.emptyActionBtnText}>＋ Add task for today</Text>
            </Pressable>
          </View>
        ) : (
          todayTasks.map((task) => (
            <TaskCard key={task.local_id} task={task} onToggle={toggleTask} />
          ))
        )}
      </View>

      {/* Upcoming Deadlines */}
      {upcomingTasks.length > 0 ? (
        <View style={styles.sectionWrap}>
          <View style={styles.sectionHeaderRow}>
            <Text style={[styles.sectionHeading, { color: colors.textPrimary }]}>
              Upcoming Deadlines
            </Text>
            <Pressable onPress={() => switchTab('calendar')}>
              <Text style={[styles.viewAllText, { color: colors.accentLight }]}>Calendar ›</Text>
            </Pressable>
          </View>

          {upcomingTasks.map((task) => (
            <TaskCard key={task.local_id} task={task} onToggle={toggleTask} />
          ))}
        </View>
      ) : null}
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 16,
  },
  progressCard: {
    flexDirection: 'row',
    padding: 18,
    borderWidth: 1,
    marginBottom: 16,
    alignItems: 'center',
  },
  cardLeft: {
    flex: 1,
    paddingRight: 10,
  },
  cardRight: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  cardEyebrow: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1.5,
  },
  cardTitle: {
    fontSize: 20,
    fontWeight: '800',
    letterSpacing: -0.4,
    marginTop: 4,
  },
  cardDesc: {
    fontSize: 12,
    marginTop: 4,
    lineHeight: 17,
  },
  progressStatsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 14,
    gap: 12,
  },
  statItem: {
    alignItems: 'flex-start',
  },
  statNum: {
    fontSize: 16,
    fontWeight: '800',
  },
  statLabel: {
    fontSize: 10,
    fontWeight: '600',
    textTransform: 'uppercase',
  },
  statDivider: {
    width: 1,
    height: 18,
    backgroundColor: 'rgba(255,255,255,0.1)',
  },
  quickActionsGrid: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 22,
  },
  actionTile: {
    flex: 1,
    paddingVertical: 12,
    alignItems: 'center',
    borderWidth: 1,
  },
  actionIconCircle: {
    width: 36,
    height: 36,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 6,
  },
  actionLabel: {
    fontSize: 11,
    fontWeight: '700',
  },
  sectionWrap: {
    marginBottom: 22,
  },
  sectionHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  sectionTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  fireIcon: {
    fontSize: 16,
  },
  sectionHeading: {
    fontSize: 16,
    fontWeight: '800',
    letterSpacing: -0.2,
  },
  sectionCount: {
    fontSize: 12,
    fontWeight: '800',
  },
  viewAllText: {
    fontSize: 12,
    fontWeight: '700',
  },
  emptyStateCard: {
    padding: 24,
    alignItems: 'center',
    borderWidth: 1,
    borderStyle: 'dashed',
  },
  emptyIcon: {
    fontSize: 26,
    color: '#6366F1',
    marginBottom: 8,
  },
  emptyTitle: {
    fontSize: 16,
    fontWeight: '700',
  },
  emptySubtitle: {
    fontSize: 12,
    textAlign: 'center',
    marginTop: 4,
    lineHeight: 18,
    maxWidth: 240,
  },
  emptyActionBtn: {
    marginTop: 14,
    paddingHorizontal: 16,
    paddingVertical: 9,
  },
  emptyActionBtnText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '800',
  },
});
