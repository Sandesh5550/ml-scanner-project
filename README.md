# FoodScanner AI

FoodScanner AI is a production-oriented full-stack nutrition intelligence platform for scanning packaged foods, analyzing ingredients, and generating health-focused recommendations. The project combines a FastAPI backend, an Expo React Native client, barcode/product lookup, authentication, reporting, and ML-assisted nutrition scoring.

## Live Deployment

| Service | URL |
| --- | --- |
| Web app | https://foodscanner-mobile.onrender.com |
| Backend API | https://foodscanner-ai.onrender.com |
| API health check | https://foodscanner-ai.onrender.com/health |
| Swagger docs | https://foodscanner-ai.onrender.com/docs |

## What It Does

- Lets users register, log in, and manage food-scanning sessions.
- Supports barcode/product lookup with local data and external product data fallback.
- Analyzes nutrition, ingredients, additives, and diet-sensitive health factors.
- Produces explainable health scoring instead of only returning a raw result.
- Tracks food activity and exposes daily/weekly report workflows.
- Runs as a public web deployment and can be packaged as an Android APK with EAS.

## Architecture

```text
foodscanner-mobile/          Expo React Native app
  src/screens/               Login, home, scan, reports, profile, manual entry
  src/services/api.js        API client and environment-aware backend routing
  src/context/               Auth/session state

foodscanner-ai/              FastAPI backend and ML engine
  api/main.py                API application and route registration
  services/                  Product lookup, scoring, auth, reports, recommendations
  database/                  Schema, session management, initialization scripts
  ml_model/                  Feature engineering, training, prediction, evaluation
  datasets/                  Product datasets and data preparation scripts
```

## Tech Stack

| Layer | Technologies |
| --- | --- |
| Mobile/Web | Expo, React Native, React Navigation, Axios |
| Backend | FastAPI, Python, Uvicorn |
| Auth | JWT-based authentication |
| Data | OpenFoodFacts integration, packaged food datasets |
| ML/Scoring | scikit-learn, feature engineering, health score explainers |
| Database | SQLite for local development, PostgreSQL-ready deployment |
| Deployment | Render static site + Render web service |

## Local Development

Clone the repo:

```bash
git clone https://github.com/atharvasawant-dev/ml-scanner-project.git
cd ml-scanner-project
```

Start the backend:

```bash
cd foodscanner-ai
pip install -r requirements.txt
py -m database.init_db
py -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Start the mobile app:

```bash
cd foodscanner-mobile
npm install
npx expo start --tunnel
```

For local Expo testing, set `foodscanner-mobile/.env` to your machine's LAN IP:

```bash
EXPO_PUBLIC_API_BASE_URL=http://YOUR_LOCAL_IP:8000
```

For public builds, use the deployed backend:

```bash
EXPO_PUBLIC_API_BASE_URL=https://foodscanner-ai.onrender.com
```

## Deployment Notes

This repository includes a root `render.yaml` for deploying both services:

- `foodscanner-ai` runs as a Render web service.
- `foodscanner-mobile` runs as a Render static site built from Expo web export.
- The mobile/web app must use a public backend URL for real users; local IPs only work on the developer's Wi-Fi.

## Status

The public frontend and backend are deployed and connected. The project is structured as a monorepo so the API, ML pipeline, and mobile client can evolve together while keeping deployment configuration in one place.

## ML Component: NutriScore Prediction

When a product has no NutriScore grade, the API predicts one (A to E) from its nutrition values.

- **Data:** about 50,000 products collected from the OpenFoodFacts API plus a curated Indian packaged-foods dataset. Labels are the real `nutriscore_grade` values published by OpenFoodFacts.
- **Models:** Random Forest, Gradient Boosting, XGBoost and SVM, with a soft-voting ensemble (`foodscanner-ai/ml_model/advanced_training.py`).
- **Measured result:** the committed evaluation (`foodscanner-ai/reports/evaluation_results.json`) shows **about 88% accuracy** (macro F1 about 0.86) for the Random Forest on a held-out stratified 20% split.
- **Limitations:** NutriScore is itself a deterministic formula over nutrition values, so this model learns to approximate that formula rather than discovering new health signal. It is useful as a fallback when a grade is missing. It is not a medical or dietary judgment. The separate "health score" shown to users comes from rule-based logic in `services/food_health_score.py`, which is unit tested.

## Tests

```bash
cd foodscanner-ai
pip install pytest
pytest
```
