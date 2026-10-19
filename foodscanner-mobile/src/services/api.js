import axios from 'axios';
import { Platform } from 'react-native';
import Constants from 'expo-constants';
import { getToken, removeToken } from '../utils/storage';
import { resetToLogin } from '../utils/navigationRef';

const DEFAULT_PUBLIC_URL = 'https://foodscanner-ai.onrender.com';
const API_PORT = '8000';

function normalizeBaseUrl(url) {
  if (!url || typeof url !== 'string') return null;
  return url.trim().replace(/\/+$/, '');
}

function extractHost(value) {
  if (!value || typeof value !== 'string') return null;

  const rawValue = value.trim();
  if (!rawValue) return null;

  try {
    const withProtocol = rawValue.includes('://') ? rawValue : `http://${rawValue}`;
    const parsed = new URL(withProtocol);
    return parsed.hostname || null;
  } catch (_e) {
    const host = rawValue.split('/')[0].split(':')[0];
    return host || null;
  }
}

function getExpoHostIp() {
  try {
    const candidates = [
      Constants?.expoConfig?.hostUri,
      Constants?.expoGoConfig?.debuggerHost,
      Constants?.manifest?.debuggerHost,
      Constants?.manifest?.hostUri,
      Constants?.manifest2?.extra?.expoClient?.hostUri,
      Constants?.manifest2?.extra?.expoClient?.debuggerHost,
      Constants?.linkingUri,
      Constants?.experienceUrl,
    ];

    for (const candidate of candidates) {
      const host = extractHost(candidate);
      if (host && host !== 'localhost' && host !== '127.0.0.1') {
        return host;
      }
    }
  } catch (_e) {
    // Fall through to default URL
  }
  return null;
}

const envBaseUrl = normalizeBaseUrl(process.env.EXPO_PUBLIC_API_BASE_URL);
const expoHostIp = getExpoHostIp();
const expoDerivedUrl = expoHostIp ? `http://${expoHostIp}:${API_PORT}` : null;
const defaultWebUrl = typeof window !== 'undefined' && window?.location?.hostname
  ? `http://${window.location.hostname}:${API_PORT}`
  : `http://localhost:${API_PORT}`;

export const BASE_URL =
  envBaseUrl ||
  (Platform.OS === 'web'
    ? defaultWebUrl
    : defaultNativeUrl);

console.log('API base URL:', BASE_URL);

let _unauthorizedHandler = null;

export function setUnauthorizedHandler(handler) {
  _unauthorizedHandler = handler;
}

export function getApiBaseUrl() {
  return BASE_URL;
}

export function getNetworkErrorMessage(error) {
  if (error?.response) {
    const detail = error.response.data?.detail;
    if (typeof detail === 'string' && detail.trim()) {
      return detail.trim();
    }
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((d) => (typeof d === 'object' && d?.msg ? d.msg : String(d)))
        .join('. ');
    }
    if (detail && typeof detail === 'object') {
      return detail.msg || detail.message || JSON.stringify(detail);
    }
    return error.message || 'Request failed';
  }

  if (error?.request || error?.message === 'Network Error') {
    return `Cannot reach backend at ${BASE_URL}. Please check your internet connection and try again.`;
  }

  return error?.message || 'Request failed';
}

export function clearAuthSession() {
  if (client.defaults.headers && client.defaults.headers.common) {
    delete client.defaults.headers.common['Authorization'];
  }
}

const client = axios.create({
  baseURL: BASE_URL,
  timeout: 60000,
});

client.interceptors.request.use(async (config) => {
  const token = await getToken();
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  } else if (config.headers && config.headers.Authorization) {
    delete config.headers.Authorization;
  }
  return config;
});

let _isHandling401 = false;

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const status = error?.response?.status;
    const requestUrl = error?.config?.url || '';

    // Bypass 401 interceptor for authentication attempt endpoints (/login, /register)
    const isAuthEndpoint = requestUrl.includes('/login') || requestUrl.includes('/register');

    if (status === 401 && !isAuthEndpoint) {
      if (!_isHandling401) {
        _isHandling401 = true;
        try {
          await removeToken();
          clearAuthSession();
        } catch (_e) {
          // ignore
        }

        if (typeof _unauthorizedHandler === 'function') {
          try {
            _unauthorizedHandler();
          } catch (_e) {
            // ignore
          }
        } else {
          try {
            resetToLogin();
          } catch (_e) {
            // ignore
          }
        }

        setTimeout(() => {
          _isHandling401 = false;
        }, 1000);
      }
    }
    return Promise.reject(error);
  }
);

export async function login(email, password) {
  const res = await client.post('/login', { email, password });
  return res.data;
}

export async function register(email, password, name) {
  const res = await client.post('/register', { email, password, name });
  return res.data;
}

export async function pingApi() {
  const res = await client.get('/docs');
  return res.status;
}

export async function scanProduct(barcode, productName, claims = null) {
  const payload = { barcode, product_name: productName || null };
  if (Array.isArray(claims) && claims.length > 0) {
    payload.claims = claims;
  }
  const res = await client.post('/scan', payload);
  return res.data;
}

export async function searchProduct(query) {
  const res = await client.get('/search', { params: { query } });
  return res.data;
}

export async function getDailyReport() {
  const res = await client.get('/report/daily');
  return res.data;
}

export async function getWeeklyReport() {
  const res = await client.get('/report/weekly');
  return res.data;
}

export async function explainProduct(barcode) {
  const res = await client.get(`/explain/${encodeURIComponent(barcode)}`);
  return res.data;
}

export async function analyzeManualProduct(data) {
  const res = await client.post('/analyze', data);
  return res.data;
}

export async function scanNutritionLabel(base64Image) {
  const res = await client.post('/ocr', { image_base64: base64Image });
  return res.data;
}

export async function logFoodItem(data) {
  const res = await client.post('/food-log', data);
  return res.data;
}

export async function deleteFoodLogItem(logId) {
  const res = await client.delete(`/food-log/${logId}`);
  return res.data;
}

export async function updateFoodLogItem(logId, data) {
  const res = await client.put(`/food-log/${logId}`, data);
  return res.data;
}

export const logFoodManual = async (productName, calories) => {
  const res = await client.post('/food-log', {
    product_name: productName,
    calories: parseFloat(calories) || 0,
  });
  return res.data;
};


export async function getUserProfile() {
  const res = await client.get('/user/profile');
  return res.data;
}

export async function updateUserProfile(data) {
  const res = await client.put('/user/profile', data);
  return res.data;
}

export async function getHistory() {
  const res = await client.get('/history');
  return res.data;
}

export async function getTodayFoods() {
  const res = await client.get('/today');
  return res.data;
}

export async function verifyClaims({ claims, barcode, nutrition, ingredients, productName } = {}) {
  const payload = {
    claims: Array.isArray(claims) ? claims : [],
    barcode: barcode || null,
    nutrition: nutrition || null,
    ingredients: ingredients || null,
    product_name: productName || null,
  };
  const res = await client.post('/verify-claims', payload);
  return res.data;
}

export async function getHealthierAlternatives({ productName, barcode, nutrition, limit = 3 } = {}) {
  const payload = {
    product_name: productName || null,
    barcode: barcode || null,
    nutrition: nutrition || null,
    limit: Number(limit) || 3,
  };
  const res = await client.post('/alternatives', payload);
  return res.data;
}

export async function getAlternativesByBarcode(barcode) {
  const res = await client.get(`/alternatives/${encodeURIComponent(barcode)}`);
  return res.data;
}

export async function compareProducts(productA, productB) {
  const res = await client.post('/compare', {
    product_a: String(productA || '').trim(),
    product_b: String(productB || '').trim(),
  });
  return res.data;
}

export async function askNutritionAssistant({ message, barcode, productContext, conversationHistory } = {}) {
  const payload = {
    message: String(message || '').trim(),
    barcode: barcode ? String(barcode).trim() : null,
    product_context: productContext || null,
    conversation_history: Array.isArray(conversationHistory) ? conversationHistory : null,
  };
  const res = await client.post('/chat', payload);
  return res.data;
}

export async function getGoalReport() {
  const res = await client.get('/report/goal');
  return res.data;
}

export async function getUserStats() {
  const res = await client.get('/stats');
  return res.data;
}

export async function deleteHistoryItem(scanId) {
  const res = await client.delete(`/history/${encodeURIComponent(scanId)}`);
  return res.data;
}

