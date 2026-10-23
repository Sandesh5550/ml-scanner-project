from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np


MODEL_PATH = Path(__file__).resolve().parent / "ensemble_model.pkl"
LEGACY_MODEL_PATH = Path(__file__).resolve().parent / "model.pkl"
EXPECTED_FEATURES = ["calories", "fat", "sugar", "salt", "protein", "fiber", "carbs"]


class ModelArtifactError(RuntimeError):
    """Raised when the ML model artifact is missing, invalid, or prediction fails."""
    pass


@lru_cache(maxsize=4)
def load_bundle(model_path: Path = MODEL_PATH) -> dict[str, Any]:
    resolved_path = Path(model_path)
    if not resolved_path.exists():
        if resolved_path == MODEL_PATH and LEGACY_MODEL_PATH.exists():
            resolved_path = LEGACY_MODEL_PATH
        else:
            raise FileNotFoundError(
                f"Model artifact not found at: {resolved_path}. Train it first with ml_model/train_model.py"
            )
    try:
        bundle = joblib.load(resolved_path)
    except Exception as exc:
        raise ModelArtifactError(f"Failed to load model artifact at {resolved_path}: {exc}") from exc

    if not isinstance(bundle, dict):
        raise ModelArtifactError(f"Invalid model artifact format at {resolved_path}: expected dictionary bundle")
    if "model" not in bundle:
        raise ModelArtifactError(f"Invalid model artifact at {resolved_path}: missing 'model' key")
    if "features" not in bundle or not isinstance(bundle["features"], (list, tuple, np.ndarray)):
        raise ModelArtifactError(f"Invalid model artifact at {resolved_path}: missing or invalid 'features' list")

    return bundle


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_heuristic_nutriscore(nutrition_dict: dict[str, Any]) -> str:
    """Explicit deterministic heuristic NutriScore calculation.
    
    Used only when deterministic fallback is explicitly requested by callers.
    """
    calories = _to_float(nutrition_dict.get("calories")) or 0.0
    sugar = _to_float(nutrition_dict.get("sugar")) or 0.0
    salt = _to_float(nutrition_dict.get("salt")) or 0.0
    fat = _to_float(nutrition_dict.get("fat")) or 0.0
    fiber = _to_float(nutrition_dict.get("fiber")) or 0.0
    protein = _to_float(nutrition_dict.get("protein")) or 0.0

    risk_score = 0.0
    risk_score += min(calories / 80.0, 6.0)
    risk_score += min(sugar / 4.5, 6.0)
    risk_score += min(salt / 0.6, 6.0)
    risk_score += min(fat / 3.0, 6.0)
    risk_score -= min(fiber / 1.5, 3.0)
    risk_score -= min(protein / 3.0, 2.0)

    if risk_score <= 2:
        return "A"
    if risk_score <= 5:
        return "B"
    if risk_score <= 8:
        return "C"
    if risk_score <= 11:
        return "D"
    return "E"


def predict_nutriscore(nutrition_dict: dict[str, Any], model_path: Path = MODEL_PATH) -> str:
    """Predict NutriScore grade (A-E) using the trained scikit-learn ensemble model.
    
    Strictly validates model artifact and inputs. Raises FileNotFoundError or ModelArtifactError
    if the model is missing or corrupt. Does NOT silently default to heuristic.
    """
    bundle = load_bundle(model_path)
    model = bundle["model"]
    features: list[str] = list(bundle["features"])

    x = []
    for f in features:
        v = _to_float(nutrition_dict.get(f))
        x.append(0.0 if v is None else v)

    X = np.asarray([x], dtype=np.float32)
    try:
        pred = model.predict(X)[0]
    except Exception as exc:
        raise ModelArtifactError(f"Prediction failed on feature vector {x}: {exc}") from exc

    return str(pred).upper()


def predict_nutriscore_details(
    nutrition_dict: dict[str, Any], model_path: Path = MODEL_PATH
) -> dict[str, Any]:
    """Predict NutriScore with full metadata, confidence probabilities, and feature values."""
    bundle = load_bundle(model_path)
    model = bundle["model"]
    features: list[str] = list(bundle["features"])

    feature_values: dict[str, float] = {}
    x = []
    for f in features:
        v = _to_float(nutrition_dict.get(f))
        val = 0.0 if v is None else v
        feature_values[f] = val
        x.append(val)

    X = np.asarray([x], dtype=np.float32)
    try:
        pred = model.predict(X)[0]
    except Exception as exc:
        raise ModelArtifactError(f"Prediction failed on feature vector {x}: {exc}") from exc

    probabilities: dict[str, float] = {}
    if hasattr(model, "predict_proba"):
        classes = getattr(model, "classes_", bundle.get("classes", []))
        probs = model.predict_proba(X)[0]
        probabilities = {str(c).upper(): round(float(p), 4) for c, p in zip(classes, probs)}

    return {
        "nutriscore": str(pred).upper(),
        "probabilities": probabilities,
        "features": feature_values,
        "feature_order": features,
        "model_version": bundle.get("model_version", "1.0.0"),
        "model_type": bundle.get("model_type", type(model).__name__),
        "dataset": bundle.get("training_dataset"),
    }

