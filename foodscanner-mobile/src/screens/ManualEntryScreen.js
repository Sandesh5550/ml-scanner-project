import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, Alert, TouchableOpacity, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { analyzeManualProduct, getNetworkErrorMessage } from '../services/api';
import { validateManualNutritionForm, formatNutritionValue } from '../utils/nutritionValidation';
import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';
import { NeoButton, NeoInput } from '../components/neo';

export default function ManualEntryScreen({ navigation, route }) {
  const isMountedRef = useRef(true);
  const abortControllerRef = useRef(null);

  const [productName, setProductName] = useState('');
  const [calories, setCalories] = useState('');
  const [protein, setProtein] = useState('');
  const [carbs, setCarbs] = useState('');
  const [sugar, setSugar] = useState('');
  const [fat, setFat] = useState('');
  const [saturatedFat, setSaturatedFat] = useState('');
  const [fiber, setFiber] = useState('');
  const [salt, setSalt] = useState('');

  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState(null);

  // Unmount safety: abort in-flight analysis requests
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
    };
  }, []);

  // Prefill or reset state on route parameter changes
  useEffect(() => {
    const prefillName = route?.params?.productName;
    const prefillNutrition = route?.params?.nutrition;
    const prefillCalories = route?.params?.calories;

    if (prefillName != null) {
      setProductName(String(prefillName));
    }
    if (prefillCalories != null) {
      setCalories(formatNutritionValue(prefillCalories, ''));
    }
    if (prefillNutrition && typeof prefillNutrition === 'object') {
      if (prefillNutrition.calories != null) setCalories(formatNutritionValue(prefillNutrition.calories, ''));
      if (prefillNutrition.protein != null) setProtein(formatNutritionValue(prefillNutrition.protein, ''));
      if (prefillNutrition.carbs != null) setCarbs(formatNutritionValue(prefillNutrition.carbs, ''));
      if (prefillNutrition.sugar != null) setSugar(formatNutritionValue(prefillNutrition.sugar, ''));
      if (prefillNutrition.fat != null) setFat(formatNutritionValue(prefillNutrition.fat, ''));
      if (prefillNutrition.saturated_fat != null) setSaturatedFat(formatNutritionValue(prefillNutrition.saturated_fat, ''));
      else if (prefillNutrition.saturatedFat != null) setSaturatedFat(formatNutritionValue(prefillNutrition.saturatedFat, ''));
      if (prefillNutrition.fiber != null) setFiber(formatNutritionValue(prefillNutrition.fiber, ''));
      if (prefillNutrition.salt != null) setSalt(formatNutritionValue(prefillNutrition.salt, ''));
      else if (prefillNutrition.sodium != null) setSalt(formatNutritionValue(prefillNutrition.sodium, ''));
    }

    setErrors({});
    setApiError(null);
  }, [route?.params]);

  const clearFieldError = (fieldName) => {
    if (errors[fieldName]) {
      setErrors((prev) => {
        const next = { ...prev };
        delete next[fieldName];
        return next;
      });
    }
    if (apiError) setApiError(null);
  };

  const onSubmit = async () => {
    // 1. Duplicate submission guard
    if (loading) return;

    // 2. Client-side input validation
    const validation = validateManualNutritionForm({
      productName,
      calories,
      protein,
      carbs,
      sugar,
      fat,
      saturatedFat,
      fiber,
      salt,
    });

    if (!validation.isValid) {
      setErrors(validation.errors);
      const firstError = Object.values(validation.errors)[0];
      if (Platform.OS !== 'web' && firstError) {
        Alert.alert('Validation Error', firstError);
      }
      return;
    }

    setErrors({});
    setApiError(null);
    setLoading(true);

    try {
      const payload = {
        ...validation.values,
      };
      if (route?.params?.ingredients) {
        payload.ingredients = route.params.ingredients;
      } else if (route?.params?.ocr?.ingredients?.raw_text) {
        payload.ingredients = route.params.ocr.ingredients.raw_text;
      }

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      const abortController = new AbortController();
      abortControllerRef.current = abortController;

      const analyzed = await analyzeManualProduct(payload, { signal: abortController.signal });

      if (!isMountedRef.current) return;

      // Construct canonical ResultScreen state
      const result = {
        ...analyzed,
        product: {
          ...(analyzed.product || {}),
          name: analyzed.product?.name || payload.product_name,
          nutrition: analyzed.product?.nutrition || {
            calories: payload.calories,
            fat: payload.fat,
            saturated_fat: payload.saturated_fat,
            sugar: payload.sugar,
            salt: payload.salt,
            protein: payload.protein,
            fiber: payload.fiber,
            carbs: payload.carbs,
          },
          barcode: null, // Manual entry does not have a fake barcode
        },
        analysis: analyzed.analysis || {},
        decision: analyzed.decision || {},
        diet_note: analyzed.diet_note || null,
        recommendations: analyzed.recommendations || [],
        health_score: analyzed.analysis?.health_score ?? analyzed.health_score ?? 0,
        final_decision: analyzed.decision?.final_decision ?? analyzed.final_decision ?? 'SAFE',
        reasons: analyzed.decision?.reasons ?? analyzed.reasons ?? [],
      };

      navigation.navigate('Result', { result, timestamp: Date.now() });
    } catch (e) {
      if (e?.name === 'AbortError' || e?.name === 'CanceledError') {
        return;
      }
      if (!isMountedRef.current) return;
      const msg = getNetworkErrorMessage(e) || 'Manual nutrition analysis failed. Please try again.';
      setApiError(msg);
      if (Platform.OS !== 'web') {
        Alert.alert('Analysis Notice', String(msg));
      }
    } finally {
      if (isMountedRef.current) {
        setLoading(false);
      }
    }
  };

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* Navigation Top */}
        <View style={styles.navBar}>
          <TouchableOpacity
            style={[styles.backBtn, PREMIUM_SHADOWS.sm]}
            onPress={() => {
              if (navigation?.canGoBack && navigation.canGoBack()) {
                navigation.goBack();
              } else {
                navigation.navigate('Main', { screen: 'Scan' });
              }
            }}
            activeOpacity={0.8}
          >
            <Ionicons name="arrow-back" size={16} color={PREMIUM_COLORS.ink} style={{ marginRight: 4 }} />
            <Text style={styles.backBtnText}>Back</Text>
          </TouchableOpacity>
        </View>

        {/* Header */}
        <View style={styles.header}>
          <View style={styles.categoryBadge}>
            <View style={styles.badgeDot} />
            <Text style={styles.badgeText}>CUSTOM DATA</Text>
          </View>
          <Text style={styles.screenTitle}>Manual Nutrition Entry</Text>
          <Text style={styles.screenSub}>
            Type declared nutrition metrics per 100g to run ML NutriScore and FSSAI analysis.
          </Text>
        </View>

        {apiError ? (
          <View style={styles.apiErrorBox}>
            <Ionicons name="alert-circle-outline" size={16} color={PREMIUM_COLORS.status.avoid} />
            <Text style={styles.apiErrorText}>{apiError}</Text>
          </View>
        ) : null}

        {/* Form Card */}
        <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
          <NeoInput
            label="Product Name *"
            placeholder="e.g. Handmade Granola"
            value={productName}
            onChangeText={(text) => {
              setProductName(text);
              clearFieldError('productName');
            }}
            error={errors.productName}
          />

          <View style={styles.row}>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Calories (kcal)"
                placeholder="450"
                value={calories}
                onChangeText={(text) => {
                  setCalories(text);
                  clearFieldError('calories');
                }}
                error={errors.calories}
                keyboardType="numeric"
              />
            </View>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Protein (g)"
                placeholder="12.5"
                value={protein}
                onChangeText={(text) => {
                  setProtein(text);
                  clearFieldError('protein');
                }}
                error={errors.protein}
                keyboardType="numeric"
              />
            </View>
          </View>

          <View style={styles.row}>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Carbohydrates (g)"
                placeholder="60.0"
                value={carbs}
                onChangeText={(text) => {
                  setCarbs(text);
                  clearFieldError('carbs');
                }}
                error={errors.carbs}
                keyboardType="numeric"
              />
            </View>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Total Sugar (g)"
                placeholder="15.0"
                value={sugar}
                onChangeText={(text) => {
                  setSugar(text);
                  clearFieldError('sugar');
                }}
                error={errors.sugar}
                keyboardType="numeric"
              />
            </View>
          </View>

          <View style={styles.row}>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Total Fat (g)"
                placeholder="18.0"
                value={fat}
                onChangeText={(text) => {
                  setFat(text);
                  clearFieldError('fat');
                }}
                error={errors.fat}
                keyboardType="numeric"
              />
            </View>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Saturated Fat (g)"
                placeholder="2.5"
                value={saturatedFat}
                onChangeText={(text) => {
                  setSaturatedFat(text);
                  clearFieldError('saturatedFat');
                }}
                error={errors.saturatedFat}
                keyboardType="numeric"
              />
            </View>
          </View>

          <View style={styles.row}>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Fiber (g)"
                placeholder="6.0"
                value={fiber}
                onChangeText={(text) => {
                  setFiber(text);
                  clearFieldError('fiber');
                }}
                error={errors.fiber}
                keyboardType="numeric"
              />
            </View>
            <View style={{ flex: 1 }}>
              <NeoInput
                label="Salt / Sodium (g)"
                placeholder="0.4"
                value={salt}
                onChangeText={(text) => {
                  setSalt(text);
                  clearFieldError('salt');
                }}
                error={errors.salt}
                keyboardType="numeric"
              />
            </View>
          </View>

          <NeoButton
            title="Analyze Nutrition Facts →"
            onPress={onSubmit}
            loading={loading}
            disabled={loading}
            variant="black"
            size="lg"
            style={{ marginTop: 8 }}
          />
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
    paddingTop: 48,
    paddingBottom: 40,
  },
  navBar: {
    marginBottom: 12,
  },
  backBtn: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.card,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: PREMIUM_RADIUS.pill,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  backBtnText: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
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
    fontSize: 26,
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
  apiErrorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.avoidBorder,
    borderRadius: PREMIUM_RADIUS.md,
    padding: 12,
    marginBottom: 16,
    gap: 8,
  },
  apiErrorText: {
    flex: 1,
    fontSize: 13,
    color: PREMIUM_COLORS.status.avoid,
    fontWeight: '600',
    lineHeight: 18,
  },
  card: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 20,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  row: {
    flexDirection: 'row',
    gap: 12,
  },
});
