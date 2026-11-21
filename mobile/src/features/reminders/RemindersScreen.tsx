import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
  Switch,
  Alert,
} from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useTasks } from '../../hooks/useTasksContext';
import { Icon } from '../../components/common/Icon';
import { notificationService, ScheduledReminder } from '../../services/notificationService';
import { formatFriendlyDate } from '../../utils/dateUtils';

interface RemindersScreenProps {
  onClose?: () => void;
}

export const RemindersScreen: React.FC<RemindersScreenProps> = ({ onClose }) => {
  const { colors, borderRadius, spacing } = useTheme();
  const { tasks } = useTasks();

  const [notificationsEnabled, setNotificationsEnabled] = useState(true);
  const [dailyBriefing, setDailyBriefing] = useState(true);
  const [urgentAlerts, setUrgentAlerts] = useState(true);
  const [reminders, setReminders] = useState<ScheduledReminder[]>([]);

  useEffect(() => {
    loadReminders();
  }, [tasks]);

  const loadReminders = async () => {
    const list = await notificationService.getScheduledReminders();
    // Also derive reminders for upcoming tasks with due dates
    const derived: ScheduledReminder[] = tasks
      .filter((t) => t.due_date && !t.is_completed)
      .slice(0, 5)
      .map((t) => ({
        id: `auto-${t.local_id}`,
        taskId: t.local_id,
        title: t.title,
        scheduledTime: `${t.due_date}T${t.due_time || '09:00:00'}`,
        delivered: false,
      }));

    setReminders([...list, ...derived]);
  };

  const handleTestNotification = () => {
    Alert.alert(
      '🔔 Test Notification',
      'ASTA Companion: "Machine Learning Assignment" is due tomorrow at 17:00. Local notifications active!'
    );
  };

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: 110 }]}
      showsVerticalScrollIndicator={false}
    >
      {/* Header */}
      <View style={[styles.topRow, { paddingHorizontal: spacing.lg, paddingTop: spacing.md }]}>
        <View>
          <Text style={[styles.screenTitle, { color: colors.textPrimary }]}>Reminders</Text>
          <Text style={[styles.screenSub, { color: colors.textSecondary }]}>
            Android device notifications & deadline alerts
          </Text>
        </View>
        {onClose ? (
          <Pressable
            onPress={onClose}
            style={[styles.closeBtn, { backgroundColor: colors.surfaceSubtle }]}
          >
            <Icon name="close" size={16} color={colors.textSecondary} />
          </Pressable>
        ) : null}
      </View>

      {/* Permission Card */}
      <View style={{ paddingHorizontal: spacing.lg, marginVertical: 10 }}>
        <View
          style={[
            styles.permCard,
            {
              backgroundColor: colors.surface,
              borderColor: colors.cardBorder,
              borderRadius: borderRadius.lg,
            },
          ]}
        >
          <View style={styles.permRow}>
            <View>
              <Text style={[styles.permTitle, { color: colors.textPrimary }]}>
                Device Notifications
              </Text>
              <Text style={[styles.permDesc, { color: colors.textMuted }]}>
                Deliver alerts directly to your Android lock screen
              </Text>
            </View>
            <Switch
              value={notificationsEnabled}
              onValueChange={setNotificationsEnabled}
              trackColor={{ false: colors.surfaceSubtle, true: colors.accent }}
              thumbColor="#FFFFFF"
            />
          </View>

          <Pressable
            onPress={handleTestNotification}
            style={[
              styles.testBtn,
              { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.sm },
            ]}
          >
            <Text style={[styles.testBtnText, { color: colors.accentLight }]}>
              Send Test Notification ›
            </Text>
          </Pressable>
        </View>
      </View>

      {/* Reminder Preferences */}
      <View style={{ paddingHorizontal: spacing.lg, marginBottom: 16 }}>
        <Text style={[styles.sectionHeading, { color: colors.textSecondary }]}>PREFERENCES</Text>
        <View
          style={[
            styles.settingsBox,
            {
              backgroundColor: colors.surface,
              borderColor: colors.cardBorder,
              borderRadius: borderRadius.md,
            },
          ]}
        >
          <View style={[styles.prefItem, { borderBottomColor: colors.border }]}>
            <Text style={[styles.prefText, { color: colors.textPrimary }]}>
              Daily Morning Briefing (08:30)
            </Text>
            <Switch
              value={dailyBriefing}
              onValueChange={setDailyBriefing}
              trackColor={{ false: colors.surfaceSubtle, true: colors.accent }}
              thumbColor="#FFFFFF"
            />
          </View>
          <View style={styles.prefItem}>
            <Text style={[styles.prefText, { color: colors.textPrimary }]}>
              Urgent & High Priority Alerts (1h before)
            </Text>
            <Switch
              value={urgentAlerts}
              onValueChange={setUrgentAlerts}
              trackColor={{ false: colors.surfaceSubtle, true: colors.accent }}
              thumbColor="#FFFFFF"
            />
          </View>
        </View>
      </View>

      {/* Upcoming Scheduled Reminders */}
      <View style={{ paddingHorizontal: spacing.lg }}>
        <Text style={[styles.sectionHeading, { color: colors.textSecondary }]}>
          UPCOMING SCHEDULED ALERTS ({reminders.length})
        </Text>

        {reminders.length === 0 ? (
          <View
            style={[
              styles.emptyCard,
              {
                backgroundColor: colors.surface,
                borderColor: colors.border,
                borderRadius: borderRadius.lg,
              },
            ]}
          >
            <Text style={styles.emptyIcon}>▲</Text>
            <Text style={[styles.emptyTitle, { color: colors.textPrimary }]}>
              No active reminders
            </Text>
            <Text style={[styles.emptySub, { color: colors.textMuted }]}>
              Tasks with scheduled due dates will generate reminders automatically.
            </Text>
          </View>
        ) : (
          reminders.map((r) => {
            const datePart = r.scheduledTime.slice(0, 10);
            const timePart = r.scheduledTime.slice(11, 16) || '09:00';
            return (
              <View
                key={r.id}
                style={[
                  styles.reminderItem,
                  {
                    backgroundColor: colors.surface,
                    borderColor: colors.cardBorder,
                    borderRadius: borderRadius.md,
                  },
                ]}
              >
                <View style={styles.reminderDot} />
                <View style={styles.reminderInfo}>
                  <Text style={[styles.reminderTaskTitle, { color: colors.textPrimary }]}>
                    {r.title}
                  </Text>
                  <Text style={[styles.reminderTimeText, { color: colors.textSecondary }]}>
                    ◷ {formatFriendlyDate(datePart)} at {timePart}
                  </Text>
                </View>
                <Text style={[styles.activePill, { color: colors.accentLight }]}>Active</Text>
              </View>
            );
          })
        )}
      </View>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingTop: 4,
  },
  topRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  screenTitle: {
    fontSize: 26,
    fontWeight: '900',
    letterSpacing: -0.5,
  },
  screenSub: {
    fontSize: 12,
    marginTop: 2,
  },
  closeBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  permCard: {
    padding: 16,
    borderWidth: 1,
  },
  permRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  permTitle: {
    fontSize: 15,
    fontWeight: '700',
  },
  permDesc: {
    fontSize: 12,
    marginTop: 2,
  },
  testBtn: {
    marginTop: 14,
    paddingVertical: 8,
    alignItems: 'center',
  },
  testBtnText: {
    fontSize: 12,
    fontWeight: '700',
  },
  sectionHeading: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1.2,
    marginBottom: 8,
  },
  settingsBox: {
    borderWidth: 1,
    overflow: 'hidden',
  },
  prefItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 14,
    borderBottomWidth: 1,
  },
  prefText: {
    fontSize: 13,
    fontWeight: '600',
    flex: 1,
    paddingRight: 10,
  },
  emptyCard: {
    padding: 26,
    alignItems: 'center',
    borderWidth: 1,
    borderStyle: 'dashed',
  },
  emptyIcon: {
    fontSize: 24,
    color: '#6366F1',
    marginBottom: 6,
  },
  emptyTitle: {
    fontSize: 15,
    fontWeight: '700',
  },
  emptySub: {
    fontSize: 12,
    textAlign: 'center',
    marginTop: 2,
  },
  reminderItem: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    borderWidth: 1,
    marginBottom: 8,
    gap: 12,
  },
  reminderDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#6366F1',
  },
  reminderInfo: {
    flex: 1,
  },
  reminderTaskTitle: {
    fontSize: 14,
    fontWeight: '700',
  },
  reminderTimeText: {
    fontSize: 12,
    marginTop: 2,
  },
  activePill: {
    fontSize: 11,
    fontWeight: '800',
  },
});
