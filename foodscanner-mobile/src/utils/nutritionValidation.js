/**
 * Manual Nutrition Entry Form Validation & Sanitization
 * Enforces strict non-negative numeric constraints, reasonable bounds, and
 * prevents silent conversion of invalid inputs to zero or null.
 */

export function validateNumericField(val, fieldName, maxVal, unit = 'g') {
  if (val === null || val === undefined) return { value: null, error: null };
  const s = String(val).trim();
  if (s === '') return { value: null, error: null };

  // Reject negative numbers
  if (s.startsWith('-')) {
    return { value: null, error: `${fieldName} cannot be negative` };
  }

  // Must match non-negative integer or float (e.g. 10, 10.5, 0.4)
  if (!/^\d+(\.\d+)?$/.test(s)) {
    return { value: null, error: `${fieldName} must be a valid number` };
  }

  const num = Number(s);
  if (!Number.isFinite(num) || Number.isNaN(num)) {
    return { value: null, error: `${fieldName} must be a valid number` };
  }

  if (num < 0) {
    return { value: null, error: `${fieldName} cannot be negative` };
  }

  if (maxVal != null && num > maxVal) {
    return { value: null, error: `${fieldName} cannot exceed ${maxVal}${unit}` };
  }

  return { value: num, error: null };
}

export function validateManualNutritionForm(fields = {}) {
  const errors = {};
  const values = {};

  // 1. Product Name validation
  const rawName = String(fields.productName || '').trim();
  if (!rawName) {
    errors.productName = 'Product name is required';
  } else if (rawName.length > 120) {
    errors.productName = 'Product name cannot exceed 120 characters';
  } else {
    values.product_name = rawName;
  }

  // 2. Numeric Field Validations
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
    if (res.error) {
      errors[rule.key] = res.error;
    } else {
      values[rule.targetKey || rule.key] = res.value;
      if (res.value !== null) {
        hasAtLeastOneNutrient = true;
      }
    }
  }

  // 3. Consistency checks
  if (values.fat != null && values.saturated_fat != null && values.saturated_fat > values.fat) {
    errors.saturatedFat = 'Saturated fat cannot exceed total fat';
  }

  if (values.carbs != null && values.sugar != null && values.sugar > values.carbs) {
    errors.sugar = 'Total sugar cannot exceed carbohydrates';
  }

  // 4. At least one nutrient value requirement
  if (!hasAtLeastOneNutrient && !errors.calories) {
    errors.calories = 'Please enter calories or at least one nutrient value';
  }

  const isValid = Object.keys(errors).length === 0;

  return {
    isValid,
    errors,
    values: isValid ? values : null,
  };
}

export { formatNutritionValue, formatNutritionWithUnit } from './formatters';
