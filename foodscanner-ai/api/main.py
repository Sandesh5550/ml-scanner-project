from __future__ import annotations

from pathlib import Path
import base64
import io
import json
import logging
import hashlib
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, select

from database.db_session import get_db
from database.orm import SessionLocal, init_db as orm_init_db
from database.models import FoodLog, OCRJob, ScanHistory, User
from services.barcode_lookup import lookup_product
from services.product_search import search_products
from services.recommendation_engine import get_healthier_alternatives, infer_food_category
from services import db_service
from services.health_report import generate_daily_report, generate_weekly_report
from services.score_explainer import explain_score
from services.goal_report import generate_goal_report
from services.auth_service import create_access_token, get_current_user, hash_password, verify_password
from services.ingredient_analyzer import analyze_ingredients
from services.additive_analyzer import analyze_additives
from services.food_health_score import compute_food_health_score, compute_diet_aware_score
from services.final_decision_engine import compute_final_decision
from services.decision_explainer import build_decision_reasons
from services.claim_verification import verify_claims
from services.ocr_service import (
    process_image_ocr,
    parse_structured_ocr,
    run_ocr_engine,
    validate_image_payload,
    MAX_BASE64_CHAR_LIMIT,
    OCRStatus,
)
from services.personalization import get_personalized_analysis, calculate_personalized_calorie_estimate
from services.ai_nutrition_assistant import ask_nutrition_assistant
from services.config import load_environment, validate_runtime_config
from services.rate_limiter import enforce_rate_limit
from services.metrics import (
    get_metrics_exposition,
    normalize_metric_endpoint,
    record_http_request,
    record_ocr_confidence,
    record_ocr_job_created,
    record_ocr_job_status,
    record_ocr_stale_reaped,
)

# Load environment configuration early
load_environment()

logger = logging.getLogger(__name__)

import pandas as pd
from rapidfuzz import fuzz


def _get_allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS") or os.environ.get("CORS_ORIGINS") or ""
    configured = [o.strip() for o in raw.split(",") if o.strip()]
    defaults = [
        "http://localhost:8081",
        "http://localhost:19006",
        "http://localhost:3000",
        "http://127.0.0.1:8081",
        "http://127.0.0.1:19006",
        "http://127.0.0.1:3000",
    ]
    origins = []
    for origin in defaults + configured:
        if origin not in origins and origin != "*":
            origins.append(origin)
    return origins


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate required runtime configuration early before accepting requests
    validate_runtime_config(raise_error=True)
    orm_init_db()

    # Startup sweep: recover any stranded jobs from previous process restarts
    startup_db = SessionLocal()
    try:
        reaped_count = recover_stale_ocr_jobs(startup_db)
        if reaped_count > 0:
            logger.info("Startup sweep recovered %d stale OCR jobs.", reaped_count)
    except Exception as exc:
        logger.warning("Startup stale OCR job recovery warning: %s", exc)
    finally:
        startup_db.close()

    print("FoodScanner API running. For Expo Go, start uvicorn with --host 0.0.0.0 and open http://<your-lan-ip>:8000/health")
    yield


app = FastAPI(title="FoodScanner AI API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_allowed_origins(),
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prometheus_metrics_middleware(request: Request, call_next):
    # Skip measuring the /metrics endpoint itself to avoid recursive skew
    if request.url.path.rstrip("/") == "/metrics":
        return await call_next(request)

    start_time = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        status_code = 500
        raise
    finally:
        duration = time.perf_counter() - start_time
        route = request.scope.get("route")
        matched = route.path if route and hasattr(route, "path") else None
        endpoint = normalize_metric_endpoint(request.url.path, matched_route=matched)
        record_http_request(
            method=request.method,
            endpoint=endpoint,
            status_code=status_code,
            duration_seconds=duration,
        )


class ScanRequest(BaseModel):
    barcode: str = Field(..., pattern=r"^\d{8,14}$")
    product_name: str | None = None
    claims: list[str] | None = None


class VerifyClaimsRequest(BaseModel):
    claims: list[str] = Field(..., description="List of health or nutritional claims to verify")
    nutrition: dict[str, Any] | None = None
    ingredients: Any | None = None
    barcode: str | None = None
    product_name: str | None = None


class CompareRequest(BaseModel):
    product_a: str
    product_b: str


class AnalyzeRequest(BaseModel):
    product_name: str
    calories: float | None = None
    fat: float | None = None
    saturated_fat: float | None = None
    sugar: float | None = None
    salt: float | None = None
    protein: float | None = None
    fiber: float | None = None
    carbs: float | None = None
    serving_size: float | None = None
    ingredients: str | None = None
    claims: list[str] | None = None


class AlternativesRequest(BaseModel):
    product_name: str | None = None
    barcode: str | None = None
    category: str | None = None
    nutrition: dict[str, Any] | None = None
    limit: int = 3


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User question or query")
    barcode: str | None = None
    product_context: dict[str, Any] | None = None
    conversation_history: list[dict[str, Any]] | None = None


class OCRRequest(BaseModel):
    image_base64: str
    claims: list[str] | None = None
    async_mode: bool | None = Field(default=False, description="Whether to run OCR asynchronously")


class FoodLogRequest(BaseModel):
    product_name: str
    barcode: str | None = None
    calories: float = 0
    fat: float | None = None
    sugar: float | None = None
    salt: float | None = None
    protein: float | None = None
    fiber: float | None = None
    carbs: float | None = None
    serving_size: float | None = None


class FoodLogUpdateRequest(BaseModel):
    product_name: str | None = None
    calories: float | None = None
    fat: float | None = None
    sugar: float | None = None
    salt: float | None = None
    protein: float | None = None
    fiber: float | None = None
    carbs: float | None = None


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    name: str | None = None
    daily_calorie_limit: int = 2000


class LoginRequest(BaseModel):
    email: str
    password: str


class UserProfileUpdateRequest(BaseModel):
    name: str | None = None
    daily_calorie_limit: int | None = None
    diet_type: str | None = None
    allergens: str | None = None
    age: int | None = None
    weight: float | None = None
    height: float | None = None
    goal_type: str | None = None
    goal_target_days: int | None = None



def _to_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_less(a: float | None, b: float | None) -> int | None:
    if a is None or b is None or a == 0:
        return None
    if b >= a:
        return None
    return int(round(((a - b) / a) * 100))


def _pct_more(a: float | None, b: float | None) -> int | None:
    if a is None or b is None or a == 0:
        return None
    if b <= a:
        return None
    return int(round(((b - a) / a) * 100))


def _csv_fuzzy_lookup(name: str, threshold: float = 75.0) -> dict | None:
    q = (name or "").strip()
    if not q:
        return None

    try:
        df = pd.read_csv(Path(__file__).resolve().parents[1] / "datasets" / "indian_foods" / "indian_packaged_foods.csv")
    except Exception:
        return None

    if "product_name" not in df.columns:
        return None

    best_row = None
    best_name = None
    best_score = 0.0
    for _, row in df.iterrows():
        candidate = str(row.get("product_name") or "").strip()
        if not candidate:
            continue
        score = max(
            float(fuzz.token_set_ratio(q.lower(), candidate.lower())),
            float(fuzz.partial_ratio(q.lower(), candidate.lower())),
        )
        if score > best_score:
            best_score = score
            best_row = row
            best_name = candidate

    if best_row is None or best_score < float(threshold):
        return None

    def _f(v: object) -> float | None:
        return _to_float(v)

    return {
        "product_name": best_name,
        "calories": _f(best_row.get("calories")),
        "fat": _f(best_row.get("fat")),
        "sugar": _f(best_row.get("sugar")),
        "salt": _f(best_row.get("salt")),
        "protein": _f(best_row.get("protein")),
        "fiber": _f(best_row.get("fiber")),
        "carbs": _f(best_row.get("carbs")),
        "ingredients": None,
        "additives": None,
        "source": "indian_dataset",
        "_match": {
            "query": q,
            "matched_name": best_name,
            "similarity": best_score,
            "source": "indian_dataset_fuzzy",
        },
    }


def _ocr_extract_text(image_bytes: bytes) -> str:
    text, _ = run_ocr_engine(image_bytes)
    return text


def _parse_nutrition_from_text(text: str) -> dict:
    return parse_structured_ocr(text)


def get_ocr_job_timeout_seconds() -> int:
    """Retrieve configurable timeout for OCR jobs (default: 120s)."""
    raw = os.environ.get("OCR_JOB_TIMEOUT_SECONDS")
    if raw:
        try:
            return max(1, int(raw.strip()))
        except (ValueError, TypeError):
            pass
    return 120


def _parse_utc_iso(dt_str: str | None) -> datetime | None:
    """Parse UTC ISO-8601 string safely into a timezone-aware datetime."""
    if not dt_str:
        return None
    try:
        clean_str = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def recover_stale_ocr_jobs(
    db: Session,
    job_id: str | None = None,
    timeout_seconds: int | None = None,
) -> int:
    """Detect and recover stale pending or processing OCR jobs exceeding the timeout.

    Uses atomic conditional database updates to ensure completed results cannot be overwritten.
    """
    if timeout_seconds is None:
        timeout_seconds = get_ocr_job_timeout_seconds()

    now = datetime.now(timezone.utc)
    now_str = now.isoformat()

    query = db.query(OCRJob).filter(OCRJob.status.in_(["pending", "processing"]))
    if job_id:
        query = query.filter(OCRJob.id == job_id)

    candidates = query.all()
    stale_ids = []
    for candidate in candidates:
        ref_time = _parse_utc_iso(candidate.started_at) if candidate.status == "processing" else None
        if not ref_time:
            ref_time = _parse_utc_iso(candidate.created_at)

        if ref_time and (now - ref_time).total_seconds() >= timeout_seconds:
            stale_ids.append(candidate.id)

    if not stale_ids:
        return 0

    rows_updated = (
        db.query(OCRJob)
        .filter(
            OCRJob.id.in_(stale_ids),
            OCRJob.status.in_(["pending", "processing"]),
        )
        .update(
            {
                OCRJob.status: "failed",
                OCRJob.error_message: "OCR processing timed out or worker restarted. Please retry your scan.",
                OCRJob.completed_at: now_str,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if rows_updated > 0:
        record_ocr_stale_reaped(rows_updated)
    return rows_updated


def execute_ocr_background_task(
    job_id: str,
    image_bytes: bytes,
    claims: list[str] | None = None,
) -> None:
    """Execute OCR pipeline in background task and persist result."""
    task_db = SessionLocal()
    try:
        now_str = datetime.now(timezone.utc).isoformat()
        # Atomic transition to processing and record started_at
        rows_started = (
            task_db.query(OCRJob)
            .filter(OCRJob.id == job_id, OCRJob.status == "pending")
            .update(
                {
                    OCRJob.status: "processing",
                    OCRJob.started_at: now_str,
                },
                synchronize_session=False,
            )
        )
        task_db.commit()
        if rows_started == 0:
            job_check = task_db.query(OCRJob).filter(OCRJob.id == job_id).first()
            if not job_check or job_check.status != "processing":
                logger.info("OCR job %s is not pending or processing; skipping execution.", job_id)
                return

        result = process_image_ocr(image_bytes)

        if claims:
            eff_nutrition = {
                "calories": result.get("calories"),
                "fat": result.get("fat"),
                "sugar": result.get("sugar"),
                "salt": result.get("salt"),
                "protein": result.get("protein"),
                "fiber": result.get("fiber"),
                "carbs": result.get("carbs"),
            }
            ing_raw = ""
            if isinstance(result.get("ingredients"), dict):
                ing_raw = result["ingredients"].get("raw_text") or ""
            result["claim_verification"] = verify_claims(
                claims=claims,
                nutrition=eff_nutrition,
                ingredients=ing_raw,
            )

        # Atomic update: only write result if job remains in processing state!
        completed_now_str = datetime.now(timezone.utc).isoformat()
        rows_completed = (
            task_db.query(OCRJob)
            .filter(OCRJob.id == job_id, OCRJob.status == "processing")
            .update(
                {
                    OCRJob.status: "completed",
                    OCRJob.result_json: json.dumps(result),
                    OCRJob.completed_at: completed_now_str,
                },
                synchronize_session=False,
            )
        )
        task_db.commit()
        if rows_completed > 0:
            duration_sec = None
            if "now_str" in locals():
                try:
                    s_dt = _parse_utc_iso(now_str)
                    if s_dt:
                        duration_sec = max(0.0, (datetime.now(timezone.utc) - s_dt).total_seconds())
                except Exception:
                    duration_sec = None
            record_ocr_job_status("completed", duration_seconds=duration_sec)
            record_ocr_confidence(result.get("confidence", "low"))
        else:
            logger.info("OCR job %s was transitioned out of processing before completion; skipped result write.", job_id)
    except Exception as exc:
        logger.exception("OCR background task failed for job %s: %s", job_id, exc)
        try:
            failed_now_str = datetime.now(timezone.utc).isoformat()
            task_db.query(OCRJob).filter(
                OCRJob.id == job_id,
                OCRJob.status.in_(["pending", "processing"]),
            ).update(
                {
                    OCRJob.status: "failed",
                    OCRJob.error_message: "OCR processing failed",
                    OCRJob.completed_at: failed_now_str,
                },
                synchronize_session=False,
            )
            task_db.commit()
            record_ocr_job_status("failed")
        except Exception:
            pass
    finally:
        try:
            del image_bytes
        except Exception:
            pass
        task_db.close()


@app.post("/ocr", tags=["tracking"])
def ocr_nutrition_label(
    req: OCRRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    async_param: bool = Query(False, alias="async"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    enforce_rate_limit("ocr", request, user_id=current_user.id)

    img_b64 = (req.image_base64 or "").strip()
    if not img_b64:
        raise HTTPException(status_code=400, detail="image_base64 is required")

    if "," in img_b64 and "base64" in img_b64[:80].lower():
        img_b64 = img_b64.split(",", 1)[1].strip()

    # Reject oversized Base64 string early before decoding
    if len(img_b64) > MAX_BASE64_CHAR_LIMIT:
        raise HTTPException(
            status_code=413,
            detail="Image payload exceeds maximum allowed size of 10 MB",
        )

    try:
        image_bytes = base64.b64decode(img_b64, validate=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid base64") from e

    if not image_bytes:
        raise HTTPException(status_code=400, detail="empty image payload")

    # Safe validation of decoded bytes (size, magic bytes, integrity)
    is_valid, status_code, err_detail, _ = validate_image_payload(image_bytes)
    if not is_valid:
        raise HTTPException(status_code=status_code, detail=err_detail)

    is_async = bool(async_param or req.async_mode)
    if is_async:
        img_hash = hashlib.sha256(image_bytes).hexdigest()
        job_id = f"ocr_{uuid.uuid4().hex}"
        now_str = datetime.now(timezone.utc).isoformat()

        job = OCRJob(
            id=job_id,
            user_id=current_user.id,
            status="pending",
            image_hash=img_hash,
            result_json=None,
            error_message=None,
            created_at=now_str,
            started_at=None,
            completed_at=None,
        )
        db.add(job)
        db.commit()
        record_ocr_job_created("async")

        background_tasks.add_task(
            execute_ocr_background_task,
            job_id=job_id,
            image_bytes=image_bytes,
            claims=req.claims,
        )

        response.status_code = status.HTTP_202_ACCEPTED
        return {
            "job_id": job_id,
            "status": "pending",
            "created_at": now_str,
            "poll_url": f"/ocr/jobs/{job_id}",
        }

    record_ocr_job_created("sync")
    sync_start = time.perf_counter()
    try:
        result = process_image_ocr(image_bytes)
        duration_sec = time.perf_counter() - sync_start
        status_val = (
            "failed"
            if isinstance(result, dict) and result.get("status") in {OCRStatus.INVALID_IMAGE, OCRStatus.EXTRACTION_FAILED}
            else "completed"
        )
        record_ocr_job_status(status_val, duration_seconds=duration_sec)
        if isinstance(result, dict):
            conf = result.get("confidence") or result.get("nutrition_confidence")
            if conf:
                record_ocr_confidence(conf)
    except Exception:
        duration_sec = time.perf_counter() - sync_start
        record_ocr_job_status("failed", duration_seconds=duration_sec)
        raise

    # Optional Batch 5 Claim Verification integration if claims are passed
    if req.claims:
        eff_nutrition = {
            "calories": result.get("calories"),
            "fat": result.get("fat"),
            "sugar": result.get("sugar"),
            "salt": result.get("salt"),
            "protein": result.get("protein"),
            "fiber": result.get("fiber"),
            "carbs": result.get("carbs"),
        }
        ing_raw = ""
        if isinstance(result.get("ingredients"), dict):
            ing_raw = result["ingredients"].get("raw_text") or ""
        result["claim_verification"] = verify_claims(
            claims=req.claims,
            nutrition=eff_nutrition,
            ingredients=ing_raw,
        )

    return result


@app.get("/ocr/jobs/{job_id}", tags=["tracking"])
def get_ocr_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Retrieve the status and result of an OCR background processing job."""
    # Check and recover if this specific job has timed out
    recover_stale_ocr_jobs(db, job_id=job_id)

    job = (
        db.query(OCRJob)
        .filter(OCRJob.id == job_id, OCRJob.user_id == current_user.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="OCR job not found")

    result_data = None
    if job.result_json:
        try:
            result_data = json.loads(job.result_json)
        except Exception:
            result_data = None

    return {
        "job_id": job.id,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "result": result_data,
        "error": job.error_message,
    }


@app.post("/food-log", tags=["tracking"])
def log_food(
    req: FoodLogRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    product_name = (req.product_name or "").strip()
    if not product_name:
        raise HTTPException(status_code=400, detail="product_name is required")

    # Scale nutrition values by serving size if provided (OCR returns per 100g values)
    scale_factor = 1.0
    if req.serving_size and req.serving_size > 0:
        scale_factor = req.serving_size / 100.0

    calories = float(req.calories or 0.0) * scale_factor
    fat = float(req.fat) * scale_factor if req.fat is not None else None
    sugar = float(req.sugar) * scale_factor if req.sugar is not None else None
    salt = float(req.salt) * scale_factor if req.salt is not None else None
    protein = float(req.protein) * scale_factor if req.protein is not None else None
    fiber = float(req.fiber) * scale_factor if req.fiber is not None else None
    carbs = float(req.carbs) * scale_factor if req.carbs is not None else None

    db_service.log_food_consumption(
        db,
        barcode=(req.barcode or "manual"),
        product_name=product_name,
        calories=calories,
        fat=fat,
        sugar=sugar,
        salt=salt,
        protein=protein,
        fiber=fiber,
        carbs=carbs,
        user_id=int(current_user.id),
    )
    db.commit()
    return {"status": "logged"}


@app.delete("/food-log/{log_id}", tags=["tracking"])
def delete_food_log_endpoint(
    log_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    success = db_service.delete_food_log(db, log_id=int(log_id), user_id=int(current_user.id))
    if not success:
        raise HTTPException(status_code=404, detail="Food log entry not found or unauthorized")
    return {"deleted": True, "log_id": log_id}


@app.put("/food-log/{log_id}", tags=["tracking"])
def update_food_log_endpoint(
    log_id: int,
    req: FoodLogUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    updates = req.model_dump(exclude_unset=True) if hasattr(req, "model_dump") else req.dict(exclude_unset=True)
    updated = db_service.update_food_log(db, log_id=int(log_id), user_id=int(current_user.id), updates=updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="Food log entry not found or unauthorized")
    return {
        "status": "updated",
        "food_log": {
            "id": updated.id,
            "product_name": updated.product_name,
            "calories": updated.calories,
            "fat": updated.fat,
            "sugar": updated.sugar,
            "salt": updated.salt,
            "protein": updated.protein,
            "fiber": updated.fiber,
            "carbs": updated.carbs,
            "consumed_at": updated.consumed_at,
        },
    }



@app.post("/analyze", tags=["tracking"])
def analyze(
    req: AnalyzeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    name = (req.product_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="product_name is required")

    product = {
        "barcode": "00000000",
        "product_name": name,
        "calories": req.calories,
        "fat": req.fat,
        "saturated_fat": req.saturated_fat,
        "sugar": req.sugar,
        "salt": req.salt,
        "protein": req.protein,
        "fiber": req.fiber,
        "carbs": req.carbs,
        "ingredients": req.ingredients,
        "additives": None,
    }

    product["ingredient_analysis"] = analyze_ingredients(product.get("ingredients"))
    product["additive_analysis"] = analyze_additives(product.get("additives"))

    diet_type = getattr(current_user, "diet_type", None)
    if diet_type:
        health = compute_diet_aware_score(product, diet_type)
    else:
        health = compute_food_health_score(product)

    today_cal = db_service.get_today_calories(db, user_id=int(current_user.id))
    daily_limit = float(getattr(current_user, "daily_calorie_limit", None) or 2000)
    remaining_cal = daily_limit - today_cal

    final = compute_final_decision(
        health,
        remaining_calories=float(remaining_cal),
        product_calories=float(product.get("calories") or 0.0),
    )
    product["health_score"] = final.get("health_score")
    product["final_decision"] = final.get("final_decision")
    product["reasons"] = build_decision_reasons(product, remaining_calories=float(remaining_cal))

    diet_note = final.get("diet_note")
    if diet_note is None and isinstance(health, dict):
        diet_note = health.get("diet_note")
    product["diet_note"] = diet_note

    nutrition_per_100g = {
        "calories": product.get("calories"),
        "fat": product.get("fat"),
        "saturated_fat": product.get("saturated_fat"),
        "sugar": product.get("sugar"),
        "salt": product.get("salt"),
        "protein": product.get("protein"),
        "fiber": product.get("fiber"),
        "carbs": product.get("carbs"),
    }

    serving_size = req.serving_size
    if isinstance(serving_size, (int, float)):
        try:
            serving_size = float(serving_size)
        except Exception:
            serving_size = None
    if serving_size is not None and serving_size <= 0:
        serving_size = None

    nutrition_per_serving = None
    if serving_size is not None:
        ratio = float(serving_size) / 100.0

        def _scale(v: object) -> float | None:
            f = _to_float(v)
            if f is None:
                return None
            return round(f * ratio, 3)

        nutrition_per_serving = {
            "serving_size": float(serving_size),
            "calories": _scale(nutrition_per_100g.get("calories")),
            "fat": _scale(nutrition_per_100g.get("fat")),
            "saturated_fat": _scale(nutrition_per_100g.get("saturated_fat")),
            "sugar": _scale(nutrition_per_100g.get("sugar")),
            "salt": _scale(nutrition_per_100g.get("salt")),
            "protein": _scale(nutrition_per_100g.get("protein")),
            "fiber": _scale(nutrition_per_100g.get("fiber")),
            "carbs": _scale(nutrition_per_100g.get("carbs")),
        }

    final_decision_str = str(product.get("final_decision") or "").upper()
    if final_decision_str != "SAFE":
        recommendations = get_healthier_alternatives(name, nutrition_per_100g, limit=3)
    else:
        recommendations = []

    personalized = get_personalized_analysis(
        product=product,
        user=current_user,
        remaining_calories=remaining_cal,
        today_calories_consumed=today_cal,
    )
    explanation = explain_score(product, diet_type)

    analysis_res = {
        "product": {
            "name": product.get("product_name"),
            "nutrition": nutrition_per_100g,
            "nutrition_per_100g": nutrition_per_100g,
            "nutrition_per_serving": nutrition_per_serving,
            "nutriscore": None,
        },
        "analysis": {
            "ingredient_analysis": product.get("ingredient_analysis"),
            "additive_analysis": product.get("additive_analysis"),
            "health_score": product.get("health_score"),
        },
        "decision": {
            "final_decision": product.get("final_decision"),
            "reasons": product.get("reasons"),
        },
        "diet_note": diet_note,
        "recommendations": recommendations,
        "daily_intake": {
            "consumed": today_cal,
            "remaining": remaining_cal,
        },
        "personalized_analysis": personalized,
        "explanation": explanation,
    }

    if req.claims:
        analysis_res["claim_verification"] = verify_claims(
            claims=req.claims,
            nutrition=nutrition_per_100g,
            ingredients=product.get("ingredients"),
        )

    return analysis_res


# Startup lifecycle managed via FastAPI lifespan context manager


@app.get("/metrics", tags=["tracking"])
def metrics() -> Response:
    """Prometheus metrics exposition endpoint."""
    payload, content_type = get_metrics_exposition()
    return Response(content=payload, media_type=content_type)


@app.get("/health", tags=["tracking"])
def health() -> dict:
    return {"status": "running"}


@app.post("/register", tags=["auth"])
def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    enforce_rate_limit("auth", request)
    email = (req.email or "").strip().lower()
    password = req.password or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="email and password are required")

    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        raise HTTPException(status_code=400, detail="email already registered")

    user = User(
        email=email,
        name=(req.name.strip() if isinstance(req.name, str) and req.name.strip() else None),
        hashed_password=hash_password(password),
        daily_calorie_limit=int(req.daily_calorie_limit or 2000),
        diet_type=None,
        created_at=db_service._utc_now_str(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user_id=int(user.id))
    return {"access_token": token, "token_type": "bearer"}


@app.post("/login", tags=["auth"])
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    enforce_rate_limit("auth", request)
    email = (req.email or "").strip().lower()
    password = req.password or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="email and password are required")

    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user_id=int(user.id))
    return {"access_token": token, "token_type": "bearer"}


@app.get("/search", tags=["products"])
def search(
    query: str = Query(..., min_length=2, max_length=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    return search_products(db, query)


@app.get("/product/{barcode}", tags=["products"])
def get_product(
    barcode: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    barcode = (barcode or "").strip()
    if not barcode.isdigit() or not (8 <= len(barcode) <= 14):
        raise HTTPException(status_code=400, detail="invalid barcode")

    product = db_service.get_product_by_barcode(db, barcode)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")

    product["ingredient_analysis"] = analyze_ingredients(product.get("ingredients"))
    product["additive_analysis"] = analyze_additives(product.get("additives"))

    diet_type = getattr(current_user, "diet_type", None)
    if diet_type:
        health = compute_diet_aware_score(product, diet_type)
    else:
        health = compute_food_health_score(product)

    today_cal = db_service.get_today_calories(db, user_id=int(current_user.id)) if current_user else 0.0
    daily_limit = float(getattr(current_user, "daily_calorie_limit", None) or 2000)
    remaining_cal = daily_limit - today_cal

    final = compute_final_decision(
        health,
        remaining_calories=float(remaining_cal),
        product_calories=float(product.get("calories") or 0.0),
    )
    product["health_score"] = final.get("health_score")
    product["final_decision"] = final.get("final_decision")
    product["reasons"] = build_decision_reasons(product, remaining_calories=float(remaining_cal))
    product["diet_note"] = health.get("diet_note")

    personalized = get_personalized_analysis(
        product=product,
        user=current_user,
        remaining_calories=remaining_cal,
        today_calories_consumed=today_cal,
    )
    explanation = explain_score(product, diet_type)

    analysis = {
        "ingredient_analysis": product.get("ingredient_analysis"),
        "additive_analysis": product.get("additive_analysis"),
        "health_score": product.get("health_score"),
    }
    decision = {
        "final_decision": product.get("final_decision"),
        "reasons": product.get("reasons"),
    }

    return {
        "product": {
            "name": product.get("product_name"),
            "brand": product.get("brand"),
            "barcode": product.get("barcode"),
            "nutrition": {
                "calories": product.get("calories"),
                "fat": product.get("fat"),
                "sugar": product.get("sugar"),
                "salt": product.get("salt"),
                "protein": product.get("protein"),
                "fiber": product.get("fiber"),
                "carbs": product.get("carbs"),
            },
            "nutriscore": product.get("nutriscore"),
        },
        "analysis": analysis,
        "decision": decision,
        "diet_note": product.get("diet_note"),
        "personalized_analysis": personalized,
        "explanation": explanation,
    }


@app.get("/today", tags=["tracking"])
def today(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    today_calories = db_service.get_today_calories(db, user_id=int(current_user.id))
    remaining_calories = db_service.get_remaining_calories(
        db,
        daily_limit=float(current_user.daily_calorie_limit or 2000),
        user_id=int(current_user.id),
    )

    start, end = db_service._local_day_bounds()
    today_foods = db.execute(
        select(
            FoodLog.id,
            FoodLog.product_name,
            FoodLog.calories,
            FoodLog.consumed_at,
            FoodLog.fat,
            FoodLog.sugar,
            FoodLog.salt,
            FoodLog.protein,
        ).where(
            FoodLog.user_id == int(current_user.id),
            FoodLog.consumed_at >= start,
            FoodLog.consumed_at < end,
        ).order_by(FoodLog.consumed_at.desc())
    ).all()

    return {
        "calories_consumed_today": today_calories,
        "remaining_calories": remaining_calories,
        "foods": [
            {
                "id": r[0],
                "product_name": r[1],
                "calories": float(r[2]) if r[2] is not None else None,
                "consumed_at": r[3],
                "fat": float(r[4]) if r[4] is not None else None,
                "sugar": float(r[5]) if r[5] is not None else None,
                "salt": float(r[6]) if r[6] is not None else None,
                "protein": float(r[7]) if r[7] is not None else None,
            }
            for r in today_foods
        ],
    }



@app.post("/scan", tags=["tracking"])
def scan(
    req: ScanRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    enforce_rate_limit("scan", request, user_id=current_user.id)
    barcode = req.barcode.strip()
    if not barcode:
        raise HTTPException(status_code=400, detail="barcode is required")

    try:
        result = lookup_product(
            db,
            barcode,
            product_name_hint=req.product_name,
            user_id=int(current_user.id),
            daily_calorie_limit=int(current_user.daily_calorie_limit or 2000),
            diet_type=current_user.diet_type,
        )
    except Exception as e:
        logging.error(f"Scan error for barcode {barcode}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Scan error: {str(e)}")
    
    if result is None:
        raise HTTPException(status_code=404, detail="product not found")

    nutrition = {
        "calories": result.get("calories"),
        "fat": result.get("fat"),
        "sugar": result.get("sugar"),
        "salt": result.get("salt"),
        "protein": result.get("protein"),
        "fiber": result.get("fiber"),
        "carbs": result.get("carbs"),
    }

    final_decision = str(result.get("final_decision") or "")
    if final_decision and final_decision.upper() != "SAFE":
        recommendations = get_healthier_alternatives(str(result.get("product_name") or ""), nutrition)
    else:
        recommendations = []

    scan_response = {
        "product": {
            "name": result.get("product_name"),
            "brand": result.get("brand"),
            "barcode": barcode,
            "nutrition": nutrition,
            "nutriscore": result.get("nutriscore"),
            "ingredients": result.get("ingredients"),
        },
        "analysis": {
            "ingredient_analysis": result.get("ingredient_analysis"),
            "additive_analysis": result.get("additive_analysis"),
            "health_score": result.get("health_score"),
        },
        "decision": {
            "final_decision": result.get("final_decision"),
            "reasons": result.get("reasons"),
        },
        "diet_note": result.get("diet_note"),
        "recommendations": recommendations,
        "daily_intake": {
            "consumed": result.get("today_calories_consumed"),
            "remaining": result.get("remaining_calories"),
        },
    }
    scan_response["personalized_analysis"] = get_personalized_analysis(
        product=result,
        user=current_user,
        remaining_calories=result.get("remaining_calories"),
        today_calories_consumed=result.get("today_calories_consumed"),
    )
    scan_response["allergen_warnings"] = scan_response["personalized_analysis"].get("allergen_warnings", [])
    scan_response["detected_allergens"] = scan_response["personalized_analysis"].get("detected_allergens", [])
    scan_response["explanation"] = explain_score(result, getattr(current_user, "diet_type", None))


    if req.claims:
        scan_response["claim_verification"] = verify_claims(
            claims=req.claims,
            nutrition=nutrition,
            ingredients=result.get("ingredients"),
        )
    return scan_response


@app.post("/verify-claims", tags=["claims"])
def verify_claims_endpoint(
    req: VerifyClaimsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if not isinstance(req.claims, list):
        raise HTTPException(status_code=400, detail="claims must be a list of strings")

    nutrition = dict(req.nutrition) if isinstance(req.nutrition, dict) else {}
    ingredients = req.ingredients

    # If barcode provided and nutrition or ingredients are missing, enrich from DB
    if req.barcode:
        bcode = str(req.barcode).strip()
        if bcode:
            product = db_service.get_product_by_barcode(db, bcode)
            if product:
                db_nutrition = {
                    "calories": product.get("calories"),
                    "fat": product.get("fat"),
                    "sugar": product.get("sugar"),
                    "salt": product.get("salt"),
                    "protein": product.get("protein"),
                    "fiber": product.get("fiber"),
                    "carbs": product.get("carbs"),
                }
                for k, v in db_nutrition.items():
                    if k not in nutrition and v is not None:
                        nutrition[k] = v
                if ingredients is None:
                    ingredients = product.get("ingredients")

    return verify_claims(claims=req.claims, nutrition=nutrition, ingredients=ingredients)


@app.post("/alternatives", tags=["products"])
def alternatives_endpoint(
    req: AlternativesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    name = (req.product_name or "").strip()
    barcode = (req.barcode or "").strip()
    nutrition = dict(req.nutrition) if isinstance(req.nutrition, dict) else {}

    if not name and not barcode:
        raise HTTPException(status_code=400, detail="product_name or barcode is required")

    # If barcode provided and name or nutrition missing, look up product
    if barcode:
        prod = db_service.get_product_by_barcode(db, barcode)
        if prod:
            if not name:
                name = prod.get("product_name") or ""
            db_nutr = {
                "calories": prod.get("calories"),
                "fat": prod.get("fat"),
                "sugar": prod.get("sugar"),
                "salt": prod.get("salt"),
                "protein": prod.get("protein"),
                "fiber": prod.get("fiber"),
                "carbs": prod.get("carbs"),
            }
            for k, v in db_nutr.items():
                if k not in nutrition and v is not None:
                    nutrition[k] = v

    if not nutrition and name:
        # Try finding product in db or csv to get nutrition
        found = db_service.get_product_by_name_fuzzy(db, name, min_similarity=80.0)
        if not found:
            found = _csv_fuzzy_lookup(name, threshold=75.0)
        if found:
            nutrition = {
                "calories": found.get("calories"),
                "fat": found.get("fat"),
                "sugar": found.get("sugar"),
                "salt": found.get("salt"),
                "protein": found.get("protein"),
                "fiber": found.get("fiber"),
                "carbs": found.get("carbs"),
            }

    limit = max(1, min(10, int(req.limit or 3)))
    alternatives = get_healthier_alternatives(name, nutrition, limit=limit)
    category = infer_food_category(name, nutrition)

    return {
        "product_name": name or None,
        "barcode": barcode or None,
        "category": category,
        "alternatives": alternatives,
        "total_alternatives": len(alternatives),
    }


@app.get("/alternatives/{barcode}", tags=["products"])
def get_alternatives_by_barcode(
    barcode: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    barcode_str = (barcode or "").strip()
    if not barcode_str:
        raise HTTPException(status_code=400, detail="barcode is required")

    prod = db_service.get_product_by_barcode(db, barcode_str)
    if prod is None:
        raise HTTPException(status_code=404, detail="product not found")

    nutrition = {
        "calories": prod.get("calories"),
        "fat": prod.get("fat"),
        "sugar": prod.get("sugar"),
        "salt": prod.get("salt"),
        "protein": prod.get("protein"),
        "fiber": prod.get("fiber"),
        "carbs": prod.get("carbs"),
    }
    name = str(prod.get("product_name") or "")
    category = infer_food_category(name, nutrition)
    alternatives = get_healthier_alternatives(name, nutrition, limit=3)

    return {
        "product_name": name,
        "barcode": barcode_str,
        "category": category,
        "alternatives": alternatives,
        "total_alternatives": len(alternatives),
    }


@app.post("/compare", tags=["products"])
def compare(
    req: CompareRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    q_a = (req.product_a or "").strip()
    q_b = (req.product_b or "").strip()
    if not q_a or not q_b:
        raise HTTPException(status_code=400, detail="product_a and product_b are required")

    def _find_product(query: str, param_name: str) -> dict:
        if query.isdigit() and 8 <= len(query) <= 14:
            p = db_service.get_product_by_barcode(db, query)
            if p is not None:
                return p
        p = db_service.get_product_by_name_fuzzy(db, query, min_similarity=80.0)
        if p is not None:
            return p
        ds = _csv_fuzzy_lookup(query, threshold=75.0)
        if ds is not None:
            return ds
        raise HTTPException(status_code=404, detail=f"{param_name} not found")

    a = _find_product(q_a, "product_a")
    b = _find_product(q_b, "product_b")

    a_enriched = dict(a)
    if not a_enriched.get("ingredient_analysis"):
        a_enriched["ingredient_analysis"] = analyze_ingredients(a_enriched.get("ingredients"))
    if not a_enriched.get("additive_analysis"):
        a_enriched["additive_analysis"] = analyze_additives(a_enriched.get("additives"))

    b_enriched = dict(b)
    if not b_enriched.get("ingredient_analysis"):
        b_enriched["ingredient_analysis"] = analyze_ingredients(b_enriched.get("ingredients"))
    if not b_enriched.get("additive_analysis"):
        b_enriched["additive_analysis"] = analyze_additives(b_enriched.get("additives"))

    diet_type = getattr(current_user, "diet_type", None)
    if diet_type:
        a_health = compute_diet_aware_score(a_enriched, diet_type)
        b_health = compute_diet_aware_score(b_enriched, diet_type)
    else:
        a_health = compute_food_health_score(a_enriched)
        b_health = compute_food_health_score(b_enriched)

    a_score_val = a_health.get("health_score")
    b_score_val = b_health.get("health_score")

    a_nutriscore = a.get("nutriscore")
    if not a_nutriscore:
        try:
            from ml_model.predict_nutriscore import calculate_heuristic_nutriscore
            a_nutriscore = calculate_heuristic_nutriscore(a)
        except Exception:
            a_nutriscore = None

    b_nutriscore = b.get("nutriscore")
    if not b_nutriscore:
        try:
            from ml_model.predict_nutriscore import calculate_heuristic_nutriscore
            b_nutriscore = calculate_heuristic_nutriscore(b)
        except Exception:
            b_nutriscore = None

    a_nutrition = {
        "calories": _to_float(a.get("calories")),
        "fat": _to_float(a.get("fat")),
        "saturated_fat": _to_float(a.get("saturated_fat")),
        "carbohydrates": _to_float(a.get("carbs")),
        "sugar": _to_float(a.get("sugar")),
        "fiber": _to_float(a.get("fiber")),
        "protein": _to_float(a.get("protein")),
        "salt": _to_float(a.get("salt")),
    }
    b_nutrition = {
        "calories": _to_float(b.get("calories")),
        "fat": _to_float(b.get("fat")),
        "saturated_fat": _to_float(b.get("saturated_fat")),
        "carbohydrates": _to_float(b.get("carbs")),
        "sugar": _to_float(b.get("sugar")),
        "fiber": _to_float(b.get("fiber")),
        "protein": _to_float(b.get("protein")),
        "salt": _to_float(b.get("salt")),
    }

    reasons: list[str] = []
    # Factual nutrient comparisons
    if a_nutrition.get("sugar") is not None and b_nutrition.get("sugar") is not None:
        if b_nutrition["sugar"] < a_nutrition["sugar"]:
            pct = _pct_less(a_nutrition["sugar"], b_nutrition["sugar"])
            reasons.append(f"{b.get('product_name')}: {pct}% less sugar" if pct is not None else f"{b.get('product_name')} has lower sugar")
        elif a_nutrition["sugar"] < b_nutrition["sugar"]:
            pct = _pct_less(b_nutrition["sugar"], a_nutrition["sugar"])
            reasons.append(f"{a.get('product_name')}: {pct}% less sugar" if pct is not None else f"{a.get('product_name')} has lower sugar")

    if a_nutrition.get("salt") is not None and b_nutrition.get("salt") is not None:
        if b_nutrition["salt"] < a_nutrition["salt"]:
            pct = _pct_less(a_nutrition["salt"], b_nutrition["salt"])
            reasons.append(f"{b.get('product_name')}: {pct}% less sodium" if pct is not None else f"{b.get('product_name')} has lower sodium")
        elif a_nutrition["salt"] < b_nutrition["salt"]:
            pct = _pct_less(b_nutrition["salt"], a_nutrition["salt"])
            reasons.append(f"{a.get('product_name')}: {pct}% less sodium" if pct is not None else f"{a.get('product_name')} has lower sodium")

    if a_nutrition.get("fat") is not None and b_nutrition.get("fat") is not None:
        if b_nutrition["fat"] < a_nutrition["fat"]:
            pct = _pct_less(a_nutrition["fat"], b_nutrition["fat"])
            reasons.append(f"{b.get('product_name')}: {pct}% less fat" if pct is not None else f"{b.get('product_name')} has lower fat")
        elif a_nutrition["fat"] < b_nutrition["fat"]:
            pct = _pct_less(b_nutrition["fat"], a_nutrition["fat"])
            reasons.append(f"{a.get('product_name')}: {pct}% less fat" if pct is not None else f"{a.get('product_name')} has lower fat")

    if a_nutrition.get("fiber") is not None and b_nutrition.get("fiber") is not None:
        if b_nutrition["fiber"] > a_nutrition["fiber"]:
            pct = _pct_more(a_nutrition["fiber"], b_nutrition["fiber"])
            reasons.append(f"{b.get('product_name')}: {pct}% more fiber" if pct is not None else f"{b.get('product_name')} has higher fiber")
        elif a_nutrition["fiber"] > b_nutrition["fiber"]:
            pct = _pct_more(b_nutrition["fiber"], a_nutrition["fiber"])
            reasons.append(f"{a.get('product_name')}: {pct}% more fiber" if pct is not None else f"{a.get('product_name')} has higher fiber")

    if a_nutrition.get("protein") is not None and b_nutrition.get("protein") is not None:
        if b_nutrition["protein"] > a_nutrition["protein"]:
            pct = _pct_more(a_nutrition["protein"], b_nutrition["protein"])
            reasons.append(f"{b.get('product_name')}: {pct}% more protein" if pct is not None else f"{b.get('product_name')} has higher protein")
        elif a_nutrition["protein"] > b_nutrition["protein"]:
            pct = _pct_more(b_nutrition["protein"], a_nutrition["protein"])
            reasons.append(f"{a.get('product_name')}: {pct}% more protein" if pct is not None else f"{a.get('product_name')} has higher protein")

    if a_nutrition.get("calories") is not None and b_nutrition.get("calories") is not None:
        if b_nutrition["calories"] < a_nutrition["calories"]:
            pct = _pct_less(a_nutrition["calories"], b_nutrition["calories"])
            reasons.append(f"{b.get('product_name')}: {pct}% fewer calories" if pct is not None else f"{b.get('product_name')} has fewer calories")
        elif a_nutrition["calories"] < b_nutrition["calories"]:
            pct = _pct_less(b_nutrition["calories"], a_nutrition["calories"])
            reasons.append(f"{a.get('product_name')}: {pct}% fewer calories" if pct is not None else f"{a.get('product_name')} has fewer calories")

    if a_score_val is not None and b_score_val is not None:
        if b_score_val > a_score_val:
            healthier = str(b.get("product_name") or "")
        elif a_score_val > b_score_val:
            healthier = str(a.get("product_name") or "")
        else:
            healthier = "TIE"
    else:
        healthier = "TIE"

    return {
        "product_a": {
            "name": a.get("product_name"),
            "brand": a.get("brand"),
            "barcode": a.get("barcode"),
            "health_score": a_score_val,
            "nutriscore": a_nutriscore,
            "nutrition": a_nutrition,
            "match": a.get("_match"),
        },
        "product_b": {
            "name": b.get("product_name"),
            "brand": b.get("brand"),
            "barcode": b.get("barcode"),
            "health_score": b_score_val,
            "nutriscore": b_nutriscore,
            "nutrition": b_nutrition,
            "match": b.get("_match"),
        },
        "healthier_product": healthier,
        "reasons": reasons,
    }


@app.get("/history", tags=["tracking"])
def history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    return db_service.get_recent_scans(db, user_id=int(current_user.id), limit=20)


@app.delete("/history/{scan_id}", tags=["tracking"])
def delete_history(
    scan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    scan = (
        db.query(ScanHistory)
        .filter(
            ScanHistory.id == int(scan_id),
            ScanHistory.user_id == int(current_user.id),
        )
        .first()
    )
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")

    db.delete(scan)
    db.commit()
    return {"deleted": True}


@app.get("/stats", tags=["tracking"])
def stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    counts = db_service.get_scan_counts(db, user_id=int(current_user.id))
    most = db_service.get_most_scanned_product(db, user_id=int(current_user.id))
    decisions = db_service.get_decision_counts(db, user_id=int(current_user.id))
    avg_score = db_service.get_average_health_score(db, user_id=int(current_user.id))

    return {
        "total_scans_ever": counts.get("total_scans", 0),
        "scans_this_week": counts.get("scans_this_week", 0),
        "most_scanned_product": most,
        "average_health_score": avg_score,
        "decision_counts": decisions,
    }


@app.get("/user/profile", tags=["user"])
def get_user_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    profile = db_service.get_user_profile(db, user_id=int(current_user.id))
    if profile is None:
        raise HTTPException(status_code=404, detail="user not found")
    profile["calorie_estimate"] = calculate_personalized_calorie_estimate(
        profile.get("age"),
        profile.get("weight"),
        profile.get("height"),
        profile.get("goal_type"),
    )
    return profile


@app.put("/user/profile", tags=["user"])
def update_user_profile(
    req: UserProfileUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    diet_type = req.diet_type
    if isinstance(diet_type, str):
        diet_type = diet_type.strip().lower()
        if not diet_type:
            diet_type = None

    updated = db_service.update_user_profile(
        db,
        user_id=int(current_user.id),
        name=req.name,
        daily_calorie_limit=req.daily_calorie_limit,
        diet_type=diet_type,
        allergens=req.allergens,
        age=req.age,
        weight=req.weight,
        height=req.height,
        goal_type=req.goal_type,
        goal_target_days=req.goal_target_days,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="user not found")
    updated["calorie_estimate"] = calculate_personalized_calorie_estimate(
        updated.get("age"),
        updated.get("weight"),
        updated.get("height"),
        updated.get("goal_type"),
    )
    return updated



@app.get("/report/daily", tags=["tracking"])
def daily_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return generate_daily_report(db, current_user)


@app.get("/report/weekly", tags=["tracking"])
def weekly_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return generate_weekly_report(db, current_user)



@app.get("/explain/{barcode}", tags=["products"])
def explain_product(
    barcode: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    barcode_str = (barcode or "").strip()
    if not barcode_str:
        raise HTTPException(status_code=400, detail="barcode is required")

    product = db_service.get_product_by_barcode(db, barcode_str)
    if product is None:
        try:
            product = lookup_product(
                db,
                barcode_str,
                user_id=int(current_user.id),
                daily_calorie_limit=int(getattr(current_user, "daily_calorie_limit", None) or 2000),
                diet_type=getattr(current_user, "diet_type", None),
            )
        except Exception:
            product = None

    if product is None:
        raise HTTPException(status_code=404, detail="product not found")

    # Enrich with ingredient/additive analysis for explanation if missing
    if not product.get("ingredient_analysis"):
        product["ingredient_analysis"] = analyze_ingredients(product.get("ingredients"))
    if not product.get("additive_analysis"):
        product["additive_analysis"] = analyze_additives(product.get("additives"))

    return explain_score(product, getattr(current_user, "diet_type", None))


@app.get("/report/goal", tags=["tracking"])
def goal_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return generate_goal_report(db, current_user)


@app.post("/chat", tags=["assistant"])
def chat_endpoint(
    req: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    enforce_rate_limit("chat", request, user_id=current_user.id)
    msg = (req.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    barcode = req.barcode.strip() if isinstance(req.barcode, str) and req.barcode.strip() else None

    try:
        response = ask_nutrition_assistant(
            query=msg,
            barcode=barcode,
            product_context=req.product_context,
            user=current_user,
            db=db,
            conversation_history=req.conversation_history,
        )
        return response
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        logging.error(f"Chat endpoint error: {exc}")
        raise HTTPException(status_code=500, detail="An error occurred while processing the chat request")
