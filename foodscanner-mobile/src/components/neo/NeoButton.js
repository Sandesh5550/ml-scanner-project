import React, { useRef } from 'react';
import { TouchableOpacity, Text, StyleSheet, ActivityIndicator, View, Animated } from 'react-native';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function NeoButton({
  children,
  title,
  onPress,
  variant = 'primary', // primary (lime), dark/black, outline/white, ai, safe, warning, danger
  size = 'md',         // sm, md, lg
  disabled = false,
  loading = false,
  icon = null,
  style,
  textStyle,
  ...rest
}) {
  const scaleAnim = useRef(new Animated.Value(1)).current;
  const buttonContent = children !== undefined ? children : title;

  const handlePressIn = () => {
    Animated.spring(scaleAnim, {
      toValue: 0.96,
      useNativeDriver: true,
      speed: 40,
      bounciness: 4,
    }).start();
  };

  const handlePressOut = () => {
    Animated.spring(scaleAnim, {
      toValue: 1,
      useNativeDriver: true,
      speed: 30,
      bounciness: 6,
    }).start();
  };

  const getVariantStyles = () => {
    switch (variant) {
      case 'black':
      case 'dark':
        return {
          bg: PREMIUM_COLORS.ink,
          text: PREMIUM_COLORS.white,
          border: 'transparent',
          shadow: PREMIUM_SHADOWS.md,
        };
      case 'white':
      case 'outline':
        return {
          bg: PREMIUM_COLORS.card,
          text: PREMIUM_COLORS.ink,
          border: PREMIUM_COLORS.border,
          shadow: PREMIUM_SHADOWS.sm,
        };
      case 'violet':
      case 'purple':
      case 'ai':
        return {
          bg: PREMIUM_COLORS.ai,
          text: PREMIUM_COLORS.white,
          border: 'transparent',
          shadow: PREMIUM_SHADOWS.sm,
        };
      case 'aiLight':
        return {
          bg: PREMIUM_COLORS.aiBg,
          text: PREMIUM_COLORS.aiDark,
          border: PREMIUM_COLORS.status.aiBorder,
          shadow: PREMIUM_SHADOWS.none,
        };
      case 'green':
      case 'safe':
      case 'success':
        return {
          bg: PREMIUM_COLORS.status.safeBg,
          text: PREMIUM_COLORS.status.safe,
          border: PREMIUM_COLORS.status.safeBorder,
          shadow: PREMIUM_SHADOWS.none,
        };
      case 'amber':
      case 'warning':
      case 'moderate':
      case 'yellow':
        return {
          bg: PREMIUM_COLORS.status.moderateBg,
          text: PREMIUM_COLORS.status.moderate,
          border: PREMIUM_COLORS.status.moderateBorder,
          shadow: PREMIUM_SHADOWS.none,
        };
      case 'coral':
      case 'danger':
      case 'red':
      case 'avoid':
        return {
          bg: PREMIUM_COLORS.status.avoidBg,
          text: PREMIUM_COLORS.status.avoid,
          border: PREMIUM_COLORS.status.avoidBorder,
          shadow: PREMIUM_SHADOWS.none,
        };
      case 'lime':
      case 'primary':
      default:
        return {
          bg: PREMIUM_COLORS.primary,
          text: PREMIUM_COLORS.ink,
          border: 'transparent',
          shadow: PREMIUM_SHADOWS.sm,
        };
    }
  };

  const getSizeStyles = () => {
    switch (size) {
      case 'sm':
        return {
          paddingVertical: 8,
          paddingHorizontal: 14,
          fontSize: 13,
          borderRadius: PREMIUM_RADIUS.pill,
        };
      case 'lg':
        return {
          paddingVertical: 16,
          paddingHorizontal: 24,
          fontSize: 16,
          borderRadius: PREMIUM_RADIUS.pill,
        };
      case 'md':
      default:
        return {
          paddingVertical: 12,
          paddingHorizontal: 20,
          fontSize: 14,
          borderRadius: PREMIUM_RADIUS.pill,
        };
    }
  };

  const vConfig = getVariantStyles();
  const sConfig = getSizeStyles();

  return (
    <Animated.View style={[{ transform: [{ scale: scaleAnim }] }, disabled && styles.disabledWrap]}>
      <TouchableOpacity
        activeOpacity={0.88}
        onPress={onPress}
        onPressIn={handlePressIn}
        onPressOut={handlePressOut}
        disabled={disabled || loading}
        style={[
          styles.button,
          {
            backgroundColor: disabled ? PREMIUM_COLORS.bgAlt : vConfig.bg,
            borderColor: disabled ? 'transparent' : vConfig.border,
            borderWidth: vConfig.border !== 'transparent' ? 1 : 0,
            paddingVertical: sConfig.paddingVertical,
            paddingHorizontal: sConfig.paddingHorizontal,
            borderRadius: sConfig.borderRadius,
          },
          vConfig.shadow,
          style,
        ]}
        {...rest}
      >
        {loading ? (
          <ActivityIndicator size="small" color={vConfig.text} />
        ) : (
          <View style={styles.contentRow}>
            {icon ? <View style={styles.iconBox}>{icon}</View> : null}
            {typeof buttonContent === 'string' ? (
              <Text
                style={[
                  styles.text,
                  {
                    color: disabled ? PREMIUM_COLORS.mutedLight : vConfig.text,
                    fontSize: sConfig.fontSize,
                  },
                  textStyle,
                ]}
              >
                {buttonContent}
              </Text>
            ) : (
              buttonContent
            )}
          </View>
        )}
      </TouchableOpacity>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  button: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  disabledWrap: {
    opacity: 0.65,
  },
  contentRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  iconBox: {
    marginRight: 4,
  },
  text: {
    fontWeight: '700',
    letterSpacing: 0.2,
  },
});
