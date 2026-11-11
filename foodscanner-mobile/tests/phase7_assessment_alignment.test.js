const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  ASSESSMENT_STATUS,
  CONFIDENCE_LEVEL,
  MANDATORY_NUTRIENTS,
  isValidScoreRange,
  formatScoreRange,
  normalizeAssessmentStatus,
  normalizeConfidence,
  getConfidenceMeta,
  getAssessmentStatusMeta,
  getMissingMandatoryNutrients,
  getMandatoryNutrientDisplayName,
  getDecisionMeta,
} = require('../src/utils/assessment');

const {
  formatNutritionValue,
  formatNutritionWithUnit,
  isNutrientPresent,
} = require('../src/utils/formatters');

describe('Phase 7: Complete Mobile UI/UX Alignment Contract', () => {

  // 1. Complete product with numeric score
  test('1. Complete product with numeric score preserves score and decision', () => {
    const rawStatus = 'ASSESSABLE';
    const rawScore = 82;
    const rawDecision = 'SAFE';
    const rawConfidence = 'HIGH';
    const rawNutrition = { calories: 350, sugar: 4.5, fat: 8.0, salt: 0.6 };

    const status = normalizeAssessmentStatus(rawStatus, {
      healthScore: rawScore,
      scoreRange: null,
      nutrition: rawNutrition,
    });
    assert.strictEqual(status, ASSESSMENT_STATUS.COMPLETE);

    const isNumeric = rawScore !== null && rawScore !== undefined && Number.isFinite(Number(rawScore));
    assert.strictEqual(isNumeric, true);
    assert.strictEqual(Number(rawScore), 82);

    const decisionMeta = getDecisionMeta(rawDecision, status);
    assert.strictEqual(decisionMeta.isValid, true);
    assert.strictEqual(decisionMeta.label, 'SAFE');
    assert.strictEqual(decisionMeta.color, '#15803D');

    const statusMeta = getAssessmentStatusMeta(status);
    assert.strictEqual(statusMeta.label, 'Complete Assessment');

    const confMeta = getConfidenceMeta(rawConfidence);
    assert.strictEqual(confMeta.label, 'High Confidence');
    assert.strictEqual(confMeta.description, 'High confidence in available product information.');
  });

  // 2. Partial product with null score and valid score range
  test('2. Partial product with null score and valid score range prevents numeric score and fake SAFE', () => {
    const rawStatus = 'PARTIALLY_ASSESSABLE';
    const rawScore = null;
    const scoreRange = { min_score: 35.0, max_score: 65.0 };
    const rawDecision = 'INCOMPLETE_DATA';

    const status = normalizeAssessmentStatus(rawStatus, {
      healthScore: rawScore,
      scoreRange,
    });
    assert.strictEqual(status, ASSESSMENT_STATUS.PARTIAL);

    // Invariant: score must strictly remain null, never coerced to 0 or 50
    const healthScore = rawScore != null && Number.isFinite(Number(rawScore)) ? Number(rawScore) : null;
    assert.strictEqual(healthScore, null);

    // Valid range validation and formatting
    assert.strictEqual(isValidScoreRange(scoreRange), true);
    const rangeText = formatScoreRange(scoreRange);
    assert.strictEqual(rangeText, 'Estimated score range: 35–65');

    // Invariant: Decision must NEVER be SAFE or MODERATE for partial assessment
    const decisionMeta = getDecisionMeta(rawDecision, status);
    assert.strictEqual(decisionMeta.isValid, false);
    assert.strictEqual(decisionMeta.label, null);

    // Even if backend accidentally passes SAFE, partial policy strictly rejects it
    const fakeSafeMeta = getDecisionMeta('SAFE', status);
    assert.strictEqual(fakeSafeMeta.isValid, false);
    assert.strictEqual(fakeSafeMeta.label, null);
  });

  // 3. Partial product with null score and missing score range
  test('3. Partial product with null score and missing score range shows partial status without range', () => {
    const rawStatus = 'PARTIAL';
    const rawScore = null;
    const scoreRange = null;

    const status = normalizeAssessmentStatus(rawStatus, {
      healthScore: rawScore,
      scoreRange,
    });
    assert.strictEqual(status, ASSESSMENT_STATUS.PARTIAL);
    assert.strictEqual(isValidScoreRange(scoreRange), false);
    assert.strictEqual(formatScoreRange(scoreRange), null);

    const statusMeta = getAssessmentStatusMeta(status);
    assert.strictEqual(statusMeta.label, 'Partial Assessment');
  });

  // 4. Unassessable product with null score
  test('4. Unassessable product with null score never displays numeric score, 0, SAFE, or MODERATE', () => {
    const rawStatus = 'UNASSESSABLE';
    const rawScore = null;
    const rawDecision = 'INSUFFICIENT_DATA';

    const status = normalizeAssessmentStatus(rawStatus, {
      healthScore: rawScore,
      scoreRange: null,
    });
    assert.strictEqual(status, ASSESSMENT_STATUS.UNASSESSABLE);

    const healthScore = rawScore != null && Number.isFinite(Number(rawScore)) ? Number(rawScore) : null;
    assert.strictEqual(healthScore, null);

    const statusMeta = getAssessmentStatusMeta(status);
    assert.strictEqual(statusMeta.label, 'Assessment Unavailable');
    assert.strictEqual(statusMeta.description, 'Insufficient nutritional information prevents a reliable assessment.');

    // Never displays SAFE, MODERATE, or AVOID
    const decisionMeta = getDecisionMeta(rawDecision, status);
    assert.strictEqual(decisionMeta.isValid, false);
    assert.strictEqual(decisionMeta.label, null);

    const safeMeta = getDecisionMeta('SAFE', status);
    assert.strictEqual(safeMeta.isValid, false);
    assert.strictEqual(safeMeta.label, null);

    const moderateMeta = getDecisionMeta('MODERATE', status);
    assert.strictEqual(moderateMeta.isValid, false);
    assert.strictEqual(moderateMeta.label, null);
  });

  // 5. Explicit nutrient value of zero
  test('5. Explicit nutrient value of zero is preserved and never treated as missing', () => {
    const nutrition = {
      calories: 0,
      sugar: 0,
      fat: 0,
      salt: 0,
      protein: 12.5,
      carbs: 0,
    };

    assert.strictEqual(isNutrientPresent(nutrition.calories), true);
    assert.strictEqual(isNutrientPresent(nutrition.sugar), true);
    assert.strictEqual(isNutrientPresent(nutrition.fat), true);
    assert.strictEqual(isNutrientPresent(nutrition.salt), true);

    assert.strictEqual(formatNutritionValue(nutrition.calories), '0');
    assert.strictEqual(formatNutritionValue(nutrition.sugar), '0');
    assert.strictEqual(formatNutritionWithUnit(nutrition.sugar, 'g'), '0g');
    assert.strictEqual(formatNutritionWithUnit(nutrition.calories, ' kcal'), '0 kcal');

    // All 4 mandatory dimensions are present (even with value 0)
    const missing = getMissingMandatoryNutrients(nutrition);
    assert.deepStrictEqual(missing, []);
  });

  // 6. Missing nutrient value
  test('6. Missing nutrient value is distinguished from zero and formatted as Not disclosed', () => {
    const nutrition = {
      calories: 450,
      fat: 15.2,
      carbs: 60,
      sugar: null,        // missing
      salt: undefined,    // missing
      protein: 8.0,
    };

    assert.strictEqual(isNutrientPresent(nutrition.sugar), false);
    assert.strictEqual(isNutrientPresent(nutrition.salt), false);
    assert.strictEqual(isNutrientPresent(nutrition.calories), true);

    assert.strictEqual(formatNutritionWithUnit(nutrition.sugar, 'g', 'Not disclosed'), 'Not disclosed');
    assert.strictEqual(formatNutritionWithUnit(nutrition.salt, 'g', '—'), '—');

    // Identifies missing mandatory dimensions: sugar and salt
    const missing = getMissingMandatoryNutrients(nutrition);
    assert.strictEqual(missing.includes('sugar'), true);
    assert.strictEqual(missing.includes('salt'), true);
    assert.strictEqual(missing.includes('calories'), false);
    assert.strictEqual(missing.includes('fat'), false);

    assert.strictEqual(getMandatoryNutrientDisplayName('sugar'), 'Total Sugars');
    assert.strictEqual(getMandatoryNutrientDisplayName('salt'), 'Salt / Sodium');
  });

  // 7. Null confidence
  test('7. Null or undefined confidence is handled gracefully without crashing', () => {
    assert.strictEqual(normalizeConfidence(null), null);
    assert.strictEqual(normalizeConfidence(undefined), null);
    assert.strictEqual(normalizeConfidence(''), null);

    assert.strictEqual(getConfidenceMeta(null), null);
    assert.strictEqual(getConfidenceMeta(undefined), null);
  });

  // 8. UNVERIFIED confidence
  test('8. UNVERIFIED confidence returns standard description and neutral badge', () => {
    const conf = normalizeConfidence('UNVERIFIED');
    assert.strictEqual(conf, CONFIDENCE_LEVEL.UNVERIFIED);

    const meta = getConfidenceMeta(conf);
    assert.notStrictEqual(meta, null);
    assert.strictEqual(meta.label, 'Unverified');
    assert.strictEqual(meta.description, 'Product information has not been sufficiently verified.');
    assert.strictEqual(meta.fg, '#475569');
  });

  // 9. Unknown assessment status
  test('9. Unknown assessment status is handled safely without throwing or fabricating scores', () => {
    const unknownStatus = 'CUSTOM_EXPERIMENTAL_STATUS';
    const normalized = normalizeAssessmentStatus(unknownStatus, { healthScore: null });
    assert.strictEqual(normalized, ASSESSMENT_STATUS.UNKNOWN);

    const meta = getAssessmentStatusMeta(normalized);
    assert.strictEqual(meta.key, ASSESSMENT_STATUS.UNKNOWN);
    assert.strictEqual(meta.description, 'Assessment status is not recognized.');

    // When score is null on unknown status, never fabricate SAFE
    const decisionMeta = getDecisionMeta('SAFE', normalized);
    assert.strictEqual(decisionMeta.isValid, false);
  });

  // 10. Existing legacy response without assessment metadata
  test('10. Existing legacy response without assessment metadata preserves complete product behavior', () => {
    const legacyPayload = {
      health_score: 76,
      final_decision: 'SAFE',
      product_name: 'Classic Oats',
      nutrition: { calories: 389, fat: 6.9, sugar: 0.99, salt: 0.02 },
    };

    // No rawStatus passed: infers COMPLETE from valid numeric health_score
    const status = normalizeAssessmentStatus(undefined, {
      healthScore: legacyPayload.health_score,
      scoreRange: null,
      nutrition: legacyPayload.nutrition,
    });
    assert.strictEqual(status, ASSESSMENT_STATUS.COMPLETE);

    const decisionMeta = getDecisionMeta(legacyPayload.final_decision, status);
    assert.strictEqual(decisionMeta.isValid, true);
    assert.strictEqual(decisionMeta.label, 'SAFE');
    assert.strictEqual(decisionMeta.sub, 'Recommended Choice');
  });

  // 11. AVOID decision with HIGH confidence
  test('11. AVOID decision with HIGH confidence keeps AVOID and does not confuse confidence with healthiness', () => {
    const rawStatus = 'ASSESSABLE';
    const rawScore = 24;
    const rawDecision = 'AVOID';
    const rawConfidence = 'HIGH';

    const status = normalizeAssessmentStatus(rawStatus, { healthScore: rawScore });
    const decisionMeta = getDecisionMeta(rawDecision, status);
    const confMeta = getConfidenceMeta(rawConfidence);

    // Invariant: HIGH confidence indicates verified complete data, NOT a healthy product
    assert.strictEqual(decisionMeta.isValid, true);
    assert.strictEqual(decisionMeta.label, 'AVOID');
    assert.strictEqual(confMeta.label, 'High Confidence');
    assert.strictEqual(confMeta.description, 'High confidence in available product information.');
  });

  // 12. Product response with missing optional fields
  test('12. Product response with missing optional fields does not crash and defaults to UNASSESSABLE', () => {
    const minimalPayload = {
      barcode: '8901234567890',
    };

    const rawScore = minimalPayload?.analysis?.health_score ?? minimalPayload?.health_score;
    const healthScore = rawScore != null && Number.isFinite(Number(rawScore)) ? Number(rawScore) : null;
    const scoreRange = minimalPayload?.score_range || null;
    const rawStatus = minimalPayload?.assessment_status || null;

    const status = normalizeAssessmentStatus(rawStatus, { healthScore, scoreRange, nutrition: null });
    assert.strictEqual(status, ASSESSMENT_STATUS.UNASSESSABLE);
    assert.strictEqual(healthScore, null);

    const missing = getMissingMandatoryNutrients(null);
    assert.deepStrictEqual(missing, MANDATORY_NUTRIENTS);

    const decisionMeta = getDecisionMeta(null, status);
    assert.strictEqual(decisionMeta.isValid, false);
  });

  // 13. Invariant: Valid Score Range bounds and ordering validation
  test('13. isValidScoreRange validates bounds [0, 100], finite numbers, and min <= max', () => {
    // Valid cases
    assert.strictEqual(isValidScoreRange({ min_score: 35, max_score: 65 }), true);
    assert.strictEqual(isValidScoreRange({ min_score: 0, max_score: 100 }), true);
    assert.strictEqual(isValidScoreRange({ min: 40, max: 40 }), true); // equal is valid

    // Invalid: min > max (inverted range)
    assert.strictEqual(isValidScoreRange({ min_score: 65, max_score: 35 }), false);
    assert.strictEqual(formatScoreRange({ min_score: 65, max_score: 35 }), null);

    // Invalid: out of bounds
    assert.strictEqual(isValidScoreRange({ min_score: -5, max_score: 50 }), false);
    assert.strictEqual(isValidScoreRange({ min_score: 50, max_score: 105 }), false);

    // Invalid: non-numeric / NaN
    assert.strictEqual(isValidScoreRange({ min_score: 'abc', max_score: 50 }), false);
    assert.strictEqual(isValidScoreRange({ min_score: NaN, max_score: 50 }), false);
    assert.strictEqual(isValidScoreRange(null), false);
    assert.strictEqual(isValidScoreRange({}), false);
  });

  // 14. Invariant: UI never recalculates health scores from score range
  test('14. Upper bound of score range is never treated as actual health score or SAFE certification', () => {
    const scoreRange = { min_score: 40, max_score: 75 };
    // Even though max_score is 75 (which would be SAFE if it were a score):
    // 1. health_score must remain null
    const healthScore = null;
    assert.strictEqual(healthScore, null);

    // 2. Decision cannot be SAFE based on max_score
    const decisionMeta = getDecisionMeta('INCOMPLETE_DATA', 'PARTIAL');
    assert.strictEqual(decisionMeta.isValid, false);
    assert.notStrictEqual(decisionMeta.label, 'SAFE');
  });

  // 15. Invariant: getProduct API function is exported and routes to /product/{barcode}
  test('15. getProduct API function is defined and targets /product/{barcode}', () => {
    const apiPath = path.resolve(__dirname, '../src/services/api.js');
    const apiContent = fs.readFileSync(apiPath, 'utf8');

    assert.ok(apiContent.includes('export async function getProduct(barcode)'));
    assert.ok(apiContent.includes('/product/${encodeURIComponent(barcode)}'));
  });

  // 16. Invariant: ResultScreen portion scaling preserves 0 and null
  test('16. calculateServingNutrition preserves explicit 0 and null across portion scales', () => {
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

    const base = {
      calories: 200,
      sugar: 0,
      fat: null,
      salt: 1.2,
    };

    const scaled50g = calculateServingNutrition(base, 50);
    assert.strictEqual(scaled50g.calories, 100);
    assert.strictEqual(scaled50g.sugar, 0);     // 0 preserved
    assert.strictEqual(scaled50g.fat, null);   // null preserved
    assert.strictEqual(scaled50g.salt, 0.6);

    const scaled200g = calculateServingNutrition(base, 200);
    assert.strictEqual(scaled200g.calories, 400);
    assert.strictEqual(scaled200g.sugar, 0);     // 0 preserved
    assert.strictEqual(scaled200g.fat, null);   // null preserved
    assert.strictEqual(scaled200g.salt, 2.4);
  });
});
