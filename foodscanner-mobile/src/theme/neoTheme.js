/**
 * PRAMAAN Theme Compatibility Bridge
 * Bridges legacy NEO_* tokens directly into the new Premium Health-Tech Design System.
 * Guarantees zero runtime breakage across any legacy screen references while immediately
 * activating the calm, modern health-tech visual palette.
 */

import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_SPACING,
  PREMIUM_TYPOGRAPHY,
} from './premiumTheme';

export const NEO_COLORS = {
  // Canvas & Surfaces - Calm Natural Health Palette
  bg: PREMIUM_COLORS.bg,               // #F7F8F3
  bgAlt: PREMIUM_COLORS.bgAlt,         // #EEF2EA
  card: PREMIUM_COLORS.card,           // #FFFFFF
  ink: PREMIUM_COLORS.ink,             // #171A17
  border: PREMIUM_COLORS.border,       // Subtle hairline border
  borderLight: PREMIUM_COLORS.borderLight,
  white: PREMIUM_COLORS.white,
  muted: PREMIUM_COLORS.secondary,     // #70766F
  mutedLight: PREMIUM_COLORS.mutedLight, // #9BA098

  // Primary Health Brand Colors
  lime: PREMIUM_COLORS.primary,        // #B8D96B
  primary: PREMIUM_COLORS.primary,
  primaryDark: PREMIUM_COLORS.primaryDark,
  primaryLight: PREMIUM_COLORS.primaryLight,
  limeAccent: PREMIUM_COLORS.limeAccent,

  // Technical & Functional Accents
  electricBlue: PREMIUM_COLORS.blue,
  blue: PREMIUM_COLORS.blue,
  cyan: PREMIUM_COLORS.primary,

  // AI Assistant Accents
  violet: PREMIUM_COLORS.ai,           // #8B7CF6
  purple: PREMIUM_COLORS.ai,
  purpleLight: PREMIUM_COLORS.aiBg,    // #F2EFFF
  ai: PREMIUM_COLORS.ai,
  aiBg: PREMIUM_COLORS.aiBg,

  // Semantic Status Colors
  green: PREMIUM_COLORS.safe,          // #557A3E
  greenLight: PREMIUM_COLORS.safeBg,   // #E5F0D0
  yellow: PREMIUM_COLORS.moderate,     // #B57900
  amber: PREMIUM_COLORS.moderate,      // #B57900
  amberLight: PREMIUM_COLORS.moderateBg,
  coral: PREMIUM_COLORS.avoid,         // #D95C5C
  red: PREMIUM_COLORS.avoid,           // #D95C5C
  redLight: PREMIUM_COLORS.avoidBg,

  // Warm Editorial Accents
  pink: '#F8E4D6',
  pinkLight: '#FDF6F0',
  orange: '#E88B54',

  // Semantic Status Mapping
  status: PREMIUM_COLORS.status,
};

export const NEO_SHADOWS = {
  sm: PREMIUM_SHADOWS.sm,
  md: PREMIUM_SHADOWS.md,
  lg: PREMIUM_SHADOWS.lg,
  none: PREMIUM_SHADOWS.none,
};

export const NEO_BORDERS = {
  thin: 1,
  regular: 1,
  thick: 1,          // Hairline clean border replaces chunky 2.5px black lines
  extraThick: 1.5,
};

export const NEO_RADIUS = {
  xs: 6,
  sm: 10,
  md: 18,            // Soft rounded cards (was 10)
  lg: 22,
  xl: 26,
  pill: 999,
};

export const NEO_SPACING = PREMIUM_SPACING;

export const NEO_TYPOGRAPHY = {
  hero: PREMIUM_TYPOGRAPHY.hero,
  h1: PREMIUM_TYPOGRAPHY.h1,
  h2: PREMIUM_TYPOGRAPHY.h2,
  h3: PREMIUM_TYPOGRAPHY.h3,
  body: PREMIUM_TYPOGRAPHY.body,
  bodyBold: PREMIUM_TYPOGRAPHY.bodyBold,
  caption: PREMIUM_TYPOGRAPHY.caption,
  badge: PREMIUM_TYPOGRAPHY.badge,
};

export default {
  colors: NEO_COLORS,
  shadows: NEO_SHADOWS,
  borders: NEO_BORDERS,
  radius: NEO_RADIUS,
  spacing: NEO_SPACING,
  typography: NEO_TYPOGRAPHY,
};
