import { ChatMessage } from '../types/assistant';
import { LocalTask } from '../types/task';

export interface AssistantResponse {
  message: string;
  suggestedPrompts?: string[];
  actionableTaskId?: string;
}

class AssistantService {
  private isServerConnected = false;

  setServerConnected(connected: boolean) {
    this.isServerConnected = connected;
  }

  /**
   * Generates a preview response based on current local tasks context.
   * Clearly marked as local companion preview until ASTA backend is linked.
   */
  async sendMessage(query: string, currentTasks: LocalTask[]): Promise<AssistantResponse> {
    // Simulate brief processing delay
    await new Promise((resolve) => setTimeout(resolve, 800));

    const q = query.toLowerCase();
    const today = new Date().toISOString().slice(0, 10);
    const pendingTasks = currentTasks.filter((t) => !t.is_completed);
    const todayTasks = pendingTasks.filter((t) => t.due_date === today);
    const urgentTasks = pendingTasks.filter((t) => t.priority === 'urgent' || t.priority === 'high');

    if (q.includes('today') || q.includes('schedule')) {
      if (todayTasks.length === 0) {
        return {
          message: `### ✦ Today's Overview\n\nYou have **no tasks due today**. You have **${pendingTasks.length} pending tasks** overall.\n\n*Would you like to schedule a deep focus session or create a new assignment?*`,
          suggestedPrompts: ['Create a task for tomorrow', 'Start 25m Focus Session', 'Review all pending tasks'],
        };
      }
      const list = todayTasks.map((t) => `• **${t.title}** [${t.priority.toUpperCase()}]`).join('\n');
      return {
        message: `### ✦ Tasks for Today (${todayTasks.length})\n\nHere is your agenda for today:\n${list}\n\n💡 *Tip: Tackle the highest priority task first!*`,
        suggestedPrompts: ['Start Pomodoro timer', 'Mark first task complete', 'Summarize pending work'],
      };
    }

    if (q.includes('urgent') || q.includes('important') || q.includes('priority')) {
      if (urgentTasks.length === 0) {
        return {
          message: `### ✦ Priority Check\n\nGreat news! You have **no urgent or high priority tasks** right now. Your workload is balanced.`,
          suggestedPrompts: ['Show all pending tasks', 'Plan study schedule', 'Open Focus mode'],
        };
      }
      const list = urgentTasks.map((t) => `• **${t.title}** (${t.category}) · *Due: ${t.due_date || 'No date'}*`).join('\n');
      return {
        message: `### ⚠️ Important Deadlines (${urgentTasks.length})\n\nThese require your immediate focus:\n${list}\n\n*Stay focused! Knock out one at a time.*`,
        suggestedPrompts: ['Start 50m Deep Work', 'Reschedule upcoming task', 'View calendar agenda'],
      };
    }

    if (q.includes('focus') || q.includes('pomodoro') || q.includes('study')) {
      return {
        message: `### 🎯 Focus Recommendation\n\nBased on your tasks, I recommend a **50-minute Deep Work session**.\n\nRecommended target:\n> **${urgentTasks[0]?.title || pendingTasks[0]?.title || 'Reviewing Notes'}**\n\n*Head to the Focus tab to launch your timer!*`,
        suggestedPrompts: ['Open Focus Mode', 'View Focus History', 'Show today tasks'],
      };
    }

    // Default intelligent assistant response
    return {
      message: `### ✦ ASTA Companion\n\nI am keeping your local tasks, schedules, and notes synchronized on this device.\n\n• **Total local tasks:** ${currentTasks.length}\n• **Pending:** ${pendingTasks.length}\n• **Completed:** ${currentTasks.length - pendingTasks.length}\n\n*(Note: This is an on-device companion preview. Once linked to your laptop's ASTA server, full conversational LLM capabilities will stream directly.)*`,
      suggestedPrompts: ['What tasks are due today?', 'Show urgent priorities', 'Help me plan a focus session'],
    };
  }
}

export const assistantService = new AssistantService();
