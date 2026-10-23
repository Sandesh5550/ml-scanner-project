/**
 * Pure Base URL Resolver for FoodScanner AI Mobile/Web Client.
 *
 * Resolves the backend base URL according to the strict precedence hierarchy:
 * 1. Explicit env override (EXPO_PUBLIC_API_BASE_URL)
 * 2. Web browser origin (window.location.hostname:PORT or localhost:PORT)
 * 3. Native Expo LAN discovery (expoHostIp:PORT)
 * 4. Production public fallback (DEFAULT_PUBLIC_URL)
 */

export const DEFAULT_PUBLIC_URL = 'https://foodscanner-ai.onrender.com';
export const API_PORT = '8000';

export function normalizeBaseUrl(url) {
  if (!url || typeof url !== 'string') return null;
  const trimmed = url.trim().replace(/\/+$/, '');
  return trimmed || null;
}

export function extractHost(value) {
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

export function getExpoHostIp(constantsObj = null) {
  try {
    const c = constantsObj;
    if (!c || typeof c !== 'object') return null;

    const candidates = [
      c.expoConfig?.hostUri,
      c.expoGoConfig?.debuggerHost,
      c.manifest?.debuggerHost,
      c.manifest?.hostUri,
      c.manifest2?.extra?.expoClient?.hostUri,
      c.manifest2?.extra?.expoClient?.debuggerHost,
      c.linkingUri,
      c.experienceUrl,
    ];

    for (const candidate of candidates) {
      const host = extractHost(candidate);
      if (host && host !== 'localhost' && host !== '127.0.0.1') {
        return host;
      }
    }
  } catch (_e) {
    // Fall through
  }
  return null;
}

export function resolveBaseUrl({
  rawEnvUrl = null,
  platform = 'web',
  windowHostname = null,
  expoHostIp = null,
  defaultPublicUrl = DEFAULT_PUBLIC_URL,
  port = API_PORT,
} = {}) {
  const envBaseUrl = normalizeBaseUrl(rawEnvUrl);
  if (envBaseUrl) {
    return envBaseUrl;
  }

  if (platform === 'web') {
    const host = (windowHostname && typeof windowHostname === 'string' && windowHostname.trim())
      ? windowHostname.trim()
      : 'localhost';
    return `http://${host}:${port}`;
  }

  // Native mobile platforms (ios / android)
  const nativeHost = (expoHostIp && typeof expoHostIp === 'string' && expoHostIp.trim())
    ? expoHostIp.trim()
    : null;

  if (nativeHost) {
    return `http://${nativeHost}:${port}`;
  }

  return defaultPublicUrl;
}
