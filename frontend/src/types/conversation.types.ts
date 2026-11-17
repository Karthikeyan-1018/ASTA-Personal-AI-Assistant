import type { ConversationMessage } from './chat.types';

export interface ConversationSummary {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  /**
   * Optional because the backend conversation list does not expose message
   * counts or previews. These values are only populated after the active
   * conversation's messages have been confirmed by the backend.
   */
  messageCount?: number;
  previewSnippet?: string;
  isPinned?: boolean;
  tags?: string[];
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
  /** True once this conversation's messages were confirmed by the backend. */
  messagesLoaded?: boolean;
  /** True when the most recent message request failed. */
  messagesUnavailable?: boolean;
  /** True while a message request is in flight for this conversation. */
  messagesLoading?: boolean;
  messagesError?: string | null;
}
