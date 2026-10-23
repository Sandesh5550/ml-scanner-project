import React, { useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Animated } from 'react-native';
import { PREMIUM_COLORS } from '../../theme/premiumTheme';

export default function NeoProgressBar({
  progress = 0, // 0 to 1 or 0 to 100
  color = PREMIUM_COLORS.primary,
  height = 8,
  label,
  valueText,
  style,
  animated = true,
}) {
  const norm = progress > 1 ? Math.min(100, Math.max(0, progress)) : Math.min(100, Math.max(0, progress * 100));
  const animWidth = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (animated) {
      Animated.timing(animWidth, {
        toValue: norm,
        duration: 500,
        useNativeDriver: false,
      }).start();
    } else {
      animWidth.setValue(norm);
    }
  }, [norm, animated, animWidth]);

  return (
    <View style={[styles.container, style]}>
      {label || valueText ? (
        <View style={styles.labelRow}>
          {label ? <Text style={styles.labelText}>{label}</Text> : <View />}
          {valueText ? <Text style={styles.valueText}>{valueText}</Text> : null}
        </View>
      ) : null}
      <View style={[styles.track, { height, borderRadius: height / 2 }]}>
        <Animated.View
          style={[
            styles.fill,
            {
              width: animWidth.interpolate({
                inputRange: [0, 100],
                outputRange: ['0%', '100%'],
              }),
              backgroundColor: color,
              borderRadius: height / 2,
            },
          ]}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginVertical: 4,
  },
  labelRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  labelText: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.ink,
  },
  valueText: {
    fontSize: 12,
    fontWeight: '700',
    color: PREMIUM_COLORS.secondary,
  },
  track: {
    backgroundColor: PREMIUM_COLORS.bgAlt,
    overflow: 'hidden',
    justifyContent: 'center',
  },
  fill: {
    height: '100%',
  },
});
