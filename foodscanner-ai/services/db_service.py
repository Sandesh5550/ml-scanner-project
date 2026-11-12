from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import Date, and_, cast, func, select
from sqlalchemy.orm import Session

from rapidfuzz import fuzz

from database.models import FoodLog, Nutrition, Product, ProductBarcodeAlias, ScanHistory, User


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _local_now_str() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _local_day_bounds() -> tuple[str, str]:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return (
        start.isoformat(sep=" ", timespec="seconds"),
        end.isoformat(sep=" ", timespec="seconds"),
    )


def get_product_by_barcode(db: Session, barcode: str) -> Optional[dict[str, Any]]:
    barcode_str = str(barcode).strip()
    stmt = (
        select(Product, Nutrition)
        .outerjoin(Nutrition, Nutrition.product_id == Product.id)
        .where(Product.barcode == barcode_str)
    )
    row = db.execute(stmt).first()

    # Fallback to barcode alias resolution if primary barcode is not matched
    if row is None:
        alias_stmt = (
            select(Product, Nutrition)
            .join(ProductBarcodeAlias, ProductBarcodeAlias.product_id == Product.id)
            .outerjoin(Nutrition, Nutrition.product_id == Product.id)
            .where(ProductBarcodeAlias.alias_barcode == barcode_str)
        )
        row = db.execute(alias_stmt).first()

    if row is None:
        return None

    product: Product = row[0]
    nutrition: Nutrition | None = row[1]

    return {
        "id": product.id,
        "barcode": product.barcode,
        "product_name": product.product_name,
        "brand": product.brand,
        "category": getattr(product, "category", None),
        "canonical_id": getattr(product, "canonical_id", None) or product.barcode,
        "nutriscore": product.nutriscore,
        "ingredients": product.ingredients,
        "additives": product.additives,
        "created_at": product.created_at,
        "calories": nutrition.calories if nutrition else None,
        "fat": nutrition.fat if nutrition else None,
        "saturated_fat": nutrition.saturated_fat if nutrition else None,
        "trans_fat": nutrition.trans_fat if nutrition else None,
        "sugar": nutrition.sugar if nutrition else None,
        "salt": nutrition.salt if nutrition else None,
        "sodium": getattr(nutrition, "sodium", None) if nutrition else None,
        "protein": nutrition.protein if nutrition else None,
        "fiber": nutrition.fiber if nutrition else None,
        "carbs": nutrition.carbs if nutrition else None,
    }


def get_canonical_product_by_barcode(db: Session, barcode: str) -> Optional[Any]:
    """Retrieve product by primary barcode or alias barcode and return CanonicalProduct contract."""
    from database.canonical_adapter import db_product_to_canonical

    barcode_str = str(barcode).strip()
    product = db.execute(
        select(Product)
        .where(Product.barcode == barcode_str)
    ).scalar_one_or_none()

    if product is None:
        product = db.execute(
            select(Product)
            .join(ProductBarcodeAlias, ProductBarcodeAlias.product_id == Product.id)
            .where(ProductBarcodeAlias.alias_barcode == barcode_str)
        ).scalar_one_or_none()

    if product is None:
        return None

    return db_product_to_canonical(product)


def upsert_canonical_product(db: Session, canonical: Any) -> Any:
    """Persist a CanonicalProduct using deterministic quality-aware merging.

    Workflow:
    1. Look up existing product by primary barcode or alias.
    2. If found:
       - Merges existing product data with incoming data via merge_canonical_products.
       - Applies merged attributes onto the DB Product and Nutrition models.
       - Adds any new alternate barcodes as aliases.
       - Flushes changes.
       - Returns the merged CanonicalProduct.
    3. If not found:
       - Converts canonical product to new DB models (Product, Nutrition, Aliases).
       - Adds to session, flushes, and returns the persisted CanonicalProduct.
       - Handles concurrent insert races via IntegrityError rollback and update retry.
    """
    from database.canonical_adapter import (
        apply_canonical_to_db_product,
        canonical_to_db_models,
        db_product_to_canonical,
        merge_canonical_products,
    )
    from schemas.product import CanonicalProduct
    from sqlalchemy.exc import IntegrityError

    if not isinstance(canonical, CanonicalProduct):
        raise TypeError(f"Expected CanonicalProduct instance, got {type(canonical)}")

    clean_barcode = str(canonical.barcode).strip()

    # 1. Lookup existing product
    existing_product = db.execute(
        select(Product).where(Product.barcode == clean_barcode)
    ).scalar_one_or_none()

    if existing_product is None:
        existing_product = db.execute(
            select(Product)
            .join(ProductBarcodeAlias, ProductBarcodeAlias.product_id == Product.id)
            .where(ProductBarcodeAlias.alias_barcode == clean_barcode)
        ).scalar_one_or_none()

    # 2. Existing Product -> Quality-Aware Merge
    if existing_product is not None:
        existing_canonical = db_product_to_canonical(existing_product)
        merged_canonical = merge_canonical_products(existing_canonical, canonical)
        apply_canonical_to_db_product(merged_canonical, existing_product)

        # Synchronize any new alternate barcodes
        for alt_code in merged_canonical.alternate_barcodes:
            if alt_code and alt_code != existing_product.barcode:
                try:
                    add_barcode_alias(db, existing_product.id, alt_code)
                except ValueError:
                    pass

        db.flush()
        return merged_canonical

    # 3. New Product -> Insert
    product_model, nutrition_model, alias_models = canonical_to_db_models(canonical)
    try:
        db.add(product_model)
        db.flush()

        nutrition_model.product_id = product_model.id
        db.add(nutrition_model)

        for alias in alias_models:
            alias.product_id = product_model.id
            db.add(alias)

        db.flush()
        return db_product_to_canonical(product_model, nutrition=nutrition_model, aliases=alias_models)
    except IntegrityError:
        # Concurrent insert race condition: another thread/worker just inserted this barcode
        db.rollback()
        retry_product = db.execute(
            select(Product).where(Product.barcode == clean_barcode)
        ).scalar_one_or_none()
        if retry_product is not None:
            existing_canonical = db_product_to_canonical(retry_product)
            merged_canonical = merge_canonical_products(existing_canonical, canonical)
            apply_canonical_to_db_product(merged_canonical, retry_product)
            db.flush()
            return merged_canonical
        raise


def add_barcode_alias(
    db: Session,
    product_id: int,
    alias_barcode: str,
    alias_type: str = "alternate",
) -> ProductBarcodeAlias:
    """Register an alternate barcode alias idempotently with validation guards.

    Guards enforced:
    1. Target product must exist in the database.
    2. Alias barcode cannot be identical to target product's primary barcode (no self-alias).
    3. Alias barcode cannot collide with any product's primary barcode (no primary shadowing).
    4. If alias already belongs to the target product, returns existing alias idempotently.
    5. If alias belongs to a different product, raises ValueError.
    """
    clean_alias = str(alias_barcode).strip()
    if not clean_alias:
        raise ValueError("Cannot add empty or whitespace-only barcode alias")

    target_product = db.execute(
        select(Product).where(Product.id == product_id)
    ).scalar_one_or_none()
    if target_product is None:
        raise ValueError(f"Cannot add alias: target product with id {product_id} does not exist")

    if clean_alias == target_product.barcode:
        raise ValueError(
            f"Cannot add alias '{clean_alias}': alias cannot be identical to target product's primary barcode"
        )

    existing_primary = db.execute(
        select(Product).where(Product.barcode == clean_alias)
    ).scalar_one_or_none()
    if existing_primary is not None:
        raise ValueError(
            f"Cannot add alias '{clean_alias}': barcode already exists as primary barcode for product id {existing_primary.id}"
        )

    existing_alias = db.execute(
        select(ProductBarcodeAlias).where(ProductBarcodeAlias.alias_barcode == clean_alias)
    ).scalar_one_or_none()
    if existing_alias is not None:
        if existing_alias.product_id == product_id:
            return existing_alias
        raise ValueError(
            f"Cannot add alias '{clean_alias}': alias already belongs to product id {existing_alias.product_id}"
        )

    alias = ProductBarcodeAlias(
        product_id=product_id,
        alias_barcode=clean_alias,
        alias_type=alias_type,
        created_at=_utc_now_str(),
    )
    db.add(alias)
    db.flush()
    return alias


def create_product(db: Session, product_data: dict[str, Any]) -> Product:
    barcode = str(product_data["barcode"])

    existing = db.execute(select(Product).where(Product.barcode == barcode)).scalar_one_or_none()
    if existing is not None:
        if product_data.get("product_name") and not existing.product_name:
            existing.product_name = str(product_data.get("product_name"))
        if product_data.get("brand") is not None and not existing.brand:
            existing.brand = str(product_data.get("brand"))
        if product_data.get("category") is not None and not existing.category:
            existing.category = str(product_data.get("category"))
        if product_data.get("canonical_id") is not None and not existing.canonical_id:
            existing.canonical_id = str(product_data.get("canonical_id"))
        if product_data.get("nutriscore") is not None and not existing.nutriscore:
            existing.nutriscore = str(product_data.get("nutriscore"))
        if product_data.get("ingredients") is not None and not existing.ingredients:
            existing.ingredients = product_data.get("ingredients")
        if product_data.get("additives") is not None and not existing.additives:
            existing.additives = product_data.get("additives")
        if product_data.get("data_source") is not None:
            existing.data_source = str(product_data.get("data_source"))
        if product_data.get("data_confidence") is not None:
            existing.data_confidence = str(product_data.get("data_confidence"))
        if "nutrition_completeness" in product_data:
            val = product_data["nutrition_completeness"]
            existing.nutrition_completeness = float(val) if val is not None else None
        if "ingredients_completeness" in product_data:
            val = product_data["ingredients_completeness"]
            existing.ingredients_completeness = float(val) if val is not None else None
        if product_data.get("last_fetched_at") is not None:
            existing.last_fetched_at = str(product_data.get("last_fetched_at"))
        if product_data.get("last_updated_at") is not None:
            existing.last_updated_at = str(product_data.get("last_updated_at"))
        if product_data.get("field_provenance_json") is not None:
            existing.field_provenance_json = str(product_data.get("field_provenance_json"))

        db.add(existing)
        db.flush()
        return existing

    product = Product(
        barcode=barcode,
        canonical_id=str(product_data.get("canonical_id") or barcode),
        product_name=str(product_data.get("product_name") or ""),
        brand=product_data.get("brand"),
        category=product_data.get("category"),
        nutriscore=product_data.get("nutriscore"),
        ingredients=product_data.get("ingredients"),
        additives=product_data.get("additives"),
        data_source=str(product_data.get("data_source") or "local_db"),
        data_confidence=str(product_data.get("data_confidence") or "UNVERIFIED"),
        nutrition_completeness=(
            float(product_data["nutrition_completeness"])
            if product_data.get("nutrition_completeness") is not None
            else None
        ),
        ingredients_completeness=(
            float(product_data["ingredients_completeness"])
            if product_data.get("ingredients_completeness") is not None
            else None
        ),
        last_fetched_at=product_data.get("last_fetched_at"),
        last_updated_at=product_data.get("last_updated_at"),
        field_provenance_json=product_data.get("field_provenance_json"),
        created_at=str(product_data.get("created_at") or _utc_now_str()),
    )
    db.add(product)
    db.flush()
    return product


def create_nutrition(db: Session, nutrition_data: dict[str, Any]) -> Nutrition:
    product_id = int(nutrition_data["product_id"])

    existing = db.execute(select(Nutrition).where(Nutrition.product_id == product_id)).scalar_one_or_none()
    if existing is not None:
        for key in ["calories", "fat", "saturated_fat", "trans_fat", "sugar", "salt", "sodium", "protein", "fiber", "carbs"]:
            if key in nutrition_data:
                setattr(existing, key, nutrition_data.get(key))
        db.add(existing)
        db.flush()
        return existing

    nutrition = Nutrition(
        product_id=product_id,
        calories=nutrition_data.get("calories"),
        fat=nutrition_data.get("fat"),
        saturated_fat=nutrition_data.get("saturated_fat"),
        trans_fat=nutrition_data.get("trans_fat"),
        sugar=nutrition_data.get("sugar"),
        salt=nutrition_data.get("salt"),
        sodium=nutrition_data.get("sodium"),
        protein=nutrition_data.get("protein"),
        fiber=nutrition_data.get("fiber"),
        carbs=nutrition_data.get("carbs"),
    )
    db.add(nutrition)
    db.flush()
    return nutrition


def log_scan(db: Session, barcode: str, result: str, user_id: int = 1) -> ScanHistory:
    scan = ScanHistory(
        user_id=user_id,
        barcode=str(barcode),
        scan_time=_local_now_str(),
        result=str(result or ""),
    )
    db.add(scan)
    db.flush()
    return scan


def log_food_consumption(
    db: Session,
    barcode: str,
    product_name: str,
    calories: Optional[float],
    fat: Optional[float] = None,
    saturated_fat: Optional[float] = None,
    trans_fat: Optional[float] = None,
    sugar: Optional[float] = None,
    salt: Optional[float] = None,
    protein: Optional[float] = None,
    fiber: Optional[float] = None,
    carbs: Optional[float] = None,
    user_id: int = 1,
) -> FoodLog:
    if calories is None:
        return None
    log = FoodLog(
        user_id=user_id,
        barcode=str(barcode),
        product_name=str(product_name or ""),
        calories=calories,
        fat=fat,
        saturated_fat=saturated_fat,
        trans_fat=trans_fat,
        sugar=sugar,
        salt=salt,
        protein=protein,
        fiber=fiber,
        carbs=carbs,
        consumed_at=_local_now_str(),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_recent_scans(db: Session, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ScanHistory.id, ScanHistory.barcode, ScanHistory.result, ScanHistory.scan_time)
        .where(ScanHistory.user_id == int(user_id))
        .order_by(ScanHistory.scan_time.desc())
        .limit(int(limit))
    ).all()

    return [{"id": r[0], "barcode": r[1], "result": r[2], "scan_time": r[3]} for r in rows]


def get_today_calories(db: Session, user_id: int) -> float:
    start, end = _local_day_bounds()
    total = db.execute(
        select(func.coalesce(func.sum(FoodLog.calories), 0.0)).where(
            FoodLog.user_id == int(user_id),
            and_(FoodLog.consumed_at >= start, FoodLog.consumed_at < end),
        )
    ).scalar_one_or_none()
    if total is None:
        total = 0.0
    return float(total or 0.0)


def get_remaining_calories(db: Session, daily_limit: float, user_id: int) -> float:
    consumed = get_today_calories(db, user_id=user_id)
    return float(daily_limit) - float(consumed)


def get_product_name_candidates(db: Session, limit: Optional[int] = None) -> list[str]:
    stmt = (
        select(Product.product_name)
        .where(Product.product_name.is_not(None))
        .order_by(Product.created_at.desc())
    )
    if limit is not None and limit > 0:
        stmt = stmt.limit(int(limit))
    rows = db.execute(stmt).all()
    return [str(r[0]) for r in rows if r and r[0]]


def _normalize_name(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _similarity(query: str, name: str) -> float:
    q = (query or "").strip().lower()
    n = (name or "").strip().lower()
    if not q or not n:
        return 0.0

    return max(
        float(fuzz.token_set_ratio(q, n)),
        float(fuzz.partial_ratio(q, n)),
    )


def _format_product_fuzzy_result(
    product: Product,
    nutrition: Nutrition | None,
    query: str,
    matched_name: str,
    similarity: float,
) -> dict[str, Any]:
    return {
        "id": product.id,
        "barcode": product.barcode,
        "product_name": product.product_name,
        "brand": product.brand,
        "nutriscore": product.nutriscore,
        "ingredients": product.ingredients,
        "additives": product.additives,
        "created_at": product.created_at,
        "calories": nutrition.calories if nutrition else None,
        "fat": nutrition.fat if nutrition else None,
        "saturated_fat": nutrition.saturated_fat if nutrition else None,
        "trans_fat": nutrition.trans_fat if nutrition else None,
        "sugar": nutrition.sugar if nutrition else None,
        "salt": nutrition.salt if nutrition else None,
        "protein": nutrition.protein if nutrition else None,
        "fiber": nutrition.fiber if nutrition else None,
        "carbs": nutrition.carbs if nutrition else None,
        "_match": {
            "query": query,
            "matched_name": matched_name,
            "similarity": similarity,
        },
    }


def get_product_by_name_fuzzy(db: Session, query: str, min_similarity: float = 80.0) -> Optional[dict[str, Any]]:
    q = _normalize_name(query)
    if not q:
        return None

    # 1. Exact case-insensitive SQL match
    stmt_exact = (
        select(Product, Nutrition)
        .outerjoin(Nutrition, Nutrition.product_id == Product.id)
        .where(func.lower(func.trim(Product.product_name)) == q.lower())
        .order_by(Product.id.desc())
    )
    exact_row = db.execute(stmt_exact).first()
    if exact_row is not None:
        p: Product = exact_row[0]
        n: Nutrition | None = exact_row[1]
        return _format_product_fuzzy_result(p, n, q, p.product_name, 100.0)

    # 2. SQL LIKE-based filtering with wildcard escaping
    escaped_q = q.replace("/", "//").replace("%", "/%").replace("_", "/_")
    term = f"%{escaped_q}%"
    stmt_like = (
        select(Product, Nutrition)
        .outerjoin(Nutrition, Nutrition.product_id == Product.id)
        .where(Product.product_name.like(term, escape="/"))
        .order_by(Product.id.desc())
    )
    like_rows = db.execute(stmt_like).all()
    if like_rows:
        best_like_row = None
        best_like_score = 0.0
        for r in like_rows:
            prod: Product = r[0]
            score = _similarity(q, prod.product_name)
            if score > best_like_score:
                best_like_score = score
                best_like_row = r

        if best_like_row is not None and best_like_score >= float(min_similarity):
            p: Product = best_like_row[0]
            n: Nutrition | None = best_like_row[1]
            return _format_product_fuzzy_result(p, n, q, p.product_name, best_like_score)

    # 3. Database-wide candidate fuzzy matching fallback (without arbitrary 400-truncation)
    candidates = get_product_name_candidates(db, limit=None)
    best_name: str | None = None
    best_score = 0.0
    for name in candidates:
        score = _similarity(q, name)
        if score > best_score:
            best_score = score
            best_name = name

    if best_name is None or best_score < float(min_similarity):
        return None

    stmt_best = (
        select(Product, Nutrition)
        .outerjoin(Nutrition, Nutrition.product_id == Product.id)
        .where(func.lower(Product.product_name) == best_name.strip().lower())
        .order_by(Product.id.desc())
    )
    row = db.execute(stmt_best).first()
    if row is None:
        return None

    p: Product = row[0]
    n: Nutrition | None = row[1]
    return _format_product_fuzzy_result(p, n, q, best_name, best_score)


def ensure_default_user(db: Session) -> Optional[User]:
    user = db.execute(select(User).where(User.id == 1)).scalar_one_or_none()
    if user is not None:
        return user

    import os
    env = (os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()
    if env in {"production", "prod"} or str(os.getenv("SKIP_DEFAULT_USER", "")).strip().lower() in {"1", "true", "yes"}:
        return None

    user = User(
        id=1,
        name=None,
        email="default@local",
        hashed_password="",
        daily_calorie_limit=2000,
        diet_type=None,
        created_at=_utc_now_str(),
    )
    db.add(user)
    db.flush()
    return user


def get_user_profile(db: Session, user_id: int) -> dict[str, Any] | None:
    user = db.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
    if user is None:
        return None
    return {
        "name": user.name,
        "email": user.email,
        "age": getattr(user, "age", None),
        "weight": getattr(user, "weight", None),
        "height": getattr(user, "height", None),
        "daily_calorie_limit": user.daily_calorie_limit,
        "diet_type": user.diet_type,
        "allergens": getattr(user, "allergens", None),
        "goal_type": user.goal_type,
        "goal_target_days": user.goal_target_days,
        "goal_started_at": user.goal_started_at,
    }


def update_user_profile(
    db: Session,
    user_id: int,
    *,
    name: str | None = None,
    daily_calorie_limit: int | None = None,
    diet_type: str | None = None,
    allergens: str | None = None,
    age: int | None = None,
    weight: float | None = None,
    height: float | None = None,
    goal_type: str | None = None,
    goal_target_days: int | None = None,
) -> dict[str, Any] | None:
    user = db.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
    if user is None:
        return None

    if name is not None:
        user.name = name
    if daily_calorie_limit is not None:
        user.daily_calorie_limit = int(daily_calorie_limit)
    if diet_type is not None:
        user.diet_type = diet_type
    if allergens is not None:
        user.allergens = allergens
    if age is not None:
        user.age = int(age)
    if weight is not None:
        user.weight = float(weight)
    if height is not None:
        user.height = float(height)
    if goal_type is not None:
        user.goal_type = goal_type
        user.goal_started_at = date.today().isoformat()
    if goal_target_days is not None:
        user.goal_target_days = int(goal_target_days)

    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "name": user.name,
        "email": user.email,
        "age": getattr(user, "age", None),
        "weight": getattr(user, "weight", None),
        "height": getattr(user, "height", None),
        "daily_calorie_limit": user.daily_calorie_limit,
        "diet_type": user.diet_type,
        "allergens": getattr(user, "allergens", None),
        "goal_type": user.goal_type,
        "goal_target_days": user.goal_target_days,
        "goal_started_at": user.goal_started_at,
    }



def get_scan_counts(db: Session, user_id: int) -> dict[str, int]:
    total = db.execute(
        select(func.count(ScanHistory.id)).where(ScanHistory.user_id == int(user_id))
    ).scalar_one()

    week_start = (date.today() - timedelta(days=7)).isoformat()
    this_week = db.execute(
        select(func.count(ScanHistory.id)).where(
            ScanHistory.user_id == int(user_id),
            func.substr(ScanHistory.scan_time, 1, 10) >= week_start,
        )
    ).scalar_one()

    return {"total_scans": int(total or 0), "scans_this_week": int(this_week or 0)}


def get_most_scanned_product(db: Session, user_id: int) -> dict[str, Any] | None:
    row = db.execute(
        select(ScanHistory.barcode, func.count(ScanHistory.id).label("cnt"))
        .where(ScanHistory.user_id == int(user_id))
        .group_by(ScanHistory.barcode)
        .order_by(func.count(ScanHistory.id).desc())
        .limit(1)
    ).first()

    if row is None:
        return None

    barcode = str(row[0])
    count = int(row[1] or 0)

    product = db.execute(select(Product).where(Product.barcode == barcode)).scalar_one_or_none()
    name = product.product_name if product is not None else None

    return {"barcode": barcode, "name": name, "count": count}


def get_decision_counts(db: Session, user_id: int) -> dict[str, int]:
    safe = db.execute(
        select(func.count(ScanHistory.id)).where(
            ScanHistory.user_id == int(user_id),
            func.upper(ScanHistory.result) == "SAFE",
        )
    ).scalar_one()
    moderate = db.execute(
        select(func.count(ScanHistory.id)).where(
            ScanHistory.user_id == int(user_id),
            func.upper(ScanHistory.result) == "MODERATE",
        )
    ).scalar_one()
    avoid = db.execute(
        select(func.count(ScanHistory.id)).where(
            ScanHistory.user_id == int(user_id),
            func.upper(ScanHistory.result) == "AVOID",
        )
    ).scalar_one()
    return {"SAFE": int(safe or 0), "MODERATE": int(moderate or 0), "AVOID": int(avoid or 0)}


def get_average_health_score(db: Session, user_id: int) -> float:
    """Approximate average health score derived from scan decision.

    We only store decision string in scan_history. Without storing health score
    per scan, we map:
    - SAFE -> 80
    - MODERATE -> 55
    - AVOID -> 30
    """

    rows = db.execute(
        select(func.upper(ScanHistory.result)).where(ScanHistory.user_id == int(user_id))
    ).all()
    if not rows:
        return 0.0

    total = 0.0
    for (res,) in rows:
        r = str(res or "").upper()
        if r == "SAFE":
            total += 80.0
        elif r == "MODERATE":
            total += 55.0
        elif r == "AVOID":
            total += 30.0
        else:
            total += 0.0

    return float(total) / float(len(rows))


def get_today_food_logs(db: Session, user_id: int) -> list[dict]:
    start, end = _local_day_bounds()
    rows = (
        db.execute(
            select(
                FoodLog.id,
                FoodLog.barcode,
                FoodLog.product_name,
                FoodLog.calories,
                FoodLog.consumed_at,
                FoodLog.fat.label("food_log_fat"),
                FoodLog.saturated_fat.label("food_log_saturated_fat"),
                FoodLog.trans_fat.label("food_log_trans_fat"),
                FoodLog.sugar.label("food_log_sugar"),
                FoodLog.salt.label("food_log_salt"),
                FoodLog.protein.label("food_log_protein"),
                FoodLog.fiber.label("food_log_fiber"),
                FoodLog.carbs.label("food_log_carbs"),
                Product.ingredients,
                Product.additives,
                Nutrition.fat.label("nutrition_fat"),
                Nutrition.saturated_fat.label("nutrition_saturated_fat"),
                Nutrition.trans_fat.label("nutrition_trans_fat"),
                Nutrition.sugar.label("nutrition_sugar"),
                Nutrition.salt.label("nutrition_salt"),
                Nutrition.protein.label("nutrition_protein"),
                Nutrition.fiber.label("nutrition_fiber"),
                Nutrition.carbs.label("nutrition_carbs"),
            )
            .outerjoin(Product, FoodLog.barcode == Product.barcode)
            .outerjoin(Nutrition, Nutrition.product_id == Product.id)
            .where(
                FoodLog.user_id == int(user_id),
                and_(FoodLog.consumed_at >= start, FoodLog.consumed_at < end),
            )
            .order_by(FoodLog.consumed_at.desc())
        )
        .all()
    )
    return [
        {
            "id": row.id,
            "barcode": row.barcode,
            "product_name": row.product_name,
            "calories": row.calories,
            "fat": row.food_log_fat if row.food_log_fat is not None else row.nutrition_fat,
            "saturated_fat": row.food_log_saturated_fat if row.food_log_saturated_fat is not None else row.nutrition_saturated_fat,
            "trans_fat": row.food_log_trans_fat if row.food_log_trans_fat is not None else row.nutrition_trans_fat,
            "sugar": row.food_log_sugar if row.food_log_sugar is not None else row.nutrition_sugar,
            "salt": row.food_log_salt if row.food_log_salt is not None else row.nutrition_salt,
            "protein": row.food_log_protein if row.food_log_protein is not None else row.nutrition_protein,
            "fiber": row.food_log_fiber if row.food_log_fiber is not None else row.nutrition_fiber,
            "carbs": row.food_log_carbs if row.food_log_carbs is not None else row.nutrition_carbs,
            "consumed_at": row.consumed_at,
            "ingredients": row.ingredients,
            "additives": row.additives,
        }
        for row in rows
    ]


def get_week_food_logs(db: Session, user_id: int) -> dict[str, list[dict]]:
    today = datetime.now().date()
    start_date = today - timedelta(days=6)
    start_date_str = start_date.isoformat()
    rows = (
        db.execute(
            select(
                func.substr(FoodLog.consumed_at, 1, 10).label("date"),
                FoodLog.id,
                FoodLog.barcode,
                FoodLog.product_name,
                FoodLog.calories,
                FoodLog.consumed_at,
                FoodLog.fat.label("food_log_fat"),
                FoodLog.saturated_fat.label("food_log_saturated_fat"),
                FoodLog.trans_fat.label("food_log_trans_fat"),
                FoodLog.sugar.label("food_log_sugar"),
                FoodLog.salt.label("food_log_salt"),
                FoodLog.protein.label("food_log_protein"),
                FoodLog.fiber.label("food_log_fiber"),
                FoodLog.carbs.label("food_log_carbs"),
                Product.ingredients,
                Product.additives,
                Nutrition.fat.label("nutrition_fat"),
                Nutrition.saturated_fat.label("nutrition_saturated_fat"),
                Nutrition.trans_fat.label("nutrition_trans_fat"),
                Nutrition.sugar.label("nutrition_sugar"),
                Nutrition.salt.label("nutrition_salt"),
                Nutrition.protein.label("nutrition_protein"),
                Nutrition.fiber.label("nutrition_fiber"),
                Nutrition.carbs.label("nutrition_carbs"),
            )
            .outerjoin(Product, FoodLog.barcode == Product.barcode)
            .outerjoin(Nutrition, Nutrition.product_id == Product.id)
            .where(
                FoodLog.user_id == int(user_id),
                func.substr(FoodLog.consumed_at, 1, 10) >= start_date_str,
            )
            .order_by(FoodLog.consumed_at.desc())
        )
        .all()
    )
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        date_str = str(row.date)
        grouped.setdefault(date_str, []).append(
            {
                "id": row.id,
                "barcode": row.barcode,
                "product_name": row.product_name,
                "calories": row.calories,
                "fat": row.food_log_fat if row.food_log_fat is not None else row.nutrition_fat,
                "saturated_fat": row.food_log_saturated_fat if row.food_log_saturated_fat is not None else row.nutrition_saturated_fat,
                "trans_fat": row.food_log_trans_fat if row.food_log_trans_fat is not None else row.nutrition_trans_fat,
                "sugar": row.food_log_sugar if row.food_log_sugar is not None else row.nutrition_sugar,
                "salt": row.food_log_salt if row.food_log_salt is not None else row.nutrition_salt,
                "protein": row.food_log_protein if row.food_log_protein is not None else row.nutrition_protein,
                "fiber": row.food_log_fiber if row.food_log_fiber is not None else row.nutrition_fiber,
                "carbs": row.food_log_carbs if row.food_log_carbs is not None else row.nutrition_carbs,
                "consumed_at": row.consumed_at,
                "ingredients": row.ingredients,
                "additives": row.additives,
            }
        )
    return grouped


def delete_food_log(db: Session, log_id: int, user_id: int) -> bool:
    """Securely delete a food log entry scoped to the owner user_id."""
    log = db.query(FoodLog).filter(FoodLog.id == int(log_id), FoodLog.user_id == int(user_id)).first()
    if log is None:
        return False
    db.delete(log)
    db.commit()
    return True


def update_food_log(db: Session, log_id: int, user_id: int, updates: dict[str, Any]) -> Optional[FoodLog]:
    """Securely update a food log entry scoped to the owner user_id."""
    log = db.query(FoodLog).filter(FoodLog.id == int(log_id), FoodLog.user_id == int(user_id)).first()
    if log is None:
        return None

    allowed_fields = {
        "product_name",
        "calories",
        "fat",
        "saturated_fat",
        "trans_fat",
        "sugar",
        "salt",
        "protein",
        "fiber",
        "carbs",
    }
    for field, val in updates.items():
        if field in allowed_fields and val is not None:
            setattr(log, field, val)

    db.commit()
    db.refresh(log)
    return log


def get_enrichment_candidates(
    db: Session,
    limit: int = 50,
    stale_only: bool = False,
    incomplete_only: bool = False,
    cache_ttl_days: int = 30,
) -> list[Product]:
    """Retrieve products from local database eligible for background enrichment.

    Candidates are selected if:
    - Data is stale (last_fetched_at is NULL, empty, or older than cache_ttl_days).
    - Nutrition is incomplete (nutrition_completeness < 1.0 or missing core mandatory nutrients).
    """
    from sqlalchemy import or_

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=cache_ttl_days)
    cutoff_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
    cutoff_iso = cutoff_dt.isoformat()

    query = select(Product).outerjoin(Nutrition, Product.id == Nutrition.product_id)

    stale_cond = or_(
        Product.last_fetched_at.is_(None),
        Product.last_fetched_at == "",
        Product.last_fetched_at < cutoff_str,
        Product.last_fetched_at < cutoff_iso,
    )

    incomplete_cond = or_(
        Product.nutrition_completeness.is_(None),
        Product.nutrition_completeness < 1.0,
        Nutrition.id.is_(None),
        Nutrition.calories.is_(None),
        Nutrition.sugar.is_(None),
        Nutrition.fat.is_(None),
        and_(Nutrition.salt.is_(None), Nutrition.sodium.is_(None)),
    )

    if stale_only:
        query = query.where(stale_cond)
    elif incomplete_only:
        query = query.where(incomplete_cond)
    else:
        query = query.where(or_(stale_cond, incomplete_cond))

    query = query.order_by(Product.id.desc()).limit(max(1, min(limit, 500)))
    return list(db.execute(query).scalars().all())
