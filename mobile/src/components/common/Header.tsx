import React from 'react';
import { View, Text, StyleSheet, Pressable } from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useTasks } from '../../hooks/useTasksContext';
import { useNavigation } from '../../navigation/NavigationContext';
import { formatGreeting } from '../../utils/dateUtils';
import { Icon } from './Icon';

interface HeaderProps {
  onSyncPress?: () => void;
}

export const Header: React.FC<HeaderProps> = ({ onSyncPress }) => {
  const { colors, spacing, borderRadius } = useTheme();
  const { syncStatus, triggerSync } = useTasks();
  const { switchTab, openModal } = useNavigation();

  const greeting = formatGreeting();
  const displayName = syncStatus.username || 'Friend';
  const currentDate = new Date().toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });

  const isConnected = syncStatus.isOnline;
  const isPending = syncStatus.pendingCount > 0;

  const handleSyncClick = () => {
    if (onSyncPress) onSyncPress();
    else triggerSync();
  };

  return (
    <View style={[styles.headerContainer, { paddingHorizontal: spacing.lg, paddingVertical: spacing.md }]}>
      {/* Top row: Brand & Status Pill */}
      <View style={styles.topRow}>
        <View>
          <View style={styles.logoRow}>
            <View style={[styles.logoIconBadge, { backgroundColor: colors.accent, borderRadius: borderRadius.xs }]}>
              <Text style={styles.logoIconText}>✦</Text>
            </View>
            <Text style={[styles.brandText, { color: colors.textPrimary }]}>ASTA</Text>
          </View>
          <Text style={[styles.dateSubtext, { color: colors.textMuted }]}>{currentDate.toUpperCase()}</Text>
        </View>

        {/* Sync Status Pill */}
        <Pressable
          onPress={handleSyncClick}
          style={({ pressed }) => [
            styles.syncPill,
            {
              backgroundColor: isPending
                ? colors.isDark
                  ? 'rgba(245, 158, 11, 0.15)'
                  : '#FEF3C7'
                : isConnected
                ? colors.isDark
                  ? 'rgba(16, 185, 129, 0.15)'
                  : '#D1FAE5'
                : colors.isDark
                ? 'rgba(255, 255, 255, 0.08)'
                : '#F1F5F9',
              borderRadius: borderRadius.full,
              opacity: pressed ? 0.75 : 1,
            },
          ]}
        >
          <View
            style={[
              styles.statusDot,
              {
                backgroundColor: isPending
                  ? colors.warning
                  : isConnected
                  ? colors.success
                  : colors.textMuted,
              },
            ]}
          />
          <Text
            style={[
              styles.syncPillText,
              {
                color: isPending
                  ? colors.warning
                  : isConnected
                  ? colors.success
                  : colors.textSecondary,
              },
            ]}
          >
            {syncStatus.isSyncing
              ? 'Syncing…'
              : isPending
              ? `${syncStatus.pendingCount} Waiting`
              : isConnected
              ? 'Connected'
              : 'On Device'}
          </Text>
        </Pressable>
      </View>

      {/* Greeting and Quick Profile Avatar */}
      <View style={styles.greetingRow}>
        <View>
          <Text style={[styles.greetingLabel, { color: colors.textSecondary }]}>{greeting},</Text>
          <Text style={[styles.userName, { color: colors.textPrimary }]}>{displayName}</Text>
        </View>

        <Pressable
          onPress={() => switchTab('profile')}
          style={({ pressed }) => [
            styles.avatarButton,
            {
              backgroundColor: colors.surfaceSubtle,
              borderColor: colors.border,
              borderRadius: borderRadius.full,
              opacity: pressed ? 0.8 : 1,
            },
          ]}
        >
          <Text style={[styles.avatarInitial, { color: colors.accentLight }]}>
            {displayName.charAt(0).toUpperCase()}
          </Text>
        </Pressable>
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  headerContainer: {
    width: '100%',
  },
  topRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  logoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  logoIconBadge: {
    width: 22,
    height: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  logoIconText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '900',
  },
  brandText: {
    fontSize: 20,
    fontWeight: '900',
    letterSpacing: 1.5,
  },
  dateSubtext: {
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1.2,
    marginTop: 2,
  },
  syncPill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 6,
    paddingHorizontal: 12,
    gap: 6,
  },
  statusDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
  },
  syncPillText: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
  greetingRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    marginTop: 4,
  },
  greetingLabel: {
    fontSize: 13,
    fontWeight: '500',
  },
  userName: {
    fontSize: 24,
    fontWeight: '800',
    letterSpacing: -0.5,
  },
  avatarButton: {
    width: 42,
    height: 42,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
  },
  avatarInitial: {
    fontSize: 18,
    fontWeight: '800',
  },
});
