import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Alert, Platform, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';

import { getUserProfile, updateUserProfile } from '../services/api';
import { useAuth } from '../context/AuthContext';
import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';
import { NeoButton, NeoInput, NeoBadge } from '../components/neo';

const DIETS = [
  { key: null, label: 'Standard / None' },
  { key: 'diabetic', label: 'Diabetic' },
  { key: 'vegan', label: 'Vegan' },
  { key: 'vegetarian', label: 'Vegetarian' },
  { key: 'low_sodium', label: 'Low Sodium' },
];

const GOALS = [
  { key: 'lose_weight', label: 'Lose Weight' },
  { key: 'control_sugar', label: 'Control Sugar' },
  { key: 'eat_clean', label: 'Eat Clean' },
  { key: 'build_muscle', label: 'Build Muscle' },
  { key: 'reduce_sodium', label: 'Reduce Sodium' },
];

const ALLERGEN_OPTIONS = [
  { key: 'peanuts', label: 'Peanuts' },
  { key: 'tree_nuts', label: 'Tree Nuts' },
  { key: 'milk', label: 'Milk & Dairy' },
  { key: 'eggs', label: 'Eggs' },
  { key: 'wheat_gluten', label: 'Wheat & Gluten' },
  { key: 'soy', label: 'Soy / Soya' },
  { key: 'fish', label: 'Fish' },
  { key: 'shellfish', label: 'Shellfish' },
  { key: 'sesame', label: 'Sesame' },
  { key: 'mustard', label: 'Mustard' },
];

export default function ProfileScreen({ navigation }) {
  const { logout, isLoggingOut } = useAuth();
  const [profile, setProfile] = useState(null);
  const [diet, setDiet] = useState(null);
  const [goal, setGoal] = useState(null);
  const [goalDays, setGoalDays] = useState('30');
  const [dailyLimit, setDailyLimit] = useState('2000');
  const [age, setAge] = useState('');
  const [weight, setWeight] = useState('');
  const [height, setHeight] = useState('');
  const [selectedAllergens, setSelectedAllergens] = useState([]);
  const [saving, setSaving] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  const fetchProfile = useCallback(async () => {
    try {
      const p = await getUserProfile();
      setProfile(p);
      setDiet(p?.diet_type ?? null);
      setGoal(p?.goal_type ?? null);
      setGoalDays(String(p?.goal_target_days ?? 30));
      setDailyLimit(String(p?.daily_calorie_limit ?? 2000));
      setAge(p?.age != null ? String(p.age) : '');
      setWeight(p?.weight != null ? String(p.weight) : '');
      setHeight(p?.height != null ? String(p.height) : '');
      if (p?.allergens) {
        const arr = String(p.allergens)
          .split(',')
          .map((s) => s.trim().toLowerCase())
          .filter(Boolean);
        setSelectedAllergens(arr);
      } else {
        setSelectedAllergens([]);
      }
    } catch (e) {
      // ignore
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      fetchProfile();
    }, [fetchProfile])
  );

  const toggleAllergen = (key) => {
    setSelectedAllergens((prev) => {
      if (prev.includes(key)) {
        return prev.filter((k) => k !== key);
      } else {
        return [...prev, key];
      }
    });
  };

  const calorieEstimate = useMemo(() => {
    const a = parseFloat(age);
    const w = parseFloat(weight);
    const h = parseFloat(height);
    if (!a || !w || !h || a <= 0 || w <= 0 || h <= 0 || a > 120 || w > 400 || h > 300) {
      return null;
    }
    const bmr = 10.0 * w + 6.25 * h - 5.0 * a - 78.0;
    const tdee = bmr * 1.35;
    let recommended = tdee;
    let goalNote = 'Maintenance caloric target';

    if (goal === 'lose_weight') {
      recommended = Math.max(1200, tdee - 450);
      goalNote = 'Caloric deficit (-450 kcal) targeted for fat loss';
    } else if (goal === 'build_muscle') {
      recommended = tdee + 250;
      goalNote = 'Caloric surplus (+250 kcal) for muscle protein synthesis';
    } else if (goal === 'control_sugar') {
      goalNote = 'Maintenance calories with glycemic control focus';
    } else if (goal === 'reduce_sodium') {
      goalNote = 'Maintenance calories with low sodium focus';
    }
    const rounded = Math.round(recommended / 50) * 50;
    return {
      bmr: Math.round(bmr),
      tdee: Math.round(tdee),
      recommended: rounded,
      goalNote,
    };
  }, [age, weight, height, goal]);

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        diet_type: diet,
        daily_calorie_limit: dailyLimit ? Number(dailyLimit) : null,
        goal_type: goal,
        goal_target_days: goalDays ? Number(goalDays) : null,
        allergens: selectedAllergens.length > 0 ? selectedAllergens.join(',') : '',
        age: age ? parseInt(age, 10) : null,
        weight: weight ? parseFloat(weight) : null,
        height: height ? parseFloat(height) : null,
      };
      await updateUserProfile(payload);
      Alert.alert('Profile Saved', 'Your dietary preferences, biometric data, and allergy alerts have been updated.');
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Update failed';
      Alert.alert('Error', String(msg));
    } finally {
      setSaving(false);
    }
  };

  const performLogout = async () => {
    try {
      setLoggingOut(true);
      await logout();
    } catch (_e) {
      // ignore
    } finally {
      setLoggingOut(false);
    }
  };

  const handleLogout = () => {
    if (loggingOut || isLoggingOut) return;

    if (Platform.OS === 'web') {
      if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
        const confirmed = window.confirm('Are you sure you want to log out of PRAMAAN?');
        if (confirmed) {
          performLogout();
        }
      } else {
        performLogout();
      }
    } else {
      Alert.alert(
        'Log Out',
        'Are you sure you want to log out of PRAMAAN?',
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Log Out', style: 'destructive', onPress: performLogout },
        ]
      );
    }
  };

  const initial = (profile?.name || profile?.email || 'U').trim().slice(0, 1).toUpperCase();

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
            <Text style={styles.badgeText}>ACCOUNT</Text>
          </View>
          <Text style={styles.screenTitle}>Health Profile</Text>
          <Text style={styles.screenSub}>Personalize nutritional limits & dietary scoring</Text>
        </View>

        {/* User Card */}
        <View style={[styles.userCard, PREMIUM_SHADOWS.sm]}>
          <View style={styles.avatarBox}>
            <Text style={styles.avatarText}>{initial}</Text>
          </View>
          <View style={styles.userInfo}>
            <NeoBadge text="VERIFIED PROFILE" variant="safe" size="sm" showDot />
            <Text style={styles.userName} numberOfLines={1}>
              {profile?.name || 'Pramaan Member'}
            </Text>
            <Text style={styles.userEmail} numberOfLines={1}>
              {profile?.email || 'member@pramaan.ai'}
            </Text>
          </View>
        </View>

        {/* Dietary Preferences Card */}
        <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
          <Text style={styles.cardTitle}>Dietary Profile</Text>
          <Text style={styles.cardSub}>
            Hazard thresholds and health scores automatically adapt to your diet.
          </Text>

          <View style={styles.chipsRow}>
            {DIETS.map((d) => {
              const active = diet === d.key;
              return (
                <TouchableOpacity
                  key={String(d.key)}
                  activeOpacity={0.85}
                  style={[
                    styles.chip,
                    active ? styles.chipActive : null,
                  ]}
                  onPress={() => setDiet(d.key)}
                >
                  <Text style={[styles.chipText, active && styles.chipTextActive]}>
                    {d.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </View>

        {/* Food Allergies & Sensitivities Card */}
        <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
          <Text style={styles.cardTitle}>Food Allergies & Sensitivities</Text>
          <Text style={styles.cardSub}>
            PRAMAAN automatically scans ingredients and flags critical allergen warnings.
          </Text>

          <View style={styles.chipsRow}>
            {ALLERGEN_OPTIONS.map((a) => {
              const active = selectedAllergens.includes(a.key);
              return (
                <TouchableOpacity
                  key={a.key}
                  activeOpacity={0.85}
                  style={[
                    styles.chip,
                    styles.allergenChip,
                    active ? styles.allergenChipActive : null,
                  ]}
                  onPress={() => toggleAllergen(a.key)}
                >
                  <Ionicons
                    name={active ? 'warning' : 'shield-checkmark-outline'}
                    size={13}
                    color={active ? PREMIUM_COLORS.status.avoid : PREMIUM_COLORS.secondary}
                    style={{ marginRight: 4 }}
                  />
                  <Text style={[styles.chipText, active && styles.allergenChipTextActive]}>
                    {a.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          {selectedAllergens.length > 0 ? (
            <View style={styles.activeAllergenNotice}>
              <Ionicons name="shield-alert-outline" size={14} color={PREMIUM_COLORS.status.avoid} />
              <Text style={styles.activeAllergenNoticeText}>
                Active safety alerts enabled for {selectedAllergens.length} declared allergen{selectedAllergens.length > 1 ? 's' : ''}.
              </Text>
            </View>
          ) : null}
        </View>

        {/* Health Goal Card */}
        <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
          <Text style={styles.cardTitle}>Primary Health Goal</Text>
          <Text style={styles.cardSub}>
            Focus recommendations on your specific wellness target.
          </Text>

          <View style={styles.chipsRow}>
            {GOALS.map((g) => {
              const active = goal === g.key;
              return (
                <TouchableOpacity
                  key={g.key}
                  activeOpacity={0.85}
                  style={[
                    styles.chip,
                    active ? styles.chipActive : null,
                  ]}
                  onPress={() => setGoal(g.key)}
                >
                  <Text style={[styles.chipText, active && styles.chipTextActive]}>
                    {g.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </View>

        {/* Biometrics & Mifflin-St Jeor Energy Estimation */}
        <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
          <Text style={styles.cardTitle}>Biometrics & Daily Energy Estimate</Text>
          <Text style={styles.cardSub}>
            Mifflin-St Jeor standard energy expenditure estimation based on your profile.
          </Text>

          <View style={styles.biometricsRow}>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Age (yrs)"
                value={age}
                onChangeText={setAge}
                placeholder="28"
                keyboardType="numeric"
              />
            </View>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Weight (kg)"
                value={weight}
                onChangeText={setWeight}
                placeholder="70"
                keyboardType="numeric"
              />
            </View>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Height (cm)"
                value={height}
                onChangeText={setHeight}
                placeholder="175"
                keyboardType="numeric"
              />
            </View>
          </View>

          {calorieEstimate ? (
            <View style={styles.calculatorResultBox}>
              <View style={styles.calcMetricsRow}>
                <View style={styles.calcMetricItem}>
                  <Text style={styles.calcMetricLabel}>Basal (BMR)</Text>
                  <Text style={styles.calcMetricValue}>{calorieEstimate.bmr}</Text>
                  <Text style={styles.calcMetricUnit}>kcal/day</Text>
                </View>
                <View style={styles.calcDivider} />
                <View style={styles.calcMetricItem}>
                  <Text style={styles.calcMetricLabel}>Maintenance (TDEE)</Text>
                  <Text style={styles.calcMetricValue}>{calorieEstimate.tdee}</Text>
                  <Text style={styles.calcMetricUnit}>kcal/day</Text>
                </View>
                <View style={styles.calcDivider} />
                <View style={styles.calcMetricItem}>
                  <Text style={styles.calcMetricLabel}>Target Budget</Text>
                  <Text style={[styles.calcMetricValue, { color: PREMIUM_COLORS.primaryDark }]}>
                    {calorieEstimate.recommended}
                  </Text>
                  <Text style={styles.calcMetricUnit}>kcal/day</Text>
                </View>
              </View>

              <Text style={styles.goalNoteText}>{calorieEstimate.goalNote}</Text>

              <TouchableOpacity
                style={styles.applyCalcBtn}
                onPress={() => setDailyLimit(String(calorieEstimate.recommended))}
                activeOpacity={0.85}
              >
                <Ionicons name="checkmark-circle-outline" size={15} color={PREMIUM_COLORS.primaryDark} />
                <Text style={styles.applyCalcBtnText}>
                  Apply {calorieEstimate.recommended} kcal to Daily Budget
                </Text>
              </TouchableOpacity>

              <Text style={styles.disclaimerText}>
                Informational guideline based on Mifflin-St Jeor formula (moderate activity). Does not constitute medical advice.
              </Text>
            </View>
          ) : (
            <Text style={styles.biometricHint}>
              Enter age, weight, and height to view personalized Mifflin-St Jeor energy calculations.
            </Text>
          )}
        </View>

        {/* Targets & Budgets */}
        <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
          <Text style={styles.cardTitle}>Calorie & Duration Targets</Text>
          <Text style={styles.cardSub}>Set your daily calorie allowance and goal timeline.</Text>

          <NeoInput
            label="Daily Calorie Budget (kcal)"
            value={dailyLimit}
            onChangeText={setDailyLimit}
            placeholder="2000"
            keyboardType="numeric"
          />

          <NeoInput
            label="Goal Duration (Days)"
            value={goalDays}
            onChangeText={setGoalDays}
            placeholder="30"
            keyboardType="numeric"
          />
        </View>

        {/* Action Buttons */}
        <View style={styles.actionsContainer}>
          <NeoButton
            title="Save Preferences"
            onPress={save}
            loading={saving}
            variant="black"
            size="lg"
          />

          <TouchableOpacity
            style={[styles.logoutBtn, (loggingOut || isLoggingOut) && { opacity: 0.6 }]}
            onPress={handleLogout}
            disabled={loggingOut || isLoggingOut}
            activeOpacity={0.8}
          >
            {loggingOut || isLoggingOut ? (
              <ActivityIndicator size="small" color={PREMIUM_COLORS.status.avoid} style={{ marginRight: 8 }} />
            ) : (
              <Ionicons name="log-out-outline" size={16} color={PREMIUM_COLORS.status.avoid} style={{ marginRight: 6 }} />
            )}
            <Text style={styles.logoutBtnText}>
              {loggingOut || isLoggingOut ? 'Logging Out...' : 'Log Out'}
            </Text>
          </TouchableOpacity>
        </View>
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

  // User Card
  userCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 18,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    gap: 14,
  },
  avatarBox: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: PREMIUM_COLORS.primaryLight,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarText: {
    fontSize: 24,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
  },
  userInfo: {
    flex: 1,
    gap: 2,
  },
  userName: {
    fontSize: 16,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginTop: 2,
  },
  userEmail: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
  },

  // Cards
  card: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 18,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  cardTitle: {
    fontSize: 17,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.2,
  },
  cardSub: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    marginTop: 2,
    marginBottom: 12,
  },

  // Chips
  chipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: PREMIUM_RADIUS.pill,
    borderWidth: 1,
    borderColor: 'transparent',
  },
  chipActive: {
    backgroundColor: PREMIUM_COLORS.primaryLight,
    borderColor: PREMIUM_COLORS.status.safeBorder,
  },
  chipText: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.ink,
  },
  chipTextActive: {
    color: PREMIUM_COLORS.primaryDark,
    fontWeight: '700',
  },
  allergenChip: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  allergenChipActive: {
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    borderColor: PREMIUM_COLORS.status.avoidBorder,
  },
  allergenChipTextActive: {
    color: PREMIUM_COLORS.status.avoid,
    fontWeight: '700',
  },
  activeAllergenNotice: {
    marginTop: 12,
    padding: 10,
    borderRadius: PREMIUM_RADIUS.lg,
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.avoidBorder,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  activeAllergenNoticeText: {
    fontSize: 12,
    color: PREMIUM_COLORS.status.avoid,
    fontWeight: '600',
    flex: 1,
  },

  // Biometrics & Calculator
  biometricsRow: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 8,
  },
  calculatorResultBox: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 14,
    marginTop: 8,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  calcMetricsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  calcMetricItem: {
    flex: 1,
    alignItems: 'center',
  },
  calcMetricLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: PREMIUM_COLORS.secondary,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  calcMetricValue: {
    fontSize: 19,
    fontWeight: '800',
    color: PREMIUM_COLORS.ink,
    marginTop: 2,
  },
  calcMetricUnit: {
    fontSize: 10,
    color: PREMIUM_COLORS.muted,
  },
  calcDivider: {
    width: 1,
    height: 30,
    backgroundColor: PREMIUM_COLORS.border,
  },
  goalNoteText: {
    fontSize: 12,
    fontWeight: '500',
    color: PREMIUM_COLORS.ink,
    marginTop: 10,
    textAlign: 'center',
  },
  applyCalcBtn: {
    marginTop: 10,
    backgroundColor: PREMIUM_COLORS.primaryLight,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.safeBorder,
    borderRadius: PREMIUM_RADIUS.pill,
    paddingVertical: 8,
    paddingHorizontal: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
  },
  applyCalcBtnText: {
    fontSize: 12,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
  },
  disclaimerText: {
    fontSize: 10,
    color: PREMIUM_COLORS.muted,
    marginTop: 8,
    textAlign: 'center',
    fontStyle: 'italic',
    lineHeight: 14,
  },
  biometricHint: {
    fontSize: 12,
    color: PREMIUM_COLORS.muted,
    fontStyle: 'italic',
    marginTop: 4,
  },

  // Actions
  actionsContainer: {
    marginTop: 10,
    gap: 12,
  },
  logoutBtn: {
    flexDirection: 'row',
    paddingVertical: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  logoutBtnText: {
    fontSize: 14,
    fontWeight: '600',
    color: PREMIUM_COLORS.status.avoid,
  },
});
