import React from 'react';
import { View, Text, StyleSheet, Pressable } from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { LocalTask } from '../../types/task';
import { Badge } from '../common/Badge';
import { formatFriendlyDate } from '../../utils/dateUtils';
import { useNavigation } from '../../navigation/NavigationContext';

interface TaskCardProps {
  task: LocalTask;
  onToggle: (localId: string) => void;
  onPress?: (task: LocalTask) => void;
}

export const TaskCard: React.FC<TaskCardProps> = ({ task, onToggle, onPress }) => {
  const { colors, borderRadius, spacing } = useTheme();
  const { openModal } = useNavigation();

  const handleCardPress = () => {
    if (onPress) onPress(task);
    else openModal({ type: 'task_details', taskId: task.local_id });
  };

  const isCompleted = task.is_completed || task.status === 'completed';
  const friendlyDueDate = formatFriendlyDate(task.due_date);
  const completedSubtasks = task.subtasks.filter((s) => s.is_completed).length;
  const totalSubtasks = task.subtasks.length;

  return (
    <Pressable
      onPress={handleCardPress}
      style={({ pressed }) => [
        styles.card,
        {
          backgroundColor: colors.card,
          borderColor: colors.cardBorder,
          borderRadius: borderRadius.lg,
          opacity: pressed ? 0.9 : 1,
        },
      ]}
    >
      {/* Priority accent strip on left */}
      <View
        style={[
          styles.priorityStrip,
          {
            backgroundColor:
              task.priority === 'urgent'
                ? colors.priorityUrgent
                : task.priority === 'high'
                ? colors.priorityHigh
                : task.priority === 'medium'
                ? colors.priorityMedium
                : colors.priorityLow,
          },
        ]}
      />

      <View style={styles.contentWrap}>
        <View style={styles.topRow}>
          {/* Checkbox */}
          <Pressable
            hitSlop={12}
            onPress={() => onToggle(task.local_id)}
            style={({ pressed }) => [
              styles.checkbox,
              {
                borderColor: isCompleted ? colors.success : colors.borderStrong,
                backgroundColor: isCompleted ? colors.success : 'transparent',
                borderRadius: borderRadius.xs,
                opacity: pressed ? 0.7 : 1,
              },
            ]}
          >
            {isCompleted ? <Text style={styles.checkIcon}>✓</Text> : null}
          </Pressable>

          {/* Title & Description */}
          <View style={styles.titleArea}>
            <Text
              style={[
                styles.titleText,
                {
                  color: isCompleted ? colors.textMuted : colors.textPrimary,
                  textDecorationLine: isCompleted ? 'line-through' : 'none',
                },
              ]}
              numberOfLines={2}
            >
              {task.title}
            </Text>

            {task.description ? (
              <Text
                style={[styles.descriptionText, { color: colors.textSecondary }]}
                numberOfLines={2}
              >
                {task.description}
              </Text>
            ) : null}
          </View>
        </View>

        {/* Badges & Meta info */}
        <View style={styles.metaRow}>
          <Badge type="category" category={task.category} />
          <Badge type="priority" priority={task.priority} />

          {task.due_date ? (
            <View style={[styles.dueBadge, { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.xs }]}>
              <Text style={[styles.dueText, { color: colors.textSecondary }]}>
                ◷ {friendlyDueDate}
                {task.due_time ? ` ${task.due_time}` : ''}
              </Text>
            </View>
          ) : null}

          {totalSubtasks > 0 ? (
            <View style={[styles.subtaskBadge, { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.xs }]}>
              <Text style={[styles.subtaskText, { color: colors.textMuted }]}>
                {completedSubtasks}/{totalSubtasks}
              </Text>
            </View>
          ) : null}

          <View style={styles.syncWrap}>
            <Badge type="sync" syncState={task.sync_state} />
          </View>
        </View>
      </View>
    </Pressable>
  );
};

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    borderWidth: 1,
    marginBottom: 10,
    overflow: 'hidden',
  },
  priorityStrip: {
    width: 4,
  },
  contentWrap: {
    flex: 1,
    padding: 14,
  },
  topRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
  },
  checkbox: {
    width: 22,
    height: 22,
    borderWidth: 1.5,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 2,
  },
  checkIcon: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '900',
  },
  titleArea: {
    flex: 1,
  },
  titleText: {
    fontSize: 15,
    fontWeight: '700',
    lineHeight: 20,
    letterSpacing: -0.2,
  },
  descriptionText: {
    fontSize: 13,
    lineHeight: 18,
    marginTop: 4,
  },
  metaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 6,
    marginTop: 10,
  },
  dueBadge: {
    paddingHorizontal: 7,
    paddingVertical: 3,
  },
  dueText: {
    fontSize: 10,
    fontWeight: '600',
  },
  subtaskBadge: {
    paddingHorizontal: 6,
    paddingVertical: 3,
  },
  subtaskText: {
    fontSize: 10,
    fontWeight: '700',
  },
  syncWrap: {
    marginLeft: 'auto',
  },
});
