import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { isValidScoreRange } from '../../utils/assessment';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function ScoreRangeCard({
  scoreRange = null,
  missingCount = 0,
}) {
  const hasValidRange = isValidScoreRange(scoreRange);

  let minVal = null;
  let maxVal = null;
  if (hasValidRange) {
    const rawMin = scoreRange.min_score !== undefined ? scoreRange.min_score : scoreRange.min;
    const rawMax = scoreRange.max_score !== undefined ? scoreRange.max_score : scoreRange.max;
    minVal = Math.round(Number(rawMin));
    maxVal = Math.round(Number(rawMax));
  }

  return (
    <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
      <View style={styles.headerRow}>
        <View style={styles.iconCircle}>
          <Ionicons name="options-outline" size={18} color="#B45309" />
        </View>
        <View style={styles.headerTextCol}>
          <Text style={styles.badgeText}>PARTIAL ASSESSMENT</Text>
          <Text style={styles.titleText}>
            {hasValidRange ? `Estimated score range: ${minVal}–${maxVal}` : 'Score Range Unavailable'}
          </Text>
        </View>
      </View>

      {hasValidRange ? (
        <View style={styles.rangeVisualBox}>
          <View style={styles.trackLabels}>
            <Text style={styles.trackBound}>0</Text>
            <Text style={styles.trackCurrent}>
              {minVal} — {maxVal}
            </Text>
            <Text style={styles.trackBound}>100</Text>
          </View>
          <View style={styles.trackBase}>
            <View
              style={[
                styles.trackFill,
                {
                  left: `${minVal}%`,
                  width: `${Math.max(4, maxVal - minVal)}%`,
                },
              ]}
            />
          </View>
          <View style={styles.rangeSubRow}>
            <Text style={styles.rangeFootnote}>Worst Case: {minVal}</Text>
            <Text style={styles.rangeFootnote}>Best Case: {maxVal}</Text>
          </View>
        </View>
      ) : null}

      <View style={styles.explanationBox}>
        <Ionicons name="information-circle-outline" size={15} color="#B45309" style={{ marginTop: 2, marginRight: 6 }} />
        <Text style={styles.explanationText}>
          {hasValidRange
            ? 'Some nutritional disclosures are missing. An exact single health score cannot be calculated, but the score is bounded within this range based on authoritative regulatory worst-case and best-case analysis.'
            : 'Some required nutritional disclosures are missing. An exact health score cannot be determined until complete nutrition information is disclosed.'}
        </Text>
      </View>
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
    borderColor: '#FCD34D',
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
    backgroundColor: '#FEF3C7',
    alignItems: 'center',
    justifyContent: 'center',
  },
  headerTextCol: {
    flex: 1,
  },
  badgeText: {
    fontSize: 10,
    fontWeight: '800',
    color: '#B45309',
    letterSpacing: 0.8,
  },
  titleText: {
    fontSize: 16,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginTop: 2,
  },
  rangeVisualBox: {
    marginTop: 14,
    backgroundColor: '#FFFDF5',
    padding: 12,
    borderRadius: PREMIUM_RADIUS.lg,
    borderWidth: 1,
    borderColor: '#FEF3C7',
  },
  trackLabels: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  trackBound: {
    fontSize: 11,
    fontWeight: '600',
    color: '#7C837B',
  },
  trackCurrent: {
    fontSize: 13,
    fontWeight: '800',
    color: '#B45309',
  },
  trackBase: {
    height: 10,
    backgroundColor: '#E2E8F0',
    borderRadius: 5,
    overflow: 'hidden',
    position: 'relative',
  },
  trackFill: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    backgroundColor: '#D97706',
    borderRadius: 5,
  },
  rangeSubRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 6,
  },
  rangeFootnote: {
    fontSize: 11,
    fontWeight: '500',
    color: '#7C837B',
  },
  explanationBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginTop: 12,
    backgroundColor: '#FEF3C7',
    padding: 10,
    borderRadius: PREMIUM_RADIUS.md,
  },
  explanationText: {
    flex: 1,
    fontSize: 12,
    lineHeight: 17,
    fontWeight: '500',
    color: '#78350F',
  },
});
