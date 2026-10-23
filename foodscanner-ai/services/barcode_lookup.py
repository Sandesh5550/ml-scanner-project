from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Any, Optional

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.openfoodfacts_service import fetch_product_by_barcode
from services.openfoodfacts_service import fetch_product_by_barcode_v2
from ml_model.predict_nutriscore import predict_nutriscore
from services.food_logger import get_remaining_calories, get_today_calories
from services.ingredient_analyzer import analyze_ingredients
from services.additive_analyzer import analyze_additives
from services.indian_food_dataset import search_indian_dataset
from services.food_health_score import compute_food_health_score, compute_diet_aware_score
from services.final_decision_engine import compute_final_decision
from services.decision_explainer import build_decision_reasons
from services import db_service


logger = logging.getLogger(__name__)


def _analyze_and_log(
    db: Session,
    product: dict[str, Any],
    user_id: int,
    daily_calorie_limit: int,
    diet_type: str | None = None,
) -> dict[str, Any]:
    if not product.get("nutriscore"):
        predicted = predict_nutriscore(product)
        product["nutriscore"] = predicted
        logger.info("NutriScore predicted by ML model")

    user_profile = {
        "max_calories": int(daily_calorie_limit or 2000),
        "max_sugar": 50,
        "max_salt": 5,
        "max_fat": 70,
    }
    today_calories = get_today_calories(db, user_id=int(user_id))
    remaining_calories = get_remaining_calories(user_profile, db, user_id=int(user_id))
    product["today_calories_consumed"] = today_calories
    product["remaining_calories"] = remaining_calories

    product["ingredient_analysis"] = analyze_ingredients(product.get("ingredients"))
    product["additive_analysis"] = analyze_additives(product.get("additives"))

    if diet_type:
        health = compute_diet_aware_score(product, diet_type)
        product["diet_note"] = health.get("diet_note")
    else:
        health = compute_food_health_score(product)

    final = compute_final_decision(
        health,
        remaining_calories=float(remaining_calories),
        product_calories=float(product.get("calories") or 0.0),
    )
    product["health_score"] = final.get("health_score")
    product["final_decision"] = final.get("final_decision")
    product["reasons"] = build_decision_reasons(product, remaining_calories)
    logger.info("Recommendation generated: %s", product.get("final_decision"))

    db_service.log_scan(
        db,
        barcode=str(product.get("barcode") or ""),
        result=str(product.get("final_decision") or ""),
        user_id=int(user_id),
    )

    return product


def lookup_product(
    db: Session,
    barcode: str,
    product_name_hint: Optional[str] = None,
    user_id: int = 1,
    daily_calorie_limit: int = 2000,
    diet_type: str | None = None,
) -> Optional[dict[str, Any]]:
    db_service.ensure_default_user(db)

    local = db_service.get_product_by_barcode(db, barcode)
    if local is not None:
        logger.info("product served from local cache")

        local = _analyze_and_log(db, local, user_id=int(user_id), daily_calorie_limit=int(daily_calorie_limit), diet_type=diet_type)
        db.commit()
        return local

    logger.info("product fetched from OpenFoodFacts API")
    fetched = fetch_product_by_barcode(barcode)
    stored: Optional[dict[str, Any]] = None

    if fetched is None:
        if not product_name_hint:
            logger.info("OpenFoodFacts v0 lookup failed; attempting v2 direct lookup")
            fetched_v2 = fetch_product_by_barcode_v2(barcode)
            if fetched_v2 is None:
                return None

            product_obj = db_service.create_product(db, fetched_v2)
            db_service.create_nutrition(
                db,
                {
                    "product_id": product_obj.id,
                    "calories": fetched_v2.get("calories"),
                    "fat": fetched_v2.get("fat"),
                    "sugar": fetched_v2.get("sugar"),
                    "salt": fetched_v2.get("salt"),
                    "protein": fetched_v2.get("protein"),
                    "fiber": fetched_v2.get("fiber"),
                    "carbs": fetched_v2.get("carbs"),
                },
            )
            db.commit()
            stored = db_service.get_product_by_barcode(db, barcode)
        else:
            dataset_product = search_indian_dataset(product_name_hint)
            if dataset_product is None:
                return None

            product_obj = db_service.create_product(
                db,
                {
                    "barcode": str(barcode),
                    "product_name": str(dataset_product["product_name"]),
                    "nutriscore": None,
                    "ingredients": None,
                    "additives": None,
                },
            )
            db_service.create_nutrition(
                db,
                {
                    "product_id": product_obj.id,
                    "calories": dataset_product.get("calories"),
                    "fat": dataset_product.get("fat"),
                    "sugar": dataset_product.get("sugar"),
                    "salt": dataset_product.get("salt"),
                    "protein": dataset_product.get("protein"),
                    "fiber": dataset_product.get("fiber"),
                    "carbs": dataset_product.get("carbs"),
                },
            )
            db.commit()
            stored = db_service.get_product_by_barcode(db, barcode)
    else:
        product_obj = db_service.create_product(db, fetched)
        db_service.create_nutrition(
            db,
            {
                "product_id": product_obj.id,
                "calories": fetched.get("calories"),
                "fat": fetched.get("fat"),
                "sugar": fetched.get("sugar"),
                "salt": fetched.get("salt"),
                "protein": fetched.get("protein"),
                "fiber": fetched.get("fiber"),
                "carbs": fetched.get("carbs"),
            },
        )
        db.commit()
        stored = db_service.get_product_by_barcode(db, barcode)

    if stored is None:
        return None

    stored = _analyze_and_log(db, stored, user_id=int(user_id), daily_calorie_limit=int(daily_calorie_limit), diet_type=diet_type)
    db.commit()

    return stored


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    barcode = "737628064502"
    from database.orm import SessionLocal

    with SessionLocal() as db:
        result = lookup_product(db, barcode)
    if result is None:
        print(None)
        return

    nutrition = {
        "calories": result.get("calories"),
        "fat": result.get("fat"),
        "sugar": result.get("sugar"),
        "salt": result.get("salt"),
        "protein": result.get("protein"),
        "fiber": result.get("fiber"),
        "carbs": result.get("carbs"),
    }
    print({
        "barcode": result.get("barcode"),
        "product_name": result.get("product_name"),
        "nutrition": nutrition,
        "nutriscore": result.get("nutriscore"),
        "recommendation": result.get("recommendation"),
        "today_calories_consumed": result.get("today_calories_consumed"),
        "remaining_calories": result.get("remaining_calories"),
    })


if __name__ == "__main__":
    main()
