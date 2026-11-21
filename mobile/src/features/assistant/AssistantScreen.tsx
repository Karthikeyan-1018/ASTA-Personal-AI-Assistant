import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  ScrollView,
  Pressable,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { useTheme } from '../../theme/themeContext';
import { useTasks } from '../../hooks/useTasksContext';
import { ChatMessage } from '../../types/assistant';
import { assistantService } from '../../services/assistantService';
import { Icon } from '../../components/common/Icon';

export const AssistantScreen: React.FC = () => {
  const { colors, borderRadius, spacing } = useTheme();
  const { tasks } = useTasks();

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'init-1',
      sender: 'assistant',
      text: '### ✦ Welcome to ASTA Companion\n\nI am your mobile intelligence companion. I monitor your local tasks, track upcoming deadlines, and optimize your study and focus sessions.\n\n*How can I help you organize today?*',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      suggestedPrompts: [
        'What tasks are due today?',
        'Show urgent deadlines',
        'Recommend a focus session',
      ],
    },
  ]);
  const [inputText, setInputText] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [isListening, setIsListening] = useState(false);

  const scrollRef = useRef<any>(null);

  useEffect(() => {
    scrollRef.current?.scrollToEnd({ animated: true });
  }, [messages, isTyping]);

  const handleSend = async (textToSend?: string) => {
    const query = (textToSend || inputText).trim();
    if (!query) return;

    const userMsg: ChatMessage = {
      id: `usr-${Date.now()}`,
      sender: 'user',
      text: query,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputText('');
    setIsTyping(true);

    try {
      const response = await assistantService.sendMessage(query, tasks);
      const assistantMsg: ChatMessage = {
        id: `ast-${Date.now()}`,
        sender: 'assistant',
        text: response.message,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        suggestedPrompts: response.suggestedPrompts,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch {
      // fallback
    } finally {
      setIsTyping(false);
    }
  };

  const handleMicToggle = () => {
    setIsListening((prev) => !prev);
    if (!isListening) {
      setTimeout(() => {
        setInputText('What tasks are due today?');
        setIsListening(false);
      }, 1500);
    }
  };

  // Render text with basic markdown formatting (bold, headings, bullet points)
  const renderFormattedText = (raw: string, isUser: boolean) => {
    const lines = raw.split('\n');
    return lines.map((line, idx) => {
      if (line.startsWith('### ')) {
        return (
          <Text
            key={idx}
            style={[
              styles.mdHeading,
              { color: isUser ? '#FFFFFF' : colors.textPrimary },
            ]}
          >
            {line.replace('### ', '')}
          </Text>
        );
      }
      if (line.startsWith('• ') || line.startsWith('- ')) {
        return (
          <Text
            key={idx}
            style={[
              styles.mdBullet,
              { color: isUser ? '#FFFFFF' : colors.textSecondary },
            ]}
          >
            {line}
          </Text>
        );
      }
      if (line.startsWith('> ')) {
        return (
          <View
            key={idx}
            style={[
              styles.mdQuote,
              {
                borderLeftColor: colors.accent,
                backgroundColor: colors.surfaceSubtle,
              },
            ]}
          >
            <Text style={{ color: colors.textPrimary, fontStyle: 'italic', fontSize: 13 }}>
              {line.replace('> ', '')}
            </Text>
          </View>
        );
      }
      return (
        <Text
          key={idx}
          style={[
            styles.mdBody,
            { color: isUser ? '#FFFFFF' : colors.textPrimary },
          ]}
        >
          {line.replace(/\*\*(.*?)\*\*/g, '$1').replace(/\*(.*?)\*/g, '$1')}
        </Text>
      );
    });
  };

  return (
    <KeyboardAvoidingView
      style={[styles.container, { backgroundColor: colors.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={Platform.OS === 'ios' ? 90 : 0}
    >
      {/* Header */}
      <View style={[styles.header, { paddingHorizontal: spacing.lg, paddingTop: spacing.md }]}>
        <View style={styles.brandRow}>
          <View style={[styles.sparkleIcon, { backgroundColor: colors.accent, borderRadius: borderRadius.xs }]}>
            <Text style={styles.sparkleIconText}>✦</Text>
          </View>
          <View>
            <Text style={[styles.screenTitle, { color: colors.textPrimary }]}>ASTA Assistant</Text>
            <Text style={[styles.statusSubtitle, { color: colors.accentLight }]}>
              Local Companion AI Interface
            </Text>
          </View>
        </View>
      </View>

      {/* Messages Scroll Area */}
      <ScrollView
        ref={scrollRef}
        style={styles.messageList}
        contentContainerStyle={[styles.messageListContent, { paddingHorizontal: spacing.lg, paddingBottom: 110 }]}
        showsVerticalScrollIndicator={false}
      >
        {messages.map((msg) => {
          const isUser = msg.sender === 'user';
          return (
            <View key={msg.id} style={styles.messageWrap}>
              <View
                style={[
                  styles.messageBubble,
                  isUser
                    ? [styles.userBubble, { backgroundColor: colors.accent, borderRadius: borderRadius.lg }]
                    : [
                        styles.assistantBubble,
                        {
                          backgroundColor: colors.surface,
                          borderColor: colors.cardBorder,
                          borderRadius: borderRadius.lg,
                        },
                      ],
                ]}
              >
                {!isUser ? (
                  <View style={styles.assistantBadge}>
                    <Text style={[styles.assistantTag, { color: colors.accentLight }]}>✦ ASTA</Text>
                  </View>
                ) : null}

                {renderFormattedText(msg.text, isUser)}

                <Text
                  style={[
                    styles.timestampText,
                    { color: isUser ? 'rgba(255,255,255,0.7)' : colors.textMuted },
                  ]}
                >
                  {msg.timestamp}
                </Text>
              </View>

              {/* Suggested Prompt Chips */}
              {msg.suggestedPrompts && (
                <View style={styles.promptChipsRow}>
                  {msg.suggestedPrompts.map((prompt) => (
                    <Pressable
                      key={prompt}
                      onPress={() => handleSend(prompt)}
                      style={[
                        styles.promptChip,
                        {
                          backgroundColor: colors.surfaceSubtle,
                          borderColor: colors.border,
                          borderRadius: borderRadius.sm,
                        },
                      ]}
                    >
                      <Text style={[styles.promptChipText, { color: colors.accentLight }]}>
                        {prompt} ›
                      </Text>
                    </Pressable>
                  ))}
                </View>
              )}
            </View>
          );
        })}

        {/* Typing indicator */}
        {isTyping && (
          <View
            style={[
              styles.typingIndicator,
              { backgroundColor: colors.surface, borderColor: colors.cardBorder, borderRadius: borderRadius.md },
            ]}
          >
            <Text style={[styles.typingText, { color: colors.textSecondary }]}>
              ASTA is analyzing your tasks…
            </Text>
          </View>
        )}
      </ScrollView>

      {/* Input Bar */}
      <View
        style={[
          styles.inputContainer,
          {
            backgroundColor: colors.surface,
            borderTopColor: colors.border,
            paddingHorizontal: spacing.lg,
          },
        ]}
      >
        {isListening ? (
          <View style={[styles.listeningBanner, { backgroundColor: colors.surfaceSubtle, borderRadius: borderRadius.sm }]}>
            <Text style={[styles.listeningText, { color: colors.accentLight }]}>
              🎤 Listening… (Mock voice input)
            </Text>
          </View>
        ) : null}

        <View style={styles.inputRow}>
          {/* Microphone button */}
          <Pressable
            onPress={handleMicToggle}
            style={[
              styles.micButton,
              {
                backgroundColor: isListening ? colors.accent : colors.surfaceSubtle,
                borderRadius: borderRadius.sm,
              },
            ]}
          >
            <Icon name="mic" size={16} color={isListening ? '#FFFFFF' : colors.textSecondary} />
          </Pressable>

          {/* Text Input */}
          <TextInput
            value={inputText}
            onChangeText={setInputText}
            placeholder="Ask ASTA anything about your tasks…"
            placeholderTextColor={colors.textMuted}
            style={[
              styles.textInput,
              {
                backgroundColor: colors.inputBackground,
                borderColor: colors.inputBorder,
                color: colors.textPrimary,
                borderRadius: borderRadius.md,
              },
            ]}
            onSubmitEditing={() => handleSend()}
            returnKeyType="send"
          />

          {/* Send Button */}
          <Pressable
            onPress={() => handleSend()}
            disabled={!inputText.trim()}
            style={[
              styles.sendButton,
              {
                backgroundColor: inputText.trim() ? colors.accent : colors.surfaceSubtle,
                borderRadius: borderRadius.sm,
              },
            ]}
          >
            <Icon
              name="send"
              size={15}
              color={inputText.trim() ? '#FFFFFF' : colors.textMuted}
            />
          </Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  header: {
    marginBottom: 8,
  },
  brandRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  sparkleIcon: {
    width: 24,
    height: 24,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sparkleIconText: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '900',
  },
  screenTitle: {
    fontSize: 22,
    fontWeight: '900',
    letterSpacing: -0.4,
  },
  statusSubtitle: {
    fontSize: 11,
    fontWeight: '700',
  },
  messageList: {
    flex: 1,
  },
  messageListContent: {
    paddingTop: 8,
    gap: 14,
  },
  messageWrap: {
    width: '100%',
  },
  messageBubble: {
    padding: 14,
    maxWidth: '88%',
  },
  userBubble: {
    alignSelf: 'flex-end',
  },
  assistantBubble: {
    alignSelf: 'flex-start',
    borderWidth: 1,
  },
  assistantBadge: {
    marginBottom: 6,
  },
  assistantTag: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1,
  },
  mdHeading: {
    fontSize: 15,
    fontWeight: '800',
    marginBottom: 6,
  },
  mdBullet: {
    fontSize: 13,
    lineHeight: 18,
    marginVertical: 2,
  },
  mdBody: {
    fontSize: 13,
    lineHeight: 19,
    marginVertical: 2,
  },
  mdQuote: {
    borderLeftWidth: 3,
    paddingLeft: 8,
    paddingVertical: 4,
    marginVertical: 6,
  },
  timestampText: {
    fontSize: 9,
    marginTop: 6,
    alignSelf: 'flex-end',
  },
  promptChipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginTop: 8,
    maxWidth: '90%',
  },
  promptChip: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderWidth: 1,
  },
  promptChipText: {
    fontSize: 11,
    fontWeight: '700',
  },
  typingIndicator: {
    padding: 10,
    borderWidth: 1,
    alignSelf: 'flex-start',
  },
  typingText: {
    fontSize: 12,
    fontStyle: 'italic',
  },
  inputContainer: {
    paddingVertical: 10,
    borderTopWidth: 1,
    position: 'absolute',
    bottom: 56, // above bottom navigation tab bar
    left: 0,
    right: 0,
  },
  listeningBanner: {
    padding: 6,
    alignItems: 'center',
    marginBottom: 8,
  },
  listeningText: {
    fontSize: 11,
    fontWeight: '700',
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  micButton: {
    width: 38,
    height: 38,
    alignItems: 'center',
    justifyContent: 'center',
  },
  textInput: {
    flex: 1,
    fontSize: 13,
    paddingHorizontal: 12,
    height: 38,
    borderWidth: 1,
  },
  sendButton: {
    width: 38,
    height: 38,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
