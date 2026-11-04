import uuid
import pytest
from fastapi.testclient import TestClient

from api.main import app
from database.orm import SessionLocal
from database.models import User, ScanHistory
from services.auth_service import create_access_token

client = TestClient(app)


@pytest.fixture
def test_users():
    """Create two clean authenticated test users to test history and user isolation."""
    email_a = f"history_user_a_{uuid.uuid4().hex[:6]}@example.com"
    email_b = f"history_user_b_{uuid.uuid4().hex[:6]}@example.com"

    with SessionLocal() as db:
        user_a = User(
            email=email_a,
            hashed_password="hashed_pw_123",
            name="History User Alpha",
            daily_calorie_limit=2000,
            created_at="2026-09-29 12:00:00",
        )
        user_b = User(
            email=email_b,
            hashed_password="hashed_pw_123",
            name="History User Beta",
            daily_calorie_limit=2000,
            created_at="2026-09-29 12:00:00",
        )
        db.add(user_a)
        db.add(user_b)
        db.commit()
        db.refresh(user_a)
        db.refresh(user_b)
        id_a, id_b = user_a.id, user_b.id

    token_a = create_access_token(user_id=id_a)
    token_b = create_access_token(user_id=id_b)

    return {
        "user_a": {"id": id_a, "email": email_a, "headers": {"Authorization": f"Bearer {token_a}"}},
        "user_b": {"id": id_b, "email": email_b, "headers": {"Authorization": f"Bearer {token_b}"}},
    }


def test_get_history_includes_real_db_id(test_users):
    """
    Test 1: Verify GET /history returns records containing real database `id`.
    """
    user_a = test_users["user_a"]

    # Seed 2 scan history records for User A directly via ORM
    with SessionLocal() as db:
        scan_1 = ScanHistory(user_id=user_a["id"], barcode="8901058851304", result="AVOID", scan_time="2026-09-29 10:00:00")
        scan_2 = ScanHistory(user_id=user_a["id"], barcode="8901719101038", result="SAFE", scan_time="2026-09-29 10:05:00")
        db.add(scan_1)
        db.add(scan_2)
        db.commit()
        db.refresh(scan_1)
        db.refresh(scan_2)
        db_id_1 = scan_1.id
        db_id_2 = scan_2.id

    res = client.get("/history", headers=user_a["headers"])
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
    assert len(items) >= 2

    # Check that each item contains the exact expected schema with 'id'
    item_ids = [it["id"] for it in items if "id" in it]
    assert db_id_1 in item_ids, f"Expected DB id {db_id_1} in GET /history, got {item_ids}"
    assert db_id_2 in item_ids, f"Expected DB id {db_id_2} in GET /history, got {item_ids}"

    # Confirm record fields match schema contract
    sample = next(it for it in items if it["id"] == db_id_2)
    assert sample["id"] == db_id_2
    assert sample["barcode"] == "8901719101038"
    assert sample["result"] == "SAFE"
    assert "scan_time" in sample


def test_delete_history_removes_correct_record(test_users):
    """
    Test 2: Verify DELETE /history/{id} removes the specific record and not others.
    """
    user_a = test_users["user_a"]

    with SessionLocal() as db:
        scan_to_delete = ScanHistory(user_id=user_a["id"], barcode="8901491100519", result="AVOID", scan_time="2026-09-29 11:00:00")
        scan_to_keep = ScanHistory(user_id=user_a["id"], barcode="8901058851304", result="MODERATE", scan_time="2026-09-29 11:05:00")
        db.add(scan_to_delete)
        db.add(scan_to_keep)
        db.commit()
        db.refresh(scan_to_delete)
        db.refresh(scan_to_keep)
        delete_id = scan_to_delete.id
        keep_id = scan_to_keep.id

    # Call DELETE /history/{scan_id}
    del_res = client.delete(f"/history/{delete_id}", headers=user_a["headers"])
    assert del_res.status_code == 200
    assert del_res.json() == {"deleted": True}

    # Verify directly in DB
    with SessionLocal() as db:
        assert db.query(ScanHistory).filter(ScanHistory.id == delete_id).first() is None
        assert db.query(ScanHistory).filter(ScanHistory.id == keep_id).first() is not None

    # Verify in GET /history
    hist_res = client.get("/history", headers=user_a["headers"])
    assert hist_res.status_code == 200
    hist_ids = [it["id"] for it in hist_res.json()]
    assert delete_id not in hist_ids
    assert keep_id in hist_ids


def test_delete_history_error_handling(test_users):
    """
    Test 3: Verify DELETE /history/{id} error handling for nonexistent and invalid IDs.
    """
    user_a = test_users["user_a"]

    # Non-existent scan_id -> 404
    res_404 = client.delete("/history/99999999", headers=user_a["headers"])
    assert res_404.status_code == 404
    assert "scan not found" in res_404.json().get("detail", "").lower()

    # Invalid non-integer ID -> 422
    res_422 = client.delete("/history/invalid-id-string", headers=user_a["headers"])
    assert res_422.status_code == 422

    # Unauthenticated delete -> 401
    res_401 = client.delete("/history/1")
    assert res_401.status_code == 401


def test_user_isolation_prevent_deleting_other_user_scan(test_users):
    """
    Test 4: User A must never be able to delete User B's scan even if they know the ID.
    """
    user_a = test_users["user_a"]
    user_b = test_users["user_b"]

    # Seed a scan for User B
    with SessionLocal() as db:
        b_scan = ScanHistory(user_id=user_b["id"], barcode="8901058851304", result="SAFE", scan_time="2026-09-29 12:00:00")
        db.add(b_scan)
        db.commit()
        db.refresh(b_scan)
        b_scan_id = b_scan.id

    # User A attempts to delete User B's scan
    res_unauthorized_del = client.delete(f"/history/{b_scan_id}", headers=user_a["headers"])
    assert res_unauthorized_del.status_code == 404, "User A should receive 404 when trying to delete User B's scan"

    # Verify User B's scan remains untouched in DB
    with SessionLocal() as db:
        remaining = db.query(ScanHistory).filter(ScanHistory.id == b_scan_id).first()
        assert remaining is not None
        assert remaining.user_id == user_b["id"]
