import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { verifyClaims } from '../services/api';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../theme/premiumTheme';

const STANDARD_FSSAI_CLAIMS = [
  'No Added Sugar',
  'Sugar Free',
  'Low Sodium',
  'High Protein',
  'High Fibre',
  'Low Fat',
  'Zero Trans Fat',
];

function _claimStatusMeta(status) {
  const s = String(status || '').toUpperCase();
  if (s === 'SUPPORTED') {
    return {
      label: 'SUPPORTED',
      bg: PREMIUM_COLORS.status.safeBg,
      fg: PREMIUM_COLORS.status.safe,
      border: PREMIUM_COLORS.status.safeBorder,
      icon: '✓',
    };
  }
  if (s === 'NOT_SUPPORTED') {
    return {
      label: 'NOT SUPPORTED',
      bg: PREMIUM_COLORS.status.avoidBg,
      fg: PREMIUM_COLORS.status.avoid,
      border: PREMIUM_COLORS.status.avoidBorder,
      icon: '✕',
    };
  }
  if (s === 'NEEDS_REVIEW') {
    return {
      label: 'NEEDS REVIEW',
      bg: PREMIUM_COLORS.status.moderateBg,
      fg: PREMIUM_COLORS.status.moderate,
      border: PREMIUM_COLORS.status.moderateBorder,
      icon: '!',
    };
  }
  return {
    label: 'INSUFFICIENT DATA',
    bg: PREMIUM_COLORS.bgAlt,
    fg: PREMIUM_COLORS.secondary,
    border: PREMIUM_COLORS.border,
    icon: '?',
  };
}

export default function ClaimVerificationCard({
  initialVerification = null,
  barcode = null,
  nutrition = null,
  ingredients = null,
  productName = null,
}) {
  const [data, setData] = useState(initialVerification);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setData(initialVerification);
    setError(null);
  }, [initialVerification, barcode, productName]);

  const results = Array.isArray(data?.results) ? data.results : [];
  const hasResults = results.length > 0;

  const handleVerify = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await verifyClaims({
        claims: STANDARD_FSSAI_CLAIMS,
        barcode: barcode && String(barcode) !== '00000000' ? String(barcode) : null,
        nutrition: nutrition || null,
        ingredients: ingredients || null,
        productName: productName || null,
      });
      setData(res);
      setExpanded(true);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Verification failed';
      setError(String(msg));
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <View style={styles.badgeWrap}>
            <Ionicons name="shield-checkmark-outline" size={12} color={PREMIUM_COLORS.primaryDark} />
            <Text style={styles.badgeText}>FSSAI AUDIT</Text>
          </View>
          <Text style={styles.title}>Health Claim Verification</Text>
          <Text style={styles.subtitle}>
            Statutory compliance check against FSSAI 2018 regulations
          </Text>
        </View>

        {hasResults ? (
          <TouchableOpacity
            style={styles.expandToggle}
            onPress={() => setExpanded(!expanded)}
            activeOpacity={0.7}
          >
            <Text style={styles.expandToggleText}>{expanded ? 'Collapse ▲' : 'Details ▼'}</Text>
          </TouchableOpacity>
        ) : null}
      </View>

      {/* Action / Trigger if not yet verified */}
      {!hasResults && (
        <View style={styles.unverifiedBox}>
          <Text style={styles.unverifiedText}>
            {'Deterministic verification of front-of-pack claims (e.g. "Sugar Free", "High Protein", "Low Sodium") against declared lab data.'}
          </Text>
          <TouchableOpacity
            style={[styles.verifyBtn, PREMIUM_SHADOWS.sm]}
            onPress={handleVerify}
            disabled={loading}
            activeOpacity={0.88}
          >
            {loading ? (
              <ActivityIndicator size="small" color={PREMIUM_COLORS.white} />
            ) : (
              <Text style={styles.verifyBtnText}>Verify 7 Core FSSAI Claims →</Text>
            )}
          </TouchableOpacity>
        </View>
      )}

      {error ? (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>⚠️ {error}</Text>
        </View>
      ) : null}

      {/* Verified Claims List */}
      {hasResults && (
        <View style={styles.resultsContainer}>
          <View style={styles.resultsSummaryRow}>
            <Text style={styles.summaryLabel}>Verified Claims</Text>
            <Text style={styles.summaryCount}>
              {results.filter((r) => String(r.status).toUpperCase() === 'SUPPORTED').length} of {results.length} supported
            </Text>
          </View>

          {results.map((item, idx) => {
            const meta = _claimStatusMeta(item.status);
            return (
              <View key={idx} style={[styles.claimItem, { borderColor: meta.border }]}>
                <View style={styles.claimTopRow}>
                  <Text style={styles.claimName}>{item.claim}</Text>
                  <View style={[styles.statusBadge, { backgroundColor: meta.bg }]}>
                    <Text style={[styles.statusIcon, { color: meta.fg }]}>{meta.icon}</Text>
                    <Text style={[styles.statusLabel, { color: meta.fg }]}>{meta.label}</Text>
                  </View>
                </View>

                {expanded && (
                  <View style={styles.claimDetails}>
                    <Text style={styles.reasonText}>{item.reason || 'Verified against nutritional criteria.'}</Text>
                    {item.regulatory_source ? (
                      <Text style={styles.sourceText}>Source: {item.regulatory_source}</Text>
                    ) : null}
                  </View>
                )}
              </View>
            );
          })}

          <Text style={styles.disclaimerText}>
            Statutory disclaimer: Rule-based assessment based on declared product data and FSSAI standards. Not a legal certification.
          </Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 18,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  headerLeft: {
    flex: 1,
  },
  badgeWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.primaryLight,
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
    color: PREMIUM_COLORS.primaryDark,
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
  expandToggle: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
    backgroundColor: PREMIUM_COLORS.bgAlt,
  },
  expandToggleText: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },
  unverifiedBox: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 14,
    marginTop: 4,
  },
  unverifiedText: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    lineHeight: 18,
    marginBottom: 12,
  },
  verifyBtn: {
    backgroundColor: PREMIUM_COLORS.primaryDark,
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: PREMIUM_RADIUS.pill,
    alignItems: 'center',
    justifyContent: 'center',
  },
  verifyBtnText: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.white,
  },
  errorBox: {
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    borderRadius: PREMIUM_RADIUS.md,
    padding: 10,
    marginTop: 10,
  },
  errorText: {
    fontSize: 13,
    color: PREMIUM_COLORS.status.avoid,
    fontWeight: '600',
  },
  resultsContainer: {
    marginTop: 6,
  },
  resultsSummaryRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  summaryLabel: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  summaryCount: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.primaryDark,
  },
  claimItem: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.md,
    padding: 12,
    marginBottom: 8,
    borderWidth: 1,
  },
  claimTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  claimName: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: PREMIUM_RADIUS.pill,
    gap: 4,
  },
  statusIcon: {
    fontSize: 11,
    fontWeight: '800',
  },
  statusLabel: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.3,
  },
  claimDetails: {
    marginTop: 8,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: PREMIUM_COLORS.divider,
  },
  reasonText: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    lineHeight: 18,
  },
  sourceText: {
    fontSize: 12,
    fontWeight: '500',
    color: PREMIUM_COLORS.muted,
    marginTop: 4,
  },
  disclaimerText: {
    fontSize: 12,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    marginTop: 10,
    lineHeight: 16,
  },
});
