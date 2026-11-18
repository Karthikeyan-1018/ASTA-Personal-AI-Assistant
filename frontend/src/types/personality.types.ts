export type PersonalityMode = 'normal' | 'motivational' | 'focus' | 'casual';

export const PERSONALITY_MODES: readonly PersonalityMode[] = [
  'normal',
  'motivational',
  'focus',
  'casual',
];

export interface PersonalitySettings {
  assistantName: string;
  mode: PersonalityMode;
  personalityTraits: string[];
  motivationalPhilosophy: string;
  proactiveMotivationEnabled: boolean;
  motivationIntensity: number;
  responseStyle: string;
  humorEnabled: boolean;
  hasOverrides: boolean;
}

export interface PersonalitySettingsUpdate {
  mode?: PersonalityMode;
  proactiveMotivationEnabled?: boolean;
  motivationIntensity?: number;
}
