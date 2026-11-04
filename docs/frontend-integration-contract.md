# PRAMAAN — Frontend Integration Contract & API Specification

> **Status:** VERIFIED & HARDENED (Batch 8 & Demo Readiness Gate)  
> **Target Framework:** React Native / Expo SDK 54 (`foodscanner-mobile`)  
> **Backend Framework:** FastAPI / Uvicorn (`foodscanner-ai`)  
> **Security Scheme:** `HTTPBearer` (HMAC-SHA256 JWT tokens)  
> **Date Verified:** 2026-09-24  

---

## 1. Authentication & Security Specification

All secured endpoints require an HTTP `Authorization` header formatted as:
```http
Authorization: Bearer <access_token>
```

### Standard Error Responses across All Secured Endpoints:
- `401 Unauthorized`:
  - `{"detail": "Missing Authorization token"}`: Header omitted or empty.
  - `{"detail": "Invalid or expired token"}`: Signature mismatch, corrupted token, or token past `exp` claim.
- `422 Unprocessable Entity`: Request body or parameter schema mismatch (FastAPI Pydantic validation error).
- `500 Internal Server Error`: Server exception (fails fast on startup if `SECRET_KEY` is omitted).

---

## 2. API Endpoints Contract

### A. Authentication & User Profile

#### 1. Register Account
- **Method:** `POST`
- **Path:** `/register`
- **Auth:** Public
- **Request Body:**
  ```json
  {
    "email": "user@example.com",
    "password": "SecurePassword123!",
    "name": "Jane Doe"
  }
  ```
  - Required: `email`, `password`
  - Optional: `name`
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "access_token": "eyJhbGciOiJIUzI1NiIsIn...",
    "token_type": "bearer"
  }
  ```
- **Error Status:** `400 Bad Request` (`{"detail": "email and password are required"}` or `"email already registered"`).

#### 2. Login
- **Method:** `POST`
- **Path:** `/login`
- **Auth:** Public
- **Request Body:**
  ```json
  {
    "email": "user@example.com",
    "password": "SecurePassword123!"
  }
  ```
  - Required: `email`, `password`
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "access_token": "eyJhbGciOiJIUzI1NiIsIn...",
    "token_type": "bearer"
  }
  ```
- **Error Status:** `401 Unauthorized` (`{"detail": "Invalid email or password"}`).

#### 3. Get User Profile
- **Method:** `GET`
- **Path:** `/user/profile`
- **Auth:** `Bearer <token>`
- **Request Body:** None
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "id": 1,
    "email": "user@example.com",
    "name": "Jane Doe",
    "age": 28,
    "weight": 70.0,
    "height": 175.0,
    "daily_calorie_limit": 2000,
    "diet_type": "balanced",
    "goal_type": "maintenance",
    "goal_target_days": 30,
    "created_at": "2026-09-24 15:00:00"
  }
  ```
  - Nullable fields: `name`, `age`, `weight`, `height`, `diet_type`, `goal_type`, `goal_target_days`.

#### 4. Update User Profile
- **Method:** `PUT`
- **Path:** `/user/profile`
- **Auth:** `Bearer <token>`
- **Request Body:**
  ```json
  {
    "name": "Jane Doe",
    "age": 29,
    "weight": 68.5,
    "height": 175.0,
    "daily_calorie_limit": 2100,
    "diet_type": "vegan",
    "goal_type": "lose_weight",
    "goal_target_days": 60
  }
  ```
  - All fields optional.
- **Success Status:** `200 OK`
- **Response Shape:** Updated profile object matching `GET /user/profile`.

---

### B. Product Scanning & Intelligence

#### 5. Scan Barcode (Scan ≠ Eat)
- **Method:** `POST`
- **Path:** `/scan`
- **Auth:** `Bearer <token>`
- **Request Body:**
  ```json
  {
    "barcode": "8901234567890",
    "product_name": null,
    "claims": ["High Protein", "Low Sugar"]
  }
  ```
  - Required: `barcode` (8-14 digits)
  - Optional: `product_name`, `claims` (string list)
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "product": {
      "name": "Whole Grain Rolled Oats",
      "nutrition": {
        "calories": 389.0,
        "fat": 6.9,
        "sugar": 0.99,
        "salt": 0.02,
        "protein": 16.89,
        "fiber": 10.6,
        "carbs": 66.3
      },
      "nutriscore": "a"
    },
    "analysis": {
      "ingredient_analysis": {
        "flags": [],
        "score_deduction": 0
      },
      "additive_analysis": {
        "flags": [],
        "score_deduction": 0
      },
      "health_score": 85.0
    },
    "decision": {
      "final_decision": "SAFE",
      "reasons": ["High in fiber", "Low sugar content"]
    },
    "diet_note": null,
    "recommendations": [],
    "daily_intake": {
      "consumed": 250.0,
      "remaining": 1750.0
    },
    "personalized_analysis": {
      "adherence": "positive",
      "summary": "Fits well into your lose_weight goal"
    },
    "explanation": { ... }
  }
  ```
- **Error Status:** `404 Not Found` (`{"detail": "product not found"}`).
- **Important Note:** Scanning **NEVER** creates a `FoodLog` consumption record.

#### 6. Analyze Nutrition Facts
- **Method:** `POST`
- **Path:** `/analyze`
- **Auth:** `Bearer <token>`
- **Request Body:**
  ```json
  {
    "product_name": "Greek Yogurt",
    "calories": 130.0,
    "fat": 4.0,
    "saturated_fat": 2.5,
    "sugar": 6.0,
    "salt": 0.1,
    "protein": 12.0,
    "fiber": 0.0,
    "carbs": 8.0,
    "serving_size": 100.0,
    "ingredients": "Milk, live cultures",
    "claims": ["High Protein"]
  }
  ```
  - Required: `product_name`
  - Optional / Nullable: all nutritional fields, `serving_size`, `ingredients`, `claims`
- **Success Status:** `200 OK`
- **Response Shape:** Matches the analysis structure of `POST /scan`.

#### 7. OCR Nutrition Label Parser
- **Method:** `POST`
- **Path:** `/ocr`
- **Auth:** `Bearer <token>`
- **Request Body:** `multipart/form-data` with `image` file
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "extracted_text": "Energy 389 kcal\nProtein 16.9g\nCarbohydrate 66.3g\nSugar 0.99g...",
    "nutrition_table": {
      "calories": 389.0,
      "fat": 6.9,
      "sugar": 0.99,
      "salt": 0.02,
      "protein": 16.89,
      "fiber": 10.6,
      "carbs": 66.3
    },
    "engine": "easyocr"
  }
  ```
- **Error Status:** `422 Unprocessable Entity` if file is missing.

#### 8. Compare Products
- **Method:** `POST`
- **Path:** `/compare`
- **Auth:** `Bearer <token>`
- **Request Body:**
  ```json
  {
    "product_a": "8901234567890",
    "product_b": "8908000000001"
  }
  ```
  - Both accept barcode or product name.
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "product_a": {
      "name": "Whole Grain Oats",
      "brand": "NutriDemo",
      "barcode": "8901234567890",
      "health_score": 85,
      "nutriscore": "a",
      "nutrition": { ... }
    },
    "product_b": {
      "name": "Dark Chocolate Bar",
      "brand": "CocoaCraft",
      "barcode": "8908000000001",
      "health_score": 33,
      "nutriscore": "d",
      "nutrition": { ... }
    },
    "healthier_product": "Whole Grain Oats",
    "reasons": [
      "Whole Grain Oats: 97% less sugar",
      "Whole Grain Oats: 78% less fat"
    ]
  }
  ```

#### 9. Healthier Alternatives
- **Method:** `POST`
- **Path:** `/alternatives`
- **Auth:** `Bearer <token>`
- **Request Body:**
  ```json
  {
    "product_name": "Potato Chips",
    "barcode": "8901234567890",
    "category": "Snacks",
    "limit": 3
  }
  ```
  - Required: `product_name` or `barcode`
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "product_name": "Potato Chips",
    "barcode": "8901234567890",
    "category": "Snacks",
    "alternatives": [
      {
        "product_name": "Baked Multigrain Crisps",
        "brand": "HealthyBites",
        "health_score": 78,
        "nutriscore": "b",
        "advantages": ["45% less fat", "50% less sodium"]
      }
    ],
    "total_alternatives": 1
  }
  ```

#### 10. Alternatives by Barcode
- **Method:** `GET`
- **Path:** `/alternatives/{barcode}`
- **Auth:** `Bearer <token>`
- **Success Status:** `200 OK`
- **Response Shape:** Matches `POST /alternatives`.

#### 11. Verify Statutory Health Claims (FSSAI)
- **Method:** `POST`
- **Path:** `/verify-claims`
- **Auth:** `Bearer <token>`
- **Request Body:**
  ```json
  {
    "claims": ["High Protein", "Low Sugar", "No Added Sugar"],
    "barcode": "8901234567890",
    "nutrition": null,
    "ingredients": null
  }
  ```
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "results": [
      {
        "claim": "High Protein",
        "normalized_claim": "high_protein",
        "status": "SUPPORTED",
        "reason": "Protein content (16.89 g/100g) exceeds the FSSAI threshold of 10.8 g/100g.",
        "evidence": {
          "actual_value": 16.89,
          "operator": ">=",
          "threshold": 10.8,
          "unit": "g/100g"
        },
        "regulatory_source": {
          "regulation": "FSSAI (Advertising and Claims) Regulations, 2018",
          "schedule": "Schedule I",
          "effective_date": "2018-11-19"
        }
      }
    ],
    "total_claims": 1,
    "disclaimer": "The system provides a rule-based assessment of product claims based on available product data and referenced regulatory criteria. It is not a legal certification."
  }
  ```
  - Status values: `SUPPORTED`, `NOT_SUPPORTED`, `NEEDS_REVIEW`, `INSUFFICIENT_DATA`.

#### 12. Query Product by Barcode
- **Method:** `GET`
- **Path:** `/product/{barcode}`
- **Auth:** `Bearer <token>`
- **Success Status:** `200 OK`
- **Response Shape:** Full product details including `product`, `analysis`, `decision`, and `personalized_analysis`.

#### 13. Score Explainability Factors
- **Method:** `GET`
- **Path:** `/explain/{barcode}`
- **Auth:** `Bearer <token>`
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "product_name": "Whole Grain Rolled Oats",
    "final_score": 85,
    "final_decision": "SAFE",
    "base_score": 100,
    "steps": [
      {
        "factor": "Sugar",
        "value": "0.99g",
        "impact": 0,
        "reason": "Low sugar (0.99g) → +0 points"
      },
      {
        "factor": "Dietary Fiber",
        "value": "10.6g",
        "impact": 15,
        "reason": "High dietary fiber (10.6g) → +15 bonus points"
      }
    ],
    "positive_factors": ["Dietary Fiber", "Protein"],
    "negative_factors": [],
    "summary": "Base score: 100. Evaluated 7 nutritional and ingredient criteria...",
    "threshold_info": {
      "SAFE": "score >= 70",
      "MODERATE": "score >= 45",
      "AVOID": "score < 45"
    }
  }
  ```

#### 14. Fuzzy Product Search
- **Method:** `GET`
- **Path:** `/search?query={query}`
- **Auth:** `Bearer <token>`
- **Success Status:** `200 OK`
- **Response Shape:** Array of search results:
  ```json
  [
    {
      "product_name": "Quaker Oats",
      "source": "indian_dataset",
      "similarity_score": 76.7
    }
  ]
  ```

---

### C. Grounded AI Nutrition Assistant (RAG)

#### 15. AI Nutrition Chat
- **Method:** `POST`
- **Path:** `/chat`
- **Auth:** `Bearer <token>`
- **Request Body:**
  ```json
  {
    "message": "Is this product good for weight loss?",
    "barcode": "8901234567890",
    "product_context": null
  }
  ```
  - Required: `message` (min length 1)
  - Optional: `barcode`, `product_context`
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "answer": "PRAMAAN Analysis for NutriDemo Whole Grain Rolled Oats:\n\n• For your goal (lose_weight): This food contains 389.0 kcal and 0.99g sugar per 100g, which provides steady sustained energy with high dietary fiber.\n• PRAMAAN Score: 85 / 100 (SAFE).\n\nSources:\n- ICMR-NIN Dietary Guidelines 2020\n- FSSAI Claims Regulation 2018",
    "sources": [
      {
        "id": "nutr-fiber-01",
        "title": "Dietary Fibre Guidelines",
        "source": "ICMR-NIN",
        "source_type": "scientific_guideline"
      }
    ],
    "product_context_used": true,
    "profile_context_used": true,
    "retrieved_context_count": 2,
    "product_name": "NutriDemo Whole Grain Rolled Oats",
    "health_score": 85,
    "final_decision": "SAFE"
  }
  ```
- **Error Status:** `503 Service Unavailable` (`{"detail": "AI assistant is temporarily unavailable. Please try again later."}`).

---

### D. Food Diary & Health Tracking

#### 16. Log Food Consumption (Explicit Diary Entry)
- **Method:** `POST`
- **Path:** `/food-log`
- **Auth:** `Bearer <token>`
- **Request Body:**
  ```json
  {
    "product_name": "Rolled Oats Breakfast",
    "calories": 250.0,
    "serving_size": 65.0,
    "barcode": "8901234567890",
    "fat": 4.5,
    "sugar": 0.6,
    "salt": 0.01,
    "protein": 11.0,
    "fiber": 7.0,
    "carbs": 43.0
  }
  ```
  - Required: `product_name`, `calories`
  - Optional: `serving_size`, `barcode`, macros
- **Success Status:** `200 OK`
- **Response Shape:** `{"status": "logged"}`

#### 17. Today's Nutritional Summary
- **Method:** `GET`
- **Path:** `/today`
- **Auth:** `Bearer <token>`
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "calories_consumed_today": 355.0,
    "remaining_calories": 1645.0,
    "foods": [
      {
        "product_name": "Rolled Oats Breakfast",
        "calories": 250.0,
        "consumed_at": "2026-09-24 08:30:00"
      },
      {
        "product_name": "Morning Banana",
        "calories": 105.0,
        "consumed_at": "2026-09-24 11:15:00"
      }
    ]
  }
  ```

#### 18. Scan History
- **Method:** `GET`
- **Path:** `/history`
- **Auth:** `Bearer <token>`
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  [
    {
      "barcode": "8901234567890",
      "result": "SAFE",
      "scan_time": "2026-09-24 14:10:22"
    }
  ]
  ```

#### 19. Scan Statistics
- **Method:** `GET`
- **Path:** `/stats`
- **Auth:** `Bearer <token>`
- **Success Status:** `200 OK`
- **Response Shape:**
  ```json
  {
    "total_scans_ever": 12,
    "scans_this_week": 8,
    "most_scanned_product": {
      "barcode": "8901234567890",
      "name": "Whole Grain Oats",
      "count": 4
    },
    "average_health_score": 74.2,
    "scans_per_day": {
      "2026-09-24": 3
    }
  }
  ```

#### 20. Reports: Daily, Weekly, and Goal
- **Paths:**
  - `GET /report/daily`
  - `GET /report/weekly`
  - `GET /report/goal`
- **Auth:** `Bearer <token>`
- **Success Status:** `200 OK`
- **Response Shapes:**
  - `/report/daily`: Macro totals, calorie target vs actual, adherence indicator.
  - `/report/weekly`: 7-day adherence trend array and average calories.
  - `/report/goal`: Goal type, streak days, active days, target days.
