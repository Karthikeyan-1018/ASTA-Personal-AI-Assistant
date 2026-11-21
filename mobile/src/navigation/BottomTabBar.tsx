import React from 'react';
import { View, Text, StyleSheet, Pressable, Platform } from 'react-native';
import { useTheme } from '../theme/themeContext';
import { useNavigation, MainTab } from './NavigationContext';
import { useTasks } from '../hooks/useTasksContext';
import { Icon, IconName } from '../components/common/Icon';

interface TabItem {
  id: MainTab;
  label: string;
  icon: IconName;
}

const TABS: TabItem[] = [
  { id: 'home', label: 'Home', icon: 'home' },
  { id: 'tasks', label: 'Tasks', icon: 'tasks' },
  { id: 'calendar', label: 'Calendar', icon: 'calendar' },
  { id: 'asta', label: 'ASTA', icon: 'asta' },
  { id: 'profile', label: 'Profile', icon: 'profile' },
];

export const BottomTabBar: React.FC = () => {
  const { colors, borderRadius, spacing } = useTheme();
  const { currentTab, switchTab } = useNavigation();
  const { tasks } = useTasks();

  const pendingCount = tasks.filter((t) => !t.is_completed).length;

  return (
    <View
      style={[
        styles.container,
        {
          backgroundColor: colors.tabBarBackground,
          borderTopColor: colors.tabBarBorder,
          paddingBottom: Platform.OS === 'ios' ? 24 : 12,
        },
      ]}
    >
      {TABS.map((tab) => {
        const isActive = currentTab === tab.id;
        const color = isActive ? colors.tabBarActive : colors.tabBarInactive;

        return (
          <Pressable
            key={tab.id}
            onPress={() => switchTab(tab.id)}
            style={({ pressed }) => [
              styles.tabButton,
              { opacity: pressed ? 0.7 : 1 },
            ]}
          >
            <View style={styles.iconWrapper}>
              <Icon name={tab.icon} size={20} color={color} />
              {/* Badge for tasks count */}
              {tab.id === 'tasks' && pendingCount > 0 ? (
                <View
                  style={[
                    styles.counterBadge,
                    { backgroundColor: colors.accent, borderRadius: borderRadius.full },
                  ]}
                >
                  <Text style={styles.counterText}>
                    {pendingCount > 99 ? '99+' : pendingCount}
                  </Text>
                </View>
              ) : null}
            </View>
            <Text style={[styles.tabLabel, { color }]}>{tab.label}</Text>
            {isActive ? (
              <View
                style={[
                  styles.activeIndicatorDot,
                  { backgroundColor: colors.accent, borderRadius: borderRadius.full },
                ]}
              />
            ) : null}
          </Pressable>
        );
      })}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'center',
    borderTopWidth: 1,
    paddingTop: 8,
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    elevation: 8,
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: -2 },
    shadowOpacity: 0.1,
    shadowRadius: 8,
  },
  tabButton: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 4,
    paddingHorizontal: 12,
    minWidth: 64,
  },
  iconWrapper: {
    position: 'relative',
    alignItems: 'center',
    justifyContent: 'center',
    height: 24,
  },
  tabLabel: {
    fontSize: 10,
    fontWeight: '700',
    marginTop: 4,
    letterSpacing: 0.2,
  },
  activeIndicatorDot: {
    width: 4,
    height: 4,
    marginTop: 3,
  },
  counterBadge: {
    position: 'absolute',
    top: -4,
    right: -10,
    minWidth: 16,
    height: 16,
    paddingHorizontal: 4,
    alignItems: 'center',
    justifyContent: 'center',
  },
  counterText: {
    color: '#FFFFFF',
    fontSize: 9,
    fontWeight: '800',
  },
});
