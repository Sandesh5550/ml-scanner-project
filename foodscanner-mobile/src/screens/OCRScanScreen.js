import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, ActivityIndicator, Alert, TouchableOpacity } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { Ionicons } from '@expo/vector-icons';

import {
  submitOcrJob,
  pollOcrJob,
  scanNutritionLabel,
  getNetworkErrorMessage,
  isOcrResultEmpty,
  extractOcrNutrientValues,
  OCR_STATUS,
  OCR_UI_STATE,
} from '../services/api';
import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';
import { NeoButton, NeoBadge, NeoProgressBar } from '../components/neo';
import { formatNutritionValue } from '../utils/formatters';

export default function OCRScanScreen({ navigation, route }) {
  const prefillName = route?.params?.productName;
  const isSyncMode = Boolean(route?.params?.sync);

  const [ocrState, setOcrState] = useState(OCR_UI_STATE.IDLE);
  const [jobId, setJobId] = useState(null);
  const [jobProgress, setJobProgress] = useState({ status: OCR_STATUS.PENDING, elapsedSec: 0, attempt: 0 });
  const [ocrResult, setOcrResult] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);

  const inFlightRef = useRef(false);
  const abortControllerRef = useRef(null);
  const lastAssetRef = useRef(null);
  const isMountedRef = useRef(true);

  // Unmount safety & navigation cleanup: cleanup active controllers and polling timers
  useEffect(() => {
    isMountedRef.current = true;
    const unsubscribe = navigation?.addListener?.('beforeRemove', () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      inFlightRef.current = false;
    });

    return () => {
      isMountedRef.current = false;
      if (typeof unsubscribe === 'function') {
        unsubscribe();
      }
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      inFlightRef.current = false;
    };
  }, [navigation]);

  const resetToIdle = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    inFlightRef.current = false;
    if (!isMountedRef.current) return;
    setOcrState(OCR_UI_STATE.IDLE);
    setJobId(null);
    setJobProgress({ status: OCR_STATUS.PENDING, elapsedSec: 0, attempt: 0 });
    setOcrResult(null);
    setErrorMessage(null);
  };

  const cancelOcrJob = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    inFlightRef.current = false;
    resetToIdle();
  };

  const goManual = (prefill) => {
    const satFat = prefill?.saturated_fat != null
      ? prefill.saturated_fat
      : (prefill?.nutrition?.saturated_fat_g?.value != null ? prefill.nutrition.saturated_fat_g.value : null);

    navigation.navigate('ManualEntry', {
      productName: prefill?.product_name || prefillName || '',
      calories: prefill?.calories != null ? formatNutritionValue(prefill.calories, '') : null,
      nutrition: prefill ? {
        calories: prefill.calories != null ? formatNutritionValue(prefill.calories, '') : null,
        fat: prefill.fat != null ? formatNutritionValue(prefill.fat, '') : null,
        saturated_fat: satFat != null ? formatNutritionValue(satFat, '') : null,
        sugar: prefill.sugar != null ? formatNutritionValue(prefill.sugar, '') : null,
        salt: prefill.salt != null ? formatNutritionValue(prefill.salt, '') : null,
        protein: prefill.protein != null ? formatNutritionValue(prefill.protein, '') : null,
        fiber: prefill.fiber != null ? formatNutritionValue(prefill.fiber, '') : null,
        carbs: prefill.carbs != null ? formatNutritionValue(prefill.carbs, '') : null,
      } : null,
      ocr: prefill || null,
      ingredients: prefill?.ingredients?.raw_text || null,
    });
  };

  const processAsset = async (asset) => {
    if (!asset?.base64) {
      Alert.alert('Image Error', 'Could not read image data. Please try again.');
      goManual(null);
      return;
    }

    if (inFlightRef.current) return;
    inFlightRef.current = true;
    lastAssetRef.current = asset;

    // Abort any prior in-flight poll
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    setErrorMessage(null);
    setOcrResult(null);

    // Support existing synchronous mode if explicitly requested via route params
    if (isSyncMode) {
      setOcrState(OCR_UI_STATE.SUBMITTING);
      try {
        const res = await scanNutritionLabel(asset.base64);
        if (!isMountedRef.current) return;
        if (isOcrResultEmpty(res)) {
          setOcrResult(res || null);
          setOcrState(OCR_UI_STATE.EMPTY);
        } else {
          setOcrResult(res);
          setOcrState(OCR_UI_STATE.SUCCESS);
        }
      } catch (e) {
        if (!isMountedRef.current) return;
        const msg = getNetworkErrorMessage(e);
        setErrorMessage(msg);
        setOcrState(OCR_UI_STATE.FAILED);
      } finally {
        inFlightRef.current = false;
      }
      return;
    }

    // Default Asynchronous Workflow
    setOcrState(OCR_UI_STATE.SUBMITTING);

    try {
      const submitRes = await submitOcrJob(asset.base64, {
        signal: abortController.signal,
        claims: route?.params?.claims || null,
      });

      if (!submitRes?.job_id) {
        throw new Error('Server did not return a valid OCR job identifier');
      }

      if (!isMountedRef.current) return;
      setJobId(submitRes.job_id);
      setJobProgress({
        status: submitRes.status || OCR_STATUS.PENDING,
        elapsedSec: 0,
        attempt: 1,
      });
      setOcrState(OCR_UI_STATE.POLLING);

      const completedJob = await pollOcrJob(submitRes.job_id, {
        signal: abortController.signal,
        intervalMs: 1500,
        timeoutMs: 45000,
        onProgress: ({ status, elapsedMs, attempt }) => {
          if (!isMountedRef.current) return;
          setJobProgress({
            status,
            elapsedSec: Math.floor(elapsedMs / 1000),
            attempt,
          });
        },
      });

      if (!isMountedRef.current) return;
      const extracted = completedJob?.result;
      if (isOcrResultEmpty(extracted)) {
        setOcrResult(extracted || null);
        setOcrState(OCR_UI_STATE.EMPTY);
      } else {
        setOcrResult(extracted);
        setOcrState(OCR_UI_STATE.SUCCESS);
      }
    } catch (err) {
      if (err?.name === 'AbortError' || abortController.signal.aborted) {
        // Quiet cancellation, reset to idle
        if (isMountedRef.current) {
          resetToIdle();
        }
        return;
      }

      if (!isMountedRef.current) return;

      let userMsg = 'OCR processing failed. Please enter details manually.';
      if (err?.code === 'ETIMEDOUT') {
        userMsg = err.message || 'OCR processing timed out. Please try again or enter details manually.';
      } else if (err?.code === 'JOB_FAILED') {
        userMsg = err.message || 'OCR extraction could not be completed for this image.';
      } else {
        userMsg = getNetworkErrorMessage(err);
      }

      setErrorMessage(userMsg);
      setOcrState(OCR_UI_STATE.FAILED);
    } finally {
      inFlightRef.current = false;
    }
  };

  const takePhoto = async () => {
    if (inFlightRef.current || ocrState === OCR_UI_STATE.SUBMITTING || ocrState === OCR_UI_STATE.POLLING) {
      return;
    }

    try {
      const perm = await ImagePicker.requestCameraPermissionsAsync();
      if (!perm?.granted) {
        Alert.alert('Permission Required', 'Camera permission is required to capture packaging.');
        return;
      }

      const res = await ImagePicker.launchCameraAsync({
        base64: true,
        quality: 0.8,
        allowsEditing: false,
      });

      if (res?.canceled) return;
      const asset = res?.assets?.[0];
      await processAsset(asset);
    } catch (_e) {
      Alert.alert('Camera Error', 'Unable to capture photo. Please try again or choose from gallery.');
    }
  };

  const pickImage = async () => {
    if (inFlightRef.current || ocrState === OCR_UI_STATE.SUBMITTING || ocrState === OCR_UI_STATE.POLLING) {
      return;
    }

    try {
      const res = await ImagePicker.launchImageLibraryAsync({
        base64: true,
        quality: 0.8,
        allowsEditing: false,
      });

      if (res?.canceled) return;
      const asset = res?.assets?.[0];
      await processAsset(asset);
    } catch (_e) {
      Alert.alert('Gallery Error', 'Unable to pick photo. Please enter details manually.');
    }
  };

  const handleRetryLast = async () => {
    if (lastAssetRef.current) {
      await processAsset(lastAssetRef.current);
    } else {
      resetToIdle();
    }
  };

  const renderCardContent = () => {
    switch (ocrState) {
      case OCR_UI_STATE.SUBMITTING:
        return (
          <View style={styles.stateContainer}>
            <NeoBadge label="UPLOADING" variant="lime" showDot style={{ marginBottom: 16 }} />
            <View style={styles.heroIconBox}>
              <ActivityIndicator size="large" color={PREMIUM_COLORS.primaryDark} />
            </View>
            <Text style={styles.cardHeroTitle}>Uploading Nutrition Label</Text>
            <Text style={styles.cardHeroSub}>
              Compressing and transferring image to asynchronous OCR queue...
            </Text>
            <View style={styles.actionBtnStack}>
              <NeoButton
                title="Cancel Upload"
                onPress={cancelOcrJob}
                variant="outline"
                size="sm"
              />
            </View>
          </View>
        );

      case OCR_UI_STATE.POLLING:
        return (
          <View style={styles.stateContainer}>
            <NeoBadge
              label={jobProgress.status === OCR_STATUS.PROCESSING ? 'PROCESSING OCR' : 'QUEUED IN WORKER'}
              variant={jobProgress.status === OCR_STATUS.PROCESSING ? 'safe' : 'moderate'}
              showDot
              style={{ marginBottom: 12 }}
            />
            <View style={styles.heroIconBox}>
              <ActivityIndicator size="large" color={PREMIUM_COLORS.primaryDark} />
            </View>
            <Text style={styles.cardHeroTitle}>
              {jobProgress.status === OCR_STATUS.PROCESSING
                ? 'Extracting Nutrition Facts'
                : 'Waiting for OCR Engine'}
            </Text>
            <Text style={styles.cardHeroSub}>
              {jobProgress.status === OCR_STATUS.PROCESSING
                ? 'Dual OCR pipeline (EasyOCR / Tesseract) is parsing nutritional table...'
                : 'Job is queued. Worker starting extraction shortly...'}
            </Text>

            {/* Polling telemetry card */}
            <View style={styles.telemetryCard}>
              <View style={styles.telemetryRow}>
                <Text style={styles.telemetryLabel}>Job ID:</Text>
                <Text style={styles.telemetryValue} numberOfLines={1}>
                  {jobId ? (jobId.length > 20 ? `${jobId.slice(0, 18)}...` : jobId) : '—'}
                </Text>
              </View>
              <View style={styles.telemetryRow}>
                <Text style={styles.telemetryLabel}>Elapsed:</Text>
                <Text style={styles.telemetryValue}>{jobProgress.elapsedSec || 0}s / 45s</Text>
              </View>
              <NeoProgressBar
                progress={Math.min(100, Math.max(5, ((jobProgress.elapsedSec || 1) / 45) * 100))}
                color={PREMIUM_COLORS.primaryDark}
                height={6}
                style={{ marginTop: 8 }}
              />
            </View>

            <View style={styles.actionBtnStack}>
              <NeoButton
                title="Cancel & Enter Manually"
                onPress={() => {
                  cancelOcrJob();
                  goManual(null);
                }}
                variant="outline"
                size="sm"
              />
            </View>
          </View>
        );

      case OCR_UI_STATE.SUCCESS: {
        const parsed = extractOcrNutrientValues(ocrResult);
        return (
          <View style={styles.stateContainer}>
            <NeoBadge label="OCR SUCCESS" variant="safe" showDot style={{ marginBottom: 12 }} />
            <View style={[styles.heroIconBox, { backgroundColor: PREMIUM_COLORS.status.safeBg }]}>
              <Ionicons name="checkmark-circle" size={36} color={PREMIUM_COLORS.status.safe} />
            </View>
            <Text style={styles.cardHeroTitle}>Nutrition Facts Detected!</Text>
            <Text style={styles.cardHeroSub}>
              {parsed.product_name
                ? `Extracted values for "${parsed.product_name}"`
                : 'Nutritional table values were successfully recognized.'}
            </Text>

            {/* Nutrition metrics grid */}
            <View style={styles.metricsGrid}>
              <View style={styles.metricCell}>
                <Text style={styles.metricVal}>
                  {parsed.calories != null ? formatNutritionValue(parsed.calories, '') : '—'}
                </Text>
                <Text style={styles.metricUnit}>kcal</Text>
                <Text style={styles.metricLabel}>Calories</Text>
              </View>
              <View style={styles.metricCell}>
                <Text style={styles.metricVal}>
                  {parsed.protein != null ? formatNutritionValue(parsed.protein, '') : '—'}
                </Text>
                <Text style={styles.metricUnit}>g</Text>
                <Text style={styles.metricLabel}>Protein</Text>
              </View>
              <View style={styles.metricCell}>
                <Text style={styles.metricVal}>
                  {parsed.carbs != null ? formatNutritionValue(parsed.carbs, '') : '—'}
                </Text>
                <Text style={styles.metricUnit}>g</Text>
                <Text style={styles.metricLabel}>Carbs</Text>
              </View>
              <View style={styles.metricCell}>
                <Text style={styles.metricVal}>
                  {parsed.fat != null ? formatNutritionValue(parsed.fat, '') : '—'}
                </Text>
                <Text style={styles.metricUnit}>g</Text>
                <Text style={styles.metricLabel}>Fat</Text>
              </View>
              <View style={styles.metricCell}>
                <Text style={styles.metricVal}>
                  {parsed.sugar != null ? formatNutritionValue(parsed.sugar, '') : '—'}
                </Text>
                <Text style={styles.metricUnit}>g</Text>
                <Text style={styles.metricLabel}>Sugar</Text>
              </View>
              <View style={styles.metricCell}>
                <Text style={styles.metricVal}>
                  {parsed.salt != null ? formatNutritionValue(parsed.salt, '') : '—'}
                </Text>
                <Text style={styles.metricUnit}>g</Text>
                <Text style={styles.metricLabel}>Salt</Text>
              </View>
            </View>

            <View style={styles.btnRow}>
              <NeoButton
                title="Review & Continue →"
                onPress={() => goManual(ocrResult)}
                variant="black"
                size="lg"
              />
              <NeoButton
                title="Scan Another Label"
                onPress={resetToIdle}
                variant="outline"
                size="md"
              />
            </View>
          </View>
        );
      }

      case OCR_UI_STATE.EMPTY:
        return (
          <View style={styles.stateContainer}>
            <NeoBadge label="NO NUTRITION FOUND" variant="moderate" showDot style={{ marginBottom: 12 }} />
            <View style={[styles.heroIconBox, { backgroundColor: PREMIUM_COLORS.status.moderateBg }]}>
              <Ionicons name="alert-circle-outline" size={36} color={PREMIUM_COLORS.status.moderate} />
            </View>
            <Text style={styles.cardHeroTitle}>No Nutrition Values Detected</Text>
            <Text style={styles.cardHeroSub}>
              We could not identify clear nutrition facts in this photo. Packaging may be reflective, curved, or lacking a tabular nutrition panel.
            </Text>

            <View style={styles.btnRow}>
              <NeoButton
                title="Enter Nutrition Manually →"
                onPress={() => goManual(ocrResult)}
                variant="black"
                size="lg"
              />
              <NeoButton
                title="Try Another Photo"
                onPress={resetToIdle}
                variant="outline"
                size="md"
              />
            </View>
          </View>
        );

      case OCR_UI_STATE.FAILED:
        return (
          <View style={styles.stateContainer}>
            <NeoBadge label="OCR FAILED" variant="avoid" showDot style={{ marginBottom: 12 }} />
            <View style={[styles.heroIconBox, { backgroundColor: PREMIUM_COLORS.status.avoidBg }]}>
              <Ionicons name="close-circle-outline" size={36} color={PREMIUM_COLORS.status.avoid} />
            </View>
            <Text style={styles.cardHeroTitle}>Could Not Read Label</Text>
            <Text style={styles.cardHeroSub}>
              {errorMessage || 'An error occurred during OCR extraction. Please check your connection or enter manually.'}
            </Text>

            <View style={styles.btnRow}>
              <NeoButton
                title="Try Again"
                onPress={handleRetryLast}
                variant="black"
                size="md"
              />
              <NeoButton
                title="Enter Details Manually →"
                onPress={() => goManual(null)}
                variant="outline"
                size="md"
              />
              <TouchableOpacity
                style={styles.manualFallbackBtn}
                onPress={resetToIdle}
                activeOpacity={0.8}
              >
                <Text style={styles.manualFallbackText}>Dismiss & Back to Scanner</Text>
              </TouchableOpacity>
            </View>
          </View>
        );

      case OCR_UI_STATE.IDLE:
      default:
        return (
          <>
            <View style={styles.heroIconBox}>
              <Ionicons name="camera-outline" size={30} color={PREMIUM_COLORS.primaryDark} />
            </View>
            <Text style={styles.cardHeroTitle}>Extract Nutrition Facts</Text>
            <Text style={styles.cardHeroSub}>
              Our OCR engine automatically reads calories, sugars, fats, and protein from packaging labels in the background.
            </Text>

            <View style={styles.btnRow}>
              <NeoButton
                title="Take Label Photo"
                onPress={takePhoto}
                variant="black"
                size="lg"
              />

              <NeoButton
                title="Choose from Gallery"
                onPress={pickImage}
                variant="outline"
                size="md"
              />

              <TouchableOpacity
                style={styles.manualFallbackBtn}
                onPress={() => goManual(null)}
                activeOpacity={0.8}
              >
                <Text style={styles.manualFallbackText}>Skip OCR & Enter Manually →</Text>
              </TouchableOpacity>
            </View>
          </>
        );
    }
  };

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
      >
        {/* Navigation Top */}
        <View style={styles.navBar}>
          <TouchableOpacity
            style={[styles.backBtn, PREMIUM_SHADOWS.sm]}
            onPress={() => {
              if (inFlightRef.current || ocrState === OCR_UI_STATE.POLLING || ocrState === OCR_UI_STATE.SUBMITTING) {
                cancelOcrJob();
              }
              if (navigation?.canGoBack && navigation.canGoBack()) {
                navigation.goBack();
              } else {
                navigation.navigate('Main', { screen: 'Home' });
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
            <Text style={styles.badgeText}>VISION OCR</Text>
          </View>
          <Text style={styles.screenTitle}>OCR Label Scanner</Text>
          <Text style={styles.screenSub}>
            Take a clear photo of the nutrition facts table on food packaging.
          </Text>
        </View>

        {/* Dynamic Card based on OCR lifecycle state */}
        <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
          {renderCardContent()}
        </View>

        {/* Capture Tips Card (visible in IDLE and EMPTY states) */}
        {(ocrState === OCR_UI_STATE.IDLE || ocrState === OCR_UI_STATE.EMPTY) && (
          <View style={[styles.tipsCard, PREMIUM_SHADOWS.sm]}>
            <Text style={styles.tipsTitle}>Tips for Best OCR Accuracy</Text>
            <View style={styles.tipRow}>
              <Text style={styles.tipBullet}>•</Text>
              <Text style={styles.tipText}>Keep packaging flat and avoid glare from overhead lights.</Text>
            </View>
            <View style={styles.tipRow}>
              <Text style={styles.tipBullet}>•</Text>
              <Text style={styles.tipText}>{'Ensure the "Nutrition Information" heading is visible.'}</Text>
            </View>
            <View style={styles.tipRow}>
              <Text style={styles.tipBullet}>•</Text>
              <Text style={styles.tipText}>Frame closely so numbers and nutrient units (g, mg) are sharp.</Text>
            </View>
          </View>
        )}
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
  card: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 24,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    marginBottom: 16,
  },
  stateContainer: {
    width: '100%',
    alignItems: 'center',
  },
  heroIconBox: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: PREMIUM_COLORS.primaryLight,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 12,
  },
  cardHeroTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.3,
    textAlign: 'center',
  },
  cardHeroSub: {
    fontSize: 14,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    textAlign: 'center',
    marginTop: 6,
    marginBottom: 16,
    lineHeight: 20,
    maxWidth: 300,
  },
  btnRow: {
    width: '100%',
    gap: 12,
  },
  actionBtnStack: {
    marginTop: 8,
    alignItems: 'center',
  },
  manualFallbackBtn: {
    alignSelf: 'center',
    paddingVertical: 8,
  },
  manualFallbackText: {
    fontSize: 14,
    fontWeight: '600',
    color: PREMIUM_COLORS.primaryDark,
  },
  telemetryCard: {
    width: '100%',
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.md,
    padding: 14,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  telemetryRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  telemetryLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },
  telemetryValue: {
    fontSize: 12,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    maxWidth: '70%',
  },
  metricsGrid: {
    width: '100%',
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    justifyContent: 'space-between',
    marginBottom: 20,
  },
  metricCell: {
    width: '31%',
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.md,
    paddingVertical: 10,
    paddingHorizontal: 8,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  metricVal: {
    fontSize: 16,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  metricUnit: {
    fontSize: 10,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
    marginTop: -2,
    marginBottom: 2,
  },
  metricLabel: {
    fontSize: 11,
    fontWeight: '500',
    color: PREMIUM_COLORS.mutedLight,
  },
  tipsCard: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 16,
  },
  tipsTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginBottom: 8,
  },
  tipRow: {
    flexDirection: 'row',
    gap: 6,
    marginBottom: 4,
  },
  tipBullet: {
    fontSize: 13,
    color: PREMIUM_COLORS.primaryDark,
    fontWeight: '700',
  },
  tipText: {
    flex: 1,
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    lineHeight: 18,
  },
});
