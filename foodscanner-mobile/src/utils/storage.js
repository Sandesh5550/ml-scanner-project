import AsyncStorage from '@react-native-async-storage/async-storage';

const TOKEN_KEY = 'foodscanner_token';
const REFRESH_TOKEN_KEY = 'foodscanner_refresh_token';

let SecureStore = null;
try {
  // Prevent Metro bundler from statically resolving uninstalled optional module
  const storePkg = ['expo', 'secure', 'store'].join('-');
  const req = typeof require === 'function' ? require : null;
  if (req) {
    SecureStore = req(storePkg);
  }
} catch (_e) {
  SecureStore = null;
}

export async function saveToken(token) {
  if (!token) return;
  if (SecureStore && typeof SecureStore.setItemAsync === 'function') {
    try {
      await SecureStore.setItemAsync(TOKEN_KEY, token);
      return;
    } catch (_e) {
      // Fall back to AsyncStorage
    }
  }
  await AsyncStorage.setItem(TOKEN_KEY, token);
}

export async function getToken() {
  if (SecureStore && typeof SecureStore.getItemAsync === 'function') {
    try {
      const val = await SecureStore.getItemAsync(TOKEN_KEY);
      if (val !== null && val !== undefined) return val;
    } catch (_e) {
      // Fall back to AsyncStorage
    }
  }
  return AsyncStorage.getItem(TOKEN_KEY);
}

export async function removeToken() {
  if (SecureStore && typeof SecureStore.deleteItemAsync === 'function') {
    try {
      await SecureStore.deleteItemAsync(TOKEN_KEY);
    } catch (_e) {
      // Fall back
    }
  }
  await AsyncStorage.removeItem(TOKEN_KEY);
}

export async function saveRefreshToken(refreshToken) {
  if (!refreshToken) return;
  if (SecureStore && typeof SecureStore.setItemAsync === 'function') {
    try {
      await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, refreshToken);
      return;
    } catch (_e) {
      // Fall back to AsyncStorage
    }
  }
  await AsyncStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export async function getRefreshToken() {
  if (SecureStore && typeof SecureStore.getItemAsync === 'function') {
    try {
      const val = await SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
      if (val !== null && val !== undefined) return val;
    } catch (_e) {
      // Fall back to AsyncStorage
    }
  }
  return AsyncStorage.getItem(REFRESH_TOKEN_KEY);
}

export async function removeRefreshToken() {
  if (SecureStore && typeof SecureStore.deleteItemAsync === 'function') {
    try {
      await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
    } catch (_e) {
      // Fall back
    }
  }
  await AsyncStorage.removeItem(REFRESH_TOKEN_KEY);
}

export async function clearTokens() {
  await Promise.all([removeToken(), removeRefreshToken()]);
}
