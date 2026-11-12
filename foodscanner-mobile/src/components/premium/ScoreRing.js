import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Animated } from 'react-native';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function ScoreRing({
  score = null,
  decision = null, // SAFE, MODERATE, AVOID
  size = 140,
  animated = true,
  showDecision = true,
}) {
  const isScoreNumeric = score !== null && score !== undefined && !isNaN(score) && Number.isFinite(Number(score));
  const numericScore = isScoreNumeric ? Math.max(0, Math.min(100, Math.round(Number(score)))) : null;
  const animValue = useRef(new Animated.Value(0)).current;
  const [displayNumber, setDisplayNumber] = useState(isScoreNumeric ? numericScore : '—');

  const getMeta = () => {
    const d = String(decision || '').toUpperCase();
    if (d === 'SAFE') {
      return {
        bg: PREMIUM_COLORS.status.safeBg,
        fg: PREMIUM_COLORS.status.safe,
        label: 'SAFE',
        ringColor: PREMIUM_COLORS.primary,
        sub: 'Recommended Choice',
      };
    }
    if (d === 'MODERATE') {
      return {
        bg: PREMIUM_COLORS.status.moderateBg,
        fg: PREMIUM_COLORS.status.moderate,
        label: 'MODERATE',
        ringColor: PREMIUM_COLORS.status.moderate,
        sub: 'Consume In Moderation',
      };
    }
    if (d === 'AVOID') {
      return {
        bg: PREMIUM_COLORS.status.avoidBg,
        fg: PREMIUM_COLORS.status.avoid,
        label: 'AVOID',
        ringColor: PREMIUM_COLORS.status.avoid,
        sub: 'High Risk Profile',
      };
    }
    return {
      bg: '#F1F5F9',
      fg: '#64748B',
      label: 'UNASSESSED',
      ringColor: '#CBD5E1',
      sub: 'Score Not Available',
    };
  };

  const meta = getMeta();

  useEffect(() => {
    if (!isScoreNumeric) {
      setDisplayNumber('—');
      return;
    }
    if (!animated) {
      setDisplayNumber(numericScore);
      return;
    }

    const listenerId = animValue.addListener(({ value }) => {
      setDisplayNumber(Math.round(value));
    });

    Animated.timing(animValue, {
      toValue: numericScore,
      duration: 1000,
      useNativeDriver: false,
    }).start();

    return () => {
      animValue.removeListener(listenerId);
    };
  }, [numericScore, animated, animValue, isScoreNumeric]);

  return (
    <View style={styles.container}>
      <View
        style={[
          styles.outerRing,
          {
            width: size,
            height: size,
            borderRadius: size / 2,
            borderColor: meta.ringColor,
          },
          PREMIUM_SHADOWS.md,
        ]}
      >
        <View
          style={[
            styles.innerCircle,
            {
              width: size - 14,
              height: size - 14,
              borderRadius: (size - 14) / 2,
              backgroundColor: PREMIUM_COLORS.card,
            },
          ]}
        >
          <Text style={[styles.scoreValue, { color: isScoreNumeric ? meta.fg : '#64748B' }]}>{displayNumber}</Text>
          <Text style={styles.scoreLabel}>HEALTH SCORE</Text>
        </View>
      </View>

      {showDecision && decision ? (
        <>
          <View style={[styles.statusBadge, { backgroundColor: meta.bg }]}>
            <View style={[styles.statusDot, { backgroundColor: meta.fg }]} />
            <Text style={[styles.statusText, { color: meta.fg }]}>{meta.label}</Text>
          </View>
          {meta.sub ? <Text style={styles.statusSub}>{meta.sub}</Text> : null}
        </>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    justifyContent: 'center',
    marginVertical: 12,
  },
  outerRing: {
    borderWidth: 6,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: PREMIUM_COLORS.bgAlt,
  },
  innerCircle: {
    alignItems: 'center',
    justifyContent: 'center',
    ...PREMIUM_SHADOWS.sm,
  },
  scoreValue: {
    fontSize: 42,
    fontWeight: '800',
    letterSpacing: -1,
  },
  scoreLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: PREMIUM_COLORS.secondary,
    letterSpacing: 0.8,
    marginTop: 2,
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: PREMIUM_RADIUS.pill,
    marginTop: 14,
  },
  statusDot: {
    width: 7,
    height: 7,
    borderRadius: 3.5,
  },
  statusText: {
    fontSize: 12,
    fontWeight: '800',
    letterSpacing: 0.6,
  },
  statusSub: {
    fontSize: 12,
    fontWeight: '500',
    color: PREMIUM_COLORS.secondary,
    marginTop: 4,
  },
});
