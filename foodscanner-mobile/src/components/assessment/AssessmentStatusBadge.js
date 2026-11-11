import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { getAssessmentStatusMeta } from '../../utils/assessment';
import { PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function AssessmentStatusBadge({
  status = null,
  showDescription = false,
  compact = false,
}) {
  const meta = getAssessmentStatusMeta(status);

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
          name={meta.icon || 'shield-outline'}
          size={compact ? 12 : 14}
          color={meta.fg}
          style={{ marginRight: 6 }}
        />
        <Text style={[styles.badgeText, compact ? styles.badgeTextCompact : null, { color: meta.fg }]}>
          {meta.label}
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
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: PREMIUM_RADIUS.pill,
    borderWidth: 1,
  },
  badgeCompact: {
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  badgeText: {
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
  badgeTextCompact: {
    fontSize: 11,
    fontWeight: '600',
  },
  description: {
    fontSize: 12,
    fontWeight: '400',
    color: '#5F665E',
    marginTop: 4,
    textAlign: 'center',
  },
});
