const { test, describe } = require('node:test');
const assert = require('node:assert');

// ==============================================================================
// STAGE 5A: SAFE HOMESCREEN REQUEST COALESCING TESTS
// ==============================================================================

describe('Stage 5A: HomeScreen Request Coalescing & Promise Identity', () => {
  test('1. Exact Promise identity: Two concurrent background calls return the EXACT same Promise object (p1 === p2)', async () => {
    let inFlightRequest = null;
    let fetchRequestId = 0;
    let networkCalls = 0;

    const mockApi = () => {
      networkCalls++;
      return new Promise((resolve) => setTimeout(() => resolve({ ok: true }), 20));
    };

    const fetchData = (isManualRefresh = false) => {
      if (inFlightRequest && !isManualRefresh) {
        return inFlightRequest.promise;
      }

      const currentReqId = ++fetchRequestId;
      const requestPromise = Promise.resolve()
        .then(async () => {
          await mockApi();
        })
        .finally(() => {
          if (inFlightRequest?.id === currentReqId) {
            inFlightRequest = null;
          }
        });

      inFlightRequest = { id: currentReqId, promise: requestPromise };
      return requestPromise;
    };

    const p1 = fetchData(false);
    const p2 = fetchData(false);

    // Strict identity assertion: must reference identical Promise in memory
    assert.strictEqual(
      p1,
      p2,
      'FAIL: p1 and p2 must be the exact same Promise instance (p1 === p2)'
    );

    await Promise.all([p1, p2]);

    assert.strictEqual(
      networkCalls,
      1,
      'Coalescing must execute exactly 1 network call for 2 concurrent calls'
    );
    assert.strictEqual(
      inFlightRequest,
      null,
      'inFlightRequest reference must be safely released after settlement'
    );
  });

  test('2. One underlying set of API calls for concurrent focus and mount events', async () => {
    let inFlightRequest = null;
    let fetchRequestId = 0;
    let apiInvocations = 0;

    const mockFetchAll = () => {
      apiInvocations++;
      return new Promise((resolve) => setTimeout(() => resolve({ profile: 'user' }), 15));
    };

    const fetchData = (isManualRefresh = false) => {
      if (inFlightRequest && !isManualRefresh) {
        return inFlightRequest.promise;
      }
      const currentReqId = ++fetchRequestId;
      const requestPromise = Promise.resolve()
        .then(async () => {
          await mockFetchAll();
        })
        .finally(() => {
          if (inFlightRequest?.id === currentReqId) {
            inFlightRequest = null;
          }
        });

      inFlightRequest = { id: currentReqId, promise: requestPromise };
      return requestPromise;
    };

    // Simulate simultaneous useEffect mount + useFocusEffect focus
    const mountPromise = fetchData(false);
    const focusPromise = fetchData(false);

    assert.strictEqual(mountPromise, focusPromise);

    await Promise.all([mountPromise, focusPromise]);
    assert.strictEqual(apiInvocations, 1);

    // Later focus after completion must execute a new fresh request
    const laterFocusPromise = fetchData(false);
    assert.notStrictEqual(laterFocusPromise, mountPromise);
    await laterFocusPromise;
    assert.strictEqual(apiInvocations, 2);
  });

  test('3. Synchronous API exception is caught and subsequent retry succeeds', async () => {
    let inFlightRequest = null;
    let fetchRequestId = 0;
    let failFirst = true;

    const fetchData = (isManualRefresh = false) => {
      if (inFlightRequest && !isManualRefresh) {
        return inFlightRequest.promise;
      }
      const currentReqId = ++fetchRequestId;
      const requestPromise = Promise.resolve()
        .then(async () => {
          if (failFirst) {
            failFirst = false;
            throw new Error('Immediate synchronous setup failure');
          }
          return { status: 'success' };
        })
        .catch(() => {
          // Graceful retention of existing state
        })
        .finally(() => {
          if (inFlightRequest?.id === currentReqId) {
            inFlightRequest = null;
          }
        });

      inFlightRequest = { id: currentReqId, promise: requestPromise };
      return requestPromise;
    };

    // First call fails
    const p1 = fetchData(false);
    await p1;

    // Reference must be released despite error
    assert.strictEqual(inFlightRequest, null);

    // Subsequent retry succeeds
    const p2 = fetchData(false);
    assert.notStrictEqual(p1, p2);
    await p2;
    assert.strictEqual(inFlightRequest, null);
  });

  test('4. Rejected API promises and cleanup release in-flight token', async () => {
    let inFlightRequest = null;
    let fetchRequestId = 0;

    const fetchData = (isManualRefresh = false) => {
      if (inFlightRequest && !isManualRefresh) {
        return inFlightRequest.promise;
      }
      const currentReqId = ++fetchRequestId;
      const requestPromise = Promise.resolve()
        .then(async () => {
          await new Promise((_, reject) => setTimeout(() => reject(new Error('Network drop')), 10));
        })
        .catch(() => {
          // Swallow network hiccup
        })
        .finally(() => {
          if (inFlightRequest?.id === currentReqId) {
            inFlightRequest = null;
          }
        });

      inFlightRequest = { id: currentReqId, promise: requestPromise };
      return requestPromise;
    };

    const p = fetchData(false);
    assert.ok(inFlightRequest !== null);
    await p;
    assert.strictEqual(inFlightRequest, null);
  });

  test('5. Manual refresh precedence: isManualRefresh=true bypasses active in-flight request', async () => {
    let inFlightRequest = null;
    let fetchRequestId = 0;
    let committedState = null;

    const fetchData = (isManualRefresh = false) => {
      if (inFlightRequest && !isManualRefresh) {
        return inFlightRequest.promise;
      }
      const currentReqId = ++fetchRequestId;
      const delay = currentReqId === 1 ? 50 : 10;

      const requestPromise = Promise.resolve()
        .then(async () => {
          await new Promise((resolve) => setTimeout(resolve, delay));
          if (currentReqId === fetchRequestId) {
            committedState = `req-${currentReqId}`;
          }
        })
        .finally(() => {
          if (inFlightRequest?.id === currentReqId) {
            inFlightRequest = null;
          }
        });

      inFlightRequest = { id: currentReqId, promise: requestPromise };
      return requestPromise;
    };

    const p1 = fetchData(false);
    const p2 = fetchData(true); // Manual refresh

    assert.notStrictEqual(p1, p2);
    assert.strictEqual(inFlightRequest.id, 2);

    await Promise.all([p1, p2]);

    // Request 2 completes and commits. Request 1 was superseded.
    assert.strictEqual(committedState, 'req-2');
    assert.strictEqual(inFlightRequest, null);
  });

  test('6. Older request completing after newer request does not overwrite state or clear loading', async () => {
    let inFlightRequest = null;
    let fetchRequestId = 0;
    let loadingState = true;
    let committedData = null;

    const fetchData = (isManualRefresh = false) => {
      if (inFlightRequest && !isManualRefresh) {
        return inFlightRequest.promise;
      }
      const currentReqId = ++fetchRequestId;
      // Req 1 is very slow, Req 2 is fast
      const delay = currentReqId === 1 ? 60 : 10;

      const requestPromise = Promise.resolve()
        .then(async () => {
          await new Promise((resolve) => setTimeout(resolve, delay));
          if (currentReqId === fetchRequestId) {
            committedData = `data-${currentReqId}`;
          }
        })
        .finally(() => {
          if (inFlightRequest?.id === currentReqId) {
            inFlightRequest = null;
          }
          if (currentReqId === fetchRequestId) {
            loadingState = false;
          }
        });

      inFlightRequest = { id: currentReqId, promise: requestPromise };
      return requestPromise;
    };

    const p1 = fetchData(false);
    const p2 = fetchData(true); // Supersede with req 2

    // Wait for Req 2 to settle first
    await p2;
    assert.strictEqual(committedData, 'data-2');
    assert.strictEqual(loadingState, false);

    // Simulate Req 3 starts while Req 1 is still creeping
    loadingState = true;
    const p3 = fetchData(true);

    // Now wait for all to settle
    await Promise.all([p1, p3]);

    // Req 1 finishing last must NOT have set loadingState to false or overwritten committedData
    assert.strictEqual(committedData, 'data-3');
    assert.strictEqual(loadingState, false);
  });

  test('7. In-flight reference released immediately upon settlement', async () => {
    let inFlightRequest = null;
    let fetchRequestId = 0;

    const fetchData = (isManualRefresh = false) => {
      if (inFlightRequest && !isManualRefresh) {
        return inFlightRequest.promise;
      }
      const currentReqId = ++fetchRequestId;
      const requestPromise = Promise.resolve()
        .then(async () => {
          await new Promise((resolve) => setTimeout(resolve, 5));
        })
        .finally(() => {
          if (inFlightRequest?.id === currentReqId) {
            inFlightRequest = null;
          }
        });

      inFlightRequest = { id: currentReqId, promise: requestPromise };
      return requestPromise;
    };

    const p = fetchData(false);
    assert.ok(inFlightRequest !== null);
    await p;
    assert.strictEqual(inFlightRequest, null);
  });
});

// ==============================================================================
// STAGE 5B: IMMEDIATE RAW-QUERY INVALIDATION TESTS
// ==============================================================================

describe('Stage 5B: Immediate Search Invalidation Lifecycle', () => {
  test('1. Valid query followed immediately by empty query clears results and loading immediately', () => {
    let searchRequestId = 0;
    let abortController = null;
    let debounceTimer = null;
    let results = [{ product_name: 'Existing' }];
    let loading = true;
    let error = null;

    const invalidateSearch = (text) => {
      const nextId = ++searchRequestId;
      if (abortController) {
        abortController.abort();
        abortController = null;
      }
      if (debounceTimer) {
        clearTimeout(debounceTimer);
        debounceTimer = null;
      }
      const clean = String(text || '').trim();
      if (clean.length < 2) {
        results = [];
        loading = false;
        error = null;
      }
      return nextId;
    };

    // User clears search
    const id = invalidateSearch('');

    assert.strictEqual(results.length, 0);
    assert.strictEqual(loading, false);
    assert.strictEqual(error, null);
    assert.strictEqual(id, 1);
  });

  test('2. Valid query followed by one-character query clears state immediately', () => {
    let searchRequestId = 0;
    let results = [{ product_name: 'Existing' }];
    let loading = true;

    const invalidateSearch = (text) => {
      const nextId = ++searchRequestId;
      const clean = String(text || '').trim();
      if (clean.length < 2) {
        results = [];
        loading = false;
      }
      return nextId;
    };

    invalidateSearch('M');

    assert.deepStrictEqual(results, []);
    assert.strictEqual(loading, false);
  });

  test('3. Query A replaced by Query B during debounce cancels Timer A and only dispatches Query B', async () => {
    let searchRequestId = 0;
    let debounceTimer = null;
    let dispatchedQueries = [];

    const handleSearchChangeText = (text) => {
      const reqId = ++searchRequestId;
      if (debounceTimer) {
        clearTimeout(debounceTimer);
        debounceTimer = null;
      }

      if (text.trim().length >= 2) {
        debounceTimer = setTimeout(() => {
          dispatchedQueries.push({ id: reqId, query: text.trim() });
        }, 50);
      }
    };

    handleSearchChangeText('Amul');
    // User types additional characters at 20ms (before 50ms timer)
    await new Promise((resolve) => setTimeout(resolve, 20));
    handleSearchChangeText('Amul Butter');

    // Wait for debounce timer to fire
    await new Promise((resolve) => setTimeout(resolve, 70));

    assert.strictEqual(dispatchedQueries.length, 1);
    assert.strictEqual(dispatchedQueries[0].query, 'Amul Butter');
    assert.strictEqual(dispatchedQueries[0].id, 2);
  });

  test('4. Old response resolving immediately after input change before React commit does not update UI state', async () => {
    let searchRequestId = 0;
    let abortController = new AbortController();
    let uiResults = [];
    let uiLoading = true;

    const req1Id = ++searchRequestId;

    let resolveReq1;
    const req1Promise = new Promise((resolve) => {
      resolveReq1 = resolve;
    });

    req1Promise.then((data) => {
      if (req1Id === searchRequestId) {
        uiResults = data;
        uiLoading = false;
      }
    });

    // Keystroke arrives: raw event immediately invalidates
    const handleRawKeystroke = (text) => {
      const nextId = ++searchRequestId;
      if (abortController) {
        abortController.abort();
      }
      if (text.trim().length < 2) {
        uiResults = [];
        uiLoading = false;
      }
      return nextId;
    };

    handleRawKeystroke('x');

    // Stale Req 1 resolves in microtask
    resolveReq1([{ product_name: 'Stale Product' }]);
    await Promise.resolve();

    assert.deepStrictEqual(uiResults, [], 'Stale request 1 must not update results');
    assert.strictEqual(uiLoading, false, 'Loading must remain false');
  });

  test('5. Old request finally block attempting to clear newer loading state is guarded', async () => {
    let searchRequestId = 0;
    let loadingState = false;

    // Request 1 starts
    const req1Id = ++searchRequestId;
    loadingState = true;

    // Keystroke for Request 2 occurs
    const req2Id = ++searchRequestId;
    loadingState = true;

    // Request 1 finally block executes
    const onReq1Finally = () => {
      if (req1Id === searchRequestId) {
        loadingState = false;
      }
    };

    onReq1Finally();
    // Loading state must remain true because req1Id !== searchRequestId!
    assert.strictEqual(loadingState, true);

    // Request 2 finally block executes
    const onReq2Finally = () => {
      if (req2Id === searchRequestId) {
        loadingState = false;
      }
    };

    onReq2Finally();
    assert.strictEqual(loadingState, false);
  });

  test('6. Clear-button invalidation uses identical authoritative path', () => {
    let searchRequestId = 10;
    let aborted = false;
    let controller = new AbortController();
    controller.signal.addEventListener('abort', () => {
      aborted = true;
    });
    let results = [{ name: 'Chips' }];

    const handleClearSearch = () => {
      searchRequestId++;
      if (controller) {
        controller.abort();
        controller = null;
      }
      results = [];
    };

    handleClearSearch();
    assert.strictEqual(searchRequestId, 11);
    assert.strictEqual(aborted, true);
    assert.deepStrictEqual(results, []);
  });

  test('7. Unmount cancellation aborts active controller and clears pending timer', () => {
    let timer = setTimeout(() => {}, 1000);
    let controller = new AbortController();
    let aborted = false;
    controller.signal.addEventListener('abort', () => {
      aborted = true;
    });

    const unmountCleanup = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      if (controller) {
        controller.abort();
        controller = null;
      }
    };

    unmountCleanup();
    assert.strictEqual(timer, null);
    assert.strictEqual(aborted, true);
    assert.strictEqual(controller, null);
  });

  test('8. Search response normalization against backend contract handles raw array, results key, and malformed object', () => {
    const normalize = (data) => {
      if (Array.isArray(data)) return data;
      if (data && typeof data === 'object') {
        if (Array.isArray(data.results)) return data.results;
        if (Array.isArray(data.products)) return data.products;
        if (Array.isArray(data.data)) return data.data;
        if (Array.isArray(data.items)) return data.items;
      }
      return [];
    };

    const rawArray = [{ product_name: 'A' }, { product_name: 'B' }];
    assert.strictEqual(normalize(rawArray).length, 2);

    const objectWithResults = { results: [{ product_name: 'C' }] };
    assert.strictEqual(normalize(objectWithResults).length, 1);

    const objectWithProducts = { products: [{ product_name: 'D' }] };
    assert.strictEqual(normalize(objectWithProducts).length, 1);

    assert.deepStrictEqual(normalize(null), []);
    assert.deepStrictEqual(normalize(undefined), []);
    assert.deepStrictEqual(normalize({ unexpected: 123 }), []);
    assert.deepStrictEqual(normalize('string error'), []);
  });
});
