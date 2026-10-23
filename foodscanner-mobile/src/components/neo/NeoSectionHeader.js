import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { PREMIUM_COLORS, PREMIUM_RADIUS } from '../../theme/premiumTheme';

export default function NeoSectionHeader({
  title,
  subtitle,
  rightElement,
  tagText,
  count,
  tagColor = 'primary',
  markerColor = PREMIUM_COLORS.primary,
  style,
}) {
  const displayTag = tagText || count;
  return (
    <View style={[styles.container, style]}>
      <View style={styles.topRow}>
        <View style={styles.titleGroup}>
          <View style={[styles.marker, { backgroundColor: markerColor }]} />
          <Text style={styles.title}>{title}</Text>
          {displayTag ? (
            <View style={[styles.tag, { backgroundColor: PREMIUM_COLORS.primaryLight }]}>
              <Text style={styles.tagText}>{displayTag}</Text>
            </View>
          ) : null}
        </View>
        {rightElement ? <View>{rightElement}</View> : null}
      </View>
      {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 12,
    marginTop: 4,
  },
  topRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  titleGroup: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    gap: 8,
  },
  marker: {
    width: 6,
    height: 18,
    borderRadius: 3,
  },
  title: {
    fontSize: 18,
    fontWeight: '700',
    color: PREMIUM_COLORS.ink,
    letterSpacing: -0.3,
  },
  tag: {
    borderRadius: PREMIUM_RADIUS.pill,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  tagText: {
    fontSize: 11,
    fontWeight: '700',
    color: PREMIUM_COLORS.primaryDark,
  },
  subtitle: {
    fontSize: 13,
    fontWeight: '400',
    color: PREMIUM_COLORS.secondary,
    marginTop: 3,
    paddingLeft: 14,
  },
});
