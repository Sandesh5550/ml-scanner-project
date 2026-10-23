import React, { createContext, useContext, useEffect, useState, useRef } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { getToken, removeToken, saveToken } from '../utils/storage';
import { setUnauthorizedHandler, clearAuthSession } from '../services/api';
import { resetToLogin } from '../utils/navigationRef';

const AuthContext = createContext();

export function AuthProvider({ children }) {
  const [isLoggedIn, setIsLoggedIn] = useState(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const logoutInProgressRef = useRef(false);

  useEffect(() => {
    getToken()
      .then((token) => setIsLoggedIn(!!token))
      .catch(() => setIsLoggedIn(false));

    setUnauthorizedHandler(() => {
      clearAuthSession();
      setIsLoggedIn(false);
      resetToLogin();
    });

    return () => {
      setUnauthorizedHandler(null);
    };
  }, []);

  const login = async (token) => {
    if (!token) return;
    await saveToken(token);
    setIsLoggedIn(true);
  };

  const logout = async () => {
    if (logoutInProgressRef.current) return;
    logoutInProgressRef.current = true;
    setIsLoggingOut(true);

    try {
      await removeToken();
      clearAuthSession();
      await AsyncStorage.clear();
    } catch (_e) {
      // ignore
    } finally {
      setIsLoggedIn(false);
      setIsLoggingOut(false);
      logoutInProgressRef.current = false;
      resetToLogin();
    }
  };

  return (
    <AuthContext.Provider value={{ isLoggedIn, isLoggingOut, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
