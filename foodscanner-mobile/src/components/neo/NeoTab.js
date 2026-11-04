import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { PREMIUM_COLORS, PREMIUM_RADIUS, PREMIUM_SHADOWS } from '../../theme/premiumTheme';

export default function NeoTab({
  tabs = [],
  activeTab,
  onTabChange,
  activeColor = PREMIUM_COLORS.card,
  style,
}) {
  return (
    <View style={[styles.container, style]}>
      {tabs.map((tab, idx) => {
        const key = typeof tab === 'string' ? tab : tab.key;
        const label = typeof tab === 'string' ? tab : tab.label;
        const isActive = activeTab === key;

        return (
          <TouchableOpacity
            key={key || idx}
            activeOpacity={0.88}
            onPress={() => onTabChange && onTabChange(key)}
            style={[
              styles.tab,
              isActive && [
                styles.tabActive,
                { backgroundColor: activeColor },
                PREMIUM_SHADOWS.sm,
              ],
            ]}
          >
            <Text style={[styles.tabText, isActive && styles.tabTextActive]}>
              {label}
            </Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    backgroundColor: PREMIUM_COLORS.bgAlt,
    borderRadius: PREMIUM_RADIUS.pill,
    padding: 4,
    marginBottom: 14,
  },
  tab: {
    flex: 1,
    paddingVertical: 9,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: PREMIUM_RADIUS.pill,
  },
  tabActive: {
    backgroundColor: PREMIUM_COLORS.card,
  },
  tabText: {
    fontSize: 13,
    fontWeight: '600',
    color: PREMIUM_COLORS.secondary,
  },
  tabTextActive: {
    color: PREMIUM_COLORS.ink,
    fontWeight: '700',
  },
});
