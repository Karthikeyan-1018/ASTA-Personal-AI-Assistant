# ASTA Mobile – Companion Application

A dedicated, premium Android companion application for ASTA, designed to let you manage your tasks, schedules, deadlines, focus sessions, and notes from your phone even when your laptop is completely switched off.

---

## 1. Executive Summary & Architecture

ASTA Mobile is built with a **local-first, offline-resilient architecture**:
- **Offline First**: All user actions (creating tasks, checking subtasks, editing priorities, writing notes, running focus timers) write immediately to the phone's on-device SQLite database.
- **Zero Data Loss**: When your laptop is turned off or unavailable, tasks stay securely stored on device in WAL journal mode.
- **Task Synchronization Queue**: Task creation, updates, and deletions are stored locally with retry tracking. The current backend integration syncs tasks; notes and focus sessions remain on the phone.
- **Automatic / Manual Synchronization**: While the app is open, it checks the configured server on startup, when returning to the foreground, and every 30 seconds. Failed queue items remain pending and are reported instead of being marked successful.
- **Linear / Notion / Things 3 Grade Interface**: Polished dark theme as primary, light theme, custom geometric vector iconography, fluid tab navigation, progress rings, and bottom sheets.

```
C:\Asta\mobile\
  ├── android/                 # Native Android project with Gradle wrapper
  ├── dist/                    # Compiled Android Hermes bytecode bundle (1.8MB)
  ├── src/
  │   ├── api/
  │   │   └── apiClient.ts     # ASTA laptop REST client (Auth, /api/tasks/)
  │   ├── components/
  │   │   ├── common/          # Header, Icon, Badge, ProgressRing, FAB
  │   │   └── tasks/           # TaskCard, TaskFormBottomSheet, TaskDetailsModal
  │   ├── database/
  │   │   ├── db.ts            # Expo SQLite database & migrations
  │   │   ├── taskRepository.ts # Task persistence, CRUD, filtering & sorting
  │   │   ├── notesRepository.ts# Notes persistence & pin toggling
  │   │   └── focusRepository.ts# Focus session history & stats
  │   ├── features/
  │   │   ├── dashboard/       # Home dashboard with daily progress ring & stats
  │   │   ├── tasks/           # All, Today, Upcoming, Overdue, Important, Categories
  │   │   ├── calendar/        # Monthly grid, weekly strip, day agenda
  │   │   ├── focus/           # Pomodoro (25m/50m/break) circular timer
  │   │   ├── notes/           # Scratchpad & notes manager
  │   │   ├── reminders/       # Notification preferences & deadline alerts
  │   │   ├── analytics/       # Completion rates, 7-day velocity, priority breakdown
  │   │   ├── assistant/       # AI conversational interface with suggested prompts
  │   │   └── settings/        # Laptop connection, auth, theme, sync controls
  │   ├── hooks/
  │   │   └── useTasksContext.tsx
  │   ├── navigation/
  │   │   ├── BottomTabBar.tsx
  │   │   └── NavigationContext.tsx
  │   ├── services/
  │   │   ├── assistantService.ts
  │   │   └── notificationService.ts
  │   ├── sync/
  │   │   ├── syncService.ts    # Reconciles local SQLite with ASTA backend
  │   │   ├── syncQueue.ts      # Mutation queue with retry count & last attempt
  │   │   ├── mockSyncAdapter.ts# Offline simulation adapter for UI validation
  │   │   └── conflictResolver.ts # Conflict detection & resolution logic
  │   ├── theme/
  │   │   ├── colors.ts        # Dark charcoal (#0B0D13) & light (#F8FAFC) palette
  │   │   ├── spacing.ts
  │   │   ├── typography.ts
  │   │   └── themeContext.tsx
  │   ├── types/               # TypeScript models (Task, Sync, Notes, Focus, AI)
  │   └── utils/
  │       └── dateUtils.ts
  ├── App.tsx                  # App entry point
  ├── app.json                 # Expo configuration
  ├── eas.json                 # EAS APK preview build profile
  └── package.json
```

---

## 2. Implemented Features & Screens

### 1. Home Dashboard (`features/dashboard/DashboardScreen.tsx`)
- **Top Header**: ASTA brand mark, personalized greeting ("Good Morning / Afternoon / Evening"), date badge, and live sync pill.
- **Daily Progress Card**: Circular progress ring showing today's completion percentage, total tasks due today, completed count, and remaining count.
- **Quick Action Grid**: 1-tap shortcuts for *New Task*, *Focus Timer*, *Quick Note*, and *Analytics*.
- **Important & Urgent Alerts**: Highlighting overdue or urgent priorities requiring attention.
- **Today's Agenda**: Clean list of today's scheduled tasks with quick checkoff.

### 2. Task Management (`features/tasks/TasksScreen.tsx`)
- **Full CRUD**: Create, edit, delete, mark completed, or reopen.
- **Filters**: *All*, *Today*, *Upcoming*, *Overdue*, *Important*, and *Completed*.
- **Categories**: *College*, *Assignments*, *Exams*, *Projects*, *Personal*, *Meetings*, *Learning*, and *Other*.
- **Search & Sort**: Real-time keyword filter across titles, descriptions, and tags. Sort by *Recent*, *Deadline*, *Priority*, or *Alphabetical*.
- **Task Details Modal (`TaskDetailsModal.tsx`)**: Edit fields, manage subtask checklist steps, add notes, or jump straight into Focus Mode for that task.
- **Quick Create Sheet (`TaskFormBottomSheet.tsx`)**: Auto-focused title input, quick date chips ("Today", "Tomorrow", "Next Week"), priority selector, and category picker.

### 3. Calendar (`features/calendar/CalendarScreen.tsx`)
- **Month Grid View**: Visual dots indicating days with scheduled deadlines.
- **Week Strip View**: Day-by-day horizontal selector.
- **Day Agenda**: Hourly chronological list of tasks due on the selected day.
- **Direct Creation**: "＋ Add for this day" button creates a task directly assigned to the selected date.

### 4. Focus Mode (`features/focus/FocusScreen.tsx`)
- **Circular Countdown Timer**: Large animated ring with elapsed / remaining indicator.
- **Presets**: 25m Pomodoro, 50m Deep Work, 5m Short Break, and 15m Long Break.
- **Task Linking**: Link focus timer to a specific pending task or run free focus.
- **Local Persistence**: Completed sessions are logged to the `focus_sessions` SQLite table and displayed in Focus Analytics.

### 5. Notes (`features/notes/NotesScreen.tsx`)
- **Clean Scratchpad**: Create and edit markdown notes locally.
- **Pinning**: Pin key notes to the top of the screen.
- **Search**: Instant text search across note titles and content.

### 6. Productivity Analytics (`features/analytics/AnalyticsScreen.tsx`)
- **Completion Rate Ring**: Overall completion percentage of on-device tasks.
- **Weekly Velocity Bar Chart**: 7-day activity tracking.
- **Priority Distribution**: Urgent, High, Medium, and Low breakdown.
- **Category Breakdown**: Percentage distribution across task categories.
- **Local Device Badge**: Explicitly distinguishes local statistics from future cloud data.

### 7. ASTA AI Assistant UI (`features/assistant/AssistantScreen.tsx`)
- **Conversational UI**: Premium chat bubbles with Markdown styling (headings, bullets, blockquotes).
- **Interactive Prompts**: Quick suggestion chips ("What tasks are due today?", "Show urgent deadlines", "Recommend a focus session").
- **Voice / Mic**: Visual listening indicator and microphone button.
- **Decoupled Architecture**: Routes through `assistantService.ts`, ready to connect to ASTA's desktop LLM endpoint.

### 8. Profile & Settings (`features/settings/SettingsScreen.tsx`)
- **Sync Status**: Live display of connection state (`CONNECTED` / `OFFLINE`), last sync timestamp, and pending changes count.
- **Laptop Connection**: Configure server HTTPS address (e.g. `https://asta.local:8443`) and sign in using existing ASTA credentials.
- **Mock Sync Adapter**: One-tap toggle to simulate offline queue flush for testing without laptop network access.
- **Theme Selection**: Dark, Light, or System automatic mode.

---

## 3. Local SQLite Database Schema

The database is initialized in `src/database/db.ts` under the file `asta-mobile.db`:

```sql
-- Main Tasks Table
CREATE TABLE mobile_tasks (
  local_id TEXT PRIMARY KEY NOT NULL,
  server_id INTEGER UNIQUE,
  title TEXT NOT NULL,
  description TEXT,
  category TEXT NOT NULL DEFAULT 'other',
  priority TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'pending',
  is_completed INTEGER NOT NULL DEFAULT 0,
  due_date TEXT,
  due_time TEXT,
  timezone TEXT,
  tags_json TEXT DEFAULT '[]',
  subtasks_json TEXT DEFAULT '[]',
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  sync_state TEXT NOT NULL DEFAULT 'pending_sync',
  sync_error TEXT
);

-- Synchronization Mutation Queue
CREATE TABLE sync_queue (
  id TEXT PRIMARY KEY NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  operation TEXT NOT NULL, -- 'CREATE' | 'UPDATE' | 'DELETE'
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  last_attempt TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  error_message TEXT
);

-- Local Notes
CREATE TABLE mobile_notes (
  id TEXT PRIMARY KEY NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'general',
  is_pinned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Focus Session Logs
CREATE TABLE focus_sessions (
  id TEXT PRIMARY KEY NOT NULL,
  task_id TEXT,
  task_title TEXT,
  duration_minutes INTEGER NOT NULL,
  completed_at TEXT NOT NULL,
  session_type TEXT NOT NULL DEFAULT 'pomodoro'
);
```

---

## 4. Running & Building the Application

### Running with Expo Go
1. Start the Expo development server:
   ```powershell
   cd C:\Asta\mobile
   npm start
   ```
2. Scan the terminal QR code with the **Expo Go** app on your Android phone.

### Exporting the Standalone Android Hermes Bundle
The mobile application has been bundled and verified:
```powershell
cd C:\Asta\mobile
npm run export
```
This compiles 660 JavaScript and asset modules into the standalone Android Hermes bytecode file located at `dist/_expo/static/js/android/`.

### Generating the Installable Android APK

#### Method A: EAS Build (Recommended for Cloud Build)
With an Expo account, generate a standalone APK directly in the cloud:
```powershell
cd C:\Asta\mobile
npx eas-cli login
npm run build:apk
```
`eas.json` is already configured with `"buildType": "apk"` under the `preview` profile. EAS will compile the project and provide a direct download link and QR code to install the APK on any Android phone.

#### Method B: Local Gradle APK Build
If an Android SDK is installed on the machine:
```powershell
cd C:\Asta\mobile
npm run prebuild
cd android
.\gradlew.bat assembleRelease
```
The compiled release APK will be located at:
`C:\Asta\mobile\android\app\build\outputs\apk\release\app-release.apk`
