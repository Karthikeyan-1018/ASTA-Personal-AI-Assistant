import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ScrollView,
  Modal,
  Alert,
} from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useTasks } from '../../hooks/useTasksContext';
import { FocusSessionType, FocusSession } from '../../types/focus';
import { recordFocusSession, getRecentFocusSessions, getFocusStats } from '../../database/focusRepository';
import { ProgressRing } from '../../components/common/ProgressRing';
import { Icon } from '../../components/common/Icon';

interface FocusScreenProps {
  initialTaskId?: string;
  onClose?: () => void;
}

export const FocusScreen: React.FC<FocusScreenProps> = ({ initialTaskId, onClose }) => {
  const { colors, borderRadius, spacing } = useTheme();
  const { tasks } = useTasks();

  const [sessionType, setSessionType] = useState<FocusSessionType>('pomodoro');
  const [totalSeconds, setTotalSeconds] = useState(25 * 60);
  const [remainingSeconds, setRemainingSeconds] = useState(25 * 60);
  const [isActive, setIsActive] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(initialTaskId || null);
  const [recentSessions, setRecentSessions] = useState<FocusSession[]>([]);
  const [stats, setStats] = useState({ totalMinutes: 0, totalSessions: 0, todayMinutes: 0 });
  const [showTaskPicker, setShowTaskPicker] = useState(false);

  const timerRef = useRef<any>(null);

  // Load history and stats
  const refreshStats = async () => {
    try {
      const s = await getFocusStats();
      const rec = await getRecentFocusSessions(5);
      setStats(s);
      setRecentSessions(rec);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    refreshStats();
  }, []);

  // Timer Tick
  useEffect(() => {
    if (isActive && remainingSeconds > 0) {
      timerRef.current = setInterval(() => {
        setRemainingSeconds((prev) => {
          if (prev <= 1) {
            clearInterval(timerRef.current!);
            handleCompleteSession();
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isActive, remainingSeconds]);

  const handleSelectType = (type: FocusSessionType, mins: number) => {
    setIsActive(false);
    setSessionType(type);
    setTotalSeconds(mins * 60);
    setRemainingSeconds(mins * 60);
  };

  const handleStartPause = () => {
    setIsActive((prev) => !prev);
  };

  const handleReset = () => {
    setIsActive(false);
    setRemainingSeconds(totalSeconds);
  };

  const handleCompleteSession = async () => {
    setIsActive(false);
    const durationMins = Math.round(totalSeconds / 60);
    const focusedTask = tasks.find((t) => t.local_id === selectedTaskId);

    try {
      await recordFocusSession(
        durationMins,
        sessionType,
        selectedTaskId,
        focusedTask?.title || null
      );
      await refreshStats();
      Alert.alert(
        '✦ Session Completed!',
        `Great job! You logged ${durationMins} minutes of dedicated focus. Keep up the momentum!`
      );
    } catch {
      // ignore
    }
  };

  const formatTime = (secs: number) => {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  const progress = totalSeconds > 0 ? (totalSeconds - remainingSeconds) / totalSeconds : 0;
  const currentTask = tasks.find((t) => t.local_id === selectedTaskId);

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: 110 }]}
      showsVerticalScrollIndicator={false}
    >
      {/* Top Bar with Close button if inside modal */}
      <View style={[styles.topBar, { paddingHorizontal: spacing.lg, paddingTop: spacing.md }]}>
        <View>
          <Text style={[styles.screenTitle, { color: colors.textPrimary }]}>Focus Mode</Text>
          <Text style={[styles.screenSub, { color: colors.textSecondary }]}>
            Deep work timer & session tracker
          </Text>
        </View>
        {onClose ? (
          <Pressable
            onPress={onClose}
            style={[styles.closeButton, { backgroundColor: colors.surfaceSubtle }]}
          >
            <Icon name="close" size={16} color={colors.textSecondary} />
          </Pressable>
        ) : null}
      </View>

      {/* Mode Presets */}
      <View style={[styles.presetsRow, { paddingHorizontal: spacing.lg }]}>
        {[
          { type: 'pomodoro' as FocusSessionType, label: '25m Pomodoro', mins: 25 },
          { type: 'deep_work' as FocusSessionType, label: '50m Deep Work', mins: 50 },
          { type: 'break' as FocusSessionType, label: '5m Break', mins: 5 },
          { type: 'break' as FocusSessionType, label: '15m Long Break', mins: 15 },
        ].map((p, idx) => {
          const isSelected = totalSeconds === p.mins * 60;
          return (
            <Pressable
              key={`${p.label}-${idx}`}
              onPress={() => handleSelectType(p.type, p.mins)}
              style={[
                styles.presetChip,
                {
                  backgroundColor: isSelected ? colors.accent : colors.surfaceSubtle,
                  borderRadius: borderRadius.sm,
                },
              ]}
            >
              <Text
                style={[
                  styles.presetChipText,
                  { color: isSelected ? '#FFFFFF' : colors.textSecondary },
                ]}
              >
                {p.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {/* Target Task Selection Card */}
      <View style={{ paddingHorizontal: spacing.lg, marginVertical: 14 }}>
        <Pressable
          onPress={() => setShowTaskPicker(true)}
          style={[
            styles.taskFocusCard,
            {
              backgroundColor: colors.surface,
              borderColor: colors.cardBorder,
              borderRadius: borderRadius.md,
            },
          ]}
        >
          <View style={styles.taskFocusLeft}>
            <Text style={[styles.focusEyebrow, { color: colors.accentLight }]}>TARGET TASK</Text>
            <Text style={[styles.focusTaskTitle, { color: colors.textPrimary }]} numberOfLines={1}>
              {currentTask ? currentTask.title : 'No specific task linked (Free Focus)'}
            </Text>
          </View>
          <Text style={[styles.changeTaskBtn, { color: colors.accentLight }]}>Change ›</Text>
        </Pressable>
      </View>

      {/* Big Circular Countdown Display */}
      <View style={styles.timerCircleArea}>
        <ProgressRing
          progress={progress}
          size={240}
          strokeWidth={14}
          color={sessionType === 'break' ? colors.success : colors.accentLight}
          centerText={formatTime(remainingSeconds)}
          subText={isActive ? (sessionType === 'break' ? 'RESTING' : 'FOCUSING') : 'PAUSED'}
        />
      </View>

      {/* Timer Controls */}
      <View style={[styles.controlsRow, { paddingHorizontal: spacing.lg }]}>
        <Pressable
          onPress={handleReset}
          style={[
            styles.secondaryControl,
            { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.full },
          ]}
        >
          <Icon name="refresh" size={18} color={colors.textSecondary} />
          <Text style={[styles.secondaryControlText, { color: colors.textSecondary }]}>Reset</Text>
        </Pressable>

        <Pressable
          onPress={handleStartPause}
          style={[
            styles.primaryControl,
            {
              backgroundColor: isActive ? colors.warning : colors.accent,
              borderRadius: borderRadius.full,
            },
          ]}
        >
          <Text style={styles.primaryControlText}>{isActive ? '❚❚ Pause' : '▶ Start Focus'}</Text>
        </Pressable>
      </View>

      {/* Focus Stats Overview */}
      <View
        style={[
          styles.statsCard,
          {
            backgroundColor: colors.surface,
            borderColor: colors.cardBorder,
            borderRadius: borderRadius.lg,
            marginHorizontal: spacing.lg,
            marginTop: 24,
          },
        ]}
      >
        <Text style={[styles.statsCardTitle, { color: colors.textPrimary }]}>
          Focus Analytics (On-Device)
        </Text>
        <View style={styles.statsRow}>
          <View style={styles.statsCol}>
            <Text style={[styles.statValue, { color: colors.accentLight }]}>
              {stats.todayMinutes}m
            </Text>
            <Text style={[styles.statSub, { color: colors.textMuted }]}>Today</Text>
          </View>
          <View style={styles.statsDivider} />
          <View style={styles.statsCol}>
            <Text style={[styles.statValue, { color: colors.textPrimary }]}>
              {stats.totalMinutes}m
            </Text>
            <Text style={[styles.statSub, { color: colors.textMuted }]}>Total Time</Text>
          </View>
          <View style={styles.statsDivider} />
          <View style={styles.statsCol}>
            <Text style={[styles.statValue, { color: colors.success }]}>{stats.totalSessions}</Text>
            <Text style={[styles.statSub, { color: colors.textMuted }]}>Sessions</Text>
          </View>
        </View>
      </View>

      {/* Task Picker Modal */}
      <Modal visible={showTaskPicker} transparent animationType="slide">
        <View style={styles.pickerOverlay}>
          <Pressable style={styles.pickerBackdrop} onPress={() => setShowTaskPicker(false)} />
          <View
            style={[
              styles.pickerContainer,
              {
                backgroundColor: colors.surface,
                borderTopLeftRadius: borderRadius.xl,
                borderTopRightRadius: borderRadius.xl,
              },
            ]}
          >
            <Text style={[styles.pickerTitle, { color: colors.textPrimary }]}>
              Select Focus Task
            </Text>
            <ScrollView style={styles.pickerList}>
              <Pressable
                onPress={() => {
                  setSelectedTaskId(null);
                  setShowTaskPicker(false);
                }}
                style={[
                  styles.pickerItem,
                  { borderBottomColor: colors.border },
                ]}
              >
                <Text style={[styles.pickerItemText, { color: colors.accentLight, fontWeight: '700' }]}>
                  ✦ Free Focus (No linked task)
                </Text>
              </Pressable>
              {tasks
                .filter((t) => !t.is_completed)
                .map((t) => (
                  <Pressable
                    key={t.local_id}
                    onPress={() => {
                      setSelectedTaskId(t.local_id);
                      setShowTaskPicker(false);
                    }}
                    style={[
                      styles.pickerItem,
                      { borderBottomColor: colors.border },
                    ]}
                  >
                    <Text style={[styles.pickerItemText, { color: colors.textPrimary }]}>
                      {t.title}
                    </Text>
                    <Text style={[styles.pickerItemCategory, { color: colors.textMuted }]}>
                      {t.category}
                    </Text>
                  </Pressable>
                ))}
            </ScrollView>
          </View>
        </View>
      </Modal>
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
  topBar: {
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
  screenSub: {
    fontSize: 12,
    marginTop: 2,
  },
  closeButton: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  presetsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  presetChip: {
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  presetChipText: {
    fontSize: 11,
    fontWeight: '700',
  },
  taskFocusCard: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 12,
    borderWidth: 1,
  },
  taskFocusLeft: {
    flex: 1,
    paddingRight: 10,
  },
  focusEyebrow: {
    fontSize: 9,
    fontWeight: '800',
    letterSpacing: 1.2,
  },
  focusTaskTitle: {
    fontSize: 13,
    fontWeight: '700',
    marginTop: 2,
  },
  changeTaskBtn: {
    fontSize: 12,
    fontWeight: '700',
  },
  timerCircleArea: {
    alignItems: 'center',
    justifyContent: 'center',
    marginVertical: 24,
  },
  controlsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 16,
  },
  secondaryControl: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 18,
    paddingVertical: 12,
    gap: 6,
  },
  secondaryControlText: {
    fontSize: 13,
    fontWeight: '700',
  },
  primaryControl: {
    paddingHorizontal: 32,
    paddingVertical: 14,
  },
  primaryControlText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '800',
  },
  statsCard: {
    padding: 16,
    borderWidth: 1,
  },
  statsCardTitle: {
    fontSize: 13,
    fontWeight: '800',
    letterSpacing: 0.5,
    marginBottom: 12,
  },
  statsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-around',
  },
  statsCol: {
    alignItems: 'center',
  },
  statValue: {
    fontSize: 20,
    fontWeight: '800',
  },
  statSub: {
    fontSize: 10,
    fontWeight: '700',
    marginTop: 2,
    textTransform: 'uppercase',
  },
  statsDivider: {
    width: 1,
    height: 24,
    backgroundColor: 'rgba(255,255,255,0.1)',
  },
  pickerOverlay: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0, 0, 0, 0.7)',
  },
  pickerBackdrop: {
    flex: 1,
  },
  pickerContainer: {
    maxHeight: '60%',
    padding: 20,
  },
  pickerTitle: {
    fontSize: 16,
    fontWeight: '800',
    marginBottom: 12,
  },
  pickerList: {
    maxHeight: 280,
  },
  pickerItem: {
    paddingVertical: 12,
    borderBottomWidth: 1,
  },
  pickerItemText: {
    fontSize: 14,
    fontWeight: '600',
  },
  pickerItemCategory: {
    fontSize: 11,
    marginTop: 2,
    textTransform: 'uppercase',
  },
});
