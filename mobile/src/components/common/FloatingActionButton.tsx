import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useNavigation } from '../../navigation/NavigationContext';

export const FloatingActionButton: React.FC = () => {
  const { colors, borderRadius, elevation } = useTheme();
  const { openModal } = useNavigation();

  return (
    <Pressable
      onPress={() => openModal({ type: 'create_task' })}
      style={({ pressed }) => [
        styles.fab,
        {
          backgroundColor: colors.accent,
          borderRadius: borderRadius.full,
          opacity: pressed ? 0.85 : 1,
          transform: [{ scale: pressed ? 0.94 : 1 }],
          ...elevation.fab,
        },
      ]}
    >
      <Text style={styles.fabIcon}>＋</Text>
    </Pressable>
  );
};

const styles = StyleSheet.create({
  fab: {
    position: 'absolute',
    right: 20,
    bottom: 84, // positioned nicely above the bottom tab bar
    width: 58,
    height: 58,
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 99,
  },
  fabIcon: {
    color: '#FFFFFF',
    fontSize: 28,
    fontWeight: '600',
    lineHeight: 32,
    textAlign: 'center',
  },
});
