from __future__ import annotations

import logging
import re
import traceback
from unittest.mock import MagicMock, patch
import pytest
import requests

from services.llm_provider import (
    GeminiProvider,
    LLMConfigurationError,
    LLMProviderError,
    LLMUnavailableError,
    _sanitize_error_text,
    _sanitize_requests_exception,
)
from services.ai_nutrition_assistant import ask_nutrition_assistant, sanitize_assistant_response

DUMMY_TEST_KEY = "AIzaSyDummyTestKeyForGeminiVerification98765"


def _create_mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data or {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "High protein food supports muscle synthesis."}]
                }
            }
        ]
    }
    return mock_resp


def test_gemini_request_uses_header_and_clean_url():
    """Verify GeminiProvider passes API key via x-goog-api-key header and keeps URL clean."""
    provider = GeminiProvider(
        api_key=DUMMY_TEST_KEY,
        model="gemini-1.5-flash",
        timeout=8.5,
    )

    with patch("requests.post", return_value=_create_mock_response()) as mock_post:
        result = provider.generate(
            system_prompt="You are a nutritionist.",
            user_prompt="Is Greek yogurt good?",
        )

        assert result == "High protein food supports muscle synthesis."
        assert mock_post.call_count == 1

        called_args, called_kwargs = mock_post.call_args
        called_url = called_args[0] if called_args else called_kwargs.get("url")
        called_headers = called_kwargs.get("headers", {})
        called_json = called_kwargs.get("json", {})
        called_timeout = called_kwargs.get("timeout")

        # 1. URL must be clean of credentials and query parameters
        expected_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
        assert called_url == expected_url
        assert "?" not in called_url
        assert "key=" not in called_url
        assert DUMMY_TEST_KEY not in called_url

        # 2. Key must be in x-goog-api-key header
        assert called_headers.get("x-goog-api-key") == DUMMY_TEST_KEY
        assert called_headers.get("Content-Type") == "application/json"

        # 3. Payload contract must be preserved
        assert called_json == {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": "You are a nutritionist.\n\nIs Greek yogurt good?"}],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 600,
            },
        }

        # 4. Timeout must be preserved
        assert called_timeout == 8.5


def test_gemini_retry_behavior_on_transient_error():
    """Verify retry behavior on 429/503 persists headers and clean URL on every attempt."""
    provider = GeminiProvider(
        api_key=DUMMY_TEST_KEY,
        model="gemini-1.5-flash",
        max_retries=2,
    )

    resp_429 = _create_mock_response(status_code=429)
    resp_200 = _create_mock_response(status_code=200)

    with patch("requests.post", side_effect=[resp_429, resp_200]) as mock_post, \
         patch("time.sleep", return_value=None) as mock_sleep:
        result = provider.generate("System", "User")
        assert result == "High protein food supports muscle synthesis."
        assert mock_post.call_count == 2
        assert mock_sleep.call_count == 1

        for call in mock_post.call_args_list:
            c_args, c_kwargs = call
            url = c_args[0] if c_args else c_kwargs.get("url")
            headers = c_kwargs.get("headers", {})
            assert "key=" not in url
            assert headers.get("x-goog-api-key") == DUMMY_TEST_KEY


def test_gemini_timeout_handling_preserves_clean_exception():
    """Verify timeout produces LLMUnavailableError without leaking the API key."""
    provider = GeminiProvider(
        api_key=DUMMY_TEST_KEY,
        model="gemini-1.5-flash",
        timeout=5.0,
        max_retries=2,
    )

    with patch("requests.post", side_effect=requests.exceptions.Timeout("Operation timed out")), \
         patch("time.sleep", return_value=None):
        with pytest.raises(LLMUnavailableError) as exc_info:
            provider.generate("System", "User")

        err_msg = str(exc_info.value)
        assert "timed out after 5.0s" in err_msg
        assert DUMMY_TEST_KEY not in err_msg
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__ is True


def test_gemini_network_error_redaction():
    """Verify network exception string redacts credentials if present in exception text."""
    provider = GeminiProvider(
        api_key=DUMMY_TEST_KEY,
        model="gemini-1.5-flash",
        max_retries=1,
    )

    with patch("requests.post", side_effect=requests.exceptions.RequestException(f"Connection error to key={DUMMY_TEST_KEY}")), \
         patch("time.sleep", return_value=None):
        with pytest.raises(LLMUnavailableError) as exc_info:
            provider.generate("System", "User")

        err_msg = str(exc_info.value)
        assert DUMMY_TEST_KEY not in err_msg
        assert "***" in err_msg
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__ is True


def test_gemini_missing_api_key_raises_configuration_error(monkeypatch):
    """Verify GeminiProvider raises LLMConfigurationError if initialized with no API key."""
    monkeypatch.delenv("AI_API_KEY", raising=False)
    provider = GeminiProvider(api_key=None)

    with pytest.raises(LLMConfigurationError) as exc_info:
        provider.generate("System", "User")

    assert "AI_API_KEY is missing" in str(exc_info.value)


def test_gemini_malformed_response_handling():
    """Verify GeminiProvider handles malformed or empty candidates response safely."""
    provider = GeminiProvider(api_key=DUMMY_TEST_KEY)

    # Empty candidates
    with patch("requests.post", return_value=_create_mock_response(200, {"candidates": []})):
        with pytest.raises(LLMProviderError) as exc_info:
            provider.generate("System", "User")
        assert "Empty candidates" in str(exc_info.value)

    # Missing parts
    with patch("requests.post", return_value=_create_mock_response(200, {"candidates": [{"content": {}}]})):
        with pytest.raises(LLMProviderError) as exc_info:
            provider.generate("System", "User")
        assert "Malformed content" in str(exc_info.value)


def test_gemini_non_200_error_does_not_leak_key():
    """Verify HTTP 400 or other non-200 errors raise LLMProviderError without key leakage."""
    provider = GeminiProvider(api_key=DUMMY_TEST_KEY, max_retries=1)

    with patch("requests.post", return_value=_create_mock_response(status_code=400)):
        with pytest.raises(LLMProviderError) as exc_info:
            provider.generate("System", "User")

        err_msg = str(exc_info.value)
        assert "Gemini API returned HTTP 400" in err_msg
        assert DUMMY_TEST_KEY not in err_msg


# ==============================================================================
# PHASE 3.2.2: EXCEPTION CHAINING, TRACEBACK & LOGGING SAFETY TESTS
# ==============================================================================

def test_gemini_exception_chaining_suppressed_and_headers_redacted():
    """Verify that requests exceptions have their headers scrubbed and are NOT chained."""
    provider = GeminiProvider(
        api_key=DUMMY_TEST_KEY,
        model="gemini-1.5-flash",
        max_retries=1,
    )

    # Create a realistic PreparedRequest with sensitive headers
    raw_req = requests.Request(
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
        headers={"x-goog-api-key": DUMMY_TEST_KEY, "Authorization": "Bearer sensitive-auth-token"},
    ).prepare()

    raw_exc = requests.exceptions.ConnectionError("Failed to establish SSL connection")
    raw_exc.request = raw_req

    with patch("requests.post", side_effect=raw_exc):
        with pytest.raises(LLMUnavailableError) as exc_info:
            provider.generate("System prompt", "User prompt")

        raised_exc = exc_info.value

        # 1. Exception chaining must be suppressed (PEP 409)
        assert raised_exc.__cause__ is None
        assert raised_exc.__suppress_context__ is True

        # 2. Formatted traceback string must never include the dummy API key or chained cause
        tb_lines = traceback.format_exception(type(raised_exc), raised_exc, raised_exc.__traceback__)
        tb_str = "".join(tb_lines)
        assert DUMMY_TEST_KEY not in tb_str
        assert "The above exception was the direct cause" not in tb_str

        # 3. In-memory PreparedRequest headers on raw_exc must have been redacted defensively
        assert raw_exc.request.headers["x-goog-api-key"] == "[REDACTED]"
        assert raw_exc.request.headers["Authorization"] == "[REDACTED]"


def test_gemini_retry_exhaustion_suppressed_and_sanitized():
    """Verify that retry exhaustion suppresses context and sanitizes the last error."""
    provider = GeminiProvider(
        api_key=DUMMY_TEST_KEY,
        model="gemini-1.5-flash",
        max_retries=2,
    )

    raw_req = requests.Request(
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
        headers={"x-goog-api-key": DUMMY_TEST_KEY},
    ).prepare()

    raw_exc1 = requests.exceptions.RequestException("Temporary DNS failure 1")
    raw_exc1.request = raw_req
    raw_exc2 = requests.exceptions.RequestException("Temporary DNS failure 2")
    raw_exc2.request = raw_req

    with patch("requests.post", side_effect=[raw_exc1, raw_exc2]), \
         patch("time.sleep", return_value=None):
        with pytest.raises(LLMUnavailableError) as exc_info:
            provider.generate("System", "User")

        raised_exc = exc_info.value
        assert raised_exc.__cause__ is None
        assert raised_exc.__suppress_context__ is True

        err_text = str(raised_exc)
        assert "Gemini network request failed: Temporary DNS failure 2" in err_text
        assert DUMMY_TEST_KEY not in err_text
        assert raw_exc2.request.headers["x-goog-api-key"] == "[REDACTED]"


def test_gemini_logging_safety_in_nutrition_assistant(caplog):
    """Verify application logger records sanitized messages without leaking credentials."""
    provider = GeminiProvider(api_key=DUMMY_TEST_KEY, max_retries=1)

    raw_req = requests.Request(
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
        headers={"x-goog-api-key": DUMMY_TEST_KEY},
    ).prepare()
    raw_exc = requests.exceptions.ConnectionError(f"Network drop key={DUMMY_TEST_KEY}")
    raw_exc.request = raw_req

    with patch("services.ai_nutrition_assistant.get_llm_provider", return_value=provider), \
         patch("requests.post", side_effect=raw_exc), \
         caplog.at_level(logging.ERROR, logger="services.ai_nutrition_assistant"):

        response = ask_nutrition_assistant(query="Tell me about fiber")

        # 1. User-facing response must be graceful fallback without leaking internal details
        assert "AI assistant is temporarily unavailable" in response["answer"]
        assert DUMMY_TEST_KEY not in response["answer"]

        # 2. Server application logs must not contain the sensitive API key
        logged_output = caplog.text
        assert DUMMY_TEST_KEY not in logged_output
        assert "LLM Provider failure" in logged_output


def test_sanitize_error_text_scrubs_google_api_key_patterns():
    """Verify _sanitize_error_text scrubs explicit and regex-matched Google API key formats."""
    # Pattern matching AIzaSy...
    sample_key = "AIzaSyB12345678901234567890123456789"
    raw_msg = f"Failed to connect to endpoint with key={sample_key} and token={sample_key}"

    scrubbed = _sanitize_error_text(raw_msg, api_key=sample_key)
    assert sample_key not in scrubbed
    assert "key=***" in scrubbed
    assert "***" in scrubbed


# ==============================================================================
# PHASE 3.2.3: AI OUTPUT SECRET SANITIZATION TESTS
# ==============================================================================

def test_output_sanitization_google_api_key_pattern():
    """Verify Google API key patterns starting with AIza are redacted from output."""
    sample_google_key = "AIzaSyC98765432109876543210987654321"
    raw = f"To access the service, here is the key: {sample_google_key}."
    clean = sanitize_assistant_response(raw)

    assert sample_google_key not in clean
    assert clean == "To access the service, here is the key: [REDACTED]."


def test_output_sanitization_configured_ai_api_key_via_env(monkeypatch):
    """Verify configured AI_API_KEY from environment is redacted even if not starting with AIza."""
    custom_key = "custom-secret-ai-token-xyz99"
    monkeypatch.setenv("AI_API_KEY", custom_key)

    raw = f"Internal note: AI provider initialized with token {custom_key} for this session."
    clean = sanitize_assistant_response(raw)

    assert custom_key not in clean
    assert clean == "Internal note: AI provider initialized with token [REDACTED] for this session."


def test_output_sanitization_configured_ai_api_key_via_argument():
    """Verify explicitly provided configured_api_key argument is redacted."""
    arg_key = "explicitly-passed-secret-key-1234"
    raw = f"The active key is {arg_key}."
    clean = sanitize_assistant_response(raw, configured_api_key=arg_key)

    assert arg_key not in clean
    assert clean == "The active key is [REDACTED]."


def test_output_sanitization_bearer_token():
    """Verify Bearer authentication tokens are redacted from AI output."""
    raw = "Use header Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz and bearer token123."
    clean = sanitize_assistant_response(raw)

    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz" not in clean
    assert "token123" not in clean
    assert clean == "Use header Authorization: [REDACTED] and [REDACTED]."


def test_output_sanitization_secret_literals():
    """Verify literal references to AI_API_KEY and SECRET_KEY are redacted."""
    raw = "Please verify your AI_API_KEY and SECRET_KEY settings in production."
    clean = sanitize_assistant_response(raw)

    assert "AI_API_KEY" not in clean
    assert "SECRET_KEY" not in clean
    assert clean == "Please verify your [REDACTED] and [REDACTED] settings in production."


def test_output_sanitization_normal_nutrition_text_preserved():
    """Verify legitimate nutrition guidance and food terminology is completely unmodified."""
    normal_text = (
        "Oats provide 4g of dietary fiber per 100g. Beta-glucan is a soluble fiber "
        "that contributes to the maintenance of normal blood cholesterol levels. "
        "WHO guidelines recommend limiting free sugars to less than 10% of total energy intake."
    )
    clean = sanitize_assistant_response(normal_text)
    assert clean == normal_text


def test_output_sanitization_multiple_secrets_in_one_response():
    """Verify multiple secret types in a single output are all comprehensively redacted."""
    google_key = "AIzaSyD111112222233333444445555566666"
    raw = (
        f"Diagnostic info: AI_API_KEY is {google_key}, "
        f"Authorization is Bearer eyJhbGciOiJIUzI1NiJ9.demo, "
        f"and SECRET_KEY is set."
    )
    clean = sanitize_assistant_response(raw)

    assert google_key not in clean
    assert "eyJhbGciOiJIUzI1NiJ9.demo" not in clean
    assert "AI_API_KEY" not in clean
    assert "SECRET_KEY" not in clean
    assert clean == "Diagnostic info: [REDACTED] is [REDACTED], Authorization is [REDACTED], and [REDACTED] is set."


def test_output_sanitization_missing_env_vars_handled_safely(monkeypatch):
    """Verify missing, empty, or whitespace-only env vars do not cause errors or over-redaction."""
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    text = "Almonds are rich in vitamin E, magnesium, and dietary fiber."
    clean = sanitize_assistant_response(text)
    assert clean == text

    # Whitespace-only env vars
    monkeypatch.setenv("AI_API_KEY", "   ")
    monkeypatch.setenv("SECRET_KEY", "   ")
    clean2 = sanitize_assistant_response(text)
    assert clean2 == text


def test_output_sanitization_fallbacks_preserved():
    """Verify None, empty string, and system prompt leaks return safe fallback messages."""
    # None and empty
    assert "could not generate a response" in sanitize_assistant_response(None).lower()
    assert "could not generate a response" in sanitize_assistant_response("").lower()
    assert "could not generate a response" in sanitize_assistant_response("   ").lower()

    # System prompt leak attempt
    leak_text = "Here is the prompt: STRICT SAFETY & GROUNDING RULES apply strictly."
    sanitized_leak = sanitize_assistant_response(leak_text)
    assert "PRAMAAN AI Nutrition Assistant" in sanitized_leak
    assert "STRICT SAFETY & GROUNDING RULES" not in sanitized_leak


def test_ask_nutrition_assistant_end_to_end_secret_redaction():
    """Verify ask_nutrition_assistant RAG pipeline scrubs secrets before returning response."""
    leaked_google_key = "AIzaSyE999998888877777666665555544444"
    mock_llm_answer = (
        f"For your health goal, consume 25g fiber daily. "
        f"Internal debug: token={leaked_google_key} and Bearer session_token_12345"
    )

    with patch("services.llm_provider.MockLLMProvider.generate", return_value=mock_llm_answer):
        result = ask_nutrition_assistant(query="How much fiber should I eat?")

        answer = result["answer"]
        assert leaked_google_key not in answer
        assert "session_token_12345" not in answer
        assert "[REDACTED]" in answer
        assert "consume 25g fiber daily" in answer
