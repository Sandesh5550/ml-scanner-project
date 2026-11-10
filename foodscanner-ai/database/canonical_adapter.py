from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from database.models import Nutrition, Product, ProductBarcodeAlias
from schemas.product import (
    CanonicalProduct,
    DataConfidence,
    DataProvenance,
    FieldProvenance,
    Nutrition100g,
)

logger = logging.getLogger(__name__)


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_iso_datetime(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        # Handles '2026-11-10 01:00:00' or ISO format
        if "T" not in val and " " in val:
            return datetime.fromisoformat(val.replace(" ", "T")).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _parse_field_provenance(json_str: Optional[str]) -> Dict[str, FieldProvenance]:
    if not json_str:
        return {}
    try:
        raw_dict = json.loads(json_str)
        if not isinstance(raw_dict, dict):
            return {}
        result: Dict[str, FieldProvenance] = {}
        for field_name, prov_data in raw_dict.items():
            if isinstance(prov_data, dict):
                src_str = prov_data.get("source", "local_db")
                try:
                    source_enum = DataProvenance(src_str)
                except ValueError:
                    source_enum = DataProvenance.LOCAL_DB

                conf_str = prov_data.get("confidence", "UNVERIFIED")
                try:
                    conf_enum = DataConfidence(conf_str)
                except ValueError:
                    conf_enum = DataConfidence.UNVERIFIED

                ts = _parse_iso_datetime(prov_data.get("timestamp"))
                result[field_name] = FieldProvenance(
                    source=source_enum,
                    confidence=conf_enum,
                    timestamp=ts,
                )
        return result
    except Exception as exc:
        logger.warning(f"Failed to parse field_provenance_json: {exc}")
        return {}


def _serialize_field_provenance(field_prov: Dict[str, FieldProvenance]) -> Optional[str]:
    if not field_prov:
        return None
    out: Dict[str, Any] = {}
    for k, v in field_prov.items():
        out[k] = {
            "source": v.source.value,
            "confidence": v.confidence.value,
            "timestamp": v.timestamp.isoformat() if v.timestamp else None,
        }
    return json.dumps(out, separators=(",", ":"))


def db_product_to_canonical(
    product: Product,
    nutrition: Optional[Nutrition] = None,
    aliases: Optional[List[ProductBarcodeAlias]] = None,
) -> CanonicalProduct:
    """Convert a database Product entity into a CanonicalProduct data contract.
    
    Invariants enforced:
    - Missing disclosures remain None (never fabricated or converted to 0.0).
    - Assessment status and confidence are strictly derived from validated disclosures.
    - Aliases and field provenance are mapped deterministically.
    """
    effective_nutrition = nutrition if nutrition is not None else product.nutrition
    nutr_model: Nutrition100g
    if effective_nutrition is not None:
        nutr_model = Nutrition100g(
            calories=effective_nutrition.calories,
            fat=effective_nutrition.fat,
            saturated_fat=effective_nutrition.saturated_fat,
            trans_fat=effective_nutrition.trans_fat,
            carbs=effective_nutrition.carbs,
            sugar=effective_nutrition.sugar,
            protein=effective_nutrition.protein,
            fiber=effective_nutrition.fiber,
            salt=effective_nutrition.salt,
            sodium=getattr(effective_nutrition, "sodium", None),
        )
    else:
        nutr_model = Nutrition100g()

    # Alternate barcodes resolution
    effective_aliases = aliases if aliases is not None else getattr(product, "aliases", [])
    alt_barcodes = [a.alias_barcode for a in effective_aliases if a.alias_barcode] if effective_aliases else []

    # Provenance source mapping
    raw_source = getattr(product, "data_source", None) or "local_db"
    try:
        primary_prov = DataProvenance(raw_source)
    except ValueError:
        primary_prov = DataProvenance.LOCAL_DB

    field_prov = _parse_field_provenance(getattr(product, "field_provenance_json", None))
    last_fetched = _parse_iso_datetime(getattr(product, "last_fetched_at", None))
    last_updated = _parse_iso_datetime(getattr(product, "last_updated_at", None))

    canonical_id_val = product.canonical_id or str(product.barcode)

    return CanonicalProduct(
        canonical_id=canonical_id_val,
        barcode=str(product.barcode),
        alternate_barcodes=alt_barcodes,
        product_name=str(product.product_name or ""),
        brand=product.brand,
        category=getattr(product, "category", None),
        variant_name=None,
        serving_size=None,
        nutrition=nutr_model,
        ingredients=product.ingredients,
        additives=product.additives,
        nutriscore=product.nutriscore,
        is_nutriscore_predicted=False,
        primary_provenance=primary_prov,
        field_provenance=field_prov,
        last_fetched_at=last_fetched,
        last_updated_at=last_updated,
    )


def canonical_to_db_models(
    canonical: CanonicalProduct,
    created_at: Optional[str] = None,
) -> Tuple[Product, Nutrition, List[ProductBarcodeAlias]]:
    """Convert a CanonicalProduct contract into SQLAlchemy ORM entities ready for persistence."""
    now_str = created_at or _utc_now_str()

    last_fetched_str = canonical.last_fetched_at.strftime("%Y-%m-%d %H:%M:%S") if canonical.last_fetched_at else None
    last_updated_str = canonical.last_updated_at.strftime("%Y-%m-%d %H:%M:%S") if canonical.last_updated_at else None
    prov_json = _serialize_field_provenance(canonical.field_provenance)

    product = Product(
        barcode=canonical.barcode,
        canonical_id=canonical.canonical_id,
        product_name=canonical.product_name,
        brand=canonical.brand,
        category=canonical.category,
        nutriscore=canonical.nutriscore,
        ingredients=canonical.ingredients,
        additives=canonical.additives,
        data_source=canonical.primary_provenance.value,
        data_confidence=canonical.overall_confidence.value,
        nutrition_completeness=canonical.nutrition_completeness,
        ingredients_completeness=canonical.ingredients_completeness,
        last_fetched_at=last_fetched_str,
        last_updated_at=last_updated_str,
        field_provenance_json=prov_json,
        created_at=now_str,
    )

    n = canonical.nutrition
    nutrition = Nutrition(
        calories=n.calories,
        fat=n.fat,
        saturated_fat=n.saturated_fat,
        trans_fat=n.trans_fat,
        carbs=n.carbs,
        sugar=n.sugar,
        protein=n.protein,
        fiber=n.fiber,
        salt=n.salt,
        sodium=n.sodium,
    )

    aliases = [
        ProductBarcodeAlias(
            alias_barcode=alt_b,
            alias_type="alternate",
            created_at=now_str,
        )
        for alt_b in canonical.alternate_barcodes
        if alt_b and alt_b != canonical.barcode
    ]

    return product, nutrition, aliases


def apply_canonical_to_db_product(
    canonical: CanonicalProduct,
    product: Product,
    nutrition: Optional[Nutrition] = None,
) -> None:
    """Non-destructively apply a CanonicalProduct's attributes onto an existing DB Product & Nutrition entity."""
    if canonical.product_name and not product.product_name:
        product.product_name = canonical.product_name
    if canonical.brand and not product.brand:
        product.brand = canonical.brand
    if canonical.category:
        product.category = canonical.category
    if canonical.canonical_id and not product.canonical_id:
        product.canonical_id = canonical.canonical_id
    if canonical.nutriscore and not product.nutriscore:
        product.nutriscore = canonical.nutriscore
    if canonical.ingredients and not product.ingredients:
        product.ingredients = canonical.ingredients
    if canonical.additives and not product.additives:
        product.additives = canonical.additives

    product.data_source = canonical.primary_provenance.value
    product.data_confidence = canonical.overall_confidence.value
    product.nutrition_completeness = canonical.nutrition_completeness
    product.ingredients_completeness = canonical.ingredients_completeness

    if canonical.last_fetched_at:
        product.last_fetched_at = canonical.last_fetched_at.strftime("%Y-%m-%d %H:%M:%S")
    product.last_updated_at = _utc_now_str()

    if canonical.field_provenance:
        product.field_provenance_json = _serialize_field_provenance(canonical.field_provenance)

    target_nutrition = nutrition if nutrition is not None else product.nutrition
    if target_nutrition is not None:
        n = canonical.nutrition
        for attr in ["calories", "fat", "saturated_fat", "trans_fat", "carbs", "sugar", "protein", "fiber", "salt", "sodium"]:
            val = getattr(n, attr, None)
            if val is not None:
                setattr(target_nutrition, attr, val)
