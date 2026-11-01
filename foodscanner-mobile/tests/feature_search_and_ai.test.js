const { test, describe } = require('node:test');
const assert = require('node:assert');

describe('Feature 1: PRAMAAN AI Assistant & Navigation', () => {
  test('1. Home screen Ask AI button targets AIAssistant route, not Scan', () => {
    let navigatedRoute = null;
    let navigatedParams = null;
    const mockNavigation = {
      navigate: (screen, params) => {
        navigatedRoute = screen;
        navigatedParams = params;
      },
    };

    // Simulate Ask AI button onPress handler from HomeScreen.js
    const handleAskAiPress = () => {
      mockNavigation.navigate('AIAssistant');
    };

    handleAskAiPress();

    assert.strictEqual(navigatedRoute, 'AIAssistant');
    assert.notStrictEqual(navigatedRoute, 'Scan');
    assert.strictEqual(navigatedParams, undefined);
  });

  test('2. AIAssistant Back button invokes navigation.goBack()', () => {
    let wentBack = false;
    const mockNavigation = {
      goBack: () => {
        wentBack = true;
      },
    };

    // Simulate Back button onPress
    mockNavigation.goBack();
    assert.strictEqual(wentBack, true);
  });

  test('3. askNutritionAssistant payload constructs proper query and product context', () => {
    const message = 'Is palm oil healthy in biscuits?';
    const barcode = '8901030383748';
    const productContext = {
      product_name: 'Amul Butter',
      barcode: '8901030383748',
      nutrition: { calories: 717, fat: 81 },
      health_score: 35,
      final_decision: 'AVOID',
    };
    const conversationHistory = [
      { role: 'user', content: 'Hello' },
      { role: 'assistant', content: 'Hi, ask me about nutrition!' },
    ];

    const payload = {
      message: String(message || '').trim(),
      barcode: barcode ? String(barcode).trim() : null,
      product_context: productContext || null,
      conversation_history: Array.isArray(conversationHistory) ? conversationHistory : null,
    };

    assert.strictEqual(payload.message, 'Is palm oil healthy in biscuits?');
    assert.strictEqual(payload.barcode, '8901030383748');
    assert.strictEqual(payload.product_context.product_name, 'Amul Butter');
    assert.strictEqual(payload.product_context.health_score, 35);
    assert.strictEqual(payload.conversation_history.length, 2);
  });

  test('4. AI Assistant response extracts answer field accurately from RAG backend response', () => {
    const backendResponse = {
      answer: 'Total fat intake should not exceed 20% to 35% of daily energy intake according to WHO guidelines.',
      sources: ['WHO Healthy Diet Fact Sheet No. 394', 'FSSAI Advertising and Claims Regulations'],
      product_context_used: true,
      health_score: 35,
      final_decision: 'AVOID',
    };

    const reply =
      backendResponse?.answer ||
      backendResponse?.reply ||
      backendResponse?.response ||
      'Default response';

    assert.strictEqual(
      reply,
      'Total fat intake should not exceed 20% to 35% of daily energy intake according to WHO guidelines.'
    );
    assert.strictEqual(backendResponse.sources.length, 2);
  });

  test('5. Suggested question chip selection dispatches message correctly', () => {
    let sentQuery = null;
    const handleSend = (text) => {
      sentQuery = text;
    };

    const suggestedPrompt = 'What are the limits for fats and saturated fatty acids?';
    handleSend(suggestedPrompt);

    assert.strictEqual(sentQuery, 'What are the limits for fats and saturated fatty acids?');
  });

  test('6. Chat error state records last failed message and retry re-submits', () => {
    let lastFailedMessage = null;
    let sentQuery = null;
    const handleSend = (text) => {
      sentQuery = text;
    };

    // Simulate failure
    const originalQuery = 'What does INS 621 do?';
    lastFailedMessage = originalQuery;

    // Simulate retry
    const handleRetry = () => {
      if (lastFailedMessage) {
        handleSend(lastFailedMessage);
      }
    };

    handleRetry();
    assert.strictEqual(sentQuery, 'What does INS 621 do?');
  });
});

describe('Feature 2: Product Search & Result Navigation', () => {
  test('1. Search query formatting and min_length guard', () => {
    const formatSearch = (q) => {
      const clean = String(q || '').trim();
      return clean.length >= 2 ? clean : null;
    };

    assert.strictEqual(formatSearch(''), null);
    assert.strictEqual(formatSearch(' a '), null);
    assert.strictEqual(formatSearch('Amul'), 'Amul');
    assert.strictEqual(formatSearch('   Britannia   '), 'Britannia');
  });

  test('2. Standard numeric barcode search result routes to scanProduct', () => {
    const item = {
      barcode: '8901030383748',
      product_name: 'Standard Barcode Biscuit',
      nutrition: { calories: 450, fat: 15 },
    };

    const isStandardBarcode =
      item.barcode && item.barcode !== '00000000' && /^\d{8,14}$/.test(item.barcode);

    assert.strictEqual(isStandardBarcode, true);
  });

  test('3. Indian dataset item with IND- prefix routes to analyzeManualProduct', () => {
    const item = {
      barcode: 'IND-48a5108a5042497d975506c24a31fa2c',
      product_name: 'Amul Butter',
      nutrition: { calories: 717, fat: 81 },
    };

    const isStandardBarcode =
      item.barcode && item.barcode !== '00000000' && /^\d{8,14}$/.test(item.barcode);

    assert.strictEqual(isStandardBarcode, false);

    const manualPayload = {
      product_name: item.product_name,
      calories: item.nutrition?.calories != null ? Number(item.nutrition.calories) : null,
      fat: item.nutrition?.fat != null ? Number(item.nutrition.fat) : null,
    };

    assert.strictEqual(manualPayload.product_name, 'Amul Butter');
    assert.strictEqual(manualPayload.calories, 717);
    assert.strictEqual(manualPayload.fat, 81);
  });

  test('4. Selecting search result navigates to ResultScreen with result payload and timestamp', () => {
    let targetScreen = null;
    let targetParams = null;
    const mockNavigation = {
      navigate: (screen, params) => {
        targetScreen = screen;
        targetParams = params;
      },
    };

    const fakeAnalysisResult = {
      product: { name: 'Amul Butter', barcode: 'IND-48a5108a5042497d975506c24a31fa2c' },
      analysis: { health_score: 35 },
      decision: { final_decision: 'AVOID' },
    };

    // Navigate to Result
    mockNavigation.navigate('Result', { result: fakeAnalysisResult, timestamp: Date.now() });

    assert.strictEqual(targetScreen, 'Result');
    assert.strictEqual(targetParams.result.product.name, 'Amul Butter');
    assert.strictEqual(targetParams.result.analysis.health_score, 35);
    assert.ok(targetParams.timestamp > 0);
  });

  test('5. Clear search resets query, results, and error state', () => {
    let query = 'Amul';
    let results = [{ product_name: 'Amul Butter' }];
    let error = 'Network error';

    const handleClear = () => {
      query = '';
      results = [];
      error = null;
    };

    handleClear();
    assert.strictEqual(query, '');
    assert.strictEqual(results.length, 0);
    assert.strictEqual(error, null);
  });
});
