/**
 * Process-local microphone ownership flags shared by Stage 2, Stage 3, and
 * Stage 4. These flags only guard against overlapping UI workflows; backend
 * state remains authoritative for voice conversations.
 */

let voiceConversationActive = false;
let manualMicrophoneActive = false;

export function setVoiceConversationActive(active: boolean): void {
  voiceConversationActive = active;
}

export function isVoiceConversationActive(): boolean {
  return voiceConversationActive;
}

export function setManualMicrophoneActive(active: boolean): void {
  manualMicrophoneActive = active;
}

export function isManualMicrophoneActive(): boolean {
  return manualMicrophoneActive;
}
