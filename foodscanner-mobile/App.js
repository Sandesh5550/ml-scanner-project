import React from 'react';
import { View, StyleSheet, Platform, useWindowDimensions } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { StatusBar } from 'expo-status-bar';

import AppNavigator from './src/navigation/AppNavigator';
import { navigationRef } from './src/utils/navigationRef';
import { AuthProvider } from './src/context/AuthContext';

export default function App() {
  const { width, height } = useWindowDimensions();

  const appContent = (
    <AuthProvider>
      <NavigationContainer ref={navigationRef}>
        <StatusBar style="auto" />
        <AppNavigator />
      </NavigationContainer>
    </AuthProvider>
  );

  if (Platform.OS === 'web' && width > 520) {
    return (
      <View style={styles.webContainer}>
        <View
          style={[
            styles.phoneFrame,
            { height: Math.min(height - 32, 900) },
          ]}
        >
          {appContent}
        </View>
      </View>
    );
  }

  return appContent;
}

const styles = StyleSheet.create({
  webContainer: {
    flex: 1,
    height: '100vh',
    width: '100vw',
    backgroundColor: '#0F172A',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  phoneFrame: {
    width: 430,
    maxWidth: '100%',
    backgroundColor: '#F5F2EC',
    borderRadius: 24,
    borderWidth: 2,
    borderColor: '#334155',
    overflow: 'hidden',
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: 16 },
    shadowOpacity: 0.4,
    shadowRadius: 32,
    elevation: 20,
  },
});

