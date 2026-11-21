import React from 'react';
import { Text, View, StyleSheet, TextStyle, ViewStyle } from 'react-native';

export type IconName =
  | 'home'
  | 'tasks'
  | 'calendar'
  | 'asta'
  | 'profile'
  | 'plus'
  | 'check'
  | 'check-circle'
  | 'circle'
  | 'search'
  | 'filter'
  | 'sort'
  | 'clock'
  | 'flame'
  | 'bell'
  | 'settings'
  | 'notes'
  | 'analytics'
  | 'refresh'
  | 'sparkles'
  | 'mic'
  | 'send'
  | 'close'
  | 'chevron-right'
  | 'chevron-left'
  | 'trash'
  | 'edit'
  | 'pin'
  | 'tag'
  | 'offline'
  | 'cloud'
  | 'sun'
  | 'moon';

interface IconProps {
  name: IconName;
  size?: number;
  color?: string;
  style?: ViewStyle;
}

const GLYPH_MAP: Record<IconName, string> = {
  home: '⌂',
  tasks: '✓',
  calendar: '◷',
  asta: '✦',
  profile: '●',
  plus: '＋',
  check: '✓',
  'check-circle': '◉',
  circle: '○',
  search: '⌕',
  filter: '▼',
  sort: '⇅',
  clock: '⏱',
  flame: '⚡',
  bell: '▲',
  settings: '⚙',
  notes: '≡',
  analytics: '▦',
  refresh: '↻',
  sparkles: '✦',
  mic: '🎤',
  send: '➤',
  close: '✕',
  'chevron-right': '›',
  'chevron-left': '‹',
  trash: '🗑',
  edit: '✎',
  pin: '📌',
  tag: '🏷',
  offline: '⚡',
  cloud: '☁',
  sun: '☼',
  moon: '☾',
};

export const Icon: React.FC<IconProps> = ({ name, size = 18, color = '#FFFFFF', style }) => {
  const glyph = GLYPH_MAP[name] || '•';

  return (
    <View style={[styles.container, { width: size, height: size }, style]}>
      <Text
        style={[
          styles.text,
          {
            fontSize: size * 0.88,
            color,
            lineHeight: size,
          },
        ]}
      >
        {glyph}
      </Text>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  text: {
    textAlign: 'center',
    fontWeight: '700',
    includeFontPadding: false,
  },
});
