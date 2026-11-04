import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { getHealthierAlternatives } from '../services/api';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../theme/premiumTheme';
import { FoodImage } from './premium';

function _nutriscoreColor(grade) {
  const g = String(grade || '').toLowerCase();
  if (g === 'a') return '#2E7D32';
  if (g === 'b') return '#689F38';
  if (g === 'c') return '#D99B00';
  if (g === 'd') return '#E66800';
  if (g === 'e') return '#C62828';
  return PREMIUM_COLORS.secondary;
}

export default function HealthierAlternativesCard({
  initialRecommendations = [],
  barcode = null,
  productName = null,
  nutrition = null,
  onSelectAlternative = null,
}) {
  const [items, setItems] = useState(
    Array.isArray(initialRecommendations) ? initialRecommendations : []
  );
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (Array.isArray(initialRecommendations) && initialRecommendations.length > 0) {
      setItems(initialRecommendations);
      setFetched(true);
    } else {
      setItems([]);
      setFetched(false);
    }
    setError(null);
  }, [initialRecommendations, barcode]);

  const handleFetchAlternatives = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getHealthierAlternatives({
        productName: productName || null,
        barcode: barcode && String(barcode) !== '00000000' ? String(barcode) : null,
        nutrition: nutrition || null,
        limit: 3,
      });
      const list = Array.isArray(res?.recommendations)
        ? res.recommendations
        : Array.isArray(res)
          ? res
          : [];
      setItems(list);
      setFetched(true);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to find alternatives';
      setError(String(msg));
    } finally {
      setLoading(false);
    }
  };

  const hasItems = items.length > 0;

  return (
    <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
      {/* Header */}
      <View style={styles.headerRow}>
        <View>
          <View style={styles.badgeWrap}>
            <Ionicons name="leaf-outline" size={12} color={PREMIUM_COLORS.primaryDark} />
            <Text style={styles.badgeText}>SMART SWAPS</Text>
          </View>
          <Text style={styles.title}>Healthier Alternatives</Text>
          <Text style={styles.subtitle}>Nutritionally superior options in this category</Text>
        </View>

        {!hasItems && !fetched && (
          <TouchableOpacity
            style={styles.findBtn}
            onPress={handleFetchAlternatives}
            disabled={loading}
            activeOpacity={0.85}
          >
            {loading ? (
              <ActivityIndicator size="small" color={PREMIUM_COLORS.primaryDark} />
            ) : (
              <Text style={styles.findBtnText}>Find Swaps →</Text>
            )}
          </TouchableOpacity>
        )}
      </View>

      {error ? (
        <View style={styles.errorBox}>
          <Ionicons name="alert-circle-outline" size={14} color={PREMIUM_COLORS.status.avoid} />
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : null}

      {/* Alternatives Horizontal Carousel */}
      {hasItems ? (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.scrollList}
        >
          {items.map((alt, idx) => {
            const name = alt?.product_name || alt?.name || `Alternative ${idx + 1}`;
            const score = alt?.health_score != null ? Math.round(Number(alt.health_score)) : null;
            const nutriscore = alt?.nutriscore ? String(alt.nutriscore).toUpperCase() : null;
            const advantages = Array.isArray(alt?.advantages) ? alt.advantages : [];
            const shortReason = alt?.reason || (advantages.length > 0 ? advantages[0] : 'Better nutrient profile');

            return (
              <TouchableOpacity
                key={alt?.barcode || idx}
                activeOpacity={0.88}
                style={[styles.itemCard, PREMIUM_SHADOWS.sm]}
                onPress={() => onSelectAlternative && onSelectAlternative(alt)}
              >
                <FoodImage
                  source={alt?.image_url}
                  productName={name}
                  size={150}
                  height={100}
                  borderRadius={PREMIUM_RADIUS.md}
                />

                <Text style={styles.itemName} numberOfLines={1}>
                  {name}
                </Text>

                <View style={styles.itemMetaRow}>
                  {score != null ? (
                    <View style={styles.scorePill}>
                      <View style={styles.scoreDot} />
                      <Text style={styles.scoreVal}>Score {score}</Text>
                    </View>
                  ) : null}

                  {nutriscore ? (
                    <View
                      style={[
                        styles.nutriscoreBadge,
                        { backgroundColor: _nutriscoreColor(nutriscore) + '20' },
                      ]}
                    >
                      <Text
                        style={[
                          styles.nutriscoreText,
                          { color: _nutriscoreColor(nutriscore) },
                        ]}
                      >
                        Grade {nutriscore}
                      </Text>
                    </View>
                  ) : null}
                </View>

                {shortReason ? (
                  <View style={styles.advantagePill}>
                    <Text style={styles.advantageText} numberOfLines={2}>
                      ✓ {shortReason}
                    </Text>
                  </View>
                ) : null}
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      ) : fetched ? (
        <View style={styles.emptyBox}>
          <Text style={styles.emptyText}>
            No healthier alternatives found for this product category.
          </Text>
        </View>
      ) : null}
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
  headerRow: {
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
  findBtn: {
    backgroundColor: PREMIUM_COLORS.primaryLight,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: PREMIUM_RADIUS.pill,
    alignItems: 'center',
  },
  findBtnText: {
    fontSize: 12,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
  },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    borderRadius: PREMIUM_RADIUS.md,
    padding: 10,
    marginTop: 8,
  },
  errorText: {
    fontSize: 13,
    color: PREMIUM_COLORS.status.avoid,
    fontWeight: '600',
  },
  scrollList: {
    paddingVertical: 4,
    gap: 12,
  },
  itemCard: {
    width: 170,
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 10,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  itemName: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginTop: 8,
    marginBottom: 4,
  },
  itemMetaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 8,
  },
  scorePill: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.status.safeBg,
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: PREMIUM_RADIUS.pill,
    gap: 4,
  },
  scoreDot: {
    width: 5,
    height: 5,
    borderRadius: 2.5,
    backgroundColor: PREMIUM_COLORS.status.safe,
  },
  scoreVal: {
    fontSize: 11,
    fontWeight: '700',
    color: PREMIUM_COLORS.status.safe,
  },
  nutriscoreBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: PREMIUM_RADIUS.sm,
  },
  nutriscoreText: {
    fontSize: 10,
    fontWeight: '800',
  },
  advantagePill: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    padding: 6,
    borderRadius: PREMIUM_RADIUS.sm,
  },
  advantageText: {
    fontSize: 12,
    fontWeight: '500',
    color: PREMIUM_COLORS.primaryDark,
    lineHeight: 16,
  },
  emptyBox: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.md,
    padding: 14,
    alignItems: 'center',
  },
  emptyText: {
    fontSize: 13,
    fontWeight: '500',
    color: PREMIUM_COLORS.secondary,
    textAlign: 'center',
    lineHeight: 18,
  },
});
