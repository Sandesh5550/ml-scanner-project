import React, { useState, useRef } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { getNetworkErrorMessage, login, register } from '../services/api';
import { useAuth } from '../context/AuthContext';
import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';
import { NeoButton, NeoInput, NeoTab } from '../components/neo';

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function LoginScreen() {
  const { login: authLogin } = useAuth();
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const isSubmittingRef = useRef(false);

  const handleModeChange = (newMode) => {
    setMode(newMode);
    setErrorMessage('');
  };

  const handleEmailChange = (val) => {
    setEmail(val);
    if (errorMessage) setErrorMessage('');
  };

  const handlePasswordChange = (val) => {
    setPassword(val);
    if (errorMessage) setErrorMessage('');
  };

  const onSubmit = async () => {
    if (loading || isSubmittingRef.current) return;

    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setErrorMessage('Please enter your email address.');
      return;
    }

    if (!EMAIL_REGEX.test(trimmedEmail)) {
      setErrorMessage('Please enter a valid email address (e.g. name@example.com).');
      return;
    }

    if (!password) {
      setErrorMessage('Please enter your password.');
      return;
    }

    if (mode === 'register' && password.length < 6) {
      setErrorMessage('Password must be at least 6 characters long.');
      return;
    }

    isSubmittingRef.current = true;
    setLoading(true);
    setErrorMessage('');

    try {
      let data;
      if (mode === 'register') {
        data = await register(trimmedEmail, password, name.trim() || null);
      } else {
        data = await login(trimmedEmail, password);
      }

      const token = data?.access_token;
      if (!token) {
        throw new Error('Authentication succeeded but no access token was returned.');
      }
      await authLogin(token);
    } catch (e) {
      const msg = getNetworkErrorMessage(e) || 'Authentication failed. Please check your credentials.';
      setErrorMessage(String(msg));
      if (Platform.OS !== 'web') {
        Alert.alert('Authentication Notice', String(msg));
      }
    } finally {
      setLoading(false);
      isSubmittingRef.current = false;
    }
  };

  const handleDemoFill = () => {
    setEmail('demo@pramaan.ai');
    setPassword('DemoPass123!');
    setErrorMessage('');
  };

  const isFormValid =
    email.trim().length > 0 &&
    password.length > 0 &&
    (mode === 'login' || password.length >= 6);

  return (
    <View style={styles.screen}>
      <KeyboardAvoidingView
        style={styles.kb}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {/* Hero Branding & Floating Food Badges */}
          <View style={styles.heroSection}>
            <View style={styles.foodBadgesRow}>
              <View style={[styles.floatingBubble, { backgroundColor: PREMIUM_COLORS.primaryLight, transform: [{ scale: 0.95 }] }]}>
                <Ionicons name="nutrition-outline" size={22} color={PREMIUM_COLORS.primaryDark} />
              </View>
              <View style={[styles.floatingBubble, { backgroundColor: '#EEF6E8', transform: [{ translateY: -10 }] }]}>
                <Ionicons name="leaf-outline" size={28} color={PREMIUM_COLORS.primaryDark} />
              </View>
              <View style={[styles.floatingBubble, { backgroundColor: PREMIUM_COLORS.aiBg, transform: [{ scale: 0.9 }] }]}>
                <Ionicons name="shield-checkmark-outline" size={20} color={PREMIUM_COLORS.ai} />
              </View>
            </View>

            <View style={styles.brandBadge}>
              <Text style={styles.brandBadgeText}>PRAMAAN</Text>
            </View>

            <Text style={styles.headline}>
              Explore, Scan and{"\n"}
              <Text style={styles.headlineHighlight}>Eat</Text> Healthy!
            </Text>
            <Text style={styles.subheadline}>
              Scientific nutritional clarity, hazard detection, and personalized food intelligence.
            </Text>
          </View>

          {/* Form Card */}
          <View style={[styles.formCard, PREMIUM_SHADOWS.sm]}>
            <NeoTab
              tabs={[
                { key: 'login', label: 'Log In' },
                { key: 'register', label: 'Create Account' },
              ]}
              activeTab={mode}
              onTabChange={handleModeChange}
            />

            {/* Inline Error Banner */}
            {errorMessage ? (
              <View style={styles.errorBanner}>
                <Ionicons name="alert-circle" size={18} color={PREMIUM_COLORS.status.avoid} />
                <Text style={styles.errorBannerText}>{errorMessage}</Text>
                <TouchableOpacity
                  onPress={() => setErrorMessage('')}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                >
                  <Ionicons name="close-circle-outline" size={16} color={PREMIUM_COLORS.secondary} />
                </TouchableOpacity>
              </View>
            ) : null}

            {mode === 'register' ? (
              <NeoInput
                label="Full Name"
                placeholder="e.g. Kaluna Sharma"
                value={name}
                onChangeText={setName}
              />
            ) : null}

            <NeoInput
              label="Email Address"
              placeholder="e.g. user@example.com"
              value={email}
              onChangeText={handleEmailChange}
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
            />

            <NeoInput
              label="Password"
              placeholder="••••••••"
              value={password}
              onChangeText={handlePasswordChange}
              secureTextEntry={!showPassword}
              rightElement={
                <TouchableOpacity
                  onPress={() => setShowPassword((prev) => !prev)}
                  hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
                  style={{ padding: 4 }}
                >
                  <Ionicons
                    name={showPassword ? 'eye-off-outline' : 'eye-outline'}
                    size={20}
                    color={PREMIUM_COLORS.secondary}
                  />
                </TouchableOpacity>
              }
            />

            <NeoButton
              title={mode === 'register' ? 'Get Started →' : 'Log In →'}
              onPress={onSubmit}
              loading={loading}
              disabled={loading || !isFormValid}
              variant="black"
              size="lg"
              style={{ marginTop: 6 }}
            />

            <TouchableOpacity
              style={styles.demoBtn}
              onPress={handleDemoFill}
              activeOpacity={0.8}
            >
              <Text style={styles.demoBtnText}>Use Fast Demo Credentials</Text>
            </TouchableOpacity>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: PREMIUM_COLORS.bg,
  },
  kb: {
    flex: 1,
  },
  scrollContent: {
    flexGrow: 1,
    padding: 24,
    paddingTop: 54,
    paddingBottom: 36,
    justifyContent: 'center',
  },
  heroSection: {
    alignItems: 'center',
    marginBottom: 24,
  },
  foodBadgesRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    marginBottom: 16,
  },
  floatingBubble: {
    width: 60,
    height: 60,
    borderRadius: 30,
    alignItems: 'center',
    justifyContent: 'center',
    ...PREMIUM_SHADOWS.sm,
  },
  brandBadge: {
    backgroundColor: PREMIUM_COLORS.primaryLight,
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
    marginBottom: 10,
  },
  brandBadgeText: {
    fontSize: 11,
    fontWeight: '800',
    color: PREMIUM_COLORS.primaryDark,
    letterSpacing: 0.8,
  },
  headline: {
    fontSize: 28,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    textAlign: 'center',
    letterSpacing: -0.5,
    lineHeight: 34,
  },
  headlineHighlight: {
    color: PREMIUM_COLORS.primaryDark,
  },
  subheadline: {
    fontSize: 14,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    textAlign: 'center',
    marginTop: 6,
    lineHeight: 20,
    maxWidth: 290,
  },
  formCard: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.xl,
    padding: 22,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: PREMIUM_COLORS.status.avoidBg,
    borderColor: PREMIUM_COLORS.status.avoidBorder,
    borderWidth: 1,
    borderRadius: PREMIUM_RADIUS.md,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginBottom: 14,
    gap: 8,
  },
  errorBannerText: {
    flex: 1,
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.status.avoid,
    lineHeight: 18,
  },
  demoBtn: {
    alignSelf: 'center',
    marginTop: 14,
    paddingVertical: 6,
  },
  demoBtnText: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.primaryDark,
  },
});
