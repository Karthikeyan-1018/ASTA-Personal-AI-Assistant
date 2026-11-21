import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TextInput,
  Pressable,
  Alert,
  Switch,
} from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useTasks } from '../../hooks/useTasksContext';
import { useNavigation } from '../../navigation/NavigationContext';
import { syncService } from '../../sync/syncService';
import { apiClient, isValidBaseUrl, normalizeBaseUrl } from '../../api/apiClient';
import { Icon } from '../../components/common/Icon';
import { formatFriendlyDate } from '../../utils/dateUtils';

export const SettingsScreen: React.FC = () => {
  const { colors, borderRadius, spacing, mode, setMode } = useTheme();
  const { syncStatus, triggerSync, tasks } = useTasks();
  const { openModal } = useNavigation();

  const [serverUrlInput, setServerUrlInput] = useState(syncStatus.serverUrl || '');
  const [usernameInput, setUsernameInput] = useState(syncStatus.username || '');
  const [passwordInput, setPasswordInput] = useState('');
  const [isBusy, setIsBusy] = useState(false);
  const [mockModeEnabled, setMockModeEnabled] = useState(syncStatus.syncMode === 'mock');

  useEffect(() => {
    if (!serverUrlInput && syncStatus.serverUrl) setServerUrlInput(syncStatus.serverUrl);
    if (!usernameInput && syncStatus.username) setUsernameInput(syncStatus.username);
  }, [serverUrlInput, syncStatus.serverUrl, syncStatus.username, usernameInput]);

  const handleSaveUrl = async () => {
    const cleaned = normalizeBaseUrl(serverUrlInput);
    if (!cleaned) {
      await syncService.setServerCredentials('', syncStatus.isAuthenticated ? 'demo-token' : null, usernameInput);
      Alert.alert('Server address cleared');
      return;
    }
    if (!isValidBaseUrl(cleaned)) {
      Alert.alert(
        'Valid URL required',
        'Enter the laptop HTTPS address, e.g. https://192.168.1.100:8443. Remote HTTP is blocked to protect your sign-in token.'
      );
      return;
    }
    await syncService.setServerCredentials(cleaned, null, usernameInput);
    Alert.alert('Server saved', 'Laptop server URL saved.');
  };

  const handleSignIn = async () => {
    const cleanedUrl = normalizeBaseUrl(serverUrlInput);
    if (!cleanedUrl || !isValidBaseUrl(cleanedUrl)) {
      Alert.alert('URL required', 'Please set a valid laptop HTTPS URL first.');
      return;
    }
    if (!usernameInput.trim() || !passwordInput.trim()) {
      Alert.alert('Credentials required', 'Please enter username and password.');
      return;
    }

    setIsBusy(true);
    try {
      const res = await apiClient.login(cleanedUrl, usernameInput.trim(), passwordInput);
      await syncService.setServerCredentials(cleanedUrl, res.access_token, res.owner.username);
      setPasswordInput('');
      Alert.alert('Signed in', `Authenticated as ${res.owner.username}.`);
      await triggerSync();
    } catch (err: any) {
      Alert.alert('Sign in failed', err?.message || 'Could not connect to ASTA server.');
    } finally {
      setIsBusy(false);
    }
  };

  const handleSignOut = async () => {
    await syncService.setServerCredentials(serverUrlInput, null, null);
    Alert.alert('Signed out', 'Offline tasks remain preserved on this phone.');
  };

  const handleToggleMockSync = (value: boolean) => {
    setMockModeEnabled(value);
    syncService.setSyncMode(value ? 'mock' : 'live');
  };

  return (
    <ScrollView
      style={[styles.container, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: 110 }]}
      showsVerticalScrollIndicator={false}
    >
      {/* Header Profile Summary */}
      <View style={[styles.profileHeader, { paddingHorizontal: spacing.lg, paddingTop: spacing.md }]}>
        <View
          style={[
            styles.avatarCircle,
            { backgroundColor: colors.surfaceSubtle, borderColor: colors.border },
          ]}
        >
          <Text style={[styles.avatarText, { color: colors.accentLight }]}>
            {syncStatus.username ? syncStatus.username.charAt(0).toUpperCase() : 'A'}
          </Text>
        </View>
        <View style={styles.profileTextWrap}>
          <Text style={[styles.profileName, { color: colors.textPrimary }]}>
            {syncStatus.username || 'ASTA Mobile User'}
          </Text>
          <Text style={[styles.profileRole, { color: colors.textMuted }]}>
            Companion Device · Android
          </Text>
        </View>
      </View>

      {/* Synchronization Status Card */}
      <View style={{ paddingHorizontal: spacing.lg, marginTop: 14 }}>
        <Text style={[styles.sectionHeading, { color: colors.textSecondary }]}>
          LAPTOP SYNCHRONIZATION
        </Text>
        <View
          style={[
            styles.cardBox,
            {
              backgroundColor: colors.surface,
              borderColor: colors.cardBorder,
              borderRadius: borderRadius.lg,
            },
          ]}
        >
          {/* Connection status header */}
          <View style={styles.connStatusRow}>
            <View>
              <Text style={[styles.connTitle, { color: colors.textPrimary }]}>
                Laptop Connection
              </Text>
              <Text
                style={[
                  styles.connBadge,
                  {
                    color: syncStatus.isOnline
                      ? colors.success
                      : syncStatus.pendingCount > 0
                      ? colors.warning
                      : colors.textMuted,
                  },
                ]}
              >
                {syncStatus.isOnline
                  ? '● CONNECTED'
                  : syncStatus.syncMode === 'mock'
                  ? '● MOCK MODE (SIMULATED)'
                  : '○ OFFLINE (ON DEVICE)'}
              </Text>
            </View>

            <Pressable
              onPress={() => triggerSync()}
              disabled={syncStatus.isSyncing}
              style={[
                styles.syncNowBtn,
                { backgroundColor: colors.accent, borderRadius: borderRadius.sm },
              ]}
            >
              <Text style={styles.syncNowBtnText}>
                {syncStatus.isSyncing ? 'Syncing…' : '↻ Sync Now'}
              </Text>
            </Pressable>
          </View>

          <View style={styles.syncDetails}>
            <Text style={[styles.syncDetailText, { color: colors.textSecondary }]}>
              Last successful sync:{' '}
              <Text style={{ color: colors.textPrimary, fontWeight: '700' }}>
                {syncStatus.lastSyncTimestamp
                  ? new Date(syncStatus.lastSyncTimestamp).toLocaleTimeString()
                  : 'Never'}
              </Text>
            </Text>

            <Text style={[styles.syncDetailText, { color: colors.textSecondary }]}>
              Pending changes in queue:{' '}
              <Text
                style={{
                  color: syncStatus.pendingCount > 0 ? colors.warning : colors.success,
                  fontWeight: '700',
                }}
              >
                {syncStatus.pendingCount}
              </Text>
            </Text>

            <Text style={[styles.statusMsgText, { color: colors.textMuted }]}>
              {syncStatus.statusMessage}
            </Text>
          </View>

          {/* Simulated Mock Sync Switch for Testing */}
          <View style={[styles.mockToggleRow, { borderTopColor: colors.border }]}>
            <View style={{ flex: 1, paddingRight: 8 }}>
              <Text style={[styles.mockToggleTitle, { color: colors.textPrimary }]}>
                Mock Sync Adapter
              </Text>
              <Text style={[styles.mockToggleSub, { color: colors.textMuted }]}>
                Simulate offline queue flush for UI testing when laptop is turned off
              </Text>
            </View>
            <Switch
              value={mockModeEnabled}
              onValueChange={handleToggleMockSync}
              trackColor={{ false: colors.surfaceSubtle, true: colors.accent }}
              thumbColor="#FFFFFF"
            />
          </View>
        </View>
      </View>

      {/* Server Config & Auth */}
      <View style={{ paddingHorizontal: spacing.lg, marginTop: 20 }}>
        <Text style={[styles.sectionHeading, { color: colors.textSecondary }]}>
          LAPTOP BACKEND CONFIGURATION
        </Text>
        <View
          style={[
            styles.cardBox,
            {
              backgroundColor: colors.surface,
              borderColor: colors.cardBorder,
              borderRadius: borderRadius.lg,
            },
          ]}
        >
          <Text style={[styles.inputLabel, { color: colors.textSecondary }]}>SERVER HTTPS URL</Text>
          <TextInput
            value={serverUrlInput}
            onChangeText={setServerUrlInput}
            placeholder="https://192.168.1.5:8443"
            placeholderTextColor={colors.textMuted}
            autoCapitalize="none"
            keyboardType="url"
            style={[
              styles.inputField,
              {
                backgroundColor: colors.inputBackground,
                borderColor: colors.inputBorder,
                color: colors.textPrimary,
                borderRadius: borderRadius.sm,
              },
            ]}
          />
          <Pressable
            onPress={handleSaveUrl}
            style={[
              styles.saveUrlBtn,
              { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.sm },
            ]}
          >
            <Text style={[styles.saveUrlBtnText, { color: colors.textPrimary }]}>
              Save Server Address
            </Text>
          </Pressable>

          {syncStatus.isAuthenticated ? (
            <View style={[styles.authStatusBox, { borderTopColor: colors.border }]}>
              <Text style={[styles.authInfo, { color: colors.textPrimary }]}>
                ✓ Signed in as <Text style={{ fontWeight: '800' }}>{syncStatus.username}</Text>
              </Text>
              <Pressable onPress={handleSignOut} style={styles.signOutBtn}>
                <Text style={[styles.signOutBtnText, { color: colors.danger }]}>Sign Out</Text>
              </Pressable>
            </View>
          ) : (
            <View style={[styles.loginBox, { borderTopColor: colors.border }]}>
              <Text style={[styles.inputLabel, { color: colors.textSecondary }]}>
                ASTA ACCOUNT LOGIN
              </Text>
              <TextInput
                value={usernameInput}
                onChangeText={setUsernameInput}
                placeholder="Username"
                placeholderTextColor={colors.textMuted}
                autoCapitalize="none"
                style={[
                  styles.inputField,
                  {
                    backgroundColor: colors.inputBackground,
                    borderColor: colors.inputBorder,
                    color: colors.textPrimary,
                    borderRadius: borderRadius.sm,
                  },
                ]}
              />
              <TextInput
                value={passwordInput}
                onChangeText={setPasswordInput}
                placeholder="Password"
                placeholderTextColor={colors.textMuted}
                secureTextEntry
                style={[
                  styles.inputField,
                  {
                    backgroundColor: colors.inputBackground,
                    borderColor: colors.inputBorder,
                    color: colors.textPrimary,
                    borderRadius: borderRadius.sm,
                  },
                ]}
              />
              <Pressable
                onPress={handleSignIn}
                disabled={isBusy}
                style={[
                  styles.signInBtn,
                  { backgroundColor: colors.accent, borderRadius: borderRadius.sm },
                ]}
              >
                <Text style={styles.signInBtnText}>
                  {isBusy ? 'Authenticating…' : 'Sign In and Connect'}
                </Text>
              </Pressable>
            </View>
          )}
        </View>
      </View>

      {/* App Appearance & Theme Selection */}
      <View style={{ paddingHorizontal: spacing.lg, marginTop: 20 }}>
        <Text style={[styles.sectionHeading, { color: colors.textSecondary }]}>APPEARANCE</Text>
        <View
          style={[
            styles.cardBox,
            {
              backgroundColor: colors.surface,
              borderColor: colors.cardBorder,
              borderRadius: borderRadius.lg,
            },
          ]}
        >
          <View style={styles.themeRow}>
            {(['dark', 'light', 'system'] as const).map((t) => {
              const isSelected = mode === t;
              return (
                <Pressable
                  key={t}
                  onPress={() => setMode(t)}
                  style={[
                    styles.themeOption,
                    {
                      backgroundColor: isSelected ? colors.accent : colors.surfaceSubtle,
                      borderRadius: borderRadius.sm,
                    },
                  ]}
                >
                  <Text
                    style={[
                      styles.themeOptionText,
                      { color: isSelected ? '#FFFFFF' : colors.textSecondary },
                    ]}
                  >
                    {t === 'dark' ? '☾ Dark' : t === 'light' ? '☼ Light' : '⚙ System'}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </View>
      </View>

      {/* Quick Nav Sub-screens */}
      <View style={{ paddingHorizontal: spacing.lg, marginTop: 20 }}>
        <Text style={[styles.sectionHeading, { color: colors.textSecondary }]}>FEATURES</Text>
        <View
          style={[
            styles.cardBox,
            {
              backgroundColor: colors.surface,
              borderColor: colors.cardBorder,
              borderRadius: borderRadius.lg,
            },
          ]}
        >
          <Pressable
            onPress={() => openModal({ type: 'notes' })}
            style={[styles.featureLink, { borderBottomColor: colors.border }]}
          >
            <View style={styles.featureLinkLeft}>
              <Icon name="notes" size={16} color={colors.accentLight} />
              <Text style={[styles.featureLinkTitle, { color: colors.textPrimary }]}>
                Notes & Scratchpad
              </Text>
            </View>
            <Text style={[styles.chevron, { color: colors.textMuted }]}>›</Text>
          </Pressable>

          <Pressable
            onPress={() => openModal({ type: 'focus_mode' })}
            style={[styles.featureLink, { borderBottomColor: colors.border }]}
          >
            <View style={styles.featureLinkLeft}>
              <Icon name="clock" size={16} color={colors.accentLight} />
              <Text style={[styles.featureLinkTitle, { color: colors.textPrimary }]}>
                Focus Timer Mode
              </Text>
            </View>
            <Text style={[styles.chevron, { color: colors.textMuted }]}>›</Text>
          </Pressable>

          <Pressable
            onPress={() => openModal({ type: 'analytics' })}
            style={[styles.featureLink, { borderBottomColor: colors.border }]}
          >
            <View style={styles.featureLinkLeft}>
              <Icon name="analytics" size={16} color={colors.accentLight} />
              <Text style={[styles.featureLinkTitle, { color: colors.textPrimary }]}>
                Productivity Analytics
              </Text>
            </View>
            <Text style={[styles.chevron, { color: colors.textMuted }]}>›</Text>
          </Pressable>

          <Pressable
            onPress={() => openModal({ type: 'reminders' })}
            style={styles.featureLink}
          >
            <View style={styles.featureLinkLeft}>
              <Icon name="bell" size={16} color={colors.accentLight} />
              <Text style={[styles.featureLinkTitle, { color: colors.textPrimary }]}>
                Reminders & Notification Settings
              </Text>
            </View>
            <Text style={[styles.chevron, { color: colors.textMuted }]}>›</Text>
          </Pressable>
        </View>
      </View>

      {/* Storage Information & About ASTA */}
      <View style={{ paddingHorizontal: spacing.lg, marginTop: 20 }}>
        <Text style={[styles.sectionHeading, { color: colors.textSecondary }]}>DEVICE STORAGE</Text>
        <View
          style={[
            styles.cardBox,
            {
              backgroundColor: colors.surface,
              borderColor: colors.cardBorder,
              borderRadius: borderRadius.lg,
              padding: 16,
            },
          ]}
        >
          <Text style={[styles.storageText, { color: colors.textSecondary }]}>
            Engine: Expo SQLite WAL database
          </Text>
          <Text style={[styles.storageText, { color: colors.textSecondary }]}>
            Local tasks stored: {tasks.length}
          </Text>
          <Text style={[styles.storageText, { color: colors.textSecondary }]}>
            Tokens: Encrypted with Android SecureStore
          </Text>
        </View>

        <View style={styles.aboutBox}>
          <Text style={[styles.aboutBrand, { color: colors.textPrimary }]}>ASTA Mobile 1.0.0</Text>
          <Text style={[styles.aboutSub, { color: colors.textMuted }]}>
            Android companion for your personal ASTA AI system.
          </Text>
        </View>
      </View>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingTop: 4,
  },
  profileHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    marginBottom: 10,
  },
  avatarCircle: {
    width: 52,
    height: 52,
    borderRadius: 26,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1.5,
  },
  avatarText: {
    fontSize: 22,
    fontWeight: '900',
  },
  profileTextWrap: {
    flex: 1,
  },
  profileName: {
    fontSize: 18,
    fontWeight: '800',
  },
  profileRole: {
    fontSize: 12,
    marginTop: 2,
  },
  sectionHeading: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1.2,
    marginBottom: 8,
  },
  cardBox: {
    borderWidth: 1,
    overflow: 'hidden',
  },
  connStatusRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
  },
  connTitle: {
    fontSize: 14,
    fontWeight: '800',
  },
  connBadge: {
    fontSize: 11,
    fontWeight: '800',
    marginTop: 4,
    letterSpacing: 0.5,
  },
  syncNowBtn: {
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  syncNowBtnText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '800',
  },
  syncDetails: {
    paddingHorizontal: 16,
    paddingBottom: 14,
    gap: 4,
  },
  syncDetailText: {
    fontSize: 12,
  },
  statusMsgText: {
    fontSize: 11,
    marginTop: 6,
    fontStyle: 'italic',
  },
  mockToggleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 14,
    borderTopWidth: 1,
  },
  mockToggleTitle: {
    fontSize: 13,
    fontWeight: '700',
  },
  mockToggleSub: {
    fontSize: 11,
    marginTop: 2,
  },
  inputLabel: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1,
    marginHorizontal: 16,
    marginTop: 14,
    marginBottom: 6,
  },
  inputField: {
    marginHorizontal: 16,
    marginBottom: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 13,
    borderWidth: 1,
  },
  saveUrlBtn: {
    marginHorizontal: 16,
    marginBottom: 14,
    paddingVertical: 10,
    alignItems: 'center',
  },
  saveUrlBtnText: {
    fontSize: 12,
    fontWeight: '700',
  },
  authStatusBox: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    borderTopWidth: 1,
  },
  authInfo: {
    fontSize: 13,
  },
  signOutBtn: {
    padding: 6,
  },
  signOutBtnText: {
    fontSize: 12,
    fontWeight: '700',
  },
  loginBox: {
    borderTopWidth: 1,
    paddingBottom: 14,
  },
  signInBtn: {
    marginHorizontal: 16,
    marginTop: 6,
    paddingVertical: 12,
    alignItems: 'center',
  },
  signInBtnText: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '800',
  },
  themeRow: {
    flexDirection: 'row',
    padding: 12,
    gap: 8,
  },
  themeOption: {
    flex: 1,
    paddingVertical: 10,
    alignItems: 'center',
  },
  themeOptionText: {
    fontSize: 12,
    fontWeight: '700',
  },
  featureLink: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 14,
    borderBottomWidth: 1,
  },
  featureLinkLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  featureLinkTitle: {
    fontSize: 13,
    fontWeight: '600',
  },
  chevron: {
    fontSize: 18,
    fontWeight: '700',
  },
  storageText: {
    fontSize: 12,
    lineHeight: 18,
  },
  aboutBox: {
    alignItems: 'center',
    paddingVertical: 24,
  },
  aboutBrand: {
    fontSize: 14,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  aboutSub: {
    fontSize: 11,
    marginTop: 4,
  },
});
