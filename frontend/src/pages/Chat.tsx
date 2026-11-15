import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
  Send,
  Square,
  RotateCcw,
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
    description: 'Inspect layout isolation and modular store design.',
    prompt: 'Explain the ASTA Phase 14 frontend architecture and how stores decouple future backend integration.',
    category: 'Engineering',
  },
  {
    title: 'Inspect Safe Local File',
    description: 'Simulate reading frontend configuration from disk.',
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

export const Chat: React.FC = () => {
  const conversations = useChatStore((s) => s.conversations);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const streamingMessageId = useChatStore((s) => s.streamingMessageId);
  const activeModel = useChatStore((s) => s.activeModel);
  const setActiveConversationId = useChatStore((s) => s.setActiveConversationId);
  const createNewConversation = useChatStore((s) => s.createNewConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const stopGeneration = useChatStore((s) => s.stopGeneration);
  const regenerateLastMessage = useChatStore((s) => s.regenerateLastMessage);
  const clearActiveConversation = useChatStore((s) => s.clearActiveConversation);

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

  const currentConversation =
    conversations.find((c) => c.id === activeConversationId) || conversations[0];

  const messages = useMemo(
    () => currentConversation?.messages || [],
    [currentConversation?.messages]
  );

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
    if (!trimmed || isStreaming) return;

    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
    await sendMessage(trimmed);
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

  const handleSaveRename = (id: string) => {
    renameConversation(id, editingTitle);
    setEditingConvId(null);
  };

  const filteredConversations = conversations.filter((c) =>
    c.title.toLowerCase().includes(searchConvQuery.toLowerCase())
  );

  return (
    <div className="flex h-[calc(100vh-8.5rem)] min-h-[500px] overflow-hidden rounded-2xl border border-slate-800/80 bg-slate-950/75 shadow-2xl backdrop-blur-sm">
      {/* Left Column: Conversation History Sidebar */}
      <div className="hidden md:flex w-64 lg:w-72 shrink-0 flex-col border-r border-slate-800/80 bg-slate-950/90">
        {/* New Chat & Search Header */}
        <div className="p-3 space-y-2 border-b border-slate-800/80">
          <button
            type="button"
            onClick={createNewConversation}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600/20 border border-indigo-500/30 px-3 py-2 text-xs font-semibold text-indigo-300 transition hover:bg-indigo-600/30"
          >
            <Plus className="h-3.5 w-3.5" />
            <span>New Conversation</span>
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

        {/* Conversation list */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {filteredConversations.map((conv) => {
            const isActive = conv.id === activeConversationId;
            const isEditing = editingConvId === conv.id;

            return (
              <div
                key={conv.id}
                onClick={() => !isEditing && setActiveConversationId(conv.id)}
                className={`group relative flex items-center justify-between rounded-xl px-3 py-2.5 text-xs transition cursor-pointer ${
                  isActive
                    ? 'border border-indigo-500/30 bg-indigo-500/15 text-white font-medium shadow-sm'
                    : 'text-slate-400 hover:border hover:border-slate-800/80 hover:bg-slate-900/60 hover:text-slate-200'
                }`}
              >
                {isEditing ? (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      handleSaveRename(conv.id);
                    }}
                    className="flex w-full items-center gap-1"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      type="text"
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      autoFocus
                      onBlur={() => handleSaveRename(conv.id)}
                      className="h-6 w-full rounded bg-slate-900 px-1.5 text-xs text-white border border-indigo-500/50 focus:outline-none"
                    />
                  </form>
                ) : (
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <MessageSquare className={`h-3.5 w-3.5 shrink-0 ${isActive ? 'text-indigo-400' : 'text-slate-500'}`} />
                    <span className="truncate">{conv.title}</span>
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
                      className="rounded p-1 text-slate-400 hover:text-slate-200 hover:bg-slate-800"
                      title="Rename"
                    >
                      <Edit2 className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteConversation(conv.id);
                      }}
                      className="rounded p-1 text-slate-400 hover:text-rose-400 hover:bg-slate-800"
                      title="Delete"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                )}
              </div>
            );
          })}
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
                  {currentConversation?.title || 'Active Session'}
                </span>
                <span className="rounded bg-indigo-500/10 px-2 py-0.5 font-mono text-[10px] text-indigo-300 hidden sm:inline">
                  {activeModel}
                </span>
              </div>
              <p className="text-[11px] text-slate-500">
                {messages.length} message{messages.length === 1 ? '' : 's'} · Simulated SSE streaming
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {messages.length > 0 && (
              <button
                type="button"
                onClick={clearActiveConversation}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-1 text-xs text-slate-400 transition hover:border-rose-500/40 hover:bg-rose-500/10 hover:text-rose-300"
                title="Clear current messages"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Clear</span>
              </button>
            )}
          </div>
        </div>

        {/* Message Thread Scroll Area */}
        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="relative flex-1 overflow-y-auto px-4 py-6 sm:px-6 space-y-6"
        >
          {messages.length === 0 ? (
            /* Welcome Screen / Empty State */
            <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center py-6 text-center">
              <div className="relative mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 via-violet-600 to-cyan-500 shadow-xl shadow-indigo-500/20">
                <Sparkles className="h-8 w-8 text-white" />
              </div>

              <h2 className="text-xl font-bold tracking-tight text-white sm:text-2xl">
                ASTA Desktop Conversational Co-Pilot
              </h2>
              <p className="mt-2 max-w-md text-xs sm:text-sm text-slate-400 leading-relaxed">
                Interact with ASTA for multi-turn reasoning, project analysis, tool execution, and
                long-term memory query.
              </p>

              {/* Starter Prompt Cards */}
              <div className="mt-6 grid w-full grid-cols-1 gap-3 sm:grid-cols-2 text-left">
                {STARTER_PROMPTS.map((item) => (
                  <button
                    key={item.title}
                    type="button"
                    onClick={() => void sendMessage(item.prompt)}
                    className="group flex flex-col justify-between rounded-2xl border border-slate-800 bg-slate-900/40 p-4 transition hover:border-indigo-500/40 hover:bg-slate-900/80 hover:shadow-lg hover:shadow-indigo-500/5"
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
                      <p className="mt-1 text-xs text-slate-500 leading-relaxed">
                        {item.description}
                      </p>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            /* Messages list */
            messages.map((msg, idx) => {
              const isUser = msg.role === 'user';
              const isCurrentlyStreaming = isStreaming && streamingMessageId === msg.id;

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
                    className={`group flex max-w-[85%] sm:max-w-[80%] flex-col ${
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
                          {msg.toolExecutions.map((tool, tIdx) => (
                            <div
                              key={tIdx}
                              className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/80 px-3 py-1.5 text-xs font-mono text-slate-300"
                            >
                              <div className="flex items-center gap-2">
                                <Wrench className="h-3.5 w-3.5 text-cyan-400" />
                                <span className="font-semibold">{tool.tool}</span>
                              </div>
                              <div className="flex items-center gap-2 text-[10px] text-slate-500">
                                <span className="text-emerald-400">EXECUTED</span>
                                <span>·</span>
                                <span>{tool.duration_ms}ms</span>
                              </div>
                            </div>
                          ))}
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
                        {isUser ? user.name : msg.model || 'ASTA'} · {msg.timestamp}
                      </span>

                      {!isUser && !isCurrentlyStreaming && msg.content && (
                        <div className="flex items-center gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
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

                          {idx === messages.length - 1 && (
                            <button
                              type="button"
                              onClick={() => void regenerateLastMessage()}
                              className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                              title="Regenerate"
                            >
                              <RotateCcw className="h-3.5 w-3.5" />
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
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
                    disabled={!input.trim()}
                    className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-md shadow-indigo-600/25 transition hover:bg-indigo-500 disabled:opacity-40"
                    title="Send message (Enter)"
                  >
                    <Send className="h-3.5 w-3.5" />
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
                  Simulated Local Environment
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
