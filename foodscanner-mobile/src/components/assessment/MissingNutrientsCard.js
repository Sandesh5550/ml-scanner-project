import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { getMissingMandatoryNutrients, getMandatoryNutrientDisplayName } from '../../utils/assessment';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function MissingNutrientsCard({
  nutrition = {},
  missingList = null,
}) {
  const missing = getMissingMandatoryNutrients(nutrition, missingList);

  if (!missing || missing.length === 0) {
    return null;
  }

  return (
    <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
      <View style={styles.headerRow}>
        <Ionicons name="document-text-outline" size={16} color="#B45309" />
        <Text style={styles.headerTitle}>Missing Mandatory Nutritional Disclosures</Text>
      </View>

      <Text style={styles.introText}>
        The following standard nutritional dimensions were not disclosed on packaging or in database records:
      </Text>

      <View style={styles.chipsRow}>
        {missing.map((key) => (
          <View key={key} style={styles.chip}>
            <Ionicons name="close-circle-outline" size={13} color="#B45309" style={{ marginRight: 4 }} />
            <Text style={styles.chipText}>{getMandatoryNutrientDisplayName(key)}</Text>
            <Text style={styles.chipStatus}>Not disclosed</Text>
          </View>
        ))}
      </View>

      <Text style={styles.policyFootnote}>
        PRAMAAN requires explicit disclosure of energy, total sugars, total fat, and sodium/salt to calculate an authoritative health score, in alignment with core FSSAI nutritional declaration requirements.
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
    borderColor: '#FDE68A',
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 6,
  },
  headerTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#92400E',
  },
  introText: {
    fontSize: 12,
    lineHeight: 17,
    color: '#5F665E',
    marginBottom: 10,
  },
  chipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 10,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FEF3C7',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: PREMIUM_RADIUS.pill,
    borderWidth: 1,
    borderColor: '#FCD34D',
    gap: 4,
  },
  chipText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#78350F',
  },
  chipStatus: {
    fontSize: 10,
    fontWeight: '600',
    color: '#B45309',
    backgroundColor: '#FDE68A',
    paddingHorizontal: 5,
    paddingVertical: 1,
    borderRadius: 4,
  },
  policyFootnote: {
    fontSize: 11,
    lineHeight: 15,
    fontWeight: '500',
    color: '#7C837B',
  },
});
