import { create } from 'zustand';
import chatSSEClient from '../services/streaming/sse-client';
import conversationsApi from '../services/api/conversations.api';
import type { ConversationMessage } from '../types/chat.types';
import type { ConversationDetail } from '../types/conversation.types';
import type { SSEDoneData, SSEErrorData, SSEStartData } from '../types/api.types';

const INITIAL_CONVERSATIONS: ConversationDetail[] = [
  {
    id: 'conv-1',
    title: 'Workspace Initial Setup & Briefing',
    createdAt: '2026-11-14T09:00:00Z',
    updatedAt: '2026-11-14T09:05:00Z',
    messageCount: 2,
    previewSnippet: 'Welcome to ASTA Desktop. Core workspace modules are online...',
    isPinned: true,
    messages: [
      {
        id: 'msg-1',
        role: 'assistant',
        content:
          'Welcome to ASTA Desktop. Core workspace modules are initialized and operational. Real-time SSE streaming pipeline is connected to `/api/chat/stream`. How can I assist you today?',
        timestamp: '09:00',
        model: 'ASTA-Core v1',
        provider: 'backend',
      },
    ],
  },
];

interface ChatStoreState {
  conversations: ConversationDetail[];
  activeConversationId: string;
  isStreaming: boolean;
  streamingMessageId: string | null;
  activeModel: string;
  error: string | null;
  loadConversations: () => Promise<void>;
  setActiveModel: (model: string) => void;
  setActiveConversationId: (id: string) => void;
  createNewConversation: () => Promise<string>;
  deleteConversation: (id: string) => Promise<void>;
  renameConversation: (id: string, newTitle: string) => Promise<void>;
  sendMessage: (content: string, isPrivate?: boolean) => Promise<void>;
  stopGeneration: () => void;
  regenerateLastMessage: () => Promise<void>;
  clearActiveConversation: () => void;
}

export const useChatStore = create<ChatStoreState>((set, get) => {
  return {
    conversations: INITIAL_CONVERSATIONS,
    activeConversationId: 'conv-1',
    isStreaming: false,
    streamingMessageId: null,
    activeModel: 'ASTA-Core v1 (Hybrid)',
    error: null,

    setActiveModel: (model) => set({ activeModel: model }),

    setActiveConversationId: (id) => {
      if (get().isStreaming) {
        get().stopGeneration();
      }
      set({ activeConversationId: id, error: null });
    },

    loadConversations: async () => {
      try {
        const backendConvs = await conversationsApi.list();
        if (backendConvs && backendConvs.length > 0) {
          const mapped: ConversationDetail[] = await Promise.all(
            backendConvs.map(async (c) => {
              let messages: ConversationMessage[] = [];
              try {
                const msgRes = await conversationsApi.listMessages(c.id, 50, 0);
                messages = msgRes.messages.map((m) => ({
                  id: String(m.id),
                  role: m.role as any,
                  content: m.content,
                  timestamp: new Date(m.created_at).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                  }),
                  model: m.model || undefined,
                  provider: m.provider || undefined,
                  isPrivate: m.is_private,
                }));
              } catch {
                // Keep empty if message load fails
              }

              const lastMsg = messages[messages.length - 1];
              return {
                id: String(c.id),
                title: c.title,
                createdAt: c.created_at,
                updatedAt: c.updated_at,
                messageCount: messages.length,
                previewSnippet: lastMsg ? lastMsg.content.slice(0, 60) : 'Empty thread',
                messages,
              };
            })
          );

          set({
            conversations: mapped,
            activeConversationId: mapped[0].id,
          });
        }
      } catch {
        // Fallback to local default conversations if backend is not yet populated
      }
    },

    createNewConversation: async () => {
      if (get().isStreaming) {
        get().stopGeneration();
      }

      let backendId: string | null = null;
      try {
        const created = await conversationsApi.create({ title: 'New Conversation' });
        backendId = String(created.id);
      } catch {
        // Fallback to local ID
        backendId = `conv-${Date.now()}`;
      }

      const newConv: ConversationDetail = {
        id: backendId,
        title: 'New Conversation',
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        messageCount: 1,
        previewSnippet: 'How can ASTA assist you today?',
        messages: [
          {
            id: `msg-${Date.now()}`,
            role: 'assistant',
            content: 'Hello! I am ASTA, your personal AI assistant. How can I help you today?',
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            model: get().activeModel,
          },
        ],
      };

      set((state) => ({
        conversations: [newConv, ...state.conversations],
        activeConversationId: backendId,
      }));

      return backendId;
    },

    deleteConversation: async (id) => {
      const numId = Number(id);
      if (!isNaN(numId) && numId > 0) {
        try {
          await conversationsApi.delete(numId);
        } catch {
          // Handled
        }
      }

      const remaining = get().conversations.filter((c) => c.id !== id);
      if (remaining.length === 0) {
        const freshId = `conv-${Date.now()}`;
        set({
          conversations: [
            {
              id: freshId,
              title: 'New Conversation',
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              messageCount: 0,
              previewSnippet: 'Empty conversation',
              messages: [],
            },
          ],
          activeConversationId: freshId,
        });
        return;
      }

      set({
        conversations: remaining,
        activeConversationId:
          get().activeConversationId === id ? remaining[0].id : get().activeConversationId,
      });
    },

    renameConversation: async (id, newTitle) => {
      const trimmed = newTitle.trim() || 'Untitled Session';
      const numId = Number(id);
      if (!isNaN(numId) && numId > 0) {
        try {
          await conversationsApi.updateTitle(numId, trimmed);
        } catch {
          // Handled
        }
      }

      set((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === id ? { ...c, title: trimmed } : c
        ),
      }));
    },

    clearActiveConversation: () => {
      chatSSEClient.cancel();
      const activeId = get().activeConversationId;
      set((state) => ({
        isStreaming: false,
        streamingMessageId: null,
        conversations: state.conversations.map((c) =>
          c.id === activeId
            ? { ...c, messages: [], messageCount: 0, previewSnippet: 'Cleared' }
            : c
        ),
      }));
    },

    stopGeneration: () => {
      chatSSEClient.cancel();
      set({ isStreaming: false, streamingMessageId: null });
    },

    sendMessage: async (content: string, isPrivate = false) => {
      const trimmed = content.trim();
      if (!trimmed || get().isStreaming) return;

      const activeId = get().activeConversationId;
      const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const userMsgId = `user-${Date.now()}`;
      const assistantMsgId = `assistant-${Date.now() + 1}`;

      const userMessage: ConversationMessage = {
        id: userMsgId,
        role: 'user',
        content: trimmed,
        timestamp: now,
      };

      const pendingAssistantMessage: ConversationMessage = {
        id: assistantMsgId,
        role: 'assistant',
        content: '',
        timestamp: now,
        model: get().activeModel,
      };

      const conv = get().conversations.find((c) => c.id === activeId);
      const isFirstUserMessage = conv?.messages.filter((m) => m.role === 'user').length === 0;
      const derivedTitle = isFirstUserMessage
        ? trimmed.length > 28
          ? trimmed.slice(0, 28) + '...'
          : trimmed
        : conv?.title || 'Conversation';

      // Update UI state with user message and pending assistant message
      set((state) => ({
        isStreaming: true,
        streamingMessageId: assistantMsgId,
        error: null,
        conversations: state.conversations.map((c) =>
          c.id === activeId
            ? {
                ...c,
                title: derivedTitle,
                updatedAt: new Date().toISOString(),
                previewSnippet: trimmed,
                messages: [...c.messages, userMessage, pendingAssistantMessage],
                messageCount: c.messages.length + 2,
              }
            : c
        ),
      }));

      const numConvId = Number(activeId);
      const backendConversationId = !isNaN(numConvId) && numConvId > 0 ? numConvId : null;

      let hasReceivedTokens = false;

      await chatSSEClient.streamChat(
        {
          message: trimmed,
          private: isPrivate,
          conversation_id: backendConversationId,
        },
        {
          onStart: (data: SSEStartData) => {
            if (data.conversation_id && String(data.conversation_id) !== activeId) {
              const newIdStr = String(data.conversation_id);
              set((state) => ({
                activeConversationId: newIdStr,
                conversations: state.conversations.map((c) =>
                  c.id === activeId ? { ...c, id: newIdStr } : c
                ),
              }));
            }
            if (data.model) {
              set({ activeModel: data.model });
            }
          },

          onDelta: (textChunk: string) => {
            hasReceivedTokens = true;
            set((state) => ({
              conversations: state.conversations.map((c) => {
                if (c.id !== get().activeConversationId && c.id !== activeId) return c;
                return {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === assistantMsgId ? { ...m, content: m.content + textChunk } : m
                  ),
                };
              }),
            }));
          },

          onDone: (data: SSEDoneData) => {
            set((state) => ({
              isStreaming: false,
              streamingMessageId: null,
              conversations: state.conversations.map((c) => {
                if (c.id !== get().activeConversationId && c.id !== activeId) return c;
                return {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === assistantMsgId
                      ? {
                          ...m,
                          provider: data.provider,
                          model: data.model || m.model,
                        }
                      : m
                  ),
                };
              }),
            }));
          },

          onError: async (errorData: SSEErrorData) => {
            set({ isStreaming: false, streamingMessageId: null, error: errorData.message });

            // If no tokens were streamed (e.g. backend offline or unauthenticated),
            // provide a graceful fallback message so the UI is informative and helpful
            if (!hasReceivedTokens) {
              const isUnauthorized = errorData.code === 'http_401';

              const fallbackNotice = isUnauthorized
                ? `🔒 **Session expired**\n\n` +
                  `*Your ASTA session is no longer valid. You have been returned to the sign-in screen — please sign in again.*`
                : `⚠️ **Backend notice:** ${errorData.message}\n\n` +
                  `*The request to \`/api/chat/stream\` could not complete (${errorData.code}). ` +
                  `Ensure FastAPI is running at \`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}\` ` +
                  `and your session is authenticated.*`;

              set((state) => ({
                conversations: state.conversations.map((c) => {
                  if (c.id !== get().activeConversationId && c.id !== activeId) return c;
                  return {
                    ...c,
                    messages: c.messages.map((m) =>
                      m.id === assistantMsgId ? { ...m, content: fallbackNotice } : m
                    ),
                  };
                }),
              }));
            }
          },
        }
      );
    },

    regenerateLastMessage: async () => {
      if (get().isStreaming) return;
      const activeId = get().activeConversationId;
      const conv = get().conversations.find((c) => c.id === activeId);
      if (!conv || conv.messages.length < 2) return;

      const lastUserMsg = [...conv.messages].reverse().find((m) => m.role === 'user');
      if (!lastUserMsg) return;

      set((state) => ({
        conversations: state.conversations.map((c) => {
          if (c.id !== activeId) return c;
          const messages = c.messages.filter(
            (_, idx) => idx !== c.messages.length - 1 || c.messages[idx].role === 'user'
          );
          return { ...c, messages };
        }),
      }));

      await get().sendMessage(lastUserMsg.content);
    },
  };
});

export default useChatStore;
