from __future__ import annotations

import os
import time
from typing import Any

import requests

try:
    import cv2
    from pyzbar.pyzbar import decode
except ImportError:
    cv2 = None
    decode = None


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
API_BASE_URL = os.environ.get("API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
API_URL = os.environ.get("API_URL", f"{API_BASE_URL}/scan")
LOGIN_URL = os.environ.get("LOGIN_URL", f"{API_BASE_URL}/login")


class ScannerAuthError(Exception):
    """Raised when scanner authentication fails."""
    pass


def login(
    email: str,
    password: str,
    base_url: str = API_BASE_URL,
    timeout: float = 10.0,
) -> str:
    """Authenticate against the /login endpoint and retrieve access token.

    Security Guarantee: Never prints, logs, or exposes the password or raw token.
    """
    clean_email = (email or "").strip().lower()
    if not clean_email or not password:
        raise ScannerAuthError("Email and password are required for authentication.")

    login_endpoint = f"{base_url.rstrip('/')}/login"
    try:
        resp = requests.post(
            login_endpoint,
            json={"email": clean_email, "password": password},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ScannerAuthError(f"Unable to connect to authentication server at {login_endpoint}: {exc}") from exc

    if resp.status_code == 200:
        try:
            data = resp.json()
        except Exception as exc:
            raise ScannerAuthError("Malformed JSON response from login endpoint.") from exc

        token = data.get("access_token")
        if not token or not isinstance(token, str):
            raise ScannerAuthError("Login response did not contain a valid access token.")
        return token

    if resp.status_code in (400, 401):
        try:
            detail = resp.json().get("detail", "Invalid email or password")
        except Exception:
            detail = "Invalid email or password"
        raise ScannerAuthError(f"Authentication failed: {detail}")

    if resp.status_code == 429:
        raise ScannerAuthError("Authentication rate limit exceeded. Please wait before retrying.")

    raise ScannerAuthError(f"Login failed with HTTP status {resp.status_code}.")


def resolve_auth_token(base_url: str = API_BASE_URL) -> str | None:
    """Resolve JWT access token from environment.

    Precedence:
    1. AUTH_TOKEN: Direct JWT Bearer token.
    2. AUTH_EMAIL & AUTH_PASSWORD: Authenticate via POST /login.

    Returns token string or None if unconfigured/failed.
    """
    token = os.environ.get("AUTH_TOKEN", "").strip()
    if token:
        return token

    email = os.environ.get("AUTH_EMAIL", "").strip()
    password = os.environ.get("AUTH_PASSWORD", "")
    if email and password:
        try:
            return login(email, password, base_url=base_url)
        except ScannerAuthError as exc:
            print(f"\n[AUTH ERROR] {exc}")
            return None

    return None


def scan_barcode(
    barcode: str,
    token: str | None = None,
    api_url: str = API_URL,
    timeout: float = 20.0,
) -> dict[str, Any] | None:
    """Send barcode scan request with optional Bearer JWT authorization.

    Handles network errors, authentication errors, and API error codes cleanly.
    """
    clean_barcode = str(barcode or "").strip()
    if not clean_barcode:
        return None

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(
            api_url,
            json={"barcode": clean_barcode},
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as e:
        print(f"\nRequest failed: {e}")
        return None

    if resp.status_code == 200:
        try:
            return resp.json()
        except Exception:
            print("\nError: Malformed JSON returned from scan endpoint.")
            return None

    if resp.status_code == 401:
        print("\nAuthentication error (401): Missing, invalid, or expired authorization token.")
        print("Tip: Provide a valid token via AUTH_TOKEN or configure AUTH_EMAIL and AUTH_PASSWORD.")
        return None

    if resp.status_code == 404:
        print(f"\nProduct not found (404) for barcode: {clean_barcode}")
        return None

    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    print(f"\nAPI error {resp.status_code}: {detail}")
    return None


def _extract_barcode_value(data: Any) -> str | None:
    if data is None:
        return None
    if isinstance(data, bytes):
        try:
            return data.decode("utf-8").strip()
        except Exception:
            return None
    return str(data).strip()


def _print_result(payload: dict[str, Any]) -> None:
    product_name = ((payload.get("product") or {}).get("name"))
    health_score = ((payload.get("analysis") or {}).get("health_score"))
    final_decision = ((payload.get("decision") or {}).get("final_decision"))
    reasons = ((payload.get("decision") or {}).get("reasons"))

    print("\n--- Scan Result ---")
    print(f"Product: {product_name}")
    print(f"Health score: {health_score}")
    print(f"Final decision: {final_decision}")
    if isinstance(reasons, list):
        print("Reasons:")
        for r in reasons:
            print(f"- {r}")
    else:
        print(f"Reasons: {reasons}")


def main() -> None:
    if cv2 is None or decode is None:
        raise RuntimeError(
            "OpenCV (cv2) and Pyzbar are required to run the webcam scanner.\n"
            "Please install them using: pip install opencv-python pyzbar"
        )

    token = resolve_auth_token(API_BASE_URL)
    if not token:
        print("\n[WARNING] Running in unauthenticated mode (no AUTH_TOKEN or AUTH_EMAIL/AUTH_PASSWORD found).")
        print("Scans against protected endpoints will return HTTP 401.")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Could not open webcam. Try changing the camera index (0, 1, 2...).")

    last_sent: str | None = None
    last_sent_ts = 0.0
    cooldown_seconds = 2.0

    print("Starting webcam barcode scanner...")
    print("- Press 'q' to quit")
    print(f"- API URL: {API_URL}")
    if token:
        print("- Authentication: Configured (Bearer token)")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        decoded = decode(gray)
        if not decoded:
            decoded = decode(frame)

        for barcode in decoded:
            x, y, w, h = barcode.rect
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

            barcode_data = _extract_barcode_value(getattr(barcode, "data", None))
            if barcode_data:
                cv2.putText(
                    frame,
                    barcode_data,
                    (x, max(0, y - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )

                now = time.time()
                if barcode_data != last_sent or (now - last_sent_ts) >= cooldown_seconds:
                    last_sent = barcode_data
                    last_sent_ts = now

                    payload = scan_barcode(barcode_data, token=token, api_url=API_URL)
                    if payload:
                        _print_result(payload)

        cv2.imshow("FoodScanner AI - Barcode Scanner (press q to quit)", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
