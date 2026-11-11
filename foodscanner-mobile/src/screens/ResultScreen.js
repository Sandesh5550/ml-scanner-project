import React, { useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  TextInput,
  Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { logFoodItem, scanProduct, analyzeManualProduct, getNetworkErrorMessage } from '../services/api';
import ClaimVerificationCard from '../components/ClaimVerificationCard';
import HealthierAlternativesCard from '../components/HealthierAlternativesCard';
import ComparisonCard from '../components/ComparisonCard';
import AIChatSection from '../components/AIChatSection';
import { ScoreRing, FoodImage } from '../components/premium';
import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';
import { NeoTab } from '../components/neo';
import { formatNutritionValue, isNutrientPresent } from '../utils/formatters';
import {
  ASSESSMENT_STATUS,
  normalizeAssessmentStatus,
  getDecisionMeta,
} from '../utils/assessment';
import {
  AssessmentStatusBadge,
  ConfidenceBadge,
  ScoreRangeCard,
  UnassessableCard,
  MissingNutrientsCard,
} from '../components/assessment';

function _calculateServingNutrition(baseNutrition100g, servingGrams) {
  if (!baseNutrition100g || !servingGrams || servingGrams <= 0) return null;
  const ratio = servingGrams / 100.0;
  const result = {};
  for (const [key, val] of Object.entries(baseNutrition100g)) {
    if (val === null || val === undefined || isNaN(val)) {
      result[key] = null;
    } else {
      result[key] = Math.round(Number(val) * ratio * 10) / 10;
    }
  }
  return result;
}

export default function ResultScreen({ route, navigation }) {
  const result = route?.params?.result;
  const barcode = result?.product?.barcode || result?.barcode;

  const rawDecision = result?.decision?.final_decision || result?.final_decision || null;
  const rawScore = result?.analysis?.health_score !== undefined ? result?.analysis?.health_score : result?.health_score;
  const isScoreNumeric = rawScore !== null && rawScore !== undefined && !isNaN(rawScore) && Number.isFinite(Number(rawScore));
  const healthScore = isScoreNumeric ? Number(rawScore) : null;
  const scoreRange = result?.score_range || result?.analysis?.score_range || null;
  const rawStatus = result?.assessment_status || result?.analysis?.assessment_status || null;
  const confidence = result?.overall_confidence || result?.confidence || result?.product?.data_confidence || null;

  const productName = result?.product?.name || result?.product_name || 'Food Product';
  const brand = result?.product?.brand || result?.brand || '';
  const imageUrl = result?.product?.image_url || result?.image_url;
  const rawNutrition = useMemo(() => {
    return result?.product?.nutrition || result?.nutrition || {};
  }, [result]);

  const assessmentStatus = useMemo(() => {
    return normalizeAssessmentStatus(rawStatus, {
      healthScore,
      scoreRange,
      nutrition: rawNutrition,
    });
  }, [rawStatus, healthScore, scoreRange, rawNutrition]);

  const decisionMeta = useMemo(() => {
    return getDecisionMeta(rawDecision, assessmentStatus);
  }, [rawDecision, assessmentStatus]);

  const missingNutrientsList = useMemo(() => {
    return result?.missing_mandatory_nutrients || result?.explanation?.missing_mandatory_nutrients || null;
  }, [result]);

  const ingredientAnalysis = result?.analysis?.ingredient_analysis || result?.ingredient_analysis;
  const additiveAnalysis = result?.analysis?.additive_analysis || result?.additive_analysis;
  const dietNote = result?.diet_note;

  const allergenWarnings = result?.allergen_warnings || result?.personalized_analysis?.allergen_warnings || [];
  const detectedAllergens = result?.detected_allergens || result?.personalized_analysis?.detected_allergens || [];
  const criticalAllergens = allergenWarnings.filter((w) => w?.severity === 'CRITICAL' || w?.user_match);

  const [activePortionTab, setActivePortionTab] = useState('100g');
  const [servingGrams, setServingGrams] = useState('100');
  const [loggedToday, setLoggedToday] = useState(false);
  const [logging, setLogging] = useState(false);

  // Expandable ingredients state
  const [expandedIngredients, setExpandedIngredients] = useState({});

  // Reset local interactive state when opening a new product
  useEffect(() => {
    setActivePortionTab('100g');
    setServingGrams('100');
    setLoggedToday(false);
    setLogging(false);
    setExpandedIngredients({});
  }, [productName, barcode, route?.params?.timestamp]);

  const toggleIngredient = (idx) => {
    setExpandedIngredients((prev) => ({
      ...prev,
      [idx]: !prev[idx],
    }));
  };

  const currentNutrition = useMemo(() => {
    if (activePortionTab === '100g') {
      return rawNutrition;
    }
    const grams = parseFloat(servingGrams) || 100;
    const scaled = _calculateServingNutrition(rawNutrition, grams);
    return scaled || rawNutrition;
  }, [rawNutrition, activePortionTab, servingGrams]);

  const caloriesVal = currentNutrition?.calories;
  const proteinVal = currentNutrition?.protein;
  const carbsVal = currentNutrition?.carbs;
  const sugarVal = currentNutrition?.sugar;
  const fatVal = currentNutrition?.fat;
  const saltVal = currentNutrition?.salt !== null && currentNutrition?.salt !== undefined
    ? currentNutrition.salt
    : currentNutrition?.sodium;

  const handleLogIntake = async () => {
    if (logging || loggedToday) return;
    setLogging(true);
    try {
      const baseServing = activePortionTab === '100g' ? 100 : parseFloat(servingGrams) || 100;
      const loggedCalories = rawNutrition?.calories != null
        ? Number(rawNutrition.calories)
        : (isNutrientPresent(caloriesVal) ? Number(caloriesVal) : 0);

      await logFoodItem({
        product_name: productName,
        calories: loggedCalories,
        fat: rawNutrition?.fat != null ? Number(rawNutrition.fat) : null,
        sugar: rawNutrition?.sugar != null ? Number(rawNutrition.sugar) : null,
        salt: rawNutrition?.salt != null ? Number(rawNutrition.salt) : (rawNutrition?.sodium != null ? Number(rawNutrition.sodium) : null),
        protein: rawNutrition?.protein != null ? Number(rawNutrition.protein) : null,
        fiber: rawNutrition?.fiber != null ? Number(rawNutrition.fiber) : null,
        carbs: rawNutrition?.carbs != null ? Number(rawNutrition.carbs) : null,
        serving_size: baseServing,
        barcode: barcode && String(barcode) !== '00000000' ? String(barcode) : null,
        nutrition: currentNutrition,
      });
      setLoggedToday(true);
      const calDisplay = isNutrientPresent(caloriesVal) ? `${formatNutritionValue(caloriesVal)} kcal` : 'recorded portion';
      Alert.alert('Logged to Daily Diary', `${productName} (${calDisplay}) has been recorded in your daily intake diary.`);

    } catch (e) {
      const msg = getNetworkErrorMessage(e);
      Alert.alert('Diary Error', String(msg));
    } finally {
      setLogging(false);
    }
  };

  // Ingredients and Additives lists
  const ingredientsList = useMemo(() => {
    if (Array.isArray(ingredientAnalysis?.flagged_ingredients)) {
      return ingredientAnalysis.flagged_ingredients;
    }
    if (Array.isArray(ingredientAnalysis)) {
      return ingredientAnalysis;
    }
    return [];
  }, [ingredientAnalysis]);

  const additivesList = useMemo(() => {
    if (Array.isArray(additiveAnalysis?.flagged_additives)) {
      return additiveAnalysis.flagged_additives;
    }
    if (Array.isArray(additiveAnalysis)) {
      return additiveAnalysis;
    }
    return [];
  }, [additiveAnalysis]);

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
      >
        {/* Navigation Bar */}
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

          {brand ? (
            <View style={styles.brandPill}>
              <Text style={styles.brandPillText} numberOfLines={1}>{brand}</Text>
            </View>
          ) : null}
        </View>

        {/* 1. Product Hero Header */}
        <View style={styles.heroCard}>
          <View style={styles.imageCenterWrap}>
            <FoodImage
              source={imageUrl}
              productName={productName}
              size={130}
              height={130}
              borderRadius={PREMIUM_RADIUS.xl}
            />
          </View>
          <Text style={styles.productTitle}>{productName}</Text>
          {barcode && String(barcode) !== '00000000' ? (
            <Text style={styles.barcodeText}>Barcode: {barcode}</Text>
          ) : null}

          {/* 2. Assessment Status Badge */}
          <View style={styles.statusBadgeWrap}>
            <AssessmentStatusBadge status={assessmentStatus} showDescription={false} />
          </View>
        </View>

        {/* Critical Allergen Safety Alert Banner */}
        {criticalAllergens.length > 0 ? (
          <View style={[styles.allergenAlertBanner, PREMIUM_SHADOWS.sm]}>
            <View style={styles.allergenAlertTop}>
              <Ionicons name="warning" size={18} color={PREMIUM_COLORS.status.avoid} />
              <Text style={styles.allergenAlertTitle}>CRITICAL ALLERGEN WARNING</Text>
            </View>
            <Text style={styles.allergenAlertBody}>
              {criticalAllergens.map((c) => c.warning || `Contains ${c.display_name || c.allergen}`).join('\n')}
            </Text>
          </View>
        ) : detectedAllergens.length > 0 ? (
          <View style={styles.allergenInfoBanner}>
            <Ionicons name="information-circle-outline" size={15} color={PREMIUM_COLORS.secondary} />
            <Text style={styles.allergenInfoText}>
              Allergens detected: {detectedAllergens.map((a) => a.replace('_', ' ')).join(', ')}
            </Text>
          </View>
        ) : null}

        {/* 3, 4, 5. Health Score / Score Range / Decision / Confidence Card */}
        {assessmentStatus === ASSESSMENT_STATUS.COMPLETE ? (
          <View style={[styles.scoreCard, PREMIUM_SHADOWS.sm]}>
            <ScoreRing
              score={healthScore}
              decision={decisionMeta.isValid ? decisionMeta.label : null}
              size={150}
              animated={true}
              showDecision={decisionMeta.isValid}
            />

            <View style={styles.metaRowUnderScore}>
              <ConfidenceBadge confidence={confidence} showDescription={true} />
            </View>

            {dietNote ? (
              <View style={styles.dietNoteBox}>
                <Ionicons name="bulb-outline" size={16} color={PREMIUM_COLORS.primaryDark} />
                <Text style={styles.dietNoteText}>{dietNote}</Text>
              </View>
            ) : null}
          </View>
        ) : assessmentStatus === ASSESSMENT_STATUS.PARTIAL ? (
          <View style={styles.assessmentWrapper}>
            <ScoreRangeCard scoreRange={scoreRange} />

            {/* Decision: only displayed if AVOID is mandated by worst-case bounds or independent rule */}
            {decisionMeta.isValid && decisionMeta.label === 'AVOID' ? (
              <View style={[styles.partialAvoidBanner, PREMIUM_SHADOWS.sm]}>
                <Ionicons name="alert-circle" size={18} color={PREMIUM_COLORS.status.avoid} />
                <View style={{ flex: 1, marginLeft: 8 }}>
                  <Text style={styles.partialAvoidTitle}>DECISION: AVOID</Text>
                  <Text style={styles.partialAvoidText}>
                    {decisionMeta.sub || 'High risk profile detected despite partial nutritional disclosure.'}
                  </Text>
                </View>
              </View>
            ) : null}

            <View style={styles.metaRowUnderScore}>
              <ConfidenceBadge confidence={confidence} showDescription={true} />
            </View>

            {dietNote ? (
              <View style={styles.dietNoteBox}>
                <Ionicons name="bulb-outline" size={16} color={PREMIUM_COLORS.primaryDark} />
                <Text style={styles.dietNoteText}>{dietNote}</Text>
              </View>
            ) : null}
          </View>
        ) : assessmentStatus === ASSESSMENT_STATUS.UNASSESSABLE ? (
          <View style={styles.assessmentWrapper}>
            <UnassessableCard />

            <View style={styles.metaRowUnderScore}>
              <ConfidenceBadge confidence={confidence} showDescription={true} />
            </View>

            {dietNote ? (
              <View style={styles.dietNoteBox}>
                <Ionicons name="bulb-outline" size={16} color={PREMIUM_COLORS.primaryDark} />
                <Text style={styles.dietNoteText}>{dietNote}</Text>
              </View>
            ) : null}
          </View>
        ) : (
          /* UNKNOWN status fallback */
          <View style={styles.assessmentWrapper}>
            {isScoreNumeric ? (
              <View style={[styles.scoreCard, PREMIUM_SHADOWS.sm]}>
                <ScoreRing
                  score={healthScore}
                  decision={decisionMeta.isValid ? decisionMeta.label : null}
                  size={150}
                  animated={true}
                  showDecision={decisionMeta.isValid}
                />
              </View>
            ) : (
              <UnassessableCard />
            )}

            <View style={styles.metaRowUnderScore}>
              <ConfidenceBadge confidence={confidence} showDescription={true} />
            </View>

            {dietNote ? (
              <View style={styles.dietNoteBox}>
                <Ionicons name="bulb-outline" size={16} color={PREMIUM_COLORS.primaryDark} />
                <Text style={styles.dietNoteText}>{dietNote}</Text>
              </View>
            ) : null}
          </View>
        )}

        {/* 6. Nutritional Information Breakdown */}
        <View style={styles.portionSection}>
          <View style={styles.sectionHeaderRow}>
            <Text style={styles.sectionTitle}>Nutrition Breakdown</Text>
            <Text style={styles.portionHint}>
              {activePortionTab === '100g' ? 'Per 100g serving' : `Per ${servingGrams}g custom serving`}
            </Text>
          </View>

          <NeoTab
            tabs={[
              { key: '100g', label: '100g Standard' },
              { key: 'custom', label: 'Custom Serving' },
            ]}
            activeTab={activePortionTab}
            onTabChange={setActivePortionTab}
          />

          {activePortionTab === 'custom' ? (
            <View style={styles.customPortionInputRow}>
              <Text style={styles.customPortionLabel}>Serving Size (grams):</Text>
              <TextInput
                style={styles.customInput}
                keyboardType="numeric"
                value={servingGrams}
                onChangeText={setServingGrams}
                maxLength={4}
              />
              <Text style={styles.customUnit}>g</Text>
            </View>
          ) : null}
        </View>

        {/* 6 Grid Nutrition Cards */}
        <View style={styles.nutritionGrid}>
          {/* Calories */}
          <View style={[styles.nutrientCard, PREMIUM_SHADOWS.sm]}>
            <View style={styles.nutrientTop}>
              <Ionicons name="flame-outline" size={14} color="#EA580C" />
              <Text style={styles.nutrientName}>Calories</Text>
            </View>
            {isNutrientPresent(caloriesVal) ? (
              <>
                <Text style={styles.nutrientValue}>{formatNutritionValue(caloriesVal)}</Text>
                <Text style={styles.nutrientUnit}>kcal</Text>
                <View style={styles.nutrientTrack}>
                  <View
                    style={[
                      styles.nutrientFill,
                      {
                        width: `${Math.min(100, (Number(caloriesVal) / 600) * 100)}%`,
                        backgroundColor: Number(caloriesVal) > 400 ? PREMIUM_COLORS.status.avoid : PREMIUM_COLORS.primaryDark,
                      },
                    ]}
                  />
                </View>
              </>
            ) : (
              <>
                <Text style={styles.nutrientMissingValue}>Not disclosed</Text>
                <Text style={styles.nutrientUnitMuted}>energy</Text>
                <View style={[styles.nutrientTrack, styles.nutrientTrackDisabled]} />
              </>
            )}
          </View>

          {/* Protein */}
          <View style={[styles.nutrientCard, PREMIUM_SHADOWS.sm]}>
            <View style={styles.nutrientTop}>
              <Ionicons name="leaf-outline" size={14} color={PREMIUM_COLORS.primaryDark} />
              <Text style={styles.nutrientName}>Protein</Text>
            </View>
            {isNutrientPresent(proteinVal) ? (
              <>
                <Text style={styles.nutrientValue}>{formatNutritionValue(proteinVal)}g</Text>
                <Text style={styles.nutrientUnit}>target 10g+</Text>
                <View style={styles.nutrientTrack}>
                  <View
                    style={[
                      styles.nutrientFill,
                      {
                        width: `${Math.min(100, (Number(proteinVal) / 20) * 100)}%`,
                        backgroundColor: PREMIUM_COLORS.primary,
                      },
                    ]}
                  />
                </View>
              </>
            ) : (
              <>
                <Text style={styles.nutrientMissingValue}>Not disclosed</Text>
                <Text style={styles.nutrientUnitMuted}>protein</Text>
                <View style={[styles.nutrientTrack, styles.nutrientTrackDisabled]} />
              </>
            )}
          </View>

          {/* Carbs */}
          <View style={[styles.nutrientCard, PREMIUM_SHADOWS.sm]}>
            <View style={styles.nutrientTop}>
              <Ionicons name="nutrition-outline" size={14} color="#D97706" />
              <Text style={styles.nutrientName}>Carbs</Text>
            </View>
            {isNutrientPresent(carbsVal) ? (
              <>
                <Text style={styles.nutrientValue}>{formatNutritionValue(carbsVal)}g</Text>
                <Text style={styles.nutrientUnit}>energy</Text>
                <View style={styles.nutrientTrack}>
                  <View
                    style={[
                      styles.nutrientFill,
                      {
                        width: `${Math.min(100, (Number(carbsVal) / 80) * 100)}%`,
                        backgroundColor: '#E8B342',
                      },
                    ]}
                  />
                </View>
              </>
            ) : (
              <>
                <Text style={styles.nutrientMissingValue}>Not disclosed</Text>
                <Text style={styles.nutrientUnitMuted}>carbohydrates</Text>
                <View style={[styles.nutrientTrack, styles.nutrientTrackDisabled]} />
              </>
            )}
          </View>

          {/* Sugar */}
          <View style={[styles.nutrientCard, PREMIUM_SHADOWS.sm]}>
            <View style={styles.nutrientTop}>
              <Ionicons name="water-outline" size={14} color="#0D9488" />
              <Text style={styles.nutrientName}>Sugar</Text>
            </View>
            {isNutrientPresent(sugarVal) ? (
              <>
                <Text style={styles.nutrientValue}>{formatNutritionValue(sugarVal)}g</Text>
                <Text style={styles.nutrientUnit}>max 5g recommended</Text>
                <View style={styles.nutrientTrack}>
                  <View
                    style={[
                      styles.nutrientFill,
                      {
                        width: `${Math.min(100, (Number(sugarVal) / 25) * 100)}%`,
                        backgroundColor: Number(sugarVal) > 10 ? PREMIUM_COLORS.status.avoid : '#8FA866',
                      },
                    ]}
                  />
                </View>
              </>
            ) : (
              <>
                <Text style={styles.nutrientMissingValue}>Not disclosed</Text>
                <Text style={styles.nutrientUnitMuted}>sugars</Text>
                <View style={[styles.nutrientTrack, styles.nutrientTrackDisabled]} />
              </>
            )}
          </View>

          {/* Fat */}
          <View style={[styles.nutrientCard, PREMIUM_SHADOWS.sm]}>
            <View style={styles.nutrientTop}>
              <Ionicons name="ellipse-outline" size={14} color="#B45309" />
              <Text style={styles.nutrientName}>Fat</Text>
            </View>
            {isNutrientPresent(fatVal) ? (
              <>
                <Text style={styles.nutrientValue}>{formatNutritionValue(fatVal)}g</Text>
                <Text style={styles.nutrientUnit}>total fat</Text>
                <View style={styles.nutrientTrack}>
                  <View
                    style={[
                      styles.nutrientFill,
                      {
                        width: `${Math.min(100, (Number(fatVal) / 30) * 100)}%`,
                        backgroundColor: Number(fatVal) > 15 ? PREMIUM_COLORS.status.moderate : '#8FA866',
                      },
                    ]}
                  />
                </View>
              </>
            ) : (
              <>
                <Text style={styles.nutrientMissingValue}>Not disclosed</Text>
                <Text style={styles.nutrientUnitMuted}>lipids</Text>
                <View style={[styles.nutrientTrack, styles.nutrientTrackDisabled]} />
              </>
            )}
          </View>

          {/* Salt / Sodium */}
          <View style={[styles.nutrientCard, PREMIUM_SHADOWS.sm]}>
            <View style={styles.nutrientTop}>
              <Ionicons name="cube-outline" size={14} color="#4B5563" />
              <Text style={styles.nutrientName}>Salt / Sodium</Text>
            </View>
            {isNutrientPresent(saltVal) ? (
              <>
                <Text style={styles.nutrientValue}>{formatNutritionValue(saltVal)}g</Text>
                <Text style={styles.nutrientUnit}>salt equivalent</Text>
                <View style={styles.nutrientTrack}>
                  <View
                    style={[
                      styles.nutrientFill,
                      {
                        width: `${Math.min(100, (Number(saltVal) / 2) * 100)}%`,
                        backgroundColor: Number(saltVal) > 1.5 ? PREMIUM_COLORS.status.avoid : '#8FA866',
                      },
                    ]}
                  />
                </View>
              </>
            ) : (
              <>
                <Text style={styles.nutrientMissingValue}>Not disclosed</Text>
                <Text style={styles.nutrientUnitMuted}>salt / sodium</Text>
                <View style={[styles.nutrientTrack, styles.nutrientTrackDisabled]} />
              </>
            )}
          </View>
        </View>

        {/* 7. Missing Mandatory Nutrient Disclosures */}
        <MissingNutrientsCard
          nutrition={rawNutrition}
          missingList={missingNutrientsList}
        />

        {/* 8. Ingredient Intelligence Section */}
        <View style={styles.sectionHeaderRow}>
          <Text style={styles.sectionTitle}>Ingredient Intelligence</Text>
          <Text style={styles.sectionCount}>{ingredientsList.length} items flagged</Text>
        </View>

        {ingredientsList.length > 0 ? (
          <View style={styles.ingredientsContainer}>
            {ingredientsList.map((item, idx) => {
              const ingName = typeof item === 'string' ? item : item?.ingredient || item?.name || `Ingredient ${idx + 1}`;
              const explanation = item?.explanation || item?.reason || item?.concern || 'Used for flavor, texture, or shelf-life stability.';
              const isFlagged = item?.flagged || item?.level === 'HIGH' || item?.is_harmful;
              const isExpanded = Boolean(expandedIngredients[idx]);

              return (
                <TouchableOpacity
                  key={idx}
                  activeOpacity={0.85}
                  style={[styles.ingredientCard, PREMIUM_SHADOWS.sm]}
                  onPress={() => toggleIngredient(idx)}
                >
                  <View style={styles.ingredientTopRow}>
                    <View style={styles.ingredientTitleGroup}>
                      <View
                        style={[
                          styles.ingredientDot,
                          { backgroundColor: isFlagged ? PREMIUM_COLORS.status.avoid : PREMIUM_COLORS.primaryDark },
                        ]}
                      />
                      <Text style={styles.ingredientName}>{ingName}</Text>
                    </View>
                    <Text style={styles.accordionArrow}>{isExpanded ? '▲' : '▼'}</Text>
                  </View>

                  {isExpanded ? (
                    <View style={styles.ingredientBody}>
                      <Text style={styles.ingredientExplanation}>{explanation}</Text>
                    </View>
                  ) : null}
                </TouchableOpacity>
              );
            })}
          </View>
        ) : (
          <View style={[styles.cleanIngredientsCard, PREMIUM_SHADOWS.sm]}>
            <Ionicons name="leaf-outline" size={24} color={PREMIUM_COLORS.primaryDark} style={{ marginBottom: 4 }} />
            <Text style={styles.cleanTitle}>Clean Ingredient Profile</Text>
            <Text style={styles.cleanSub}>No high-risk preservatives, palm oil, or harmful additives detected in this product.</Text>
          </View>
        )}

        {/* Additives Analysis Section */}
        {additivesList.length > 0 ? (
          <View style={styles.additivesSection}>
            <Text style={styles.subSectionTitle}>Additives & E-Numbers</Text>
            <View style={styles.additivesChipsRow}>
              {additivesList.map((add, idx) => {
                const addName = typeof add === 'string' ? add : add?.code || add?.name || `Additive ${idx + 1}`;
                return (
                  <View key={idx} style={styles.additiveChip}>
                    <Ionicons name="alert-circle-outline" size={12} color={PREMIUM_COLORS.status.avoid} />
                    <Text style={styles.additiveChipText}>{addName}</Text>
                  </View>
                );
              })}
            </View>
          </View>
        ) : null}

        {/* 9. Recommendations, Comparisons & AI Assistant */}
        {/* FSSAI Statutory Claim Verification Card */}
        <ClaimVerificationCard
          initialVerification={result?.claims_verification || result?.claim_verification}
          barcode={barcode}
          nutrition={rawNutrition}
          ingredients={result?.product?.ingredients || result?.ingredients}
          productName={productName}
        />

        {/* Healthier Alternatives Smart Swaps */}
        <HealthierAlternativesCard
          initialRecommendations={result?.recommendations || []}
          barcode={barcode}
          productName={productName}
          nutrition={rawNutrition}
          onSelectAlternative={async (alt) => {
            if (!alt) return;
            try {
              let altResult;
              const hasBarcode = alt?.barcode && String(alt.barcode) !== '00000000' && /^\d{8,14}$/.test(String(alt.barcode).trim());
              if (hasBarcode) {
                altResult = await scanProduct(String(alt.barcode).trim(), alt.name || null);
              } else if (alt.name) {
                altResult = await analyzeManualProduct({ product_name: alt.name });
              }
              if (altResult) {
                if (typeof navigation.push === 'function') {
                  navigation.push('Result', { result: altResult, timestamp: Date.now() });
                } else {
                  navigation.navigate('Result', { result: altResult, timestamp: Date.now() });
                }
              }
            } catch (_err) {
              Alert.alert('Alternative Product', 'Could not load complete analysis for this alternative.');
            }
          }}
        />

        {/* Nutritional Head-to-Head Comparison */}
        <ComparisonCard
          currentProduct={result?.product || { name: productName, barcode, nutrition: rawNutrition }}
          alternatives={result?.recommendations}
        />

        {/* Conversational AI Nutrition Assistant */}
        <AIChatSection
          barcode={barcode}
          productName={productName}
          nutrition={currentNutrition}
          healthScore={healthScore}
          decision={decisionMeta.isValid ? decisionMeta.label : null}
        />

        {/* Explicit Intake Logging Action Button (Scan ≠ Eat Invariant) */}
        <View style={styles.diaryActionContainer}>
          <TouchableOpacity
            activeOpacity={0.88}
            style={[
              styles.logDiaryBtn,
              loggedToday ? styles.logDiaryBtnSuccess : null,
              PREMIUM_SHADOWS.md,
            ]}
            onPress={handleLogIntake}
            disabled={logging || loggedToday}
          >
            {logging ? (
              <ActivityIndicator size="small" color={PREMIUM_COLORS.white} />
            ) : loggedToday ? (
              <View style={styles.logDiaryContent}>
                <Text style={styles.logDiaryIcon}>✓</Text>
                <Text style={styles.logDiaryText}>{"Logged to Today's Diary"}</Text>
              </View>
            ) : (
              <View style={styles.logDiaryContent}>
                <Text style={styles.logDiaryIcon}>+ </Text>
                <Text style={styles.logDiaryText}>Log to Daily Diary</Text>
              </View>
            )}
          </TouchableOpacity>
          <Text style={styles.scanNotEatDisclaimer}>
            Scan ≠ Eat: This product is not recorded in your calorie intake until you tap the button above.
          </Text>
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
    paddingBottom: 100,
  },

  // Navigation Bar
  navBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 16,
  },
  backBtn: {
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
  brandPill: {
    backgroundColor: PREMIUM_COLORS.primaryLight,
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: PREMIUM_RADIUS.pill,
  },
  brandPillText: {
    fontSize: 11,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
    textTransform: 'uppercase',
  },

  // Product Hero
  heroCard: {
    alignItems: 'center',
    marginBottom: 16,
  },
  imageCenterWrap: {
    marginBottom: 12,
  },
  productTitle: {
    fontSize: 24,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    textAlign: 'center',
    letterSpacing: -0.4,
    lineHeight: 30,
  },
  barcodeText: {
    fontSize: 13,
    fontWeight: '500',
    color: PREMIUM_COLORS.secondary,
    marginTop: 4,
  },
  statusBadgeWrap: {
    marginTop: 8,
    alignItems: 'center',
  },

  // Allergen Alert Banners
  allergenAlertBanner: {
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1.5,
    borderColor: PREMIUM_COLORS.status.avoidBorder,
  },
  allergenAlertTop: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 6,
  },
  allergenAlertTitle: {
    fontSize: 13,
    fontWeight: '800',
    color: PREMIUM_COLORS.status.avoid,
    letterSpacing: 0.6,
  },
  allergenAlertBody: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.status.avoid,
    lineHeight: 18,
  },
  allergenInfoBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.md,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginBottom: 14,
    gap: 6,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  allergenInfoText: {
    fontSize: 12,
    color: PREMIUM_COLORS.secondary,
    fontWeight: '500',
  },

  // Score & Assessment Cards
  scoreCard: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 20,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  assessmentWrapper: {
    marginBottom: 16,
  },
  metaRowUnderScore: {
    alignItems: 'center',
    marginTop: 8,
  },
  partialAvoidBanner: {
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 12,
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 8,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.avoidBorder,
  },
  partialAvoidTitle: {
    fontSize: 12,
    fontWeight: '800',
    color: PREMIUM_COLORS.status.avoid,
    letterSpacing: 0.5,
  },
  partialAvoidText: {
    fontSize: 12,
    fontWeight: '500',
    color: PREMIUM_COLORS.status.avoid,
    lineHeight: 16,
    marginTop: 2,
  },
  dietNoteBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.md,
    padding: 12,
    marginTop: 12,
    gap: 8,
  },
  dietNoteText: {
    flex: 1,
    fontSize: 13,
    fontWeight: '500',
    color: PREMIUM_COLORS.ink,
    lineHeight: 18,
  },

  // Portion Section
  portionSection: {
    marginBottom: 10,
  },
  sectionHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.2,
  },
  portionHint: {
    fontSize: 13,
    fontWeight: '500',
    color: PREMIUM_COLORS.secondary,
  },
  customPortionInputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.card,
    padding: 10,
    borderRadius: PREMIUM_RADIUS.md,
    marginBottom: 10,
    gap: 8,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  customPortionLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.ink,
  },
  customInput: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.sm,
    paddingHorizontal: 10,
    paddingVertical: 4,
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    width: 60,
    textAlign: 'center',
  },
  customUnit: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },

  // Nutrition Cards
  nutritionGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
    marginBottom: 14,
  },
  nutrientCard: {
    width: '48%',
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 12,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  nutrientTop: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 4,
  },
  nutrientName: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },
  nutrientValue: {
    fontSize: 19,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.3,
  },
  nutrientMissingValue: {
    fontSize: 14,
    fontWeight: '700',
    color: '#7C837B',
    letterSpacing: -0.2,
    marginVertical: 3,
  },
  nutrientUnit: {
    fontSize: 11,
    fontWeight: '500',
    color: PREMIUM_COLORS.muted,
    marginBottom: 8,
  },
  nutrientUnitMuted: {
    fontSize: 11,
    fontWeight: '500',
    color: '#9CA3AF',
    marginBottom: 8,
    textTransform: 'capitalize',
  },
  nutrientTrack: {
    height: 4,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: 2,
    overflow: 'hidden',
  },
  nutrientTrackDisabled: {
    backgroundColor: '#E2E8F0',
    opacity: 0.6,
  },
  nutrientFill: {
    height: '100%',
    borderRadius: 2,
  },

  // Ingredients Section
  sectionCount: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },
  ingredientsContainer: {
    gap: 8,
    marginBottom: 16,
  },
  ingredientCard: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.md,
    padding: 12,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  ingredientTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  ingredientTitleGroup: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  ingredientDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  ingredientName: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  accordionArrow: {
    fontSize: 11,
    color: PREMIUM_COLORS.secondary,
  },
  ingredientBody: {
    marginTop: 8,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: PREMIUM_COLORS.divider,
  },
  ingredientExplanation: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    lineHeight: 18,
  },
  cleanIngredientsCard: {
    backgroundColor: PREMIUM_COLORS.primaryLight,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 16,
    alignItems: 'center',
    marginBottom: 16,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.safeBorder,
  },
  cleanTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
    marginBottom: 4,
  },
  cleanSub: {
    fontSize: 12,
    color: PREMIUM_COLORS.primaryDark,
    textAlign: 'center',
    lineHeight: 17,
  },

  // Additives Section
  additivesSection: {
    marginBottom: 18,
  },
  subSectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginBottom: 8,
  },
  additivesChipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  additiveChip: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: PREMIUM_RADIUS.pill,
    gap: 4,
  },
  additiveChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.status.avoid,
  },

  // Diary Logging
  diaryActionContainer: {
    marginTop: 10,
    marginBottom: 20,
    alignItems: 'center',
  },
  logDiaryBtn: {
    backgroundColor: PREMIUM_COLORS.ink,
    paddingVertical: 14,
    paddingHorizontal: 24,
    borderRadius: PREMIUM_RADIUS.pill,
    width: '100%',
    alignItems: 'center',
    justifyContent: 'center',
  },
  logDiaryBtnSuccess: {
    backgroundColor: PREMIUM_COLORS.primaryDark,
  },
  logDiaryContent: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  logDiaryIcon: {
    color: PREMIUM_COLORS.white,
    fontSize: 16,
    fontWeight: '700',
    marginRight: 6,
  },
  logDiaryText: {
    color: PREMIUM_COLORS.white,
    fontSize: 15,
    fontWeight: '700',
    letterSpacing: 0.2,
  },
  scanNotEatDisclaimer: {
    fontSize: 11,
    fontWeight: '500',
    color: PREMIUM_COLORS.muted,
    textAlign: 'center',
    marginTop: 8,
    lineHeight: 16,
    paddingHorizontal: 12,
  },
});
