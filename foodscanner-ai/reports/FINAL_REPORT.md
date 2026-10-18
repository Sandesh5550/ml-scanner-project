# FoodScanner AI Model Upgrade

## Executive summary
This upgrade introduces a large-scale dataset pipeline, extensive feature engineering (50+ features), and an advanced ensemble model training workflow.

Key outcomes:
- Dataset size increased from ~313 training samples (previous small/synthetic pipeline) to **50,000+ OpenFoodFacts products** plus the existing Indian packaged foods dataset.
- New engineered feature set adds ratios, densities, balance scores, processing indicators, ingredient/additive risk scores, polynomial and interaction terms.
- Advanced training trains **4 algorithms + a soft-voting ensemble** and persists all artifacts without changing any existing API/mobile code.

## Data
### Source
- **OpenFoodFacts API** (category-based paging)
- Existing: `datasets/indian_foods/indian_packaged_foods.csv`

### Collection
Script:
- `foodscanner-ai/scripts/collect_large_dataset.py`

Details:
- 10 categories:
  - snacks
  - breakfast-cereals
  - biscuits
  - chocolate
  - beverages
  - candies
  - instant-noodles
  - dairy-products
  - nuts
  - soups
- 50 pages per category (target 50,000+ total)
- Retry: 5 retries with exponential backoff
- Rate limit: 0.5s between requests
- Deduplication: by `code`
- Keep only products with complete minimum nutrition (`calories`, `sugar`, `salt`)

Output:
- `datasets/large_openfoodfacts.csv`

### Cleaning
Script:
- `foodscanner-ai/scripts/clean_dataset.py`

Cleaning rules:
- Remove rows missing calories/sugar/salt
- Remove outliers: calories > 1000 OR sugar > 100
- Fill missing saturated_fat with 0
- Fill missing fiber with 0
- Remove duplicates by (code, product_name)

Outputs:
- `datasets/large_openfoodfacts_cleaned.csv`
- `reports/data_quality.txt`

## Feature engineering
Module:
- `foodscanner-ai/ml_model/feature_engineering.py`

Features created (50+):
- Ratios: sugar/protein, fiber/carbs, fat/calories, energy density, satfat/fat, carbs/fiber
- Density: nutrient_density, unhealthy_density, fiber_adequacy, protein_density
- Balance: carb_balance_score, protein_adequacy_score, sugar_excess_score, fiber_deficit_score
- Processing: is_highly_processed, additive_count, ingredient_count
- Risk: ingredient_risk_score, additive_risk_score
- Polynomial: calories_squared, sugar_cubed, salt_squared
- Interactions: sugar_fat_interaction, salt_calories_interaction, fiber_carbs_interaction, protein_satfat_interaction, processed_sugar_interaction
- Plus additional logs, per-100-cal metrics, composite quality/risk indices

Feature selection:
- `select_best_features(X, y, n_features=40)` selects the top 40 features by importance.

Visualization:
- Feature correlation heatmap saved to: `reports/feature_correlation.png`

## Model training
Module:
- `foodscanner-ai/ml_model/advanced_training.py`

Workflow:
- Loads `datasets/large_openfoodfacts_cleaned.csv` + `datasets/indian_foods/indian_packaged_foods.csv`
- Applies feature engineering and selects top 40 features
- 80/20 split with stratification

Models:
- RandomForest (GridSearchCV over requested hyperparameters; base target ~300 estimators)
- GradientBoosting
- XGBoost (if available) else GradientBoosting fallback
- SVM (RBF kernel with scaling)
- Ensemble: soft VotingClassifier of RF + GB + XGB (weighted)

Artifacts saved to `foodscanner-ai/ml_model/`:
- `random_forest_model.pkl`
- `gradient_boosting_model.pkl`
- `xgboost_model.pkl`
- `svm_model.pkl`
- `ensemble_model.pkl`
- `best_hyperparams.json`

## Evaluation
Module:
- `foodscanner-ai/ml_model/evaluation.py`

Metrics:
- accuracy, balanced accuracy
- precision/recall/F1 (weighted/macro/micro)
- per-class metrics
- confusion matrix
- ROC curves (one-vs-rest)
- 5-fold cross-validation (best-effort)

Outputs:
- `reports/evaluation_summary.txt`
- `reports/evaluation_results.json`
- `reports/model_comparison.csv`
- `reports/confusion_matrix_{model}.png`
- `reports/roc_curves_{model}.png`
- `reports/feature_importance.png`

## Integration (no API/mobile changes)
- `foodscanner-ai/ml_model/predict_nutriscore.py` now defaults to loading `ensemble_model.pkl`.
  - Backward compatible: automatically falls back to legacy `model.pkl` if the ensemble file is missing.
- `foodscanner-ai/ml_model/train_model.py` now supports `--advanced`:
  - Default behavior unchanged: running the script without flags still trains the original small pipeline.
  - `--advanced` trains and saves the ensemble models.

## Results
- Before: ~313-sample small training pipeline, ~72% accuracy (baseline)
- After: 50,000+ OpenFoodFacts samples + Indian dataset, 50+ engineered features, ensemble model
- Measured (see `reports/evaluation_results.json`): Random Forest reaches about **88% accuracy** (macro F1 about 0.86) on a held-out stratified 20% split. The earlier 92% figure was a target and was not reached.
- Caveat: NutriScore is a deterministic formula over nutrition values, so the model approximates that formula rather than learning new health signal.

To reproduce:
1. Collect:
   - Run `python foodscanner-ai/scripts/collect_large_dataset.py`
2. Clean:
   - Run `python foodscanner-ai/scripts/clean_dataset.py`
3. Train advanced:
   - Run `python -m foodscanner-ai.ml_model.train_model --advanced`
4. Prediction (existing app code unchanged):
   - Existing `predict_nutriscore()` uses the ensemble automatically if present.
