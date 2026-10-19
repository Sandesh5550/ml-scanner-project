import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, StyleSheet, ScrollView, ActivityIndicator, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';

import { getDailyReport, getWeeklyReport, getGoalReport } from '../services/api';
import { resetToLogin } from '../utils/navigationRef';
import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';
import { formatNutritionValue } from '../utils/formatters';

function _ratingMeta(rating) {
  const r = String(rating || '').toUpperCase();
  if (r === 'GREAT' || r === 'GOOD') {
    return { label: r, bg: PREMIUM_COLORS.status.safeBg, fg: PREMIUM_COLORS.status.safe, icon: 'sparkles' };
  }
  if (r === 'FAIR') {
    return { label: 'MODERATE', bg: PREMIUM_COLORS.status.moderateBg, fg: PREMIUM_COLORS.status.moderate, icon: 'flash' };
  }
  return { label: 'NEEDS ATTENTION', bg: PREMIUM_COLORS.status.avoidBg, fg: PREMIUM_COLORS.status.avoid, icon: 'alert-circle' };
}

function _formatGoalName(type) {
  if (!type || typeof type !== 'string') return 'Dietary Goal';
  return type
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ');
}

function PremiumReportBar({ label, consumed, limit, unit = 'g', iconName = 'leaf-outline' }) {
  const c = Number(consumed) || 0;
  const l = Number(limit) || 1;
  const pct = Math.min(100, Math.round((c / l) * 100));
  const ratio = c / l;
  const color = ratio <= 0.8 ? PREMIUM_COLORS.primaryDark : ratio <= 1 ? PREMIUM_COLORS.status.moderate : PREMIUM_COLORS.status.avoid;

  return (
    <View style={styles.barItem}>
      <View style={styles.barRowTop}>
        <View style={styles.barLabelGroup}>
          <Ionicons name={iconName} size={14} color={color} />
          <Text style={styles.barLabel}>{label}</Text>
        </View>
        <Text style={[styles.barPctText, { color }]}>{pct}% limit</Text>
      </View>

      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${pct}%`, backgroundColor: color }]} />
      </View>

      <View style={styles.barFooter}>
        <Text style={styles.barSub}>{formatNutritionValue(c)} {unit} consumed</Text>
        <Text style={styles.barLimit}>of {formatNutritionValue(l)} {unit} target</Text>
      </View>
    </View>
  );
}

export default function ReportScreen() {
  const [loading, setLoading] = useState(true);
  const [daily, setDaily] = useState(null);
  const [weekly, setWeekly] = useState(null);
  const [goal, setGoal] = useState(null);

  const loadReports = useCallback(async () => {
    try {
      const [d, w, g] = await Promise.all([
        getDailyReport(),
        getWeeklyReport(),
        getGoalReport().catch(() => null),
      ]);
      setDaily(d);
      setWeekly(w);
      setGoal(g);
    } catch (e) {
      if (e?.response?.status === 401) {
        resetToLogin();
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      loadReports();
    }, [loadReports])
  );

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={PREMIUM_COLORS.primaryDark} />
        <Text style={styles.loadingText}>Loading nutrition analytics...</Text>
      </View>
    );
  }

  if (!daily || !weekly) {
    return (
      <View style={styles.center}>
        <View style={[styles.card, PREMIUM_SHADOWS.md, { padding: 24, alignItems: 'center' }]}>
          <Text style={{ color: PREMIUM_COLORS.ink, fontWeight: '700', fontSize: 16 }}>
            Unable to load health reports
          </Text>
          <Text style={{ marginTop: 6, color: PREMIUM_COLORS.secondary, fontWeight: '400', fontSize: 13, textAlign: 'center' }}>
            Please check connection to the backend and try again.
          </Text>
        </View>
      </View>
    );
  }

  const ratingMeta = _ratingMeta(daily?.overall_rating);
  const overallScore = Math.round(Number(daily?.overall_score || 0));
  const nb = daily?.nutrition_breakdown || {};
  const days = weekly?.days || [];
  const weeklyAvg = Math.round(Number(weekly?.average_score || overallScore));

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
      >
        {/* Header */}
        <View style={styles.header}>
          <View style={styles.categoryBadge}>
            <View style={styles.badgeDot} />
            <Text style={styles.badgeText}>ANALYTICS</Text>
          </View>
          <Text style={styles.screenTitle}>Nutrition Report</Text>
          <Text style={styles.screenSub}>Personal dietary trends and goal compliance</Text>
        </View>

        {/* 1. Overall Daily Score Card */}
        <View style={[styles.dailyHeroCard, PREMIUM_SHADOWS.sm]}>
          <View style={styles.dailyHeroTop}>
            <View style={{ flex: 1 }}>
              <Text style={styles.dailyHeroTag}>TODAY'S OVERALL SCORE</Text>
              <Text style={styles.dailyHeroScore}>{overallScore}<Text style={styles.scoreMax}> /100</Text></Text>
            </View>
            <View style={[styles.ratingPill, { backgroundColor: ratingMeta.bg }]}>
              <Ionicons name={ratingMeta.icon} size={12} color={ratingMeta.fg} style={{ marginRight: 4 }} />
              <Text style={[styles.ratingText, { color: ratingMeta.fg }]}>{ratingMeta.label}</Text>
            </View>
          </View>

          {daily?.advice ? (
            <View style={styles.adviceBox}>
              <Ionicons name="bulb-outline" size={16} color={PREMIUM_COLORS.primaryDark} />
              <Text style={styles.adviceText}>{daily.advice}</Text>
            </View>
          ) : null}
        </View>

        {/* 2. Daily Macro Limits */}
        <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
          <Text style={styles.cardTitle}>Daily Nutrient Limits</Text>
          <Text style={styles.cardSub}>Consumption tracking against personalized daily budget</Text>

          <View style={styles.barsContainer}>
            <PremiumReportBar
              label="Calories"
              consumed={nb.calories?.consumed}
              limit={nb.calories?.limit || 2000}
              unit="kcal"
              iconName="flame-outline"
            />
            <PremiumReportBar
              label="Sugar"
              consumed={nb.sugar?.consumed}
              limit={nb.sugar?.limit || 50}
              unit="g"
              iconName="water-outline"
            />
            <PremiumReportBar
              label="Salt / Sodium"
              consumed={nb.salt?.consumed}
              limit={nb.salt?.limit || 5}
              unit="g"
              iconName="cube-outline"
            />
            <PremiumReportBar
              label="Total Fat"
              consumed={nb.fat?.consumed}
              limit={nb.fat?.limit || 70}
              unit="g"
              iconName="ellipse-outline"
            />
            <PremiumReportBar
              label="Protein"
              consumed={nb.protein?.consumed}
              limit={nb.protein?.limit || 50}
              unit="g"
              iconName="leaf-outline"
            />
          </View>
        </View>

        {/* 3. 7-Day Weekly Trend Chart */}
        <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
          <View style={styles.trendHeader}>
            <View>
              <Text style={styles.cardTitle}>7-Day Health Trend</Text>
              <Text style={styles.cardSub}>Weekly score curve</Text>
            </View>
            <View style={styles.avgPill}>
              <Text style={styles.avgLabel}>Avg Score</Text>
              <Text style={styles.avgValue}>{weeklyAvg}</Text>
            </View>
          </View>

          {days.length > 0 ? (
            <View style={styles.chartContainer}>
              <View style={styles.chartBarsRow}>
                {days.map((d, idx) => {
                  const score = Math.round(Number(d.score != null ? d.score : (d.day_score || 0)));
                  const barHeight = Math.max(16, (score / 100) * 110);
                  const isSafe = score >= 70;
                  const isModerate = score >= 45;
                  const barColor = isSafe
                    ? PREMIUM_COLORS.primary
                    : isModerate
                      ? '#E8B342'
                      : PREMIUM_COLORS.status.avoid;

                  return (
                    <View key={d.date || idx} style={styles.chartCol}>
                      <Text style={styles.chartScoreText}>{score > 0 ? score : '—'}</Text>
                      <View style={styles.chartBarTrack}>
                        <View
                          style={[
                            styles.chartBarFill,
                            {
                              height: barHeight,
                              backgroundColor: barColor,
                            },
                          ]}
                        />
                      </View>
                      <Text style={styles.chartDayText}>
                        {d.short_day || (d.day_name ? d.day_name.slice(0, 3) : `D${idx + 1}`)}
                      </Text>
                    </View>
                  );
                })}

              </View>
            </View>
          ) : (
            <Text style={styles.noTrendText}>Log meals across the week to generate trend analytics.</Text>
          )}
        </View>

        {/* 4. Goal Progress & Tracking */}
        {goal ? (
          <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
            <Text style={styles.cardTitle}>
              {goal.goal_type ? `${_formatGoalName(goal.goal_type)} Tracking` : 'Goal Tracking'}
            </Text>
            <Text style={styles.cardSub}>
              {goal.goal_summary || 'Dietary progress tracking and compliance'}
            </Text>

            {goal.goal_type && goal.status !== 'NO_GOAL' ? (
              <View style={styles.goalMetricsRow}>
                <View style={styles.goalMetricBox}>
                  <Text style={styles.goalMetricNum}>{goal.days_active != null ? goal.days_active : '—'}</Text>
                  <Text style={styles.goalMetricLabel}>Days Active</Text>
                </View>
                <View style={styles.goalMetricBox}>
                  <Text style={styles.goalMetricNum}>{goal.days_remaining != null ? goal.days_remaining : '—'}</Text>
                  <Text style={styles.goalMetricLabel}>Days Left</Text>
                </View>
                <View style={styles.goalMetricBox}>
                  <Text style={styles.goalMetricNum}>
                    {goal.progress_score != null ? `${Math.round(goal.progress_score)}/100` : '—'}
                  </Text>
                  <Text style={styles.goalMetricLabel}>Progress Score</Text>
                </View>
              </View>
            ) : null}
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: PREMIUM_COLORS.bg,
  },
  scrollContent: {
    padding: 18,
    paddingTop: 52,
    paddingBottom: 110,
  },
  center: {
    flex: 1,
    backgroundColor: PREMIUM_COLORS.bg,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 20,
  },
  loadingText: {
    fontSize: 14,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
    marginTop: 10,
  },

  // Header
  header: {
    marginBottom: 16,
  },
  categoryBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 4,
  },
  badgeDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: PREMIUM_COLORS.primaryDark,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
    letterSpacing: 0.6,
  },
  screenTitle: {
    fontSize: 28,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.4,
  },
  screenSub: {
    fontSize: 14,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    marginTop: 4,
    lineHeight: 20,
  },

  // Daily Hero
  dailyHeroCard: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 20,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  dailyHeroTop: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
  },
  dailyHeroTag: {
    fontSize: 12,
    fontWeight: '700',
    color: PREMIUM_COLORS.secondary,
    letterSpacing: 0.6,
    marginBottom: 4,
  },
  dailyHeroScore: {
    fontSize: 38,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -1,
  },
  scoreMax: {
    fontSize: 16,
    fontWeight: '500',
    color: PREMIUM_COLORS.muted,
  },
  ratingPill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: PREMIUM_RADIUS.pill,
    gap: 5,
  },
  ratingText: {
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
  adviceBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.bgAlt,
    padding: 12,
    borderRadius: PREMIUM_RADIUS.md,
    marginTop: 14,
    gap: 8,
  },
  adviceIcon: {
    fontSize: 16,
  },
  adviceText: {
    flex: 1,
    fontSize: 13,
    fontWeight: '500',
    color: PREMIUM_COLORS.ink,
    lineHeight: 18,
  },

  // Card general
  card: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 20,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  cardTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.2,
  },
  cardSub: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    marginTop: 2,
    marginBottom: 14,
  },

  // Nutrient Bars
  barsContainer: {
    gap: 14,
  },
  barItem: {},
  barRowTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 5,
  },
  barLabelGroup: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  barEmoji: {
    fontSize: 13,
  },
  barLabel: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  barPctText: {
    fontSize: 12,
    fontWeight: '700',
  },
  barTrack: {
    height: 6,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: 3,
    overflow: 'hidden',
  },
  barFill: {
    height: '100%',
    borderRadius: 3,
  },
  barFooter: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 4,
  },
  barSub: {
    fontSize: 12,
    fontWeight: '500',
    color: PREMIUM_COLORS.secondary,
  },
  barLimit: {
    fontSize: 12,
    fontWeight: '500',
    color: PREMIUM_COLORS.muted,
  },

  // Trends
  trendHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 10,
  },
  avgPill: {
    backgroundColor: PREMIUM_COLORS.primaryLight,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
    alignItems: 'center',
  },
  avgLabel: {
    fontSize: 9,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
  },
  avgValue: {
    fontSize: 14,
    fontWeight: '800',
    color: PREMIUM_COLORS.primaryDark,
  },
  chartContainer: {
    marginTop: 8,
  },
  chartBarsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    height: 140,
    paddingTop: 10,
  },
  chartCol: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'flex-end',
  },
  chartScoreText: {
    fontSize: 10,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
    marginBottom: 4,
  },
  chartBarTrack: {
    width: 14,
    height: 110,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: 7,
    justifyContent: 'flex-end',
    overflow: 'hidden',
  },
  chartBarFill: {
    width: '100%',
    borderRadius: 7,
  },
  chartDayText: {
    fontSize: 10,
    fontWeight: '700',
    color: PREMIUM_COLORS.secondary,
    marginTop: 6,
  },
  noTrendText: {
    fontSize: 12,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    textAlign: 'center',
    paddingVertical: 18,
  },

  // Goal
  goalMetricsRow: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 4,
  },
  goalMetricBox: {
    flex: 1,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    padding: 12,
    borderRadius: PREMIUM_RADIUS.lg,
    alignItems: 'center',
  },
  goalMetricNum: {
    fontSize: 18,
    fontWeight: '800',
    color: PREMIUM_COLORS.ink,
  },
  goalMetricLabel: {
    fontSize: 11,
    fontWeight: '500',
    color: PREMIUM_COLORS.secondary,
    marginTop: 2,
  },
});
