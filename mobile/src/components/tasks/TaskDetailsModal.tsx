import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  Modal,
  ScrollView,
  Alert,
} from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useTasks } from '../../hooks/useTasksContext';
import { useNavigation } from '../../navigation/NavigationContext';
import { LocalTask, TaskCategory, TaskPriority, SubTask } from '../../types/task';
import { Badge } from '../common/Badge';
import { Icon } from '../common/Icon';
import { formatFriendlyDate } from '../../utils/dateUtils';

interface TaskDetailsModalProps {
  visible: boolean;
  taskId: string | null;
  onClose: () => void;
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

export const TaskDetailsModal: React.FC<TaskDetailsModalProps> = ({ visible, taskId, onClose }) => {
  const { colors, borderRadius, spacing } = useTheme();
  const { tasks, modifyTask, removeTask, toggleTask } = useTasks();
  const { openModal } = useNavigation();

  const task = tasks.find((t) => t.local_id === taskId) || null;

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [priority, setPriority] = useState<TaskPriority>('medium');
  const [category, setCategory] = useState<TaskCategory>('other');
  const [dueDate, setDueDate] = useState('');
  const [notes, setNotes] = useState('');
  const [subtasks, setSubtasks] = useState<SubTask[]>([]);
  const [newSubtaskTitle, setNewSubtaskTitle] = useState('');
  const [isEditing, setIsEditing] = useState(false);

  useEffect(() => {
    if (task) {
      setTitle(task.title);
      setDescription(task.description || '');
      setPriority(task.priority);
      setCategory(task.category);
      setDueDate(task.due_date || '');
      setNotes(task.notes || '');
      setSubtasks(task.subtasks || []);
    }
  }, [task]);

  if (!task) return null;

  const isCompleted = task.is_completed || task.status === 'completed';

  const handleSaveEdits = async () => {
    if (!title.trim()) {
      Alert.alert('Title required', 'Task title cannot be empty.');
      return;
    }
    await modifyTask(task.local_id, {
      title: title.trim(),
      description: description.trim() || null,
      priority,
      category,
      due_date: dueDate.trim() || null,
      notes: notes.trim() || null,
      subtasks,
    });
    setIsEditing(false);
  };

  const handleAddSubtask = () => {
    if (!newSubtaskTitle.trim()) return;
    const updated = [
      ...subtasks,
      {
        id: `st-${Date.now()}-${Math.random().toString(36).slice(2, 5)}`,
        title: newSubtaskTitle.trim(),
        is_completed: false,
      },
    ];
    setSubtasks(updated);
    setNewSubtaskTitle('');
    modifyTask(task.local_id, { subtasks: updated });
  };

  const handleToggleSubtask = (stId: string) => {
    const updated = subtasks.map((st) =>
      st.id === stId ? { ...st, is_completed: !st.is_completed } : st
    );
    setSubtasks(updated);
    modifyTask(task.local_id, { subtasks: updated });
  };

  const handleDeleteSubtask = (stId: string) => {
    const updated = subtasks.filter((st) => st.id !== stId);
    setSubtasks(updated);
    modifyTask(task.local_id, { subtasks: updated });
  };

  const handleDeleteTask = () => {
    Alert.alert('Delete Task', `Are you sure you want to delete "${task.title}"?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          await removeTask(task.local_id);
          onClose();
        },
      },
    ]);
  };

  const handleStartFocus = () => {
    onClose();
    openModal({ type: 'focus_mode', taskId: task.local_id });
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <Pressable style={styles.backdrop} onPress={onClose} />

        <View
          style={[
            styles.modalContent,
            {
              backgroundColor: colors.surface,
              borderColor: colors.border,
              borderTopLeftRadius: borderRadius.xxl,
              borderTopRightRadius: borderRadius.xxl,
            },
          ]}
        >
          {/* Header Bar */}
          <View style={styles.topBar}>
            <Pressable
              onPress={() => toggleTask(task.local_id)}
              style={[
                styles.completePill,
                {
                  backgroundColor: isCompleted ? colors.successLight : colors.surfaceSubtle,
                  borderRadius: borderRadius.sm,
                },
              ]}
            >
              <Text
                style={[
                  styles.completePillText,
                  { color: isCompleted ? colors.success : colors.textSecondary },
                ]}
              >
                {isCompleted ? '✓ Completed' : '○ Mark Complete'}
              </Text>
            </Pressable>

            <View style={styles.headerActions}>
              <Pressable
                onPress={() => (isEditing ? handleSaveEdits() : setIsEditing(true))}
                style={[
                  styles.actionButton,
                  {
                    backgroundColor: isEditing ? colors.accent : colors.surfaceSubtle,
                    borderRadius: borderRadius.sm,
                  },
                ]}
              >
                <Text
                  style={[
                    styles.actionButtonText,
                    { color: isEditing ? '#FFFFFF' : colors.textSecondary },
                  ]}
                >
                  {isEditing ? 'Save' : 'Edit'}
                </Text>
              </Pressable>

              <Pressable
                onPress={handleDeleteTask}
                style={[styles.actionButton, { backgroundColor: colors.dangerLight, borderRadius: borderRadius.sm }]}
              >
                <Icon name="trash" size={14} color={colors.danger} />
              </Pressable>

              <Pressable onPress={onClose} style={styles.closeBtn}>
                <Icon name="close" size={16} color={colors.textSecondary} />
              </Pressable>
            </View>
          </View>

          <ScrollView style={styles.scrollArea} showsVerticalScrollIndicator={false}>
            {/* Title */}
            {isEditing ? (
              <TextInput
                value={title}
                onChangeText={setTitle}
                style={[
                  styles.titleInputEdit,
                  {
                    color: colors.textPrimary,
                    borderColor: colors.border,
                    backgroundColor: colors.inputBackground,
                    borderRadius: borderRadius.sm,
                  },
                ]}
              />
            ) : (
              <Text
                style={[
                  styles.taskTitle,
                  {
                    color: isCompleted ? colors.textMuted : colors.textPrimary,
                    textDecorationLine: isCompleted ? 'line-through' : 'none',
                  },
                ]}
              >
                {task.title}
              </Text>
            )}

            {/* Badges Row */}
            <View style={styles.badgesRow}>
              <Badge type="category" category={task.category} />
              <Badge type="priority" priority={task.priority} />
              <Badge type="sync" syncState={task.sync_state} />
            </View>

            {/* Quick Focus Action */}
            {!isCompleted ? (
              <Pressable
                onPress={handleStartFocus}
                style={({ pressed }) => [
                  styles.focusBanner,
                  {
                    backgroundColor: colors.surfaceSubtle,
                    borderColor: colors.border,
                    borderRadius: borderRadius.md,
                    opacity: pressed ? 0.8 : 1,
                  },
                ]}
              >
                <Text style={styles.focusIcon}>⚡</Text>
                <View style={styles.focusTextWrap}>
                  <Text style={[styles.focusTitle, { color: colors.textPrimary }]}>
                    Focus on this task
                  </Text>
                  <Text style={[styles.focusSub, { color: colors.textMuted }]}>
                    Launch 25m / 50m Pomodoro timer
                  </Text>
                </View>
                <Text style={[styles.arrowIcon, { color: colors.accentLight }]}>›</Text>
              </Pressable>
            ) : null}

            {/* Description */}
            <Text style={[styles.sectionTitle, { color: colors.textSecondary }]}>DESCRIPTION</Text>
            {isEditing ? (
              <TextInput
                value={description}
                onChangeText={setDescription}
                multiline
                placeholder="Add description"
                placeholderTextColor={colors.textMuted}
                style={[
                  styles.areaInput,
                  {
                    color: colors.textPrimary,
                    backgroundColor: colors.inputBackground,
                    borderColor: colors.inputBorder,
                    borderRadius: borderRadius.sm,
                  },
                ]}
              />
            ) : (
              <Text style={[styles.bodyText, { color: task.description ? colors.textPrimary : colors.textMuted }]}>
                {task.description || 'No description provided.'}
              </Text>
            )}

            {/* Due Date & Time */}
            <Text style={[styles.sectionTitle, { color: colors.textSecondary }]}>DEADLINE</Text>
            {isEditing ? (
              <TextInput
                value={dueDate}
                onChangeText={setDueDate}
                placeholder="YYYY-MM-DD"
                placeholderTextColor={colors.textMuted}
                style={[
                  styles.smallInputEdit,
                  {
                    color: colors.textPrimary,
                    backgroundColor: colors.inputBackground,
                    borderColor: colors.inputBorder,
                    borderRadius: borderRadius.sm,
                  },
                ]}
              />
            ) : (
              <Text style={[styles.bodyText, { color: colors.textPrimary }]}>
                {task.due_date ? `◷ ${formatFriendlyDate(task.due_date)}` : 'No deadline set'}
                {task.due_time ? ` at ${task.due_time}` : ''}
              </Text>
            )}

            {/* Subtasks Checklist */}
            <Text style={[styles.sectionTitle, { color: colors.textSecondary }]}>
              SUBTASKS ({subtasks.filter((s) => s.is_completed).length}/{subtasks.length})
            </Text>
            {subtasks.map((st) => (
              <View key={st.id} style={styles.subtaskItem}>
                <Pressable
                  onPress={() => handleToggleSubtask(st.id)}
                  style={[
                    styles.subCheck,
                    {
                      borderColor: st.is_completed ? colors.success : colors.borderStrong,
                      backgroundColor: st.is_completed ? colors.success : 'transparent',
                      borderRadius: borderRadius.xs,
                    },
                  ]}
                >
                  {st.is_completed ? <Text style={styles.subCheckIcon}>✓</Text> : null}
                </Pressable>
                <Text
                  style={[
                    styles.subtaskTitle,
                    {
                      color: st.is_completed ? colors.textMuted : colors.textPrimary,
                      textDecorationLine: st.is_completed ? 'line-through' : 'none',
                    },
                  ]}
                >
                  {st.title}
                </Text>
                <Pressable onPress={() => handleDeleteSubtask(st.id)} hitSlop={8}>
                  <Text style={[styles.subDelIcon, { color: colors.textMuted }]}>✕</Text>
                </Pressable>
              </View>
            ))}

            <View style={styles.addSubtaskRow}>
              <TextInput
                value={newSubtaskTitle}
                onChangeText={setNewSubtaskTitle}
                placeholder="Add subtask step…"
                placeholderTextColor={colors.textMuted}
                style={[
                  styles.subtaskInput,
                  {
                    color: colors.textPrimary,
                    backgroundColor: colors.inputBackground,
                    borderColor: colors.inputBorder,
                    borderRadius: borderRadius.sm,
                  },
                ]}
                onSubmitEditing={handleAddSubtask}
              />
              <Pressable
                onPress={handleAddSubtask}
                style={[styles.addSubtaskBtn, { backgroundColor: colors.accent, borderRadius: borderRadius.sm }]}
              >
                <Text style={styles.addSubtaskBtnText}>＋</Text>
              </Pressable>
            </View>

            {/* Notes */}
            <Text style={[styles.sectionTitle, { color: colors.textSecondary }]}>NOTES</Text>
            {isEditing ? (
              <TextInput
                value={notes}
                onChangeText={setNotes}
                multiline
                placeholder="Add personal notes or links…"
                placeholderTextColor={colors.textMuted}
                style={[
                  styles.areaInput,
                  {
                    color: colors.textPrimary,
                    backgroundColor: colors.inputBackground,
                    borderColor: colors.inputBorder,
                    borderRadius: borderRadius.sm,
                  },
                ]}
              />
            ) : (
              <Text style={[styles.bodyText, { color: task.notes ? colors.textPrimary : colors.textMuted }]}>
                {task.notes || 'No extra notes.'}
              </Text>
            )}

            {/* Sync Meta Footer */}
            <View style={[styles.syncMetaBox, { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.md }]}>
              <Text style={[styles.metaLabel, { color: colors.textMuted }]}>
                Local ID: {task.local_id}
              </Text>
              {task.server_id ? (
                <Text style={[styles.metaLabel, { color: colors.textMuted }]}>
                  Server ID: #{task.server_id} (Linked to laptop backend)
                </Text>
              ) : (
                <Text style={[styles.metaLabel, { color: colors.warning }]}>
                  Waiting for laptop server availability to sync
                </Text>
              )}
              <Text style={[styles.metaLabel, { color: colors.textMuted }]}>
                Created: {new Date(task.created_at).toLocaleString()}
              </Text>
            </View>
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0, 0, 0, 0.7)',
  },
  backdrop: {
    flex: 1,
  },
  modalContent: {
    maxHeight: '92%',
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 32,
    borderTopWidth: 1,
  },
  topBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  completePill: {
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  completePillText: {
    fontSize: 12,
    fontWeight: '800',
  },
  headerActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  actionButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionButtonText: {
    fontSize: 12,
    fontWeight: '700',
  },
  closeBtn: {
    padding: 6,
  },
  scrollArea: {
    marginBottom: 10,
  },
  taskTitle: {
    fontSize: 22,
    fontWeight: '800',
    letterSpacing: -0.4,
    lineHeight: 28,
  },
  titleInputEdit: {
    fontSize: 18,
    fontWeight: '700',
    padding: 10,
    borderWidth: 1,
    marginBottom: 10,
  },
  badgesRow: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 10,
    marginBottom: 14,
  },
  focusBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 12,
    borderWidth: 1,
    marginBottom: 16,
  },
  focusIcon: {
    fontSize: 20,
    marginRight: 10,
  },
  focusTextWrap: {
    flex: 1,
  },
  focusTitle: {
    fontSize: 13,
    fontWeight: '700',
  },
  focusSub: {
    fontSize: 11,
    marginTop: 1,
  },
  arrowIcon: {
    fontSize: 18,
    fontWeight: '700',
  },
  sectionTitle: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1.2,
    marginTop: 16,
    marginBottom: 6,
  },
  bodyText: {
    fontSize: 14,
    lineHeight: 20,
  },
  areaInput: {
    fontSize: 13,
    padding: 10,
    borderWidth: 1,
    minHeight: 60,
    textAlignVertical: 'top',
  },
  smallInputEdit: {
    fontSize: 13,
    padding: 8,
    borderWidth: 1,
  },
  subtaskItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 7,
    gap: 10,
  },
  subCheck: {
    width: 18,
    height: 18,
    borderWidth: 1.5,
    alignItems: 'center',
    justifyContent: 'center',
  },
  subCheckIcon: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '900',
  },
  subtaskTitle: {
    flex: 1,
    fontSize: 13,
  },
  subDelIcon: {
    fontSize: 14,
    paddingHorizontal: 6,
  },
  addSubtaskRow: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 8,
  },
  subtaskInput: {
    flex: 1,
    fontSize: 13,
    padding: 9,
    borderWidth: 1,
  },
  addSubtaskBtn: {
    width: 40,
    alignItems: 'center',
    justifyContent: 'center',
  },
  addSubtaskBtnText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '800',
  },
  syncMetaBox: {
    padding: 12,
    marginTop: 24,
    marginBottom: 16,
    gap: 4,
  },
  metaLabel: {
    fontSize: 11,
  },
});
