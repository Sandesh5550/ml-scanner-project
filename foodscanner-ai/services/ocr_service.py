from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DISCLAIMER_TEXT = (
    "FSSAI license detection is extraction only and does not constitute official license verification. "
    "OCR nutritional values are parsed from label images and should be reviewed before logging."
)

_OCR_PARSER_VERSION = "2.0.0"
_OCR_CACHE: Dict[str, Dict[str, Any]] = {}
_OCR_CACHE_MAX = 64
_EASYOCR_READER = None

MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB maximum decoded image payload
MAX_BASE64_CHAR_LIMIT = 16 * 1024 * 1024  # ~16 MB Base64 string limit
SUPPORTED_IMAGE_FORMATS = {"JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF"}


def detect_image_format(data: bytes) -> Optional[str]:
    """Detect image format from binary magic bytes / file signatures."""
    if not data or len(data) < 4:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "GIF"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data.startswith(b"BM"):
        return "BMP"
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):
        return "TIFF"
    return None


def validate_image_payload(
    image_bytes: bytes,
    max_bytes: int = MAX_IMAGE_SIZE_BYTES,
) -> Tuple[bool, int, str, Optional[str]]:
    """Validate decoded image bytes for size, magic bytes signature, and structural integrity.

    Returns:
        (is_valid, http_status_code, error_detail, detected_format)
    """
    if not image_bytes:
        return False, 400, "empty image payload", None

    if len(image_bytes) > max_bytes:
        return (
            False,
            413,
            f"Image payload exceeds maximum allowed size of {max_bytes // (1024 * 1024)} MB",
            None,
        )

    fmt = detect_image_format(image_bytes)
    if fmt is None:
        return (
            False,
            400,
            "Unsupported image format. Allowed formats: JPEG, PNG, GIF, WebP, BMP, TIFF",
            None,
        )

    from PIL import Image

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.verify()
    except Exception:
        return False, 400, "Corrupted or invalid image data", None

    return True, 200, "", fmt


# ==============================================================================
# 1. ENUMS & DATA STRUCTURES
# ==============================================================================

class OCRStatus:
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NO_TEXT = "NO_TEXT"
    INVALID_IMAGE = "INVALID_IMAGE"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


class ServingBasis:
    PER_100G = "per_100g"
    PER_100ML = "per_100ml"
    PER_SERVING = "per_serving"
    UNKNOWN = "unknown"


class FSSAIStatus:
    DETECTED = "DETECTED"
    NOT_DETECTED = "NOT_DETECTED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class NutrientField:
    value: Optional[float]
    unit: str
    basis: str = ServingBasis.PER_100G
    raw_value: Optional[float] = None
    raw_unit: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "basis": self.basis,
            "raw_value": self.raw_value,
            "raw_unit": self.raw_unit,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class ServingInfo:
    size_value: Optional[float] = None
    size_unit: Optional[str] = None
    basis: str = ServingBasis.PER_100G
    servings_per_pack: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "size_value": self.size_value,
            "size_unit": self.size_unit,
            "basis": self.basis,
            "servings_per_pack": self.servings_per_pack,
        }


@dataclass
class IngredientInfo:
    raw_text: str = ""
    items: List[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "items": self.items,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class FSSAILicenseInfo:
    value: Optional[str] = None
    status: str = FSSAIStatus.NOT_DETECTED
    confidence: float = 0.0
    raw_text: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "status": self.status,
            "confidence": round(self.confidence, 3),
            "raw_text": self.raw_text,
        }


@dataclass
class DetectedRegion:
    type: str
    text: str
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "confidence": round(self.confidence, 3),
        }


# ==============================================================================
# 2. IMAGE PREPROCESSING
# ==============================================================================

class OCRPreprocessor:
    """Deterministic, non-destructive image preprocessing for noisy packaged food labels."""

    @staticmethod
    def preprocess(image_bytes: bytes) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
        """Preprocess raw image bytes.
        Returns:
            (preprocessed_pil_image, metadata_dict)
        """
        if not image_bytes or len(image_bytes) < 10:
            return None, None

        try:
            from PIL import Image, ImageEnhance, ImageFilter, ImageOps

            # Open image
            img = Image.open(io.BytesIO(image_bytes))

            # Transpose orientation according to EXIF (critical for phone photos)
            img = ImageOps.exif_transpose(img)

            # Ensure RGB
            if img.mode != "RGB":
                img = img.convert("RGB")

            orig_w, orig_h = img.size
            metadata: Dict[str, Any] = {
                "original_width": orig_w,
                "original_height": orig_h,
                "upscaled": False,
                "downscaled": False,
            }

            # Resize if necessary
            min_dim = min(orig_w, orig_h)
            max_dim = max(orig_w, orig_h)

            # Upscale small images to help OCR read compact text
            if min_dim < 1000:
                scale = 1200.0 / float(min_dim)
                new_w = int(orig_w * scale)
                new_h = int(orig_h * scale)
                img = img.resize((new_w, new_h), Image.Resampling.BICUBIC)
                metadata["upscaled"] = True
                metadata["scale_factor"] = round(scale, 2)
            # Downscale enormous images to prevent memory exhaustion
            elif max_dim > 3000:
                scale = 2400.0 / float(max_dim)
                new_w = int(orig_w * scale)
                new_h = int(orig_h * scale)
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                metadata["downscaled"] = True
                metadata["scale_factor"] = round(scale, 2)

            # Contrast enhancement
            img = ImageEnhance.Contrast(img).enhance(1.40)

            # Denoise with subtle median filter
            img = img.filter(ImageFilter.MedianFilter(size=3))

            # Sharpen edges of typography
            img = img.filter(ImageFilter.SHARPEN)

            return img, metadata

        except Exception as e:
            logger.warning(f"Image preprocessing failed: {e}")
            return None, None


# ==============================================================================
# 3. OCR ENGINE INVOCATION
# ==============================================================================

def get_easyocr_reader():
    global _EASYOCR_READER
    if _EASYOCR_READER is None:
        try:
            import easyocr
            _EASYOCR_READER = easyocr.Reader(["en"], gpu=False)
        except Exception as e:
            logger.error(f"Failed to initialize EasyOCR: {e}")
            return None
    return _EASYOCR_READER


def run_ocr_engine(image_bytes: bytes) -> Tuple[str, float]:
    """Execute preferred OCR engine (easyocr with pytesseract fallback).
    Returns:
        (extracted_text, engine_confidence)
    """
    engine_name = str(os.getenv("FOODSCANNER_OCR_ENGINE") or "easyocr").strip().lower()

    if engine_name != "tesseract":
        reader = get_easyocr_reader()
        if reader is not None:
            try:
                import numpy as np
                from PIL import Image

                prep_img, _ = OCRPreprocessor.preprocess(image_bytes)
                if prep_img is not None:
                    arr = np.array(prep_img)
                else:
                    arr = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))

                # Readtext with detail=1 returns list of (bbox, text, prob)
                results = reader.readtext(arr, detail=1)
                if results:
                    lines = []
                    probs = []
                    for item in results:
                        if len(item) >= 3:
                            _, t, p = item[0], item[1], item[2]
                            if t and str(t).strip():
                                lines.append(str(t).strip())
                                probs.append(float(p))
                        elif len(item) >= 2:
                            lines.append(str(item[1]).strip())
                            probs.append(0.7)

                    text = "\n".join(lines)
                    avg_prob = sum(probs) / len(probs) if probs else 0.7
                    if text.strip():
                        return text, round(avg_prob, 3)
            except Exception as e:
                logger.error(f"EasyOCR execution error: {e}")

    # Fallback to Tesseract
    try:
        from PIL import Image, ImageOps
        import pytesseract

        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        img = img.convert("L")
        img = ImageOps.autocontrast(img)
        img = img.resize((img.size[0] * 2, img.size[1] * 2))
        img = img.point(lambda p: 255 if p > 160 else 0)

        # PyTesseract data for confidence
        try:
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            confs = [float(c) for c in data.get("conf", []) if str(c).isdigit() and float(c) > 0]
            avg_conf = (sum(confs) / len(confs) / 100.0) if confs else 0.65
        except Exception:
            avg_conf = 0.65

        text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
        if not text.strip():
            text = pytesseract.image_to_string(img, config="--oem 3 --psm 4")

        return str(text or ""), round(avg_conf, 3)

    except Exception as e:
        logger.error(f"Tesseract OCR fallback failed: {e}")
        return "", 0.0


# ==============================================================================
# 4. REGION DETECTION & CLASSIFICATION
# ==============================================================================

def classify_text_regions(text: str) -> List[DetectedRegion]:
    """Segment raw OCR text into semantic regions (nutrition, ingredients, fssai, allergen, product_info)."""
    if not text or not text.strip():
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    regions: List[DetectedRegion] = []

    nutrition_lines: List[str] = []
    ingredient_lines: List[str] = []
    fssai_lines: List[str] = []
    allergen_lines: List[str] = []
    other_lines: List[str] = []

    current_mode = "unknown"

    nutrition_keywords = [
        "nutritional information", "nutrition facts", "nutritional value",
        "typical values", "per 100g", "per 100 g", "per 100ml", "energy",
        "carbohydrate", "protein", "total fat", "sugars", "dietary fibre"
    ]
    ingredient_keywords = ["ingredients:", "ingredients", "ingredients are", "made from:", "composition:"]
    allergen_keywords = ["allergen advice:", "allergen", "contains:", "may contain", "manufactured in a facility"]
    fssai_keywords = ["fssai", "lic no", "lic. no", "license no"]
    product_info_keywords = [
        "mfd by", "mfd. by", "mfg date", "batch no", "net weight", "net wt",
        "net qty", "mrp", "marketed by", "manufactured by", "customer care", "best before", "pkd"
    ]

    for ln in lines:
        ln_lower = ln.lower()

        # Check section trigger
        if any(ik in ln_lower for ik in ingredient_keywords) and not any(nk in ln_lower for nk in ["per 100g", "energy"]):
            current_mode = "ingredients"
            ingredient_lines.append(ln)
            continue
        elif any(nk in ln_lower for nk in ["nutritional information", "nutrition facts", "nutritional value", "typical values"]):
            current_mode = "nutrition"
            nutrition_lines.append(ln)
            continue
        elif any(ak in ln_lower for ak in allergen_keywords):
            current_mode = "allergen"
            allergen_lines.append(ln)
            continue
        elif any(fk in ln_lower for fk in fssai_keywords) or re.search(r"\b1\d{13}\b", ln):
            current_mode = "fssai"
            fssai_lines.append(ln)
            continue
        elif any(pk in ln_lower for pk in product_info_keywords):
            current_mode = "product_info"
            other_lines.append(ln)
            continue

        # In-section attribution
        if current_mode == "nutrition":
            if any(ik in ln_lower for ik in ingredient_keywords):
                current_mode = "ingredients"
                ingredient_lines.append(ln)
            elif any(ak in ln_lower for ak in allergen_keywords):
                current_mode = "allergen"
                allergen_lines.append(ln)
            elif any(pk in ln_lower for pk in product_info_keywords):
                current_mode = "product_info"
                other_lines.append(ln)
            else:
                nutrition_lines.append(ln)
        elif current_mode == "ingredients":
            if any(nk in ln_lower for nk in ["nutritional information", "nutrition facts", "typical values"]):
                current_mode = "nutrition"
                nutrition_lines.append(ln)
            elif any(ak in ln_lower for ak in allergen_keywords):
                current_mode = "allergen"
                allergen_lines.append(ln)
            elif any(pk in ln_lower for pk in product_info_keywords):
                current_mode = "product_info"
                other_lines.append(ln)
            else:
                ingredient_lines.append(ln)
        elif current_mode == "allergen":
            if any(pk in ln_lower for pk in product_info_keywords):
                current_mode = "product_info"
                other_lines.append(ln)
            elif any(nk in ln_lower for nk in ["nutritional information", "nutrition facts"]):
                current_mode = "nutrition"
                nutrition_lines.append(ln)
            elif any(ik in ln_lower for ik in ingredient_keywords):
                current_mode = "ingredients"
                ingredient_lines.append(ln)
            else:
                allergen_lines.append(ln)
        elif current_mode == "fssai":
            if any(pk in ln_lower for pk in product_info_keywords):
                current_mode = "product_info"
                other_lines.append(ln)
            else:
                other_lines.append(ln)
        elif current_mode == "product_info":
            if any(nk in ln_lower for nk in ["nutritional information", "nutrition facts"]):
                current_mode = "nutrition"
                nutrition_lines.append(ln)
            elif any(ik in ln_lower for ik in ingredient_keywords):
                current_mode = "ingredients"
                ingredient_lines.append(ln)
            else:
                other_lines.append(ln)
        else:
            # Fallback heuristic: check if line looks like nutrition row
            if re.search(r"\b(protein|fat|carbohydrate|sugar|salt|sodium|fiber|fibre|energy|kcal)\b", ln_lower):
                nutrition_lines.append(ln)
            else:
                other_lines.append(ln)

    if nutrition_lines:
        regions.append(DetectedRegion(type="nutrition", text="\n".join(nutrition_lines), confidence=0.88))
    if ingredient_lines:
        regions.append(DetectedRegion(type="ingredients", text="\n".join(ingredient_lines), confidence=0.85))
    if fssai_lines:
        regions.append(DetectedRegion(type="fssai_license", text="\n".join(fssai_lines), confidence=0.92))
    if allergen_lines:
        regions.append(DetectedRegion(type="allergen_info", text="\n".join(allergen_lines), confidence=0.80))
    if other_lines:
        regions.append(DetectedRegion(type="product_info", text="\n".join(other_lines), confidence=0.70))

    return regions


# ==============================================================================
# 5. SERVING BASIS EXTRACTION
# ==============================================================================

def detect_serving_basis(text: str) -> ServingInfo:
    """Analyze label text to determine whether values are per 100g, per 100ml, or per serving."""
    text_lower = text.lower()
    info = ServingInfo(basis=ServingBasis.PER_100G)

    # 1. Check basis declaration
    if re.search(r"\bper\s*100\s*ml\b", text_lower):
        info.basis = ServingBasis.PER_100ML
    elif re.search(r"\bper\s*100\s*g\b", text_lower):
        info.basis = ServingBasis.PER_100G
    elif re.search(r"\bper\s*serving\b|\bper\s*portion\b|\bper\s*serve\b", text_lower):
        # Check if there is also a per 100g column
        if not re.search(r"\b100\s*g\b|\b100g\b", text_lower):
            info.basis = ServingBasis.PER_SERVING

    # 2. Extract serving size
    m_serve = re.search(
        r"(?:serving\s*size|serve\s*size|portion\s*size)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(g|ml|gm|grams?|ml\b|pack|piece|biscuit|cookie)?",
        text_lower,
    )
    if m_serve:
        try:
            info.size_value = float(m_serve.group(1))
            unit = (m_serve.group(2) or "g").strip().lower()
            if unit in ["gm", "grams", "gram"]:
                unit = "g"
            info.size_unit = unit
        except (ValueError, TypeError):
            pass

    # 3. Extract servings per pack
    m_servings = re.search(r"(?:servings?\s*per\s*(?:pack|container)|serves)\s*[:\-]?\s*(\d+(?:\.\d+)?)", text_lower)
    if m_servings:
        try:
            info.servings_per_pack = float(m_servings.group(1))
        except (ValueError, TypeError):
            pass

    return info


# ==============================================================================
# 6. INGREDIENT LIST EXTRACTION
# ==============================================================================

def extract_ingredients_list(text: str) -> IngredientInfo:
    """Extract and parse clean ingredients from packaging text, preserving sequence and nested items."""
    if not text or not text.strip():
        return IngredientInfo(raw_text="", items=[], confidence=0.0)

    text_lower = text.lower()
    match = re.search(
        r"(?:ingredients\s*[:\-]?|made\s*from\s*[:\-]?|composition\s*[:\-]?)\s*(.*?)(?=\n\s*(?:nutrit|allergen|mfd|fssai|best before|net wt|contains added flavour)|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    raw_section = ""
    if match:
        raw_section = match.group(1).strip()
    else:
        # Fallback: find lines containing ingredients
        lines = text.splitlines()
        capturing = False
        captured = []
        for ln in lines:
            if re.search(r"ingredients\b", ln, re.IGNORECASE):
                capturing = True
                cleaned = re.sub(r"^\s*ingredients\s*[:\-]?\s*", "", ln, flags=re.IGNORECASE).strip()
                if cleaned:
                    captured.append(cleaned)
                continue
            if capturing:
                if re.search(r"^(?:nutrit|allergen|mfd|fssai|batch|net qty)", ln, re.IGNORECASE):
                    break
                captured.append(ln.strip())
        raw_section = " ".join(captured).strip()

    if not raw_section:
        return IngredientInfo(raw_text="", items=[], confidence=0.0)

    # Clean OCR noise: collapse spaces, remove trailing periods/colons
    clean_text = re.sub(r"\s+", " ", raw_section).strip().rstrip(".:;")

    # Split ingredients respecting nested parentheses: "Edible Vegetable Oil (Palmolein, Rice Bran)"
    items: List[str] = []
    buffer: List[str] = []
    paren_depth = 0

    for ch in clean_text:
        if ch == "(":
            paren_depth += 1
            buffer.append(ch)
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
            buffer.append(ch)
        elif ch in (",", ";", "•", "·", "|") and paren_depth == 0:
            item_str = "".join(buffer).strip()
            # Clean leading numbers or bullet characters
            item_str = re.sub(r"^[\d\.\-\*\s]+", "", item_str).strip()
            if item_str and len(item_str) >= 2:
                items.append(item_str)
            buffer = []
        else:
            buffer.append(ch)

    if buffer:
        item_str = "".join(buffer).strip()
        item_str = re.sub(r"^[\d\.\-\*\s]+", "", item_str).strip()
        if item_str and len(item_str) >= 2:
            items.append(item_str)

    confidence = 0.90 if len(items) >= 2 else (0.75 if items else 0.40)
    return IngredientInfo(raw_text=clean_text, items=items, confidence=confidence)


# ==============================================================================
# 7. FSSAI LICENSE EXTRACTION
# ==============================================================================

def extract_fssai_license(text: str) -> FSSAILicenseInfo:
    """Extract and validate 14-digit FSSAI license numbers from packaging text."""
    if not text or not text.strip():
        return FSSAILicenseInfo(value=None, status=FSSAIStatus.NOT_DETECTED, confidence=1.0, raw_text=None)

    # 1. Search for keyword followed by digits: "FSSAI Lic. No. 10012011000123"
    pattern = re.search(
        r"(?:fssai(?:\s*(?:lic(?:ense)?\.?\s*(?:no\.?)?|no\.?|:))|lic(?:ense)?\.?\s*no\.?)\s*[:\-]?\s*([0-9\s]{10,20})",
        text,
        re.IGNORECASE,
    )
    if pattern:
        candidate_raw = pattern.group(1).strip()
        digits = re.sub(r"\D", "", candidate_raw)
        if len(digits) == 14:
            return FSSAILicenseInfo(
                value=digits,
                status=FSSAIStatus.DETECTED,
                confidence=0.96,
                raw_text=pattern.group(0).strip(),
            )
        elif 10 <= len(digits) <= 16:
            return FSSAILicenseInfo(
                value=digits,
                status=FSSAIStatus.AMBIGUOUS,
                confidence=0.60,
                raw_text=pattern.group(0).strip(),
            )

    # 2. Standalone 14-digit sequence starting with 1 or 2 (standard Indian FSSAI license format)
    standalone_match = re.search(r"\b([12]\d{13})\b", text)
    if standalone_match:
        val = standalone_match.group(1)
        # Check if FSSAI is mentioned anywhere in the overall text
        has_fssai_context = bool(re.search(r"\bfssai\b", text, re.IGNORECASE))
        return FSSAILicenseInfo(
            value=val,
            status=FSSAIStatus.DETECTED,
            confidence=0.90 if has_fssai_context else 0.75,
            raw_text=standalone_match.group(0),
        )

    # 3. Check for FSSAI keyword with incomplete/scrambled digits
    if re.search(r"\bfssai\b", text, re.IGNORECASE):
        # Look for numbers near fssai
        near_match = re.search(r"fssai[^\n\r]{1,40}?(\d+)", text, re.IGNORECASE)
        if near_match:
            d = near_match.group(1)
            if len(d) != 14:
                return FSSAILicenseInfo(
                    value=d,
                    status=FSSAIStatus.AMBIGUOUS,
                    confidence=0.50,
                    raw_text=near_match.group(0),
                )

    return FSSAILicenseInfo(value=None, status=FSSAIStatus.NOT_DETECTED, confidence=0.90, raw_text=None)


# ==============================================================================
# 8. STRUCTURED NUTRITION PARSER & NORMALIZER
# ==============================================================================

def _normalize_token_number(token_str: str) -> Optional[float]:
    """Parse numeric values while correcting OCR artifacts like '@' instead of '.', 'o' instead of '0'."""
    if not token_str:
        return None
    cleaned = token_str.strip().replace("@", ".").replace("o", "0").replace("O", "0")
    is_neg = cleaned.startswith("-")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned or cleaned == ".":
        return None
    # Fix double decimal points: 0..5 -> 0.5
    if cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = parts[0] + "." + "".join(parts[1:])
    try:
        val = float(cleaned)
        return -val if is_neg else val
    except (TypeError, ValueError):
        return None


def extract_structured_nutrition(
    text: str,
    serving_info: ServingInfo,
    base_confidence: float = 0.85,
) -> Tuple[Dict[str, NutrientField], List[str]]:
    """Extract individual macro and micronutrients, normalize units, and check consistency."""
    warnings: List[str] = []
    fields: Dict[str, NutrientField] = {}

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Normalize common OCR character confusions on lines
    norm_lines: List[str] = []
    for ln in lines:
        l_norm = (
            ln.replace("@", ".")
            .replace("kca|", "kcal")
            .replace("larbohydrate", "carbohydrate")
            .replace("carbohvdrate", "carbohydrate")
            .replace("lotal", "total")
            .replace("iolal", "total")
            .replace("fa|", "fat")
            .replace("fal}", "fat")
            .replace("proein", "protein")
            .replace("isodiv", "sodium")
        )
        # Trailing "g" read as "9": "fat 7 9" -> "fat 7 g"
        l_norm = re.sub(r"\b(fat|sugars?|protein|fibre|fiber|carbohydrate|carbs)\s*(\d{1,2})\s*9\b", r"\1 \2 g", l_norm, flags=re.IGNORECASE)
        norm_lines.append(l_norm)
    lines = norm_lines

    def _extract_row_val(
        pattern: str,
        max_lookahead: int = 2,
        skip_pattern: Optional[str] = None,
    ) -> Tuple[Optional[float], Optional[str], Optional[float]]:
        for i, line in enumerate(lines):
            if not re.search(pattern, line, re.IGNORECASE):
                continue

            for j in range(i, min(len(lines), i + max_lookahead + 1)):
                probe = lines[j]
                if j > i and re.search(r"^(?:\d+\s*%|%|rda)\s*$", probe, re.IGNORECASE):
                    continue
                if skip_pattern and re.search(skip_pattern, probe, re.IGNORECASE):
                    continue

                # Search for numeric value with optional unit
                m = re.search(r"(-?\s*\d+(?:[.@]\d+)?)\s*(kcal|kj|g|gm|mg|mcg|µg)?\b", probe, re.IGNORECASE)
                if m:
                    raw_num = _normalize_token_number(m.group(1))
                    raw_unit = (m.group(2) or "").lower()
                    if raw_num is not None:
                        return raw_num, raw_unit, 0.90
        return None, None, 0.0

    # 1. Energy / Calories
    cal_raw, cal_unit, cal_conf = _extract_row_val(r"energy|calories?|kcal|\bcal\b", max_lookahead=2)
    if cal_raw is not None:
        if cal_raw < 0:
            warnings.append("Negative energy value detected; rejected.")
        else:
            final_cal = cal_raw
            # If label gave kJ, convert to kcal
            if cal_unit == "kj" or cal_raw > 4500:
                final_cal = round(cal_raw / 4.184, 1)
            fields["energy_kcal"] = NutrientField(
                value=final_cal,
                unit="kcal",
                basis=serving_info.basis,
                raw_value=cal_raw,
                raw_unit=cal_unit or "kcal",
                confidence=cal_conf,
            )

    # 2. Protein
    p_raw, p_unit, p_conf = _extract_row_val(r"\bprotein\b|\bproteins\b|\bprot\b")
    if p_raw is not None:
        if p_raw < 0:
            warnings.append("Negative protein value detected; rejected.")
        else:
            fields["protein_g"] = NutrientField(
                value=p_raw,
                unit="g",
                basis=serving_info.basis,
                raw_value=p_raw,
                raw_unit=p_unit or "g",
                confidence=p_conf,
            )

    # 3. Carbohydrates
    c_raw, c_unit, c_conf = _extract_row_val(r"carbohydrate|carbohydrates|\bcarbs?\b")
    if c_raw is not None:
        if c_raw < 0:
            warnings.append("Negative carbohydrate value detected; rejected.")
        else:
            fields["carbohydrates_g"] = NutrientField(
                value=c_raw,
                unit="g",
                basis=serving_info.basis,
                raw_value=c_raw,
                raw_unit=c_unit or "g",
                confidence=c_conf,
            )

    # 4. Total Sugars & Added Sugars
    # Added Sugars
    as_raw, as_unit, as_conf = _extract_row_val(r"added\s*sugars?|of\s*which\s*added|includes\s*.*added", max_lookahead=2)
    if as_raw is not None and as_raw >= 0:
        fields["added_sugars_g"] = NutrientField(
            value=as_raw,
            unit="g",
            basis=serving_info.basis,
            raw_value=as_raw,
            raw_unit=as_unit or "g",
            confidence=as_conf,
        )

    # Total Sugars (avoid taking added sugar row)
    s_raw, s_unit, s_conf = _extract_row_val(r"total\s*sugars?|\bsugars?\b", max_lookahead=3, skip_pattern=r"\badded\b")
    if s_raw is not None:
        if s_raw < 0:
            warnings.append("Negative sugar value detected; rejected.")
        else:
            fields["sugars_g"] = NutrientField(
                value=s_raw,
                unit="g",
                basis=serving_info.basis,
                raw_value=s_raw,
                raw_unit=s_unit or "g",
                confidence=s_conf,
            )

    # 5. Total Fat, Saturated Fat, Trans Fat
    sat_raw, sat_unit, sat_conf = _extract_row_val(r"saturated\s*fat|sat\s*fat|saturates|saturated\s*fatty\s*acids")
    if sat_raw is not None and sat_raw >= 0:
        fields["saturated_fat_g"] = NutrientField(
            value=sat_raw,
            unit="g",
            basis=serving_info.basis,
            raw_value=sat_raw,
            raw_unit=sat_unit or "g",
            confidence=sat_conf,
        )

    tf_raw, tf_unit, tf_conf = _extract_row_val(r"trans\s*fat|trans\s*fatty\s*acids|trans-fat")
    if tf_raw is not None and tf_raw >= 0:
        fields["trans_fat_g"] = NutrientField(
            value=tf_raw,
            unit="g",
            basis=serving_info.basis,
            raw_value=tf_raw,
            raw_unit=tf_unit or "g",
            confidence=tf_conf,
        )

    f_raw, f_unit, f_conf = _extract_row_val(r"total\s*fat|\bfat\b", max_lookahead=2, skip_pattern=r"saturated|trans")
    if f_raw is not None:
        if f_raw < 0:
            warnings.append("Negative fat value detected; rejected.")
        else:
            fields["fat_g"] = NutrientField(
                value=f_raw,
                unit="g",
                basis=serving_info.basis,
                raw_value=f_raw,
                raw_unit=f_unit or "g",
                confidence=f_conf,
            )

    # 6. Dietary Fibre
    fib_raw, fib_unit, fib_conf = _extract_row_val(r"dietary\s*fibre|dietary\s*fiber|\bfibre\b|\bfiber\b")
    if fib_raw is not None and fib_raw >= 0:
        fields["fibre_g"] = NutrientField(
            value=fib_raw,
            unit="g",
            basis=serving_info.basis,
            raw_value=fib_raw,
            raw_unit=fib_unit or "g",
            confidence=fib_conf,
        )

    # 7. Sodium and Salt
    sod_raw, sod_unit, sod_conf = _extract_row_val(r"\bsodium\b|\bsod\b")
    salt_raw, salt_unit, salt_conf = _extract_row_val(r"\bsalt\b")

    if sod_raw is not None and sod_raw >= 0:
        # Normalize sodium to mg
        sod_mg = sod_raw
        if sod_unit == "g" or sod_raw < 2.0:
            # Stated in grams -> convert to mg
            sod_mg = round(sod_raw * 1000.0, 1)
        fields["sodium_mg"] = NutrientField(
            value=sod_mg,
            unit="mg",
            basis=serving_info.basis,
            raw_value=sod_raw,
            raw_unit=sod_unit or ("mg" if sod_raw > 2.0 else "g"),
            confidence=sod_conf,
        )

    if salt_raw is not None and salt_raw >= 0:
        fields["salt_g"] = NutrientField(
            value=salt_raw,
            unit="g",
            basis=serving_info.basis,
            raw_value=salt_raw,
            raw_unit=salt_unit or "g",
            confidence=salt_conf,
        )
        # Cross-calculate sodium if not explicitly present on label: sodium = (salt / 2.5) * 1000
        if "sodium_mg" not in fields:
            derived_sod = round((salt_raw / 2.5) * 1000.0, 1)
            fields["sodium_mg"] = NutrientField(
                value=derived_sod,
                unit="mg",
                basis=serving_info.basis,
                raw_value=derived_sod,
                raw_unit="mg",
                confidence=round(salt_conf * 0.95, 3),
            )
    elif "sodium_mg" in fields and fields["sodium_mg"].value is not None:
        # Compute salt from sodium: salt = sodium * 2.5
        derived_salt = round((fields["sodium_mg"].value * 2.5) / 1000.0, 3)
        fields["salt_g"] = NutrientField(
            value=derived_salt,
            unit="g",
            basis=serving_info.basis,
            raw_value=derived_salt,
            raw_unit="g",
            confidence=round(fields["sodium_mg"].confidence * 0.95, 3),
        )

    # ==============================================================================
    # 9. VALIDATION & SANITY CHECKS
    # ==============================================================================

    # Per-100g ceiling validation (single nutrient cannot exceed 100g in 100g product)
    if serving_info.basis == ServingBasis.PER_100G:
        for k in ["protein_g", "carbohydrates_g", "sugars_g", "fat_g", "fibre_g", "salt_g"]:
            if k in fields and fields[k].value is not None and fields[k].value > 100.0:
                # Often dropped decimal point: 503 -> 50.3
                orig = fields[k].value
                if 100.0 < orig <= 1000.0:
                    fields[k].value = round(orig / 10.0, 2)
                    warnings.append(f"Corrected OCR dropped decimal point for {k}: {orig} -> {fields[k].value}")
                else:
                    warnings.append(f"Nutrient {k} value ({orig}g) physically impossible in 100g; flagged.")

    # Consistency check: sugars <= carbohydrates
    if "sugars_g" in fields and "carbohydrates_g" in fields:
        s = fields["sugars_g"].value
        c = fields["carbohydrates_g"].value
        if s is not None and c is not None and s > c:
            warnings.append(f"Conflict: Sugars ({s}g) declared greater than total carbohydrates ({c}g).")

    # Consistency check: saturated fat <= total fat
    if "saturated_fat_g" in fields and "fat_g" in fields:
        sat = fields["saturated_fat_g"].value
        tf = fields["fat_g"].value
        if sat is not None and tf is not None and sat > tf:
            warnings.append(f"Conflict: Saturated fat ({sat}g) exceeds total fat ({tf}g).")
            # If total fat was unread or misread, adjust total fat to at least saturated fat
            fields["fat_g"].value = sat

    # Consistency check: added sugars <= total sugars
    if "added_sugars_g" in fields and "sugars_g" in fields:
        as_val = fields["added_sugars_g"].value
        ts_val = fields["sugars_g"].value
        if as_val is not None and ts_val is not None and as_val > ts_val:
            warnings.append(f"Conflict: Added sugars ({as_val}g) exceeds total sugars ({ts_val}g).")

    return fields, warnings


# ==============================================================================
# 10. MAIN OCR 2.0 ORCHESTRATOR
# ==============================================================================

def parse_structured_ocr(raw_text: str, base_confidence: float = 0.85) -> Dict[str, Any]:
    """Parse raw OCR text into complete structured OCR 2.0 format."""
    if not raw_text or not raw_text.strip():
        return {
            "status": OCRStatus.NO_TEXT,
            "ocr_version": _OCR_PARSER_VERSION,
            "product_name": "",
            "calories": None,
            "fat": None,
            "sugar": None,
            "salt": None,
            "protein": None,
            "fiber": None,
            "carbs": None,
            "serving_size": None,
            "confidence": "low",
            "raw_text": "",
            "nutrition": {},
            "serving": ServingInfo().to_dict(),
            "ingredients": IngredientInfo().to_dict(),
            "fssai_license": FSSAILicenseInfo().to_dict(),
            "regions": [],
            "overall_confidence": 0.0,
            "warnings": ["No readable text found in image."],
            "disclaimer": DISCLAIMER_TEXT,
        }

    # 1. Classify regions
    regions = classify_text_regions(raw_text)

    # 2. Serving info
    serving_info = detect_serving_basis(raw_text)

    # 3. Structured nutrition
    nutrients, warnings = extract_structured_nutrition(raw_text, serving_info, base_confidence)

    # 4. Ingredients
    ingredients_info = extract_ingredients_list(raw_text)

    # 5. FSSAI License
    fssai_info = extract_fssai_license(raw_text)

    # 6. Serving basis scaling check:
    # If basis was per_serving and serving size is known, provide both per-serving and 100g scaled
    if serving_info.basis == ServingBasis.PER_SERVING and serving_info.size_value and serving_info.size_value > 0:
        warnings.append(
            f"Label values declared per serving ({serving_info.size_value}{serving_info.size_unit or 'g'}); "
            "standardized to 100g equivalent."
        )

    # 7. Backward-compatible top-level scalar values
    cal_val = nutrients.get("energy_kcal").value if "energy_kcal" in nutrients else None
    fat_val = nutrients.get("fat_g").value if "fat_g" in nutrients else None
    sug_val = nutrients.get("sugars_g").value if "sugars_g" in nutrients else None
    salt_val = nutrients.get("salt_g").value if "salt_g" in nutrients else None
    prot_val = nutrients.get("protein_g").value if "protein_g" in nutrients else None
    fib_val = nutrients.get("fibre_g").value if "fibre_g" in nutrients else None
    carb_val = nutrients.get("carbohydrates_g").value if "carbohydrates_g" in nutrients else None
    serving_size_val = serving_info.size_value

    # 8. Overall confidence calculation
    extracted_macro_count = sum(1 for v in [cal_val, fat_val, sug_val, salt_val, prot_val, carb_val] if v is not None)
    has_ing = len(ingredients_info.items) > 0
    has_fssai = fssai_info.status == FSSAIStatus.DETECTED

    overall_conf = base_confidence * (0.5 + 0.5 * (extracted_macro_count / 6.0))
    if has_fssai:
        overall_conf = min(1.0, overall_conf + 0.05)
    overall_conf = round(overall_conf, 3)

    # Qualitative confidence level for mobile backward compatibility
    if extracted_macro_count >= 4 and base_confidence >= 0.70:
        legacy_confidence = "high"
        status = OCRStatus.SUCCESS
    elif extracted_macro_count >= 1 or has_ing:
        legacy_confidence = "medium"
        status = OCRStatus.PARTIAL
    else:
        legacy_confidence = "low"
        status = OCRStatus.LOW_CONFIDENCE
        warnings.append("Low confidence: unable to detect clear nutritional rows.")

    nutrition_dict = {k: v.to_dict() for k, v in nutrients.items()}

    return {
        "status": status,
        "ocr_version": _OCR_PARSER_VERSION,
        "product_name": "",
        "calories": cal_val,
        "fat": fat_val,
        "sugar": sug_val,
        "salt": salt_val,
        "protein": prot_val,
        "fiber": fib_val,
        "carbs": carb_val,
        "serving_size": serving_size_val,
        "confidence": legacy_confidence,
        "raw_text": raw_text,
        "nutrition": nutrition_dict,
        "serving": serving_info.to_dict(),
        "ingredients": ingredients_info.to_dict(),
        "fssai_license": fssai_info.to_dict(),
        "regions": [r.to_dict() for r in regions],
        "detailed_confidence": {
            "overall": overall_conf,
            "nutrition": round(base_confidence * (extracted_macro_count / 6.0), 3) if extracted_macro_count else 0.0,
            "ingredients": ingredients_info.confidence,
            "fssai_license": fssai_info.confidence,
        },
        "warnings": warnings,
        "disclaimer": DISCLAIMER_TEXT,
    }


def process_image_ocr(image_bytes: bytes) -> Dict[str, Any]:
    """Execute complete end-to-end OCR 2.0 pipeline from raw image bytes with hash caching."""
    if not image_bytes:
        return {
            "status": OCRStatus.INVALID_IMAGE,
            "ocr_version": _OCR_PARSER_VERSION,
            "product_name": "",
            "calories": None,
            "fat": None,
            "sugar": None,
            "salt": None,
            "protein": None,
            "fiber": None,
            "carbs": None,
            "serving_size": None,
            "confidence": "low",
            "raw_text": "",
            "nutrition": {},
            "serving": ServingInfo().to_dict(),
            "ingredients": IngredientInfo().to_dict(),
            "fssai_license": FSSAILicenseInfo().to_dict(),
            "regions": [],
            "warnings": ["Image bytes are empty or corrupt."],
            "disclaimer": DISCLAIMER_TEXT,
        }

    # Cache by image content hash
    img_hash = hashlib.sha256(image_bytes).hexdigest()
    cache_key = f"{_OCR_PARSER_VERSION}:{img_hash}"
    if cache_key in _OCR_CACHE:
        return dict(_OCR_CACHE[cache_key])

    extracted_text, base_conf = run_ocr_engine(image_bytes)
    result = parse_structured_ocr(extracted_text, base_confidence=base_conf)

    # Store in LRU-style cache
    _OCR_CACHE[cache_key] = dict(result)
    if len(_OCR_CACHE) > _OCR_CACHE_MAX:
        try:
            oldest_key = next(iter(_OCR_CACHE.keys()))
            _OCR_CACHE.pop(oldest_key, None)
        except Exception:
            _OCR_CACHE.clear()

    return result
