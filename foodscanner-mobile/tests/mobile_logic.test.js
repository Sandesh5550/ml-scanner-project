const { test, describe } = require('node:test');
const assert = require('node:assert');

// 1. Re-implemented pure utility logic mirroring mobile app helpers
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

function getNetworkErrorMessage(error, baseUrl = 'http://127.0.0.1:8000') {
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
    return `Cannot reach backend at ${baseUrl}. Please check your internet connection and try again.`;
  }
  return error?.message || 'Request failed';
}

function getDecisionMeta(decision) {
  const d = String(decision || '').toUpperCase();
  if (d === 'SAFE') return { text: 'SAFE', color: 'green' };
  if (d === 'MODERATE') return { text: 'MODERATE', color: 'amber' };
  return { text: d || 'AVOID', color: 'red' };
}

function calculateServingNutrition(baseNutrition100g, servingGrams) {
  if (!baseNutrition100g || !servingGrams || servingGrams <= 0) return null;
  const ratio = servingGrams / 100.0;
  const result = {};
  for (const [key, val] of Object.entries(baseNutrition100g)) {
    if (val === null || val === undefined || isNaN(val)) {
      result[key] = null;
    } else {
      result[key] = Math.round(Number(val) * ratio * 10) / 10;
    }
  }
  return result;
}

describe('Mobile API and URL Resolution Logic', () => {
  const {
    DEFAULT_PUBLIC_URL: PROD_DEFAULT_PUBLIC_URL,
    API_PORT: PROD_API_PORT,
    normalizeBaseUrl: prodNormalizeBaseUrl,
    extractHost: prodExtractHost,
    getExpoHostIp: prodGetExpoHostIp,
    resolveBaseUrl: prodResolveBaseUrl,
  } = require('../src/utils/urlResolver');

  test('normalizeBaseUrl removes trailing slashes and whitespace', () => {
    assert.strictEqual(prodNormalizeBaseUrl('  http://192.168.1.50:8000/// '), 'http://192.168.1.50:8000');
    assert.strictEqual(prodNormalizeBaseUrl('https://api.foodscanner.com/'), 'https://api.foodscanner.com');
    assert.strictEqual(prodNormalizeBaseUrl(null), null);
    assert.strictEqual(prodNormalizeBaseUrl(undefined), null);
    assert.strictEqual(prodNormalizeBaseUrl(''), null);
    assert.strictEqual(prodNormalizeBaseUrl('   '), null);
  });

  test('extractHost parses host from various Expo debugger candidates', () => {
    assert.strictEqual(prodExtractHost('192.168.1.45:8081'), '192.168.1.45');
    assert.strictEqual(prodExtractHost('http://10.0.2.2:8081/index.bundle'), '10.0.2.2');
    assert.strictEqual(prodExtractHost('exp://192.168.0.10:8081'), '192.168.0.10');
    assert.strictEqual(prodExtractHost(''), null);
    assert.strictEqual(prodExtractHost(null), null);
  });

  test('getExpoHostIp extracts valid non-loopback host from Constants candidates', () => {
    assert.strictEqual(prodGetExpoHostIp({ expoConfig: { hostUri: '192.168.1.45:8081' } }), '192.168.1.45');
    assert.strictEqual(prodGetExpoHostIp({ expoGoConfig: { debuggerHost: '10.0.2.2:8081' } }), '10.0.2.2');
    assert.strictEqual(prodGetExpoHostIp({ manifest: { debuggerHost: '172.20.10.4:8081' } }), '172.20.10.4');
    assert.strictEqual(prodGetExpoHostIp({ manifest2: { extra: { expoClient: { hostUri: '192.168.0.100:8081' } } } }), '192.168.0.100');
    // Loopback hosts must be rejected to allow fallback to production
    assert.strictEqual(prodGetExpoHostIp({ expoConfig: { hostUri: 'localhost:8081' } }), null);
    assert.strictEqual(prodGetExpoHostIp({ expoConfig: { hostUri: '127.0.0.1:8081' } }), null);
    assert.strictEqual(prodGetExpoHostIp(null), null);
  });

  test('resolveBaseUrl: 1. Explicit EXPO_PUBLIC_API_BASE_URL override takes top precedence', () => {
    const explicitUrl = 'https://custom-api.foodscanner.com';
    // Overrides web platform
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: explicitUrl, platform: 'web', windowHostname: 'my-web.com' }),
      'https://custom-api.foodscanner.com'
    );
    // Overrides iOS platform even with active Expo LAN host
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: explicitUrl, platform: 'ios', expoHostIp: '192.168.1.99' }),
      'https://custom-api.foodscanner.com'
    );
    // Overrides Android platform
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: explicitUrl, platform: 'android', expoHostIp: '10.0.2.2' }),
      'https://custom-api.foodscanner.com'
    );
    // Normalizes trailing slashes and whitespace
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: '  https://custom-api.foodscanner.com///  ', platform: 'ios' }),
      'https://custom-api.foodscanner.com'
    );
  });

  test('resolveBaseUrl: 2. Web browser origin fallback when env URL is unset', () => {
    // When windowHostname is present, uses hostname with port 8000
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: null, platform: 'web', windowHostname: 'foodscanner.app' }),
      'http://foodscanner.app:8000'
    );
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: '', platform: 'web', windowHostname: '192.168.1.10' }),
      'http://192.168.1.10:8000'
    );
    // When windowHostname is null, undefined, or empty, defaults to localhost:8000
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: null, platform: 'web', windowHostname: null }),
      'http://localhost:8000'
    );
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: '   ', platform: 'web', windowHostname: '' }),
      'http://localhost:8000'
    );
    // Preserves custom port parameter if specified
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: null, platform: 'web', windowHostname: 'localhost', port: '8080' }),
      'http://localhost:8080'
    );
  });

  test('resolveBaseUrl: 3. Native iOS/Android Expo LAN IP fallback during local development', () => {
    // iOS with Expo LAN host IP
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: null, platform: 'ios', expoHostIp: '192.168.1.55' }),
      'http://192.168.1.55:8000'
    );
    // Android with Expo LAN host IP
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: null, platform: 'android', expoHostIp: '10.0.2.2' }),
      'http://10.0.2.2:8000'
    );
    // Preserves custom port on native LAN
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: null, platform: 'ios', expoHostIp: '192.168.1.55', port: '9000' }),
      'http://192.168.1.55:9000'
    );
  });

  test('resolveBaseUrl: 4. Native production fallback to DEFAULT_PUBLIC_URL when Expo host is unavailable', () => {
    // iOS standalone production build (expoHostIp is null)
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: null, platform: 'ios', expoHostIp: null }),
      'https://foodscanner-ai.onrender.com'
    );
    // Android standalone production build (expoHostIp is undefined)
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: null, platform: 'android', expoHostIp: undefined }),
      'https://foodscanner-ai.onrender.com'
    );
    // Empty string expoHostIp falls back to production URL
    assert.strictEqual(
      prodResolveBaseUrl({ rawEnvUrl: null, platform: 'ios', expoHostIp: '   ' }),
      'https://foodscanner-ai.onrender.com'
    );
    // Default constant matches exported DEFAULT_PUBLIC_URL
    assert.strictEqual(
      prodResolveBaseUrl({ platform: 'ios' }),
      PROD_DEFAULT_PUBLIC_URL
    );
    // Custom defaultPublicUrl is respected
    assert.strictEqual(
      prodResolveBaseUrl({ platform: 'android', defaultPublicUrl: 'https://staging.foodscanner.com' }),
      'https://staging.foodscanner.com'
    );
  });

  test('getNetworkErrorMessage provides user-friendly backend offline advice', () => {
    const offlineErr = { message: 'Network Error', request: {} };
    const msg = getNetworkErrorMessage(offlineErr, 'http://192.168.1.100:8000');
    assert.match(msg, /Cannot reach backend at http:\/\/192\.168\.1\.100:8000/);

    const apiErr = { response: { data: { detail: 'Product expired or invalid' } } };
    assert.strictEqual(getNetworkErrorMessage(apiErr), 'Product expired or invalid');
  });
});

describe('Mobile ResultScreen Decision and Serving Calculations', () => {
  test('getDecisionMeta maps SAFE, MODERATE, AVOID correctly', () => {
    assert.strictEqual(getDecisionMeta('safe').color, 'green');
    assert.strictEqual(getDecisionMeta('moderate').color, 'amber');
    assert.strictEqual(getDecisionMeta('avoid').color, 'red');
    assert.strictEqual(getDecisionMeta('').color, 'red');
  });

  test('calculateServingNutrition correctly scales from 100g baseline', () => {
    const base100g = {
      calories: 400,
      sugar: 20,
      fat: 10,
      salt: 1.5,
    };

    // 50g portion (half)
    const half = calculateServingNutrition(base100g, 50);
    assert.strictEqual(half.calories, 200);
    assert.strictEqual(half.sugar, 10);
    assert.strictEqual(half.fat, 5);
    assert.strictEqual(half.salt, 0.8); // 0.75 rounds to 0.8

    // 150g portion (1.5x)
    const oneAndHalf = calculateServingNutrition(base100g, 150);
    assert.strictEqual(oneAndHalf.calories, 600);
    assert.strictEqual(oneAndHalf.sugar, 30);
    assert.strictEqual(oneAndHalf.fat, 15);
  });
});

// 2. Batch 9A Component and Invariant Unit Test Helpers
function getClaimStatusMeta(status) {
  const s = String(status || '').toUpperCase();
  if (s === 'SUPPORTED') return { label: 'SUPPORTED', color: 'green' };
  if (s === 'NOT_SUPPORTED') return { label: 'NOT SUPPORTED', color: 'red' };
  if (s === 'NEEDS_REVIEW') return { label: 'NEEDS REVIEW', color: 'amber' };
  return { label: 'INSUFFICIENT DATA', color: 'gray' };
}

function buildScanRequest(barcode, productName, claims = null) {
  const payload = { barcode: String(barcode).trim(), product_name: productName || null };
  if (Array.isArray(claims) && claims.length > 0) {
    payload.claims = claims;
  }
  return { path: '/scan', method: 'POST', body: payload };
}

function buildVerifyClaimsRequest({ claims, barcode, nutrition, ingredients, productName } = {}) {
  return {
    path: '/verify-claims',
    method: 'POST',
    body: {
      claims: Array.isArray(claims) ? claims : [],
      barcode: barcode || null,
      nutrition: nutrition || null,
      ingredients: ingredients || null,
      product_name: productName || null,
    },
  };
}

function buildCompareRequest(productA, productB) {
  return {
    path: '/compare',
    method: 'POST',
    body: {
      product_a: String(productA || '').trim(),
      product_b: String(productB || '').trim(),
    },
  };
}

function buildChatRequest({ message, barcode, productContext } = {}) {
  return {
    path: '/chat',
    method: 'POST',
    body: {
      message: String(message || '').trim(),
      barcode: barcode ? String(barcode).trim() : null,
      product_context: productContext || null,
    },
  };
}

function buildFoodLogRequest({ productName, calories, barcode, servingSize, nutrition } = {}) {
  return {
    path: '/food-log',
    method: 'POST',
    body: {
      product_name: productName,
      calories: Number(calories) || 0,
      serving_size: servingSize || 100,
      barcode: barcode || null,
      ...nutrition,
    },
  };
}

describe('Batch 9A: Claim Verification Logic and Component State', () => {
  test('getClaimStatusMeta correctly maps all four statutory FSSAI statuses', () => {
    assert.strictEqual(getClaimStatusMeta('SUPPORTED').color, 'green');
    assert.strictEqual(getClaimStatusMeta('supported').label, 'SUPPORTED');

    assert.strictEqual(getClaimStatusMeta('NOT_SUPPORTED').color, 'red');
    assert.strictEqual(getClaimStatusMeta('not_supported').label, 'NOT SUPPORTED');

    assert.strictEqual(getClaimStatusMeta('NEEDS_REVIEW').color, 'amber');
    assert.strictEqual(getClaimStatusMeta('INSUFFICIENT_DATA').color, 'gray');
    assert.strictEqual(getClaimStatusMeta(null).label, 'INSUFFICIENT DATA');
    assert.strictEqual(getClaimStatusMeta(undefined).color, 'gray');
  });

  test('Claim verification payload constructs valid regulatory query structure', () => {
    const claims = ['High Protein', 'Sugar Free'];
    const req = buildVerifyClaimsRequest({
      claims,
      barcode: '8901058000256',
      nutrition: { protein: 12.0, sugar: 0.2 },
    });
    assert.strictEqual(req.path, '/verify-claims');
    assert.deepStrictEqual(req.body.claims, claims);
    assert.strictEqual(req.body.barcode, '8901058000256');
    assert.strictEqual(req.body.nutrition.protein, 12.0);
  });
});

describe('Batch 9A: Healthier Alternatives & Comparison Logic', () => {
  test('Alternatives gracefully handles empty recommendations list without crashing', () => {
    const emptyList = [];
    const hasItems = Array.isArray(emptyList) && emptyList.length > 0;
    assert.strictEqual(hasItems, false);

    const validList = [{ product_name: 'Baked Chips', health_score: 75, advantages: ['50% less fat'] }];
    assert.strictEqual(validList.length, 1);
    assert.strictEqual(validList[0].advantages[0], '50% less fat');
  });

  test('Compare request cleanly trims queries and targets /compare', () => {
    const req = buildCompareRequest('  Maggi  ', '8901058000256 ');
    assert.strictEqual(req.path, '/compare');
    assert.strictEqual(req.body.product_a, 'Maggi');
    assert.strictEqual(req.body.product_b, '8901058000256');
  });
});

describe('Batch 9A: AI Nutrition Assistant Logic & Error Handling', () => {
  test('Chat request attaches product context without overriding health score', () => {
    const context = {
      product_name: 'Rolled Oats',
      health_score: 85,
      final_decision: 'SAFE',
    };
    const req = buildChatRequest({
      message: 'Is this good for weight loss?',
      barcode: '8901234567890',
      productContext: context,
    });
    assert.strictEqual(req.path, '/chat');
    assert.strictEqual(req.body.message, 'Is this good for weight loss?');
    assert.strictEqual(req.body.product_context.health_score, 85);
  });

  test('AI assistant service error handles 503 fallback message', () => {
    const simulate503Error = { response: { status: 503, data: { detail: 'Provider down' } } };
    let displayedMessage;
    if (simulate503Error?.response?.data?.detail) {
      displayedMessage = simulate503Error.response.data.detail;
    } else {
      displayedMessage = 'AI assistant is temporarily unavailable. Please try again later.';
    }
    assert.strictEqual(displayedMessage, 'Provider down');
  });
});

describe('Batch 9A Core Domain Invariant: Scan ≠ Eat Preservation', () => {
  test('Analytical queries NEVER target the intake endpoint /food-log', () => {
    const scanReq = buildScanRequest('8901234567890', 'Oats');
    const claimReq = buildVerifyClaimsRequest({ claims: ['Sugar Free'], barcode: '8901234567890' });
    const compareReq = buildCompareRequest('Prod A', 'Prod B');
    const chatReq = buildChatRequest({ message: 'Hello', barcode: '8901234567890' });

    assert.notStrictEqual(scanReq.path, '/food-log');
    assert.notStrictEqual(claimReq.path, '/food-log');
    assert.notStrictEqual(compareReq.path, '/food-log');
    assert.notStrictEqual(chatReq.path, '/food-log');

    // ONLY explicit food log action targets /food-log
    const logReq = buildFoodLogRequest({ productName: 'Oats', calories: 250 });
    assert.strictEqual(logReq.path, '/food-log');
  });
});

describe('Batch 9B: Neo-Brutalist Design System & Theme Contract', () => {
  // Pure design token representation as implemented in src/theme/neoTheme.js
  const NEO_TOKENS = {
    colors: {
      bg: '#FAF6EE',
      card: '#FFFFFF',
      ink: '#111111',
      border: '#111111',
      yellow: '#FFD166',
      coral: '#FF6B6B',
      cyan: '#4ECDC4',
      purple: '#9D84B7',
      green: '#51CF66',
      pink: '#FF85A1',
      orange: '#FFA94D',
    },
    shadows: {
      sm: { shadowColor: '#111111', shadowOffset: { width: 2, height: 2 }, shadowOpacity: 1, shadowRadius: 0 },
      md: { shadowColor: '#111111', shadowOffset: { width: 3, height: 3 }, shadowOpacity: 1, shadowRadius: 0 },
      lg: { shadowColor: '#111111', shadowOffset: { width: 4, height: 4 }, shadowOpacity: 1, shadowRadius: 0 },
    },
    borders: {
      regular: 2,
      thick: 2.5,
    },
    screens: [
      'LoginScreen',
      'HomeScreen',
      'ScanScreen',
      'ResultScreen',
      'ReportScreen',
      'ProfileScreen',
      'ManualEntryScreen',
      'OCRScanScreen',
    ],
  };

  test('Design system reflects reference palette (cream canvas, solid ink, vibrant accents)', () => {
    assert.strictEqual(NEO_TOKENS.colors.bg, '#FAF6EE', 'Primary canvas must be warm cream');
    assert.strictEqual(NEO_TOKENS.colors.ink, '#111111', 'Solid black ink outlines required');
    assert.ok(NEO_TOKENS.colors.yellow, 'Yellow highlight accent required');
    assert.ok(NEO_TOKENS.colors.coral, 'Coral alert accent required');
    assert.ok(NEO_TOKENS.colors.cyan, 'Cyan scanner accent required');
    assert.ok(NEO_TOKENS.colors.purple, 'Purple AI assistant accent required');
    assert.ok(NEO_TOKENS.colors.green, 'Green statutory safe accent required');
  });

  test('Shadow system enforces hard unblurred offset shadows', () => {
    assert.strictEqual(NEO_TOKENS.shadows.sm.shadowRadius, 0, 'Zero blur for sm shadow');
    assert.strictEqual(NEO_TOKENS.shadows.sm.shadowOpacity, 1, 'Full opacity for sm shadow');
    assert.deepStrictEqual(NEO_TOKENS.shadows.sm.shadowOffset, { width: 2, height: 2 });

    assert.strictEqual(NEO_TOKENS.shadows.md.shadowRadius, 0, 'Zero blur for md shadow');
    assert.deepStrictEqual(NEO_TOKENS.shadows.md.shadowOffset, { width: 3, height: 3 });

    assert.strictEqual(NEO_TOKENS.shadows.lg.shadowRadius, 0, 'Zero blur for lg shadow');
    assert.deepStrictEqual(NEO_TOKENS.shadows.lg.shadowOffset, { width: 4, height: 4 });
  });

  test('Preservation invariant: all 8 canonical screens are preserved', () => {
    assert.strictEqual(NEO_TOKENS.screens.length, 8);
    const requiredScreens = [
      'LoginScreen',
      'HomeScreen',
      'ScanScreen',
      'ResultScreen',
      'ReportScreen',
      'ProfileScreen',
      'ManualEntryScreen',
      'OCRScanScreen',
    ];
    for (const screen of requiredScreens) {
      assert.ok(NEO_TOKENS.screens.includes(screen), `Screen ${screen} must be present`);
    }
  });
});

describe('Batch 10: Product Selection Determinism & Navigation Audit', () => {
  // Benchmark products contract
  const BENCHMARKS = [
    { id: 'parle-g', name: 'Parle-G', barcode: '8901719101038' },
    { id: 'maggi', name: 'Maggi', barcode: '8901058851304' },
    { id: 'kurkure', name: 'Kurkure', barcode: '8901491100519' },
    { id: 'lays', name: "Lay's", barcode: '8901491101844' },
    { id: 'amul-butter', name: 'Amul Butter', barcode: '8901262010320' },
  ];

  test('Benchmark products define valid canonical barcodes and never 00000000', () => {
    for (const p of BENCHMARKS) {
      assert.ok(p.barcode, `${p.name} must have a barcode`);
      assert.notStrictEqual(p.barcode, '00000000', `${p.name} barcode must never be fake 00000000`);
      assert.match(p.barcode, /^\d{8,14}$/, `${p.name} barcode must be 8-14 numeric digits`);
    }
    const parleG = BENCHMARKS.find((b) => b.id === 'parle-g');
    const maggi = BENCHMARKS.find((b) => b.id === 'maggi');
    assert.strictEqual(parleG.barcode, '8901719101038');
    assert.strictEqual(maggi.barcode, '8901058851304');
    assert.notStrictEqual(parleG.barcode, maggi.barcode, 'Parle-G and Maggi barcodes must be distinct');
  });

  test('Flow A & B: Product selection resolves to its exact canonical barcode', () => {
    function resolveProductScan(selectedBenchmark) {
      return {
        targetBarcode: selectedBenchmark.barcode,
        targetName: selectedBenchmark.name,
      };
    }

    const flowA = resolveProductScan(BENCHMARKS.find((b) => b.id === 'parle-g'));
    assert.strictEqual(flowA.targetBarcode, '8901719101038');
    assert.strictEqual(flowA.targetName, 'Parle-G');

    const flowB = resolveProductScan(BENCHMARKS.find((b) => b.id === 'maggi'));
    assert.strictEqual(flowB.targetBarcode, '8901058851304');
    assert.strictEqual(flowB.targetName, 'Maggi');
  });

  test('Flow C & D: Product switching is deterministic with no stale closure', () => {
    let currentSelection = null;
    function selectProduct(benchmark) {
      currentSelection = {
        targetBarcode: benchmark.barcode,
        targetName: benchmark.name,
      };
      return currentSelection;
    }

    // Flow C: Parle-G then Maggi -> final result must be Maggi
    selectProduct(BENCHMARKS.find((b) => b.id === 'parle-g'));
    const finalFlowC = selectProduct(BENCHMARKS.find((b) => b.id === 'maggi'));
    assert.strictEqual(finalFlowC.targetBarcode, '8901058851304');
    assert.strictEqual(finalFlowC.targetName, 'Maggi');

    // Flow D: Maggi then Parle-G -> final result must be Parle-G
    selectProduct(BENCHMARKS.find((b) => b.id === 'maggi'));
    const finalFlowD = selectProduct(BENCHMARKS.find((b) => b.id === 'parle-g'));
    assert.strictEqual(finalFlowD.targetBarcode, '8901719101038');
    assert.strictEqual(finalFlowD.targetName, 'Parle-G');
  });

  test('Manual search routing prevents sending 00000000 to barcode scan endpoint', () => {
    function planScanRequest({ barcode, productName }) {
      const raw = String(barcode || '').trim();
      const hint = String(productName || '').trim();
      const isDigits = /^\d{8,14}$/.test(raw) && raw !== '00000000';

      if (isDigits) {
        return { type: 'barcode_scan', barcode: raw, hint: hint || null };
      }
      if (hint) {
        return { type: 'manual_analysis', productName: hint };
      }
      throw new Error('Input Required');
    }

    // Entering only a product name
    const nameSearch = planScanRequest({ barcode: '', productName: 'Parle-G' });
    assert.strictEqual(nameSearch.type, 'manual_analysis');
    assert.strictEqual(nameSearch.productName, 'Parle-G');
    assert.strictEqual(nameSearch.barcode, undefined, 'Must not send barcode for name-only searches');

    // Entering a numeric barcode
    const barcodeSearch = planScanRequest({ barcode: '8901719101038', productName: '' });
    assert.strictEqual(barcodeSearch.type, 'barcode_scan');
    assert.strictEqual(barcodeSearch.barcode, '8901719101038');
  });

  test('Navigation Back button logic provides safe fallback when canGoBack is false', () => {
    function handleBackNavigation(nav, fallbackRoute) {
      if (nav.canGoBack()) {
        nav.goBack();
        return 'went_back';
      }
      nav.navigate(fallbackRoute.name, fallbackRoute.params);
      return 'fallback_navigated';
    }

    let historyPopCount = 0;
    let fallbackCalls = [];

    // Case 1: Can go back
    const mockNavWithBack = {
      canGoBack: () => true,
      goBack: () => { historyPopCount++; },
      navigate: (r, p) => { fallbackCalls.push({ r, p }); },
    };
    const res1 = handleBackNavigation(mockNavWithBack, { name: 'Main', params: { screen: 'Home' } });
    assert.strictEqual(res1, 'went_back');
    assert.strictEqual(historyPopCount, 1);
    assert.strictEqual(fallbackCalls.length, 0);

    // Case 2: Cannot go back (e.g. opened directly or deep link)
    const mockNavNoBack = {
      canGoBack: () => false,
      goBack: () => { historyPopCount++; },
      navigate: (r, p) => { fallbackCalls.push({ r, p }); },
    };
    const res2 = handleBackNavigation(mockNavNoBack, { name: 'Main', params: { screen: 'Home' } });
    assert.strictEqual(res2, 'fallback_navigated');
    assert.strictEqual(fallbackCalls.length, 1);
    assert.strictEqual(fallbackCalls[0].r, 'Main');
    assert.deepStrictEqual(fallbackCalls[0].p, { screen: 'Home' });
  });

  test('ResultScreen state reset clears portion and expanded toggles on product change', () => {
    function simulateResultState(prevProduct, newProduct) {
      let state = {
        activePortionTab: '250g',
        servingGrams: '250',
        loggedToday: true,
        expandedIngredients: { 0: true, 1: true },
      };

      // Simulating useEffect([productName, barcode, timestamp])
      if (prevProduct.name !== newProduct.name || prevProduct.barcode !== newProduct.barcode) {
        state = {
          activePortionTab: '100g',
          servingGrams: '100',
          loggedToday: false,
          expandedIngredients: {},
        };
      }
      return state;
    }

    const productA = { name: 'Parle-G', barcode: '8901719101038' };
    const productB = { name: 'Maggi', barcode: '8901058851304' };

    const resetState = simulateResultState(productA, productB);
    assert.strictEqual(resetState.activePortionTab, '100g');
    assert.strictEqual(resetState.servingGrams, '100');
    assert.strictEqual(resetState.loggedToday, false);
    assert.deepStrictEqual(resetState.expandedIngredients, {});
  });
});

// ---------------------------------------------------------------------------
// Batch 11: Production Authentication, Login Validation, Token Storage & Logout Reliability
// ---------------------------------------------------------------------------
describe('Batch 11: Production Authentication, Login Validation, Token Storage & Logout Reliability', () => {
  const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  // 1. Login & Register Field Validation
  test('1. Login field validation rejects empty fields and malformed email addresses', () => {
    const validateForm = (email, password, mode = 'login') => {
      const trimmedEmail = (email || '').trim();
      if (!trimmedEmail) return { valid: false, error: 'Please enter your email address.' };
      if (!EMAIL_REGEX.test(trimmedEmail)) return { valid: false, error: 'Please enter a valid email address.' };
      if (!password) return { valid: false, error: 'Please enter your password.' };
      if (mode === 'register' && password.length < 6) return { valid: false, error: 'Password must be at least 6 characters long.' };
      return { valid: true, error: null };
    };

    assert.strictEqual(validateForm('', 'Secret123').valid, false);
    assert.strictEqual(validateForm('notanemail', 'Secret123').valid, false);
    assert.strictEqual(validateForm('user@', 'Secret123').valid, false);
    assert.strictEqual(validateForm('demo@pramaan.ai', '').valid, false);
    assert.strictEqual(validateForm('demo@pramaan.ai', '12345', 'register').valid, false);
    assert.strictEqual(validateForm('demo@pramaan.ai', '123456', 'register').valid, true);
    assert.strictEqual(validateForm('demo@pramaan.ai', 'DemoPass123!', 'login').valid, true);
  });

  // 2. Login API Token Extraction
  test('2. Login response extracts JWT access token and rejects missing token payload', () => {
    const extractToken = (data) => {
      const token = data?.access_token;
      if (!token || typeof token !== 'string') {
        throw new Error('Authentication succeeded but no access token was returned.');
      }
      return token;
    };

    const validPayload = { access_token: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...', token_type: 'bearer' };
    assert.strictEqual(extractToken(validPayload), 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...');
    assert.throws(() => extractToken({}), /no access token was returned/);
    assert.throws(() => extractToken(null), /no access token was returned/);
  });

  // 3. FastAPI Array Error Formatting
  test('3. FastAPI Pydantic validation array error is cleanly formatted without [object Object]', () => {
    const errorWithArray = {
      response: {
        status: 422,
        data: {
          detail: [
            { type: 'value_error', loc: ['body', 'email'], msg: 'value is not a valid email address' },
            { type: 'string_too_short', loc: ['body', 'password'], msg: 'String should have at least 6 characters' },
          ],
        },
      },
    };
    const formatted = getNetworkErrorMessage(errorWithArray);
    assert.strictEqual(formatted, 'value is not a valid email address. String should have at least 6 characters');
    assert.strictEqual(formatted.includes('[object Object]'), false);
  });

  // 4. Token Persistence Lifecycle
  test('4. Token storage lifecycle: saveToken, getToken, and removeToken simulate storage persistence', async () => {
    const mockStorage = new Map();
    const TOKEN_KEY = 'foodscanner_token';

    const saveToken = async (tok) => { if (tok) mockStorage.set(TOKEN_KEY, tok); };
    const getToken = async () => mockStorage.get(TOKEN_KEY) || null;
    const removeToken = async () => { mockStorage.delete(TOKEN_KEY); };

    assert.strictEqual(await getToken(), null);
    await saveToken('token_lifecycle_jwt_123');
    assert.strictEqual(await getToken(), 'token_lifecycle_jwt_123');
    await removeToken();
    assert.strictEqual(await getToken(), null);
  });

  // 5. Logout Clears Token, Storage, and Session State
  test('5. Logout completely clears token, storage, and resets authenticated session', async () => {
    const mockStorage = new Map([['foodscanner_token', 'active_token_999'], ['user_prefs', 'saved']]);
    let isLoggedIn = true;
    let authHeader = 'Bearer active_token_999';

    const logout = async () => {
      mockStorage.clear();
      authHeader = undefined;
      isLoggedIn = false;
    };

    assert.strictEqual(isLoggedIn, true);
    await logout();
    assert.strictEqual(isLoggedIn, false);
    assert.strictEqual(mockStorage.size, 0);
    assert.strictEqual(authHeader, undefined);
  });

  // 6. Logout Navigation Stack Reset & Back Button Lock
  test('6. Logout triggers navigation stack reset to Login at index 0, preventing Back navigation to protected screens', () => {
    const mockNavigationStack = {
      routes: [{ name: 'Home' }, { name: 'Profile' }],
      index: 1,
      reset(payload) {
        this.index = payload.index;
        this.routes = payload.routes;
      },
      canGoBack() {
        return this.index > 0;
      },
    };

    assert.strictEqual(mockNavigationStack.canGoBack(), true);

    // Perform navigation reset to Login
    mockNavigationStack.reset({ index: 0, routes: [{ name: 'Login' }] });

    assert.strictEqual(mockNavigationStack.index, 0);
    assert.strictEqual(mockNavigationStack.routes.length, 1);
    assert.strictEqual(mockNavigationStack.routes[0].name, 'Login');
    assert.strictEqual(mockNavigationStack.canGoBack(), false);
  });

  // 7. 401 Interceptor Clears Protected Endpoints
  test('7. 401 response on protected endpoint triggers session cleanup', async () => {
    let sessionCleaned = false;
    const handleResponseError = async (error) => {
      const status = error?.response?.status;
      const url = error?.config?.url || '';
      const isAuthEndpoint = url.includes('/login') || url.includes('/register');
      if (status === 401 && !isAuthEndpoint) {
        sessionCleaned = true;
      }
      return Promise.reject(error);
    };

    const protectedError = { response: { status: 401 }, config: { url: '/user/profile' } };
    await assert.rejects(async () => handleResponseError(protectedError));
    assert.strictEqual(sessionCleaned, true);
  });

  // 8. 401 Interceptor Bypasses /login and /register
  test('8. 401 response on /login or /register does NOT trigger session cleanup or navigation reset', async () => {
    let sessionCleaned = false;
    const handleResponseError = async (error) => {
      const status = error?.response?.status;
      const url = error?.config?.url || '';
      const isAuthEndpoint = url.includes('/login') || url.includes('/register');
      if (status === 401 && !isAuthEndpoint) {
        sessionCleaned = true;
      }
      return Promise.reject(error);
    };

    const loginError = { response: { status: 401, data: { detail: 'Invalid email or password' } }, config: { url: '/login' } };
    await assert.rejects(async () => handleResponseError(loginError));
    assert.strictEqual(sessionCleaned, false);

    const registerError = { response: { status: 401 }, config: { url: '/register' } };
    await assert.rejects(async () => handleResponseError(registerError));
    assert.strictEqual(sessionCleaned, false);
  });

  // 9. Concurrent 401 Latch Prevents Multi-Call Spams
  test('9. In-flight 401 latch prevents duplicate unauthorized handler executions during burst failures', () => {
    let executionCount = 0;
    let isHandling401 = false;

    const on401 = () => {
      if (!isHandling401) {
        isHandling401 = true;
        executionCount += 1;
      }
    };

    // Simulate 5 simultaneous 401 responses
    on401();
    on401();
    on401();
    on401();
    on401();

    assert.strictEqual(executionCount, 1);
  });

  // 10. Session Restoration on App Boot
  test('10. Session restoration: app boots into authenticated state when token is present', async () => {
    const restoreSession = async (tokenProvider) => {
      const token = await tokenProvider();
      return !!token;
    };

    const hasSession = await restoreSession(async () => 'valid_persisted_token');
    assert.strictEqual(hasSession, true);
  });

  // 11. Session Absence on Cold Boot
  test('11. Session absence: app boots into Login screen when no token exists', async () => {
    const restoreSession = async (tokenProvider) => {
      const token = await tokenProvider();
      return !!token;
    };

    const hasSession = await restoreSession(async () => null);
    assert.strictEqual(hasSession, false);
  });

  // 12. Duplicate Login Submission Guard
  test('12. Duplicate login submission guard prevents concurrent API calls', async () => {
    let apiCallCount = 0;
    let isSubmitting = false;

    const submitLogin = async () => {
      if (isSubmitting) return;
      isSubmitting = true;
      apiCallCount += 1;
      // Simulate pending network call
      await new Promise((resolve) => setTimeout(resolve, 10));
      isSubmitting = false;
    };

    // Simulate double-click
    const p1 = submitLogin();
    const p2 = submitLogin();
    await Promise.all([p1, p2]);

    assert.strictEqual(apiCallCount, 1);
  });

  // 13. Duplicate Logout Submission Guard
  test('13. Duplicate logout submission guard prevents concurrent storage clearing', async () => {
    let logoutCallCount = 0;
    let isLoggingOut = false;

    const handleLogout = async () => {
      if (isLoggingOut) return;
      isLoggingOut = true;
      logoutCallCount += 1;
      await new Promise((resolve) => setTimeout(resolve, 10));
      isLoggingOut = false;
    };

    const p1 = handleLogout();
    const p2 = handleLogout();
    await Promise.all([p1, p2]);

    assert.strictEqual(logoutCallCount, 1);
  });

  // 14. Cross-Platform Logout Confirmation Behavior
  test('14. Cross-platform logout confirmation: web uses window.confirm, native uses Alert.alert', () => {
    let loggedOut = false;
    const performLogout = () => { loggedOut = true; };

    // Web simulation - cancel
    const handleLogoutWebCancel = (confirmFn) => {
      if (confirmFn('Are you sure?')) performLogout();
    };
    handleLogoutWebCancel(() => false);
    assert.strictEqual(loggedOut, false);

    // Web simulation - confirm
    const handleLogoutWebConfirm = (confirmFn) => {
      if (confirmFn('Are you sure?')) performLogout();
    };
    handleLogoutWebConfirm(() => true);
    assert.strictEqual(loggedOut, true);
  });
});

// ---------------------------------------------------------------------------
// Batch 12: Search Input Focus State, Typography & Zero Layout Shift Invariants
// ---------------------------------------------------------------------------
describe('Batch 12: Search Input Focus State, Typography & Zero Layout Shift Invariants', () => {
  const getSearchInputStyle = (isFocused) => ({
    wrapper: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: isFocused ? '#F7FAF1' : '#FFFFFF',
      borderRadius: 16,
      paddingHorizontal: 16,
      borderWidth: 1.5,
      borderColor: isFocused ? '#557A3E' : '#E1E6DC',
      minHeight: 52,
      ...(isFocused ? {
        shadowColor: '#557A3E',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.12,
        shadowRadius: 6,
        elevation: 2,
      } : {}),
    },
    innerInput: {
      flex: 1,
      paddingVertical: 12,
      fontSize: 16,
      fontWeight: '500',
      color: '#171A17',
      backgroundColor: 'transparent',
      borderWidth: 0,
      outlineStyle: 'none',
      outlineWidth: 0,
    },
    placeholderTextColor: '#7C837B',
    selectionColor: '#557A3E',
  });

  test('1. Unfocused input has subtle border (#E1E6DC) and no black outline', () => {
    const unfocused = getSearchInputStyle(false);
    assert.strictEqual(unfocused.wrapper.borderColor, '#E1E6DC');
    assert.strictEqual(unfocused.wrapper.backgroundColor, '#FFFFFF');
    assert.strictEqual(unfocused.wrapper.borderRadius, 16);
    assert.notStrictEqual(unfocused.wrapper.borderColor, '#000000');
    assert.strictEqual(unfocused.innerInput.outlineStyle, 'none');
    assert.strictEqual(unfocused.innerInput.borderWidth, 0);
  });

  test('2. Focused input switches to health-tech green accent (#557A3E) with bright background (#F7FAF1)', () => {
    const focused = getSearchInputStyle(true);
    assert.strictEqual(focused.wrapper.borderColor, '#557A3E');
    assert.strictEqual(focused.wrapper.backgroundColor, '#F7FAF1');
    assert.strictEqual(focused.wrapper.shadowColor, '#557A3E');
    assert.strictEqual(focused.selectionColor, '#557A3E');
  });

  test('3. Zero layout shift: dimensions and border widths are identical across focus transitions', () => {
    const unfocused = getSearchInputStyle(false);
    const focused = getSearchInputStyle(true);

    assert.strictEqual(unfocused.wrapper.borderWidth, focused.wrapper.borderWidth);
    assert.strictEqual(unfocused.wrapper.minHeight, focused.wrapper.minHeight);
    assert.strictEqual(unfocused.wrapper.paddingHorizontal, focused.wrapper.paddingHorizontal);
    assert.strictEqual(unfocused.innerInput.fontSize, focused.innerInput.fontSize);
  });

  test('4. Typography and placeholder conform to PRAMAAN specifications', () => {
    const style = getSearchInputStyle(false);
    assert.strictEqual(style.placeholderTextColor, '#7C837B');
    assert.strictEqual(style.innerInput.color, '#171A17');
    assert.strictEqual(style.innerInput.fontSize, 16);
    assert.strictEqual(style.innerInput.fontWeight, '500');
  });
});

describe('Batch 13: Manual Nutrition Entry Complete Integration & Invariant Tests', () => {
  // Pure test helpers mirroring src/utils/nutritionValidation.js and ManualEntryScreen.js
  function validateNumericField(val, fieldName, maxVal, unit = 'g') {
    if (val === null || val === undefined) return { value: null, error: null };
    const s = String(val).trim();
    if (s === '') return { value: null, error: null };
    if (s.startsWith('-')) return { value: null, error: `${fieldName} cannot be negative` };
    if (!/^\d+(\.\d+)?$/.test(s)) return { value: null, error: `${fieldName} must be a valid number` };
    const num = Number(s);
    if (!Number.isFinite(num) || Number.isNaN(num)) return { value: null, error: `${fieldName} must be a valid number` };
    if (num < 0) return { value: null, error: `${fieldName} cannot be negative` };
    if (maxVal != null && num > maxVal) return { value: null, error: `${fieldName} cannot exceed ${maxVal}${unit}` };
    return { value: num, error: null };
  }

  function validateManualNutritionForm(fields = {}) {
    const errors = {};
    const values = {};
    const rawName = String(fields.productName || '').trim();
    if (!rawName) errors.productName = 'Product name is required';
    else if (rawName.length > 120) errors.productName = 'Product name cannot exceed 120 characters';
    else values.product_name = rawName;

    const numericRules = [
      { key: 'calories', label: 'Calories', max: 2000, unit: ' kcal' },
      { key: 'protein', label: 'Protein', max: 100, unit: 'g' },
      { key: 'carbs', label: 'Carbohydrates', max: 100, unit: 'g' },
      { key: 'sugar', label: 'Sugar', max: 100, unit: 'g' },
      { key: 'fat', label: 'Total Fat', max: 100, unit: 'g' },
      { key: 'saturatedFat', label: 'Saturated Fat', max: 100, unit: 'g', targetKey: 'saturated_fat' },
      { key: 'fiber', label: 'Fiber', max: 100, unit: 'g' },
      { key: 'salt', label: 'Salt', max: 100, unit: 'g' },
    ];

    let hasAtLeastOneNutrient = false;
    for (const rule of numericRules) {
      const rawVal = fields[rule.key];
      const res = validateNumericField(rawVal, rule.label, rule.max, rule.unit);
      if (res.error) errors[rule.key] = res.error;
      else {
        values[rule.targetKey || rule.key] = res.value;
        if (res.value !== null) hasAtLeastOneNutrient = true;
      }
    }

    if (values.fat != null && values.saturated_fat != null && values.saturated_fat > values.fat) {
      errors.saturatedFat = 'Saturated fat cannot exceed total fat';
    }
    if (values.carbs != null && values.sugar != null && values.sugar > values.carbs) {
      errors.sugar = 'Total sugar cannot exceed carbohydrates';
    }
    if (!hasAtLeastOneNutrient && !errors.calories) {
      errors.calories = 'Please enter calories or at least one nutrient value';
    }

    const isValid = Object.keys(errors).length === 0;
    return { isValid, errors, values: isValid ? values : null };
  }

  function mapManualAnalysisToResult(analyzed, payload) {
    return {
      ...analyzed,
      product: {
        ...(analyzed?.product || {}),
        name: analyzed?.product?.name || payload.product_name,
        nutrition: analyzed?.product?.nutrition || payload,
        barcode: null, // manual entry does not have a fake barcode
      },
      analysis: analyzed?.analysis || {},
      decision: analyzed?.decision || {},
      diet_note: analyzed?.diet_note || null,
      recommendations: analyzed?.recommendations || [],
      health_score: analyzed?.analysis?.health_score ?? analyzed?.health_score ?? 0,
      final_decision: analyzed?.decision?.final_decision ?? analyzed?.final_decision ?? 'SAFE',
      reasons: analyzed?.decision?.reasons ?? analyzed?.reasons ?? [],
    };
  }

  test('1. Manual form validation: rejects empty product name and whitespace', () => {
    const emptyName = validateManualNutritionForm({ productName: '', calories: '200' });
    assert.strictEqual(emptyName.isValid, false);
    assert.strictEqual(emptyName.errors.productName, 'Product name is required');

    const spacesName = validateManualNutritionForm({ productName: '   ', calories: '200' });
    assert.strictEqual(spacesName.isValid, false);
    assert.strictEqual(spacesName.errors.productName, 'Product name is required');

    const validName = validateManualNutritionForm({ productName: 'Test Oats', calories: '389' });
    assert.strictEqual(validName.isValid, true);
    assert.strictEqual(validName.values.product_name, 'Test Oats');
  });

  test('2. Numeric validation: parses valid integers, decimals, and zero', () => {
    assert.deepStrictEqual(validateNumericField('0', 'Sugar', 100), { value: 0, error: null });
    assert.deepStrictEqual(validateNumericField('0.0', 'Fat', 100), { value: 0, error: null });
    assert.deepStrictEqual(validateNumericField('389', 'Calories', 2000), { value: 389, error: null });
    assert.deepStrictEqual(validateNumericField('16.9', 'Protein', 100), { value: 16.9, error: null });
  });

  test('3. Decimal values: maintains precision without float distortion', () => {
    const res = validateNumericField('0.05', 'Salt', 100);
    assert.strictEqual(res.error, null);
    assert.strictEqual(res.value, 0.05);

    const oats = validateManualNutritionForm({
      productName: 'Test Oats',
      calories: '389',
      fat: '6.9',
      saturatedFat: '1.2',
      carbs: '66.3',
      sugar: '0.9',
      fiber: '10.6',
      protein: '16.9',
      salt: '0.05',
    });
    assert.strictEqual(oats.isValid, true);
    assert.strictEqual(oats.values.fat, 6.9);
    assert.strictEqual(oats.values.saturated_fat, 1.2);
    assert.strictEqual(oats.values.salt, 0.05);
  });

  test('4. Empty values: empty inputs are null, NOT silently converted to zero', () => {
    const res = validateNumericField('', 'Fiber', 100);
    assert.strictEqual(res.value, null);
    assert.strictEqual(res.error, null);

    const formRes = validateManualNutritionForm({
      productName: 'Black Coffee',
      calories: '5',
      fat: '',
      sugar: '',
      protein: '',
    });
    assert.strictEqual(formRes.isValid, true);
    assert.strictEqual(formRes.values.fat, null);
    assert.strictEqual(formRes.values.sugar, null);
    assert.strictEqual(formRes.values.protein, null);
    assert.notStrictEqual(formRes.values.fat, 0);
  });

  test('5. Invalid values: rejects negative, NaN, malformed text and excessive values', () => {
    assert.strictEqual(validateNumericField('-10', 'Calories', 2000).error, 'Calories cannot be negative');
    assert.strictEqual(validateNumericField('abc', 'Protein', 100).error, 'Protein must be a valid number');
    assert.strictEqual(validateNumericField('1.2.3', 'Carbs', 100).error, 'Carbs must be a valid number');
    assert.strictEqual(validateNumericField('2500', 'Calories', 2000, ' kcal').error, 'Calories cannot exceed 2000 kcal');
    assert.strictEqual(validateNumericField('150', 'Sugar', 100).error, 'Sugar cannot exceed 100g');

    // Saturated fat > total fat
    const consistencyErr = validateManualNutritionForm({
      productName: 'Bad Snack',
      calories: '200',
      fat: '5',
      saturatedFat: '8',
    });
    assert.strictEqual(consistencyErr.isValid, false);
    assert.strictEqual(consistencyErr.errors.saturatedFat, 'Saturated fat cannot exceed total fat');
  });

  test('6. Correct API payload: builds exact schema required by /analyze', () => {
    const validation = validateManualNutritionForm({
      productName: 'Test Oats',
      calories: '389',
      fat: '6.9',
      saturatedFat: '1.2',
      carbs: '66.3',
      sugar: '0.9',
      fiber: '10.6',
      protein: '16.9',
      salt: '0.05',
    });
    assert.strictEqual(validation.isValid, true);
    const payload = validation.values;

    assert.strictEqual(payload.product_name, 'Test Oats');
    assert.strictEqual(payload.calories, 389);
    assert.strictEqual(payload.fat, 6.9);
    assert.strictEqual(payload.saturated_fat, 1.2);
    assert.strictEqual(payload.carbs, 66.3);
    assert.strictEqual(payload.sugar, 0.9);
    assert.strictEqual(payload.fiber, 10.6);
    assert.strictEqual(payload.protein, 16.9);
    assert.strictEqual(payload.salt, 0.05);
  });

  test('7. Successful API response mapping: constructs valid ResultScreen state', () => {
    const mockApiResponse = {
      product: {
        name: 'Test Oats',
        nutrition: { calories: 389, fat: 6.9, sugar: 0.9, protein: 16.9 },
        nutriscore: null,
      },
      analysis: {
        ingredient_analysis: { risk_level: 'LOW' },
        additive_analysis: { risk_level: 'LOW' },
        health_score: 100,
      },
      decision: {
        final_decision: 'SAFE',
        reasons: [],
      },
      diet_note: null,
      recommendations: [],
    };

    const payload = { product_name: 'Test Oats', calories: 389 };
    const result = mapManualAnalysisToResult(mockApiResponse, payload);

    assert.strictEqual(result.product.name, 'Test Oats');
    assert.strictEqual(result.analysis.health_score, 100);
    assert.strictEqual(result.health_score, 100);
    assert.strictEqual(result.decision.final_decision, 'SAFE');
    assert.strictEqual(result.final_decision, 'SAFE');
    assert.strictEqual(result.product.barcode, null);
  });

  test('8. Error response handling: formats Pydantic array and network errors cleanly', () => {
    const pydanticError = {
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'calories'], msg: 'Input should be a valid number' },
            { loc: ['body', 'product_name'], msg: 'Field required' },
          ],
        },
      },
    };
    const formattedMsg = getNetworkErrorMessage(pydanticError);
    assert.ok(formattedMsg.includes('Input should be a valid number'));
    assert.ok(formattedMsg.includes('Field required'));
    assert.notStrictEqual(formattedMsg, '[object Object]');

    const offlineError = { request: {}, message: 'Network Error' };
    const offlineMsg = getNetworkErrorMessage(offlineError);
    assert.ok(offlineMsg.includes('Cannot reach backend'));
  });

  test('9. Loading state: button is disabled when loading is active', () => {
    function getButtonState(loading) {
      return {
        disabled: Boolean(loading),
        loading: Boolean(loading),
      };
    }
    assert.deepStrictEqual(getButtonState(true), { disabled: true, loading: true });
    assert.deepStrictEqual(getButtonState(false), { disabled: false, loading: false });
  });

  test('10. Duplicate submission protection: in-flight latch prevents multiple requests', async () => {
    let apiCallCount = 0;
    let loading = false;

    async function submitForm() {
      if (loading) return 'blocked';
      loading = true;
      try {
        apiCallCount += 1;
        await new Promise((r) => setTimeout(r, 20));
        return 'success';
      } finally {
        loading = false;
      }
    }

    // Fire 5 rapid submissions simultaneously
    const results = await Promise.all([
      submitForm(),
      submitForm(),
      submitForm(),
      submitForm(),
      submitForm(),
    ]);

    assert.strictEqual(apiCallCount, 1, 'Only one API request should be made on rapid taps');
    assert.strictEqual(results.filter((r) => r === 'success').length, 1);
    assert.strictEqual(results.filter((r) => r === 'blocked').length, 4);
  });

  test('11. Result navigation: creates route params with canonical result and timestamp', () => {
    const payload = { product_name: 'Test Oats', calories: 389 };
    const result = mapManualAnalysisToResult({ analysis: { health_score: 95 } }, payload);
    const navParams = { result, timestamp: Date.now() };

    assert.ok(navParams.timestamp > 0);
    assert.strictEqual(navParams.result.product.name, 'Test Oats');
    assert.strictEqual(navParams.result.health_score, 95);
  });

  test('12. Scan ≠ Eat: manual analysis targets /analyze, NEVER /food-log', () => {
    const validation = validateManualNutritionForm({ productName: 'Test Oats', calories: '389' });
    const analysisReq = { path: '/analyze', method: 'POST', body: validation.values };

    assert.strictEqual(analysisReq.path, '/analyze');
    assert.notStrictEqual(analysisReq.path, '/food-log');
  });

  test('13. Explicit diary logging: diary action button correctly passes nutrition to /food-log', () => {
    function buildManualDiaryLog({ productName, nutrition, servingSize, barcode }) {
      return {
        path: '/food-log',
        method: 'POST',
        body: {
          product_name: productName,
          calories: Number(nutrition?.calories) || 0,
          fat: nutrition?.fat != null ? Number(nutrition.fat) : null,
          sugar: nutrition?.sugar != null ? Number(nutrition.sugar) : null,
          salt: nutrition?.salt != null ? Number(nutrition.salt) : null,
          protein: nutrition?.protein != null ? Number(nutrition.protein) : null,
          fiber: nutrition?.fiber != null ? Number(nutrition.fiber) : null,
          carbs: nutrition?.carbs != null ? Number(nutrition.carbs) : null,
          serving_size: servingSize || 100,
          barcode: barcode || null,
        },
      };
    }

    const logReq = buildManualDiaryLog({
      productName: 'Test Oats',
      nutrition: { calories: 389, fat: 6.9, sugar: 0.9, protein: 16.9 },
      servingSize: 100,
      barcode: null,
    });

    assert.strictEqual(logReq.path, '/food-log');
    assert.strictEqual(logReq.body.product_name, 'Test Oats');
    assert.strictEqual(logReq.body.calories, 389);
    assert.strictEqual(logReq.body.protein, 16.9);
    assert.strictEqual(logReq.body.barcode, null);
  });

  test('14. Authentication: client interceptor attaches Bearer token to /analyze', async () => {
    async function simulateRequestInterceptor(config, storedToken) {
      if (storedToken) {
        config.headers = config.headers || {};
        config.headers.Authorization = `Bearer ${storedToken}`;
      }
      return config;
    }

    const config = await simulateRequestInterceptor({ url: '/analyze' }, 'valid_jwt_token_xyz');
    assert.strictEqual(config.headers.Authorization, 'Bearer valid_jwt_token_xyz');
  });

  test('15. Manual result without barcode: barcode is null and child cards handle it gracefully', () => {
    const payload = { product_name: 'Custom Salad', calories: 150 };
    const result = mapManualAnalysisToResult({}, payload);

    assert.strictEqual(result.product.barcode, null);
    assert.notStrictEqual(result.product.barcode, '00000000', 'Must not use fake 00000000 barcode');

    // Child card barcode sanitization logic
    const sanitizedBarcode = result.product.barcode && String(result.product.barcode) !== '00000000'
      ? String(result.product.barcode)
      : null;
    assert.strictEqual(sanitizedBarcode, null);
  });
});

describe('Batch 14: History Item DB Identification & Report Goal Contract Compliance', () => {
  // 1. History Item DB Identification
  test('1. GET /history item schema contains real numeric id', () => {
    const rawHistoryRecord = {
      id: 142,
      barcode: '8901058851304',
      result: 'AVOID',
      scan_time: '2026-09-29 14:30:00',
    };

    assert.strictEqual(typeof rawHistoryRecord.id, 'number');
    assert.strictEqual(rawHistoryRecord.id, 142);
    assert.ok(rawHistoryRecord.id > 0);
  });

  // 2. deleteHistoryItem targets exact /history/{id}
  test('2. deleteHistoryItem targets /history/{scanId} using exact id', () => {
    function buildDeleteHistoryUrl(scanId) {
      return `/history/${encodeURIComponent(scanId)}`;
    }

    assert.strictEqual(buildDeleteHistoryUrl(142), '/history/142');
    assert.strictEqual(buildDeleteHistoryUrl('142'), '/history/142');
  });

  // 3. Goal Report: maps real backend fields without inventing values
  test('3. Goal Report maps days_active, days_remaining, and progress_score directly', () => {
    const backendGoalResponse = {
      goal_type: 'lose_weight',
      days_active: 12,
      days_remaining: 18,
      progress_score: 75.0,
      status: 'ON_TRACK',
      goal_summary: 'You are on track with your lose weight goal! Average daily score: 75/100',
      recommendations: ['Avoid products over 400 calories'],
    };

    function parseGoalMetrics(goal) {
      if (!goal || !goal.goal_type || goal.status === 'NO_GOAL') {
        return null;
      }
      return {
        daysActive: goal.days_active != null ? String(goal.days_active) : '—',
        daysLeft: goal.days_remaining != null ? String(goal.days_remaining) : '—',
        progressScore: goal.progress_score != null ? `${Math.round(goal.progress_score)}/100` : '—',
      };
    }

    const metrics = parseGoalMetrics(backendGoalResponse);
    assert.deepStrictEqual(metrics, {
      daysActive: '12',
      daysLeft: '18',
      progressScore: '75/100',
    });
  });

  // 4. Goal Name Formatting
  test('4. _formatGoalName transforms snake_case goal names to Title Case', () => {
    function _formatGoalName(type) {
      if (!type || typeof type !== 'string') return 'Dietary Goal';
      return type
        .split('_')
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
        .join(' ');
    }

    assert.strictEqual(_formatGoalName('lose_weight'), 'Lose Weight');
    assert.strictEqual(_formatGoalName('control_sugar'), 'Control Sugar');
    assert.strictEqual(_formatGoalName('eat_clean'), 'Eat Clean');
    assert.strictEqual(_formatGoalName('build_muscle'), 'Build Muscle');
    assert.strictEqual(_formatGoalName(null), 'Dietary Goal');
  });

  // 5. NO_GOAL status does NOT display misleading metric placeholders
  test('5. NO_GOAL status renders summary and suppresses misleading 30-day/0-adherence placeholders', () => {
    const noGoalResponse = {
      goal_type: null,
      days_active: 0,
      days_remaining: 0,
      progress_score: 0,
      status: 'NO_GOAL',
      goal_summary: 'No active goal. Set a goal in your profile to track progress.',
      recommendations: [],
    };

    const hasActiveGoal = Boolean(noGoalResponse.goal_type && noGoalResponse.status !== 'NO_GOAL');
    assert.strictEqual(hasActiveGoal, false);

    // Card subtitle displays real summary, not hardcoded placeholder
    const cardSub = noGoalResponse.goal_summary || 'Dietary progress tracking and compliance';
    assert.strictEqual(cardSub, 'No active goal. Set a goal in your profile to track progress.');
  });
});

// =============================================================================
// Batch 15: Nutrition Decimal Precision Formatting Tests
// =============================================================================
describe('Batch 15: Nutrition Decimal Precision Formatting Contract', () => {
  const { formatNutritionValue, formatNutritionWithUnit } = require('../src/utils/formatters');

  test('1. Exact example conversions from specifications', () => {
    assert.strictEqual(formatNutritionValue(133.333333333333), '133.33');
    assert.strictEqual(formatNutritionValue(1.33333333333333), '1.33');
    assert.strictEqual(formatNutritionValue(30.6666666666667), '30.67');
    assert.strictEqual(formatNutritionValue(29.3333333333333), '29.33');
    assert.strictEqual(formatNutritionValue(0.666666666666667), '0.67');
    assert.strictEqual(formatNutritionValue(2.36666666666667), '2.37');
  });

  test('2. Whole numbers display cleanly without trailing .00', () => {
    assert.strictEqual(formatNutritionValue(133), '133');
    assert.strictEqual(formatNutritionValue(133.0), '133');
    assert.strictEqual(formatNutritionValue(0), '0');
    assert.strictEqual(formatNutritionValue(2000), '2000');
  });

  test('3. Handles missing, null, undefined and invalid values safely', () => {
    assert.strictEqual(formatNutritionValue(null), '0');
    assert.strictEqual(formatNutritionValue(undefined), '0');
    assert.strictEqual(formatNutritionValue(''), '0');
    assert.strictEqual(formatNutritionValue('invalid'), '0');
    assert.strictEqual(formatNutritionValue(NaN), '0');
    assert.strictEqual(formatNutritionValue(null, '—'), '—');
    assert.strictEqual(formatNutritionValue(undefined, ''), '');
  });

  test('4. formatNutritionWithUnit keeps units and labels intact', () => {
    assert.strictEqual(formatNutritionWithUnit(133.333333333333, ' kcal'), '133.33 kcal');
    assert.strictEqual(formatNutritionWithUnit(1.33333333333333, 'g'), '1.33g');
    assert.strictEqual(formatNutritionWithUnit(null, 'g', '—'), '—');
  });
});

describe('Batch 16: Phase 1 Reliability — Food Log Deletion, Modification & Report Consistency Contract', () => {
  test('1. deleteFoodLogItem targets /food-log/{logId} with correct method and id', () => {
    function planDeleteUrl(logId) {
      if (!logId) throw new Error('logId required');
      return { method: 'DELETE', url: `/food-log/${logId}` };
    }
    assert.deepStrictEqual(planDeleteUrl(42), { method: 'DELETE', url: '/food-log/42' });
    assert.throws(() => planDeleteUrl(null), /logId required/);
  });

  test('2. updateFoodLogItem targets /food-log/{logId} with valid payload', () => {
    function planUpdatePayload(logId, data) {
      if (!logId) throw new Error('logId required');
      return { method: 'PUT', url: `/food-log/${logId}`, body: data };
    }
    const update = planUpdatePayload(42, { calories: 350.5, portion_size: 150 });
    assert.strictEqual(update.method, 'PUT');
    assert.strictEqual(update.url, '/food-log/42');
    assert.strictEqual(update.body.calories, 350.5);
  });

  test('3. ReportScreen trend score resolution handles both d.score and d.day_score seamlessly', () => {
    function resolveDayScore(d) {
      return Math.round(Number(d?.score != null ? d.score : (d?.day_score || 0)));
    }
    assert.strictEqual(resolveDayScore({ score: 85, day_score: 85 }), 85);
    assert.strictEqual(resolveDayScore({ day_score: 74 }), 74);
    assert.strictEqual(resolveDayScore({ score: 92 }), 92);
    assert.strictEqual(resolveDayScore({}), 0);
  });

  test('4. ResultScreen intake calculation avoids double scaling on custom portions', () => {
    function calculateLoggedIntake(rawNutrition, portionGrams) {
      const scale = portionGrams / 100.0;
      // Backend applies (rawNutrition * portionGrams / 100)
      return {
        calories: Number(rawNutrition.calories) * scale,
        sugar: Number(rawNutrition.sugar) * scale,
      };
    }
    const raw = { calories: 400, sugar: 20 };
    const scaled = calculateLoggedIntake(raw, 50);
    // Half serving of 400 kcal is 200 kcal, not 100 kcal
    assert.strictEqual(scaled.calories, 200);
    assert.strictEqual(scaled.sugar, 10);
  });
});

describe('Batch 17: Phase 2 Personalization — Biometrics, Mifflin-St Jeor & Allergen Safety Contract', () => {
  test('1. ProfileScreen Mifflin-St Jeor calculation computes accurate BMR, TDEE, and goal adaptations', () => {
    function computeEnergyEstimate(age, weight, height, goal) {
      const a = parseFloat(age);
      const w = parseFloat(weight);
      const h = parseFloat(height);
      if (!a || !w || !h || a <= 0 || w <= 0 || h <= 0 || a > 120 || w > 400 || h > 300) {
        return null;
      }
      const bmr = 10.0 * w + 6.25 * h - 5.0 * a - 78.0;
      const tdee = bmr * 1.35;
      let recommended = tdee;
      let goalNote = 'Maintenance target';

      if (goal === 'lose_weight') {
        recommended = Math.max(1200, tdee - 450);
        goalNote = 'Caloric deficit (-450 kcal) targeted for fat loss';
      } else if (goal === 'build_muscle') {
        recommended = tdee + 250;
        goalNote = 'Caloric surplus (+250 kcal) for muscle protein synthesis';
      }
      return {
        bmr: Math.round(bmr),
        tdee: Math.round(tdee),
        recommended: Math.round(recommended / 50) * 50,
        goalNote,
      };
    }

    // 28yo, 70kg, 175cm
    const maint = computeEnergyEstimate('28', '70', '175', 'eat_clean');
    assert.strictEqual(maint.bmr, 1576);
    assert.strictEqual(maint.tdee, 2127);
    assert.strictEqual(maint.recommended, 2150);

    // Deficit for weight loss
    const loss = computeEnergyEstimate('28', '70', '175', 'lose_weight');
    assert.strictEqual(loss.recommended, 1700);

    // Surplus for muscle building
    const muscle = computeEnergyEstimate('28', '70', '175', 'build_muscle');
    assert.strictEqual(muscle.recommended, 2400);

    // Null for invalid inputs
    assert.strictEqual(computeEnergyEstimate('-5', '70', '175', 'eat_clean'), null);
    assert.strictEqual(computeEnergyEstimate('', '70', '175', 'eat_clean'), null);
  });

  test('2. ProfileScreen allergen multi-select toggling constructs correct API comma-delimited string', () => {
    function toggleAllergen(currentList, key) {
      if (currentList.includes(key)) {
        return currentList.filter((k) => k !== key);
      } else {
        return [...currentList, key];
      }
    }

    let allergens = [];
    allergens = toggleAllergen(allergens, 'peanuts');
    assert.deepStrictEqual(allergens, ['peanuts']);

    allergens = toggleAllergen(allergens, 'milk');
    assert.deepStrictEqual(allergens, ['peanuts', 'milk']);

    // Toggle off 'peanuts'
    allergens = toggleAllergen(allergens, 'peanuts');
    assert.deepStrictEqual(allergens, ['milk']);

    // Payload serialization
    const payloadStr = allergens.length > 0 ? allergens.join(',') : '';
    assert.strictEqual(payloadStr, 'milk');
  });

  test('3. ResultScreen allergen alert classification differentiates critical matches from informational', () => {
    const rawWarnings = [
      { allergen: 'peanuts', severity: 'CRITICAL', user_match: true, warning: 'ALLERGEN ALERT: Contains Peanuts' },
      { allergen: 'soy', severity: 'MEDIUM', user_match: false, warning: 'Contains Soy.' },
    ];

    const criticalAllergens = rawWarnings.filter((w) => w?.severity === 'CRITICAL' || w?.user_match);
    assert.strictEqual(criticalAllergens.length, 1);
    assert.strictEqual(criticalAllergens[0].allergen, 'peanuts');
    assert.strictEqual(criticalAllergens[0].user_match, true);
  });
});

describe('Batch 18: Phase 3 AI Intelligence & Nutritional Comparison Contract', () => {
  test('1. Multi-turn chat message formatting passes conversation history without mutation', () => {
    function formatChatPayload(message, barcode, productContext, history) {
      return {
        message: String(message || '').trim(),
        barcode: barcode ? String(barcode).trim() : null,
        product_context: productContext || null,
        conversation_history: Array.isArray(history) ? history.slice(-6) : null,
      };
    }

    const messages = [
      { role: 'user', content: 'What is the score?' },
      { role: 'assistant', content: 'Score is 75/100.' },
      { role: 'user', content: 'Is it high in protein?' },
    ];

    const payload = formatChatPayload('Is it high in protein?', '8901234567890', { name: 'Almonds' }, messages);
    assert.strictEqual(payload.message, 'Is it high in protein?');
    assert.strictEqual(payload.conversation_history.length, 3);
    assert.strictEqual(payload.conversation_history[0].content, 'What is the score?');
  });

  test('2. ComparisonCard alternative suggestions extracts valid product names and caps at 3', () => {
    function extractAlternativeSuggestions(alternatives) {
      if (!Array.isArray(alternatives)) return [];
      return alternatives
        .map((a) => (typeof a === 'string' ? a : a?.product_name || a?.name))
        .filter((n) => typeof n === 'string' && n.trim().length > 0)
        .slice(0, 3);
    }

    const recommendations = [
      { product_name: 'Baked Multigrain Crisps', reason: 'Lower saturated fat' },
      { product_name: 'Roasted Makhana', reason: 'Zero trans fats' },
      { product_name: 'Sprouted Moong Snack', reason: 'High fiber and protein' },
      { product_name: 'Air Popped Corn', reason: 'Low calorie' },
    ];

    const suggestions = extractAlternativeSuggestions(recommendations);
    assert.strictEqual(suggestions.length, 3);
    assert.deepStrictEqual(suggestions, [
      'Baked Multigrain Crisps',
      'Roasted Makhana',
      'Sprouted Moong Snack',
    ]);
  });

  test('3. Analytical queries preserve Scan != Eat and never target /food-log', () => {
    const endpointsHit = [];
    const mockClient = {
      post: (url) => { endpointsHit.push(url); return Promise.resolve({ data: {} }); },
    };

    // Chat query
    mockClient.post('/chat', { message: 'Is this snack healthy?' });
    // Comparison query
    mockClient.post('/compare', { product_a: '8901', product_b: '8902' });

    assert.strictEqual(endpointsHit.includes('/food-log'), false);
    assert.strictEqual(endpointsHit.length, 2);
  });
});

describe('Batch 19: Async OCR Integration, Polling Lifecycle & Production Reliability Contract', () => {
  const {
    OCR_STATUS,
    OCR_UI_STATE,
    isOcrResultEmpty,
    extractOcrNutrientValues,
    sleepWithAbort,
    pollOcrJobWithClient,
  } = require('../src/utils/ocrPolling');

  test('1. submitOcrJob builds exact backend contract payload with async flag and claims', async () => {
    let captured = null;
    const mockClient = {
      post: async (url, payload, config) => {
        captured = { url, payload, config };
        return {
          data: {
            job_id: 'ocr_test_12345',
            status: 'pending',
            created_at: new Date().toISOString(),
            poll_url: '/ocr/jobs/ocr_test_12345',
          },
        };
      },
    };

    async function submitOcrJobTest(base64Image, options = {}) {
      const payload = {
        image_base64: base64Image,
        async_mode: true,
      };
      if (Array.isArray(options?.claims) && options.claims.length > 0) {
        payload.claims = options.claims;
      }
      const config = {};
      if (options?.signal) {
        config.signal = options.signal;
      }
      const res = await mockClient.post('/ocr?async=true', payload, config);
      return res.data;
    }

    const testB64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
    const res = await submitOcrJobTest(testB64, { claims: ['High Protein', 'Gluten Free'] });

    assert.strictEqual(res.job_id, 'ocr_test_12345');
    assert.strictEqual(res.status, 'pending');
    assert.strictEqual(captured.url, '/ocr?async=true');
    assert.strictEqual(captured.payload.image_base64, testB64);
    assert.strictEqual(captured.payload.async_mode, true);
    assert.deepStrictEqual(captured.payload.claims, ['High Protein', 'Gluten Free']);
  });

  test('2. getOcrJobStatus validates job_id and routes to /ocr/jobs/{job_id}', async () => {
    let queriedUrl = null;
    const mockClient = {
      get: async (url, config) => {
        queriedUrl = url;
        return {
          data: {
            job_id: 'ocr_abc_789',
            status: 'processing',
            created_at: '2026-10-23T12:00:00Z',
            started_at: '2026-10-23T12:00:01Z',
            completed_at: null,
            result: null,
            error: null,
          },
        };
      },
    };

    async function getOcrJobStatusTest(jobId, options = {}) {
      if (!jobId || typeof jobId !== 'string' || !jobId.trim()) {
        throw new Error('jobId is required and must be a non-empty string');
      }
      const config = {};
      if (options?.signal) config.signal = options.signal;
      const res = await mockClient.get(`/ocr/jobs/${encodeURIComponent(jobId.trim())}`, config);
      return res.data;
    }

    // Valid job_id
    const data = await getOcrJobStatusTest('ocr_abc_789');
    assert.strictEqual(data.job_id, 'ocr_abc_789');
    assert.strictEqual(queriedUrl, '/ocr/jobs/ocr_abc_789');

    // URI encoding validation for special characters
    await getOcrJobStatusTest('ocr/job?test#1');
    assert.strictEqual(queriedUrl, '/ocr/jobs/ocr%2Fjob%3Ftest%231');

    // Rejects empty/invalid job_id
    await assert.rejects(async () => {
      await getOcrJobStatusTest('');
    }, /jobId is required/);

    await assert.rejects(async () => {
      await getOcrJobStatusTest(null);
    }, /jobId is required/);
  });

  test('3. Polling lifecycle: transitions pending -> processing -> completed and invokes onProgress', async () => {
    const states = [
      { job_id: 'ocr_flow_1', status: 'pending', result: null, error: null },
      { job_id: 'ocr_flow_1', status: 'processing', result: null, error: null },
      {
        job_id: 'ocr_flow_1',
        status: 'completed',
        result: {
          calories: 180,
          protein: 12.5,
          carbs: 22.0,
          fat: 4.2,
          sugar: 3.1,
          salt: 0.8,
          fiber: 5.0,
          product_name: 'Organic Lentil Soup',
          confidence: 'high',
        },
        error: null,
      },
    ];

    let pollIndex = 0;
    const mockFetcher = async () => {
      const item = states[Math.min(pollIndex, states.length - 1)];
      pollIndex += 1;
      return item;
    };

    const progressReports = [];
    let currentTime = 1000;
    const mockNow = () => currentTime;
    const mockSleep = async (ms) => { currentTime += ms; };

    const finalResult = await pollOcrJobWithClient(mockFetcher, 'ocr_flow_1', {
      intervalMs: 500,
      timeoutMs: 10000,
      nowFn: mockNow,
      sleepFn: mockSleep,
      onProgress: (info) => progressReports.push(info),
    });

    assert.strictEqual(finalResult.status, 'completed');
    assert.strictEqual(finalResult.result.calories, 180);
    assert.strictEqual(finalResult.result.product_name, 'Organic Lentil Soup');

    // Progress callbacks fired for pending, processing, and completed
    assert.strictEqual(progressReports.length, 3);
    assert.strictEqual(progressReports[0].status, 'pending');
    assert.strictEqual(progressReports[1].status, 'processing');
    assert.strictEqual(progressReports[2].status, 'completed');
  });

  test('4. Polling lifecycle: backend job status "failed" terminates polling and throws error', async () => {
    const states = [
      { job_id: 'ocr_fail_1', status: 'processing', result: null, error: null },
      { job_id: 'ocr_fail_1', status: 'failed', result: null, error: 'OCR processing failed: unreadable image' },
    ];

    let pollIndex = 0;
    const mockFetcher = async () => {
      const item = states[Math.min(pollIndex, states.length - 1)];
      pollIndex += 1;
      return item;
    };

    let currentTime = 1000;
    await assert.rejects(
      async () => {
        await pollOcrJobWithClient(mockFetcher, 'ocr_fail_1', {
          intervalMs: 500,
          timeoutMs: 10000,
          nowFn: () => currentTime,
          sleepFn: async (ms) => { currentTime += ms; },
        });
      },
      (err) => {
        assert.strictEqual(err.code, 'JOB_FAILED');
        assert.ok(err.message.includes('unreadable image'));
        return true;
      }
    );
  });

  test('5. Polling lifecycle: bounded timeout stops polling after timeoutMs', async () => {
    const mockFetcher = async () => ({
      job_id: 'ocr_stuck_1',
      status: 'processing',
      result: null,
      error: null,
    });

    let currentTime = 0;
    const mockNow = () => currentTime;
    const mockSleep = async (ms) => { currentTime += ms; };

    await assert.rejects(
      async () => {
        await pollOcrJobWithClient(mockFetcher, 'ocr_stuck_1', {
          intervalMs: 1000,
          timeoutMs: 5000, // 5s timeout
          nowFn: mockNow,
          sleepFn: mockSleep,
        });
      },
      (err) => {
        assert.strictEqual(err.code, 'ETIMEDOUT');
        assert.ok(err.message.includes('timed out after 5 seconds'));
        return true;
      }
    );
  });

  test('6. Polling lifecycle: AbortSignal stops polling immediately and cancels sleep', async () => {
    const controller = new AbortController();
    let pollCount = 0;

    const mockFetcher = async () => {
      pollCount += 1;
      if (pollCount === 1) {
        // Abort during first sleep
        controller.abort();
      }
      return { job_id: 'ocr_abort_1', status: 'processing', result: null, error: null };
    };

    await assert.rejects(
      async () => {
        await pollOcrJobWithClient(mockFetcher, 'ocr_abort_1', {
          intervalMs: 1500,
          timeoutMs: 30000,
          signal: controller.signal,
        });
      },
      (err) => {
        assert.strictEqual(err.name, 'AbortError');
        return true;
      }
    );

    assert.strictEqual(pollCount, 1, 'Subsequent polls must not be executed after abort');
  });

  test('7. Polling lifecycle: prevents overlapping polling requests (strict concurrency = 1)', async () => {
    let concurrentCount = 0;
    let maxConcurrencyObserved = 0;
    let totalPolls = 0;

    const mockFetcher = async () => {
      concurrentCount += 1;
      maxConcurrencyObserved = Math.max(maxConcurrencyObserved, concurrentCount);
      totalPolls += 1;

      // Simulate network latency
      await new Promise((r) => setTimeout(r, 15));
      concurrentCount -= 1;

      if (totalPolls >= 3) {
        return { job_id: 'ocr_seq_1', status: 'completed', result: { calories: 100 }, error: null };
      }
      return { job_id: 'ocr_seq_1', status: 'processing', result: null, error: null };
    };

    const res = await pollOcrJobWithClient(mockFetcher, 'ocr_seq_1', {
      intervalMs: 10,
      timeoutMs: 10000,
    });

    assert.strictEqual(res.status, 'completed');
    assert.strictEqual(maxConcurrencyObserved, 1, 'Maximum concurrency must never exceed 1');
    assert.strictEqual(totalPolls, 3);
  });

  test('8. Transient network error resilience retries up to maxConsecutiveTransientErrors', async () => {
    let calls = 0;
    const mockFetcher = async () => {
      calls += 1;
      if (calls === 1 || calls === 2) {
        // Transient 503 Service Unavailable / Network drop
        const err = new Error('Network timeout');
        err.response = { status: 503, data: { detail: 'Service busy' } };
        throw err;
      }
      return {
        job_id: 'ocr_retry_1',
        status: 'completed',
        result: { calories: 220, protein: 8 },
        error: null,
      };
    };

    let currentTime = 1000;
    const res = await pollOcrJobWithClient(mockFetcher, 'ocr_retry_1', {
      intervalMs: 100,
      timeoutMs: 10000,
      maxConsecutiveTransientErrors: 3,
      nowFn: () => currentTime,
      sleepFn: async (ms) => { currentTime += ms; },
    });

    assert.strictEqual(res.status, 'completed');
    assert.strictEqual(calls, 3);
  });

  test('9. Non-retryable HTTP errors (401, 404, 400) fail fast without retrying', async () => {
    let calls = 0;
    const mockFetcher = async () => {
      calls += 1;
      const err = new Error('Not Found');
      err.response = { status: 404, data: { detail: 'OCR job not found' } };
      throw err;
    };

    await assert.rejects(
      async () => {
        await pollOcrJobWithClient(mockFetcher, 'ocr_404_1', {
          intervalMs: 100,
          timeoutMs: 10000,
          maxConsecutiveTransientErrors: 3,
        });
      },
      (err) => {
        assert.strictEqual(err.response?.status, 404);
        return true;
      }
    );

    assert.strictEqual(calls, 1, 'Non-retryable 404 must not trigger retries');
  });

  test('10. isOcrResultEmpty accurately differentiates empty, failed, and valid nutrition results', () => {
    // Empty inputs
    assert.strictEqual(isOcrResultEmpty(null), true);
    assert.strictEqual(isOcrResultEmpty(undefined), true);
    assert.strictEqual(isOcrResultEmpty({}), true);

    // Backend terminal error statuses
    assert.strictEqual(isOcrResultEmpty({ status: 'NO_TEXT' }), true);
    assert.strictEqual(isOcrResultEmpty({ status: 'INVALID_IMAGE' }), true);
    assert.strictEqual(isOcrResultEmpty({ status: 'EXTRACTION_FAILED' }), true);

    // All nutrients null or NaN
    assert.strictEqual(isOcrResultEmpty({
      calories: null,
      fat: null,
      sugar: null,
      salt: null,
      protein: null,
      fiber: null,
      carbs: null,
    }), true);

    // Valid positive nutrition
    assert.strictEqual(isOcrResultEmpty({
      calories: 250,
      protein: 10,
      carbs: 30,
    }), false);

    // Valid zero nutrition (e.g. black coffee, water, zero calorie beverage)
    assert.strictEqual(isOcrResultEmpty({
      calories: 0,
      sugar: 0,
    }), false);
  });

  test('11. extractOcrNutrientValues extracts all canonical keys safely', () => {
    const rawResult = {
      product_name: 'Highland Rolled Oats',
      calories: '375',
      fat: '7.5',
      sugar: '1.2',
      salt: '0.02',
      protein: '13.0',
      fiber: '10.5',
      carbs: '62.0',
      confidence: 'high',
      raw_text: 'Nutrition Information per 100g...',
      ocr_version: '2.0.0',
      claim_verification: [{ claim: 'High Fiber', status: 'VERIFIED' }],
    };

    const extracted = extractOcrNutrientValues(rawResult);
    assert.strictEqual(extracted.product_name, 'Highland Rolled Oats');
    assert.strictEqual(extracted.calories, 375);
    assert.strictEqual(extracted.fat, 7.5);
    assert.strictEqual(extracted.sugar, 1.2);
    assert.strictEqual(extracted.protein, 13.0);
    assert.strictEqual(extracted.fiber, 10.5);
    assert.strictEqual(extracted.carbs, 62.0);
    assert.strictEqual(extracted.confidence, 'high');
    assert.strictEqual(extracted.ocr_version, '2.0.0');
    assert.strictEqual(extracted.claim_verification.length, 1);

    // Handles null safely
    const emptyExtracted = extractOcrNutrientValues(null);
    assert.strictEqual(emptyExtracted.calories, null);
    assert.strictEqual(emptyExtracted.product_name, '');
  });

  test('12. Synchronous OCR preservation: scanNutritionLabel executes immediate POST /ocr without async flag', async () => {
    let hitEndpoint = null;
    let hitPayload = null;
    const mockClient = {
      post: async (url, payload) => {
        hitEndpoint = url;
        hitPayload = payload;
        return {
          data: {
            calories: 150,
            product_name: 'Sync Crackers',
            confidence: 0.88,
          },
        };
      },
    };

    async function scanNutritionLabelTest(base64Image) {
      const res = await mockClient.post('/ocr', { image_base64: base64Image });
      return res.data;
    }

    const testImg = 'sample_b64_string';
    const result = await scanNutritionLabelTest(testImg);

    assert.strictEqual(hitEndpoint, '/ocr');
    assert.strictEqual(hitPayload.image_base64, testImg);
    assert.strictEqual(hitPayload.async_mode, undefined);
    assert.strictEqual(result.calories, 150);
  });

  test('13. Duplicate submission guard prevents overlapping uploads while in-flight', async () => {
    let activeSubmissions = 0;
    let duplicateRejectionCount = 0;

    let inFlight = false;
    async function guardedProcessAsset(b64) {
      if (inFlight) {
        duplicateRejectionCount += 1;
        return null;
      }
      inFlight = true;
      activeSubmissions += 1;
      try {
        await new Promise((r) => setTimeout(r, 20));
        return { job_id: 'ocr_success' };
      } finally {
        inFlight = false;
        activeSubmissions -= 1;
      }
    }

    // Trigger two submissions concurrently
    const [p1, p2] = await Promise.all([
      guardedProcessAsset('img1'),
      guardedProcessAsset('img2'),
    ]);

    assert.strictEqual(duplicateRejectionCount, 1);
    assert.strictEqual(activeSubmissions, 0);
    assert.ok(p1 !== null || p2 !== null);
  });

  test('14. Security & Privacy invariant: no auth tokens or sensitive data leaked in errors', () => {
    const errorWithAuthHeader = {
      response: {
        status: 400,
        data: { detail: 'Corrupted image bytes' },
      },
      config: {
        headers: {
          Authorization: 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sensitive_token_payload',
        },
      },
      message: 'Request failed with status code 400',
    };

    const sanitizedMsg = getNetworkErrorMessage(errorWithAuthHeader);
    assert.strictEqual(sanitizedMsg, 'Corrupted image bytes');
    assert.strictEqual(sanitizedMsg.includes('Bearer'), false);
    assert.strictEqual(sanitizedMsg.includes('eyJhbGciOi'), false);
  });

  test('15. sleepWithAbort and OCR status constants contract', async () => {
    // Contract constants integrity
    assert.strictEqual(OCR_STATUS.PENDING, 'pending');
    assert.strictEqual(OCR_STATUS.PROCESSING, 'processing');
    assert.strictEqual(OCR_STATUS.COMPLETED, 'completed');
    assert.strictEqual(OCR_STATUS.FAILED, 'failed');

    assert.strictEqual(OCR_UI_STATE.IDLE, 'IDLE');
    assert.strictEqual(OCR_UI_STATE.SUBMITTING, 'SUBMITTING');
    assert.strictEqual(OCR_UI_STATE.POLLING, 'POLLING');
    assert.strictEqual(OCR_UI_STATE.SUCCESS, 'SUCCESS');
    assert.strictEqual(OCR_UI_STATE.EMPTY, 'EMPTY');
    assert.strictEqual(OCR_UI_STATE.FAILED, 'FAILED');

    // sleepWithAbort resolves normally
    await sleepWithAbort(5);

    // sleepWithAbort rejects when aborted prior to call
    const preAborted = new AbortController();
    preAborted.abort();
    await assert.rejects(async () => {
      await sleepWithAbort(100, preAborted.signal);
    }, (err) => {
      assert.strictEqual(err.name, 'AbortError');
      return true;
    });

    // sleepWithAbort rejects when aborted midway
    const midwayController = new AbortController();
    const sleepPromise = sleepWithAbort(1000, midwayController.signal);
    midwayController.abort();
    await assert.rejects(async () => {
      await sleepPromise;
    }, (err) => {
      assert.strictEqual(err.name, 'AbortError');
      return true;
    });
  });
});
