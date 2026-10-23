from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional


DISCLAIMER_TEXT = (
    "The system provides a rule-based assessment of product claims based on "
    "available product data and referenced regulatory criteria. It is not a legal certification."
)


@dataclass(frozen=True)
class RegulatorySource:
    authority: str
    document: str
    reference: str
    version: str
    effective_date: str


@dataclass(frozen=True)
class ClaimRule:
    rule_id: str
    claim_name: str
    normalized_claim: str
    regulatory_source: RegulatorySource
    required_nutrients: List[str]
    requires_ingredients: bool
    description: str


# ==============================================================================
# 1. VERSIONED FSSAI REGULATORY RULES CONFIGURATION
# ==============================================================================

_FSSAI_SOURCE_2018 = RegulatorySource(
    authority="FSSAI",
    document="Food Safety and Standards (Advertising and Claims) Regulations, 2018",
    reference="Schedule I (Nutrient Content Claims)",
    version="2018.1",
    effective_date="2019-07-01",
)

_FSSAI_NON_ADDITION_2018 = RegulatorySource(
    authority="FSSAI",
    document="Food Safety and Standards (Advertising and Claims) Regulations, 2018",
    reference="Regulation 4(5) & Schedule I (Non-Addition Claims - Sugars)",
    version="2018.1",
    effective_date="2019-07-01",
)

REGULATORY_RULES: Dict[str, ClaimRule] = {
    "sugar_free": ClaimRule(
        rule_id="FSSAI-ACR-2018-SCH1-SUG-FREE",
        claim_name="Sugar Free",
        normalized_claim="sugar_free",
        regulatory_source=_FSSAI_SOURCE_2018,
        required_nutrients=["sugar"],
        requires_ingredients=False,
        description="Product must contain not more than 0.5 g of sugars per 100 g (solids) or 100 ml (liquids).",
    ),
    "low_fat": ClaimRule(
        rule_id="FSSAI-ACR-2018-SCH1-FAT-LOW",
        claim_name="Low Fat",
        normalized_claim="low_fat",
        regulatory_source=_FSSAI_SOURCE_2018,
        required_nutrients=["fat"],
        requires_ingredients=False,
        description="Product must contain not more than 3 g of fat per 100 g for solid foods (or 1.5 g per 100 ml for liquids).",
    ),
    "low_sodium": ClaimRule(
        rule_id="FSSAI-ACR-2018-SCH1-SOD-LOW",
        claim_name="Low Sodium",
        normalized_claim="low_sodium",
        regulatory_source=_FSSAI_SOURCE_2018,
        required_nutrients=["salt"],
        requires_ingredients=False,
        description="Product must contain not more than 0.12 g (120 mg) of sodium per 100 g (equivalent to <= 0.3 g salt per 100 g).",
    ),
    "high_fibre": ClaimRule(
        rule_id="FSSAI-ACR-2018-SCH1-FBR-HIGH",
        claim_name="High Fibre",
        normalized_claim="high_fibre",
        regulatory_source=_FSSAI_SOURCE_2018,
        required_nutrients=["fiber"],
        requires_ingredients=False,
        description="Product must contain not less than 6 g of dietary fibre per 100 g (or 3 g per 100 kcal).",
    ),
    "high_protein": ClaimRule(
        rule_id="FSSAI-ACR-2018-SCH1-PROT-HIGH",
        claim_name="High Protein",
        normalized_claim="high_protein",
        regulatory_source=_FSSAI_SOURCE_2018,
        required_nutrients=["protein"],
        requires_ingredients=False,
        description="Product must contain not less than 20% of the Recommended Dietary Allowance (RDA) per 100 g (>= 10.8 g protein/100 g based on ICMR-NIN/FSSAI 54 g adult RDA).",
    ),
    "zero_trans_fat": ClaimRule(
        rule_id="FSSAI-ACR-2018-SCH1-TRANSFAT-FREE",
        claim_name="Zero Trans Fat",
        normalized_claim="zero_trans_fat",
        regulatory_source=_FSSAI_SOURCE_2018,
        required_nutrients=["trans_fat"],
        requires_ingredients=False,
        description="Product must contain less than 0.2 g of trans fatty acids per 100 g or 100 ml.",
    ),
    "no_added_sugar": ClaimRule(
        rule_id="FSSAI-ACR-2018-REG4-SUG-NO-ADDED",
        claim_name="No Added Sugar",
        normalized_claim="no_added_sugar",
        regulatory_source=_FSSAI_NON_ADDITION_2018,
        required_nutrients=["sugar"],
        requires_ingredients=True,
        description="Product must not contain any added sugars, syrups, honey, or concentrated fruit juices in its ingredients.",
    ),
}


# ==============================================================================
# 2. ADDED SUGARS INGREDIENT REGISTRY
# ==============================================================================

ADDED_SUGARS_PATTERNS = [
    r"(?<!no added )(?<!zero )(?<!without )(?<!no )\bsugars?\b(?![- ]free)",
    r"\bcane sugar\b",
    r"\bwhite sugar\b",
    r"\bbrown sugar\b",
    r"\bbeet sugar\b",
    r"\bsucrose\b",
    r"\bglucose\b",
    r"\bfructose\b",
    r"\bdextrose\b",
    r"\bmaltose\b",
    r"\bcorn syrup\b",
    r"\bhigh fructose corn syrup\b",
    r"\bhfcs\b",
    r"\binvert sugar\b",
    r"\binvert syrup\b",
    r"\bhoney\b",
    r"\bmolasses\b",
    r"\bgolden syrup\b",
    r"\bmaple syrup\b",
    r"\bjaggery\b",
    r"\bgur\b",
    r"\bkhandsari\b",
    r"\bconcentrated fruit juice\b",
    r"\bfruit juice concentrate\b",
    r"\bcane juice\b",
    r"\bliquid glucose\b",
    r"\bagave\b",
    r"\bmaltodextrin\b",
    r"\bmalt extract\b",
    r"\brice syrup\b",
    r"\bdate syrup\b",
]

_COMPILED_ADDED_SUGARS = [re.compile(p, re.IGNORECASE) for p in ADDED_SUGARS_PATTERNS]


# ==============================================================================
# 3. CLAIM NORMALIZATION
# ==============================================================================

_CLAIM_ALIAS_MAP: Dict[str, str] = {
    # Sugar Free
    "sugar free": "sugar_free",
    "sugar-free": "sugar_free",
    "zero sugar": "sugar_free",
    "0 sugar": "sugar_free",
    "no sugar": "sugar_free",
    "without sugar": "sugar_free",
    "0% sugar": "sugar_free",
    "sugarless": "sugar_free",
    "free of sugar": "sugar_free",

    # No Added Sugar
    "no added sugar": "no_added_sugar",
    "no-added-sugar": "no_added_sugar",
    "without added sugar": "no_added_sugar",
    "no sugar added": "no_added_sugar",
    "zero added sugar": "no_added_sugar",
    "0 added sugar": "no_added_sugar",
    "no added sugars": "no_added_sugar",

    # Low Fat
    "low fat": "low_fat",
    "low-fat": "low_fat",
    "low in fat": "low_fat",
    "little fat": "low_fat",

    # Low Sodium
    "low sodium": "low_sodium",
    "low-sodium": "low_sodium",
    "low salt": "low_sodium",
    "low-salt": "low_sodium",
    "low in sodium": "low_sodium",
    "low in salt": "low_sodium",
    "little sodium": "low_sodium",
    "little salt": "low_sodium",

    # High Fibre
    "high fibre": "high_fibre",
    "high fiber": "high_fibre",
    "high-fibre": "high_fibre",
    "high-fiber": "high_fibre",
    "rich in fibre": "high_fibre",
    "rich in fiber": "high_fibre",
    "high in fibre": "high_fibre",
    "high in fiber": "high_fibre",
    "excellent source of fibre": "high_fibre",
    "excellent source of fiber": "high_fibre",
    "fibre rich": "high_fibre",
    "fiber rich": "high_fibre",

    # High Protein
    "high protein": "high_protein",
    "high-protein": "high_protein",
    "rich in protein": "high_protein",
    "high in protein": "high_protein",
    "protein rich": "high_protein",
    "excellent source of protein": "high_protein",

    # Zero Trans Fat
    "zero trans fat": "zero_trans_fat",
    "trans fat free": "zero_trans_fat",
    "trans-fat free": "zero_trans_fat",
    "trans-fat-free": "zero_trans_fat",
    "0g trans fat": "zero_trans_fat",
    "0 trans fat": "zero_trans_fat",
    "no trans fat": "zero_trans_fat",
    "no trans-fat": "zero_trans_fat",
    "free of trans fat": "zero_trans_fat",
}


def normalize_claim_text(raw_text: str) -> str:
    """Normalize raw claim text by lowercasing, stripping punctuation, and mapping to canonical claim key."""
    if not raw_text or not isinstance(raw_text, str):
        return ""
    cleaned = raw_text.strip().lower()
    # Normalize hyphens and underscores to spaces
    cleaned = re.sub(r"[-_]+", " ", cleaned)
    # Remove leading/trailing non-alphanumeric chars (like quotes or exclamation marks)
    cleaned = re.sub(r"^[^\w%]+|[^\w%]+$", "", cleaned)
    # Collapse multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Check direct alias map
    if cleaned in _CLAIM_ALIAS_MAP:
        return _CLAIM_ALIAS_MAP[cleaned]

    # Check if raw lowercase with hyphens intact matches
    raw_clean = raw_text.strip().lower()
    if raw_clean in _CLAIM_ALIAS_MAP:
        return _CLAIM_ALIAS_MAP[raw_clean]

    # Return sanitized version as unknown claim key
    return cleaned.replace(" ", "_")


# ==============================================================================
# 4. HELPER UTILITIES
# ==============================================================================

def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        val_str = value.strip()
        if not val_str:
            return None
        # Remove trailing unit strings if present like "g", "mg", "kcal"
        val_str = re.sub(r"[a-zA-Z%]+$", "", val_str).strip()
    try:
        return float(val_str) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None


def _extract_ingredient_text(ingredients: Any) -> str:
    if not ingredients:
        return ""
    if isinstance(ingredients, list):
        return " ".join(str(i) for i in ingredients if i).lower()
    return str(ingredients).lower()


# ==============================================================================
# 5. VERIFICATION EVALUATION LOGIC
# ==============================================================================

def _verify_sugar_free(rule: ClaimRule, nutrition: dict, ingredients_text: str) -> dict:
    val = _to_float(nutrition.get("sugar"))
    if val is None:
        val = _to_float(nutrition.get("sugars"))
    if val is None:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Sugar content is missing from nutrition data; cannot verify 'Sugar Free'.",
            "evidence": {"declared_sugar": None, "threshold": 0.5, "unit": "g/100g"},
            "data_quality": "LOW",
        }
    if val <= 0.5:
        return {
            "status": "SUPPORTED",
            "reason": f"Sugar content ({val} g/100g) is within the FSSAI threshold (<= 0.5 g/100g).",
            "evidence": {"declared_sugar": val, "threshold": 0.5, "unit": "g/100g", "comparison": "<="},
            "data_quality": "HIGH",
        }
    return {
        "status": "NOT_SUPPORTED",
        "reason": f"Sugar content ({val} g/100g) exceeds the FSSAI limit of 0.5 g/100g for 'Sugar Free'.",
        "evidence": {"declared_sugar": val, "threshold": 0.5, "unit": "g/100g", "comparison": ">"},
        "data_quality": "HIGH",
    }


def _verify_low_fat(rule: ClaimRule, nutrition: dict, ingredients_text: str) -> dict:
    val = _to_float(nutrition.get("fat"))
    if val is None:
        val = _to_float(nutrition.get("total_fat"))
    if val is None:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Fat content is missing from nutrition data; cannot verify 'Low Fat'.",
            "evidence": {"declared_fat": None, "threshold": 3.0, "unit": "g/100g"},
            "data_quality": "LOW",
        }
    if val <= 3.0:
        return {
            "status": "SUPPORTED",
            "reason": f"Fat content ({val} g/100g) satisfies the FSSAI threshold (<= 3.0 g/100g for solid foods).",
            "evidence": {"declared_fat": val, "threshold": 3.0, "unit": "g/100g", "comparison": "<="},
            "data_quality": "HIGH",
        }
    return {
        "status": "NOT_SUPPORTED",
        "reason": f"Fat content ({val} g/100g) exceeds the FSSAI threshold of 3.0 g/100g for 'Low Fat'.",
        "evidence": {"declared_fat": val, "threshold": 3.0, "unit": "g/100g", "comparison": ">"},
        "data_quality": "HIGH",
    }


def _verify_low_sodium(rule: ClaimRule, nutrition: dict, ingredients_text: str) -> dict:
    salt_val = _to_float(nutrition.get("salt"))
    raw_sod = nutrition.get("sodium")
    if raw_sod is None:
        raw_sod = nutrition.get("sodium_content")

    sodium_val = None
    sod_mg = _to_float(nutrition.get("sodium_mg"))
    if sod_mg is not None:
        sodium_val = round(sod_mg / 1000.0, 4)
    elif raw_sod is not None:
        is_mg = isinstance(raw_sod, str) and "mg" in raw_sod.lower()
        val = _to_float(raw_sod)
        if val is not None:
            if is_mg or val > 1.5:
                # If explicit mg or >1.5g in 100g, interpreted as mg
                sodium_val = round(val / 1000.0, 4)
            else:
                sodium_val = val

    if sodium_val is None and salt_val is not None:
        sodium_val = round(salt_val / 2.5, 4)

    if sodium_val is None:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Neither sodium nor salt is declared in nutrition data; cannot verify 'Low Sodium'.",
            "evidence": {"declared_sodium": None, "declared_salt": None, "threshold_sodium": 0.12, "unit": "g/100g"},
            "data_quality": "LOW",
        }

    if sodium_val <= 0.12:
        return {
            "status": "SUPPORTED",
            "reason": f"Sodium content ({sodium_val} g/100g) satisfies the FSSAI threshold (<= 0.12 g or 120 mg/100g).",
            "evidence": {"sodium": sodium_val, "salt": salt_val, "threshold_sodium": 0.12, "unit": "g/100g", "comparison": "<="},
            "data_quality": "HIGH",
        }
    return {
        "status": "NOT_SUPPORTED",
        "reason": f"Sodium content ({sodium_val} g/100g) exceeds the FSSAI threshold of 0.12 g (120 mg/100g) for 'Low Sodium'.",
        "evidence": {"sodium": sodium_val, "salt": salt_val, "threshold_sodium": 0.12, "unit": "g/100g", "comparison": ">"},
        "data_quality": "HIGH",
    }


def _verify_high_fibre(rule: ClaimRule, nutrition: dict, ingredients_text: str) -> dict:
    val = _to_float(nutrition.get("fiber"))
    if val is None:
        val = _to_float(nutrition.get("fibre"))
    if val is None:
        val = _to_float(nutrition.get("dietary_fiber"))
    if val is None:
        val = _to_float(nutrition.get("dietary_fibre"))

    if val is None:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Dietary fibre is missing from nutrition data; cannot verify 'High Fibre'.",
            "evidence": {"declared_fiber": None, "threshold": 6.0, "unit": "g/100g"},
            "data_quality": "LOW",
        }
    if val >= 6.0:
        return {
            "status": "SUPPORTED",
            "reason": f"Dietary fibre ({val} g/100g) satisfies the FSSAI threshold (>= 6.0 g/100g).",
            "evidence": {"declared_fiber": val, "threshold": 6.0, "unit": "g/100g", "comparison": ">="},
            "data_quality": "HIGH",
        }
    return {
        "status": "NOT_SUPPORTED",
        "reason": f"Dietary fibre ({val} g/100g) is below the FSSAI threshold of 6.0 g/100g for 'High Fibre'.",
        "evidence": {"declared_fiber": val, "threshold": 6.0, "unit": "g/100g", "comparison": "<"},
        "data_quality": "HIGH",
    }


def _verify_high_protein(rule: ClaimRule, nutrition: dict, ingredients_text: str) -> dict:
    val = _to_float(nutrition.get("protein"))
    if val is None:
        val = _to_float(nutrition.get("proteins"))

    if val is None:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Protein content is missing from nutrition data; cannot verify 'High Protein'.",
            "evidence": {"declared_protein": None, "threshold": 10.8, "unit": "g/100g"},
            "data_quality": "LOW",
        }
    # Threshold: >= 20% of adult RDA (54.0g) = 10.8 g / 100g
    threshold = 10.8
    if val >= threshold:
        return {
            "status": "SUPPORTED",
            "reason": f"Protein content ({val} g/100g) satisfies the FSSAI requirement (>= 20% of RDA / >= 10.8 g/100g).",
            "evidence": {"declared_protein": val, "threshold": threshold, "unit": "g/100g", "basis": "20% of 54g adult RDA", "comparison": ">="},
            "data_quality": "HIGH",
        }
    return {
        "status": "NOT_SUPPORTED",
        "reason": f"Protein content ({val} g/100g) is below the FSSAI threshold of 10.8 g/100g (20% of adult RDA) for 'High Protein'.",
        "evidence": {"declared_protein": val, "threshold": threshold, "unit": "g/100g", "basis": "20% of 54g adult RDA", "comparison": "<"},
        "data_quality": "HIGH",
    }


def _verify_zero_trans_fat(rule: ClaimRule, nutrition: dict, ingredients_text: str) -> dict:
    # Trans fat is distinct from total fat. Must be explicitly declared.
    trans_fat = _to_float(nutrition.get("trans_fat"))
    if trans_fat is None:
        trans_fat = _to_float(nutrition.get("transfat"))
    if trans_fat is None:
        trans_fat = _to_float(nutrition.get("trans_fatty_acids"))

    if trans_fat is None:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Trans fat content is not declared in the nutrition facts; cannot verify 'Zero Trans Fat'.",
            "evidence": {"declared_trans_fat": None, "threshold": 0.2, "unit": "g/100g"},
            "data_quality": "LOW",
        }

    sat_fat = _to_float(nutrition.get("saturated_fat"))
    if sat_fat is None:
        sat_fat = _to_float(nutrition.get("sat_fat"))
    if sat_fat is None:
        sat_fat = _to_float(nutrition.get("saturated_fatty_acids"))

    # FSSAI Schedule I: trans fat < 0.2 g/100g
    if trans_fat < 0.2:
        # Also check saturated fat if declared (shall not exceed 1.5g per 100g solids)
        if sat_fat is not None and sat_fat > 1.5:
            return {
                "status": "NOT_SUPPORTED",
                "reason": f"Trans fat is {trans_fat} g/100g, but saturated fat ({sat_fat} g/100g) exceeds the allowable limit of 1.5 g/100g for trans-fat free claims under FSSAI Schedule I.",
                "evidence": {"trans_fat": trans_fat, "saturated_fat": sat_fat, "threshold_trans_fat": 0.2, "threshold_saturated_fat": 1.5, "unit": "g/100g"},
                "data_quality": "HIGH",
            }
        return {
            "status": "SUPPORTED",
            "reason": f"Trans fat ({trans_fat} g/100g) is strictly less than the FSSAI limit of 0.2 g/100g for 'Trans Fat Free'.",
            "evidence": {"trans_fat": trans_fat, "saturated_fat": sat_fat, "threshold": 0.2, "unit": "g/100g", "comparison": "<"},
            "data_quality": "HIGH",
        }
    return {
        "status": "NOT_SUPPORTED",
        "reason": f"Trans fat ({trans_fat} g/100g) is at or above the FSSAI threshold of 0.2 g/100g for 'Trans Fat Free'.",
        "evidence": {"trans_fat": trans_fat, "threshold": 0.2, "unit": "g/100g", "comparison": ">="},
        "data_quality": "HIGH",
    }


def _verify_no_added_sugar(rule: ClaimRule, nutrition: dict, ingredients_text: str) -> dict:
    if not ingredients_text or not ingredients_text.strip():
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Ingredient list is missing; cannot verify absence of added sugars without ingredient declarations.",
            "evidence": {"ingredients_provided": False},
            "data_quality": "LOW",
        }

    matched_added_sugars = []
    for pattern in _COMPILED_ADDED_SUGARS:
        match = pattern.search(ingredients_text)
        if match:
            matched_added_sugars.append(match.group(0).lower())

    if matched_added_sugars:
        unique_matches = sorted(list(set(matched_added_sugars)))
        return {
            "status": "NOT_SUPPORTED",
            "reason": f"Product ingredients contain added sugar components: {', '.join(unique_matches)}.",
            "evidence": {"flagged_ingredients": unique_matches, "rule": "FSSAI Regulation 4(5)"},
            "data_quality": "HIGH",
        }

    declared_sugar = _to_float(nutrition.get("sugar"))
    return {
        "status": "SUPPORTED",
        "reason": "No added sugars, syrups, honey, or concentrated fruit juices detected in the ingredient list.",
        "evidence": {
            "added_sugars_detected": False,
            "declared_sugar_100g": declared_sugar,
            "note": "Natural sugars may still be present and should be labelled as naturally occurring.",
        },
        "data_quality": "HIGH" if declared_sugar is not None else "MEDIUM",
    }


_EVALUATORS: Dict[str, Callable[[ClaimRule, dict, str], dict]] = {
    "sugar_free": _verify_sugar_free,
    "low_fat": _verify_low_fat,
    "low_sodium": _verify_low_sodium,
    "high_fibre": _verify_high_fibre,
    "high_protein": _verify_high_protein,
    "zero_trans_fat": _verify_zero_trans_fat,
    "no_added_sugar": _verify_no_added_sugar,
}


# ==============================================================================
# 6. PUBLIC CLAIM VERIFICATION API
# ==============================================================================

def verify_single_claim(
    claim_text: str,
    nutrition: Optional[dict[str, Any]] = None,
    ingredients: Optional[Any] = None,
) -> dict[str, Any]:
    """Verify a single claim string against available nutrition and ingredient data."""
    raw_claim = str(claim_text or "").strip()
    normalized = normalize_claim_text(raw_claim)
    nutrition_dict = nutrition or {}
    ing_text = _extract_ingredient_text(ingredients)

    rule = REGULATORY_RULES.get(normalized)
    if rule is None:
        return {
            "claim": raw_claim,
            "normalized_claim": normalized,
            "status": "NEEDS_REVIEW",
            "reason": f"Claim '{raw_claim}' is not currently defined in the active FSSAI regulatory rule set.",
            "evidence": {"unrecognized_claim": raw_claim},
            "rule_id": None,
            "source": None,
            "data_quality": "UNKNOWN",
        }

    evaluator = _EVALUATORS.get(normalized)
    if not evaluator:
        return {
            "claim": raw_claim,
            "normalized_claim": normalized,
            "status": "NEEDS_REVIEW",
            "reason": f"Evaluation logic for claim '{rule.claim_name}' is not configured.",
            "evidence": {},
            "rule_id": rule.rule_id,
            "source": asdict(rule.regulatory_source),
            "data_quality": "UNKNOWN",
        }

    res = evaluator(rule, nutrition_dict, ing_text)
    return {
        "claim": raw_claim,
        "normalized_claim": normalized,
        "status": res["status"],
        "reason": res["reason"],
        "evidence": res.get("evidence", {}),
        "rule_id": rule.rule_id,
        "source": asdict(rule.regulatory_source),
        "data_quality": res.get("data_quality", "HIGH"),
    }


def verify_claims(
    claims: List[str],
    nutrition: Optional[dict[str, Any]] = None,
    ingredients: Optional[Any] = None,
) -> dict[str, Any]:
    """Verify a list of claims deterministically, deduplicating while preserving input ordering."""
    if not claims:
        return {
            "results": [],
            "total_claims": 0,
            "disclaimer": DISCLAIMER_TEXT,
        }

    seen_normalized = set()
    results = []

    for raw_claim in claims:
        if not raw_claim or not str(raw_claim).strip():
            continue
        c_str = str(raw_claim).strip()
        norm = normalize_claim_text(c_str)
        # Deduplicate on canonical normalized claim
        if norm in seen_normalized:
            continue
        seen_normalized.add(norm)

        res = verify_single_claim(c_str, nutrition, ingredients)
        results.append(res)

    return {
        "results": results,
        "total_claims": len(results),
        "disclaimer": DISCLAIMER_TEXT,
    }
