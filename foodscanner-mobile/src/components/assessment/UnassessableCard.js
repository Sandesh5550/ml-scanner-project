import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function UnassessableCard() {
  return (
    <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
      <View style={styles.headerRow}>
        <View style={styles.iconCircle}>
          <Ionicons name="help-buoy-outline" size={20} color="#475569" />
        </View>
        <View style={styles.headerTextCol}>
          <Text style={styles.badgeText}>DATA LIMITATION</Text>
          <Text style={styles.titleText}>Assessment Unavailable</Text>
        </View>
      </View>

      <View style={styles.explanationBox}>
        <Ionicons name="alert-circle-outline" size={16} color="#475569" style={{ marginTop: 2, marginRight: 6 }} />
        <Text style={styles.explanationText}>
          Insufficient nutritional information prevents a reliable health assessment. Key mandatory disclosures (such as calories, sugars, fat, or salt) were not found for this product.
        </Text>
      </View>

      <Text style={styles.footnote}>
        Available product information and disclosures are presented below for transparency. No health score or safety rating has been fabricated.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 16,
    marginVertical: 10,
    borderWidth: 1,
    borderColor: '#CBD5E1',
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  iconCircle: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: '#F1F5F9',
    alignItems: 'center',
    justifyContent: 'center',
  },
  headerTextCol: {
    flex: 1,
  },
  badgeText: {
    fontSize: 10,
    fontWeight: '800',
    color: '#64748B',
    letterSpacing: 0.8,
  },
  titleText: {
    fontSize: 16,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginTop: 2,
  },
  explanationBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginTop: 12,
    backgroundColor: '#F1F5F9',
    padding: 12,
    borderRadius: PREMIUM_RADIUS.md,
  },
  explanationText: {
    flex: 1,
    fontSize: 12,
    lineHeight: 18,
    fontWeight: '500',
    color: '#334155',
  },
  footnote: {
    fontSize: 11,
    fontWeight: '500',
    color: '#64748B',
    marginTop: 10,
    lineHeight: 16,
  },
});
