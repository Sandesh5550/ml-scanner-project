import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Animated,
  Image,
  Alert,
  Platform,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons, Feather } from '@expo/vector-icons';

import {
  getDailyReport,
  getTodayFoods,
  getUserProfile,
  getHistory,
  scanProduct,
  analyzeManualProduct,
  deleteFoodLogItem,
  getNetworkErrorMessage,
} from '../services/api';

import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';
import { FoodImage } from '../components/premium';
import { formatNutritionValue } from '../utils/formatters';

// Curated real food items corresponding to real benchmark packaged food categories
// Sequence per specification: chips -> makhana -> wafers -> oats -> cereal -> snack
const BENCHMARK_HERO_FOODS = [
  {
    name: 'Potato Crisps',
    category: 'Crispy Snack',
    uri: 'https://images.unsplash.com/photo-1566478989037-eec170784d0b?auto=format&fit=crop&w=600&q=80',
  },
  {
    name: 'Roasted Makhana',
    category: 'Superfood',
    uri: 'https://images.unsplash.com/photo-1608039829572-78524f79c4c7?auto=format&fit=crop&w=600&q=80',
  },
  {
    name: 'Spiced Wafers',
    category: 'Crispy Namkeen',
    uri: 'https://images.unsplash.com/photo-1621447504864-d8686e12698c?auto=format&fit=crop&w=600&q=80',
  },
  {
    name: 'Rolled Oats',
    category: 'Whole Grain',
    uri: 'https://images.unsplash.com/photo-1517093709121-657754b2d35b?auto=format&fit=crop&w=600&q=80',
  },
  {
    name: 'Enriched Cereal',
    category: 'Breakfast Grain',
    uri: 'https://images.unsplash.com/photo-1525351484163-7529414344d8?auto=format&fit=crop&w=600&q=80',
  },
  {
    name: 'Wholesome Trail',
    category: 'Healthy Snack',
    uri: 'https://images.unsplash.com/photo-1599599810769-bcde5a160d32?auto=format&fit=crop&w=600&q=80',
  },
];

function _greeting(name) {
  const h = new Date().getHours();
  const prefix = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  const display = name ? `, ${name}` : '';
  return `${prefix}${display}`;
}

function _scoreMeta(score) {
  const s = Number(score) || 0;
  if (s >= 70) return { fg: PREMIUM_COLORS.status.safe, bg: PREMIUM_COLORS.status.safeBg, label: 'SAFE' };
  if (s >= 45) return { fg: PREMIUM_COLORS.status.moderate, bg: PREMIUM_COLORS.status.moderateBg, label: 'MODERATE' };
  return { fg: PREMIUM_COLORS.status.avoid, bg: PREMIUM_COLORS.status.avoidBg, label: 'AVOID' };
}

export default function HomeScreen({ navigation }) {
  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState(null);
  const [report, setReport] = useState(null);
  const [todayFoods, setTodayFoods] = useState([]);
  const [recentScans, setRecentScans] = useState([]);

  // Screen entrance micro-motion
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const slideAnim = useRef(new Animated.Value(10)).current;

  // Hero Card entrance and CTA tactile scale
  const heroCardAnim = useRef(new Animated.Value(0)).current;
  const heroCardSlide = useRef(new Animated.Value(10)).current;
  const ctaScale = useRef(new Animated.Value(1)).current;

  // Staggered quick actions entrance
  const shortcutAnim1 = useRef(new Animated.Value(0)).current;
  const shortcutAnim2 = useRef(new Animated.Value(0)).current;

  // Dynamic food rotation state
  const [activeHeroIndex, setActiveHeroIndex] = useState(0);
  const heroImageOpacity = useRef(new Animated.Value(1)).current;

  const fetchData = React.useCallback(async () => {
    try {
      const [p, r, t, h] = await Promise.all([
        getUserProfile().catch(() => null),
        getDailyReport().catch(() => null),
        getTodayFoods().catch(() => null),
        getHistory().catch(() => null),
      ]);
      if (p) setProfile(p);
      if (r) setReport(r);
      if (t) setTodayFoods(Array.isArray(t?.foods) ? t.foods : []);
      if (h) setRecentScans(Array.isArray(h) ? h : Array.isArray(h?.history) ? h.history : []);
    } catch (_e) {
      // Gracefully retain existing state on network hiccup
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useFocusEffect(
    React.useCallback(() => {
      fetchData();
    }, [fetchData])
  );

  // Entrance micro-motion triggers
  useEffect(() => {
    if (!loading) {
      // Screen entrance
      Animated.parallel([
        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 350,
          useNativeDriver: true,
        }),
        Animated.timing(slideAnim, {
          toValue: 0,
          duration: 350,
          useNativeDriver: true,
        }),
        Animated.timing(heroCardAnim, {
          toValue: 1,
          duration: 400,
          useNativeDriver: true,
        }),
        Animated.timing(heroCardSlide, {
          toValue: 0,
          duration: 400,
          useNativeDriver: true,
        }),
      ]).start();

      // Subtle quick shortcuts stagger (100ms)
      Animated.stagger(100, [
        Animated.timing(shortcutAnim1, {
          toValue: 1,
          duration: 320,
          useNativeDriver: true,
        }),
        Animated.timing(shortcutAnim2, {
          toValue: 1,
          duration: 320,
          useNativeDriver: true,
        }),
      ]).start();
    }
  }, [loading, fadeAnim, slideAnim, heroCardAnim, heroCardSlide, shortcutAnim1, shortcutAnim2]);

  // Merge real product images from scan history with curated benchmark foods
  const heroFoodList = useMemo(() => {
    const scansWithImages = recentScans
      .filter((s) => s?.image_url && typeof s.image_url === 'string' && s.image_url.startsWith('http'))
      .map((s) => ({
        name: s.product_name || s.name || 'Scanned Food',
        category: 'Recent Scan',
        uri: s.image_url,
      }));

    if (scansWithImages.length > 0) {
      return [...scansWithImages, ...BENCHMARK_HERO_FOODS];
    }
    return BENCHMARK_HERO_FOODS;
  }, [recentScans]);

  // Dynamic hero image rotation: 4 second interval, subtle 450ms crossfade
  useEffect(() => {
    if (heroFoodList.length <= 1) return;

    const interval = setInterval(() => {
      Animated.timing(heroImageOpacity, {
        toValue: 0,
        duration: 300,
        useNativeDriver: true,
      }).start(() => {
        setActiveHeroIndex((prev) => (prev + 1) % heroFoodList.length);
        Animated.timing(heroImageOpacity, {
          toValue: 1,
          duration: 450,
          useNativeDriver: true,
        }).start();
      });
    }, 4200);

    return () => clearInterval(interval);
  }, [heroFoodList.length, heroImageOpacity]);

  const currentHeroFood = heroFoodList[activeHeroIndex] || BENCHMARK_HERO_FOODS[0];

  const handleCtaPressIn = () => {
    Animated.spring(ctaScale, {
      toValue: 0.97,
      useNativeDriver: true,
    }).start();
  };

  const handleCtaPressOut = () => {
    Animated.spring(ctaScale, {
      toValue: 1,
      friction: 4,
      useNativeDriver: true,
    }).start();
  };

  const name = profile?.name || '';
  const greeting = useMemo(() => _greeting(name), [name]);
  const overallScore = report?.overall_score ?? 0;
  const scoreMeta = _scoreMeta(overallScore);

  const calories = report?.nutrition_breakdown?.calories;
  const consumedKcal = Number(calories?.consumed) || 0;
  const limitKcal = Number(calories?.limit) || 2000;
  const caloriePct = limitKcal > 0 ? Math.min(1, consumedKcal / limitKcal) : 0;

  const protein = report?.nutrition_breakdown?.protein;
  const carbs = report?.nutrition_breakdown?.carbs;
  const sugar = report?.nutrition_breakdown?.sugar;

  const foodsTodayOnly = useMemo(() => {
    const arr = Array.isArray(todayFoods) ? todayFoods : [];
    const today = new Date().toISOString().slice(0, 10);
    return arr.filter((f) => {
      const ts = String(f?.consumed_at || '').slice(0, 10);
      return ts === today;
    });
  }, [todayFoods]);

  const handleRescan = async (barcode, productName) => {
    if (!barcode && !productName) return;
    try {
      let result;
      const validBarcode = barcode && String(barcode) !== '00000000' && /^\d{8,14}$/.test(String(barcode).trim());
      if (validBarcode) {
        result = await scanProduct(String(barcode).trim(), productName || null);
      } else if (productName) {
        result = await analyzeManualProduct({ product_name: productName });
      } else {
        navigation.navigate('Main', { screen: 'Scan' });
        return;
      }
      if (result) {
        navigation.navigate('Result', { result, timestamp: Date.now() });
      } else {
        navigation.navigate('Main', { screen: 'Scan' });
      }
    } catch (_e) {
      navigation.navigate('Main', { screen: 'Scan' });
    }
  };

  const [deletingId, setDeletingId] = useState(null);

  const handleDeleteFood = (logId, name) => {
    if (!logId) return;
    const confirmMessage = `Remove "${name || 'this food'}" from today's intake?`;
    const performDelete = async () => {
      setDeletingId(logId);
      try {
        await deleteFoodLogItem(logId);
        await fetchData();
      } catch (err) {
        const msg = getNetworkErrorMessage(err) || 'Failed to remove food item';
        if (Platform.OS === 'web') {
          window.alert(msg);
        } else {
          Alert.alert('Error', msg);
        }
      } finally {
        setDeletingId(null);
      }
    };

    if (Platform.OS === 'web') {
      if (typeof window !== 'undefined' && window.confirm(confirmMessage)) {
        performDelete();
      }
    } else {
      Alert.alert(
        'Remove Intake',
        confirmMessage,
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Remove', style: 'destructive', onPress: performDelete },
        ]
      );
    }
  };


  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={PREMIUM_COLORS.primaryDark} />
        <Text style={styles.loadingText}>Opening your nutrition dashboard...</Text>
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View style={{ opacity: fadeAnim, transform: [{ translateY: slideAnim }] }}>
          {/* 1. Refined Editorial Header & Greeting */}
          <View style={styles.header}>
            <View style={{ flex: 1 }}>
              <Text style={styles.greeting}>{greeting}</Text>
              <Text style={styles.heroTitle}>Let's understand{"\n"}what you eat.</Text>
              <Text style={styles.heroSub}>Scan food. Understand better.</Text>
            </View>

            <TouchableOpacity
              activeOpacity={0.85}
              onPress={() => navigation.navigate('Main', { screen: 'Profile' })}
              style={[styles.avatarCircle, PREMIUM_SHADOWS.sm]}
            >
              {name ? (
                <Text style={styles.avatarInitials}>
                  {name.slice(0, 2).toUpperCase()}
                </Text>
              ) : (
                <Ionicons name="person-outline" size={20} color={PREMIUM_COLORS.primaryDark} />
              )}
            </TouchableOpacity>
          </View>

          {/* 2. Hero Editorial Scanner Card with Dynamic Food Imagery */}
          <Animated.View
            style={[
              styles.heroScannerCard,
              PREMIUM_SHADOWS.md,
              {
                opacity: heroCardAnim,
                transform: [{ translateY: heroCardSlide }],
              },
            ]}
          >
            <View style={styles.heroRow}>
              {/* Left Column: Text & CTA Button */}
              <View style={styles.heroLeftCol}>
                <View style={styles.heroBadge}>
                  <View style={styles.heroBadgeDot} />
                  <Text style={styles.heroBadgeText}>AI SCANNER</Text>
                </View>

                <Text style={styles.scannerCardTitle}>Understand what you eat.</Text>
                <Text style={styles.scannerCardSub}>
                  Scan a packaged food to understand its nutrition, ingredients and health signals.
                </Text>

                <Animated.View style={{ transform: [{ scale: ctaScale }], alignSelf: 'flex-start' }}>
                  <TouchableOpacity
                    activeOpacity={0.9}
                    style={[styles.scannerCtaBtn, PREMIUM_SHADOWS.sm]}
                    onPressIn={handleCtaPressIn}
                    onPressOut={handleCtaPressOut}
                    onPress={() => navigation.navigate('Main', { screen: 'Scan' })}
                  >
                    <Ionicons name="barcode-outline" size={18} color={PREMIUM_COLORS.white} />
                    <Text style={styles.scannerCtaText}>Scan Product</Text>
                  </TouchableOpacity>
                </Animated.View>
              </View>

              {/* Right Column: Editorial Food Photography Container */}
              <View style={styles.heroRightCol}>
                <View style={[styles.heroImageFrame, PREMIUM_SHADOWS.md]}>
                  <Animated.Image
                    source={{ uri: currentHeroFood.uri }}
                    style={[styles.heroImage, { opacity: heroImageOpacity }]}
                    resizeMode="cover"
                  />
                  <View style={styles.heroImageTagPill}>
                    <Text style={styles.heroImageTagText} numberOfLines={1}>
                      {currentHeroFood.name}
                    </Text>
                  </View>
                </View>
              </View>
            </View>
          </Animated.View>

          {/* 3. Quick Action Shortcuts with subtle stagger */}
          <View style={styles.quickShortcutsRow}>
            <Animated.View style={{ flex: 1, opacity: shortcutAnim1 }}>
              <TouchableOpacity
                style={[styles.shortcutCard, PREMIUM_SHADOWS.sm]}
                activeOpacity={0.88}
                onPress={() => navigation.navigate('ManualEntry')}
              >
                <View style={[styles.shortcutIconWrap, { backgroundColor: '#EBF4FE' }]}>
                  <Feather name="edit-3" size={18} color="#2563EB" />
                </View>
                <View style={styles.shortcutTextWrap}>
                  <Text style={styles.shortcutTitle}>Manual Entry</Text>
                  <Text style={styles.shortcutSub}>Type nutrition data</Text>
                </View>
              </TouchableOpacity>
            </Animated.View>

            <Animated.View style={{ flex: 1, opacity: shortcutAnim2 }}>
              <TouchableOpacity
                style={[styles.shortcutCard, PREMIUM_SHADOWS.sm]}
                activeOpacity={0.88}
                onPress={() => navigation.navigate('OCRScan')}
              >
                <View style={[styles.shortcutIconWrap, { backgroundColor: PREMIUM_COLORS.aiBg }]}>
                  <Ionicons name="camera-outline" size={20} color={PREMIUM_COLORS.ai} />
                </View>
                <View style={styles.shortcutTextWrap}>
                  <Text style={styles.shortcutTitle}>OCR Scanner</Text>
                  <Text style={styles.shortcutSub}>Capture photo of table</Text>
                </View>
              </TouchableOpacity>
            </Animated.View>
          </View>

          {/* 4. Daily Nutrition Summary */}
          <View style={styles.sectionHeaderRow}>
            <Text style={styles.sectionTitle}>Daily Nutrition</Text>
            <View style={[styles.overallScorePill, { backgroundColor: scoreMeta.bg }]}>
              <View style={[styles.scoreDot, { backgroundColor: scoreMeta.fg }]} />
              <Text style={[styles.overallScoreText, { color: scoreMeta.fg }]}>
                Score {overallScore}
              </Text>
            </View>
          </View>

          <View style={styles.nutritionGrid}>
            <View style={[styles.nutritionCard, PREMIUM_SHADOWS.sm]}>
              <View style={styles.macroTopRow}>
                <Ionicons name="flame-outline" size={15} color="#EA580C" />
                <Text style={styles.macroTag}>Calories</Text>
              </View>
              <Text style={styles.macroValue}>{formatNutritionValue(consumedKcal)}</Text>
              <Text style={styles.macroSub}>of {formatNutritionValue(limitKcal)} kcal</Text>
              <View style={styles.macroProgressTrack}>
                <View
                  style={[
                    styles.macroProgressFill,
                    {
                      width: `${caloriePct * 100}%`,
                      backgroundColor: caloriePct > 1 ? PREMIUM_COLORS.status.avoid : PREMIUM_COLORS.primaryDark,
                    },
                  ]}
                />
              </View>
            </View>

            <View style={[styles.nutritionCard, PREMIUM_SHADOWS.sm]}>
              <View style={styles.macroTopRow}>
                <Ionicons name="leaf-outline" size={15} color={PREMIUM_COLORS.primaryDark} />
                <Text style={styles.macroTag}>Protein</Text>
              </View>
              <Text style={styles.macroValue}>{formatNutritionValue(protein?.consumed)}g</Text>
              <Text style={styles.macroSub}>limit {formatNutritionValue(protein?.limit || 50)}g</Text>
              <View style={styles.macroProgressTrack}>
                <View
                  style={[
                    styles.macroProgressFill,
                    {
                      width: `${Math.min(100, ((protein?.consumed || 0) / (protein?.limit || 50)) * 100)}%`,
                      backgroundColor: PREMIUM_COLORS.primary,
                    },
                  ]}
                />
              </View>
            </View>

            <View style={[styles.nutritionCard, PREMIUM_SHADOWS.sm]}>
              <View style={styles.macroTopRow}>
                <Ionicons name="nutrition-outline" size={15} color="#D97706" />
                <Text style={styles.macroTag}>Carbs</Text>
              </View>
              <Text style={styles.macroValue}>{formatNutritionValue(carbs?.consumed)}g</Text>
              <Text style={styles.macroSub}>limit {formatNutritionValue(carbs?.limit || 250)}g</Text>
              <View style={styles.macroProgressTrack}>
                <View
                  style={[
                    styles.macroProgressFill,
                    {
                      width: `${Math.min(100, ((carbs?.consumed || 0) / (carbs?.limit || 250)) * 100)}%`,
                      backgroundColor: '#E8B342',
                    },
                  ]}
                />
              </View>
            </View>

            <View style={[styles.nutritionCard, PREMIUM_SHADOWS.sm]}>
              <View style={styles.macroTopRow}>
                <Ionicons name="water-outline" size={15} color="#0D9488" />
                <Text style={styles.macroTag}>Sugar</Text>
              </View>
              <Text style={styles.macroValue}>{formatNutritionValue(sugar?.consumed)}g</Text>
              <Text style={styles.macroSub}>limit {formatNutritionValue(sugar?.limit || 25)}g</Text>
              <View style={styles.macroProgressTrack}>
                <View
                  style={[
                    styles.macroProgressFill,
                    {
                      width: `${Math.min(100, ((sugar?.consumed || 0) / (sugar?.limit || 25)) * 100)}%`,
                      backgroundColor: (sugar?.consumed || 0) > (sugar?.limit || 25) ? PREMIUM_COLORS.status.avoid : '#9DB176',
                    },
                  ]}
                />
              </View>
            </View>
          </View>

          {/* 5. Recent Scans Carousel */}
          <View style={[styles.sectionHeaderRow, { marginTop: 26 }]}>
            <Text style={styles.sectionTitle}>Recent Scans</Text>
            <TouchableOpacity
              activeOpacity={0.7}
              onPress={() => navigation.navigate('Main', { screen: 'Scan' })}
            >
              <Text style={styles.sectionAction}>View all</Text>
            </TouchableOpacity>
          </View>

          {recentScans && recentScans.length > 0 ? (
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={styles.recentScansRow}
            >
              {recentScans.slice(0, 6).map((item, idx) => {
                const pName = item?.product_name || item?.name || 'Scanned Product';
                const pScore = item?.health_score != null ? Math.round(Number(item.health_score)) : 72;
                const pMeta = _scoreMeta(pScore);
                const barcode = item?.barcode;

                return (
                  <TouchableOpacity
                    key={item?.id || idx}
                    activeOpacity={0.9}
                    style={[styles.recentScanCard, PREMIUM_SHADOWS.sm]}
                    onPress={() => handleRescan(barcode, pName)}
                  >
                    <FoodImage
                      source={item?.image_url}
                      productName={pName}
                      size={144}
                      height={96}
                      borderRadius={PREMIUM_RADIUS.md}
                    />
                    <Text style={styles.recentScanName} numberOfLines={1}>
                      {pName}
                    </Text>
                    <View style={styles.recentScanFooter}>
                      <View style={[styles.recentScorePill, { backgroundColor: pMeta.bg }]}>
                        <View style={[styles.scoreDot, { backgroundColor: pMeta.fg }]} />
                        <Text style={[styles.recentScoreText, { color: pMeta.fg }]}>
                          {pScore}
                        </Text>
                      </View>
                      <Text style={styles.recentActionHint}>Tap to view →</Text>
                    </View>
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          ) : (
            <View style={[styles.emptyRecentCard, PREMIUM_SHADOWS.sm]}>
              <View style={styles.emptyIconCircle}>
                <Ionicons name="barcode-outline" size={26} color={PREMIUM_COLORS.secondary} />
              </View>
              <Text style={styles.emptyRecentTitle}>No products scanned yet</Text>
              <Text style={styles.emptyRecentSub}>
                Scan your first packaged food to see ingredient safety & NutriScore analysis here.
              </Text>
            </View>
          )}

          {/* 6. AI Nutrition Assistant Section */}
          <View style={[styles.aiCard, PREMIUM_SHADOWS.sm]}>
            <View style={styles.aiHeaderRow}>
              <View style={styles.aiBadge}>
                <Ionicons name="sparkles" size={13} color={PREMIUM_COLORS.ai} />
                <Text style={styles.aiBadgeText}>PRAMAAN AI</Text>
              </View>
              <Text style={styles.aiStatusDot}>● Online</Text>
            </View>
            <Text style={styles.aiTitle}>Ask Pramaan AI</Text>
            <Text style={styles.aiSub}>
              Understand ingredients, decode nutritional jargon & verify health claims instantly.
            </Text>

            <TouchableOpacity
              activeOpacity={0.88}
              style={[styles.aiCtaBtn, PREMIUM_SHADOWS.sm]}
              onPress={() => navigation.navigate('Main', { screen: 'Scan' })}
            >
              <Text style={styles.aiCtaText}>Ask AI →</Text>
            </TouchableOpacity>
          </View>

          {/* 7. Today's Consumption Diary */}
          <View style={[styles.sectionHeaderRow, { marginTop: 26 }]}>
            <Text style={styles.sectionTitle}>Today's Intake</Text>
            <Text style={styles.sectionCountText}>{foodsTodayOnly.length} items logged</Text>
          </View>

          {foodsTodayOnly.length === 0 ? (
            <View style={[styles.emptyDiaryCard, PREMIUM_SHADOWS.sm]}>
              <View style={styles.emptyIconCircle}>
                <Ionicons name="restaurant-outline" size={24} color={PREMIUM_COLORS.secondary} />
              </View>
              <Text style={styles.emptyDiaryTitle}>Nothing consumed yet today</Text>
              <Text style={styles.emptyDiarySub}>
                Scanning a product never logs it automatically. Tap "+ Log to Daily Diary" on any product result to record intake.
              </Text>
            </View>
          ) : (
            foodsTodayOnly.map((f, idx) => {
              const pName = f?.product_name || f?.name || `Meal item ${idx + 1}`;
              const kcal = Number(f?.calories) || 0;
              return (
                <View key={f?.id || idx} style={[styles.diaryItemCard, PREMIUM_SHADOWS.sm]}>
                  <View style={styles.diaryIconBox}>
                    <Ionicons name="restaurant-outline" size={18} color={PREMIUM_COLORS.primaryDark} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.diaryItemName} numberOfLines={1}>{pName}</Text>
                    <Text style={styles.diaryItemTime}>
                      Logged {f?.consumed_at ? new Date(f.consumed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'today'}
                    </Text>
                  </View>
                  <View style={styles.diaryKcalBadge}>
                    <Text style={styles.diaryKcalText}>{formatNutritionValue(kcal)} kcal</Text>
                  </View>
                  {f?.id ? (
                    <TouchableOpacity
                      style={styles.diaryDeleteBtn}
                      onPress={() => handleDeleteFood(f.id, pName)}
                      disabled={deletingId === f.id}
                      activeOpacity={0.7}
                      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                    >
                      {deletingId === f.id ? (
                        <ActivityIndicator size="small" color={PREMIUM_COLORS.status.avoid} />
                      ) : (
                        <Ionicons name="trash-outline" size={16} color={PREMIUM_COLORS.status.avoid} />
                      )}
                    </TouchableOpacity>
                  ) : null}
                </View>
              );

            })
          )}
        </Animated.View>
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
    gap: 12,
  },
  loadingText: {
    fontSize: 14,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },

  // 1. Header
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    marginBottom: 20,
  },
  greeting: {
    fontSize: 14,
    fontWeight: '600',
    color: PREMIUM_COLORS.primaryDark,
    marginBottom: 4,
  },
  heroTitle: {
    fontSize: 30,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.6,
    lineHeight: 36,
  },
  heroSub: {
    fontSize: 14,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    marginTop: 4,
  },
  avatarCircle: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: PREMIUM_COLORS.card,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarInitials: {
    fontSize: 16,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
  },

  // 2. Editorial Hero Scanner Card
  heroScannerCard: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 18,
    marginBottom: 18,
    borderWidth: 1,
    borderColor: 'rgba(85, 122, 62, 0.14)',
  },
  heroRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  heroLeftCol: {
    flex: 1.25,
  },
  heroRightCol: {
    flex: 0.95,
    alignItems: 'center',
    justifyContent: 'center',
  },
  heroBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.primaryLight,
    paddingHorizontal: 9,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
    alignSelf: 'flex-start',
    gap: 6,
    marginBottom: 10,
  },
  heroBadgeDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: PREMIUM_COLORS.primaryDark,
  },
  heroBadgeText: {
    fontSize: 10,
    fontWeight: '800',
    color: PREMIUM_COLORS.primaryDark,
    letterSpacing: 0.6,
  },
  scannerCardTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.3,
    marginBottom: 6,
    lineHeight: 25,
  },
  scannerCardSub: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    lineHeight: 18,
    marginBottom: 14,
  },
  scannerCtaBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.ink,
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: PREMIUM_RADIUS.pill,
    gap: 7,
  },
  scannerCtaText: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.white,
    letterSpacing: 0.2,
  },

  // Hero Right Column Image Frame
  heroImageFrame: {
    width: 122,
    height: 134,
    borderRadius: 20,
    backgroundColor: PREMIUM_COLORS.primaryLight,
    overflow: 'hidden',
    position: 'relative',
    borderWidth: 1,
    borderColor: 'rgba(23, 26, 23, 0.05)',
  },
  heroImage: {
    width: '100%',
    height: '100%',
  },
  heroImageTagPill: {
    position: 'absolute',
    bottom: 6,
    left: 6,
    right: 6,
    backgroundColor: 'rgba(23, 26, 23, 0.76)',
    paddingHorizontal: 6,
    paddingVertical: 3,
    borderRadius: PREMIUM_RADIUS.pill,
    alignItems: 'center',
    justifyContent: 'center',
  },
  heroImageTagText: {
    fontSize: 10,
    fontWeight: '600',
    color: '#FFFFFF',
    letterSpacing: 0.2,
  },

  // 3. Quick Shortcuts
  quickShortcutsRow: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 20,
  },
  shortcutCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.card,
    padding: 13,
    borderRadius: PREMIUM_RADIUS.lg,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    gap: 10,
  },
  shortcutIconWrap: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  shortcutTextWrap: {
    flex: 1,
  },
  shortcutTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  shortcutSub: {
    fontSize: 12,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    marginTop: 1,
  },

  // 4. Section Headers & Nutrition Grid
  sectionHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 19,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.2,
  },
  sectionAction: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.primaryDark,
  },
  sectionCountText: {
    fontSize: 12,
    fontWeight: '500',
    color: PREMIUM_COLORS.secondary,
  },
  overallScorePill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
    gap: 5,
  },
  scoreDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  overallScoreText: {
    fontSize: 12,
    fontWeight: '700',
  },
  nutritionGrid: {
    flexDirection: 'row',
    gap: 10,
  },
  nutritionCard: {
    flex: 1,
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 12,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  macroTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginBottom: 6,
  },
  macroTag: {
    fontSize: 12,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },
  macroValue: {
    fontSize: 18,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.3,
  },
  macroSub: {
    fontSize: 11,
    fontWeight: '500',
    color: PREMIUM_COLORS.muted,
    marginBottom: 8,
  },
  macroProgressTrack: {
    height: 4,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: 2,
    overflow: 'hidden',
  },
  macroProgressFill: {
    height: '100%',
    borderRadius: 2,
  },

  // 5. Recent Scans
  recentScansRow: {
    paddingVertical: 4,
    gap: 14,
  },
  recentScanCard: {
    width: 164,
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 10,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  recentScanName: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginTop: 8,
    marginBottom: 4,
  },
  recentScanFooter: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 4,
  },
  recentScorePill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: PREMIUM_RADIUS.pill,
    gap: 4,
  },
  recentScoreText: {
    fontSize: 11,
    fontWeight: '700',
  },
  recentActionHint: {
    fontSize: 11,
    fontWeight: '500',
    color: PREMIUM_COLORS.muted,
  },
  emptyRecentCard: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 24,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  emptyIconCircle: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 10,
  },
  emptyRecentTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  emptyRecentSub: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    textAlign: 'center',
    marginTop: 4,
    maxWidth: 240,
    lineHeight: 18,
  },

  // 6. AI Section
  aiCard: {
    backgroundColor: PREMIUM_COLORS.status.aiBg,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 18,
    marginTop: 24,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.status.aiBorder,
  },
  aiHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  aiBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: PREMIUM_RADIUS.pill,
    gap: 4,
  },
  aiBadgeText: {
    fontSize: 10,
    fontWeight: '800',
    color: PREMIUM_COLORS.ai,
    letterSpacing: 0.5,
  },
  aiStatusDot: {
    fontSize: 11,
    fontWeight: '600',
    color: '#7C3AED',
  },
  aiTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    marginBottom: 4,
  },
  aiSub: {
    fontSize: 14,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    lineHeight: 20,
    marginBottom: 14,
  },
  aiCtaBtn: {
    alignSelf: 'flex-start',
    backgroundColor: PREMIUM_COLORS.ai,
    paddingVertical: 9,
    paddingHorizontal: 16,
    borderRadius: PREMIUM_RADIUS.pill,
  },
  aiCtaText: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.white,
  },

  // 7. Today's Diary
  diaryItemCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    gap: 12,
  },
  diaryIconBox: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: PREMIUM_COLORS.bgAlt,
    alignItems: 'center',
    justifyContent: 'center',
  },
  diaryItemName: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  diaryItemTime: {
    fontSize: 12,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    marginTop: 2,
  },
  diaryKcalBadge: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
  },
  diaryKcalText: {
    fontSize: 12,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  diaryDeleteBtn: {
    padding: 7,
    borderRadius: PREMIUM_RADIUS.sm,
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    alignItems: 'center',
    justifyContent: 'center',
    marginLeft: 4,
  },
  emptyDiaryCard: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.lg,
    padding: 20,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  emptyDiaryTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
  },
  emptyDiarySub: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    textAlign: 'center',
    marginTop: 4,
    lineHeight: 18,
  },
});
