from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import requests

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scanner.barcode_scanner import (
    ScannerAuthError,
    _extract_barcode_value,
    _print_result,
    login,
    resolve_auth_token,
    scan_barcode,
)


# ==============================================================================
# 1. LOGIN TESTS
# ==============================================================================

def test_login_success():
    """Verify login succeeds and returns access_token without exposing credentials."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "valid-jwt-token-12345", "token_type": "bearer"}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        token = login("test@example.com", "SecurePassword123!", base_url="http://127.0.0.1:8000")
        assert token == "valid-jwt-token-12345"
        mock_post.assert_called_once_with(
            "http://127.0.0.1:8000/login",
            json={"email": "test@example.com", "password": "SecurePassword123!"},
            timeout=10.0,
        )


def test_login_invalid_credentials_raises_auth_error():
    """Verify 401 returns clean ScannerAuthError and never prints or logs password."""
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"detail": "Invalid email or password"}

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(ScannerAuthError) as exc_info:
            login("user@example.com", "MySuperSecretPassword!")
        
        # Verify clean error message and ensure password is NOT exposed
        assert "Authentication failed" in str(exc_info.value)
        assert "MySuperSecretPassword!" not in str(exc_info.value)


def test_login_rate_limit_handled():
    """Verify 429 returns informative rate limit error."""
    mock_resp = MagicMock()
    mock_resp.status_code = 429

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(ScannerAuthError) as exc_info:
            login("user@example.com", "password123")
        assert "rate limit exceeded" in str(exc_info.value).lower()


def test_login_network_error_handled():
    """Verify connection failure raises ScannerAuthError cleanly."""
    with patch("requests.post", side_effect=requests.ConnectionError("Connection refused")):
        with pytest.raises(ScannerAuthError) as exc_info:
            login("user@example.com", "password123")
        assert "Unable to connect" in str(exc_info.value)


def test_login_missing_credentials_fails_early():
    """Verify empty email or password fails fast without network request."""
    with patch("requests.post") as mock_post:
        with pytest.raises(ScannerAuthError) as exc_info:
            login("", "password123")
        assert "Email and password are required" in str(exc_info.value)
        mock_post.assert_not_called()


# ==============================================================================
# 2. RESOLVE AUTH TOKEN TESTS
# ==============================================================================

def test_resolve_auth_token_from_env_auth_token(monkeypatch):
    """Verify AUTH_TOKEN environment variable takes top precedence without login request."""
    monkeypatch.setenv("AUTH_TOKEN", "direct-env-token-xyz")
    monkeypatch.setenv("AUTH_EMAIL", "other@example.com")
    monkeypatch.setenv("AUTH_PASSWORD", "other-pass")

    with patch("scanner.barcode_scanner.login") as mock_login:
        token = resolve_auth_token()
        assert token == "direct-env-token-xyz"
        mock_login.assert_not_called()


def test_resolve_auth_token_from_email_password(monkeypatch):
    """Verify fallback to AUTH_EMAIL and AUTH_PASSWORD when AUTH_TOKEN is unset."""
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.setenv("AUTH_EMAIL", "env_user@example.com")
    monkeypatch.setenv("AUTH_PASSWORD", "EnvSecretPass123")

    with patch("scanner.barcode_scanner.login", return_value="token-from-login") as mock_login:
        token = resolve_auth_token(base_url="http://testserver:8000")
        assert token == "token-from-login"
        mock_login.assert_called_once_with("env_user@example.com", "EnvSecretPass123", base_url="http://testserver:8000")


def test_resolve_auth_token_unconfigured(monkeypatch):
    """Verify returns None when no auth environment variables are set."""
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_EMAIL", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)

    token = resolve_auth_token()
    assert token is None


# ==============================================================================
# 3. SCAN BARCODE TESTS
# ==============================================================================

def test_scan_barcode_authenticated_success():
    """Verify scan_barcode attaches Bearer token header and returns parsed JSON."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "product": {"name": "Whole Grain Oats"},
        "analysis": {"health_score": 85},
        "decision": {"final_decision": "SAFE", "reasons": ["High in fiber"]},
    }

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = scan_barcode("8901234567890", token="jwt-bearer-abc", api_url="http://127.0.0.1:8000/scan")
        assert result is not None
        assert result["product"]["name"] == "Whole Grain Oats"

        mock_post.assert_called_once_with(
            "http://127.0.0.1:8000/scan",
            json={"barcode": "8901234567890"},
            headers={"Content-Type": "application/json", "Authorization": "Bearer jwt-bearer-abc"},
            timeout=20.0,
        )


def test_scan_barcode_unauthorized_401(capsys):
    """Verify 401 returns None and prints clean guidance without raising unhandled exception."""
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = '{"detail": "Invalid or expired token"}'

    with patch("requests.post", return_value=mock_resp):
        result = scan_barcode("8901234567890", token="expired-token")
        assert result is None
        captured = capsys.readouterr()
        assert "Authentication error (401)" in captured.out
        # Invariant: Never print full token value in error output
        assert "expired-token" not in captured.out


def test_scan_barcode_not_found_404(capsys):
    """Verify 404 returns None and prints product not found message."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = '{"detail": "product not found"}'

    with patch("requests.post", return_value=mock_resp):
        result = scan_barcode("0000000000000", token="valid-token")
        assert result is None
        captured = capsys.readouterr()
        assert "Product not found (404)" in captured.out


def test_scan_barcode_network_timeout(capsys):
    """Verify timeout is caught and handled gracefully."""
    with patch("requests.post", side_effect=requests.Timeout("Request timed out")):
        result = scan_barcode("8901234567890", token="valid-token")
        assert result is None
        captured = capsys.readouterr()
        assert "Request failed: Request timed out" in captured.out


# ==============================================================================
# 4. OUTPUT FORMATTING TESTS
# ==============================================================================

def test_extract_barcode_value():
    """Verify barcode extraction handles str, bytes, and None."""
    assert _extract_barcode_value(b"8901234567890") == "8901234567890"
    assert _extract_barcode_value("8901234567890") == "8901234567890"
    assert _extract_barcode_value(None) is None


def test_print_result_output(capsys):
    """Verify _print_result maintains expected output structure."""
    payload = {
        "product": {"name": "Healthy Makhana"},
        "analysis": {"health_score": 90},
        "decision": {
            "final_decision": "SAFE",
            "reasons": ["Low sodium", "Zero trans fat"],
        },
    }
    _print_result(payload)
    out = capsys.readouterr().out
    assert "Product: Healthy Makhana" in out
    assert "Health score: 90" in out
    assert "Final decision: SAFE" in out
    assert "- Low sodium" in out
    assert "- Zero trans fat" in out
