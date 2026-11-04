/**
 * Pure OCR Polling Lifecycle, State Machine & Extraction Utilities
 * Source of Truth for Asynchronous OCR Job Integration in PRAMAAN Mobile
 */

export const OCR_STATUS = {
  PENDING: 'pending',
  PROCESSING: 'processing',
  COMPLETED: 'completed',
  FAILED: 'failed',
};

export const OCR_UI_STATE = {
  IDLE: 'IDLE',
  SUBMITTING: 'SUBMITTING',
  POLLING: 'POLLING',
  SUCCESS: 'SUCCESS',
  EMPTY: 'EMPTY',
  FAILED: 'FAILED',
};

/**
 * Checks whether an OCR result payload contains meaningful nutrition facts or is empty.
 * @param {Object|null} result - Result object returned from OCR job or synchronous call.
 * @returns {boolean} True if no usable nutrition values were detected.
 */
export function isOcrResultEmpty(result) {
  if (!result || typeof result !== 'object') {
    return true;
  }

  // Known backend terminal extraction failure status values
  const terminalFailStatuses = ['NO_TEXT', 'INVALID_IMAGE', 'EXTRACTION_FAILED'];
  if (terminalFailStatuses.includes(result.status)) {
    return true;
  }

  const nutrientKeys = ['calories', 'fat', 'saturated_fat', 'sugar', 'salt', 'protein', 'fiber', 'carbs'];
  const hasAtLeastOneNutrient = nutrientKeys.some((k) => {
    const val = result[k];
    return val !== null && val !== undefined && !Number.isNaN(Number(val));
  });

  return !hasAtLeastOneNutrient;
}

/**
 * Extracts normalized nutrition fields from an OCR response for display and form prefilling.
 * @param {Object|null} result
 * @returns {Object}
 */
export function extractOcrNutrientValues(result) {
  if (!result || typeof result !== 'object') {
    return {
      product_name: '',
      calories: null,
      fat: null,
      saturated_fat: null,
      sugar: null,
      salt: null,
      protein: null,
      fiber: null,
      carbs: null,
      confidence: null,
      raw_text: '',
      ocr_version: null,
      claim_verification: null,
    };
  }

  const satFatVal = result.saturated_fat != null
    ? result.saturated_fat
    : (result.nutrition?.saturated_fat_g?.value != null ? result.nutrition.saturated_fat_g.value : null);

  return {
    product_name: result.product_name || '',
    calories: result.calories != null ? Number(result.calories) : null,
    fat: result.fat != null ? Number(result.fat) : null,
    saturated_fat: satFatVal != null ? Number(satFatVal) : null,
    sugar: result.sugar != null ? Number(result.sugar) : null,
    salt: result.salt != null ? Number(result.salt) : null,
    protein: result.protein != null ? Number(result.protein) : null,
    fiber: result.fiber != null ? Number(result.fiber) : null,
    carbs: result.carbs != null ? Number(result.carbs) : null,
    confidence: result.confidence != null ? result.confidence : (result.nutrition_confidence || null),
    raw_text: result.raw_text || '',
    ocr_version: result.ocr_version || null,
    claim_verification: result.claim_verification || null,
  };
}

/**
 * Promise-based sleep that immediately rejects if the provided AbortSignal fires.
 * @param {number} ms - Milliseconds to sleep.
 * @param {AbortSignal} [signal] - Optional AbortSignal.
 * @returns {Promise<void>}
 */
export function sleepWithAbort(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      const err = new Error('Aborted');
      err.name = 'AbortError';
      return reject(err);
    }

    let timerId = null;

    const onAbort = () => {
      if (timerId) clearTimeout(timerId);
      const err = new Error('Aborted');
      err.name = 'AbortError';
      reject(err);
    };

    if (signal && typeof signal.addEventListener === 'function') {
      signal.addEventListener('abort', onAbort, { once: true });
    }

    timerId = setTimeout(() => {
      if (signal && typeof signal.removeEventListener === 'function') {
        signal.removeEventListener('abort', onAbort);
      }
      resolve();
    }, ms);
  });
}

/**
 * Core polling lifecycle engine. Sequentially queries status endpoint with bounded timeout,
 * transient error resilience, abort cancellation, and progress notifications.
 *
 * @param {Function} fetchStatusFn - Function(jobId, { signal }) returning Promise<{ status, result, error, ... }>
 * @param {string} jobId - The unique OCR job identifier.
 * @param {Object} [options]
 * @param {number} [options.intervalMs=1500] - Polling interval in ms.
 * @param {number} [options.timeoutMs=45000] - Hard bounded polling timeout in ms.
 * @param {number} [options.maxConsecutiveTransientErrors=3] - Max transient network retries.
 * @param {Function} [options.onProgress] - Callback ({ status, job, elapsedMs, attempt }) on each status poll.
 * @param {AbortSignal} [options.signal] - Signal to cleanly cancel polling on unmount or user action.
 * @param {Function} [options.nowFn=Date.now] - Clock function for testing.
 * @param {Function} [options.sleepFn=sleepWithAbort] - Sleeper function for testing.
 * @returns {Promise<Object>} The completed job response object.
 */
export async function pollOcrJobWithClient(fetchStatusFn, jobId, options = {}) {
  if (!jobId || typeof jobId !== 'string' || !jobId.trim()) {
    throw new Error('jobId is required and must be a non-empty string');
  }

  const {
    intervalMs = 1500,
    timeoutMs = 45000,
    maxConsecutiveTransientErrors = 3,
    onProgress = null,
    signal = null,
    nowFn = Date.now,
    sleepFn = sleepWithAbort,
  } = options;

  const startTime = nowFn();
  let attempt = 0;
  let consecutiveErrors = 0;

  while (true) {
    if (signal?.aborted) {
      const err = new Error('OCR job polling cancelled by user');
      err.name = 'AbortError';
      throw err;
    }

    const elapsedMs = nowFn() - startTime;
    if (elapsedMs >= timeoutMs) {
      const timeoutError = new Error(
        `OCR processing timed out after ${Math.round(timeoutMs / 1000)} seconds. Please try again or enter details manually.`
      );
      timeoutError.code = 'ETIMEDOUT';
      throw timeoutError;
    }

    attempt += 1;
    let jobData;

    try {
      jobData = await fetchStatusFn(jobId, { signal });
      consecutiveErrors = 0; // Reset consecutive transient errors on successful response
    } catch (err) {
      if (signal?.aborted || err?.name === 'AbortError' || err?.name === 'CanceledError') {
        const cancelErr = new Error('OCR job polling cancelled');
        cancelErr.name = 'AbortError';
        throw cancelErr;
      }

      const status = err?.response?.status;
      // 400, 401, 403, 404 are non-retryable terminal client errors
      if (status && (status === 400 || status === 401 || status === 403 || status === 404)) {
        throw err;
      }

      consecutiveErrors += 1;
      if (consecutiveErrors > maxConsecutiveTransientErrors) {
        throw err;
      }

      // Transient retry delay
      await sleepFn(intervalMs, signal);
      continue;
    }

    if (!jobData || typeof jobData !== 'object') {
      throw new Error('Malformed OCR job status response received from backend');
    }

    // Check terminal job states
    if (jobData.status === OCR_STATUS.COMPLETED) {
      if (typeof onProgress === 'function') {
        try {
          onProgress({ status: OCR_STATUS.COMPLETED, job: jobData, elapsedMs: nowFn() - startTime, attempt });
        } catch (_cbErr) {
          // Progress listener errors should never break polling resolution
        }
      }
      return jobData;
    }

    if (jobData.status === OCR_STATUS.FAILED) {
      const msg = jobData.error || 'OCR processing failed. Please try again or enter details manually.';
      const failError = new Error(msg);
      failError.code = 'JOB_FAILED';
      failError.job = jobData;
      throw failError;
    }

    // Intermediate state (pending or processing)
    if (typeof onProgress === 'function') {
      try {
        onProgress({ status: jobData.status, job: jobData, elapsedMs: nowFn() - startTime, attempt });
      } catch (_cbErr) {
        // Ignore progress listener errors
      }
    }

    // Wait before next poll tick
    await sleepFn(intervalMs, signal);
  }
}
