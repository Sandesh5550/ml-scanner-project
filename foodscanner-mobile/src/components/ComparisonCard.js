import React, { useState } from 'react';
import { View, Text, StyleSheet, TextInput, TouchableOpacity, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { compareProducts } from '../services/api';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../theme/premiumTheme';
import { formatNutritionValue } from '../utils/formatters';

const COMPARE_SUGGESTIONS = ['Parle-G', 'Maggi', 'Kurkure', "Lay's", 'Amul Butter'];

function _scorePill(score) {
  const s = Number(score);
  if (!Number.isFinite(s)) return { text: 'N/A', bg: PREMIUM_COLORS.bgAlt, fg: PREMIUM_COLORS.secondary };
  if (s >= 70) return { text: `${s}`, bg: PREMIUM_COLORS.status.safeBg, fg: PREMIUM_COLORS.status.safe };
  if (s >= 45) return { text: `${s}`, bg: PREMIUM_COLORS.status.moderateBg, fg: PREMIUM_COLORS.status.moderate };
  return { text: `${s}`, bg: PREMIUM_COLORS.status.avoidBg, fg: PREMIUM_COLORS.status.avoid };
}

export default function ComparisonCard({ currentProduct = null, alternatives = [] }) {
  const [expanded, setExpanded] = useState(false);
  const [targetQuery, setTargetQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [comparison, setComparison] = useState(null);
  const [error, setError] = useState(null);

  const alternativeSuggestions = React.useMemo(() => {
    if (!Array.isArray(alternatives)) return [];
    return alternatives
      .map((a) => (typeof a === 'string' ? a : a?.product_name || a?.name))
      .filter((n) => typeof n === 'string' && n.trim().length > 0)
      .slice(0, 3);
  }, [alternatives]);

  const currentIdentifier =
    currentProduct?.barcode && String(currentProduct.barcode) !== '00000000'
      ? currentProduct.barcode
      : currentProduct?.name || currentProduct?.product_name || '';

  // Reset comparison state when current product changes
  React.useEffect(() => {
    setComparison(null);
    setTargetQuery('');
    setExpanded(false);
    setError(null);
  }, [currentIdentifier]);

  const handleCompare = async (target) => {
    const query = String(target || targetQuery || '').trim();
    if (!query || !currentIdentifier || loading) return;

    setLoading(true);
    setError(null);
    try {
      const res = await compareProducts(currentIdentifier, query);
      setComparison(res);
      setTargetQuery(query);
      setExpanded(true);
    } catch (e) {
      const status = e?.response?.status;
      if (status === 404) {
        setError(`Product "${query}" not found in database. Try another search term.`);
      } else {
        const msg = e?.response?.data?.detail || e?.message || 'Comparison failed';
        setError(String(msg));
      }
    } finally {
      setLoading(false);
    }
  };

  const prodA = comparison?.product_a || {};
  const prodB = comparison?.product_b || {};
  const compMetrics = [
    { label: 'Health Score', key: 'health_score', unit: '/100', higherIsBetter: true },
    { label: 'Calories', key: 'calories', unit: ' kcal', higherIsBetter: false },
    { label: 'Sugar', key: 'sugar', unit: 'g', higherIsBetter: false },
    { label: 'Protein', key: 'protein', unit: 'g', higherIsBetter: true },
    { label: 'Fat', key: 'fat', unit: 'g', higherIsBetter: false },
    { label: 'Salt / Sodium', key: 'salt', unit: 'g', higherIsBetter: false },
  ];

  return (
    <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
      {/* Header */}
      <View style={styles.header}>
        <View>
          <View style={styles.badgeWrap}>
            <Ionicons name="swap-horizontal-outline" size={13} color={PREMIUM_COLORS.primaryDark} />
            <Text style={styles.badgeText}>BENCHMARK</Text>
          </View>
          <Text style={styles.title}>Nutritional Comparison</Text>
          <Text style={styles.subtitle}>Head-to-head analysis with similar products</Text>
        </View>

        {comparison ? (
          <TouchableOpacity
            style={styles.toggleBtn}
            onPress={() => setExpanded(!expanded)}
            activeOpacity={0.7}
          >
            <Text style={styles.toggleBtnText}>{expanded ? 'Hide ▲' : 'Show ▼'}</Text>
          </TouchableOpacity>
        ) : null}
      </View>

      {/* Input Search Box */}
      <View style={styles.searchRow}>
        <TextInput
          style={styles.input}
          placeholder="Compare against e.g. Maggi, Parle-G..."
          placeholderTextColor={PREMIUM_COLORS.muted}
          value={targetQuery}
          onChangeText={(t) => {
            setTargetQuery(t);
            if (error) setError(null);
          }}
          returnKeyType="search"
          onSubmitEditing={() => handleCompare(targetQuery)}
        />
        <TouchableOpacity
          style={[styles.compareBtn, (!targetQuery.trim() || loading) && styles.compareBtnDisabled]}
          onPress={() => handleCompare(targetQuery)}
          disabled={!targetQuery.trim() || loading}
          activeOpacity={0.88}
        >
          {loading ? (
            <ActivityIndicator size="small" color={PREMIUM_COLORS.white} />
          ) : (
            <Text style={styles.compareBtnText}>Compare</Text>
          )}
        </TouchableOpacity>
      </View>

      {/* Benchmark Suggestions */}
      {!comparison && (
        <View style={styles.suggestionsWrap}>
          {alternativeSuggestions.length > 0 ? (
            <View style={{ marginBottom: 10 }}>
              <Text style={styles.suggestionsLabel}>Compare with healthier alternatives:</Text>
              <View style={styles.chipsRow}>
                {alternativeSuggestions.map((item) => (
                  <TouchableOpacity
                    key={item}
                    style={[styles.chip, styles.alternativeChip]}
                    activeOpacity={0.8}
                    onPress={() => {
                      setTargetQuery(item);
                      handleCompare(item);
                    }}
                  >
                    <Ionicons name="sparkles" size={11} color={PREMIUM_COLORS.primaryDark} style={{ marginRight: 4 }} />
                    <Text style={[styles.chipText, styles.alternativeChipText]}>{item}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            </View>
          ) : null}

          <Text style={styles.suggestionsLabel}>Popular benchmarks:</Text>
          <View style={styles.chipsRow}>
            {COMPARE_SUGGESTIONS.map((item) => (
              <TouchableOpacity
                key={item}
                style={styles.chip}
                activeOpacity={0.8}
                onPress={() => {
                  setTargetQuery(item);
                  handleCompare(item);
                }}
              >
                <Text style={styles.chipText}>{item}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>
      )}

      {error ? (
        <View style={styles.errorBox}>
          <Ionicons name="alert-circle-outline" size={14} color={PREMIUM_COLORS.status.avoid} />
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : null}

      {/* Comparison Results */}
      {comparison && expanded && (
        <View style={styles.resultsContainer}>
          {/* Product A vs Product B Header */}
          <View style={styles.vsHeaderRow}>
            <View style={styles.productSide}>
              <Text style={styles.sideLabel}>CURRENT</Text>
              <Text style={styles.productName} numberOfLines={2}>
                {prodA?.product_name || currentProduct?.name || 'Product A'}
              </Text>
              {prodA?.health_score != null ? (
                <View style={[styles.scoreBadge, { backgroundColor: _scorePill(prodA.health_score).bg }]}>
                  <Text style={[styles.scoreBadgeText, { color: _scorePill(prodA.health_score).fg }]}>
                    Score {prodA.health_score}
                  </Text>
                </View>
              ) : null}
            </View>

            <View style={styles.vsBadge}>
              <Text style={styles.vsText}>VS</Text>
            </View>

            <View style={[styles.productSide, { alignItems: 'flex-end' }]}>
              <Text style={styles.sideLabel}>TARGET</Text>
              <Text style={[styles.productName, { textAlign: 'right' }]} numberOfLines={2}>
                {prodB?.product_name || targetQuery || 'Product B'}
              </Text>
              {prodB?.health_score != null ? (
                <View style={[styles.scoreBadge, { backgroundColor: _scorePill(prodB.health_score).bg }]}>
                  <Text style={[styles.scoreBadgeText, { color: _scorePill(prodB.health_score).fg }]}>
                    Score {prodB.health_score}
                  </Text>
                </View>
              ) : null}
            </View>
          </View>

          {/* Metric Comparison Rows */}
          <View style={styles.metricsBox}>
            {compMetrics.map((m) => {
              const valA = Number(prodA[m.key] ?? prodA?.nutrition?.[m.key]);
              const valB = Number(prodB[m.key] ?? prodB?.nutrition?.[m.key]);
              const validA = Number.isFinite(valA);
              const validB = Number.isFinite(valB);

              let highlightA = false;
              let highlightB = false;
              if (validA && validB && valA !== valB) {
                if (m.higherIsBetter) {
                  highlightA = valA > valB;
                  highlightB = valB > valA;
                } else {
                  highlightA = valA < valB;
                  highlightB = valB < valA;
                }
              }

              return (
                <View key={m.key} style={styles.metricRow}>
                  <Text style={[styles.metricVal, highlightA && styles.metricValWinner]}>
                    {validA ? (m.key === 'health_score' ? `${Math.round(valA)}${m.unit}` : `${formatNutritionValue(valA)}${m.unit}`) : '—'}
                  </Text>
                  <Text style={styles.metricLabel}>{m.label}</Text>
                  <Text style={[styles.metricVal, { textAlign: 'right' }, highlightB && styles.metricValWinner]}>
                    {validB ? (m.key === 'health_score' ? `${Math.round(valB)}${m.unit}` : `${formatNutritionValue(valB)}${m.unit}`) : '—'}
                  </Text>
                </View>
              );
            })}
          </View>
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
  toggleBtn: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
    backgroundColor: PREMIUM_COLORS.bgAlt,
  },
  toggleBtnText: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },
  searchRow: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'center',
  },
  input: {
    flex: 1,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.md,
    paddingHorizontal: 14,
    paddingVertical: 10,
    fontSize: 14,
    fontWeight: '500',
    color: PREMIUM_COLORS.ink,
  },
  compareBtn: {
    backgroundColor: PREMIUM_COLORS.ink,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: PREMIUM_RADIUS.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  compareBtnDisabled: {
    opacity: 0.5,
  },
  compareBtnText: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.white,
  },
  suggestionsWrap: {
    marginTop: 10,
  },
  suggestionsLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
    marginBottom: 6,
  },
  chipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  chip: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    paddingHorizontal: 11,
    paddingVertical: 6,
    borderRadius: PREMIUM_RADIUS.pill,
  },
  chipText: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.ink,
  },
  alternativeChip: {
    backgroundColor: PREMIUM_COLORS.primaryLight,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.safeBorder,
    flexDirection: 'row',
    alignItems: 'center',
  },
  alternativeChipText: {
    color: PREMIUM_COLORS.primaryDark,
    fontWeight: '700',
  },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
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
    marginTop: 14,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 14,
  },
  vsHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: PREMIUM_COLORS.divider,
    marginBottom: 10,
  },
  productSide: {
    flex: 1,
  },
  sideLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: PREMIUM_COLORS.muted,
    letterSpacing: 0.6,
  },
  productName: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginTop: 2,
    marginBottom: 4,
  },
  scoreBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: PREMIUM_RADIUS.pill,
  },
  scoreBadgeText: {
    fontSize: 11,
    fontWeight: '700',
  },
  vsBadge: {
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: PREMIUM_COLORS.card,
    alignItems: 'center',
    justifyContent: 'center',
    marginHorizontal: 8,
    ...PREMIUM_SHADOWS.sm,
  },
  vsText: {
    fontSize: 11,
    fontWeight: '800',
    color: PREMIUM_COLORS.secondary,
  },
  metricsBox: {
    gap: 8,
  },
  metricRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 4,
  },
  metricVal: {
    width: 60,
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },
  metricValWinner: {
    color: PREMIUM_COLORS.primaryDark,
    fontWeight: '800',
  },
  metricLabel: {
    flex: 1,
    textAlign: 'center',
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
});
