import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function NeoCard({
  children,
  style,
  headerTitle,
  headerRight,
  accentColor,
  contentStyle,
  onPress,
  disabled = false,
  shadow = 'md',
  ...rest
}) {
  const CardContainer = onPress ? TouchableOpacity : View;
  const shadowStyle = shadow === 'none' ? PREMIUM_SHADOWS.none : PREMIUM_SHADOWS[shadow] || PREMIUM_SHADOWS.md;

  return (
    <CardContainer
      style={[styles.card, shadowStyle, style]}
      onPress={onPress}
      disabled={disabled || !onPress}
      activeOpacity={0.92}
      {...rest}
    >
      {headerTitle ? (
        <View style={[styles.headerBanner, accentColor ? { backgroundColor: accentColor } : styles.defaultHeader]}>
          <Text style={styles.headerTitleText} numberOfLines={1}>{headerTitle}</Text>
          {headerRight ? <View>{headerRight}</View> : null}
        </View>
      ) : null}
      <View style={[styles.body, contentStyle]}>
        {children}
      </View>
    </CardContainer>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: PREMIUM_COLORS.card,
    borderRadius: PREMIUM_RADIUS.lg, // 22px
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    overflow: 'hidden',
    marginBottom: 16,
  },
  headerBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: PREMIUM_COLORS.divider,
  },
  defaultHeader: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
  },
  headerTitleText: {
    fontSize: 13,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: 0.3,
    textTransform: 'uppercase',
  },
  body: {
    padding: 16,
  },
});
