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


CONFIDENCE_RANK: Dict[DataConfidence, int] = {
    DataConfidence.HIGH: 4,
    DataConfidence.MEDIUM: 3,
    DataConfidence.LOW: 2,
    DataConfidence.UNVERIFIED: 1,
}

PROVENANCE_RANK: Dict[DataProvenance, int] = {
    DataProvenance.ADMIN_VERIFIED: 10,
    DataProvenance.MANUAL: 8,
    DataProvenance.OCR: 6,
    DataProvenance.OFF_API_V3: 5,
    DataProvenance.OFF_API_V2: 4,
    DataProvenance.INDIAN_CSV: 3,
    DataProvenance.OFF_CSV: 2,
    DataProvenance.LOCAL_DB: 1,
}


def _resolve_field(
    field_name: str,
    val_existing: Any,
    val_incoming: Any,
    prov_existing: Optional[FieldProvenance],
    prov_incoming: Optional[FieldProvenance],
    ts_existing: Optional[datetime],
    ts_incoming: Optional[datetime],
    existing_completeness: float,
    incoming_completeness: float,
    is_text: bool = False,
) -> Tuple[Any, Optional[FieldProvenance]]:
    """Deterministically resolve a single field between existing and incoming records.

    Rules strictly enforced:
    1. Non-Null Invariant: Existing non-null fields are never overwritten with None or empty text.
    2. Admin Verification Lock: Fields with provenance == ADMIN_VERIFIED are immutable to automated external updates.
    3. Missing Existing Value: If existing field is None or empty and incoming is present, incoming is adopted.
    4. Explicit Zero Semantics: Explicit 0.0 is a valid numeric disclosure and is preserved.
    5. Quality & Confidence Precedence: Higher confidence rank strictly wins over lower confidence rank.
    6. Provenance Precedence: When confidences are equal, higher provenance rank wins.
    7. Freshness & Completeness: When confidence and provenance are equal, fresher timestamp wins
       provided incoming completeness is >= existing completeness.
    """
    present_e = val_existing is not None and (not is_text or str(val_existing).strip() != "")
    present_i = val_incoming is not None and (not is_text or str(val_incoming).strip() != "")

    # Case 1: Neither present
    if not present_e and not present_i:
        return None, None

    # Case 2: Only existing present -> preserve existing
    if present_e and not present_i:
        return val_existing, prov_existing

    # Case 3: Only incoming present -> adopt incoming
    if not present_e and present_i:
        return val_incoming, prov_incoming

    # Case 4: Both present -> Conflict resolution
    # 4A. ADMIN_VERIFIED Protection
    is_admin_e = prov_existing is not None and prov_existing.source == DataProvenance.ADMIN_VERIFIED
    is_admin_i = prov_incoming is not None and prov_incoming.source == DataProvenance.ADMIN_VERIFIED

    if is_admin_e and not is_admin_i:
        return val_existing, prov_existing
    if is_admin_i and not is_admin_e:
        return val_incoming, prov_incoming

    # 4B. Confidence Comparison
    conf_e = prov_existing.confidence if prov_existing else DataConfidence.UNVERIFIED
    conf_i = prov_incoming.confidence if prov_incoming else DataConfidence.UNVERIFIED
    c_diff = CONFIDENCE_RANK.get(conf_i, 1) - CONFIDENCE_RANK.get(conf_e, 1)
    if c_diff > 0:
        return val_incoming, prov_incoming
    if c_diff < 0:
        return val_existing, prov_existing

    # 4C. Provenance Ranking (Confidences are equal)
    source_e = prov_existing.source if prov_existing else DataProvenance.LOCAL_DB
    source_i = prov_incoming.source if prov_incoming else DataProvenance.LOCAL_DB
    p_diff = PROVENANCE_RANK.get(source_i, 1) - PROVENANCE_RANK.get(source_e, 1)
    if p_diff > 0:
        return val_incoming, prov_incoming
    if p_diff < 0:
        return val_existing, prov_existing

    # 4D. Freshness and Completeness (Confidence and Provenance are equal)
    if ts_incoming and ts_existing and ts_incoming > ts_existing and incoming_completeness >= existing_completeness:
        return val_incoming, prov_incoming

    return val_existing, prov_existing


def merge_canonical_products(
    existing: CanonicalProduct,
    incoming: CanonicalProduct,
) -> CanonicalProduct:
    """Deterministically merge an incoming CanonicalProduct into an existing CanonicalProduct.

    Invariants enforced:
    - Identity integrity: Refuses to merge products with mismatched barcodes/identities.
    - Preserves higher-quality and ADMIN_VERIFIED disclosures.
    - Missing disclosures remain None (never converted to 0.0).
    - Explicit declared zero values remain preserved.
    - Idempotent and conflict-deterministic.
    """
    # 1. Identity Guard
    same_barcode = existing.barcode == incoming.barcode
    is_alias = (
        incoming.barcode in existing.alternate_barcodes
        or existing.barcode in incoming.alternate_barcodes
    )
    same_canonical_id = (
        existing.canonical_id
        and incoming.canonical_id
        and existing.canonical_id == incoming.canonical_id
    )

    if not (same_barcode or is_alias or same_canonical_id):
        raise ValueError(
            f"Cannot merge products with different identities: "
            f"existing barcode='{existing.barcode}' (id='{existing.canonical_id}'), "
            f"incoming barcode='{incoming.barcode}' (id='{incoming.canonical_id}'). "
            f"Barcodes are unique formulation identifiers."
        )

    now_dt = datetime.now(timezone.utc)
    ts_e = existing.last_fetched_at or existing.last_updated_at
    ts_i = incoming.last_fetched_at or incoming.last_updated_at
    comp_e = existing.nutrition_completeness
    comp_i = incoming.nutrition_completeness

    merged_prov: Dict[str, FieldProvenance] = {}

    def get_prov(prod: CanonicalProduct, fname: str) -> Optional[FieldProvenance]:
        if fname in prod.field_provenance:
            return prod.field_provenance[fname]
        ts = prod.last_fetched_at or prod.last_updated_at
        return FieldProvenance(
            source=prod.primary_provenance,
            confidence=prod.overall_confidence,
            timestamp=ts,
        )

    # 2. Metadata Fields Resolution
    meta_fields = [
        ("product_name", True),
        ("brand", True),
        ("category", True),
        ("variant_name", True),
        ("serving_size", True),
        ("ingredients", True),
        ("additives", True),
        ("nutriscore", True),
    ]

    resolved_meta: Dict[str, Any] = {}
    for fname, is_txt in meta_fields:
        val_e = getattr(existing, fname, None)
        val_i = getattr(incoming, fname, None)
        p_e = get_prov(existing, fname) if val_e is not None else None
        p_i = get_prov(incoming, fname) if val_i is not None else None

        val_res, prov_res = _resolve_field(
            field_name=fname,
            val_existing=val_e,
            val_incoming=val_i,
            prov_existing=p_e,
            prov_incoming=p_i,
            ts_existing=ts_e,
            ts_incoming=ts_i,
            existing_completeness=comp_e,
            incoming_completeness=comp_i,
            is_text=is_txt,
        )
        resolved_meta[fname] = val_res
        if prov_res is not None:
            merged_prov[fname] = prov_res

    # Special handling for product_name: non-empty name is required by schema
    if not resolved_meta.get("product_name"):
        resolved_meta["product_name"] = existing.product_name or incoming.product_name

    # 3. Nutrition Fields Resolution
    nutr_fields = [
        "calories",
        "fat",
        "saturated_fat",
        "trans_fat",
        "carbs",
        "sugar",
        "protein",
        "fiber",
        "salt",
        "sodium",
    ]

    resolved_nutr: Dict[str, Any] = {}
    for nfield in nutr_fields:
        val_e = getattr(existing.nutrition, nfield, None)
        val_i = getattr(incoming.nutrition, nfield, None)
        p_e = get_prov(existing, nfield) if val_e is not None else None
        p_i = get_prov(incoming, nfield) if val_i is not None else None

        val_res, prov_res = _resolve_field(
            field_name=nfield,
            val_existing=val_e,
            val_incoming=val_i,
            prov_existing=p_e,
            prov_incoming=p_i,
            ts_existing=ts_e,
            ts_incoming=ts_i,
            existing_completeness=comp_e,
            incoming_completeness=comp_i,
            is_text=False,
        )
        resolved_nutr[nfield] = val_res
        if prov_res is not None:
            merged_prov[nfield] = prov_res

    # 4. Aliases / Alternate Barcodes
    all_aliases = set(existing.alternate_barcodes) | set(incoming.alternate_barcodes)
    # Never alias the primary barcode
    all_aliases.discard(existing.barcode)
    all_aliases.discard(incoming.barcode)
    # If incoming barcode differs from existing primary barcode, add it as alias
    if incoming.barcode != existing.barcode:
        all_aliases.add(incoming.barcode)
    merged_aliases = sorted(list(filter(None, all_aliases)))

    # 5. Timestamps & Primary Provenance
    timestamps = [t for t in [existing.last_fetched_at, incoming.last_fetched_at] if t is not None]
    max_last_fetched = max(timestamps) if timestamps else None

    # If any field has ADMIN_VERIFIED provenance, product primary provenance preserves ADMIN_VERIFIED
    has_admin = any(p.source == DataProvenance.ADMIN_VERIFIED for p in merged_prov.values())
    if has_admin or existing.primary_provenance == DataProvenance.ADMIN_VERIFIED:
        final_provenance = DataProvenance.ADMIN_VERIFIED
    elif incoming.last_fetched_at and (not existing.last_fetched_at or incoming.last_fetched_at >= existing.last_fetched_at):
        final_provenance = incoming.primary_provenance
    else:
        final_provenance = existing.primary_provenance

    # Nutriscore prediction flag
    is_pred = existing.is_nutriscore_predicted and incoming.is_nutriscore_predicted

    return CanonicalProduct(
        canonical_id=existing.canonical_id or incoming.canonical_id or str(existing.barcode),
        barcode=str(existing.barcode),
        alternate_barcodes=merged_aliases,
        product_name=str(resolved_meta["product_name"]),
        brand=resolved_meta.get("brand"),
        category=resolved_meta.get("category"),
        variant_name=resolved_meta.get("variant_name"),
        serving_size=resolved_meta.get("serving_size"),
        nutrition=Nutrition100g(**resolved_nutr),
        ingredients=resolved_meta.get("ingredients"),
        additives=resolved_meta.get("additives"),
        nutriscore=resolved_meta.get("nutriscore"),
        is_nutriscore_predicted=is_pred,
        primary_provenance=final_provenance,
        field_provenance=merged_prov,
        last_fetched_at=max_last_fetched,
        last_updated_at=now_dt,
    )


def apply_canonical_to_db_product(
    canonical: CanonicalProduct,
    product: Product,
    nutrition: Optional[Nutrition] = None,
) -> None:
    """Apply a CanonicalProduct's attributes onto an existing DB Product & Nutrition entity."""
    if canonical.product_name:
        product.product_name = canonical.product_name
    if canonical.brand is not None:
        product.brand = canonical.brand
    if canonical.category is not None:
        product.category = canonical.category
    if canonical.canonical_id:
        product.canonical_id = canonical.canonical_id
    if canonical.nutriscore is not None:
        product.nutriscore = canonical.nutriscore
    if canonical.ingredients is not None:
        product.ingredients = canonical.ingredients
    if canonical.additives is not None:
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
            setattr(target_nutrition, attr, getattr(n, attr, None))
