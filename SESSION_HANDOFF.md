# PRAMAAN / FoodScanner AI — Session Handoff & Source of Truth

**Generated:** 2026-10-28 02:25 IST  
**Repository Root:** `/Users/sandeshshinde/ml-scanner-project/`  
**Current Branch:** `main`  
**Latest Local Commit:** `20b6141` (`fix: improve OCR reliability and image loading`)  

---

## 1. Project Overview & Current Architecture

PRAMAAN (FoodScanner AI) is an Indian packaged food intelligence platform combining deterministic health assessment with regulatory claim verification and grounded nutritional guidance.

### Architecture Stack
- **Backend (`foodscanner-ai`):**
  - Framework: FastAPI (Python 3.11.17 via `.venv311`)
  - Database: SQLite (`database/foodscanner.db`) with SQLAlchemy ORM and Alembic migrations; PostgreSQL connection-pool support for production.
  - Algorithms: Deterministic Health Score Engine (0–100 scale), Rule-based Claim Verifier (FSSAI 2018 Schedule I), Ingredient & Additive Risk Analyzer (INS database), Async OCR Pipeline (Tesseract Engine).
  - Search Engine: Unified multi-source product search combining SQLite database, canonical Indian packaged foods, and local OpenFoodFacts dataset (37,444 rows).
  - AI RAG Pipeline: Hybrid TF-IDF (`scikit-learn`) + RapidFuzz topic-matching retriever, multi-turn prompt builder with strict tag isolation, guardrails, and modular LLM providers (`MockLLMProvider`, `GeminiProvider`, `OpenAIProvider`).
- **Frontend (`foodscanner-mobile`):**
  - Framework: React Native 0.81.5 with Expo SDK 54 (`expo-router` / React Navigation 7 native stack + bottom tabs).
  - Design Language: Neo-Brutalist design system (cream canvas `#FAF7F2`, ink borders `#111827`, hard unblurred offset shadows, vivid status accents).
  - Client Services: Axios client with JWT bearer interceptor, in-flight 401 latch and session reset, auto host-IP resolution for Expo web and native devices.

---

## 2. Features Implemented & Verified in This Session

### A. Fixed Pramaan AI Navigation & Dedicated Assistant Screen
- **Problem:** Tapping "Ask AI" on `HomeScreen.js` redirected to the camera barcode scanner (`Scan`). No assistant screen was registered.
- **Solution:**
  - Created [`foodscanner-mobile/src/screens/AIAssistantScreen.js`](file:///Users/sandeshshinde/ml-scanner-project/foodscanner-mobile/src/screens/AIAssistantScreen.js) with Neo-Brutalist UI, message history, suggested question chips, loading spinner, retry cards, and active product context banner.
  - Registered screen in [`AppNavigator.js`](file:///Users/sandeshshinde/ml-scanner-project/foodscanner-mobile/src/navigation/AppNavigator.js) inside authenticated stack with `headerShown: false`.
  - Updated `HomeScreen.js` quick-action button to navigate to `'AIAssistant'`.
  - Preserved standard back navigation with `navigation.goBack()`.

### B. Global Home Screen Product Search
- **Problem:** Users could only scan barcodes or manually enter nutrition; there was no way to search products by name, brand, or ingredient on the home screen.
- **Solution:**
  - Added debounced search bar (350ms delay, minimum 2 characters) on `HomeScreen.js`.
  - Built unified search in [`services/product_search.py`](file:///Users/sandeshshinde/ml-scanner-project/foodscanner-ai/services/product_search.py) querying SQLite products + nutrition, canonical Indian packaged foods, and the local OpenFoodFacts dataset.
  - Implemented dual routing: standard numeric barcodes (8–14 digits) call `scanProduct()`; synthetic Indian dataset barcodes (`IND-...`) call `analyzeManualProduct()`, navigating to `ResultScreen` with full health analysis and zero regex errors.

### C. Search Security, Wildcard Escaping & Performance Hardening
- **Problem:** SQL LIKE queries did not escape literal `%` or `_`, and `_csv_fuzzy_lookup` re-read `indian_packaged_foods.csv` from disk on every `/compare` and `/alternatives` request.
- **Solution:**
  - Added SQLite `ESCAPE '/'` to `_search_local_db` and escaped `/`, `%`, `_` in Python.
  - Added thread-safe singleton loading with `threading.Lock()` to `get_openfoodfacts_df()`.
  - Added module-level caching `_INDIAN_FOODS_CACHE` in `api/main.py`.

### D. RAG Tokenization Fix & Grounding Alignment
- **Problem:** Naive whitespace `.split()` left punctuation attached to tokens (e.g. `'biscuits?'` vs `'biscuits,'` or `'(ins'` vs `'ins'`), preventing token overlap detection. Suggested prompt chip in mobile UI asked a question that was not in the knowledge base.
- **Solution:**
  - Updated [`services/rag_service.py`](file:///Users/sandeshshinde/ml-scanner-project/foodscanner-ai/services/rag_service.py) to use `re.findall(r"\b[a-zA-Z0-9_-]+\b", ...)` for query and document tokenization.
  - Added minimum query length guard (`len(q) < 3` returns `[]`) to prevent spurious single-letter partial matches.
  - Aligned mobile suggested prompt chip #1 to `'What are the limits for fats and saturated fatty acids?'`, which matches actual knowledge-base documents (`NUTR-FAT-005` & `REG-FSSAI-011`).

---

## 3. Important Files Changed and Their Purpose

| File | Status | Lines Changed | Purpose |
| :--- | :--- | :--- | :--- |
| `foodscanner-ai/api/main.py` | Modified | +30 / -17 | Added `limit` param to `/search`; added `_INDIAN_FOODS_CACHE` to avoid disk reads in fuzzy lookup. |
| `foodscanner-ai/services/product_search.py` | Modified | +358 / -79 | Unified search across SQLite and OpenFoodFacts; thread-safe caching; SQL wildcard escaping. |
| `foodscanner-ai/services/rag_service.py` | Modified | +7 / -2 | Regex word tokenization; query length guard `< 3`. |
| `foodscanner-ai/tests/test_rag_hardening.py` | Modified | +19 / -0 | Added tests for unindexed topic insufficient-evidence handling and punctuation resilience. |
| `foodscanner-ai/tests/test_search_enhancements.py` | Created (untracked) | +134 | 9 integration tests covering search, brands, ingredients, OpenFoodFacts, injection, wildcards, auth. |
| `foodscanner-mobile/src/navigation/AppNavigator.js` | Modified | +2 / -0 | Registered `AIAssistant` route in root stack. |
| `foodscanner-mobile/src/screens/HomeScreen.js` | Modified | +401 / -1 | Search bar UI, 350ms debounce, loading/empty/error states, result cards, dual barcode routing. |
| `foodscanner-mobile/src/screens/AIAssistantScreen.js` | Created (untracked) | +674 | Complete conversational AI assistant screen with Neo-Brutalist design. |
| `foodscanner-mobile/tests/feature_search_and_ai.test.js` | Created (untracked) | +216 | 11 mobile unit tests covering navigation, RAG response extraction, prompts, barcode routing. |

---

## 4. Test Results: Verified in This Session

All test results recorded below were **actually executed in this session**:

### Backend Tests (`foodscanner-ai`)
- **Command:** `cd foodscanner-ai && .venv311/bin/pytest`
- **Result:** **307 passed, 2 skipped, 0 failed** in 11.88s
- **Baseline:** 296 passed, 2 skipped
- **Net Increase:** +11 verified tests (9 in `test_search_enhancements.py` + 2 in `test_rag_hardening.py`)
- **Verified Status:** 100% PASS, zero regressions.

### Mobile Logic & Unit Tests (`foodscanner-mobile`)
- **Command:** `cd foodscanner-mobile && node --test tests/mobile_logic.test.js`
- **Result:** **94 passed, 0 failed** across 17 suites
- **Verified Status:** 100% PASS.

### Mobile Feature Tests (`foodscanner-mobile`)
- **Command:** `cd foodscanner-mobile && node --test tests/feature_search_and_ai.test.js`
- **Result:** **11 passed, 0 failed** across 2 suites
- **Verified Status:** 100% PASS.

### Combined Mobile Suite
- **Command:** `cd foodscanner-mobile && node --test tests/*.test.js`
- **Result:** **105 passed, 0 failed** across 19 suites in 229ms
- **Verified Status:** 100% PASS.

### Mobile Linter
- **Command:** `cd foodscanner-mobile && npm run lint`
- **Result:** **0 errors, 0 warnings** (clean `expo lint`)
- **Verified Status:** Clean.

### Whitespace & Git Diff Integrity
- **Command:** `git diff --check`
- **Result:** Clean (exit code 0).

---

## 5. RAG Implementation: Knowledge Base & Grounding Invariants

### Architecture
- **Retriever:** `KnowledgeRetriever` in `services/rag_service.py`
- **Scoring Formula:** Hybrid similarity = `(0.70 * tfidf_score) + (0.20 * topic_sim) + (0.10 * text_sim) + direct_term_boost`
- **Threshold:** `min_score = 0.20`, top `k = 3`
- **Indexing:** 27 verified chunks across 3 JSON files in `foodscanner-ai/knowledge_base/`:
  - `nutrition_faq.json` (8 docs): Sugars vs added sugar, health score calculation, high protein paradox, weight loss, muscle building, NutriScore, high risk additives, hidden sodium.
  - `nutrition_guidelines.json` (7 docs): Energy balance, sugar limits, protein RDA, dietary fibre, fats and fatty acids, sodium limits, serving size rules.
  - `regulatory_guidelines.json` (12 docs): Sugar-free, low-sugar, no added sugar, low-fat, fat-free, low-sodium, high-fibre, source of fibre, high-protein, source of protein, zero trans-fat, FSSAI license labeling.

### Grounding & Insufficient-Evidence Policy
- **Verified Invariant:** Queries covering topics outside the 27 knowledge base documents (e.g. `Is palm oil healthy in biscuits?`) return `retrieved_context_count: 0`, `sources: []`, and the honest disclaimer:
  > *"Sufficient verified evidence was not found in the local knowledge base for this query. The PRAMAAN AI Assistant provides factual nutrition information strictly grounded in verified FSSAI, ICMR-NIN, and WHO guidelines."*
- **Audit Clarification:** The palm oil query returns 0 documents because "palm" and "oil" do not exist in the 27 knowledge base files. This is not a retrieval bug; it is the intended safety behavior of the Grounded RAG architecture. No fabricated answer should ever be returned.

---

## 6. AI Provider & Model Configuration

- **Active Provider:** Configurable via `AI_PROVIDER` environment variable in `foodscanner-ai/.env`:
  - `mock` *(current default for local testing & demos)*: Uses `MockLLMProvider` which formats retrieved knowledge chunks directly without external API calls.
  - `gemini`: Uses `GeminiProvider` targeting model `gemini-1.5-flash` via Google Generative Language API (`/v1beta/models/{model}:generateContent`).
  - `openai`: Uses `OpenAIProvider` targeting model `gpt-4o-mini` (or local Ollama/LocalAI via `AI_BASE_URL`).
- **Exact Model IDs:**
  - Google Gemini: `gemini-1.5-flash`
  - OpenAI: `gpt-4o-mini`
  - Mock: `deterministic-mock-v1`
- **Pending Task — Gemini Flash Billing:**
  - In `.env`, `AI_PROVIDER` is currently set to `mock`.
  - When switching to `AI_PROVIDER=gemini`, a valid `AI_API_KEY` must be supplied, and the Google Cloud / Google AI Studio project must have billing enabled or sufficient free-tier quota. This verification is pending production deployment setup.

---

## 7. Current Git State (Uncommitted Changes)

- **Branch:** `main`
- **Tracking:** 2 commits ahead of `origin/main` (local cleanup and OCR fixes)
- **Status:** 6 tracked files modified, 3 untracked files created. Zero staged changes.
```
 M foodscanner-ai/api/main.py
 M foodscanner-ai/services/product_search.py
 M foodscanner-ai/services/rag_service.py
 M foodscanner-ai/tests/test_rag_hardening.py
 M foodscanner-mobile/src/navigation/AppNavigator.js
 M foodscanner-mobile/src/screens/HomeScreen.js
?? foodscanner-ai/tests/test_search_enhancements.py
?? foodscanner-mobile/src/screens/AIAssistantScreen.js
?? foodscanner-mobile/tests/feature_search_and_ai.test.js
```

---

## 8. Exact Next Steps for Next Session (In Priority Order)

1. **Commit Approved Changes:**
   Review `git diff` and stage the 9 files for a single local commit:
   ```bash
   git add foodscanner-ai/api/main.py \
           foodscanner-ai/services/product_search.py \
           foodscanner-ai/services/rag_service.py \
           foodscanner-ai/tests/test_rag_hardening.py \
           foodscanner-ai/tests/test_search_enhancements.py \
           foodscanner-mobile/src/navigation/AppNavigator.js \
           foodscanner-mobile/src/screens/HomeScreen.js \
           foodscanner-mobile/src/screens/AIAssistantScreen.js \
           foodscanner-mobile/tests/feature_search_and_ai.test.js
   git commit -m "feat: add pramaan ai assistant screen and global product search"
   ```
2. **Gemini Flash Billing & Live Provider Setup (Optional):**
   If live Gemini LLM generation is desired instead of offline mock:
   - Obtain a Google AI Studio API key with verified quota/billing.
   - Update `foodscanner-ai/.env` with `AI_PROVIDER=gemini` and `AI_API_KEY=<key>`.
   - Test live `POST /chat` to verify remote completion.
3. **Knowledge Base Scope Expansion (Optional):**
   If the team wants questions about cooking oils (palm oil, mustard oil, sunflower oil) answered natively:
   - Ingest an official ICMR-NIN guideline chunk on *Dietary Fats and Edible Vegetable Oils* into `foodscanner-ai/knowledge_base/nutrition_guidelines.json`.
4. **SQLite FTS5 Optimization (Optional):**
   Migrate `large_openfoodfacts_cleaned.csv` into an on-disk SQLite FTS5 table to reduce RAM footprint from 25.6 MB to zero.

---

## 9. Local Development Commands & URLs

### Active Running Services
- **FastAPI Backend:** Running on `http://localhost:8000` (docs: `http://localhost:8000/docs`)
- **Expo Web Client:** Running on `http://localhost:8081`

### Service Start Commands
```bash
# Backend (Python 3.11):
cd foodscanner-ai && .venv311/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000

# Mobile Web / Native (Expo 54):
cd foodscanner-mobile && npx expo start --web --port 8081
```

### Test Commands
```bash
# Backend pytest suite (307 tests):
cd foodscanner-ai && .venv311/bin/pytest

# Mobile Node test suite (105 tests across 19 suites):
cd foodscanner-mobile && node --test tests/*.test.js

# Mobile Linter:
cd foodscanner-mobile && npm run lint

# Whitespace check:
git diff --check
```
