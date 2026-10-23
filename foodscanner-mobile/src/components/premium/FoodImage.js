import React, { useState } from 'react';
import { View, Text, StyleSheet, Image } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { PREMIUM_COLORS, PREMIUM_RADIUS, PREMIUM_SHADOWS } from '../../theme/premiumTheme';

export default function FoodImage({
  source = null,
  category = 'Food',
  productName = '',
  size = 120,
  height = null,
  borderRadius = PREMIUM_RADIUS.lg,
  style,
}) {
  const [imageError, setImageError] = useState(false);

  const imageUri = typeof source === 'string' ? source.trim() : source?.uri;
  const isRemoteValid = Boolean(imageUri && imageUri.startsWith('http') && !imageError);

  const getCategoryFallback = () => {
    const text = `${productName} ${category}`.toLowerCase();
    if (text.includes('chip') || text.includes('wafer') || text.includes('snack') || text.includes('kurkure')) {
      return { icon: 'nutrition-outline', label: 'Crispy Snack', bg: '#EDF5E1', accent: '#557A3E' };
    }
    if (text.includes('biscuit') || text.includes('cookie') || text.includes('parle') || text.includes('bakery')) {
      return { icon: 'cafe-outline', label: 'Baked Treat', bg: '#F8EFE0', accent: '#8C5E2D' };
    }
    if (text.includes('noodle') || text.includes('maggi') || text.includes('pasta') || text.includes('soup')) {
      return { icon: 'restaurant-outline', label: 'Grain & Meal', bg: '#FBF0E4', accent: '#A85A24' };
    }
    if (text.includes('milk') || text.includes('butter') || text.includes('cheese') || text.includes('dairy')) {
      return { icon: 'water-outline', label: 'Dairy & Protein', bg: '#FEF9E7', accent: '#8C6D15' };
    }
    if (text.includes('salad') || text.includes('veggie') || text.includes('vegetable') || text.includes('fruit')) {
      return { icon: 'leaf-outline', label: 'Fresh Produce', bg: '#E5F3D8', accent: '#4B7728' };
    }
    if (text.includes('beverage') || text.includes('drink') || text.includes('juice') || text.includes('tea')) {
      return { icon: 'flask-outline', label: 'Beverage', bg: '#E6F4FB', accent: '#1D6FA5' };
    }
    if (text.includes('chocolate') || text.includes('sweet') || text.includes('candy')) {
      return { icon: 'cube-outline', label: 'Confectionery', bg: '#F6EAE1', accent: '#7A3E1D' };
    }
    return { icon: 'barcode-outline', label: 'Packaged Food', bg: PREMIUM_COLORS.bgAlt, accent: PREMIUM_COLORS.primaryDark };
  };

  const fallback = getCategoryFallback();
  const boxHeight = height || size;

  if (isRemoteValid) {
    return (
      <View
        style={[
          styles.container,
          {
            width: size,
            height: boxHeight,
            borderRadius,
            backgroundColor: PREMIUM_COLORS.bgAlt,
          },
          PREMIUM_SHADOWS.sm,
          style,
        ]}
      >
        <Image
          source={{ uri: imageUri }}
          style={[styles.image, { borderRadius }]}
          resizeMode="cover"
          onError={() => setImageError(true)}
        />
      </View>
    );
  }

  return (
    <View
      style={[
        styles.fallbackBox,
        {
          width: size,
          height: boxHeight,
          borderRadius,
          backgroundColor: fallback.bg,
        },
        PREMIUM_SHADOWS.sm,
        style,
      ]}
    >
      <View style={[styles.glowCircle, { backgroundColor: fallback.accent + '15' }]}>
        <Ionicons name={fallback.icon} size={Math.min(30, Math.floor(size * 0.28))} color={fallback.accent} />
      </View>
      <Text style={[styles.fallbackLabel, { color: fallback.accent }]} numberOfLines={1}>
        {fallback.label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: PREMIUM_COLORS.border,
  },
  image: {
    width: '100%',
    height: '100%',
  },
  fallbackBox: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: 'rgba(23, 26, 23, 0.06)',
    padding: 10,
  },
  glowCircle: {
    width: 48,
    height: 48,
    borderRadius: 24,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 6,
  },
  fallbackLabel: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.3,
    textTransform: 'uppercase',
  },
});
