import React from 'react';
import { View, Text, StyleSheet, ViewStyle } from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { TaskCategory, TaskPriority, SyncState } from '../../types/task';

interface BadgeProps {
  label?: string;
  type?: 'priority' | 'category' | 'sync' | 'custom';
  priority?: TaskPriority;
  category?: TaskCategory;
  syncState?: SyncState;
  color?: string;
  backgroundColor?: string;
  style?: ViewStyle;
}

export const Badge: React.FC<BadgeProps> = ({
  label,
  type = 'custom',
  priority,
  category,
  syncState,
  color,
  backgroundColor,
  style,
}) => {
  const { colors, borderRadius } = useTheme();

  let text = label || '';
  let badgeColor = color || colors.textSecondary;
  let badgeBg = backgroundColor || colors.badgeBg;

  if (type === 'priority' && priority) {
    text = priority.toUpperCase();
    switch (priority) {
      case 'urgent':
        badgeColor = colors.priorityUrgent;
        badgeBg = colors.priorityUrgentBg;
        break;
      case 'high':
        badgeColor = colors.priorityHigh;
        badgeBg = colors.priorityHighBg;
        break;
      case 'medium':
        badgeColor = colors.priorityMedium;
        badgeBg = colors.priorityMediumBg;
        break;
      case 'low':
        badgeColor = colors.priorityLow;
        badgeBg = colors.priorityLowBg;
        break;
    }
  } else if (type === 'category' && category) {
    text = category.charAt(0).toUpperCase() + category.slice(1);
    switch (category) {
      case 'college':
        badgeColor = colors.categoryCollege;
        badgeBg = colors.isDark ? 'rgba(56, 189, 248, 0.15)' : 'rgba(2, 132, 199, 0.1)';
        break;
      case 'assignments':
        badgeColor = colors.categoryAssignments;
        badgeBg = colors.isDark ? 'rgba(129, 140, 248, 0.15)' : 'rgba(79, 70, 229, 0.1)';
        break;
      case 'exams':
        badgeColor = colors.categoryExams;
        badgeBg = colors.isDark ? 'rgba(244, 63, 94, 0.15)' : 'rgba(225, 29, 72, 0.1)';
        break;
      case 'projects':
        badgeColor = colors.categoryProjects;
        badgeBg = colors.isDark ? 'rgba(168, 85, 247, 0.15)' : 'rgba(147, 51, 234, 0.1)';
        break;
      case 'personal':
        badgeColor = colors.categoryPersonal;
        badgeBg = colors.isDark ? 'rgba(52, 211, 153, 0.15)' : 'rgba(5, 150, 105, 0.1)';
        break;
      case 'meetings':
        badgeColor = colors.categoryMeetings;
        badgeBg = colors.isDark ? 'rgba(251, 191, 36, 0.15)' : 'rgba(217, 119, 6, 0.1)';
        break;
      case 'learning':
        badgeColor = colors.categoryLearning;
        badgeBg = colors.isDark ? 'rgba(45, 212, 191, 0.15)' : 'rgba(13, 148, 136, 0.1)';
        break;
      default:
        badgeColor = colors.categoryOther;
        badgeBg = colors.badgeBg;
        break;
    }
  } else if (type === 'sync' && syncState) {
    switch (syncState) {
      case 'synced':
        text = 'SYNCED';
        badgeColor = colors.syncSynced;
        badgeBg = colors.isDark ? 'rgba(16, 185, 129, 0.15)' : 'rgba(5, 150, 105, 0.1)';
        break;
      case 'pending_sync':
        text = 'PENDING SYNC';
        badgeColor = colors.syncPending;
        badgeBg = colors.isDark ? 'rgba(245, 158, 11, 0.18)' : 'rgba(217, 119, 6, 0.12)';
        break;
      case 'sync_failed':
        text = 'SYNC FAILED';
        badgeColor = colors.syncFailed;
        badgeBg = colors.isDark ? 'rgba(239, 68, 68, 0.2)' : 'rgba(220, 38, 38, 0.12)';
        break;
      case 'local_only':
      default:
        text = 'LOCAL ONLY';
        badgeColor = colors.textMuted;
        badgeBg = colors.badgeBg;
        break;
    }
  }

  return (
    <View style={[styles.badge, { backgroundColor: badgeBg, borderRadius: borderRadius.xs }, style]}>
      <Text style={[styles.text, { color: badgeColor }]}>{text}</Text>
    </View>
  );
};

const styles = StyleSheet.create({
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    alignSelf: 'flex-start',
    justifyContent: 'center',
    alignItems: 'center',
  },
  text: {
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
});
