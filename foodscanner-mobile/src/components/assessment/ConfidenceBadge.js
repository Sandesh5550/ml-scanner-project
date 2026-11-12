import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { getConfidenceMeta } from '../../utils/assessment';
import { PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function ConfidenceBadge({
  confidence = null,
  showDescription = true,
  compact = false,
}) {
  const meta = getConfidenceMeta(confidence);

  // Gracefully handle missing, null, or unrecognized confidence
  if (!meta) return null;

  return (
    <View style={styles.container}>
      <View
        style={[
          styles.badge,
          compact ? styles.badgeCompact : null,
          { backgroundColor: meta.bg, borderColor: meta.border },
        ]}
      >
        <Ionicons
          name={meta.icon || 'information-circle'}
          size={compact ? 12 : 14}
          color={meta.fg}
          style={{ marginRight: 5 }}
        />
        <Text style={[styles.badgeText, compact ? styles.badgeTextCompact : null, { color: meta.fg }]}>
          {compact ? meta.shortLabel : meta.label}
        </Text>
      </View>
      {showDescription && meta.description ? (
        <Text style={styles.description}>{meta.description}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    marginVertical: 4,
  },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: PREMIUM_RADIUS.pill,
    borderWidth: 1,
  },
  badgeCompact: {
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.3,
    textTransform: 'uppercase',
  },
  badgeTextCompact: {
    fontSize: 10,
    fontWeight: '700',
  },
  description: {
    fontSize: 12,
    fontWeight: '500',
    color: '#5F665E',
    marginTop: 4,
    textAlign: 'center',
  },
});
