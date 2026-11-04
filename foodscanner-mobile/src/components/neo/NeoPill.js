import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { PREMIUM_COLORS, PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function NeoPill({
  label,
  children,
  bg = PREMIUM_COLORS.card,
  color = PREMIUM_COLORS.ink,
  style,
  textStyle,
}) {
  return (
    <View style={[styles.pill, { backgroundColor: bg }, style]}>
      {label ? (
        <Text style={[styles.text, { color }, textStyle]}>{label}</Text>
      ) : (
        children
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    borderRadius: PREMIUM_RADIUS.pill,
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
    alignItems: 'center',
    justifyContent: 'center',
    alignSelf: 'flex-start',
  },
  text: {
    fontSize: 12,
    fontWeight: '600',
    letterSpacing: 0.1,
  },
});
