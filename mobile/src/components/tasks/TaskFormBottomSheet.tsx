import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  Modal,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
  Alert,
} from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useTasks } from '../../hooks/useTasksContext';
import { TaskCategory, TaskPriority } from '../../types/task';
import { Icon } from '../common/Icon';

interface TaskFormBottomSheetProps {
  visible: boolean;
  onClose: () => void;
  initialDate?: string;
  initialCategory?: string;
}

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent'];
const CATEGORIES: TaskCategory[] = [
  'college',
  'assignments',
  'exams',
  'projects',
  'personal',
  'meetings',
  'learning',
  'other',
];

export const TaskFormBottomSheet: React.FC<TaskFormBottomSheetProps> = ({
  visible,
  onClose,
  initialDate,
  initialCategory,
}) => {
  const { colors, borderRadius, spacing } = useTheme();
  const { addNewTask } = useTasks();

  const titleInputRef = useRef<any>(null);

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [priority, setPriority] = useState<TaskPriority>('medium');
  const [category, setCategory] = useState<TaskCategory>(
    (initialCategory as TaskCategory) || 'assignments'
  );
  const [dueDate, setDueDate] = useState(initialDate || new Date().toISOString().slice(0, 10));
  const [dueTime, setDueTime] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (visible) {
      if (initialDate) setDueDate(initialDate);
      if (initialCategory && CATEGORIES.includes(initialCategory as TaskCategory)) {
        setCategory(initialCategory as TaskCategory);
      }
      setTimeout(() => {
        titleInputRef.current?.focus();
      }, 150);
    } else {
      setTitle('');
      setDescription('');
      setPriority('medium');
      setDueTime('');
    }
  }, [visible, initialDate, initialCategory]);

  const handleSave = async () => {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      Alert.alert('Title required', 'Please enter a task name.');
      return;
    }

    if (dueDate && !/^\d{4}-\d{2}-\d{2}$/.test(dueDate.trim())) {
      Alert.alert('Invalid date', 'Please use YYYY-MM-DD format, e.g. 2026-11-21');
      return;
    }

    setIsSubmitting(true);
    try {
      await addNewTask({
        title: trimmedTitle,
        description: description.trim() || null,
        category,
        priority,
        due_date: dueDate.trim() || null,
        due_time: dueTime.trim() || null,
      });
      onClose();
    } catch (err: any) {
      Alert.alert('Could not save task', err?.message || 'Local storage error');
    } finally {
      setIsSubmitting(false);
    }
  };

  const setQuickDate = (daysFromToday: number) => {
    const d = new Date();
    d.setDate(d.getDate() + daysFromToday);
    setDueDate(d.toISOString().slice(0, 10));
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.overlay}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <Pressable style={styles.backdrop} onPress={onClose} />

        <View
          style={[
            styles.sheetContainer,
            {
              backgroundColor: colors.surface,
              borderColor: colors.border,
              borderTopLeftRadius: borderRadius.xxl,
              borderTopRightRadius: borderRadius.xxl,
            },
          ]}
        >
          {/* Grab handle */}
          <View style={[styles.grabHandle, { backgroundColor: colors.borderStrong }]} />

          {/* Header */}
          <View style={styles.sheetHeader}>
            <View>
              <Text style={[styles.sheetTitle, { color: colors.textPrimary }]}>Quick Create Task</Text>
              <Text style={[styles.sheetSubtitle, { color: colors.textMuted }]}>
                Saves offline to phone immediately
              </Text>
            </View>
            <Pressable
              onPress={onClose}
              style={[styles.closeButton, { backgroundColor: colors.surfaceSubtle }]}
            >
              <Icon name="close" size={16} color={colors.textSecondary} />
            </Pressable>
          </View>

          <ScrollView style={styles.formScroll} showsVerticalScrollIndicator={false}>
            {/* Title Input - Auto Focused */}
            <TextInput
              ref={titleInputRef}
              value={title}
              onChangeText={setTitle}
              placeholder="What needs to be done?"
              placeholderTextColor={colors.textMuted}
              style={[
                styles.titleInput,
                {
                  color: colors.textPrimary,
                  borderBottomColor: colors.border,
                },
              ]}
              returnKeyType="next"
            />

            {/* Description */}
            <TextInput
              value={description}
              onChangeText={setDescription}
              placeholder="Add details, notes, or instructions (optional)"
              placeholderTextColor={colors.textMuted}
              multiline
              numberOfLines={2}
              style={[
                styles.descInput,
                {
                  backgroundColor: colors.inputBackground,
                  borderColor: colors.inputBorder,
                  color: colors.textPrimary,
                  borderRadius: borderRadius.md,
                },
              ]}
            />

            {/* Quick Date Chips */}
            <Text style={[styles.fieldLabel, { color: colors.textSecondary }]}>DUE DATE</Text>
            <View style={styles.quickDateRow}>
              {[
                { label: 'Today', days: 0 },
                { label: 'Tomorrow', days: 1 },
                { label: 'Next Week', days: 7 },
              ].map((q) => {
                const target = new Date();
                target.setDate(target.getDate() + q.days);
                const iso = target.toISOString().slice(0, 10);
                const isSelected = dueDate === iso;

                return (
                  <Pressable
                    key={q.label}
                    onPress={() => setQuickDate(q.days)}
                    style={[
                      styles.chip,
                      {
                        backgroundColor: isSelected ? colors.accent : colors.surfaceSubtle,
                        borderRadius: borderRadius.sm,
                      },
                    ]}
                  >
                    <Text
                      style={[
                        styles.chipText,
                        { color: isSelected ? '#FFFFFF' : colors.textSecondary },
                      ]}
                    >
                      {q.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            {/* Custom Date & Time Inputs */}
            <View style={styles.inputRow}>
              <View style={styles.flexHalf}>
                <TextInput
                  value={dueDate}
                  onChangeText={setDueDate}
                  placeholder="YYYY-MM-DD"
                  placeholderTextColor={colors.textMuted}
                  style={[
                    styles.smallInput,
                    {
                      backgroundColor: colors.inputBackground,
                      borderColor: colors.inputBorder,
                      color: colors.textPrimary,
                      borderRadius: borderRadius.md,
                    },
                  ]}
                />
              </View>
              <View style={styles.flexHalf}>
                <TextInput
                  value={dueTime}
                  onChangeText={setDueTime}
                  placeholder="Time (e.g. 14:00)"
                  placeholderTextColor={colors.textMuted}
                  style={[
                    styles.smallInput,
                    {
                      backgroundColor: colors.inputBackground,
                      borderColor: colors.inputBorder,
                      color: colors.textPrimary,
                      borderRadius: borderRadius.md,
                    },
                  ]}
                />
              </View>
            </View>

            {/* Priority Selector */}
            <Text style={[styles.fieldLabel, { color: colors.textSecondary }]}>PRIORITY</Text>
            <View style={styles.priorityRow}>
              {PRIORITIES.map((p) => {
                const isSelected = priority === p;
                let activeColor = colors.priorityMedium;
                if (p === 'urgent') activeColor = colors.priorityUrgent;
                if (p === 'high') activeColor = colors.priorityHigh;
                if (p === 'low') activeColor = colors.priorityLow;

                return (
                  <Pressable
                    key={p}
                    onPress={() => setPriority(p)}
                    style={[
                      styles.priorityOption,
                      {
                        backgroundColor: isSelected ? activeColor : colors.surfaceSubtle,
                        borderRadius: borderRadius.sm,
                      },
                    ]}
                  >
                    <Text
                      style={[
                        styles.priorityOptionText,
                        { color: isSelected ? '#FFFFFF' : colors.textSecondary },
                      ]}
                    >
                      {p.toUpperCase()}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            {/* Category Selector */}
            <Text style={[styles.fieldLabel, { color: colors.textSecondary }]}>CATEGORY</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.categoryScroll}>
              {CATEGORIES.map((c) => {
                const isSelected = category === c;
                return (
                  <Pressable
                    key={c}
                    onPress={() => setCategory(c)}
                    style={[
                      styles.categoryChip,
                      {
                        backgroundColor: isSelected ? colors.accent : colors.surfaceSubtle,
                        borderRadius: borderRadius.sm,
                      },
                    ]}
                  >
                    <Text
                      style={[
                        styles.categoryChipText,
                        { color: isSelected ? '#FFFFFF' : colors.textSecondary },
                      ]}
                    >
                      {c.charAt(0).toUpperCase() + c.slice(1)}
                    </Text>
                  </Pressable>
                );
              })}
            </ScrollView>

            {/* Save Button */}
            <Pressable
              onPress={handleSave}
              disabled={isSubmitting}
              style={({ pressed }) => [
                styles.saveButton,
                {
                  backgroundColor: colors.accent,
                  borderRadius: borderRadius.lg,
                  opacity: pressed || isSubmitting ? 0.8 : 1,
                },
              ]}
            >
              <Text style={styles.saveButtonText}>
                {isSubmitting ? 'Saving to Phone…' : 'Save Task Locally'}
              </Text>
              <Text style={styles.saveButtonIcon}>✓</Text>
            </Pressable>
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0, 0, 0, 0.65)',
  },
  backdrop: {
    flex: 1,
  },
  sheetContainer: {
    maxHeight: '90%',
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 32,
    borderTopWidth: 1,
  },
  grabHandle: {
    width: 38,
    height: 4,
    borderRadius: 2,
    alignSelf: 'center',
    marginBottom: 14,
  },
  sheetHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  sheetTitle: {
    fontSize: 19,
    fontWeight: '800',
    letterSpacing: -0.3,
  },
  sheetSubtitle: {
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
  formScroll: {
    marginBottom: 10,
  },
  titleInput: {
    fontSize: 17,
    fontWeight: '700',
    paddingVertical: 12,
    borderBottomWidth: 1,
    marginBottom: 12,
  },
  descInput: {
    fontSize: 14,
    padding: 12,
    borderWidth: 1,
    minHeight: 60,
    marginBottom: 14,
    textAlignVertical: 'top',
  },
  fieldLabel: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1.2,
    marginBottom: 8,
    marginTop: 6,
  },
  quickDateRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 8,
  },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  chipText: {
    fontSize: 12,
    fontWeight: '700',
  },
  inputRow: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 12,
  },
  flexHalf: {
    flex: 1,
  },
  smallInput: {
    fontSize: 13,
    padding: 10,
    borderWidth: 1,
  },
  priorityRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 14,
  },
  priorityOption: {
    flex: 1,
    paddingVertical: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  priorityOptionText: {
    fontSize: 11,
    fontWeight: '800',
  },
  categoryScroll: {
    flexDirection: 'row',
    marginBottom: 20,
  },
  categoryChip: {
    paddingHorizontal: 14,
    paddingVertical: 7,
    marginRight: 8,
  },
  categoryChipText: {
    fontSize: 12,
    fontWeight: '600',
  },
  saveButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 15,
    gap: 8,
    marginTop: 6,
  },
  saveButtonText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '800',
  },
  saveButtonIcon: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '900',
  },
});
