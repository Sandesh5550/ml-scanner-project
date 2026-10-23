from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier, VotingClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split


MODEL_DIR = Path(__file__).resolve().parent
ENSEMBLE_MODEL_PATH = MODEL_DIR / "ensemble_model.pkl"
LEGACY_MODEL_PATH = MODEL_DIR / "model.pkl"

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"
LARGE_OFF_CSV = DATASETS_DIR / "large_openfoodfacts_cleaned.csv"
INDIAN_CSV_PATH = DATASETS_DIR / "indian_foods" / "indian_packaged_foods.csv"

FEATURES = ["calories", "fat", "sugar", "salt", "protein", "fiber", "carbs"]
CLASSES = np.array(list("abcde"))
MODEL_VERSION = "1.0.0"


def _nutriscore_points_per_100g(product: pd.Series) -> int:
    """Official NutriScore points per 100g (simplified version)."""
    points = 0

    # Energy (kJ)
    energy_kj = float(product.get("calories", 0) or 0) * 4.184
    if energy_kj > 3350:
        points += 10
    elif energy_kj > 3010:
        points += 8
    elif energy_kj > 2670:
        points += 6
    elif energy_kj > 2330:
        points += 4
    elif energy_kj > 1990:
        points += 2

    # Sugars
    sugar = float(product.get("sugar", 0) or 0)
    if sugar > 13.5:
        points += 10
    elif sugar > 9:
        points += 8
    elif sugar > 4.5:
        points += 6
    elif sugar > 0:
        points += 4

    # Saturated fat
    sat_fat = float(product.get("saturated_fat", 0) or 0)
    if sat_fat > 10:
        points += 10
    elif sat_fat > 6:
        points += 8
    elif sat_fat > 3:
        points += 6
    elif sat_fat > 0:
        points += 4

    # Sodium (mg)
    sodium_mg = float(product.get("salt", 0) or 0) * 1000 * 2.5
    if sodium_mg > 900:
        points += 10
    elif sodium_mg > 600:
        points += 8
    elif sodium_mg > 300:
        points += 6
    elif sodium_mg > 0:
        points += 4

    # Protein (negative points)
    protein = float(product.get("protein", 0) or 0)
    if protein > 8:
        points -= 5
    elif protein > 6.5:
        points -= 2
    elif protein > 4.7:
        points -= 1

    # Fiber (negative points)
    fiber = float(product.get("fiber", 0) or 0)
    if fiber > 4.7:
        points -= 5
    elif fiber > 3.7:
        points -= 2
    elif fiber > 2.8:
        points -= 1

    return max(0, points)


def _nutriscore_label_from_points(points: int) -> str:
    if points <= -1:
        return "a"
    if points <= 2:
        return "b"
    if points <= 10:
        return "c"
    if points <= 18:
        return "d"
    return "e"


def _load_real_openfoodfacts_data(csv_path: Path) -> tuple[np.ndarray, np.ndarray]:
    df = pd.read_csv(csv_path, low_memory=False)
    mask = df["nutriscore_grade"].astype("string").str.strip().str.lower().isin(list("abcde"))
    df = df.loc[mask].copy()

    for col in ["fat", "sugar", "salt", "protein", "fiber", "carbs"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).clip(0.0, 100.0)
    df["calories"] = pd.to_numeric(df["calories"], errors="coerce").fillna(0.0).clip(0.0, 950.0)

    X = df[FEATURES].values.astype(np.float32)
    y = df["nutriscore_grade"].astype("string").str.strip().str.lower().to_numpy(dtype="<U1")
    return X, y


def _load_real_indian_data(csv_path: Path) -> tuple[np.ndarray, np.ndarray]:
    df = pd.read_csv(csv_path)
    df = df.dropna(subset=FEATURES, how="all").copy()
    for col in ["fat", "sugar", "salt", "protein", "fiber", "carbs"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).clip(0.0, 100.0)
    df["calories"] = pd.to_numeric(df["calories"], errors="coerce").fillna(0.0).clip(0.0, 950.0)

    X = df[FEATURES].values.astype(np.float32)
    y = np.array(
        [_nutriscore_label_from_points(_nutriscore_points_per_100g(row)) for _, row in df.iterrows()],
        dtype="<U1",
    )
    return X, y


def _generate_synthetic_data(n_samples: int = 2000, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)

    calories = rng.uniform(0, 700, n_samples)
    fat = rng.uniform(0, 60, n_samples)
    sugar = rng.uniform(0, 60, n_samples)
    salt = rng.uniform(0, 5, n_samples)
    protein = rng.uniform(0, 40, n_samples)
    fiber = rng.uniform(0, 30, n_samples)
    carbs = rng.uniform(0, 100, n_samples)

    X = np.column_stack([calories, fat, sugar, salt, protein, fiber, carbs]).astype(np.float32)

    risk = (
        0.004 * calories
        + 0.12 * fat
        + 0.18 * sugar
        + 2.5 * salt
        + 0.02 * carbs
        - 0.08 * protein
        - 0.25 * fiber
    )
    risk += rng.normal(0, 1.5, n_samples)

    y = np.empty(n_samples, dtype="<U1")
    y[risk < 3.0] = "a"
    y[(risk >= 3.0) & (risk < 6.0)] = "b"
    y[(risk >= 6.0) & (risk < 9.0)] = "c"
    y[(risk >= 9.0) & (risk < 12.0)] = "d"
    y[risk >= 12.0] = "e"

    return X, y


def train_model(
    model_path: Path = ENSEMBLE_MODEL_PATH,
    legacy_path: Path = LEGACY_MODEL_PATH,
    random_state: int = 42,
) -> dict[str, Any]:
    """Train a reproducible soft-voting ensemble model and save artifact bundle with metadata."""
    X_parts: list[np.ndarray] = []
    y_parts: list[np.ndarray] = []
    datasets_used: list[str] = []

    # 1. Load large OpenFoodFacts dataset if available
    if LARGE_OFF_CSV.is_file():
        print(f"Loading OpenFoodFacts dataset from: {LARGE_OFF_CSV}")
        X_off, y_off = _load_real_openfoodfacts_data(LARGE_OFF_CSV)
        print(f"Loaded {len(X_off)} real OpenFoodFacts samples.")
        X_parts.append(X_off)
        y_parts.append(y_off)
        datasets_used.append(f"large_openfoodfacts_cleaned.csv (N={len(X_off)})")

    # 2. Load Indian food dataset if available
    if INDIAN_CSV_PATH.is_file():
        print(f"Loading Indian food dataset from: {INDIAN_CSV_PATH}")
        X_ind, y_ind = _load_real_indian_data(INDIAN_CSV_PATH)
        print(f"Loaded {len(X_ind)} Indian packaged food samples.")
        X_parts.append(X_ind)
        y_parts.append(y_ind)
        datasets_used.append(f"indian_packaged_foods.csv (N={len(X_ind)})")

    # 3. Fallback to synthetic if no datasets present
    if not X_parts:
        print("No CSV datasets found; generating synthetic dataset...")
        X_syn, y_syn = _generate_synthetic_data(n_samples=2500, seed=random_state)
        X_parts.append(X_syn)
        y_parts.append(y_syn)
        datasets_used.append(f"synthetic_data (N={len(X_syn)})")

    X = np.vstack(X_parts)
    y = np.concatenate(y_parts)
    print(f"Total dataset size: {len(X)} samples across classes: {sorted(list(set(y)))}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=random_state, stratify=y
    )
    print(f"Train set: {len(X_train)} samples, Test set: {len(X_test)} samples")

    # Build ensemble estimators
    rf = RandomForestClassifier(
        n_estimators=150,
        max_depth=14,
        random_state=random_state,
        n_jobs=-1,
        class_weight="balanced",
    )
    gb = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        random_state=random_state,
    )

    ensemble = VotingClassifier(
        estimators=[("rf", rf), ("gb", gb)],
        voting="soft",
        weights=[1.0, 1.0],
        n_jobs=-1,
    )

    print("Fitting VotingClassifier ensemble (RandomForest + GradientBoosting)...")
    ensemble.fit(X_train, y_train)

    preds = ensemble.predict(X_test)
    acc = float(accuracy_score(y_test, preds))
    class_labels = sorted(list(set(y)))
    report = classification_report(y_test, preds, target_names=class_labels, output_dict=False)
    report_dict = classification_report(y_test, preds, target_names=class_labels, output_dict=True)
    cm = confusion_matrix(y_test, preds, labels=class_labels)

    print(f"Ensemble Test Accuracy: {acc:.4f}")
    print("\nClassification Report:\n" + report)
    print("Confusion Matrix:\n", cm)

    bundle = {
        "model": ensemble,
        "model_version": MODEL_VERSION,
        "model_type": "VotingClassifier(RandomForest + GradientBoosting)",
        "features": list(FEATURES),
        "classes": class_labels,
        "training_dataset": " + ".join(datasets_used),
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
        "random_state": random_state,
        "metrics": {
            "test_accuracy": acc,
            "train_samples": int(len(X_train)),
            "test_samples": int(len(X_test)),
            "test_split": 0.2,
            "confusion_matrix": cm.tolist(),
            "classification_report": report_dict,
        },
    }

    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, model_path, compress=3)
    print(f"Saved primary ensemble model artifact to: {model_path}")

    # Also save to legacy_path for backward compatibility
    if legacy_path and legacy_path != model_path:
        joblib.dump(bundle, legacy_path, compress=3)
        print(f"Saved compatibility model artifact to: {legacy_path}")

    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train NutriScore model")
    parser.add_argument(
        "--advanced",
        action="store_true",
        help="Train advanced models",
    )
    args = parser.parse_args()

    train_model()

