# PRAMAAN / FoodScanner AI — System Architecture & Implementation

PRAMAAN AI is the core intelligence engine powering food health scoring, NutriScore prediction, ingredient hazard detection, and nutrition tracking.

---

## 1. System Components

### A. FastAPI Application Layer (`api/main.py`)
- **Route Registration:** 23 structured REST endpoints with Pydantic request validation and response models.
- **Runtime Configuration & Startup Validation Gate (`services/config.py`):** Automatically discovers and loads `.env` across local directories (`foodscanner-ai/.env`, project root `.env`). Startup event runs `validate_runtime_config(raise_error=True)`, ensuring required `SECRET_KEY` is present and non-empty. Prevents runtime 500 errors during authentication by failing fast at boot with clear configuration guidance.
- **Security Middleware:** Strict CORS origin resolution with dynamic LAN regex support for Expo development clients (`_get_allowed_origins`).
- **Authentication Dependency:** Injected `get_current_user` enforcing HMAC-SHA256 JWT tokens with OpenAPI `HTTPBearer` authorization. Swagger UI includes full interactive Bearer authentication modal.

### B. Machine Learning & NutriScore Engine (`ml_model/`)
- **Pipeline:** Gradient Boosting, Random Forest, SVM, and weighted ensemble classification models (`ensemble_model.pkl`).
- **Feature Vector:** `[calories, fat, sugar, salt, protein, fiber, carbs]` (7-dimensional continuous vector).
- **Validation:** **82.69% accuracy on held-out test dataset**.
- **Determinism:** `predict_nutriscore()` provides deterministic class predictions (grades `A` through `E`); `predict_nutriscore_details()` outputs full class probability distributions summing to 1.0.
- **Fail-Safe Integrity:** Missing or corrupted model artifacts raise explicit `ModelArtifactError` rather than silently degrading into heuristics.

### C. Database & Data Persistence (`database/`)
- **Engine:** SQLite for local execution, PostgreSQL for containerized cloud deployment (`render.yaml`).
- **Migration System:** Automated runtime schema migration (`init_db.py`) adds missing columns to SQLite/PostgreSQL tables without breaking active databases.
- **Portability:** Path-independent database resolution in `orm.py` resolving relative SQLite database paths across any execution CWD.
- **Scan ≠ Eat Isolation:** Scans write strictly to `scan_history`; food intake writes strictly to `daily_food_log`.

### D. Nutrition Analysis & Decision Engines (`services/`)
- **Standardized Salt Threshold:** Standardized across `food_health_score.py` and `decision_explainer.py` at `1.5g / 100g`.
- **Diet-Aware Penalties:** Configurable multipliers for low-sodium, diabetic, clean eating, and high-protein diet goals.
- **Ingredient & Additive Parser:** Token-boundary regex matching (`\b`) prevents substring false positives (e.g., "msg" inside "message") and eliminates redundant flag reporting.
- **Recommendations Engine:** Infers food categories (`infer_food_category`) and calculates deterministic % differences in sugar, salt, fat, and calories.

### E. OpenFoodFacts Client (`services/openfoodfacts_service.py`)
- Structured barcode lookup against OpenFoodFacts API with timeout and network error handling.
- Automatically normalizes sodium to salt (`salt = sodium * 2.5`) and energy kJ to kcal (`kcal = kJ / 4.184`).

### F. OCR Processing (`api/main.py`)
- Dual-engine OCR pipeline with EasyOCR preference and Tesseract fallback.
- Auto-rotates, upscales, and binarizes label images before applying regular expression pattern extractors for nutritional rows.

### G. Health Claim Verification Engine (`services/claim_verification.py`)
- **Deterministic FSSAI Compliance:** Verifies front-of-pack and marketing claims against declared product nutrition, ingredients, and official statutory criteria from the *Food Safety and Standards (Advertising and Claims) Regulations, 2018* (Schedule I, Schedule II, and Regulation 4(5)).
- **Versioned Regulatory Rules:** Rules are defined as declarative data configurations (`ClaimRule`, `RegulatorySource`) in `REGULATORY_RULES`, completely separating regulatory criteria and source citations from verification execution logic.
- **Normalization Pipeline:** Normalizes casing, whitespace, hyphens, and lexical variations ("sugar-free", "0 sugar", "no-added-sugar", "rich in protein") into canonical claim keys.
- **Evidence-Based Auditing:** Outputs structured numerical evidence, thresholds, comparison operators, data quality flags, and regulatory source references. Never guesses or converts missing data into verified claims.
- **Verification Statuses:**
  - `SUPPORTED`: Product data satisfies official regulatory threshold.
  - `NOT_SUPPORTED`: Product data explicitly violates regulatory threshold.
  - `NEEDS_REVIEW`: Claim is unmapped, ambiguous, or requires legal interpretation.
  - `INSUFFICIENT_DATA`: Required nutrient or ingredient declaration is missing.
- **Key Distinctions:**
  - **Health Score:** Continuous numerical score (0-100) evaluating global nutritional balance (calories, sugar, salt, fat, protein, fiber).
  - **Health Claim Verification:** Deterministic legal/regulatory assessment of specific packaged marketing statements against FSSAI statutory limits.
  - **Ingredient Analysis:** Qualitative NLP/token-based scan identifying additives, preservatives, artificial sweeteners, and allergens.
- **Regulatory Rule Maintenance:** Future FSSAI notifications can be accommodated simply by appending or modifying entries in `REGULATORY_RULES` with updated version strings and effective dates without altering verification code.
- **Statutory Disclaimer:** *The system provides a rule-based assessment of product claims based on available product data and referenced regulatory criteria. It is not a legal certification.*

---

## 2. API Endpoints Reference

### Public Endpoints
- `GET /health` — Service heartbeat verification.
- `GET /docs` — Interactive OpenAPI Swagger UI.
- `GET /openapi.json` — Raw OpenAPI schema.
- `POST /register` — Account registration with email, password, and calorie budget.
- `POST /login` — User authentication returning JWT Bearer token.

### Protected Endpoints (Bearer JWT Required)
- `POST /verify-claims` — Deterministic FSSAI health claim verification against product data / database barcode.
- `POST /scan` — Barcode scanning, health scoring, healthier alternatives, and optional additive claim verification (Scan ≠ Eat).
- `POST /analyze` — Ad-hoc nutrition profile scoring.
- `POST /ocr` — Nutrition label image OCR analysis.
- `POST /food-log` — Explicit food consumption logging.
- `GET /today` — Today's consumed calories and meal log.
- `GET /history` — User scan history.
- `DELETE /history/{id}` — Delete scan history record.
- `GET /stats` — User scan counts and health score averages.
- `GET /user/profile` — User profile, body metrics, and diet preferences.
- `PUT /user/profile` — Update user profile and diet settings.
- `GET /report/daily` — Daily intake summary.
- `GET /report/weekly` — 7-day trend analysis and score metrics.
- `GET /report/goal` — Calorie goal tracking.
- `POST /compare` — Head-to-head product nutrition comparison.
- `GET /search` — Fuzzy product search.
- `GET /product/{barcode}` — Database product lookup.
- `GET /explain/{barcode}` — Explainable scoring factor breakdown.

---

## 3. Testing & Verification

Run the full 57-test backend suite from either the project root or the `foodscanner-ai` folder:

```bash
# From repository root:
.\foodscanner-ai\.venv\Scripts\python.exe -m pytest foodscanner-ai/tests -v

# From foodscanner-ai:
pytest tests -v
```

**Results:** 57 passed, 0 failed, 0 errors.
