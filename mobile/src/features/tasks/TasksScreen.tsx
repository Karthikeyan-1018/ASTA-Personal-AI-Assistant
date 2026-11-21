import React, { useState, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  ScrollView,
  Pressable,
} from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useTasks } from '../../hooks/useTasksContext';
import { useNavigation } from '../../navigation/NavigationContext';
import { TaskCard } from '../../components/tasks/TaskCard';
import { TaskCategory, TaskFilter, TaskSortOption, LocalTask } from '../../types/task';
import { Icon } from '../../components/common/Icon';

const FILTERS: { id: TaskFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'today', label: 'Today' },
  { id: 'upcoming', label: 'Upcoming' },
  { id: 'overdue', label: 'Overdue' },
  { id: 'important', label: 'Important' },
  { id: 'completed', label: 'Completed' },
];

const CATEGORIES: { id: TaskCategory | 'all'; label: string }[] = [
  { id: 'all', label: 'All Categories' },
  { id: 'college', label: 'College' },
  { id: 'assignments', label: 'Assignments' },
  { id: 'exams', label: 'Exams' },
  { id: 'projects', label: 'Projects' },
  { id: 'personal', label: 'Personal' },
  { id: 'meetings', label: 'Meetings' },
  { id: 'learning', label: 'Learning' },
  { id: 'other', label: 'Other' },
];

export const TasksScreen: React.FC = () => {
  const { colors, borderRadius, spacing } = useTheme();
  const { tasks, toggleTask } = useTasks();
  const { openModal } = useNavigation();

  const [activeFilter, setActiveFilter] = useState<TaskFilter>('all');
  const [selectedCategory, setSelectedCategory] = useState<TaskCategory | 'all'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortOption, setSortOption] = useState<TaskSortOption>('created_at');

  const todayStr = new Date().toISOString().slice(0, 10);

  // Filter & sort logic
  const filteredTasks = useMemo(() => {
    let result = tasks.filter((task) => {
      // 1. Text Search
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim();
        const matchTitle = task.title.toLowerCase().includes(q);
        const matchDesc = task.description?.toLowerCase().includes(q) || false;
        const matchTags = task.tags.some((t) => t.toLowerCase().includes(q));
        if (!matchTitle && !matchDesc && !matchTags) return false;
      }

      // 2. Category Filter
      if (selectedCategory !== 'all' && task.category !== selectedCategory) {
        return false;
      }

      // 3. Tab Filter
      switch (activeFilter) {
        case 'today':
          return task.due_date === todayStr && !task.is_completed;
        case 'upcoming':
          return Boolean(task.due_date && task.due_date > todayStr && !task.is_completed);
        case 'overdue':
          return Boolean(task.due_date && task.due_date < todayStr && !task.is_completed);
        case 'completed':
          return task.is_completed;
        case 'important':
          return (task.priority === 'urgent' || task.priority === 'high') && !task.is_completed;
        case 'all':
        default:
          return true;
      }
    });

    // Sort
    result.sort((a, b) => {
      switch (sortOption) {
        case 'due_date':
          if (!a.due_date) return 1;
          if (!b.due_date) return -1;
          return a.due_date.localeCompare(b.due_date);
        case 'priority': {
          const pOrder = { urgent: 4, high: 3, medium: 2, low: 1 };
          return pOrder[b.priority] - pOrder[a.priority];
        }
        case 'title':
          return a.title.localeCompare(b.title);
        case 'created_at':
        default:
          return b.created_at.localeCompare(a.created_at);
      }
    });

    return result;
  }, [tasks, activeFilter, selectedCategory, searchQuery, sortOption, todayStr]);

  const cycleSort = () => {
    const opts: TaskSortOption[] = ['created_at', 'due_date', 'priority', 'title'];
    const idx = opts.indexOf(sortOption);
    setSortOption(opts[(idx + 1) % opts.length]);
  };

  const sortLabels: Record<TaskSortOption, string> = {
    created_at: 'Recent',
    due_date: 'Deadline',
    priority: 'Priority',
    title: 'A-Z',
  };

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Search & Header Bar */}
      <View style={[styles.headerArea, { paddingHorizontal: spacing.lg, paddingTop: spacing.md }]}>
        <View style={styles.titleRow}>
          <Text style={[styles.screenTitle, { color: colors.textPrimary }]}>Tasks</Text>
          <Pressable
            onPress={cycleSort}
            style={[
              styles.sortBtn,
              { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.sm },
            ]}
          >
            <Icon name="sort" size={13} color={colors.textSecondary} />
            <Text style={[styles.sortBtnText, { color: colors.textSecondary }]}>
              {sortLabels[sortOption]}
            </Text>
          </Pressable>
        </View>

        {/* Search Input */}
        <View
          style={[
            styles.searchBar,
            {
              backgroundColor: colors.inputBackground,
              borderColor: colors.inputBorder,
              borderRadius: borderRadius.md,
            },
          ]}
        >
          <Icon name="search" size={16} color={colors.textMuted} />
          <TextInput
            value={searchQuery}
            onChangeText={setSearchQuery}
            placeholder="Search tasks, notes, or tags…"
            placeholderTextColor={colors.textMuted}
            style={[styles.searchInput, { color: colors.textPrimary }]}
          />
          {searchQuery.length > 0 ? (
            <Pressable onPress={() => setSearchQuery('')} hitSlop={8}>
              <Icon name="close" size={14} color={colors.textMuted} />
            </Pressable>
          ) : null}
        </View>
      </View>

      {/* Main Filter Tabs */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.filterScroll}
        contentContainerStyle={[styles.filterScrollContent, { paddingHorizontal: spacing.lg }]}
      >
        {FILTERS.map((f) => {
          const isActive = activeFilter === f.id;
          return (
            <Pressable
              key={f.id}
              onPress={() => setActiveFilter(f.id)}
              style={[
                styles.filterTab,
                {
                  backgroundColor: isActive ? colors.accent : colors.surfaceSubtle,
                  borderRadius: borderRadius.sm,
                },
              ]}
            >
              <Text
                style={[
                  styles.filterTabText,
                  { color: isActive ? '#FFFFFF' : colors.textSecondary },
                ]}
              >
                {f.label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {/* Category Pills Strip */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.categoryScroll}
        contentContainerStyle={[styles.categoryScrollContent, { paddingHorizontal: spacing.lg }]}
      >
        {CATEGORIES.map((cat) => {
          const isSelected = selectedCategory === cat.id;
          return (
            <Pressable
              key={cat.id}
              onPress={() => setSelectedCategory(cat.id)}
              style={[
                styles.categoryPill,
                {
                  borderColor: isSelected ? colors.accent : colors.border,
                  backgroundColor: isSelected
                    ? colors.isDark
                      ? 'rgba(99, 102, 241, 0.18)'
                      : '#EEF2FF'
                    : 'transparent',
                  borderRadius: borderRadius.full,
                },
              ]}
            >
              <Text
                style={[
                  styles.categoryPillText,
                  { color: isSelected ? colors.accentLight : colors.textMuted },
                ]}
              >
                {cat.label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {/* Task List */}
      <ScrollView
        style={styles.listArea}
        contentContainerStyle={[styles.listContent, { paddingBottom: 120, paddingHorizontal: spacing.lg }]}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.countRow}>
          <Text style={[styles.countLabel, { color: colors.textMuted }]}>
            {filteredTasks.length} {filteredTasks.length === 1 ? 'task' : 'tasks'}
          </Text>
        </View>

        {filteredTasks.length === 0 ? (
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
            <Text style={styles.emptyIcon}>✓</Text>
            <Text style={[styles.emptyTitle, { color: colors.textPrimary }]}>No tasks found</Text>
            <Text style={[styles.emptySubtitle, { color: colors.textSecondary }]}>
              {searchQuery
                ? 'Try a different search keyword.'
                : 'No tasks match this filter. Tap below to create one.'}
            </Text>
            <Pressable
              onPress={() => openModal({ type: 'create_task' })}
              style={[
                styles.createBtn,
                { backgroundColor: colors.accent, borderRadius: borderRadius.sm },
              ]}
            >
              <Text style={styles.createBtnText}>＋ Create New Task</Text>
            </Pressable>
          </View>
        ) : (
          filteredTasks.map((task) => (
            <TaskCard key={task.local_id} task={task} onToggle={toggleTask} />
          ))
        )}
      </ScrollView>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  headerArea: {
    marginBottom: 10,
  },
  titleRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  screenTitle: {
    fontSize: 26,
    fontWeight: '900',
    letterSpacing: -0.5,
  },
  sortBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  sortBtnText: {
    fontSize: 11,
    fontWeight: '700',
  },
  searchBar: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    paddingHorizontal: 12,
    height: 42,
    gap: 8,
  },
  searchInput: {
    flex: 1,
    fontSize: 13,
    paddingVertical: 0,
  },
  filterScroll: {
    maxHeight: 38,
    marginBottom: 8,
  },
  filterScrollContent: {
    gap: 8,
    alignItems: 'center',
  },
  filterTab: {
    paddingHorizontal: 14,
    paddingVertical: 7,
  },
  filterTabText: {
    fontSize: 12,
    fontWeight: '700',
  },
  categoryScroll: {
    maxHeight: 34,
    marginBottom: 10,
  },
  categoryScrollContent: {
    gap: 6,
    alignItems: 'center',
  },
  categoryPill: {
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderWidth: 1,
  },
  categoryPillText: {
    fontSize: 11,
    fontWeight: '600',
  },
  listArea: {
    flex: 1,
  },
  listContent: {
    paddingTop: 6,
  },
  countRow: {
    marginBottom: 10,
  },
  countLabel: {
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
  emptyCard: {
    padding: 30,
    alignItems: 'center',
    borderWidth: 1,
    borderStyle: 'dashed',
    marginTop: 10,
  },
  emptyIcon: {
    fontSize: 28,
    color: '#6366F1',
    marginBottom: 10,
  },
  emptyTitle: {
    fontSize: 16,
    fontWeight: '700',
  },
  emptySubtitle: {
    fontSize: 12,
    textAlign: 'center',
    marginTop: 4,
    maxWidth: 240,
    lineHeight: 18,
  },
  createBtn: {
    marginTop: 16,
    paddingHorizontal: 18,
    paddingVertical: 10,
  },
  createBtnText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '800',
  },
});
