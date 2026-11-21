import React, { useState, useEffect, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  ScrollView,
  Pressable,
  Modal,
  Alert,
} from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { LocalNote } from '../../types/notes';
import {
  getAllNotes,
  createNote,
  updateNote,
  deleteNote,
  togglePinNote,
} from '../../database/notesRepository';
import { Icon } from '../../components/common/Icon';

interface NotesScreenProps {
  onClose?: () => void;
}

export const NotesScreen: React.FC<NotesScreenProps> = ({ onClose }) => {
  const { colors, borderRadius, spacing } = useTheme();

  const [notes, setNotes] = useState<LocalNote[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [editingNote, setEditingNote] = useState<LocalNote | null>(null);
  const [isEditorVisible, setIsEditorVisible] = useState(false);

  // Form fields
  const [formTitle, setFormTitle] = useState('');
  const [formContent, setFormContent] = useState('');
  const [formCategory, setFormCategory] = useState('general');
  const [formIsPinned, setFormIsPinned] = useState(false);

  const loadNotes = async () => {
    try {
      const data = await getAllNotes();
      setNotes(data);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    loadNotes();
  }, []);

  const filteredNotes = useMemo(() => {
    if (!searchQuery.trim()) return notes;
    const q = searchQuery.toLowerCase().trim();
    return notes.filter(
      (n) => n.title.toLowerCase().includes(q) || n.content.toLowerCase().includes(q)
    );
  }, [notes, searchQuery]);

  const handleOpenCreate = () => {
    setEditingNote(null);
    setFormTitle('');
    setFormContent('');
    setFormCategory('general');
    setFormIsPinned(false);
    setIsEditorVisible(true);
  };

  const handleOpenEdit = (n: LocalNote) => {
    setEditingNote(n);
    setFormTitle(n.title);
    setFormContent(n.content);
    setFormCategory(n.category);
    setFormIsPinned(n.is_pinned);
    setIsEditorVisible(true);
  };

  const handleSaveNote = async () => {
    if (!formTitle.trim()) {
      Alert.alert('Title required', 'Please add a note title.');
      return;
    }
    if (editingNote) {
      await updateNote(editingNote.id, {
        title: formTitle.trim(),
        content: formContent.trim(),
        category: formCategory,
        is_pinned: formIsPinned,
      });
    } else {
      await createNote({
        title: formTitle.trim(),
        content: formContent.trim(),
        category: formCategory,
        is_pinned: formIsPinned,
      });
    }
    setIsEditorVisible(false);
    await loadNotes();
  };

  const handleDelete = (id: string, title: string) => {
    Alert.alert('Delete Note', `Are you sure you want to delete "${title}"?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          await deleteNote(id);
          await loadNotes();
        },
      },
    ]);
  };

  const handleTogglePin = async (id: string) => {
    await togglePinNote(id);
    await loadNotes();
  };

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Top Header */}
      <View style={[styles.topRow, { paddingHorizontal: spacing.lg, paddingTop: spacing.md }]}>
        <View>
          <Text style={[styles.screenTitle, { color: colors.textPrimary }]}>Notes</Text>
          <Text style={[styles.screenSub, { color: colors.textSecondary }]}>
            Scratchpad & quick memos stored on phone
          </Text>
        </View>

        <View style={styles.topActions}>
          <Pressable
            onPress={handleOpenCreate}
            style={[styles.addBtn, { backgroundColor: colors.accent, borderRadius: borderRadius.sm }]}
          >
            <Text style={styles.addBtnText}>＋ New Note</Text>
          </Pressable>
          {onClose ? (
            <Pressable
              onPress={onClose}
              style={[styles.closeBtn, { backgroundColor: colors.surfaceSubtle }]}
            >
              <Icon name="close" size={16} color={colors.textSecondary} />
            </Pressable>
          ) : null}
        </View>
      </View>

      {/* Search Bar */}
      <View style={{ paddingHorizontal: spacing.lg, marginVertical: 10 }}>
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
          <Icon name="search" size={15} color={colors.textMuted} />
          <TextInput
            value={searchQuery}
            onChangeText={setSearchQuery}
            placeholder="Search notes…"
            placeholderTextColor={colors.textMuted}
            style={[styles.searchInput, { color: colors.textPrimary }]}
          />
        </View>
      </View>

      {/* Notes List */}
      <ScrollView
        style={styles.notesList}
        contentContainerStyle={[styles.notesListContent, { paddingHorizontal: spacing.lg, paddingBottom: 110 }]}
        showsVerticalScrollIndicator={false}
      >
        {filteredNotes.length === 0 ? (
          <View
            style={[
              styles.emptyNotesCard,
              {
                backgroundColor: colors.surface,
                borderColor: colors.border,
                borderRadius: borderRadius.lg,
              },
            ]}
          >
            <Text style={styles.emptyNoteIcon}>≡</Text>
            <Text style={[styles.emptyNoteTitle, { color: colors.textPrimary }]}>No notes yet</Text>
            <Text style={[styles.emptyNoteSub, { color: colors.textMuted }]}>
              Capture thoughts, assignment notes, or meeting memos.
            </Text>
            <Pressable
              onPress={handleOpenCreate}
              style={[
                styles.emptyCreateBtn,
                { backgroundColor: colors.accent, borderRadius: borderRadius.sm },
              ]}
            >
              <Text style={styles.emptyCreateBtnText}>＋ Create First Note</Text>
            </Pressable>
          </View>
        ) : (
          filteredNotes.map((note) => (
            <Pressable
              key={note.id}
              onPress={() => handleOpenEdit(note)}
              style={({ pressed }) => [
                styles.noteCard,
                {
                  backgroundColor: colors.surface,
                  borderColor: note.is_pinned ? colors.accent : colors.cardBorder,
                  borderRadius: borderRadius.md,
                  opacity: pressed ? 0.9 : 1,
                },
              ]}
            >
              <View style={styles.noteTop}>
                <View style={styles.titleWrap}>
                  {note.is_pinned ? (
                    <Text style={[styles.pinnedTag, { color: colors.accentLight }]}>📌 </Text>
                  ) : null}
                  <Text style={[styles.noteTitle, { color: colors.textPrimary }]} numberOfLines={1}>
                    {note.title}
                  </Text>
                </View>
                <Pressable onPress={() => handleTogglePin(note.id)} hitSlop={10}>
                  <Text style={[styles.pinBtn, { color: note.is_pinned ? colors.accent : colors.textMuted }]}>
                    {note.is_pinned ? '★' : '☆'}
                  </Text>
                </Pressable>
              </View>

              <Text style={[styles.noteContent, { color: colors.textSecondary }]} numberOfLines={3}>
                {note.content}
              </Text>

              <View style={styles.noteFooter}>
                <Text style={[styles.noteDate, { color: colors.textMuted }]}>
                  {new Date(note.updated_at).toLocaleDateString('en-US', {
                    month: 'short',
                    day: 'numeric',
                  })}
                </Text>
                <Pressable onPress={() => handleDelete(note.id, note.title)} hitSlop={8}>
                  <Icon name="trash" size={13} color={colors.textMuted} />
                </Pressable>
              </View>
            </Pressable>
          ))
        )}
      </ScrollView>

      {/* Note Editor Modal */}
      <Modal visible={isEditorVisible} transparent animationType="slide">
        <View style={styles.editorOverlay}>
          <View
            style={[
              styles.editorContainer,
              {
                backgroundColor: colors.surface,
                borderTopLeftRadius: borderRadius.xl,
                borderTopRightRadius: borderRadius.xl,
              },
            ]}
          >
            <View style={styles.editorHeader}>
              <Text style={[styles.editorHeaderTitle, { color: colors.textPrimary }]}>
                {editingNote ? 'Edit Note' : 'New Note'}
              </Text>
              <Pressable
                onPress={() => setIsEditorVisible(false)}
                style={[styles.closeBtn, { backgroundColor: colors.surfaceSubtle }]}
              >
                <Icon name="close" size={16} color={colors.textSecondary} />
              </Pressable>
            </View>

            <TextInput
              value={formTitle}
              onChangeText={setFormTitle}
              placeholder="Title"
              placeholderTextColor={colors.textMuted}
              style={[
                styles.titleEditorInput,
                {
                  color: colors.textPrimary,
                  borderBottomColor: colors.border,
                },
              ]}
            />

            <TextInput
              value={formContent}
              onChangeText={setFormContent}
              placeholder="Write your note here…"
              placeholderTextColor={colors.textMuted}
              multiline
              textAlignVertical="top"
              style={[
                styles.contentEditorInput,
                {
                  color: colors.textPrimary,
                  backgroundColor: colors.inputBackground,
                  borderColor: colors.inputBorder,
                  borderRadius: borderRadius.md,
                },
              ]}
            />

            <Pressable
              onPress={() => setFormIsPinned((p) => !p)}
              style={styles.pinToggleRow}
            >
              <Text style={[styles.pinToggleText, { color: colors.textSecondary }]}>
                {formIsPinned ? '📌 Pinned to top' : '☆ Pin note to top'}
              </Text>
            </Pressable>

            <Pressable
              onPress={handleSaveNote}
              style={[
                styles.saveNoteBtn,
                { backgroundColor: colors.accent, borderRadius: borderRadius.md },
              ]}
            >
              <Text style={styles.saveNoteBtnText}>Save Note</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  topRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
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
  topActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  addBtn: {
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  addBtnText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '800',
  },
  closeBtn: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
  },
  searchBar: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    paddingHorizontal: 12,
    height: 40,
    gap: 8,
  },
  searchInput: {
    flex: 1,
    fontSize: 13,
  },
  notesList: {
    flex: 1,
  },
  notesListContent: {
    gap: 10,
    paddingTop: 6,
  },
  emptyNotesCard: {
    padding: 30,
    alignItems: 'center',
    borderWidth: 1,
    borderStyle: 'dashed',
    marginTop: 20,
  },
  emptyNoteIcon: {
    fontSize: 28,
    color: '#6366F1',
    marginBottom: 8,
  },
  emptyNoteTitle: {
    fontSize: 16,
    fontWeight: '700',
  },
  emptyNoteSub: {
    fontSize: 12,
    textAlign: 'center',
    marginTop: 4,
    maxWidth: 240,
  },
  emptyCreateBtn: {
    marginTop: 14,
    paddingHorizontal: 16,
    paddingVertical: 9,
  },
  emptyCreateBtnText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '800',
  },
  noteCard: {
    padding: 14,
    borderWidth: 1,
  },
  noteTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  titleWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    paddingRight: 8,
  },
  pinnedTag: {
    fontSize: 12,
  },
  noteTitle: {
    fontSize: 15,
    fontWeight: '700',
  },
  pinBtn: {
    fontSize: 16,
    fontWeight: '800',
  },
  noteContent: {
    fontSize: 13,
    lineHeight: 18,
    marginBottom: 10,
  },
  noteFooter: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  noteDate: {
    fontSize: 10,
    fontWeight: '600',
  },
  editorOverlay: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0, 0, 0, 0.7)',
  },
  editorContainer: {
    maxHeight: '85%',
    padding: 20,
    paddingBottom: 36,
  },
  editorHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 14,
  },
  editorHeaderTitle: {
    fontSize: 18,
    fontWeight: '800',
  },
  titleEditorInput: {
    fontSize: 17,
    fontWeight: '700',
    paddingVertical: 10,
    borderBottomWidth: 1,
    marginBottom: 12,
  },
  contentEditorInput: {
    fontSize: 14,
    padding: 12,
    borderWidth: 1,
    minHeight: 140,
    marginBottom: 12,
  },
  pinToggleRow: {
    paddingVertical: 6,
    marginBottom: 14,
  },
  pinToggleText: {
    fontSize: 13,
    fontWeight: '600',
  },
  saveNoteBtn: {
    paddingVertical: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  saveNoteBtnText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '800',
  },
});
