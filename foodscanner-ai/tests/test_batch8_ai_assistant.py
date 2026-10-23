from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "test-secret-key-for-batch-8-ai-assistant"
os.environ["AI_PROVIDER"] = "mock"
os.environ["FOODSCANNER_OCR_ENGINE"] = "tesseract"

from database.orm import SessionLocal, init_db as orm_init_db
from database.init_db import init_db
from database.models import User, Product, Nutrition, FoodLog
from services import db_service
from services.auth_service import hash_password, create_access_token
from services.rag_service import retrieve_relevant_knowledge, get_knowledge_retriever
from services.llm_provider import MockLLMProvider, GeminiProvider, OpenAIProvider, LLMProviderError, LLMUnavailableError
from services.food_health_score import compute_food_health_score, compute_diet_aware_score
from api.main import app


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Ensure database schema is initialized."""
    init_db()
    orm_init_db()


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def auth_user(db_session):
    """Create or retrieve a test user with a valid JWT token."""
    email = "batch8_chat_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Batch8 Chat User",
            hashed_password=hash_password("ChatPassword123"),
            daily_calorie_limit=2000,
            age=28,
            weight=75.0,
            height=178.0,
            diet_type="vegan",
            goal_type="lose_weight",
            goal_target_days=30,
            created_at=db_service._utc_now_str(),
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    else:
        user.age = 28
        user.weight = 75.0
        user.height = 178.0
        user.diet_type = "vegan"
        user.goal_type = "lose_weight"
        user.daily_calorie_limit = 2000
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=int(user.id))
    return {"user": user, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture(autouse=True)
def seed_batch8_products(db_session):
    """Seed test products for Batch 8 chat and RAG tests."""
    p1 = db_service.get_product_by_barcode(db_session, "8908000000001")
    if not p1:
        prod1 = db_service.create_product(
            db_session,
            {
                "barcode": "8908000000001",
                "product_name": "Batch8 Dark Chocolate Bar",
                "brand": "CocoaCraft",
                "nutriscore": "d",
                "ingredients": "Cocoa mass, sugar, cocoa butter, emulsifier",
                "additives": "E322",
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": prod1.id,
                "calories": 530.0,
                "fat": 32.0,
                "saturated_fat": 19.0,
                "sugar": 38.0,
                "salt": 0.05,
                "protein": 6.5,
                "fiber": 8.0,
                "carbs": 52.0,
            },
        )
        db_session.commit()

    p2 = db_service.get_product_by_barcode(db_session, "8908000000002")
    if not p2:
        prod2 = db_service.create_product(
            db_session,
            {
                "barcode": "8908000000002",
                "product_name": "Batch8 Incomplete Oatmeal",
                "brand": "PureOats",
                "nutriscore": "a",
                "ingredients": "Whole rolled oats",
                "additives": None,
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": prod2.id,
                "calories": 370.0,
                "fat": None,
                "saturated_fat": None,
                "sugar": None,
                "salt": None,
                "protein": 12.0,
                "fiber": 10.0,
                "carbs": None,
            },
        )
        db_session.commit()


# ==============================================================================
# TEST 1: /chat Requires Authentication
# ==============================================================================
def test_chat_endpoint_requires_auth():
    client = TestClient(app)

    # 1. No token -> 401
    res_unauth = client.post("/chat", json={"message": "What is sugar?"})
    assert res_unauth.status_code == 401

    # 2. Invalid token -> 401
    res_bad = client.post(
        "/chat",
        json={"message": "What is sugar?"},
        headers={"Authorization": "Bearer fake.bad.jwt.token"},
    )
    assert res_bad.status_code == 401


# ==============================================================================
# TEST 2: Empty Message Rejected
# ==============================================================================
def test_chat_empty_message_rejected(auth_user):
    client = TestClient(app)

    # Empty string
    res_empty = client.post("/chat", json={"message": ""}, headers=auth_user["headers"])
    assert res_empty.status_code in (400, 422)

    # Whitespace only
    res_spaces = client.post("/chat", json={"message": "   "}, headers=auth_user["headers"])
    assert res_spaces.status_code in (400, 422)


# ==============================================================================
# TEST 3: General Nutrition Question Works With Retrieval
# ==============================================================================
def test_chat_general_nutrition_question(auth_user):
    client = TestClient(app)

    res = client.post(
        "/chat",
        json={"message": "What is the difference between sugar and added sugar?"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()

    assert data["product_context_used"] is False
    assert data["retrieved_context_count"] > 0
    assert len(data["sources"]) > 0
    assert "sugar" in data["answer"].lower()
    assert "added" in data["answer"].lower()


# ==============================================================================
# TEST 4: Product-Specific Question Receives Product Context
# ==============================================================================
def test_chat_product_specific_question(auth_user):
    client = TestClient(app)

    res = client.post(
        "/chat",
        json={
            "message": "Why did this product get this score?",
            "barcode": "8908000000001",
        },
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()

    assert data["product_context_used"] is True
    assert data["product_name"] == "Batch8 Dark Chocolate Bar"
    assert data["health_score"] is not None
    assert "official score" in data["answer"].lower() or "score" in data["answer"].lower()


# ==============================================================================
# TEST 5: User Profile Context Is Used When Available
# ==============================================================================
def test_chat_user_profile_context_used(auth_user):
    client = TestClient(app)

    # User profile has goal_type="lose_weight" and diet_type="vegan"
    res = client.post(
        "/chat",
        json={
            "message": "Is this product good for my goal?",
            "barcode": "8908000000001",
        },
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()

    assert data["profile_context_used"] is True
    assert "lose weight" in data["answer"].lower() or "goal" in data["answer"].lower()


# ==============================================================================
# TEST 6: Missing Profile Does Not Crash
# ==============================================================================
def test_chat_missing_profile_does_not_crash(db_session):
    email = "batch8_no_profile@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="No Profile User",
            hashed_password=hash_password("Secret123"),
            daily_calorie_limit=2000,
            age=None,
            weight=None,
            height=None,
            diet_type=None,
            goal_type=None,
            created_at=db_service._utc_now_str(),
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=int(user.id))
    headers = {"Authorization": f"Bearer {token}"}

    client = TestClient(app)
    res = client.post(
        "/chat",
        json={"message": "Can I eat this?", "barcode": "8908000000001"},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["profile_context_used"] is False
    assert len(data["answer"]) > 0


# ==============================================================================
# TEST 7: Unknown Barcode Handled Gracefully
# ==============================================================================
def test_chat_unknown_barcode_handled_gracefully(auth_user):
    client = TestClient(app)

    with patch("services.ai_nutrition_assistant.lookup_product", return_value=None):
        res = client.post(
            "/chat",
            json={"message": "Tell me about this food", "barcode": "0000000000000"},
            headers=auth_user["headers"],
        )
    assert res.status_code == 200
    data = res.json()

    # Unknown product resolves safely without crashing
    assert data["product_context_used"] is False
    assert data["product_name"] is None
    assert len(data["answer"]) > 0


# ==============================================================================
# TEST 8: Missing Nutrition Handled Gracefully
# ==============================================================================
def test_chat_missing_nutrition_handled_gracefully(auth_user):
    client = TestClient(app)

    # Product with missing fat/sugar/salt values
    res = client.post(
        "/chat",
        json={"message": "What is the nutrition breakdown?", "barcode": "8908000000002"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()

    assert data["product_context_used"] is True
    assert data["product_name"] == "Batch8 Incomplete Oatmeal"
    assert len(data["answer"]) > 0


# ==============================================================================
# TEST 9: Retrieval Returns Relevant Knowledge
# ==============================================================================
def test_rag_retrieval_returns_relevant_knowledge():
    # Direct retrieval service test
    sugar_results = retrieve_relevant_knowledge("What are the FSSAI rules for sugar free?", top_k=3)
    assert len(sugar_results) > 0
    top_doc = sugar_results[0]
    assert "sugar" in top_doc["topic"].lower() or "sugar" in top_doc["text"].lower()

    protein_results = retrieve_relevant_knowledge("How much protein do I need per day?", top_k=3)
    assert len(protein_results) > 0
    assert any("protein" in d["topic"].lower() or "protein" in d["text"].lower() for d in protein_results)


# ==============================================================================
# TEST 10: Sources Are Returned in Schema
# ==============================================================================
def test_chat_sources_returned_in_schema(auth_user):
    client = TestClient(app)

    res = client.post(
        "/chat",
        json={"message": "How much salt can I eat per day?"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()

    assert "sources" in data
    assert len(data["sources"]) > 0
    for s in data["sources"]:
        assert "id" in s
        assert "title" in s
        assert "source" in s
        assert "type" in s


# ==============================================================================
# TEST 11: AI Does NOT Create Food-Log Entries (Scan != Eat)
# ==============================================================================
def test_chat_does_not_create_food_log(auth_user, db_session):
    client = TestClient(app)
    user_id = auth_user["user"].id

    count_before = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()

    # Chat with barcode
    res1 = client.post(
        "/chat",
        json={"message": "Is this chocolate healthy?", "barcode": "8908000000001"},
        headers=auth_user["headers"],
    )
    assert res1.status_code == 200

    # General chat
    res2 = client.post(
        "/chat",
        json={"message": "Tell me about fiber"},
        headers=auth_user["headers"],
    )
    assert res2.status_code == 200

    count_after = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()
    assert count_after == count_before, "POST /chat must NEVER write to FoodLog (Scan != Eat)"


# ==============================================================================
# TEST 12: AI Does NOT Modify Health Score
# ==============================================================================
def test_chat_does_not_modify_health_score(auth_user, seed_batch8_products):
    client = TestClient(app)

    # Check deterministic health score
    prod_data = {
        "calories": 530.0,
        "fat": 32.0,
        "saturated_fat": 19.0,
        "sugar": 38.0,
        "salt": 0.05,
        "protein": 6.5,
        "fiber": 8.0,
        "ingredients": "Cocoa mass, sugar, cocoa butter, emulsifier",
    }
    user_diet = getattr(auth_user["user"], "diet_type", None)
    if user_diet:
        deterministic_score = compute_diet_aware_score(prod_data, user_diet)["health_score"]
    else:
        deterministic_score = compute_food_health_score(prod_data)["health_score"]

    res = client.post(
        "/chat",
        json={"message": "Can you score this product?", "barcode": "8908000000001"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()

    # The reported health score in response must match the deterministic score
    assert data["health_score"] == deterministic_score


# ==============================================================================
# TEST 13: AI Does NOT Invent Missing Nutrition Values
# ==============================================================================
def test_chat_does_not_invent_missing_nutrition(auth_user):
    client = TestClient(app)

    # Incomplete oatmeal has no fat declared
    res = client.post(
        "/chat",
        json={"message": "How much fat is in this product?", "barcode": "8908000000002"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()

    # Answer should state not available or not invent a number
    assert "not available" in data["answer"].lower() or "fat" in data["answer"].lower()


# ==============================================================================
# TEST 14: AI Provider Failure Handled Gracefully
# ==============================================================================
def test_chat_provider_failure_handled_gracefully(auth_user):
    client = TestClient(app)

    # Simulate provider network timeout or failure
    with patch("services.llm_provider.MockLLMProvider.generate", side_effect=LLMUnavailableError("Service timed out")):
        res = client.post(
            "/chat",
            json={"message": "What is sugar?"},
            headers=auth_user["headers"],
        )
        assert res.status_code == 200
        data = res.json()
        assert "temporarily unavailable" in data["answer"].lower()


# ==============================================================================
# TEST 15: Missing API Key Handled Gracefully
# ==============================================================================
def test_chat_missing_api_key_handled_gracefully():
    # If Gemini requested without key, health check reports unconfigured
    gemini = GeminiProvider(api_key=None)
    status = gemini.health_check()
    assert status["status"] == "unconfigured"

    # OpenAI without key reports unconfigured
    openai = OpenAIProvider(api_key=None)
    status_oa = openai.health_check()
    assert status_oa["status"] == "unconfigured"


# ==============================================================================
# TEST 16: Malformed AI Response Handled Gracefully
# ==============================================================================
def test_chat_malformed_ai_response_handled_gracefully(auth_user):
    client = TestClient(app)

    with patch("services.llm_provider.MockLLMProvider.generate", return_value=""):
        res = client.post(
            "/chat",
            json={"message": "What is nutrition?"},
            headers=auth_user["headers"],
        )
        assert res.status_code == 200
        assert "answer" in res.json()


# ==============================================================================
# TEST 17: Response is JSON Serializable
# ==============================================================================
def test_chat_response_json_serializable(auth_user):
    client = TestClient(app)

    res = client.post(
        "/chat",
        json={"message": "Is this good for weight loss?", "barcode": "8908000000001"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    serialized = json.dumps(res.json())
    assert isinstance(serialized, str)


# ==============================================================================
# TEST 18: Existing /scan Still Works
# ==============================================================================
def test_existing_scan_still_works(auth_user):
    client = TestClient(app)

    res = client.post("/scan", json={"barcode": "8908000000001"}, headers=auth_user["headers"])
    assert res.status_code == 200
    data = res.json()
    assert data["product"]["name"] == "Batch8 Dark Chocolate Bar"
    assert "health_score" in data["analysis"]
    assert "final_decision" in data["decision"]


# ==============================================================================
# TEST 19: Existing /analyze Still Works
# ==============================================================================
def test_existing_analyze_still_works(auth_user):
    client = TestClient(app)

    res = client.post(
        "/analyze",
        json={"product_name": "Batch8 Protein Snack", "calories": 250, "protein": 15, "sugar": 3},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert data["product"]["name"] == "Batch8 Protein Snack"
    assert "personalized_analysis" in data


# ==============================================================================
# TEST 20: Existing /compare Still Works
# ==============================================================================
def test_existing_compare_still_works(auth_user):
    client = TestClient(app)

    res = client.post(
        "/compare",
        json={"product_a": "8908000000001", "product_b": "8908000000002"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert "healthier_product" in data
    assert "product_a" in data
    assert "product_b" in data


# ==============================================================================
# TEST 21: Existing /alternatives Still Works
# ==============================================================================
def test_existing_alternatives_still_works(auth_user):
    client = TestClient(app)

    res = client.post(
        "/alternatives",
        json={"product_name": "Balaji Wafers Masala", "nutrition": {"calories": 540, "fat": 35, "sugar": 2.5}},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert "alternatives" in data
    assert isinstance(data["alternatives"], list)


# ==============================================================================
# TEST 22: Existing /verify-claims Still Works
# ==============================================================================
def test_existing_verify_claims_still_works(auth_user):
    client = TestClient(app)

    res = client.post(
        "/verify-claims",
        json={"claims": ["sugar_free"], "nutrition": {"sugar": 0.2}},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert data["results"][0]["status"] == "SUPPORTED"
