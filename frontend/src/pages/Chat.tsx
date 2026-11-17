import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
  Send,
  Square,
  Copy,
  Check,
  Plus,
  Trash2,
  Edit2,
  Sparkles,
  Bot,
  User,
  Cpu,
  Search,
  MessageSquare,
  Wrench,
  ArrowDown,
  Shield,
  AlertTriangle,
  Loader2,
} from 'lucide-react';
import { useChatStore } from '../stores/chat.store';
import { useAuthStore } from '../stores/auth.store';
import MarkdownRenderer from '../components/chat/MarkdownRenderer';

interface StarterPrompt {
  title: string;
  description: string;
  prompt: string;
  category: string;
}

const STARTER_PROMPTS: StarterPrompt[] = [
  {
    title: 'Daily Workspace Briefing',
    description: 'Summarize today’s tasks, scheduled alerts, and system health.',
    prompt: 'Give me a concise briefing of my tasks, reminders, and workspace priorities for today.',
    category: 'Workspace',
  },
  {
    title: 'Review System Architecture',
    description: 'Inspect how backend-connected chat, tools, and stores fit together.',
    prompt: 'Explain how ASTA organizes backend-connected chat, tool execution, and Zustand stores.',
    category: 'Engineering',
  },
  {
    title: 'Inspect Safe Local File',
    description: 'Read frontend configuration from disk with the safe file reader tool.',
    prompt: 'Run the safe file reader tool to inspect frontend/package.json.',
    category: 'Tool Calling',
  },
  {
    title: 'Recall Semantic Memory',
    description: 'Query indexed user rules and coding conventions.',
    prompt: 'What key project conventions and preferences are currently stored in my memory?',
    category: 'Memory',
  },
];

const InlineError: React.FC<{
  title: string;
  message?: string | null;
  onRetry?: () => void;
  onDismiss?: () => void;
  retryDisabled?: boolean;
}> = ({ title, message, onRetry, onDismiss, retryDisabled }) => (
  <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3.5 py-3 text-xs text-rose-200">
    <div className="flex items-start gap-2">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-300" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold text-rose-100">{title}</p>
        {message && <p className="mt-1 break-words text-[11px] text-rose-200/90">{message}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            disabled={retryDisabled}
            className="rounded-lg border border-rose-400/30 bg-rose-500/15 px-2 py-1 text-[11px] font-semibold text-rose-100 transition hover:bg-rose-500/25 disabled:opacity-40"
          >
            Retry
          </button>
        )}
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-lg px-2 py-1 text-[11px] text-rose-200/80 transition hover:bg-rose-500/15 hover:text-rose-100"
          >
            Dismiss
          </button>
        )}
      </div>
    </div>
  </div>
);

export const Chat: React.FC = () => {
  const conversations = useChatStore((s) => s.conversations);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const conversationsStatus = useChatStore((s) => s.conversationsStatus);
  const conversationsError = useChatStore((s) => s.conversationsError);
  const conversationsLoaded = useChatStore((s) => s.conversationsLoaded);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const isSending = useChatStore((s) => s.isSending);
  const streamingMessageId = useChatStore((s) => s.streamingMessageId);
  const activeModel = useChatStore((s) => s.activeModel);
  const streamError = useChatStore((s) => s.streamError);
  const creatingConversation = useChatStore((s) => s.creatingConversation);
  const pendingConversationIds = useChatStore((s) => s.pendingConversationIds);
  const setActiveConversationId = useChatStore((s) => s.setActiveConversationId);
  const createNewConversation = useChatStore((s) => s.createNewConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const stopGeneration = useChatStore((s) => s.stopGeneration);
  const loadConversations = useChatStore((s) => s.loadConversations);
  const loadMessages = useChatStore((s) => s.loadMessages);
  const retryActiveConversation = useChatStore((s) => s.retryActiveConversation);
  const clearConversationsError = useChatStore((s) => s.clearConversationsError);
  const clearStreamError = useChatStore((s) => s.clearStreamError);

  const user = useAuthStore((s) => s.user);

  const [input, setInput] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const [searchConvQuery, setSearchConvQuery] = useState('');
  const [editingConvId, setEditingConvId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const currentConversation = activeConversationId
    ? conversations.find((c) => c.id === activeConversationId) ?? null
    : null;

  const messages = useMemo(
    () => currentConversation?.messages ?? [],
    [currentConversation?.messages]
  );

  const messagesLoaded = currentConversation?.messagesLoaded ?? false;
  const messagesUnavailable = currentConversation?.messagesUnavailable ?? false;
  const messagesLoading = currentConversation?.messagesLoading ?? false;
  const isBusy = isStreaming || isSending;
  const hasConversations = conversations.length > 0;
  const listFailed = conversationsStatus === 'error';
  const listLoading =
    conversationsStatus === 'loading' ||
    (conversationsStatus === 'idle' && !conversationsLoaded);
  const initialListFailure = listFailed && !conversationsLoaded && !hasConversations;

  // AppShell loads conversations on authentication. If Chat mounts first, start
  // the same request here; the store serialises it so only one request runs.
  useEffect(() => {
    if (conversationsStatus === 'idle') {
      void loadConversations();
    }
  }, [conversationsStatus, loadConversations]);

  // Fetch message history lazily for the active conversation only.
  useEffect(() => {
    if (!activeConversationId) return;
    if (messagesLoaded) return;
    if (messagesLoading) return;
    if (messagesUnavailable) return;
    void loadMessages(activeConversationId);
  }, [
    activeConversationId,
    messagesLoaded,
    messagesLoading,
    messagesUnavailable,
    loadMessages,
  ]);

  // Auto-scroll on new messages or streaming chunk
  useEffect(() => {
    if (!showScrollBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, showScrollBottom]);

  // Track scroll position to show "scroll to bottom" button
  const handleScroll = () => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    setShowScrollBottom(!isAtBottom);
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    setShowScrollBottom(false);
  };

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }, [input]);

  const handleSend = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || isBusy) return;

    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    const result = await sendMessage(trimmed);

    // If no backend conversation could be created, restore the draft. A failed
    // stream keeps the user message in the thread and does not re-send it.
    if (!result.ok && result.conversationId === null) {
      setInput(trimmed);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  const handleCopyMessage = async (id: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 1800);
    } catch {
      // Ignore
    }
  };

  const handleStartRename = (id: string, currentTitle: string) => {
    setEditingConvId(id);
    setEditingTitle(currentTitle);
  };

  const handleSaveRename = async (id: string) => {
    if (editingConvId !== id) return;
    setEditingConvId(null);
    await renameConversation(id, editingTitle);
  };

  const filteredConversations = conversations.filter((c) =>
    c.title.toLowerCase().includes(searchConvQuery.toLowerCase())
  );

  const messageCount = currentConversation?.messageCount ?? messages.length;

  const threadSummary = !currentConversation
    ? 'No active conversation'
    : messagesUnavailable && !messagesLoaded
    ? 'Message history unavailable'
    : !messagesLoaded
    ? 'Loading message history...'
    : `${messageCount} message${messageCount === 1 ? '' : 's'}`;

  const renderThreadContent = () => {
    if (initialListFailure) {
      return (
        <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center py-10 text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-rose-500/30 bg-rose-500/10 text-rose-300">
            <AlertTriangle className="h-6 w-6" />
          </div>
          <h2 className="text-base font-bold text-white">Conversations could not be loaded</h2>
          <p className="mt-2 text-xs text-slate-400">
            {conversationsError || 'The backend conversation list is unavailable.'}
          </p>
          <button
            type="button"
            onClick={() => void loadConversations()}
            disabled={listLoading}
            className="mt-4 inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-40"
          >
            {listLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            <span>Retry</span>
          </button>
        </div>
      );
    }

    if (listLoading && !hasConversations) {
      return (
        <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center py-10 text-center">
          <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
          <p className="mt-3 text-xs text-slate-400">Loading conversations...</p>
        </div>
      );
    }

    if (!hasConversations) {
      return (
        <div className="mx-auto flex h-full max-w-lg flex-col items-center justify-center py-10 text-center">
          <div className="relative mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 via-violet-600 to-cyan-500 shadow-xl shadow-indigo-500/20">
            <MessageSquare className="h-8 w-8 text-white" />
          </div>
          <h2 className="text-xl font-bold tracking-tight text-white sm:text-2xl">
            No conversations yet
          </h2>
          <p className="mt-2 max-w-md text-xs leading-relaxed text-slate-400 sm:text-sm">
            Create your first conversation to start chatting with ASTA. Conversations,
            messages and history are stored by the backend — nothing is fabricated locally.
          </p>
          <button
            type="button"
            onClick={() => void createNewConversation()}
            disabled={creatingConversation}
            className="mt-5 inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 px-5 py-2.5 text-xs font-semibold text-white shadow-lg shadow-indigo-600/25 transition hover:from-indigo-500 hover:to-violet-500 disabled:opacity-50"
          >
            {creatingConversation ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Plus className="h-3.5 w-3.5" />
            )}
            <span>{creatingConversation ? 'Creating...' : 'New Conversation'}</span>
          </button>
          {conversationsError && (
            <p className="mt-3 text-[11px] text-rose-300">{conversationsError}</p>
          )}
        </div>
      );
    }

    if (!currentConversation) {
      return (
        <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center py-10 text-center">
          <MessageSquare className="h-7 w-7 text-slate-500" />
          <p className="mt-3 text-xs text-slate-400">
            Select a conversation from the list to view its messages.
          </p>
        </div>
      );
    }

    if (messagesUnavailable && !messagesLoaded && messages.length === 0 && !messagesLoading) {
      return (
        <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center py-10 text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-rose-500/30 bg-rose-500/10 text-rose-300">
            <AlertTriangle className="h-6 w-6" />
          </div>
          <h2 className="text-base font-bold text-white">Messages could not be loaded</h2>
          <p className="mt-2 text-xs text-slate-400">
            {currentConversation.messagesError ||
              'The backend did not return this conversation’s message history.'}
          </p>
          <button
            type="button"
            onClick={() => void loadMessages(currentConversation.id, true)}
            disabled={messagesLoading || isBusy}
            className="mt-4 inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-40"
          >
            {messagesLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            <span>Retry</span>
          </button>
        </div>
      );
    }

    if (!messagesLoaded && messages.length === 0) {
      return (
        <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center py-10 text-center">
          <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
          <p className="mt-3 text-xs text-slate-400">Loading messages...</p>
        </div>
      );
    }

    if (messages.length === 0) {
      return (
        <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center py-6 text-center">
          <div className="relative mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 via-violet-600 to-cyan-500 shadow-xl shadow-indigo-500/20">
            <Sparkles className="h-8 w-8 text-white" />
          </div>

          <h2 className="text-xl font-bold tracking-tight text-white sm:text-2xl">
            Start this conversation
          </h2>
          <p className="mt-2 max-w-md text-xs leading-relaxed text-slate-400 sm:text-sm">
            This backend conversation has no messages yet. Send a prompt or choose a starter
            below.
          </p>

          {/* Starter Prompt Cards */}
          <div className="mt-6 grid w-full grid-cols-1 gap-3 text-left sm:grid-cols-2">
            {STARTER_PROMPTS.map((item) => (
              <button
                key={item.title}
                type="button"
                onClick={() => void sendMessage(item.prompt)}
                disabled={isBusy}
                className="group flex flex-col justify-between rounded-2xl border border-slate-800 bg-slate-900/40 p-4 transition hover:border-indigo-500/40 hover:bg-slate-900/80 hover:shadow-lg hover:shadow-indigo-500/5 disabled:opacity-50"
              >
                <div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-slate-200 group-hover:text-indigo-300">
                      {item.title}
                    </span>
                    <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-slate-400">
                      {item.category}
                    </span>
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-slate-500">
                    {item.description}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>
      );
    }

    return messages.map((msg) => {
      const isUser = msg.role === 'user';
      const isCurrentlyStreaming = isStreaming && streamingMessageId === msg.id;
      const isFailed = msg.status === 'failed';
      const isIncomplete = msg.status === 'incomplete';

      return (
        <div
          key={msg.id}
          className={`flex items-start gap-3.5 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}
        >
          {/* Avatar */}
          <div
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border ${
              isUser
                ? 'border-indigo-400/40 bg-indigo-600 text-white'
                : 'border-slate-800 bg-slate-900 text-indigo-400 ring-1 ring-indigo-500/20'
            }`}
          >
            {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
          </div>

          {/* Message Bubble */}
          <div
            className={`group flex max-w-[85%] flex-col sm:max-w-[80%] ${
              isUser ? 'items-end' : 'items-start'
            }`}
          >
            <div
              className={`rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
                isUser
                  ? 'bg-gradient-to-r from-indigo-600 to-violet-600 text-white'
                  : 'border border-slate-800/90 bg-slate-900/90 text-slate-200 backdrop-blur-md'
              }`}
            >
              {/* If assistant is starting and content is still empty */}
              {!isUser && !msg.content && isCurrentlyStreaming ? (
                <div className="flex items-center gap-2 py-1 text-xs text-slate-400">
                  <span className="flex items-center gap-1">
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-400 [animation-delay:-0.3s]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-400 [animation-delay:-0.15s]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-400" />
                  </span>
                  <span>ASTA is thinking...</span>
                </div>
              ) : !isUser && isFailed && !msg.content ? (
                <p className="py-1 text-xs text-rose-300">
                  No response content was received.
                </p>
              ) : isUser ? (
                <p className="whitespace-pre-wrap break-words">{msg.content}</p>
              ) : (
                <div>
                  <MarkdownRenderer content={msg.content} />
                  {isCurrentlyStreaming && (
                    <span className="ml-1 inline-block h-3.5 w-1.5 animate-pulse bg-indigo-400 align-middle" />
                  )}
                </div>
              )}

              {/* Tool Executions Preview if attached to message */}
              {msg.toolExecutions && msg.toolExecutions.length > 0 && (
                <div className="mt-3 space-y-2 border-t border-slate-800/80 pt-2.5">
                  {msg.toolExecutions.map((tool, tIdx) => {
                    const toolStatus = tool.success
                      ? { label: 'EXECUTED', tone: 'text-emerald-400' }
                      : tool.confirmation_required
                        ? { label: 'PENDING CONFIRMATION', tone: 'text-amber-300' }
                        : { label: 'FAILED', tone: 'text-rose-300' };

                    return (
                      <div
                        key={tIdx}
                        className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/80 px-3 py-1.5 text-xs font-mono text-slate-300"
                      >
                        <div className="flex items-center gap-2">
                          <Wrench className="h-3.5 w-3.5 text-cyan-400" />
                          <span className="font-semibold">{tool.tool}</span>
                        </div>
                        <div className="flex items-center gap-2 text-[10px] text-slate-500">
                          <span className={toolStatus.tone}>{toolStatus.label}</span>
                          <span>·</span>
                          <span>
                            {typeof tool.duration_ms === 'number'
                              ? `${tool.duration_ms}ms`
                              : 'duration unavailable'}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Metadata & Hover Controls */}
            <div
              className={`mt-1.5 flex items-center gap-2 px-1 text-[11px] text-slate-500 ${
                isUser ? 'flex-row-reverse' : 'flex-row'
              }`}
            >
              <span>
                {isUser ? user.name : msg.model || msg.provider || 'ASTA'} · {msg.timestamp}
              </span>

              {!isUser && isIncomplete && (
                <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-300">
                  Incomplete
                </span>
              )}

              {!isUser && isFailed && (
                <span className="rounded bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-300">
                  Failed
                </span>
              )}

              {!isUser && !isCurrentlyStreaming && msg.content && (
                <div className="flex items-center gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
                  <button
                    type="button"
                    onClick={() => void handleCopyMessage(msg.id, msg.content)}
                    className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                    title="Copy message"
                  >
                    {copiedId === msg.id ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      );
    });
  };

  return (
    <div className="flex h-[calc(100vh-8.5rem)] min-h-[500px] overflow-hidden rounded-2xl border border-slate-800/80 bg-slate-950/75 shadow-2xl backdrop-blur-sm">
      {/* Left Column: Conversation History Sidebar */}
      <div className="hidden md:flex w-64 lg:w-72 shrink-0 flex-col border-r border-slate-800/80 bg-slate-950/90">
        {/* New Chat & Search Header */}
        <div className="p-3 space-y-2 border-b border-slate-800/80">
          <button
            type="button"
            onClick={() => void createNewConversation()}
            disabled={creatingConversation}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600/20 border border-indigo-500/30 px-3 py-2 text-xs font-semibold text-indigo-300 transition hover:bg-indigo-600/30 disabled:opacity-50"
          >
            {creatingConversation ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Plus className="h-3.5 w-3.5" />
            )}
            <span>{creatingConversation ? 'Creating...' : 'New Conversation'}</span>
          </button>

          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              value={searchConvQuery}
              onChange={(e) => setSearchConvQuery(e.target.value)}
              placeholder="Filter threads..."
              className="h-8 w-full rounded-lg border border-slate-800 bg-slate-900/90 pl-8 pr-3 text-xs text-slate-200 placeholder-slate-500 focus:border-indigo-500/50 focus:outline-none"
            />
          </div>
        </div>

        {conversationsError && (
          <div className="p-2">
            <InlineError
              title={listFailed ? 'Conversations could not be loaded.' : 'Conversation action failed.'}
              message={conversationsError}
              onRetry={listFailed ? () => void loadConversations() : undefined}
              onDismiss={clearConversationsError}
              retryDisabled={listLoading}
            />
          </div>
        )}

        {/* Conversation list */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {filteredConversations.length === 0 ? (
            <p className="px-3 py-6 text-center text-[11px] text-slate-500">
              {conversations.length === 0
                ? 'No conversations yet.'
                : 'No conversations match this filter.'}
            </p>
          ) : (
            filteredConversations.map((conv) => {
              const isActive = conv.id === activeConversationId;
              const isEditing = editingConvId === conv.id;
              const isPending = pendingConversationIds.includes(conv.id);

              return (
                <div
                  key={conv.id}
                  onClick={() => !isEditing && setActiveConversationId(conv.id)}
                  className={`group relative flex items-center justify-between rounded-xl px-3 py-2.5 text-xs transition cursor-pointer ${
                    isActive
                      ? 'border border-indigo-500/30 bg-indigo-500/15 text-white font-medium shadow-sm'
                      : 'text-slate-400 hover:border hover:border-slate-800/80 hover:bg-slate-900/60 hover:text-slate-200'
                  } ${isPending ? 'opacity-70' : ''}`}
                >
                  {isEditing ? (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        void handleSaveRename(conv.id);
                      }}
                      className="flex w-full items-center gap-1"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <input
                        type="text"
                        value={editingTitle}
                        onChange={(e) => setEditingTitle(e.target.value)}
                        autoFocus
                        disabled={isPending}
                        aria-label={`Rename conversation ${conv.title}`}
                        className="h-6 w-full rounded bg-slate-900 px-1.5 text-xs text-white border border-indigo-500/50 focus:outline-none disabled:opacity-50"
                      />
                    </form>
                  ) : (
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <MessageSquare className={`h-3.5 w-3.5 shrink-0 ${isActive ? 'text-indigo-400' : 'text-slate-500'}`} />
                      <span className="truncate">{conv.title}</span>
                      {isPending && <Loader2 className="h-3 w-3 shrink-0 animate-spin text-slate-400" />}
                    </div>
                  )}

                  {/* Edit & Delete Actions */}
                  {!isEditing && (
                    <div className="hidden group-hover:flex items-center gap-1 shrink-0 ml-1">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleStartRename(conv.id, conv.title);
                        }}
                        disabled={isPending}
                        className="rounded p-1 text-slate-400 hover:text-slate-200 hover:bg-slate-800 disabled:opacity-40"
                        title="Rename"
                        aria-label={`Rename conversation ${conv.title}`}
                      >
                        <Edit2 className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          void deleteConversation(conv.id);
                        }}
                        disabled={isPending}
                        className="rounded p-1 text-slate-400 hover:text-rose-400 hover:bg-slate-800 disabled:opacity-40"
                        title="Delete"
                        aria-label={`Delete conversation ${conv.title}`}
                      >
                        {isPending ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Trash2 className="h-3 w-3" />
                        )}
                      </button>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Right Column: Active Conversation Workspace */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Chat Thread Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-slate-800/80 bg-slate-950/90 px-4 py-2.5 sm:px-6">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-500/15 text-indigo-400 ring-1 ring-indigo-500/30 shrink-0">
              <Cpu className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="truncate text-xs sm:text-sm font-bold text-white">
                  {currentConversation?.title || 'No active conversation'}
                </span>
                {activeModel && (
                  <span className="rounded bg-indigo-500/10 px-2 py-0.5 font-mono text-[10px] text-indigo-300 hidden sm:inline">
                    {activeModel}
                  </span>
                )}
              </div>
              <p className="text-[11px] text-slate-500">
                {threadSummary} · Backend SSE stream
              </p>
            </div>
          </div>
        </div>

        {/* Message Thread Scroll Area */}
        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="relative flex-1 overflow-y-auto px-4 py-6 sm:px-6 space-y-6"
        >
          {conversationsError && !initialListFailure && (
            <div className="md:hidden">
              <InlineError
                title={listFailed ? 'Conversations could not be loaded.' : 'Conversation action failed.'}
                message={conversationsError}
                onRetry={listFailed ? () => void loadConversations() : undefined}
                onDismiss={clearConversationsError}
                retryDisabled={listLoading}
              />
            </div>
          )}

          {streamError && (
            <InlineError
              title="The response could not be completed."
              message={streamError}
              onRetry={() => void retryActiveConversation()}
              onDismiss={clearStreamError}
              retryDisabled={isBusy}
            />
          )}

          {messagesUnavailable && (messages.length > 0 || messagesLoaded) && (
            <InlineError
              title="Messages could not be loaded."
              message={currentConversation?.messagesError}
              onRetry={() => void loadMessages(activeConversationId, true)}
              retryDisabled={isBusy || messagesLoading}
            />
          )}

          {renderThreadContent()}
          <div ref={messagesEndRef} />
        </div>

        {/* Scroll To Bottom Button */}
        {showScrollBottom && (
          <button
            type="button"
            onClick={scrollToBottom}
            className="absolute bottom-24 right-8 flex h-8 w-8 items-center justify-center rounded-full border border-slate-700 bg-slate-800 text-slate-200 shadow-xl transition hover:bg-slate-700"
            title="Scroll to bottom"
          >
            <ArrowDown className="h-4 w-4" />
          </button>
        )}

        {/* Input Composer Area */}
        <div className="shrink-0 border-t border-slate-800/80 bg-slate-950 p-3 sm:p-4">
          <form onSubmit={handleSend} className="relative flex flex-col gap-2">
            <div className="relative flex items-end rounded-2xl border border-slate-800 bg-slate-900/90 shadow-inner focus-within:border-indigo-500/60 focus-within:ring-2 focus-within:ring-indigo-500/20">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={1}
                placeholder="Ask ASTA anything, formulate a plan, or trigger a tool..."
                className="max-h-44 min-h-[46px] w-full resize-none bg-transparent px-4 py-3 text-xs sm:text-sm text-slate-100 placeholder-slate-500 focus:outline-none"
              />

              <div className="flex items-center gap-1.5 p-2 shrink-0">
                {isStreaming ? (
                  <button
                    type="button"
                    onClick={stopGeneration}
                    className="flex h-8 w-8 items-center justify-center rounded-xl bg-rose-600 text-white shadow-md shadow-rose-600/30 transition hover:bg-rose-500"
                    title="Stop generation"
                  >
                    <Square className="h-3.5 w-3.5 fill-current" />
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!input.trim() || isSending}
                    className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-md shadow-indigo-600/25 transition hover:bg-indigo-500 disabled:opacity-40"
                    title={isSending ? 'Creating conversation...' : 'Send message (Enter)'}
                  >
                    {isSending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Send className="h-3.5 w-3.5" />
                    )}
                  </button>
                )}
              </div>
            </div>

            {/* Hint & Status Footer */}
            <div className="flex items-center justify-between px-1 text-[11px] text-slate-500">
              <span className="hidden sm:inline">
                Press <kbd className="rounded border border-slate-700 bg-slate-800 px-1 py-0.2 text-[10px] text-slate-300">Enter</kbd> to send, <kbd className="rounded border border-slate-700 bg-slate-800 px-1 py-0.2 text-[10px] text-slate-300">Shift + Enter</kbd> for newline
              </span>
              <div className="flex items-center gap-2 ml-auto">
                <span className="flex items-center gap-1 text-[10px] text-indigo-400">
                  <Shield className="h-3 w-3" />
                  Authenticated session
                </span>
              </div>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};

export default Chat;
