import React, { useMemo, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, ActivityIndicator, Alert, TouchableOpacity } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { Ionicons } from '@expo/vector-icons';

import { scanNutritionLabel } from '../services/api';
import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';
import { NeoButton } from '../components/neo';
import { formatNutritionValue } from '../utils/formatters';

export default function OCRScanScreen({ navigation, route }) {
  const prefillName = route?.params?.productName;
  const [loading, setLoading] = useState(false);

  const goManual = (prefill) => {
    navigation.navigate('ManualEntry', {
      productName: prefill?.product_name || prefillName || '',
      calories: prefill?.calories != null ? formatNutritionValue(prefill.calories, '') : null,
      nutrition: prefill ? {
        calories: prefill.calories != null ? formatNutritionValue(prefill.calories, '') : null,
        fat: prefill.fat != null ? formatNutritionValue(prefill.fat, '') : null,
        sugar: prefill.sugar != null ? formatNutritionValue(prefill.sugar, '') : null,
        salt: prefill.salt != null ? formatNutritionValue(prefill.salt, '') : null,
        protein: prefill.protein != null ? formatNutritionValue(prefill.protein, '') : null,
        fiber: prefill.fiber != null ? formatNutritionValue(prefill.fiber, '') : null,
        carbs: prefill.carbs != null ? formatNutritionValue(prefill.carbs, '') : null,
      } : null,
      ocr: prefill || null,
    });
  };

  const processAsset = async (asset) => {
    if (!asset?.base64) {
      Alert.alert('Image Error', 'Could not read image data. Please try again.');
      goManual(null);
      return;
    }

    setLoading(true);
    try {
      const res = await scanNutritionLabel(asset.base64);

      const hasValues = res && (
        res.calories != null ||
        res.fat != null ||
        res.sugar != null ||
        res.protein != null ||
        res.salt != null ||
        res.fiber != null ||
        res.carbs != null
      );

      if (!hasValues) {
        Alert.alert(
          'Low Confidence Read',
          'Could not detect all nutrition values automatically. Please review and fill in the missing fields.'
        );
      }
      goManual(res);
    } catch (e) {
      let msg = 'Could not read label. Please enter manually.';
      if (e?.code === 'ECONNABORTED' || e?.message?.includes('timeout')) {
        msg = 'OCR request timed out. Please enter details manually.';
      } else if (e?.response?.data?.detail) {
        msg = String(e.response.data.detail);
      } else if (!e?.response) {
        msg = 'Backend unreachable. Please verify network connection or enter manually.';
      }
      Alert.alert('OCR Result', msg);
      goManual(null);
    } finally {
      setLoading(false);
    }
  };

  const takePhoto = async () => {
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

        {/* Capture Action Card */}
        <View style={[styles.card, PREMIUM_SHADOWS.sm]}>
          <View style={styles.heroIconBox}>
            <Ionicons name="camera-outline" size={30} color={PREMIUM_COLORS.primaryDark} />
          </View>
          <Text style={styles.cardHeroTitle}>Extract Nutrition Facts</Text>
          <Text style={styles.cardHeroSub}>
            Our OCR engine automatically reads calories, sugars, fats, and protein from packaging labels.
          </Text>

          {loading ? (
            <View style={styles.loadingBox}>
              <ActivityIndicator size="large" color={PREMIUM_COLORS.primaryDark} />
              <Text style={styles.loadingText}>Extracting text with EasyOCR / Tesseract...</Text>
            </View>
          ) : (
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
          )}
        </View>

        {/* Capture Tips Card */}
        <View style={[styles.tipsCard, PREMIUM_SHADOWS.sm]}>
          <Text style={styles.tipsTitle}>Tips for Best OCR Accuracy</Text>
          <View style={styles.tipRow}>
            <Text style={styles.tipBullet}>•</Text>
            <Text style={styles.tipText}>Keep packaging flat and avoid glare from overhead lights.</Text>
          </View>
          <View style={styles.tipRow}>
            <Text style={styles.tipBullet}>•</Text>
            <Text style={styles.tipText}>Ensure the "Nutrition Information" heading is visible.</Text>
          </View>
          <View style={styles.tipRow}>
            <Text style={styles.tipBullet}>•</Text>
            <Text style={styles.tipText}>Frame closely so numbers and nutrient units (g, mg) are sharp.</Text>
          </View>
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
  card: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 24,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    marginBottom: 16,
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
  },
  cardHeroSub: {
    fontSize: 14,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    textAlign: 'center',
    marginTop: 6,
    marginBottom: 20,
    lineHeight: 20,
    maxWidth: 290,
  },
  btnRow: {
    width: '100%',
    gap: 12,
  },
  loadingBox: {
    alignItems: 'center',
    paddingVertical: 20,
    gap: 12,
  },
  loadingText: {
    fontSize: 14,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
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
