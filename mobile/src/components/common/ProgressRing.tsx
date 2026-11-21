import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useTheme } from '../../theme/themeContext';

interface ProgressRingProps {
  progress: number; // 0 to 1
  size?: number;
  strokeWidth?: number;
  color?: string;
  backgroundColor?: string;
  centerText?: string;
  subText?: string;
}

export const ProgressRing: React.FC<ProgressRingProps> = ({
  progress,
  size = 110,
  strokeWidth = 10,
  color,
  backgroundColor,
  centerText,
  subText,
}) => {
  const { colors } = useTheme();

  const clamped = Math.max(0, Math.min(1, progress));
  const percent = Math.round(clamped * 100);

  const activeColor = color || colors.accentLight;
  const trackColor = backgroundColor || (colors.isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)');

  // Outer border with inner cutout creates a ring
  return (
    <View style={[styles.container, { width: size, height: size }]}>
      {/* Background Track */}
      <View
        style={[
          styles.ringBase,
          {
            width: size,
            height: size,
            borderRadius: size / 2,
            borderWidth: strokeWidth,
            borderColor: trackColor,
          },
        ]}
      />

      {/* Dynamic progress highlight */}
      <View
        style={[
          styles.ringBase,
          styles.activeIndicator,
          {
            width: size,
            height: size,
            borderRadius: size / 2,
            borderWidth: strokeWidth,
            borderColor: activeColor,
            borderTopColor: clamped > 0.15 ? activeColor : trackColor,
            borderRightColor: clamped > 0.4 ? activeColor : trackColor,
            borderBottomColor: clamped > 0.65 ? activeColor : trackColor,
            borderLeftColor: clamped > 0.85 ? activeColor : trackColor,
            transform: [{ rotate: '-45deg' }],
          },
        ]}
      />

      {/* Center Label */}
      <View style={styles.centerContent}>
        <Text style={[styles.centerPercent, { color: colors.textPrimary }]}>
          {centerText || `${percent}%`}
        </Text>
        {subText ? (
          <Text style={[styles.subLabel, { color: colors.textSecondary }]}>{subText}</Text>
        ) : null}
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    justifyContent: 'center',
    position: 'relative',
  },
  ringBase: {
    position: 'absolute',
  },
  activeIndicator: {
    position: 'absolute',
  },
  centerContent: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  centerPercent: {
    fontSize: 22,
    fontWeight: '800',
    letterSpacing: -0.5,
  },
  subLabel: {
    fontSize: 10,
    fontWeight: '600',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    marginTop: 2,
  },
});
