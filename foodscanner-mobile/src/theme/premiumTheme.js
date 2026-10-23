/**
 * PRAMAAN Premium Health-Tech Design System Tokens
 * Source of Truth for PRAMAAN Mobile Client
 * 
 * Aesthetic:
 * - Calm natural health palette (off-white, white, dark charcoal, natural green)
 * - Soft elevated cards (18-26px border radius, diffused multi-layer shadows)
 * - Restrained semantic accents (Safe green, Moderate amber, Avoid coral, AI lavender)
 * - Clean editorial typography with strong hierarchy
 */

import { Platform } from 'react-native';

export const SYSTEM_FONT = Platform.select({
  ios: 'System',
  android: 'Roboto',
  default: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
});

export const PREMIUM_COLORS = {
  // Canvas & Surfaces
  bg: '#F7F8F3',            // Calm natural off-white canvas
  bgAlt: '#EEF2EA',         // Soft sage / nested surface tint
  card: '#FFFFFF',          // Clean pure white elevated card
  cardAlt: '#FDFDFB',       // Alternative subtle warm card
  border: 'rgba(23, 26, 23, 0.07)', // Soft hairline border (never heavy black)
  borderLight: 'rgba(23, 26, 23, 0.04)',
  divider: '#EBEFE7',

  // Typography - High Contrast Editorial System
  ink: '#171A17',           // Deep charcoal primary text
  text: '#171A17',
  secondary: '#5F665E',      // Balanced secondary text (high contrast)
  muted: '#7C837B',          // Muted text (readable contrast)
  mutedLight: '#6B726A',     // Subdued caption / metadata text
  white: '#FFFFFF',

  // Health Green System
  primary: '#B8D96B',        // Primary natural health green
  primaryDark: '#557A3E',    // Deep forest green
  primaryLight: '#E5F0D0',   // Soft green surface tint
  lime: '#B8D96B',
  limeAccent: '#D7F28A',     // Fresh vibrant accent lime

  // Semantic Colors
  status: {
    safe: '#557A3E',
    safeBg: '#E5F0D0',
    safeBorder: '#CCE4A6',

    moderate: '#B57900',
    moderateBg: '#FEF3D6',
    moderateBorder: '#F9E2A8',

    avoid: '#D95C5C',
    avoidBg: '#F5DADA',
    avoidBorder: '#EFAFAF',

    ai: '#8B7CF6',
    aiBg: '#F2EFFF',
    aiBorder: '#D8D1FC',

    neutral: '#EEF2EA',
    neutralText: '#70766F',
  },

  // Direct Semantic Aliases
  safe: '#557A3E',
  safeBg: '#E5F0D0',
  moderate: '#B57900',
  moderateBg: '#FEF3D6',
  avoid: '#D95C5C',
  avoidBg: '#F5DADA',

  // AI Assistant Accents
  ai: '#8B7CF6',
  aiBg: '#F2EFFF',
  aiDark: '#6D5CD8',
  violet: '#8B7CF6',

  // Warm Editorial Accents (used sparingly)
  warmCream: '#F7EFD9',
  softPeach: '#F8E4D6',
  softAmber: '#FEF3D6',
  softRed: '#F5DADA',
  softBlue: '#E6F3FB',
  blue: '#3B82F6',
};

export const PREMIUM_SHADOWS = {
  // Soft, diffused modern health-tech elevation (No hard black borders or brutalist offsets)
  sm: {
    shadowColor: '#171A17',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.04,
    shadowRadius: 8,
    elevation: 2,
  },
  md: {
    shadowColor: '#171A17',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.06,
    shadowRadius: 16,
    elevation: 3,
  },
  lg: {
    shadowColor: '#171A17',
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.08,
    shadowRadius: 24,
    elevation: 5,
  },
  floating: {
    shadowColor: '#171A17',
    shadowOffset: { width: 0, height: 12 },
    shadowOpacity: 0.10,
    shadowRadius: 28,
    elevation: 8,
  },
  none: {
    shadowColor: 'transparent',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0,
    shadowRadius: 0,
    elevation: 0,
  },
};

export const PREMIUM_BORDERS = {
  none: 0,
  hairline: 1,
  thin: 1.5,
};

export const PREMIUM_RADIUS = {
  xs: 6,
  sm: 10,
  md: 16,
  lg: 22,
  xl: 26,
  card: 24,
  pill: 999,
};

export const PREMIUM_SPACING = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 28,
  hero: 36,
};

export const PREMIUM_TYPOGRAPHY = {
  hero: {
    fontFamily: SYSTEM_FONT,
    fontSize: 30,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.6,
    lineHeight: 36,
  },
  h1: {
    fontFamily: SYSTEM_FONT,
    fontSize: 26,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.5,
    lineHeight: 32,
  },
  h2: {
    fontFamily: SYSTEM_FONT,
    fontSize: 20,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.3,
    lineHeight: 26,
  },
  h3: {
    fontFamily: SYSTEM_FONT,
    fontSize: 16,
    fontWeight: '600',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.2,
    lineHeight: 22,
  },
  body: {
    fontFamily: SYSTEM_FONT,
    fontSize: 15,
    fontWeight: '400',
    color: PREMIUM_COLORS.ink,
    lineHeight: 22,
  },
  bodyBold: {
    fontFamily: SYSTEM_FONT,
    fontSize: 15,
    fontWeight: '600',
    color: PREMIUM_COLORS.ink,
    lineHeight: 22,
  },
  caption: {
    fontFamily: SYSTEM_FONT,
    fontSize: 13,
    fontWeight: '500',
    color: PREMIUM_COLORS.secondary,
    lineHeight: 18,
  },
  metadata: {
    fontFamily: SYSTEM_FONT,
    fontSize: 12,
    fontWeight: '500',
    color: PREMIUM_COLORS.mutedLight,
    lineHeight: 16,
  },
  badge: {
    fontFamily: SYSTEM_FONT,
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.3,
  },
};

export default {
  colors: PREMIUM_COLORS,
  shadows: PREMIUM_SHADOWS,
  borders: PREMIUM_BORDERS,
  radius: PREMIUM_RADIUS,
  spacing: PREMIUM_SPACING,
  typography: PREMIUM_TYPOGRAPHY,
};
