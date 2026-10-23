# PRAMAAN — AI Food Scanner (ml-scanner-project)

[![CI / Test Suite](https://img.shields.io/badge/Backend%20Tests-77%20Passing-brightgreen)](file:///c:/Users/student/Desktop/devside/ml-scanner-project/foodscanner-ai/tests)
[![Mobile Tests](https://img.shields.io/badge/Mobile%20Tests-5%20Passing-brightgreen)](file:///c:/Users/student/Desktop/devside/ml-scanner-project/foodscanner-mobile/tests)
[![Expo SDK](https://img.shields.io/badge/Expo%20SDK-54-blue)](file:///c:/Users/student/Desktop/devside/ml-scanner-project/foodscanner-mobile/package.json)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue)](file:///c:/Users/student/Desktop/devside/ml-scanner-project/foodscanner-ai/pyproject.toml)

PRAMAAN is an intelligent nutrition intelligence platform designed to decode packaged foods for health-conscious consumers. By scanning barcodes or nutrition fact labels, PRAMAAN analyzes nutrients, detects risky ingredients and additives, generates explainable health scores, provides healthier product alternatives, deterministically verifies front-of-pack regulatory health claims against official FSSAI standards, and tracks personal food consumption.

---

## 1. Project Purpose & Core Invariant

### Purpose
Consumers frequently purchase packaged foods without understanding misleading claims, hidden sugars, harmful additives, or excess sodium. PRAMAAN provides instant, scientific, transparent nutritional clarity.

### Core Domain Invariant: Scan ≠ Eat
**Scanning or analyzing a food product must never automatically log consumption.**
- **Scanning (`POST /scan` or barcode lookup)** queries product intelligence and logs an entry strictly to `scan_history`.
- **Consumption (`POST /food-log`)** is only recorded when the user explicitly triggers an intake action (e.g. tapping `+ Log to Daily Diary` on mobile).

---

## 2. Architecture Overview

```text
ml-scanner-project/
├── foodscanner-ai/                 # Backend API, Database, and ML Engine
│   ├── api/main.py                 # FastAPI application, CORS, route definitions
│   ├── database/                   # SQLite/PostgreSQL schemas, ORM models, auto-migrations
│   ├── ml_model/                   # NutriScore classification pipeline and model artifacts
│   ├── services/                   # Business logic (scoring, OFF, OCR, auth, recommendations)
│   ├── datasets/                   # Packaged food dataset tables (Indian + OFF)
│   └── tests/                      # Pytest test suite (Batches 1 to 4)
│
├── foodscanner-mobile/             # Client Application (Expo SDK 54 / React Native 0.81)
│   ├── App.js                      # Root entry and session bootstrap
│   ├── src/screens/                # UI screens (Scan, Result, OCR, Reports, Profile, Login)
│   ├── src/services/api.js         # Axios client, dynamic LAN host routing, 401 interceptor
│   ├── src/context/AuthContext.js  # Authentication state & token management
│   └── tests/                      # Mobile pure-logic test suite
│
├── docs/                           # Architecture, checklists, and audit documentation
└── render.yaml                     # Render deployment blueprint (Web, Static, PostgreSQL)
```

---

## 3. Component Details

### A. Core Backend & Database
- **Framework:** FastAPI with Uvicorn.
- **ORM:** SQLAlchemy declarative models with automated database schema migration (`init_db.py`).
- **Database Support:** Dual compatibility for SQLite (local development and portable tests) and PostgreSQL (production).
- **Domain Tables:**
  - `users`: User profiles, credentials, age, weight, height, daily calorie budgets, and diet types.
  - `products`: Barcode, product name, brand, nutriscore, ingredients, additives.
  - `nutrition`: Macro- and micronutrients per 100g (calories, fat, sugar, salt, protein, fiber, carbs).
  - `scan_history`: Immutable log of scanned products per user.
  - `daily_food_log`: Explicit food intake entries per user.
  - `user_diet_profile`: Macro limit thresholds.

### B. Health Scoring & Ingredient Analysis
- **Standardized Salt Threshold:** Standardized across scoring engines and decision explainers at `1.5g / 100g`.
- **Diet-Aware Scoring:** Adapts penalties based on user profile (e.g., low-sodium diet triples salt penalties).
- **Ingredient & Additive Intelligence:** Token boundary matching (`\b`) and longest-match-first sorting prevents substring false positives (e.g., "msg" inside "message") and eliminates duplicate flag reporting.

### C. Machine Learning Pipeline
- **NutriScore Classification:** Trained ensemble/scikit-learn models (`ensemble_model.pkl`, `model.pkl`) predict missing NutriScore grades (A through E).
- **Validation Metrics:** Evaluated on held-out test data with **82.69% held-out test accuracy** (documented baseline test metric; not a live production guarantee).
- **Inference Determinism:** Explicit feature validation and class probability distributions; missing/corrupted models raise explicit `ModelArtifactError` rather than silently failing to heuristic fallbacks.

### D. OpenFoodFacts Integration
- Resilient multi-tier product lookup with automatic failover.
- Network error handling, timeouts, and HTTP status handling (404/500).
- Automatic nutrient normalization: converts sodium to salt (`salt = sodium * 2.5`) and energy kJ to kcal (`kcal = kJ / 4.184`).

### E. Healthier Product Recommendations
- Automatic food category inference (`infer_food_category`) for Indian and global packaged food categories.
- Deterministic alternative ranking calculates percentage nutritional advantages (e.g. `-45% sugar`, `+20% fiber`).

### F. Mobile Client (Expo SDK 54)
- **Engine:** React Native 0.81.5 with React 19.1.0 on Expo SDK 54 (`~54.0.33`).
- **Camera Scanning:** Native camera barcode scanning powered by `expo-camera` (`~17.0.10`) with zero legacy `expo-barcode-scanner` dependencies.
- **Dynamic LAN IP Routing:** Automatic extraction of the developer machine's LAN host from Expo debug manifests (`Constants.expoConfig.hostUri`, etc.), allowing physical iPhones and Android devices to seamlessly connect to the local backend without hardcoding `localhost`.
- **OCR Label Scanning:** Full workflow using `expo-image-picker` with review/edit capabilities before logging or analyzing.

### G. Security Layer
- **Authentication:** Salted bcrypt password hashing (`passlib[argon2]`) and standard JWT Bearer token generation.
- **CORS Protection:** Wildcard `*` disabled; strict origin validation supporting localhost, Expo local development ports, and regex-matched private LAN subnets (`192.168.x.x`, `10.x.x.x`, `172.16-31.x.x`).
- **Endpoint Protection:** All personal, scanning, reporting, comparison, and analysis routes require authenticated JWT Bearer headers.

### H. Health Claim Verification Engine (`services/claim_verification.py`)
- **Deterministic FSSAI Compliance:** Verifies front-of-pack and marketing claims against declared product nutrition, ingredients, and official statutory criteria from the *Food Safety and Standards (Advertising and Claims) Regulations, 2018* (Schedule I, Schedule II, and Regulation 4(5)).
- **Versioned Regulatory Rules:** Rules are defined as declarative data configurations (`ClaimRule`, `RegulatorySource`) in `REGULATORY_RULES`, completely separating regulatory criteria and source citations from verification execution logic.
- **Supported Regulatory Claims:**
  1. **No Added Sugar:** Verified against ingredient declaration for absence of mono/disaccharides, syrups, honey, or fruit juice concentrates (FSSAI Reg 4(5) & Schedule I).
  2. **Sugar Free:** Total sugars $\le 0.5\text{ g} / 100\text{g}$ (solids) or $100\text{ml}$ (liquids) (FSSAI Schedule I).
  3. **Low Sodium:** Sodium $\le 0.12\text{ g} / 100\text{g}$ ($\le 120\text{ mg}$), or equivalent salt $\le 0.3\text{ g} / 100\text{g}$ (FSSAI Schedule I).
  4. **High Protein:** Protein $\ge 20\%$ of adult 54g RDA ($\ge 10.8\text{ g} / 100\text{g}$) (FSSAI Schedule I).
  5. **High Fibre:** Dietary fibre $\ge 6.0\text{ g} / 100\text{g}$ (FSSAI Schedule I).
  6. **Low Fat:** Total fat $\le 3.0\text{ g} / 100\text{g}$ for solid foods (FSSAI Schedule I).
  7. **Zero Trans Fat:** Trans fatty acids $< 0.2\text{ g} / 100\text{g}$ and saturated fat $\le 1.5\text{ g} / 100\text{g}$ if declared (FSSAI Schedule I).
- **Verification Statuses:**
  - `SUPPORTED`: Available product data satisfies verified rule criteria.
  - `NOT_SUPPORTED`: Available product data conflicts with rule criteria.
  - `NEEDS_REVIEW`: Unrecognized claim or requires specialized manual inspection.
  - `INSUFFICIENT_DATA`: Required nutrient or ingredient declaration is missing (never guessed).
- **Core Domain Distinctions:**
  - **Health Score:** Continuous numerical algorithm (0-100) evaluating global nutritional profile.
  - **Health Claim Verification:** Deterministic statutory check evaluating whether marketing claims comply with FSSAI regulations.
  - **Ingredient Analysis:** Qualitative token scanning identifying additives, allergens, and preservatives.
- **Future Rule Maintenance:** Regulatory rule metadata is stored in `REGULATORY_RULES` with version and effective dates; new FSSAI notifications can be updated without modifying verification engine logic.
- **Statutory Disclaimer:** *"The system provides a rule-based assessment of product claims based on available product data and referenced regulatory criteria. It is not a legal certification."*

---

## 4. API Specification

| Method | Endpoint | Access | Description |
|---|---|---|---|
| `GET` | `/health` | Public | Service health verification |
| `GET` | `/docs` | Public | Interactive Swagger API documentation |
| `GET` | `/openapi.json` | Public | OpenAPI 3.0 specification |
| `POST` | `/register` | Public | Register new user account |
| `POST` | `/login` | Public | Authenticate user and receive JWT |
| `POST` | `/verify-claims` | Authenticated | Deterministic FSSAI health claim verification against product data / database barcode |
| `POST` | `/scan` | Authenticated | Barcode lookup, health scoring, recommendations, and optional claim verification (Scan ≠ Eat) |
| `POST` | `/analyze` | Authenticated | Analyze manually entered nutrition facts |
| `POST` | `/ocr` | Authenticated | OCR nutrition table parser via EasyOCR/Tesseract |
| `POST` | `/food-log` | Authenticated | Explicitly log food consumption to diary |
| `GET` | `/today` | Authenticated | Daily consumed calories, remaining budget, and foods list |
| `GET` | `/history` | Authenticated | Recent barcode scan history |
| `DELETE`| `/history/{id}` | Authenticated | Delete specific scan history entry |
| `GET` | `/stats` | Authenticated | User scan statistics and average health scores |
| `GET` | `/user/profile` | Authenticated | Retrieve current user profile and diet preferences |
| `PUT` | `/user/profile` | Authenticated | Update user profile, metrics, goals, and diet types |
| `GET` | `/report/daily` | Authenticated | Generate daily nutritional breakdown |
| `GET` | `/report/weekly` | Authenticated | Weekly trend analysis and day scores |
| `GET` | `/report/goal` | Authenticated | Calorie goal adherence and tracking |
| `POST` | `/compare` | Authenticated | Compare two products nutritionally |
| `GET` | `/search` | Authenticated | Fuzzy search products by name |
| `GET` | `/product/{barcode}` | Authenticated | Query product from database |
| `GET` | `/explain/{barcode}` | Authenticated | Detailed explainability factor steps |

---

## 5. Local Setup & Execution

### Prerequisites
- Python 3.10+ (Python 3.11 / 3.12 supported)
- Node.js 18+ (Node 20+ recommended)
- Tesseract OCR (optional locally, required for pytesseract engine)

### Backend Setup (`foodscanner-ai`)
```bash
# 1. Navigate to backend directory
cd foodscanner-ai

# 2. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure local environment (REQUIRED)
cp .env.example .env
# Open .env and set SECRET_KEY (e.g. python -c "import secrets; print(secrets.token_hex(32))")
# NOTE: .env must NEVER be committed to Git.
# Keep AI_PROVIDER=mock for reliable offline/demo testing unless an external LLM is configured.

# 5. Initialize database
python -m database.init_db

# 6. Start API server
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 7. Swagger / Demo Testing Workflow:
# - Open interactive documentation at http://127.0.0.1:8000/docs
# - Register a demo user via POST /register or login via POST /login
# - Copy the returned "access_token"
# - Click the green "Authorize" button in Swagger and paste the token
# - Execute protected endpoints (e.g. POST /scan, POST /chat, GET /today)
```

### Mobile Setup (`foodscanner-mobile`)
```bash
# 1. Navigate to mobile directory
cd foodscanner-mobile

# 2. Install dependencies
npm install

# 3. Configure local environment (optional for LAN auto-discovery)
cp .env.example .env

# 4. Start Expo development server
npx expo start --lan
```
*Note: Point your Expo Go app (iOS Camera or Android Expo Go) at the QR code generated by Expo. Ensure your mobile device and computer are on the same Wi-Fi network.*

---

## 6. Automated Testing

### Backend Test Suite (Pytest)
The test suite can be run from **either** the repository root or the `foodscanner-ai` directory:

```bash
# From repository root:
.\foodscanner-ai\.venv\Scripts\python.exe -m pytest foodscanner-ai/tests -v

# From foodscanner-ai directory:
cd foodscanner-ai
python -m pytest tests -v
```
**Results:** **57 passed**, 0 failed, 0 errors.

### Mobile Test Suite (Node.js Native Test Runner)
```bash
# From foodscanner-mobile directory:
cd foodscanner-mobile
npm test
# or:
node --test tests/
```
**Results:** **5 passed**, 0 failed across 2 suites.

---

## 7. Environment Configuration Reference

| Variable | Location | Description | Default |
|---|---|---|---|
| `DATABASE_URL` | `foodscanner-ai/.env` | SQLite/PostgreSQL connection string | `sqlite:///database/foodscanner.db` |
| `SECRET_KEY` | `foodscanner-ai/.env` | JWT token signature secret | Required |
| `FOODSCANNER_OCR_ENGINE` | `foodscanner-ai/.env` | Preferred OCR engine (`easyocr` or `tesseract`) | `easyocr` |
| `ALLOWED_ORIGINS` | `foodscanner-ai/.env` | Comma-separated allowed CORS origins | Local dev origins |
| `EXPO_PUBLIC_API_BASE_URL` | `foodscanner-mobile/.env` | Explicit backend API URL override | Dynamic LAN or Render |

---

## 8. Current Limitations (Phase 2 State)
1. **Health Claim Verification Scope:** Phase 2 Batch 5 supports 7 core FSSAI claims ("No Added Sugar", "Sugar Free", "Low Sodium", "High Protein", "High Fibre", "Low Fat", "Zero Trans Fat"). Expansion to vitamin/mineral micronutrient claims and Front-of-Pack mobile verification badges are scheduled for subsequent iterations.
2. **OCR 2.0:** OCR parses nutrition tables; multi-label packaging bounding boxes and FSSAI license OCR are not yet supported (scheduled for subsequent Phase 2 batches).
3. **Mobile Compare UI:** While `/compare` and recommendation engines exist in the backend, the mobile client does not yet include a dedicated comparison screen (scheduled for subsequent Phase 2 batches).
4. **Physical Device Validation:** The mobile app has been validated via Expo dev server, LAN configuration, and automated unit tests. Physical iPhone field testing requires an in-person device on the local network.

---

## Maintenance

Last documentation review: 2026-09-23. Keep this README aligned with the current setup, usage, and repository structure.
