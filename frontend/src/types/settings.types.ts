export interface UserSettings {
  assistantName: string;
  userDisplayName: string;
  theme: 'dark' | 'midnight' | 'obsidian';
  accentColor: 'indigo' | 'purple' | 'cyan' | 'emerald';
  compactMode: boolean;
  fontSize: 'sm' | 'md' | 'lg';
  defaultModel: string;
  streamingSpeed: 'fast' | 'normal' | 'relaxed';
  sendOnEnter: boolean;
  codeWrap: boolean;
  retentionDays: number;
  privateModeDefault: boolean;
  confirmElevatedTools: boolean;
  autoApproveReadOnly: boolean;
  auditLogging: boolean;
  ttsEnabled: boolean;
}
