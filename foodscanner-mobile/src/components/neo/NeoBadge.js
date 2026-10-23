import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { PREMIUM_COLORS, PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function NeoBadge({
  label,
  text,
  children,
  color,
  variant = 'lime', // lime, safe/green, moderate/amber, avoid/coral, ai/violet, gray
  icon = null,
  showDot = false,
  size = 'md',      // sm, md
  style,
  textStyle,
}) {
  const badgeText = label !== undefined ? label : text;
  const badgeColor = color || variant;

  const getThemeMeta = () => {
    switch (badgeColor) {
      case 'coral':
      case 'red':
      case 'avoid':
      case 'danger':
        return {
          bg: PREMIUM_COLORS.status.avoidBg,
          text: PREMIUM_COLORS.status.avoid,
          dot: PREMIUM_COLORS.status.avoid,
        };
      case 'green':
      case 'safe':
      case 'success':
        return {
          bg: PREMIUM_COLORS.status.safeBg,
          text: PREMIUM_COLORS.status.safe,
          dot: PREMIUM_COLORS.status.safe,
        };
      case 'amber':
      case 'yellow':
      case 'warning':
      case 'moderate':
        return {
          bg: PREMIUM_COLORS.status.moderateBg,
          text: PREMIUM_COLORS.status.moderate,
          dot: PREMIUM_COLORS.status.moderate,
        };
      case 'violet':
      case 'purple':
      case 'ai':
        return {
          bg: PREMIUM_COLORS.status.aiBg,
          text: PREMIUM_COLORS.status.ai,
          dot: PREMIUM_COLORS.status.ai,
        };
      case 'gray':
      case 'muted':
        return {
          bg: PREMIUM_COLORS.bgAlt,
          text: PREMIUM_COLORS.secondary,
          dot: PREMIUM_COLORS.secondary,
        };
      case 'lime':
      case 'primary':
      default:
        return {
          bg: PREMIUM_COLORS.primaryLight,
          text: PREMIUM_COLORS.primaryDark,
          dot: PREMIUM_COLORS.primaryDark,
        };
    }
  };

  const isSm = size === 'sm';
  const meta = getThemeMeta();

  return (
    <View
      style={[
        styles.badge,
        {
          backgroundColor: meta.bg,
          paddingVertical: isSm ? 3 : 5,
          paddingHorizontal: isSm ? 8 : 12,
          borderRadius: PREMIUM_RADIUS.pill,
        },
        style,
      ]}
    >
      {showDot ? <View style={[styles.dot, { backgroundColor: meta.dot }]} /> : null}
      {icon ? <View style={styles.iconBox}>{icon}</View> : null}
      {badgeText ? (
        <Text
          style={[
            styles.text,
            {
              color: meta.text,
              fontSize: isSm ? 11 : 12,
            },
            textStyle,
          ]}
        >
          {badgeText}
        </Text>
      ) : (
        children
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    gap: 5,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  iconBox: {
    marginRight: 2,
  },
  text: {
    fontWeight: '700',
    letterSpacing: 0.2,
  },
});
