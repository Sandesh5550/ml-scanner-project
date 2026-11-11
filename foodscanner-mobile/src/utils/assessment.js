/**
 * PRAMAAN Assessment & Confidence Utilities (Phase 7 Alignment)
 *
 * Provides authoritative domain normalization, validation, and presentation
 * metadata for assessment status, confidence levels, score ranges, missing
 * nutrients, and decision validity.
 */

export const ASSESSMENT_STATUS = {
  COMPLETE: 'COMPLETE',
  PARTIAL: 'PARTIAL',
  UNASSESSABLE: 'UNASSESSABLE',
  UNKNOWN: 'UNKNOWN',
};

export const CONFIDENCE_LEVEL = {
  HIGH: 'HIGH',
  MEDIUM: 'MEDIUM',
  LOW: 'LOW',
  UNVERIFIED: 'UNVERIFIED',
};

export const MANDATORY_NUTRIENTS = ['calories', 'sugar', 'fat', 'salt'];

/**
 * Validate a score range object received from the backend.
 * @param {Object} range - Object containing min_score/max_score (or min/max).
 * @returns {boolean} True only if both bounds are finite numbers, within [0, 100], and min <= max.
 */
export function isValidScoreRange(range) {
  if (!range || typeof range !== 'object') return false;
  const rawMin = range.min_score !== undefined ? range.min_score : range.min;
  const rawMax = range.max_score !== undefined ? range.max_score : range.max;

  if (rawMin === null || rawMin === undefined || rawMax === null || rawMax === undefined) {
    return false;
  }

  const min = typeof rawMin === 'number' ? rawMin : Number(rawMin);
  const max = typeof rawMax === 'number' ? rawMax : Number(rawMax);

  if (!Number.isFinite(min) || !Number.isFinite(max)) return false;
  if (min < 0 || max > 100) return false;
  if (min > max) return false;

  return true;
}

/**
 * Format a validated score range for display.
 * @param {Object} range
 * @param {string} [prefix='Estimated score range: ']
 * @returns {string|null} Formatted range string or null if invalid.
 */
export function formatScoreRange(range, prefix = 'Estimated score range: ') {
  if (!isValidScoreRange(range)) return null;
  const rawMin = range.min_score !== undefined ? range.min_score : range.min;
  const rawMax = range.max_score !== undefined ? range.max_score : range.max;
  const min = Math.round(Number(rawMin));
  const max = Math.round(Number(rawMax));
  return `${prefix}${min}–${max}`;
}

/**
 * Normalize raw assessment status string into canonical enum.
 * Supports legacy backend responses by inferring status from healthScore and scoreRange.
 *
 * @param {string} rawStatus - The assessment_status from the backend.
 * @param {Object} [fallbackContext]
 * @param {number|null} [fallbackContext.healthScore]
 * @param {Object|null} [fallbackContext.scoreRange]
 * @returns {string} One of ASSESSMENT_STATUS values.
 */
export function normalizeAssessmentStatus(rawStatus, { healthScore = null, scoreRange = null } = {}) {
  if (rawStatus !== null && rawStatus !== undefined && String(rawStatus).trim() !== '') {
    const s = String(rawStatus).trim().toUpperCase();
    if (s === 'COMPLETE' || s === 'ASSESSABLE') {
      return ASSESSMENT_STATUS.COMPLETE;
    }
    if (s === 'PARTIAL' || s === 'PARTIALLY_ASSESSABLE') {
      return ASSESSMENT_STATUS.PARTIAL;
    }
    if (s === 'UNASSESSABLE') {
      return ASSESSMENT_STATUS.UNASSESSABLE;
    }
    if (s === 'UNVERIFIED') {
      return ASSESSMENT_STATUS.UNASSESSABLE;
    }
    return ASSESSMENT_STATUS.UNKNOWN;
  }

  // Legacy fallback when assessment_status was not sent
  const isNumericScore = healthScore !== null && healthScore !== undefined && Number.isFinite(Number(healthScore));
  if (isNumericScore) {
    return ASSESSMENT_STATUS.COMPLETE;
  }
  if (isValidScoreRange(scoreRange)) {
    return ASSESSMENT_STATUS.PARTIAL;
  }
  if (healthScore === null || healthScore === undefined) {
    return ASSESSMENT_STATUS.UNASSESSABLE;
  }
  return ASSESSMENT_STATUS.UNKNOWN;
}

/**
 * Normalize raw confidence level string.
 * @param {string} rawConfidence
 * @returns {string|null} Canonical CONFIDENCE_LEVEL or null if missing/invalid.
 */
export function normalizeConfidence(rawConfidence) {
  if (!rawConfidence) return null;
  const c = String(rawConfidence).trim().toUpperCase();
  if (c === CONFIDENCE_LEVEL.HIGH) return CONFIDENCE_LEVEL.HIGH;
  if (c === CONFIDENCE_LEVEL.MEDIUM) return CONFIDENCE_LEVEL.MEDIUM;
  if (c === CONFIDENCE_LEVEL.LOW) return CONFIDENCE_LEVEL.LOW;
  if (c === CONFIDENCE_LEVEL.UNVERIFIED) return CONFIDENCE_LEVEL.UNVERIFIED;
  return null;
}

/**
 * Get presentation metadata for a confidence level.
 * @param {string} confidence
 * @returns {Object|null} Metadata object or null if confidence is missing/unrecognized.
 */
export function getConfidenceMeta(confidence) {
  const norm = normalizeConfidence(confidence);
  if (!norm) return null;

  switch (norm) {
    case CONFIDENCE_LEVEL.HIGH:
      return {
        key: CONFIDENCE_LEVEL.HIGH,
        label: 'High Confidence',
        shortLabel: 'HIGH',
        description: 'High confidence in available product information.',
        fg: '#15803D',
        bg: '#DCFCE7',
        border: '#86EFAC',
        icon: 'shield-checkmark',
      };
    case CONFIDENCE_LEVEL.MEDIUM:
      return {
        key: CONFIDENCE_LEVEL.MEDIUM,
        label: 'Medium Confidence',
        shortLabel: 'MEDIUM',
        description: 'Some information may be incomplete.',
        fg: '#0284C7',
        bg: '#E0F2FE',
        border: '#7DD3FC',
        icon: 'information-circle',
      };
    case CONFIDENCE_LEVEL.LOW:
      return {
        key: CONFIDENCE_LEVEL.LOW,
        label: 'Low Confidence',
        shortLabel: 'LOW',
        description: 'Limited information is available.',
        fg: '#D97706',
        bg: '#FEF3C7',
        border: '#FCD34D',
        icon: 'alert-circle',
      };
    case CONFIDENCE_LEVEL.UNVERIFIED:
      return {
        key: CONFIDENCE_LEVEL.UNVERIFIED,
        label: 'Unverified',
        shortLabel: 'UNVERIFIED',
        description: 'Product information has not been sufficiently verified.',
        fg: '#475569',
        bg: '#F1F5F9',
        border: '#CBD5E1',
        icon: 'help-circle',
      };
    default:
      return null;
  }
}

/**
 * Get presentation metadata for an assessment status.
 * @param {string} status
 * @returns {Object} Metadata object.
 */
export function getAssessmentStatusMeta(status) {
  const norm = normalizeAssessmentStatus(status);

  switch (norm) {
    case ASSESSMENT_STATUS.COMPLETE:
      return {
        key: ASSESSMENT_STATUS.COMPLETE,
        label: 'Complete Assessment',
        description: 'Full mandatory nutritional disclosures verified.',
        fg: '#15803D',
        bg: '#DCFCE7',
        border: '#86EFAC',
        badgeBg: '#15803D',
        badgeFg: '#FFFFFF',
        icon: 'checkmark-circle',
      };
    case ASSESSMENT_STATUS.PARTIAL:
      return {
        key: ASSESSMENT_STATUS.PARTIAL,
        label: 'Partial Assessment',
        description: 'Some nutritional information is missing.',
        fg: '#B45309',
        bg: '#FEF3C7',
        border: '#FCD34D',
        badgeBg: '#D97706',
        badgeFg: '#FFFFFF',
        icon: 'warning',
      };
    case ASSESSMENT_STATUS.UNASSESSABLE:
      return {
        key: ASSESSMENT_STATUS.UNASSESSABLE,
        label: 'Assessment Unavailable',
        description: 'Insufficient nutritional information prevents a reliable assessment.',
        fg: '#475569',
        bg: '#F1F5F9',
        border: '#CBD5E1',
        badgeBg: '#64748B',
        badgeFg: '#FFFFFF',
        icon: 'close-circle',
      };
    default:
      return {
        key: ASSESSMENT_STATUS.UNKNOWN,
        label: 'Status: ' + (status || 'Unknown'),
        description: 'Assessment status is not recognized.',
        fg: '#64748B',
        bg: '#F8FAFC',
        border: '#E2E8F0',
        badgeBg: '#64748B',
        badgeFg: '#FFFFFF',
        icon: 'help-circle-outline',
      };
  }
}

/**
 * Identify missing mandatory nutritional dimensions.
 * Explicit numeric zero (0) is strictly treated as present and valid.
 *
 * @param {Object} nutrition - Nutrition dictionary (per 100g).
 * @param {Array<string>} [backendMissingList] - Optional pre-computed missing list.
 * @returns {Array<string>} Array of missing mandatory nutrient keys ('calories', 'sugar', 'fat', 'salt').
 */
export function getMissingMandatoryNutrients(nutrition, backendMissingList = null) {
  if (Array.isArray(backendMissingList) && backendMissingList.length > 0) {
    const mapped = new Set();
    for (const item of backendMissingList) {
      const s = String(item).toLowerCase().trim();
      if (s === 'calories' || s === 'calorie' || s === 'energy') mapped.add('calories');
      else if (s === 'sugar' || s === 'sugars') mapped.add('sugar');
      else if (s === 'fat' || s === 'total_fat') mapped.add('fat');
      else if (s === 'salt' || s === 'sodium') mapped.add('salt');
      else if (s) mapped.add(s);
    }
    return Array.from(mapped);
  }

  const missing = [];
  const n = nutrition || {};

  const isCaloriesMissing = n.calories === null || n.calories === undefined || (typeof n.calories === 'number' && isNaN(n.calories));
  if (isCaloriesMissing) missing.push('calories');

  const isSugarMissing = n.sugar === null || n.sugar === undefined || (typeof n.sugar === 'number' && isNaN(n.sugar));
  if (isSugarMissing) missing.push('sugar');

  const isFatMissing = n.fat === null || n.fat === undefined || (typeof n.fat === 'number' && isNaN(n.fat));
  if (isFatMissing) missing.push('fat');

  const hasSalt = n.salt !== null && n.salt !== undefined && !isNaN(Number(n.salt));
  const hasSodium = n.sodium !== null && n.sodium !== undefined && !isNaN(Number(n.sodium));
  if (!hasSalt && !hasSodium) missing.push('salt');

  return missing;
}

/**
 * Map canonical nutrient keys to human-friendly display labels.
 * @param {string} key
 * @returns {string}
 */
export function getMandatoryNutrientDisplayName(key) {
  switch (String(key).toLowerCase().trim()) {
    case 'calories':
      return 'Calories';
    case 'sugar':
      return 'Total Sugars';
    case 'fat':
      return 'Total Fat';
    case 'salt':
      return 'Salt / Sodium';
    case 'saturated_fat':
      return 'Saturated Fat';
    case 'trans_fat':
      return 'Trans Fat';
    case 'protein':
      return 'Protein';
    case 'carbs':
      return 'Carbohydrates';
    case 'fiber':
      return 'Dietary Fiber';
    default:
      return key.charAt(0).toUpperCase() + key.slice(1);
  }
}

/**
 * Resolve whether a decision badge should be displayed, and provide its presentation metadata.
 *
 * Invariants:
 * - COMPLETE: SAFE, MODERATE, AVOID are valid.
 * - PARTIAL: Only AVOID is valid! Never display SAFE or MODERATE.
 * - UNASSESSABLE: Never display SAFE, MODERATE, or AVOID.
 * - UNKNOWN: Never display SAFE or MODERATE unless score is verified complete.
 *
 * @param {string} decision - Raw final_decision from backend.
 * @param {string} [assessmentStatus] - Normalized or raw assessment status.
 * @returns {Object} { isValid: boolean, label: string|null, color: string, bg: string, sub: string }
 */
export function getDecisionMeta(decision, assessmentStatus = null) {
  const normStatus = assessmentStatus ? normalizeAssessmentStatus(assessmentStatus) : null;
  const d = String(decision || '').trim().toUpperCase();

  // 1. UNASSESSABLE or UNKNOWN products NEVER show SAFE or MODERATE
  if (normStatus === ASSESSMENT_STATUS.UNASSESSABLE || normStatus === ASSESSMENT_STATUS.UNKNOWN) {
    if (d === 'AVOID') {
      return {
        isValid: true,
        label: 'AVOID',
        color: '#DC2626',
        bg: '#FEE2E2',
        sub: 'High Risk Profile',
      };
    }
    return {
      isValid: false,
      label: null,
      color: '#64748B',
      bg: '#F1F5F9',
      sub: normStatus === ASSESSMENT_STATUS.UNASSESSABLE ? 'Assessment Unavailable' : 'Status Unrecognized',
    };
  }

  // 2. PARTIAL products: only display if the backend decisively mandates AVOID
  if (normStatus === ASSESSMENT_STATUS.PARTIAL) {
    if (d === 'AVOID') {
      return {
        isValid: true,
        label: 'AVOID',
        color: '#DC2626',
        bg: '#FEE2E2',
        sub: 'High Risk Profile (Worst-Case / Mandatory Rule)',
      };
    }
    // Partial cannot be certified as SAFE or MODERATE
    return {
      isValid: false,
      label: null,
      color: '#B45309',
      bg: '#FEF3C7',
      sub: 'Decision Pending Complete Nutrition',
    };
  }

  // 3. COMPLETE assessments (or default legacy)
  if (d === 'SAFE') {
    return {
      isValid: true,
      label: 'SAFE',
      color: '#15803D',
      bg: '#DCFCE7',
      sub: 'Recommended Choice',
    };
  }
  if (d === 'MODERATE') {
    return {
      isValid: true,
      label: 'MODERATE',
      color: '#D97706',
      bg: '#FEF3C7',
      sub: 'Consume In Moderation',
    };
  }
  if (d === 'AVOID') {
    return {
      isValid: true,
      label: 'AVOID',
      color: '#DC2626',
      bg: '#FEE2E2',
      sub: 'High Risk Profile',
    };
  }

  return {
    isValid: false,
    label: null,
    color: '#64748B',
    bg: '#F1F5F9',
    sub: 'Status Unspecified',
  };
}
