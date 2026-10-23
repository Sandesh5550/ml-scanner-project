import React from 'react';
import { View, Platform, StyleSheet, ActivityIndicator } from 'react-native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import {
  PREMIUM_COLORS,
  PREMIUM_SHADOWS,
  PREMIUM_RADIUS,
} from '../theme/premiumTheme';

import { useAuth } from '../context/AuthContext';

import LoginScreen from '../screens/LoginScreen';
import HomeScreen from '../screens/HomeScreen';
import ScanScreen from '../screens/ScanScreen';
import ResultScreen from '../screens/ResultScreen';
import ReportScreen from '../screens/ReportScreen';
import ProfileScreen from '../screens/ProfileScreen';
import ManualEntryScreen from '../screens/ManualEntryScreen';
import OCRScanScreen from '../screens/OCRScanScreen';

const Tab = createBottomTabNavigator();
const Stack = createNativeStackNavigator();

function TabNavigator() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarShowLabel: true,
        tabBarActiveTintColor: PREMIUM_COLORS.primaryDark,
        tabBarInactiveTintColor: PREMIUM_COLORS.secondary,
        tabBarLabelStyle: {
          fontWeight: '700',
          fontSize: 10,
          marginTop: -2,
          marginBottom: 4,
          letterSpacing: 0.2,
        },
        tabBarStyle: {
          position: 'absolute',
          bottom: Platform.OS === 'ios' ? 24 : 14,
          left: 16,
          right: 16,
          backgroundColor: PREMIUM_COLORS.card,
          borderRadius: 28,
          height: 64,
          paddingBottom: 4,
          paddingTop: 4,
          borderTopWidth: 0,
          borderWidth: 1,
          borderColor: PREMIUM_COLORS.border,
          ...PREMIUM_SHADOWS.floating,
        },
        tabBarItemStyle: {
          justifyContent: 'center',
          alignItems: 'center',
          paddingVertical: 2,
        },
        tabBarIcon: ({ focused }) => {
          let iconName;
          if (route.name === 'Home') {
            iconName = focused ? 'home' : 'home-outline';
          } else if (route.name === 'Scan') {
            iconName = focused ? 'scan' : 'scan-outline';
          } else if (route.name === 'Report') {
            iconName = focused ? 'stats-chart' : 'stats-chart-outline';
          } else if (route.name === 'Profile') {
            iconName = focused ? 'person' : 'person-outline';
          }

          const iconColor = focused ? PREMIUM_COLORS.primaryDark : PREMIUM_COLORS.secondary;

          return (
            <View
              style={[
                styles.iconWrap,
                focused ? styles.iconWrapFocused : null,
              ]}
            >
              <Ionicons name={iconName} size={20} color={iconColor} />
            </View>
          );
        },
      })}
    >
      <Tab.Screen name="Home" component={HomeScreen} />
      <Tab.Screen name="Scan" component={ScanScreen} />
      <Tab.Screen name="Report" component={ReportScreen} options={{ tabBarLabel: 'Reports' }} />
      <Tab.Screen name="Profile" component={ProfileScreen} />
    </Tab.Navigator>
  );
}

export default function AppNavigator() {
  const { isLoggedIn } = useAuth();

  if (isLoggedIn === null) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={PREMIUM_COLORS.primaryDark} />
      </View>
    );
  }

  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      {!isLoggedIn ? (
        <Stack.Screen name="Login" component={LoginScreen} />
      ) : (
        <>
          <Stack.Screen name="Main" component={TabNavigator} />
          <Stack.Screen
            name="Result"
            component={ResultScreen}
            options={{ unmountOnBlur: true, headerShown: false }}
          />
          <Stack.Screen name="ManualEntry" component={ManualEntryScreen} options={{ headerShown: false }} />
          <Stack.Screen name="OCRScan" component={OCRScanScreen} options={{ headerShown: false }} />
        </>
      )}
    </Stack.Navigator>
  );
}

const styles = StyleSheet.create({
  iconWrap: {
    paddingHorizontal: 14,
    paddingVertical: 4,
    borderRadius: PREMIUM_RADIUS.pill,
    backgroundColor: 'transparent',
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconWrapFocused: {
    backgroundColor: PREMIUM_COLORS.primaryLight,
  },
  loadingContainer: {
    flex: 1,
    backgroundColor: PREMIUM_COLORS.bg,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
