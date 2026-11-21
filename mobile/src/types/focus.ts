export type FocusSessionType = 'pomodoro' | 'deep_work' | 'custom' | 'break';

export interface FocusSession {
  id: string;
  task_id: string | null;
  task_title: string | null;
  duration_minutes: number;
  completed_at: string;
  session_type: FocusSessionType;
}

export interface FocusTimerState {
  isActive: boolean;
  isPaused: boolean;
  durationSeconds: number;
  remainingSeconds: number;
  sessionType: FocusSessionType;
  selectedTaskId: string | null;
  selectedTaskTitle: string | null;
}
