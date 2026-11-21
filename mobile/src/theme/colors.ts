export interface ThemeColors {
  isDark: boolean;
  background: string;
  surface: string;
  surfaceSubtle: string;
  surfaceRaised: string;
  card: string;
  cardBorder: string;
  modalBackground: string;
  textPrimary: string;
  textSecondary: string;
  textMuted: string;
  textInverse: string;
  accent: string;
  accentLight: string;
  accentGradientStart: string;
  accentGradientEnd: string;
  border: string;
  borderStrong: string;
  divider: string;
  inputBackground: string;
  inputBorder: string;
  tabBarBackground: string;
  tabBarBorder: string;
  tabBarActive: string;
  tabBarInactive: string;
  success: string;
  successLight: string;
  warning: string;
  warningLight: string;
  danger: string;
  dangerLight: string;
  info: string;
  infoLight: string;
  priorityLow: string;
  priorityLowBg: string;
  priorityMedium: string;
  priorityMediumBg: string;
  priorityHigh: string;
  priorityHighBg: string;
  priorityUrgent: string;
  priorityUrgentBg: string;
  categoryCollege: string;
  categoryAssignments: string;
  categoryExams: string;
  categoryProjects: string;
  categoryPersonal: string;
  categoryMeetings: string;
  categoryLearning: string;
  categoryOther: string;
  badgeBg: string;
  syncSynced: string;
  syncPending: string;
  syncFailed: string;
}

export const darkColors: ThemeColors = {
  isDark: true,
  background: '#0B0D13',
  surface: '#12151E',
  surfaceSubtle: '#181D29',
  surfaceRaised: '#1E2433',
  card: '#141824',
  cardBorder: 'rgba(255, 255, 255, 0.08)',
  modalBackground: '#11141E',
  textPrimary: '#F8FAFC',
  textSecondary: '#94A3B8',
  textMuted: '#64748B',
  textInverse: '#0B0D13',
  accent: '#6366F1',
  accentLight: '#818CF8',
  accentGradientStart: '#6366F1',
  accentGradientEnd: '#4338CA',
  border: 'rgba(255, 255, 255, 0.1)',
  borderStrong: 'rgba(255, 255, 255, 0.2)',
  divider: 'rgba(255, 255, 255, 0.06)',
  inputBackground: '#171B26',
  inputBorder: 'rgba(255, 255, 255, 0.12)',
  tabBarBackground: '#0F121A',
  tabBarBorder: 'rgba(255, 255, 255, 0.08)',
  tabBarActive: '#818CF8',
  tabBarInactive: '#64748B',
  success: '#10B981',
  successLight: 'rgba(16, 185, 129, 0.18)',
  warning: '#F59E0B',
  warningLight: 'rgba(245, 158, 11, 0.18)',
  danger: '#EF4444',
  dangerLight: 'rgba(239, 68, 68, 0.18)',
  info: '#38BDF8',
  infoLight: 'rgba(56, 189, 248, 0.18)',
  priorityLow: '#10B981',
  priorityLowBg: 'rgba(16, 185, 129, 0.15)',
  priorityMedium: '#6366F1',
  priorityMediumBg: 'rgba(99, 102, 241, 0.15)',
  priorityHigh: '#F59E0B',
  priorityHighBg: 'rgba(245, 158, 11, 0.15)',
  priorityUrgent: '#EF4444',
  priorityUrgentBg: 'rgba(239, 68, 68, 0.2)',
  categoryCollege: '#38BDF8',
  categoryAssignments: '#818CF8',
  categoryExams: '#F43F5E',
  categoryProjects: '#A855F7',
  categoryPersonal: '#34D399',
  categoryMeetings: '#FBBF24',
  categoryLearning: '#2DD4BF',
  categoryOther: '#94A3B8',
  badgeBg: 'rgba(255, 255, 255, 0.07)',
  syncSynced: '#10B981',
  syncPending: '#F59E0B',
  syncFailed: '#EF4444',
};

export const lightColors: ThemeColors = {
  isDark: false,
  background: '#F8FAFC',
  surface: '#FFFFFF',
  surfaceSubtle: '#F1F5F9',
  surfaceRaised: '#FFFFFF',
  card: '#FFFFFF',
  cardBorder: 'rgba(0, 0, 0, 0.06)',
  modalBackground: '#FFFFFF',
  textPrimary: '#0F172A',
  textSecondary: '#475569',
  textMuted: '#94A3B8',
  textInverse: '#FFFFFF',
  accent: '#4F46E5',
  accentLight: '#6366F1',
  accentGradientStart: '#4F46E5',
  accentGradientEnd: '#4338CA',
  border: 'rgba(0, 0, 0, 0.08)',
  borderStrong: 'rgba(0, 0, 0, 0.18)',
  divider: 'rgba(0, 0, 0, 0.06)',
  inputBackground: '#F1F5F9',
  inputBorder: 'rgba(0, 0, 0, 0.12)',
  tabBarBackground: '#FFFFFF',
  tabBarBorder: 'rgba(0, 0, 0, 0.07)',
  tabBarActive: '#4F46E5',
  tabBarInactive: '#94A3B8',
  success: '#059669',
  successLight: 'rgba(5, 150, 105, 0.12)',
  warning: '#D97706',
  warningLight: 'rgba(217, 119, 6, 0.12)',
  danger: '#DC2626',
  dangerLight: 'rgba(220, 38, 38, 0.12)',
  info: '#0284C7',
  infoLight: 'rgba(2, 132, 199, 0.12)',
  priorityLow: '#059669',
  priorityLowBg: 'rgba(5, 150, 105, 0.12)',
  priorityMedium: '#4F46E5',
  priorityMediumBg: 'rgba(79, 70, 229, 0.12)',
  priorityHigh: '#D97706',
  priorityHighBg: 'rgba(217, 119, 6, 0.12)',
  priorityUrgent: '#DC2626',
  priorityUrgentBg: 'rgba(220, 38, 38, 0.15)',
  categoryCollege: '#0284C7',
  categoryAssignments: '#4F46E5',
  categoryExams: '#E11D48',
  categoryProjects: '#9333EA',
  categoryPersonal: '#059669',
  categoryMeetings: '#D97706',
  categoryLearning: '#0D9488',
  categoryOther: '#64748B',
  badgeBg: 'rgba(0, 0, 0, 0.05)',
  syncSynced: '#059669',
  syncPending: '#D97706',
  syncFailed: '#DC2626',
};
