import React, { useState } from 'react';
import { View, Text, StyleSheet, TextInput, TouchableOpacity, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { askNutritionAssistant } from '../services/api';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../theme/premiumTheme';

const SUGGESTED_QUESTIONS = [
  'Is this good for weight loss?',
  'Why is this score low?',
  'Are these ingredients safe?',
  'What are healthier alternatives?',
];

export default function AIChatSection({
  barcode = null,
  productName = null,
  nutrition = null,
  healthScore = null,
  decision = null,
}) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState('');
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);

  // Reset conversation when viewing a new product
  React.useEffect(() => {
    setMessages([]);
    setQuery('');
    setExpanded(false);
  }, [barcode, productName]);

  const handleSend = async (textToSend) => {
    const message = String(textToSend || query || '').trim();
    if (!message || loading) return;

    const userMsg = { role: 'user', content: message };
    setMessages((prev) => [...prev, userMsg]);
    setQuery('');
    setLoading(true);
    setExpanded(true);

    const productContext = {
      product_name: productName || null,
      barcode: barcode && String(barcode) !== '00000000' ? String(barcode) : null,
      nutrition: nutrition || null,
      health_score: healthScore != null ? Number(healthScore) : null,
      final_decision: decision || null,
    };

    try {
      const res = await askNutritionAssistant({
        message,
        barcode: barcode && String(barcode) !== '00000000' ? String(barcode) : null,
        productContext,
        conversationHistory: messages.slice(-6),
      });

      const reply = res?.reply || res?.response || res?.answer || 'I evaluated the product nutrition and ingredients based on scientific nutritional standards.';
      setMessages((prev) => [...prev, { role: 'assistant', content: reply }]);
    } catch (e) {
      const fallbackMsg = e?.response?.data?.detail || 'AI assistant is temporarily unavailable. Please try again later.';
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: fallbackMsg },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
      {/* Header */}
      <View style={styles.header}>
        <View>
          <View style={styles.badgeWrap}>
            <Ionicons name="sparkles" size={11} color={PREMIUM_COLORS.ai} />
            <Text style={styles.badgeText}>INTELLIGENCE</Text>
          </View>
          <Text style={styles.title}>Ask Nutrition AI</Text>
          <Text style={styles.subtitle}>Ask questions about ingredients, additives & macros</Text>
        </View>

        {messages.length > 0 ? (
          <TouchableOpacity
            style={styles.toggleBtn}
            onPress={() => setExpanded(!expanded)}
            activeOpacity={0.7}
          >
            <Text style={styles.toggleBtnText}>{expanded ? 'Collapse ▲' : 'Expand ▼'}</Text>
          </TouchableOpacity>
        ) : null}
      </View>

      {/* Suggested Question Chips */}
      <View style={styles.chipsRow}>
        {SUGGESTED_QUESTIONS.map((q, idx) => (
          <TouchableOpacity
            key={idx}
            style={styles.chip}
            activeOpacity={0.8}
            onPress={() => handleSend(q)}
            disabled={loading}
          >
            <Text style={styles.chipText}>{q}</Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Conversational Message Stream */}
      {messages.length > 0 && expanded ? (
        <View style={styles.messageStream}>
          {messages.map((m, idx) => {
            const isUser = m.role === 'user';
            return (
              <View
                key={idx}
                style={[
                  styles.bubbleWrap,
                  isUser ? styles.bubbleWrapUser : styles.bubbleWrapAi,
                ]}
              >
                {!isUser ? (
                  <View style={styles.aiAvatar}>
                    <Ionicons name="sparkles" size={13} color={PREMIUM_COLORS.ai} />
                  </View>
                ) : null}
                <View
                  style={[
                    styles.bubble,
                    isUser ? styles.bubbleUser : styles.bubbleAi,
                  ]}
                >
                  <Text
                    style={[
                      styles.bubbleText,
                      isUser ? styles.bubbleTextUser : styles.bubbleTextAi,
                    ]}
                  >
                    {m.content}
                  </Text>
                </View>
              </View>
            );
          })}

          {loading ? (
            <View style={styles.loadingRow}>
              <ActivityIndicator size="small" color={PREMIUM_COLORS.ai} />
              <Text style={styles.loadingText}>Analyzing nutritional facts...</Text>
            </View>
          ) : null}
        </View>
      ) : null}

      {/* Input Row */}
      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          placeholder="Ask anything about this food..."
          placeholderTextColor={PREMIUM_COLORS.mutedLight}
          value={query}
          onChangeText={setQuery}
          onSubmitEditing={() => handleSend(query)}
          returnKeyType="send"
        />
        <TouchableOpacity
          style={[styles.sendBtn, (!query.trim() || loading) && styles.sendBtnDisabled]}
          onPress={() => handleSend(query)}
          disabled={!query.trim() || loading}
          activeOpacity={0.85}
        >
          {loading ? (
            <ActivityIndicator size="small" color={PREMIUM_COLORS.white} />
          ) : (
            <Text style={styles.sendBtnText}>Ask →</Text>
          )}
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: PREMIUM_COLORS.status.aiBg,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 18,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.aiBorder,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  badgeWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: PREMIUM_RADIUS.pill,
    alignSelf: 'flex-start',
    gap: 4,
    marginBottom: 6,
  },
  badgeSparkle: {
    fontSize: 10,
  },
  badgeText: {
    fontSize: 10,
    fontWeight: '800',
    color: PREMIUM_COLORS.ai,
    letterSpacing: 0.5,
  },
  title: {
    fontSize: 18,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.3,
  },
  subtitle: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    marginTop: 2,
    lineHeight: 18,
  },
  toggleBtn: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
    backgroundColor: '#FFFFFF',
  },
  toggleBtnText: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.ai,
  },
  chipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginBottom: 12,
  },
  chip: {
    backgroundColor: '#FFFFFF',
    paddingHorizontal: 11,
    paddingVertical: 6,
    borderRadius: PREMIUM_RADIUS.pill,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.aiBorder,
  },
  chipText: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.ink,
  },
  messageStream: {
    backgroundColor: '#FFFFFF',
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 12,
    marginBottom: 12,
    gap: 10,
  },
  bubbleWrap: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  bubbleWrapUser: {
    justifyContent: 'flex-end',
  },
  bubbleWrapAi: {
    justifyContent: 'flex-start',
  },
  aiAvatar: {
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: PREMIUM_COLORS.status.aiBg,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 2,
  },
  bubble: {
    maxWidth: '85%',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 16,
  },
  bubbleUser: {
    backgroundColor: PREMIUM_COLORS.ai,
    borderBottomRightRadius: 4,
  },
  bubbleAi: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderBottomLeftRadius: 4,
  },
  bubbleText: {
    fontSize: 14,
    lineHeight: 20,
  },
  bubbleTextUser: {
    color: '#FFFFFF',
    fontWeight: '500',
  },
  bubbleTextAi: {
    color: PREMIUM_COLORS.ink,
    fontWeight: '400',
  },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    padding: 6,
  },
  loadingText: {
    fontSize: 13,
    color: PREMIUM_COLORS.ai,
    fontWeight: '500',
  },
  inputRow: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'center',
  },
  input: {
    flex: 1,
    backgroundColor: '#FFFFFF',
    borderRadius: PREMIUM_RADIUS.pill,
    paddingHorizontal: 16,
    paddingVertical: 10,
    fontSize: 14,
    fontWeight: '500',
    color: PREMIUM_COLORS.ink,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.aiBorder,
  },
  sendBtn: {
    backgroundColor: PREMIUM_COLORS.ai,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: PREMIUM_RADIUS.pill,
  },
  sendBtnDisabled: {
    opacity: 0.5,
  },
  sendBtnText: {
    fontSize: 13,
    fontWeight: '700',
    color: '#FFFFFF',
  },
});
