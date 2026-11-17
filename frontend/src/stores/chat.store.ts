import { create } from 'zustand';
import chatSSEClient from '../services/streaming/sse-client';
import conversationsApi from '../services/api/conversations.api';
import type { ChatRole, ConversationMessage } from '../types/chat.types';
import type { ConversationDetail } from '../types/conversation.types';
import type {
  ConversationResponse,
  MessageResponse,
  SSEDoneData,
  SSEErrorData,
  SSEStartData,
} from '../types/api.types';

export type ConversationListStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface SendMessageResult {
  ok: boolean;
  conversationId: string | null;
  error: string | null;
}

interface ChatStoreState {
  conversations: ConversationDetail[];
  activeConversationId: string;
  conversationsStatus: ConversationListStatus;
  conversationsError: string | null;
  conversationsLoaded: boolean;
  isStreaming: boolean;
  isSending: boolean;
  streamingMessageId: string | null;
  activeModel: string;
  streamError: string | null;
  creatingConversation: boolean;
  pendingConversationIds: string[];
  loadConversations: () => Promise<void>;
  loadMessages: (id: string, force?: boolean) => Promise<void>;
  retryActiveConversation: () => Promise<void>;
  ensureConversation: () => Promise<string | null>;
  setActiveModel: (model: string) => void;
  setActiveConversationId: (id: string) => void;
  createNewConversation: () => Promise<string | null>;
  startNewConversationWithMessage: (content: string, isPrivate?: boolean) => Promise<SendMessageResult>;
  deleteConversation: (id: string) => Promise<boolean>;
  renameConversation: (id: string, newTitle: string) => Promise<boolean>;
  sendMessage: (content: string, isPrivate?: boolean) => Promise<SendMessageResult>;
  stopGeneration: () => void;
  clearConversationsError: () => void;
  clearStreamError: () => void;
}

// Conversation creation is deduplicated across concurrent senders so a fresh
// account can never create two backend conversations for one user action.
let creationPromise: Promise<string | null> | null = null;

// Only one send/stream may own the UI at a time. A monotonically increasing
// attempt id lets late SSE callbacks detect that they are stale.
let sendAttempt = 0;
let abortedSendAttempt = -1;

let localMessageCounter = 0;

function nextLocalMessageId(prefix: string): string {
  localMessageCounter += 1;
  return `${prefix}-${Date.now()}-${localMessageCounter}`;
}

function toBackendId(value: string): number | null {
  if (!/^[1-9]\d*$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function readableError(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    const message = error.message.trim();
    if (message) return message;
  }
  if (typeof error === 'string' && error.trim()) return error.trim();
  return fallback;
}

function formatClock(value?: string): string {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function mapBackendMessage(message: MessageResponse): ConversationMessage {
  return {
    id: String(message.id),
    role: message.role as ChatRole,
    content: message.content,
    timestamp: formatClock(message.created_at),
    provider: message.provider || undefined,
    model: message.model || undefined,
    isPrivate: message.is_private,
    status: 'complete',
  };
}

function conversationFromResponse(
  conversation: ConversationResponse,
  messagesLoaded = false
): ConversationDetail {
  return {
    id: String(conversation.id),
    title: conversation.title,
    createdAt: conversation.created_at,
    updatedAt: conversation.updated_at,
    messages: [],
    messageCount: messagesLoaded ? 0 : undefined,
    previewSnippet: undefined,
    messagesLoaded,
    messagesUnavailable: false,
    messagesLoading: false,
    messagesError: null,
  };
}

function updateMessage(
  conversations: ConversationDetail[],
  messageId: string,
  updater: (message: ConversationMessage) => ConversationMessage
): ConversationDetail[] {
  return conversations.map((conversation) => {
    if (!conversation.messages.some((message) => message.id === messageId)) {
      return conversation;
    }
    return {
      ...conversation,
      messages: conversation.messages.map((message) =>
        message.id === messageId ? updater(message) : message
      ),
    };
  });
}

function findMessage(
  conversations: ConversationDetail[],
  messageId: string
): ConversationMessage | undefined {
  for (const conversation of conversations) {
    const match = conversation.messages.find((message) => message.id === messageId);
    if (match) return match;
  }
  return undefined;
}

function mergeMessages(
  primary: ConversationMessage[],
  secondary: ConversationMessage[]
): ConversationMessage[] {
  const seen = new Set(primary.map((message) => message.id));
  return [...primary, ...secondary.filter((message) => !seen.has(message.id))];
}

export const useChatStore = create<ChatStoreState>((set, get) => {
  const performCreateConversation = async (): Promise<string | null> => {
    set({ creatingConversation: true, conversationsError: null });

    try {
      const created = await conversationsApi.create({ title: 'New Conversation' });

      if (!created || typeof created.id !== 'number') {
        throw new Error('The backend did not return a valid conversation id.');
      }

      const id = String(created.id);
      // The backend guarantees a freshly created conversation has no messages,
      // so an empty confirmed message list is accurate here.
      const entry = conversationFromResponse(created, true);

      set((state) => ({
        conversations: [entry, ...state.conversations.filter((c) => c.id !== id)],
        activeConversationId: id,
      }));

      return id;
    } catch (error) {
      set({
        conversationsError: readableError(error, 'The conversation could not be created.'),
      });
      return null;
    } finally {
      set({ creatingConversation: false });
      creationPromise = null;
    }
  };

  return {
    conversations: [],
    activeConversationId: '',
    conversationsStatus: 'idle',
    conversationsError: null,
    conversationsLoaded: false,
    isStreaming: false,
    isSending: false,
    streamingMessageId: null,
    activeModel: '',
    streamError: null,
    creatingConversation: false,
    pendingConversationIds: [],

    setActiveModel: (model) => set({ activeModel: model }),

    setActiveConversationId: (id) => {
      if (!get().conversations.some((conversation) => conversation.id === id)) {
        return;
      }
      if (get().isStreaming) {
        get().stopGeneration();
      }
      set({ activeConversationId: id, streamError: null });
    },

    loadConversations: async () => {
      if (get().conversationsStatus === 'loading') return;

      set({ conversationsStatus: 'loading', conversationsError: null });

      try {
        const backendConversations = await conversationsApi.list();
        const previous = new Map(
          get().conversations.map((conversation) => [conversation.id, conversation])
        );

        const mapped = backendConversations.map((conversation) => {
          const id = String(conversation.id);
          const cached = previous.get(id);

          if (!cached) return conversationFromResponse(conversation);

          // Refresh metadata from the backend without dropping messages that
          // were already confirmed for this conversation.
          return {
            ...cached,
            title: conversation.title,
            createdAt: conversation.created_at,
            updatedAt: conversation.updated_at,
          };
        });

        set((state) => ({
          conversations: mapped,
          activeConversationId: mapped.some(
            (conversation) => conversation.id === state.activeConversationId
          )
            ? state.activeConversationId
            : mapped[0]?.id ?? '',
          conversationsStatus: 'ready',
          conversationsLoaded: true,
          conversationsError: null,
        }));
      } catch (error) {
        // Preserve the last confirmed list. The UI shows the error and Retry.
        set({
          conversationsStatus: 'error',
          conversationsError: readableError(
            error,
            'Conversations could not be loaded.'
          ),
        });
      }
    },

    loadMessages: async (id, force = false) => {
      const backendId = toBackendId(id);
      if (backendId === null) return;

      const conversation = get().conversations.find((c) => c.id === id);
      if (!conversation) return;
      if (conversation.messagesLoading) return;
      if (!force && conversation.messagesLoaded) return;

      set((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === id
            ? { ...c, messagesLoading: true, messagesError: null }
            : c
        ),
      }));

      try {
        const response = await conversationsApi.listMessages(backendId, 50, 0);
        const messages = response.messages.map(mapBackendMessage);
        const lastMessage = messages[messages.length - 1];

        set((state) => ({
          conversations: state.conversations.map((c) =>
            c.id === id
              ? {
                  ...c,
                  messages,
                  messagesLoaded: true,
                  messagesLoading: false,
                  messagesUnavailable: false,
                  messagesError: null,
                  messageCount:
                    typeof response.total === 'number' ? response.total : messages.length,
                  previewSnippet: lastMessage
                    ? lastMessage.content.slice(0, 60)
                    : undefined,
                }
              : c
          ),
        }));
      } catch (error) {
        // Keep any previously confirmed messages on screen and surface the
        // failure instead of pretending the conversation is empty.
        set((state) => ({
          conversations: state.conversations.map((c) =>
            c.id === id
              ? {
                  ...c,
                  messagesLoading: false,
                  messagesUnavailable: true,
                  messagesError: readableError(
                    error,
                    'Messages could not be loaded.'
                  ),
                }
              : c
          ),
        }));
      }
    },

    retryActiveConversation: async () => {
      const id = get().activeConversationId;
      if (!id || get().isStreaming || get().isSending) return;
      set({ streamError: null });
      await get().loadMessages(id, true);
    },

    createNewConversation: async () => {
      if (get().isStreaming) {
        get().stopGeneration();
      }
      if (creationPromise) {
        return creationPromise;
      }
      creationPromise = performCreateConversation();
      return creationPromise;
    },

    ensureConversation: async () => {
      const { activeConversationId, conversations } = get();

      if (
        toBackendId(activeConversationId) !== null &&
        conversations.some((conversation) => conversation.id === activeConversationId)
      ) {
        return activeConversationId;
      }

      return get().createNewConversation();
    },

    startNewConversationWithMessage: async (content, isPrivate = false) => {
      const trimmed = content.trim();
      if (!trimmed) {
        return { ok: false, conversationId: null, error: null };
      }
      if (get().isStreaming || get().isSending) {
        return {
          ok: false,
          conversationId: get().activeConversationId || null,
          error: null,
        };
      }

      const id = await get().createNewConversation();
      if (!id) {
        return {
          ok: false,
          conversationId: null,
          error:
            get().conversationsError ??
            'The conversation could not be created.',
        };
      }

      return get().sendMessage(trimmed, isPrivate);
    },

    deleteConversation: async (id) => {
      const backendId = toBackendId(id);

      if (backendId === null) {
        set({
          conversationsError:
            'This conversation is not backed by the server and cannot be deleted.',
        });
        return false;
      }

      if (get().pendingConversationIds.includes(id)) return false;

      set((state) => ({
        pendingConversationIds: [...state.pendingConversationIds, id],
        conversationsError: null,
      }));

      try {
        await conversationsApi.delete(backendId);

        set((state) => {
          const remaining = state.conversations.filter(
            (conversation) => conversation.id !== id
          );

          return {
            conversations: remaining,
            activeConversationId:
              state.activeConversationId === id
                ? remaining[0]?.id ?? ''
                : state.activeConversationId,
            pendingConversationIds: state.pendingConversationIds.filter(
              (pendingId) => pendingId !== id
            ),
            conversationsError: null,
          };
        });

        return true;
      } catch (error) {
        set((state) => ({
          pendingConversationIds: state.pendingConversationIds.filter(
            (pendingId) => pendingId !== id
          ),
          conversationsError: readableError(
            error,
            'The conversation could not be deleted.'
          ),
        }));
        return false;
      }
    },

    renameConversation: async (id, newTitle) => {
      const backendId = toBackendId(id);

      if (backendId === null) {
        set({
          conversationsError:
            'This conversation is not backed by the server and cannot be renamed.',
        });
        return false;
      }

      const trimmed = newTitle.trim();
      if (!trimmed) {
        set({ conversationsError: 'A conversation title cannot be empty.' });
        return false;
      }

      if (get().pendingConversationIds.includes(id)) return false;

      set((state) => ({
        pendingConversationIds: [...state.pendingConversationIds, id],
        conversationsError: null,
      }));

      try {
        const updated = await conversationsApi.updateTitle(backendId, trimmed);
        const confirmedTitle =
          typeof updated?.title === 'string' && updated.title.trim()
            ? updated.title.trim()
            : trimmed;

        set((state) => ({
          conversations: state.conversations.map((conversation) =>
            conversation.id === id
              ? { ...conversation, title: confirmedTitle }
              : conversation
          ),
          pendingConversationIds: state.pendingConversationIds.filter(
            (pendingId) => pendingId !== id
          ),
          conversationsError: null,
        }));

        return true;
      } catch (error) {
        set((state) => ({
          pendingConversationIds: state.pendingConversationIds.filter(
            (pendingId) => pendingId !== id
          ),
          conversationsError: readableError(
            error,
            'The conversation could not be renamed.'
          ),
        }));
        return false;
      }
    },

    sendMessage: async (content, isPrivate = false) => {
      const trimmed = content.trim();

      if (!trimmed) {
        return {
          ok: false,
          conversationId: get().activeConversationId || null,
          error: null,
        };
      }

      if (get().isStreaming || get().isSending) {
        return {
          ok: false,
          conversationId: get().activeConversationId || null,
          error: null,
        };
      }

      const attempt = ++sendAttempt;
      set({ isSending: true, streamError: null });

      let conversationId: string | null = get().activeConversationId || null;
      let assistantMessage: ConversationMessage | null = null;

      try {
        const ensured = await get().ensureConversation();

        if (ensured === null) {
          return {
            ok: false,
            conversationId: null,
            error:
              get().conversationsError ??
              'The conversation could not be created.',
          };
        }

        conversationId = ensured;

        // The user may have stopped during conversation creation.
        if (abortedSendAttempt === attempt) {
          return { ok: false, conversationId, error: null };
        }

        const knownConversation = get().conversations.find(
          (conversation) => conversation.id === conversationId
        );

        if (!knownConversation) {
          const message = 'The conversation is no longer available.';
          set({ streamError: message });
          return { ok: false, conversationId, error: message };
        }

        // Load the thread before appending if it has not been confirmed yet.
        // A failed load is surfaced, but does not prevent a new message.
        if (
          !knownConversation.messagesLoaded &&
          !knownConversation.messagesUnavailable &&
          !knownConversation.messagesLoading
        ) {
          await get().loadMessages(conversationId);
        }

        if (abortedSendAttempt === attempt) {
          return { ok: false, conversationId, error: null };
        }

        const target = get().conversations.find(
          (conversation) => conversation.id === conversationId
        );

        if (!target) {
          const message = 'The conversation is no longer available.';
          set({ streamError: message });
          return { ok: false, conversationId, error: message };
        }

        const timestamp = formatClock();
        const userMessage: ConversationMessage = {
          id: nextLocalMessageId('user'),
          role: 'user',
          content: trimmed,
          timestamp,
          status: 'complete',
        };

        assistantMessage = {
          id: nextLocalMessageId('assistant'),
          role: 'assistant',
          content: '',
          timestamp,
          status: 'streaming',
        };

        const assistantMessageId = assistantMessage.id;
        const activeAssistantMessage = assistantMessage;
        // Mirrors received tokens in case the provisional entry must be rebuilt.
        let streamedContent = '';

        const adoptConversation = (nextId: string) => {
          set((state) => {
            const source = state.conversations.find((conversation) =>
              conversation.messages.some(
                (message) => message.id === assistantMessageId
              )
            );

            if (!source) {
              // The provisional entry disappeared (for example a concurrent
              // list refresh). Rebuild it from the in-flight turn so no
              // received token is silently discarded.
              const now = new Date().toISOString();
              return {
                activeConversationId: nextId,
                conversations: [
                  {
                    id: nextId,
                    title: 'New Conversation',
                    createdAt: now,
                    updatedAt: now,
                    messages: [
                      userMessage,
                      { ...activeAssistantMessage, content: streamedContent },
                    ],
                    messagesLoaded: false,
                    messagesUnavailable: false,
                    messagesLoading: false,
                    messagesError: null,
                  },
                  ...state.conversations,
                ],
              };
            }

            if (source.id === nextId) {
              return { activeConversationId: nextId };
            }

            const existingTarget = state.conversations.find(
              (conversation) => conversation.id === nextId
            );

            if (existingTarget) {
              return {
                activeConversationId: nextId,
                conversations: state.conversations
                  .filter((conversation) => conversation.id !== source.id)
                  .map((conversation) =>
                    conversation.id === nextId
                      ? {
                          ...conversation,
                          messages: mergeMessages(
                            source.messages,
                            conversation.messages
                          ),
                          messagesLoaded:
                            Boolean(source.messagesLoaded) ||
                            Boolean(conversation.messagesLoaded),
                          messagesUnavailable: false,
                          messagesLoading: false,
                          messagesError: null,
                        }
                      : conversation
                  ),
              };
            }

            // Adopt the backend-assigned id on the existing entry.
            return {
              activeConversationId: nextId,
              conversations: state.conversations.map((conversation) =>
                conversation.id === source.id
                  ? { ...conversation, id: nextId }
                  : conversation
              ),
            };
          });
        };

        const isCurrentAttempt = () => attempt === sendAttempt;

        set((state) => ({
          isSending: false,
          isStreaming: true,
          streamingMessageId: assistantMessageId,
          streamError: null,
          conversations: state.conversations.map((conversation) =>
            conversation.id === conversationId
              ? {
                  ...conversation,
                  messages: [
                    ...conversation.messages,
                    userMessage,
                    activeAssistantMessage,
                  ],
                  messageCount: undefined,
                  previewSnippet: trimmed,
                  updatedAt: new Date().toISOString(),
                }
              : conversation
          ),
        }));

        let sawDone = false;
        let sawError = false;

        await chatSSEClient.streamChat(
          {
            message: trimmed,
            private: isPrivate,
            conversation_id: toBackendId(conversationId),
          },
          {
            onStart: (data: SSEStartData) => {
              if (!isCurrentAttempt()) return;

              if (data.model) {
                set({ activeModel: data.model });
              }

              if (data.model || data.provider) {
                set((state) => ({
                  conversations: updateMessage(
                    state.conversations,
                    assistantMessageId,
                    (message) => ({
                      ...message,
                      model: data.model ?? message.model,
                      provider: data.provider ?? message.provider,
                    })
                  ),
                }));
              }

              if (data.conversation_id !== undefined && data.conversation_id !== null) {
                const nextId = String(data.conversation_id);
                if (nextId !== conversationId) {
                  conversationId = nextId;
                  adoptConversation(nextId);
                }
              }
            },

            onDelta: (textChunk: string) => {
              if (!isCurrentAttempt() || !textChunk) return;

              streamedContent += textChunk;

              set((state) => ({
                conversations: updateMessage(
                  state.conversations,
                  assistantMessageId,
                  (message) => ({
                    ...message,
                    content: message.content + textChunk,
                  })
                ),
              }));
            },

            onDone: (data: SSEDoneData) => {
              if (!isCurrentAttempt()) return;
              sawDone = true;

              if (data.conversation_id !== undefined && data.conversation_id !== null) {
                const nextId = String(data.conversation_id);
                if (nextId !== conversationId) {
                  conversationId = nextId;
                  adoptConversation(nextId);
                }
              }

              if (data.model) {
                set({ activeModel: data.model });
              }

              const content =
                findMessage(get().conversations, assistantMessageId)?.content ?? '';

              set((state) => ({
                isStreaming: false,
                streamingMessageId: null,
                conversations: updateMessage(
                  state.conversations,
                  assistantMessageId,
                  (message) => ({
                    ...message,
                    status: content ? 'complete' : 'incomplete',
                    provider: data.provider ?? message.provider,
                    model: data.model ?? message.model,
                  })
                ),
              }));

              if (!content) {
                set({ streamError: 'The assistant returned an empty response.' });
              }
            },

            onError: (errorData: SSEErrorData) => {
              if (!isCurrentAttempt()) return;
              sawError = true;

              const content =
                findMessage(get().conversations, assistantMessageId)?.content ?? '';

              set((state) => ({
                isStreaming: false,
                streamingMessageId: null,
                streamError:
                  errorData.message || 'The response could not be completed.',
                conversations: updateMessage(
                  state.conversations,
                  assistantMessageId,
                  (message) => ({
                    ...message,
                    status: content ? 'incomplete' : 'failed',
                  })
                ),
              }));
            },
          }
        );

        if (!isCurrentAttempt()) {
          return { ok: false, conversationId, error: get().streamError };
        }

        if (!sawDone && !sawError) {
          if (abortedSendAttempt === attempt || !get().isStreaming) {
            // stopGeneration already marked the interrupted message.
            set({ isStreaming: false, streamingMessageId: null });
            return { ok: false, conversationId, error: null };
          }

          const content =
            findMessage(get().conversations, assistantMessageId)?.content ?? '';
          const terminationMessage =
            'The response ended unexpectedly before it completed.';

          set((state) => ({
            isStreaming: false,
            streamingMessageId: null,
            streamError: terminationMessage,
            conversations: updateMessage(
              state.conversations,
              assistantMessageId,
              (message) => ({
                ...message,
                status: content ? 'incomplete' : 'failed',
              })
            ),
          }));

          return { ok: false, conversationId, error: terminationMessage };
        }

        return {
          ok: sawDone && !sawError,
          conversationId,
          error: get().streamError,
        };
      } catch (error) {
        const message = readableError(error, 'The message could not be sent.');

        if (attempt === sendAttempt) {
          set((state) => ({
            isStreaming: false,
            streamingMessageId: null,
            streamError: message,
            conversations: assistantMessage
              ? updateMessage(
                  state.conversations,
                  assistantMessage.id,
                  (current) => ({ ...current, status: 'failed' })
                )
              : state.conversations,
          }));
        }

        return { ok: false, conversationId, error: message };
      } finally {
        if (attempt === sendAttempt && get().isSending) {
          set({ isSending: false });
        }
      }
    },

    stopGeneration: () => {
      abortedSendAttempt = sendAttempt;

      const messageId = get().streamingMessageId;

      if (messageId) {
        const content =
          findMessage(get().conversations, messageId)?.content ?? '';

        set((state) => ({
          isStreaming: false,
          streamingMessageId: null,
          conversations: updateMessage(
            state.conversations,
            messageId,
            (message) => ({
              ...message,
              // Keep any received text, but make it clear that this answer was
              // interrupted rather than completed by the provider.
              status: content ? 'incomplete' : 'failed',
            })
          ),
        }));
      } else {
        set({ isStreaming: false, streamingMessageId: null });
      }

      set({ isSending: false, streamError: null });
      chatSSEClient.cancel();
    },

    clearConversationsError: () => set({ conversationsError: null }),
    clearStreamError: () => set({ streamError: null }),
  };
});

export default useChatStore;
