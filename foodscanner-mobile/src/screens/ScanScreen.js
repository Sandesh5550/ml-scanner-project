import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  Alert,
  ActivityIndicator,
  ScrollView,
  Animated,
  Dimensions,
  Platform,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';

import { scanProduct, analyzeManualProduct } from '../services/api';
import WebBarcodeScanner from '../components/WebBarcodeScanner';
import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';

export const BENCHMARK_PRODUCTS = [
  {
    id: 'parle-g',
    name: 'Parle-G',
    barcode: '8901719101038',
    brand: 'Parle',
    category: 'Biscuits & Cookies',
    icon: 'nutrition-outline',
  },
  {
    id: 'maggi',
    name: 'Maggi',
    barcode: '8901058851304',
    brand: 'Nestlé',
    category: 'Instant Noodles',
    icon: 'restaurant-outline',
  },
  {
    id: 'kurkure',
    name: 'Kurkure',
    barcode: '8901491100519',
    brand: 'PepsiCo',
    category: 'Chips & Snacks',
    icon: 'flame-outline',
  },
  {
    id: 'lays',
    name: "Lay's",
    barcode: '8901491101844',
    brand: 'PepsiCo',
    category: 'Potato Chips',
    icon: 'fast-food-outline',
  },
  {
    id: 'amul-butter',
    name: 'Amul Butter',
    barcode: '8901262010320',
    brand: 'Amul',
    category: 'Dairy',
    icon: 'cube-outline',
  },
];

const { width: SCREEN_WIDTH } = Dimensions.get('window');
const SCAN_BOX_SIZE = Math.min(260, SCREEN_WIDTH * 0.72);

export default function ScanScreen({ navigation }) {
  const [permission, requestPermission] = useCameraPermissions();
  const [scanned, setScanned] = useState(false);
  const [barcode, setBarcode] = useState('');
  const [productName, setProductName] = useState('');
  const [loading, setLoading] = useState(false);
  const [showCamera, setShowCamera] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [isBarcodeFocused, setIsBarcodeFocused] = useState(false);
  const [isNameFocused, setIsNameFocused] = useState(false);
  const productNameRef = useRef(null);

  // Animated vertical scan line
  const laserAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(laserAnim, {
          toValue: SCAN_BOX_SIZE - 20,
          duration: 1800,
          useNativeDriver: true,
        }),
        Animated.timing(laserAnim, {
          toValue: 0,
          duration: 1800,
          useNativeDriver: true,
        }),
      ])
    ).start();
  }, [laserAnim]);

  useEffect(() => {
    if (!permission) return;
    if (permission.granted) return;
    if (!showCamera) return;
    if (!permission.canAskAgain) return;
    requestPermission();
  }, [permission, showCamera, requestPermission]);

  useFocusEffect(
    React.useCallback(() => {
      setScanned(false);
      setLoading(false);
      setNotFound(false);
    }, [])
  );

  const runScan = async ({ targetBarcode, targetName } = {}) => {
    const raw = targetBarcode !== undefined ? String(targetBarcode || '').trim() : String(barcode || '').trim();
    const hint = targetName !== undefined ? String(targetName || '').trim() : String(productName || '').trim();

    const hasLetters = /[A-Za-z]/.test(raw);
    if (hasLetters) {
      Alert.alert(
        'Barcode only',
        'Please enter digits only for the barcode. Use the product name field below for text search.'
      );
      return;
    }

    if (!raw && !hint) {
      Alert.alert('Input Required', 'Please enter a numeric barcode or product name to analyze.');
      return;
    }

    const isDigits = /^\d{8,14}$/.test(raw);
    if (raw && !isDigits) {
      Alert.alert('Invalid barcode', 'Enter numeric barcode digits (8-14 digits) or select a popular food below.');
      return;
    }

    setLoading(true);
    setNotFound(false);
    try {
      let result;
      if (raw) {
        result = await scanProduct(raw, hint || null);
      } else {
        result = await analyzeManualProduct({ product_name: hint });
      }

      if (!result) {
        throw new Error('No product data returned');
      }

      // Navigate to Result screen with fresh timestamp (preserving navigation stack for Back button)
      navigation.navigate('Result', { result, timestamp: Date.now() });
    } catch (e) {
      const status = e?.response?.status;
      if (status === 404) {
        setNotFound(true);
        setShowCamera(false);
        setTimeout(() => {
          productNameRef?.current?.focus?.();
        }, 60);
      } else {
        const msg = e?.response?.data?.detail || e?.message || 'Scan failed';
        Alert.alert('Scan Result', String(msg));
      }
    } finally {
      setLoading(false);
    }
  };

  const onBarcodeScanned = ({ data }) => {
    if (scanned || loading) return;
    setScanned(true);
    const scannedCode = String(data || '').trim();
    setBarcode(scannedCode);
    setShowCamera(false);
    runScan({ targetBarcode: scannedCode, targetName: null });
    setTimeout(() => setScanned(false), 1500);
  };

  if (!permission && Platform.OS !== 'web') {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={PREMIUM_COLORS.primaryDark} size="small" />
        <Text style={styles.permissionPrompt}>Checking camera permissions...</Text>
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* Navigation Bar with Back Button */}
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
            <Text style={styles.badgeText}>SCANNER</Text>
          </View>
          <Text style={styles.screenTitle}>Check Product</Text>
          <Text style={styles.screenSub}>
            Scan food packaging barcode or lookup by product name
          </Text>
        </View>

        {/* Live Camera View or Trigger Card */}
        {showCamera && Platform.OS === 'web' ? (
          <View style={[styles.cameraContainer, PREMIUM_SHADOWS.md]}>
            <WebBarcodeScanner
              boxSize={SCAN_BOX_SIZE}
              onBarcodeScanned={scanned ? undefined : onBarcodeScanned}
              onClose={() => {
                setShowCamera(false);
                setScanned(false);
              }}
            />
          </View>
        ) : showCamera && permission?.granted ? (
          <View style={[styles.cameraContainer, PREMIUM_SHADOWS.md]}>
            <CameraView
              style={StyleSheet.absoluteFill}
              barcodeScannerSettings={{
                barcodeTypes: ['qr', 'ean13', 'ean8', 'upc_a', 'upc_e', 'code128', 'code39'],
              }}
              onBarcodeScanned={scanned ? undefined : onBarcodeScanned}
            />
            {/* Viewfinder Reticle Overlay */}
            <View style={styles.reticleOverlay}>
              <View style={[styles.reticleBox, { width: SCAN_BOX_SIZE, height: SCAN_BOX_SIZE }]}>
                {/* Glowing Corner Accents */}
                <View style={[styles.corner, styles.cornerTL]} />
                <View style={[styles.corner, styles.cornerTR]} />
                <View style={[styles.corner, styles.cornerBL]} />
                <View style={[styles.corner, styles.cornerBR]} />

                {/* Animated Laser Sweep */}
                <Animated.View
                  style={[
                    styles.laserLine,
                    { transform: [{ translateY: laserAnim }] },
                  ]}
                />
              </View>

              <Text style={styles.cameraHint}>Align barcode within the green frame</Text>

              <TouchableOpacity
                style={[styles.closeCameraBtn, PREMIUM_SHADOWS.sm]}
                activeOpacity={0.85}
                onPress={() => {
                  setShowCamera(false);
                  setScanned(false);
                }}
              >
                <Text style={styles.closeCameraText}>✕ Close Camera</Text>
              </TouchableOpacity>
            </View>
          </View>
        ) : (
          <TouchableOpacity
            style={[styles.cameraHeroCard, PREMIUM_SHADOWS.sm]}
            activeOpacity={0.9}
            onPress={async () => {
              if (Platform.OS === 'web') {
                setScanned(false);
                setShowCamera(true);
                return;
              }
              if (permission?.granted) {
                setScanned(false);
                setShowCamera(true);
                return;
              }
              if (permission?.canAskAgain) {
                const p = await requestPermission();
                if (p?.granted) {
                  setScanned(false);
                  setShowCamera(true);
                }
                return;
              }
              Alert.alert('Camera Permission Required', 'Please enable camera access in system settings to scan barcodes.');
            }}
          >
            <View style={styles.cameraHeroIconWrap}>
              <Ionicons name="barcode-outline" size={30} color={PREMIUM_COLORS.primaryDark} />
            </View>
            <Text style={styles.cameraHeroTitle}>
              {Platform.OS === 'web' ? 'Open Live Webcam Scanner' : 'Open Live Barcode Scanner'}
            </Text>
            <Text style={styles.cameraHeroSub}>
              {Platform.OS === 'web'
                ? 'Scan food packaging barcode using your laptop webcam'
                : 'Point at any packaged food label for instant scientific breakdown'}
            </Text>
          </TouchableOpacity>
        )}

        {/* Input Details Bottom Sheet Card */}
        <View style={[styles.inputCard, PREMIUM_SHADOWS.sm]}>
          <Text style={styles.inputSectionTitle}>Manual Barcode Digits</Text>
          <View style={[styles.inputWrapper, isBarcodeFocused && styles.inputWrapperFocused]}>
            <TextInput
              style={styles.textInput}
              placeholder="e.g. 8901058000256"
              placeholderTextColor="#7C837B"
              value={barcode}
              onChangeText={setBarcode}
              keyboardType="numeric"
              selectionColor="#557A3E"
              underlineColorAndroid="transparent"
              onFocus={() => setIsBarcodeFocused(true)}
              onBlur={() => setIsBarcodeFocused(false)}
            />
            {barcode ? (
              <TouchableOpacity
                style={styles.searchInsideBtn}
                onPress={() => runScan({ targetBarcode: barcode })}
                disabled={loading}
              >
                {loading ? (
                  <ActivityIndicator size="small" color={PREMIUM_COLORS.primaryDark} />
                ) : (
                  <Text style={styles.searchInsideBtnText}>Check →</Text>
                )}
              </TouchableOpacity>
            ) : null}
          </View>

          {notFound ? (
            <View style={styles.notFoundCard}>
              <View style={styles.notFoundHeader}>
                <Ionicons name="alert-circle-outline" size={16} color={PREMIUM_COLORS.status.avoid} />
                <Text style={styles.notFoundTitle}>Barcode not found</Text>
              </View>
              <Text style={styles.notFoundSub}>
                Try typing the product brand/name below or enter nutrition facts manually.
              </Text>
              <TouchableOpacity
                style={styles.manualEntryBtn}
                onPress={() => navigation.navigate('ManualEntry', { productName: productName.trim() || '' })}
              >
                <Text style={styles.manualEntryBtnText}>Enter Nutrition Manually →</Text>
              </TouchableOpacity>
            </View>
          ) : null}

          <Text style={[styles.inputSectionTitle, { marginTop: 18 }]}>Or Search By Product Name</Text>
          <View style={[styles.inputWrapper, isNameFocused && styles.inputWrapperFocused]}>
            <TextInput
              ref={productNameRef}
              style={styles.textInput}
              placeholder="e.g. Dairy Milk, Maggi Noodles, Oat Milk"
              placeholderTextColor="#7C837B"
              value={productName}
              onChangeText={(t) => {
                setProductName(t);
                if (notFound) setNotFound(false);
              }}
              selectionColor="#557A3E"
              underlineColorAndroid="transparent"
              onFocus={() => setIsNameFocused(true)}
              onBlur={() => setIsNameFocused(false)}
            />
            {productName ? (
              <TouchableOpacity
                style={styles.searchInsideBtn}
                onPress={() => runScan({ targetBarcode: null, targetName: productName })}
                disabled={loading}
              >
                {loading ? (
                  <ActivityIndicator size="small" color={PREMIUM_COLORS.primaryDark} />
                ) : (
                  <Text style={styles.searchInsideBtnText}>Search →</Text>
                )}
              </TouchableOpacity>
            ) : null}
          </View>

          {/* Quick Benchmark Chips */}
          <View style={styles.benchmarkHeaderRow}>
            <Text style={styles.inputSectionTitle}>Popular Packaged Foods</Text>
            <Text style={styles.benchmarkSub}>Tap to test & analyze</Text>
          </View>
          <View style={styles.chipsRow}>
            {BENCHMARK_PRODUCTS.map((item) => {
              const isSelected = barcode === item.barcode || productName.toLowerCase() === item.name.toLowerCase();
              return (
                <TouchableOpacity
                  key={item.id}
                  activeOpacity={0.8}
                  style={[styles.chip, isSelected && styles.chipSelected]}
                  onPress={() => {
                    setBarcode(item.barcode);
                    setProductName(item.name);
                    runScan({ targetBarcode: item.barcode, targetName: item.name });
                  }}
                  disabled={loading}
                >
                  <Ionicons
                    name={item.icon || 'fast-food-outline'}
                    size={14}
                    color={isSelected ? PREMIUM_COLORS.primaryDark : PREMIUM_COLORS.secondary}
                    style={{ marginRight: 6 }}
                  />
                  <Text style={[styles.chipText, isSelected && styles.chipTextSelected]}>
                    {item.name}
                  </Text>
                </TouchableOpacity>
              );
            })}
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
    paddingTop: 52,
    paddingBottom: 110,
  },
  center: {
    flex: 1,
    backgroundColor: PREMIUM_COLORS.bg,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
  },
  permissionPrompt: {
    fontSize: 14,
    color: PREMIUM_COLORS.secondary,
    fontWeight: '500',
  },

  // Navigation Bar
  navBar: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 14,
  },
  backBtn: {
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

  // Live Camera
  cameraContainer: {
    height: 380,
    borderRadius: PREMIUM_RADIUS.xl,
    overflow: 'hidden',
    marginBottom: 18,
    position: 'relative',
    backgroundColor: '#000000',
  },
  reticleOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0, 0, 0, 0.45)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  reticleBox: {
    position: 'relative',
    borderRadius: 18,
    borderColor: 'rgba(255, 255, 255, 0.2)',
    borderWidth: 1,
  },
  corner: {
    position: 'absolute',
    width: 28,
    height: 28,
    borderColor: PREMIUM_COLORS.primary,
  },
  cornerTL: {
    top: -2,
    left: -2,
    borderTopWidth: 4,
    borderLeftWidth: 4,
    borderTopLeftRadius: 16,
  },
  cornerTR: {
    top: -2,
    right: -2,
    borderTopWidth: 4,
    borderRightWidth: 4,
    borderTopRightRadius: 16,
  },
  cornerBL: {
    bottom: -2,
    left: -2,
    borderBottomWidth: 4,
    borderLeftWidth: 4,
    borderBottomLeftRadius: 16,
  },
  cornerBR: {
    bottom: -2,
    right: -2,
    borderBottomWidth: 4,
    borderRightWidth: 4,
    borderBottomRightRadius: 16,
  },
  laserLine: {
    position: 'absolute',
    top: 10,
    left: 10,
    right: 10,
    height: 3,
    backgroundColor: PREMIUM_COLORS.primary,
    borderRadius: 2,
    shadowColor: PREMIUM_COLORS.primary,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.9,
    shadowRadius: 8,
  },
  cameraHint: {
    fontSize: 13,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 18,
    letterSpacing: 0.3,
  },
  closeCameraBtn: {
    backgroundColor: 'rgba(255, 255, 255, 0.9)',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: PREMIUM_RADIUS.pill,
    marginTop: 14,
  },
  closeCameraText: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },

  // Camera Hero Card
  cameraHeroCard: {
    backgroundColor: PREMIUM_COLORS.primaryLight,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 24,
    alignItems: 'center',
    marginBottom: 18,
    borderWidth: 1,
    borderColor: 'rgba(85, 122, 62, 0.12)',
  },
  cameraHeroIconWrap: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: '#FFFFFF',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 10,
    ...PREMIUM_SHADOWS.sm,
  },
  cameraHeroTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  cameraHeroSub: {
    fontSize: 13,
    fontWeight: '500',
    color: PREMIUM_COLORS.primaryDark,
    textAlign: 'center',
    marginTop: 4,
    maxWidth: 270,
    lineHeight: 18,
  },

  // Input Card
  inputCard: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 20,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  inputSectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginBottom: 8,
  },
  inputWrapper: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    paddingHorizontal: 16,
    borderWidth: 1.5,
    borderColor: '#E1E6DC',
    minHeight: 52,
  },
  inputWrapperFocused: {
    borderColor: '#557A3E',
    backgroundColor: '#F7FAF1',
    shadowColor: '#557A3E',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.12,
    shadowRadius: 6,
    elevation: 2,
  },
  textInput: {
    flex: 1,
    paddingVertical: 12,
    fontSize: 16,
    fontWeight: '500',
    color: '#171A17',
    backgroundColor: 'transparent',
    borderWidth: 0,
    outlineStyle: 'none',
    outlineWidth: 0,
  },
  searchInsideBtn: {
    backgroundColor: PREMIUM_COLORS.primary,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: PREMIUM_RADIUS.pill,
  },
  searchInsideBtnText: {
    fontSize: 12,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },

  // Not Found
  notFoundCard: {
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    borderRadius: PREMIUM_RADIUS.md,
    padding: 14,
    marginTop: 10,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.avoidBorder,
  },
  notFoundHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 4,
  },
  notFoundTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.status.avoid,
  },
  notFoundSub: {
    fontSize: 11,
    fontWeight: '500',
    color: PREMIUM_COLORS.secondary,
    marginTop: 2,
    marginBottom: 8,
  },
  manualEntryBtn: {
    alignSelf: 'flex-start',
    backgroundColor: '#FFFFFF',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: PREMIUM_RADIUS.pill,
  },
  manualEntryBtnText: {
    fontSize: 11,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },

  // Chips & Benchmarks
  benchmarkHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 20,
    marginBottom: 10,
  },
  benchmarkSub: {
    fontSize: 11,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },
  chipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.bgAlt,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: PREMIUM_RADIUS.pill,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  chipSelected: {
    backgroundColor: PREMIUM_COLORS.primaryLight,
    borderColor: PREMIUM_COLORS.primary,
  },
  chipText: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.ink,
  },
  chipTextSelected: {
    color: PREMIUM_COLORS.primaryDark,
    fontWeight: '700',
  },
});
