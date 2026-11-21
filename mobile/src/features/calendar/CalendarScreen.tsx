import React, { useState, useMemo } from 'react';
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
import { TaskCard } from '../../components/tasks/TaskCard';
import { getMonthDays, getWeekDays, formatFriendlyDate } from '../../utils/dateUtils';
import { Icon } from '../../components/common/Icon';

type CalendarViewMode = 'month' | 'week';

export const CalendarScreen: React.FC = () => {
  const { colors, borderRadius, spacing } = useTheme();
  const { tasks, toggleTask } = useTasks();
  const { openModal } = useNavigation();

  const today = new Date();
  const todayStr = today.toISOString().slice(0, 10);

  const [currentYear, setCurrentYear] = useState(today.getFullYear());
  const [currentMonthIndex, setCurrentMonthIndex] = useState(today.getMonth()); // 0-11
  const [selectedDate, setSelectedDate] = useState(todayStr);
  const [viewMode, setViewMode] = useState<CalendarViewMode>('month');

  // Month navigation
  const prevMonth = () => {
    if (currentMonthIndex === 0) {
      setCurrentMonthIndex(11);
      setCurrentYear((y) => y - 1);
    } else {
      setCurrentMonthIndex((m) => m - 1);
    }
  };

  const nextMonth = () => {
    if (currentMonthIndex === 11) {
      setCurrentMonthIndex(0);
      setCurrentYear((y) => y + 1);
    } else {
      setCurrentMonthIndex((m) => m + 1);
    }
  };

  const monthName = new Date(currentYear, currentMonthIndex, 1).toLocaleDateString('en-US', {
    month: 'long',
    year: 'numeric',
  });

  // Calculate task indicators: map date -> count
  const taskCountByDate = useMemo(() => {
    const map = new Map<string, number>();
    for (const t of tasks) {
      if (t.due_date) {
        map.set(t.due_date, (map.get(t.due_date) || 0) + 1);
      }
    }
    return map;
  }, [tasks]);

  const monthDays = useMemo(
    () => getMonthDays(currentYear, currentMonthIndex),
    [currentYear, currentMonthIndex]
  );
  const weekDays = useMemo(() => getWeekDays(new Date(selectedDate)), [selectedDate]);

  // Tasks for the selected date
  const selectedDayTasks = useMemo(() => {
    return tasks.filter((t) => t.due_date === selectedDate);
  }, [tasks, selectedDate]);

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: 120 }]}
      showsVerticalScrollIndicator={false}
    >
      {/* Header and View Mode Toggle */}
      <View style={[styles.headerRow, { paddingHorizontal: spacing.lg, paddingTop: spacing.md }]}>
        <View>
          <Text style={[styles.screenTitle, { color: colors.textPrimary }]}>Calendar</Text>
          <Text style={[styles.monthLabel, { color: colors.textSecondary }]}>{monthName}</Text>
        </View>

        <View style={styles.rightControls}>
          {/* Mode Switcher */}
          <View style={[styles.toggleWrap, { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.sm }]}>
            <Pressable
              onPress={() => setViewMode('month')}
              style={[
                styles.toggleBtn,
                { backgroundColor: viewMode === 'month' ? colors.accent : 'transparent', borderRadius: borderRadius.xs },
              ]}
            >
              <Text style={[styles.toggleText, { color: viewMode === 'month' ? '#FFFFFF' : colors.textSecondary }]}>
                Month
              </Text>
            </Pressable>
            <Pressable
              onPress={() => setViewMode('week')}
              style={[
                styles.toggleBtn,
                { backgroundColor: viewMode === 'week' ? colors.accent : 'transparent', borderRadius: borderRadius.xs },
              ]}
            >
              <Text style={[styles.toggleText, { color: viewMode === 'week' ? '#FFFFFF' : colors.textSecondary }]}>
                Week
              </Text>
            </Pressable>
          </View>

          {/* Month Steppers */}
          <View style={styles.navArrows}>
            <Pressable
              onPress={prevMonth}
              style={[styles.arrowBtn, { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.xs }]}
            >
              <Text style={[styles.arrowText, { color: colors.textPrimary }]}>‹</Text>
            </Pressable>
            <Pressable
              onPress={nextMonth}
              style={[styles.arrowBtn, { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.xs }]}
            >
              <Text style={[styles.arrowText, { color: colors.textPrimary }]}>›</Text>
            </Pressable>
          </View>
        </View>
      </View>

      {/* Calendar Grid / Strip */}
      <View
        style={[
          styles.calendarCard,
          {
            backgroundColor: colors.surface,
            borderColor: colors.cardBorder,
            borderRadius: borderRadius.lg,
            marginHorizontal: spacing.lg,
          },
        ]}
      >
        {/* Days of week header */}
        <View style={styles.weekHeader}>
          {['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'].map((d) => (
            <Text key={d} style={[styles.weekDayHeader, { color: colors.textMuted }]}>
              {d}
            </Text>
          ))}
        </View>

        {viewMode === 'month' ? (
          <View style={styles.monthGrid}>
            {monthDays.map((item, idx) => {
              const isSelected = item.dateStr === selectedDate;
              const isToday = item.dateStr === todayStr;
              const taskCount = taskCountByDate.get(item.dateStr) || 0;

              return (
                <Pressable
                  key={`${item.dateStr}-${idx}`}
                  onPress={() => setSelectedDate(item.dateStr)}
                  style={[
                    styles.dayCell,
                    {
                      backgroundColor: isSelected
                        ? colors.accent
                        : isToday
                        ? colors.surfaceSubtle
                        : 'transparent',
                      borderRadius: borderRadius.sm,
                    },
                  ]}
                >
                  <Text
                    style={[
                      styles.dayNumber,
                      {
                        color: isSelected
                          ? '#FFFFFF'
                          : item.isCurrentMonth
                          ? colors.textPrimary
                          : colors.textMuted,
                        fontWeight: isToday || isSelected ? '800' : '500',
                      },
                    ]}
                  >
                    {item.day}
                  </Text>

                  {/* Task indicator dot */}
                  {taskCount > 0 ? (
                    <View
                      style={[
                        styles.taskDot,
                        {
                          backgroundColor: isSelected ? '#FFFFFF' : colors.accentLight,
                        },
                      ]}
                    />
                  ) : (
                    <View style={styles.emptyDot} />
                  )}
                </Pressable>
              );
            })}
          </View>
        ) : (
          /* Week Strip View */
          <View style={styles.weekStrip}>
            {weekDays.map((item) => {
              const isSelected = item.dateStr === selectedDate;
              const taskCount = taskCountByDate.get(item.dateStr) || 0;

              return (
                <Pressable
                  key={item.dateStr}
                  onPress={() => setSelectedDate(item.dateStr)}
                  style={[
                    styles.weekCell,
                    {
                      backgroundColor: isSelected ? colors.accent : colors.surfaceSubtle,
                      borderRadius: borderRadius.md,
                    },
                  ]}
                >
                  <Text
                    style={[
                      styles.weekCellDayName,
                      { color: isSelected ? '#FFFFFF' : colors.textMuted },
                    ]}
                  >
                    {item.dayName}
                  </Text>
                  <Text
                    style={[
                      styles.weekCellDayNum,
                      { color: isSelected ? '#FFFFFF' : colors.textPrimary },
                    ]}
                  >
                    {item.dayNumber}
                  </Text>
                  {taskCount > 0 ? (
                    <View
                      style={[
                        styles.weekTaskDot,
                        { backgroundColor: isSelected ? '#FFFFFF' : colors.accentLight },
                      ]}
                    />
                  ) : (
                    <View style={styles.emptyDot} />
                  )}
                </Pressable>
              );
            })}
          </View>
        )}
      </View>

      {/* Selected Day Header & Add Task CTA */}
      <View style={[styles.agendaHeaderRow, { paddingHorizontal: spacing.lg }]}>
        <View>
          <Text style={[styles.agendaTitle, { color: colors.textPrimary }]}>
            Agenda · {formatFriendlyDate(selectedDate)}
          </Text>
          <Text style={[styles.agendaSubtitle, { color: colors.textSecondary }]}>
            {selectedDayTasks.length} {selectedDayTasks.length === 1 ? 'deadline' : 'deadlines'} scheduled
          </Text>
        </View>

        <Pressable
          onPress={() => openModal({ type: 'create_task', initialDate: selectedDate })}
          style={[
            styles.addForDateBtn,
            { backgroundColor: colors.accent, borderRadius: borderRadius.sm },
          ]}
        >
          <Text style={styles.addForDateText}>＋ Add for this day</Text>
        </Pressable>
      </View>

      {/* Selected Day Task List */}
      <View style={{ paddingHorizontal: spacing.lg }}>
        {selectedDayTasks.length === 0 ? (
          <View
            style={[
              styles.emptyDayCard,
              {
                backgroundColor: colors.surface,
                borderColor: colors.border,
                borderRadius: borderRadius.lg,
              },
            ]}
          >
            <Text style={styles.emptyDayIcon}>◷</Text>
            <Text style={[styles.emptyDayTitle, { color: colors.textPrimary }]}>Nothing scheduled</Text>
            <Text style={[styles.emptyDaySub, { color: colors.textMuted }]}>
              You have no deadlines due on {selectedDate}.
            </Text>
          </View>
        ) : (
          selectedDayTasks.map((task) => (
            <TaskCard key={task.local_id} task={task} onToggle={toggleTask} />
          ))
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
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  screenTitle: {
    fontSize: 26,
    fontWeight: '900',
    letterSpacing: -0.5,
  },
  monthLabel: {
    fontSize: 13,
    fontWeight: '600',
    marginTop: 2,
  },
  rightControls: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  toggleWrap: {
    flexDirection: 'row',
    padding: 3,
  },
  toggleBtn: {
    paddingHorizontal: 8,
    paddingVertical: 5,
  },
  toggleText: {
    fontSize: 11,
    fontWeight: '700',
  },
  navArrows: {
    flexDirection: 'row',
    gap: 4,
  },
  arrowBtn: {
    width: 28,
    height: 28,
    alignItems: 'center',
    justifyContent: 'center',
  },
  arrowText: {
    fontSize: 16,
    fontWeight: '700',
    lineHeight: 18,
  },
  calendarCard: {
    borderWidth: 1,
    padding: 14,
    marginBottom: 20,
  },
  weekHeader: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginBottom: 8,
  },
  weekDayHeader: {
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 0.8,
    width: 38,
    textAlign: 'center',
  },
  monthGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-around',
  },
  dayCell: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    marginVertical: 2,
  },
  dayNumber: {
    fontSize: 13,
  },
  taskDot: {
    width: 4,
    height: 4,
    borderRadius: 2,
    marginTop: 2,
  },
  emptyDot: {
    width: 4,
    height: 4,
    marginTop: 2,
  },
  weekStrip: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 4,
  },
  weekCell: {
    width: 42,
    paddingVertical: 8,
    alignItems: 'center',
  },
  weekCellDayName: {
    fontSize: 10,
    fontWeight: '700',
    marginBottom: 2,
  },
  weekCellDayNum: {
    fontSize: 14,
    fontWeight: '800',
  },
  weekTaskDot: {
    width: 4,
    height: 4,
    borderRadius: 2,
    marginTop: 4,
  },
  agendaHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  agendaTitle: {
    fontSize: 17,
    fontWeight: '800',
    letterSpacing: -0.3,
  },
  agendaSubtitle: {
    fontSize: 12,
    marginTop: 2,
  },
  addForDateBtn: {
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  addForDateText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '800',
  },
  emptyDayCard: {
    padding: 24,
    alignItems: 'center',
    borderWidth: 1,
    borderStyle: 'dashed',
  },
  emptyDayIcon: {
    fontSize: 24,
    color: '#6366F1',
    marginBottom: 6,
  },
  emptyDayTitle: {
    fontSize: 15,
    fontWeight: '700',
  },
  emptyDaySub: {
    fontSize: 12,
    marginTop: 3,
  },
});
