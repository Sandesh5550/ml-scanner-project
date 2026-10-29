import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { askNutritionAssistant, getNetworkErrorMessage } from '../services/api';
import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';

const DEFAULT_SUGGESTED_PROMPTS = [
  'What are the limits for fats and saturated fatty acids?',
  'What does INS 621 (MSG) do?',
  'How to identify added sugars on labels?',
  'Explain statutory FSSAI health claims',
];

export default function AIAssistantScreen({ route, navigation }) {
  const initialProduct = route?.params?.product || null;
  const initialProductName = route?.params?.productName || initialProduct?.product_name || initialProduct?.name || null;
  const initialBarcode = route?.params?.barcode || initialProduct?.barcode || null;
  const initialNutrition = route?.params?.nutrition || initialProduct?.nutrition || null;
  const initialHealthScore = route?.params?.healthScore || initialProduct?.health_score || null;
  const initialDecision = route?.params?.decision || initialProduct?.final_decision || null;

  const [query, setQuery] = useState('');
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);
  const [lastFailedMessage, setLastFailedMessage] = useState(null);

  const scrollViewRef = useRef(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (scrollViewRef.current) {
      setTimeout(() => {
        scrollViewRef.current?.scrollToEnd({ animated: true });
      }, 100);
    }
  }, [messages, loading]);

  const handleSend = async (textToSend) => {
    const message = String(textToSend || query || '').trim();
    if (!message || loading) return;

    const userMsg = { role: 'user', content: message, timestamp: Date.now() };
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setQuery('');
    setLoading(true);
    setErrorMessage(null);
    setLastFailedMessage(null);

    const productContext = initialProductName
      ? {
          product_name: initialProductName,
          barcode: initialBarcode && String(initialBarcode) !== '00000000' ? String(initialBarcode) : null,
          nutrition: initialNutrition,
          health_score: initialHealthScore != null ? Number(initialHealthScore) : null,
          final_decision: initialDecision,
        }
      : null;

    try {
      const res = await askNutritionAssistant({
        message,
        barcode: initialBarcode && String(initialBarcode) !== '00000000' ? String(initialBarcode) : null,
        productContext,
        conversationHistory: nextMessages.slice(-6).map((m) => ({
          role: m.role,
          content: m.content,
        })),
      });

      const reply =
        res?.answer ||
        res?.reply ||
        res?.response ||
        'I evaluated food nutrition and ingredients based on statutory scientific standards.';

      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: reply,
          sources: Array.isArray(res?.sources) ? res.sources : [],
          timestamp: Date.now(),
        },
      ]);
    } catch (e) {
      const msg = getNetworkErrorMessage(e) || e?.response?.data?.detail || 'AI assistant is temporarily unavailable. Please try again.';
      setErrorMessage(msg);
      setLastFailedMessage(message);
    } finally {
      setLoading(false);
    }
  };

  const handleRetry = () => {
    if (lastFailedMessage) {
      handleSend(lastFailedMessage);
    }
  };

  const handleClearChat = () => {
    setMessages([]);
    setErrorMessage(null);
    setLastFailedMessage(null);
  };

  return (
    <View style={styles.screen}>
      {/* 1. Header Bar */}
      <View style={[styles.header, PREMIUM_SHADOWS.sm]}>
        <TouchableOpacity
          activeOpacity={0.8}
          style={styles.backBtn}
          onPress={() => navigation.goBack()}
          accessibilityLabel="Back"
        >
          <Ionicons name="arrow-back" size={22} color={PREMIUM_COLORS.ink} />
        </TouchableOpacity>

        <View style={styles.headerTitleWrap}>
          <View style={styles.headerTitleRow}>
            <Ionicons name="sparkles" size={16} color={PREMIUM_COLORS.ai} style={{ marginRight: 6 }} />
            <Text style={styles.headerTitle}>PRAMAAN AI</Text>
          </View>
          <Text style={styles.headerSubtitle}>Verified Nutrition & Ingredient Intelligence</Text>
        </View>

        <View style={styles.headerRightActions}>
          <View style={styles.onlinePill}>
            <View style={styles.greenDot} />
            <Text style={styles.onlineText}>Online</Text>
          </View>
          {messages.length > 0 ? (
            <TouchableOpacity
              activeOpacity={0.7}
              onPress={handleClearChat}
              style={styles.clearBtn}
              accessibilityLabel="Clear chat"
            >
              <Ionicons name="trash-outline" size={18} color={PREMIUM_COLORS.secondary} />
            </TouchableOpacity>
          ) : null}
        </View>
      </View>

      {/* 2. Optional Product Context Banner */}
      {initialProductName ? (
        <View style={[styles.productBanner, PREMIUM_SHADOWS.sm]}>
          <View style={styles.productBannerIcon}>
            <Ionicons name="nutrition" size={18} color={PREMIUM_COLORS.primaryDark} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.productBannerLabel}>Active Product Context</Text>
            <Text style={styles.productBannerName} numberOfLines={1}>
              {initialProductName}
            </Text>
          </View>
          {initialHealthScore != null ? (
            <View style={styles.scoreBadge}>
              <Text style={styles.scoreBadgeText}>{initialHealthScore}/100</Text>
            </View>
          ) : null}
        </View>
      ) : null}

      {/* 3. Messages & Conversation Container */}
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 10 : 0}
      >
        <ScrollView
          ref={scrollViewRef}
          style={styles.messagesList}
          contentContainerStyle={styles.messagesContent}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
        >
          {/* Welcome / Empty State */}
          {messages.length === 0 ? (
            <View style={styles.emptyContainer}>
              <View style={[styles.emptyIconCircle, PREMIUM_SHADOWS.sm]}>
                <Ionicons name="sparkles" size={32} color={PREMIUM_COLORS.ai} />
              </View>
              <Text style={styles.emptyTitle}>Ask Anything About Your Food</Text>
              <Text style={styles.emptySub}>
                Grounded in official FSSAI, ICMR-NIN, and WHO scientific guidelines.
              </Text>

              <View style={styles.chipsSection}>
                <Text style={styles.chipsSectionTitle}>SUGGESTED QUESTIONS</Text>
                {DEFAULT_SUGGESTED_PROMPTS.map((promptText, idx) => (
                  <TouchableOpacity
                    key={idx}
                    activeOpacity={0.85}
                    style={[styles.suggestedChip, PREMIUM_SHADOWS.sm]}
                    onPress={() => handleSend(promptText)}
                  >
                    <Ionicons name="help-circle-outline" size={16} color={PREMIUM_COLORS.ai} />
                    <Text style={styles.suggestedChipText}>{promptText}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            </View>
          ) : null}

          {/* Conversation Bubbles */}
          {messages.map((item, index) => {
            const isUser = item.role === 'user';
            return (
              <View
                key={index}
                style={[
                  styles.messageRow,
                  isUser ? styles.messageRowUser : styles.messageRowAssistant,
                ]}
              >
                {!isUser ? (
                  <View style={styles.assistantAvatar}>
                    <Ionicons name="sparkles" size={14} color={PREMIUM_COLORS.ai} />
                  </View>
                ) : null}

                <View
                  style={[
                    styles.messageBubble,
                    isUser ? styles.userBubble : styles.assistantBubble,
                    PREMIUM_SHADOWS.sm,
                  ]}
                >
                  <Text style={[styles.messageText, isUser ? styles.userText : styles.assistantText]}>
                    {item.content}
                  </Text>

                  {/* Sources tag if returned by RAG */}
                  {Array.isArray(item.sources) && item.sources.length > 0 ? (
                    <View style={styles.sourcesContainer}>
                      <Text style={styles.sourcesLabel}>Sources:</Text>
                      {item.sources.map((src, sIdx) => (
                        <View key={sIdx} style={styles.sourceTag}>
                          <Text style={styles.sourceTagText} numberOfLines={1}>
                            {typeof src === 'string' ? src : src?.title || 'FSSAI Standards'}
                          </Text>
                        </View>
                      ))}
                    </View>
                  ) : null}
                </View>
              </View>
            );
          })}

          {/* Loading Indicator */}
          {loading ? (
            <View style={[styles.messageRow, styles.messageRowAssistant]}>
              <View style={styles.assistantAvatar}>
                <Ionicons name="sparkles" size={14} color={PREMIUM_COLORS.ai} />
              </View>
              <View style={[styles.messageBubble, styles.assistantBubble, styles.loadingBubble]}>
                <ActivityIndicator size="small" color={PREMIUM_COLORS.ai} />
                <Text style={styles.loadingBubbleText}>Consulting nutrition guidelines...</Text>
              </View>
            </View>
          ) : null}

          {/* Error Banner */}
          {errorMessage ? (
            <View style={[styles.errorCard, PREMIUM_SHADOWS.sm]}>
              <Ionicons name="alert-circle" size={18} color={PREMIUM_COLORS.status.avoid} />
              <Text style={styles.errorText}>{errorMessage}</Text>
              {lastFailedMessage ? (
                <TouchableOpacity activeOpacity={0.8} style={styles.retryBtn} onPress={handleRetry}>
                  <Text style={styles.retryBtnText}>Retry</Text>
                </TouchableOpacity>
              ) : null}
            </View>
          ) : null}
        </ScrollView>

        {/* 4. Bottom Input Bar */}
        <View style={[styles.inputBar, PREMIUM_SHADOWS.md]}>
          <TextInput
            style={styles.textInput}
            value={query}
            onChangeText={setQuery}
            placeholder="Ask about ingredients, additives, health claims..."
            placeholderTextColor={PREMIUM_COLORS.secondary}
            multiline
            maxLength={500}
            editable={!loading}
          />
          <TouchableOpacity
            activeOpacity={0.85}
            style={[
              styles.sendBtn,
              !query.trim() || loading ? styles.sendBtnDisabled : styles.sendBtnActive,
            ]}
            onPress={() => handleSend()}
            disabled={!query.trim() || loading}
            accessibilityLabel="Send message"
          >
            <Ionicons
              name="arrow-up"
              size={20}
              color={!query.trim() || loading ? PREMIUM_COLORS.secondary : PREMIUM_COLORS.white}
            />
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: PREMIUM_COLORS.bg,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.card,
    paddingTop: Platform.OS === 'ios' ? 56 : 20,
    paddingBottom: 14,
    paddingHorizontal: 16,
    borderBottomWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  backBtn: {
    width: 38,
    height: 38,
    borderRadius: PREMIUM_RADIUS.sm,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  headerTitleWrap: {
    flex: 1,
  },
  headerTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  headerTitle: {
    fontSize: 17,
    fontWeight: '800',
    color: PREMIUM_COLORS.ink,
    letterSpacing: 0.2,
  },
  headerSubtitle: {
    fontSize: 11,
    color: PREMIUM_COLORS.secondary,
    fontWeight: '500',
    marginTop: 1,
  },
  headerRightActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  onlinePill: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.primaryLight,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
    gap: 5,
  },
  greenDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: PREMIUM_COLORS.status.safe,
  },
  onlineText: {
    fontSize: 11,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
  },
  clearBtn: {
    padding: 6,
  },

  productBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.primaryLight,
    marginHorizontal: 16,
    marginTop: 12,
    marginBottom: 4,
    padding: 12,
    borderRadius: PREMIUM_RADIUS.md,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    gap: 10,
  },
  productBannerIcon: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: PREMIUM_COLORS.white,
    alignItems: 'center',
    justifyContent: 'center',
  },
  productBannerLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  productBannerName: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  scoreBadge: {
    backgroundColor: PREMIUM_COLORS.ink,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
  },
  scoreBadgeText: {
    color: PREMIUM_COLORS.white,
    fontWeight: '800',
    fontSize: 11,
  },

  messagesList: {
    flex: 1,
  },
  messagesContent: {
    padding: 16,
    paddingBottom: 24,
  },

  emptyContainer: {
    alignItems: 'center',
    paddingTop: 36,
    paddingBottom: 20,
  },
  emptyIconCircle: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: PREMIUM_COLORS.aiBg,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 16,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  emptyTitle: {
    fontSize: 18,
    fontWeight: '800',
    color: PREMIUM_COLORS.ink,
    textAlign: 'center',
    marginBottom: 6,
  },
  emptySub: {
    fontSize: 13,
    color: PREMIUM_COLORS.secondary,
    textAlign: 'center',
    maxWidth: 290,
    lineHeight: 18,
    marginBottom: 28,
  },
  chipsSection: {
    width: '100%',
  },
  chipsSectionTitle: {
    fontSize: 11,
    fontWeight: '800',
    color: PREMIUM_COLORS.secondary,
    letterSpacing: 0.6,
    marginBottom: 10,
  },
  suggestedChip: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.card,
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: PREMIUM_RADIUS.md,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    marginBottom: 8,
    gap: 10,
  },
  suggestedChipText: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.ink,
    flex: 1,
  },

  messageRow: {
    flexDirection: 'row',
    marginBottom: 14,
    alignItems: 'flex-start',
  },
  messageRowUser: {
    justifyContent: 'flex-end',
  },
  messageRowAssistant: {
    justifyContent: 'flex-start',
  },
  assistantAvatar: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: PREMIUM_COLORS.aiBg,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 8,
    marginTop: 2,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  messageBubble: {
    maxWidth: '82%',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: PREMIUM_RADIUS.lg,
  },
  userBubble: {
    backgroundColor: PREMIUM_COLORS.ink,
    borderBottomRightRadius: 4,
  },
  assistantBubble: {
    backgroundColor: PREMIUM_COLORS.card,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    borderBottomLeftRadius: 4,
  },
  messageText: {
    fontSize: 14,
    lineHeight: 20,
  },
  userText: {
    color: PREMIUM_COLORS.white,
    fontWeight: '500',
  },
  assistantText: {
    color: PREMIUM_COLORS.ink,
    fontWeight: '500',
  },

  sourcesContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    marginTop: 8,
    paddingTop: 8,
    borderTopWidth: 1,
    borderColor: 'rgba(23, 26, 23, 0.08)',
    gap: 6,
  },
  sourcesLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: PREMIUM_COLORS.secondary,
    textTransform: 'uppercase',
  },
  sourceTag: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  sourceTagText: {
    fontSize: 10,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondaryDark,
  },

  loadingBubble: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  loadingBubbleText: {
    fontSize: 13,
    color: PREMIUM_COLORS.secondary,
    fontWeight: '500',
  },

  errorCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FEE2E2',
    borderWidth: 1,
    borderColor: '#F87171',
    borderRadius: PREMIUM_RADIUS.md,
    padding: 12,
    marginTop: 8,
    gap: 8,
  },
  errorText: {
    flex: 1,
    fontSize: 12,
    color: '#991B1B',
    fontWeight: '600',
  },
  retryBtn: {
    backgroundColor: '#DC2626',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 4,
  },
  retryBtnText: {
    color: PREMIUM_COLORS.white,
    fontSize: 11,
    fontWeight: '700',
  },

  inputBar: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.card,
    borderTopWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 10,
  },
  textInput: {
    flex: 1,
    minHeight: 40,
    maxHeight: 100,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.pill,
    paddingHorizontal: 16,
    paddingVertical: 8,
    fontSize: 14,
    color: PREMIUM_COLORS.ink,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  sendBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendBtnActive: {
    backgroundColor: PREMIUM_COLORS.ink,
  },
  sendBtnDisabled: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
});
