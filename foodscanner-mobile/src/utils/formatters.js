/**
 * PRAMAAN Nutrition Presentation Formatter
 *
 * Rules:
 * - All displayed nutrition values show a maximum of 2 decimal places.
 * - Whole numbers display cleanly without trailing .00 (e.g. 133, 0).
 * - Floats round to at most 2 decimal places:
 *     133.333333333333 kcal -> 133.33 kcal
 *     1.33333333333333 g -> 1.33 g
 *     30.6666666666667 g -> 30.67 g
 *     29.3333333333333 g -> 29.33 g
 *     0.666666666666667 g -> 0.67 g
 *     2.36666666666667 g -> 2.37 g
 * - Missing, null, undefined, or NaN safely fall back to specified fallback string.
 * - Formats values at the UI presentation layer without modifying stored/calculated payloads.
 */

export function formatNutritionValue(val, fallback = '0') {
  if (val === null || val === undefined || val === '') {
    return fallback;
  }
  const num = typeof val === 'number' ? val : Number(val);
  if (!Number.isFinite(num) || Number.isNaN(num)) {
    return fallback;
  }
  const rounded = Math.round((num + Number.EPSILON) * 100) / 100;
  return Number(rounded.toFixed(2)).toString();
}

export function formatNutritionWithUnit(val, unit = '', fallback = '—') {
  if (val === null || val === undefined || val === '') {
    return fallback;
  }
  const num = typeof val === 'number' ? val : Number(val);
  if (!Number.isFinite(num) || Number.isNaN(num)) {
    return fallback;
  }
  const rounded = Math.round((num + Number.EPSILON) * 100) / 100;
  const str = Number(rounded.toFixed(2)).toString();
  return `${str}${unit}`;
}

export default formatNutritionValue;
