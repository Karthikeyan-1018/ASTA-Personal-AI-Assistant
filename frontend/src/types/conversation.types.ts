import type { ConversationMessage } from './chat.types';

export interface ConversationSummary {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messageCount: number;
  previewSnippet: string;
  isPinned?: boolean;
  tags?: string[];
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}
