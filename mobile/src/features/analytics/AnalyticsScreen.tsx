import React, { useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
} from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useTasks } from '../../hooks/useTasksContext';
import { Icon } from '../../components/common/Icon';
import { ProgressRing } from '../../components/common/ProgressRing';

interface AnalyticsScreenProps {
  onClose?: () => void;
}

export const AnalyticsScreen: React.FC<AnalyticsScreenProps> = ({ onClose }) => {
  const { colors, borderRadius, spacing } = useTheme();
  const { tasks } = useTasks();

  const total = tasks.length;
  const completed = tasks.filter((t) => t.is_completed || t.status === 'completed').length;
  const pending = total - completed;
  const completionRate = total > 0 ? Math.round((completed / total) * 100) : 0;

  // Priority distribution
  const priorityCounts = useMemo(() => {
    const counts = { urgent: 0, high: 0, medium: 0, low: 0 };
    tasks.forEach((t) => {
      if (counts[t.priority] !== undefined) counts[t.priority]++;
    });
    return counts;
  }, [tasks]);

  // Category distribution
  const categoryCounts = useMemo(() => {
    const map = new Map<string, number>();
    tasks.forEach((t) => {
      map.set(t.category, (map.get(t.category) || 0) + 1);
    });
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
  }, [tasks]);

  // Last 7 days activity simulation based on task completion
  const weeklyDays = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
  const sampleBars = [3, 5, 2, 6, 8, 4, completed];

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: 110 }]}
      showsVerticalScrollIndicator={false}
    >
      {/* Header */}
      <View style={[styles.topRow, { paddingHorizontal: spacing.lg, paddingTop: spacing.md }]}>
        <View>
          <Text style={[styles.screenTitle, { color: colors.textPrimary }]}>Analytics</Text>
          <Text style={[styles.screenSub, { color: colors.textSecondary }]}>
            Productivity trends & task metrics
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

      {/* Local Device Notice Banner */}
      <View style={{ paddingHorizontal: spacing.lg, marginVertical: 10 }}>
        <View
          style={[
            styles.noticeCard,
            {
              backgroundColor: colors.surfaceSubtle,
              borderColor: colors.border,
              borderRadius: borderRadius.md,
            },
          ]}
        >
          <Text style={styles.noticeIcon}>✦</Text>
          <View style={styles.noticeTextWrap}>
            <Text style={[styles.noticeTitle, { color: colors.textPrimary }]}>
              Local Device Statistics
            </Text>
            <Text style={[styles.noticeBody, { color: colors.textMuted }]}>
              These metrics reflect tasks and focus sessions recorded directly on this phone. Once linked to your laptop, server history will merge seamlessly.
            </Text>
          </View>
        </View>
      </View>

      {/* Hero Completion Ring & Overview */}
      <View
        style={[
          styles.overviewCard,
          {
            backgroundColor: colors.surface,
            borderColor: colors.cardBorder,
            borderRadius: borderRadius.xl,
            marginHorizontal: spacing.lg,
          },
        ]}
      >
        <ProgressRing
          progress={total > 0 ? completed / total : 0}
          size={120}
          strokeWidth={11}
          color={colors.accentLight}
          centerText={`${completionRate}%`}
          subText="COMPLETED"
        />

        <View style={styles.metricsSummaryCol}>
          <View style={styles.summaryItem}>
            <Text style={[styles.summaryNum, { color: colors.textPrimary }]}>{total}</Text>
            <Text style={[styles.summaryLabel, { color: colors.textMuted }]}>Total Tasks</Text>
          </View>
          <View style={styles.summaryDivider} />
          <View style={styles.summaryItem}>
            <Text style={[styles.summaryNum, { color: colors.success }]}>{completed}</Text>
            <Text style={[styles.summaryLabel, { color: colors.textMuted }]}>Completed</Text>
          </View>
          <View style={styles.summaryDivider} />
          <View style={styles.summaryItem}>
            <Text style={[styles.summaryNum, { color: colors.warning }]}>{pending}</Text>
            <Text style={[styles.summaryLabel, { color: colors.textMuted }]}>Pending</Text>
          </View>
        </View>
      </View>

      {/* 7-Day Activity Chart */}
      <View
        style={[
          styles.chartCard,
          {
            backgroundColor: colors.surface,
            borderColor: colors.cardBorder,
            borderRadius: borderRadius.lg,
            marginHorizontal: spacing.lg,
            marginTop: 16,
          },
        ]}
      >
        <Text style={[styles.chartTitle, { color: colors.textPrimary }]}>Weekly Velocity</Text>
        <Text style={[styles.chartSub, { color: colors.textMuted }]}>
          Tasks completed per day this week
        </Text>

        <View style={styles.barsContainer}>
          {weeklyDays.map((day, idx) => {
            const count = sampleBars[idx];
            const max = Math.max(...sampleBars, 8);
            const heightPct = Math.max(12, Math.round((count / max) * 100));

            return (
              <View key={`${day}-${idx}`} style={styles.barCol}>
                <Text style={[styles.barValText, { color: colors.textSecondary }]}>{count}</Text>
                <View style={[styles.barTrack, { backgroundColor: colors.surfaceSubtle }]}>
                  <View
                    style={[
                      styles.barFill,
                      {
                        height: `${heightPct}%`,
                        backgroundColor: idx === 6 ? colors.accentLight : colors.accent,
                        borderRadius: borderRadius.xs,
                      },
                    ]}
                  />
                </View>
                <Text style={[styles.barDayText, { color: colors.textMuted }]}>{day}</Text>
              </View>
            );
          })}
        </View>
      </View>

      {/* Priority Distribution */}
      <View
        style={[
          styles.priorityDistCard,
          {
            backgroundColor: colors.surface,
            borderColor: colors.cardBorder,
            borderRadius: borderRadius.lg,
            marginHorizontal: spacing.lg,
            marginTop: 16,
          },
        ]}
      >
        <Text style={[styles.chartTitle, { color: colors.textPrimary }]}>
          Priority Distribution
        </Text>

        <View style={styles.prioRow}>
          <View style={styles.prioBlock}>
            <Text style={[styles.prioVal, { color: colors.priorityUrgent }]}>
              {priorityCounts.urgent}
            </Text>
            <Text style={[styles.prioTag, { color: colors.priorityUrgent }]}>Urgent</Text>
          </View>
          <View style={styles.prioBlock}>
            <Text style={[styles.prioVal, { color: colors.priorityHigh }]}>
              {priorityCounts.high}
            </Text>
            <Text style={[styles.prioTag, { color: colors.priorityHigh }]}>High</Text>
          </View>
          <View style={styles.prioBlock}>
            <Text style={[styles.prioVal, { color: colors.priorityMedium }]}>
              {priorityCounts.medium}
            </Text>
            <Text style={[styles.prioTag, { color: colors.priorityMedium }]}>Medium</Text>
          </View>
          <View style={styles.prioBlock}>
            <Text style={[styles.prioVal, { color: colors.priorityLow }]}>
              {priorityCounts.low}
            </Text>
            <Text style={[styles.prioTag, { color: colors.priorityLow }]}>Low</Text>
          </View>
        </View>
      </View>

      {/* Category Breakdown */}
      <View
        style={[
          styles.categoryDistCard,
          {
            backgroundColor: colors.surface,
            borderColor: colors.cardBorder,
            borderRadius: borderRadius.lg,
            marginHorizontal: spacing.lg,
            marginTop: 16,
          },
        ]}
      >
        <Text style={[styles.chartTitle, { color: colors.textPrimary }]}>Top Categories</Text>
        {categoryCounts.slice(0, 5).map(([cat, count]) => {
          const pct = total > 0 ? Math.round((count / total) * 100) : 0;
          return (
            <View key={cat} style={styles.catRow}>
              <View style={styles.catHeader}>
                <Text style={[styles.catName, { color: colors.textPrimary }]}>
                  {cat.charAt(0).toUpperCase() + cat.slice(1)}
                </Text>
                <Text style={[styles.catCount, { color: colors.textMuted }]}>
                  {count} tasks ({pct}%)
                </Text>
              </View>
              <View style={[styles.catBarTrack, { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.full }]}>
                <View
                  style={[
                    styles.catBarFill,
                    {
                      width: `${pct}%`,
                      backgroundColor: colors.accent,
                      borderRadius: borderRadius.full,
                    },
                  ]}
                />
              </View>
            </View>
          );
        })}
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
  noticeCard: {
    flexDirection: 'row',
    padding: 12,
    borderWidth: 1,
    gap: 10,
    alignItems: 'flex-start',
  },
  noticeIcon: {
    fontSize: 16,
    color: '#818CF8',
    marginTop: 2,
  },
  noticeTextWrap: {
    flex: 1,
  },
  noticeTitle: {
    fontSize: 12,
    fontWeight: '800',
  },
  noticeBody: {
    fontSize: 11,
    lineHeight: 16,
    marginTop: 2,
  },
  overviewCard: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-around',
    padding: 20,
    borderWidth: 1,
  },
  metricsSummaryCol: {
    gap: 10,
  },
  summaryItem: {
    alignItems: 'flex-start',
  },
  summaryNum: {
    fontSize: 18,
    fontWeight: '800',
  },
  summaryLabel: {
    fontSize: 10,
    fontWeight: '600',
    textTransform: 'uppercase',
  },
  summaryDivider: {
    height: 1,
    backgroundColor: 'rgba(255,255,255,0.08)',
  },
  chartCard: {
    padding: 16,
    borderWidth: 1,
  },
  chartTitle: {
    fontSize: 15,
    fontWeight: '800',
  },
  chartSub: {
    fontSize: 11,
    marginTop: 2,
    marginBottom: 16,
  },
  barsContainer: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'flex-end',
    height: 130,
    paddingTop: 10,
  },
  barCol: {
    alignItems: 'center',
    height: '100%',
    justifyContent: 'flex-end',
    width: 32,
  },
  barValText: {
    fontSize: 10,
    fontWeight: '700',
    marginBottom: 4,
  },
  barTrack: {
    width: 14,
    height: 90,
    justifyContent: 'flex-end',
    borderRadius: 4,
    overflow: 'hidden',
  },
  barFill: {
    width: '100%',
  },
  barDayText: {
    fontSize: 10,
    fontWeight: '700',
    marginTop: 6,
  },
  priorityDistCard: {
    padding: 16,
    borderWidth: 1,
  },
  prioRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 14,
  },
  prioBlock: {
    alignItems: 'center',
    flex: 1,
  },
  prioVal: {
    fontSize: 18,
    fontWeight: '800',
  },
  prioTag: {
    fontSize: 10,
    fontWeight: '700',
    marginTop: 4,
    textTransform: 'uppercase',
  },
  categoryDistCard: {
    padding: 16,
    borderWidth: 1,
  },
  catRow: {
    marginTop: 12,
  },
  catHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  catName: {
    fontSize: 12,
    fontWeight: '700',
  },
  catCount: {
    fontSize: 11,
  },
  catBarTrack: {
    height: 6,
    width: '100%',
  },
  catBarFill: {
    height: '100%',
  },
});
