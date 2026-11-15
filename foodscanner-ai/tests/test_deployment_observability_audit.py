"""Tests for Task 5: Deployment Readiness & Observability Audit.

Validates:
1. database_connected Gauge in Prometheus metrics exposition
2. record_database_status behavior on healthy and failed database states
3. Readiness probe integration with database_connected metric
4. Strict production configuration validation (ENVIRONMENT=production key entropy & placeholder rejection)
5. Strict rejection of ALLOW_INSECURE_DEV_AUTH in production
6. Error response sanitization on /scan endpoint (no internal exception leak)
7. Grafana dashboard JSON schema validity and presence of Database Status panel
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from api.main import app
from database.orm import init_db as orm_init_db
from database.models import User
from services.auth_service import create_access_token
from services.config import get_secret_key, validate_runtime_config
from services.metrics import (
    DATABASE_CONNECTED,
    METRICS_REGISTRY,
    get_metrics_exposition,
    record_database_status,
)


@pytest.fixture(scope="module")
def client():
    orm_init_db()
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_headers():
    token = create_access_token(user_id=1)
    return {"Authorization": f"Bearer {token}"}


class TestMetricsAndObservability:
    def test_database_connected_gauge_initialization(self):
        """Gauge exists in dedicated registry and init_metrics sets it to 0.0 initially."""
        assert DATABASE_CONNECTED is not None
        from services.metrics import init_metrics
        init_metrics()
        # Initial state before runtime DB check must be 0.0 (unconnected) to avoid false healthy reporting
        assert METRICS_REGISTRY.get_sample_value("database_connected") == 0.0

        record_database_status(True)
        assert METRICS_REGISTRY.get_sample_value("database_connected") == 1.0

    def test_record_database_status_toggle(self):
        """Toggling database status gauge reflects immediately."""
        record_database_status(False)
        assert METRICS_REGISTRY.get_sample_value("database_connected") == 0.0

        record_database_status(True)
        assert METRICS_REGISTRY.get_sample_value("database_connected") == 1.0

    def test_metrics_endpoint_exposition(self, client):
        """GET /metrics includes database_connected metric and correct types."""
        res = client.get("/metrics")
        assert res.status_code == 200
        content = res.text
        assert "# HELP database_connected" in content
        assert "# TYPE database_connected gauge" in content
        assert "database_connected 1.0" in content

    def test_readiness_probe_success_sets_gauge(self, client):
        """GET /ready and /health/ready return 200 and set database_connected to 1.0."""
        for path in ("/ready", "/health/ready"):
            res = client.get(path)
            assert res.status_code == 200
            data = res.json()
            assert data.get("status") == "ready"
            assert data.get("database") == "connected"
        assert METRICS_REGISTRY.get_sample_value("database_connected") == 1.0

    def test_readiness_probe_failure_sets_gauge_to_zero(self, client):
        """When database ping fails, readiness returns 503 and gauge is set to 0.0."""
        with patch("api.main.text") as mock_text:
            mock_text.side_effect = RuntimeError("Simulated DB connection failure")
            res = client.get("/ready")
            assert res.status_code == 503
            assert METRICS_REGISTRY.get_sample_value("database_connected") == 0.0

        # Reset gauge to healthy
        record_database_status(True)

    def test_metrics_scrapeable_during_database_outage_without_overhead(self, client):
        """During a database outage, /metrics remains fully scrapeable, fast, and reports database_connected 0.0."""
        # Simulate database failure recorded by readiness probe
        record_database_status(False)

        res = client.get("/metrics")
        assert res.status_code == 200
        content = res.text
        assert "# HELP database_connected" in content
        assert "database_connected 0.0" in content

        # Reset gauge to healthy
        record_database_status(True)


class TestProductionSecurityConfiguration:
    def test_production_rejects_allow_insecure_dev_auth(self, monkeypatch):
        """In production, ALLOW_INSECURE_DEV_AUTH cannot bypass SECRET_KEY."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ALLOW_INSECURE_DEV_AUTH", "true")
        monkeypatch.delenv("SECRET_KEY", raising=False)

        # get_secret_key must raise
        with pytest.raises(RuntimeError, match="strictly forbidden in production"):
            get_secret_key()

        # validate_runtime_config must return invalid
        config = validate_runtime_config(raise_error=False)
        assert config["valid"] is False
        assert any("ALLOW_INSECURE_DEV_AUTH is strictly forbidden" in err for err in config["errors"])

    def test_production_rejects_insecure_dev_placeholder_keys(self, monkeypatch):
        """In production, known development placeholder keys are rejected."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("ALLOW_INSECURE_DEV_AUTH", raising=False)

        for bad_key in [
            "pramaan-local-dev-secret-key-minimum-32-chars",
            "dev-insecure-secret-key",
            "changeme",
            "secret",
        ]:
            monkeypatch.setenv("SECRET_KEY", bad_key)
            config = validate_runtime_config(raise_error=False)
            assert config["valid"] is False
            assert any("insecure development placeholder" in err for err in config["errors"])

    def test_production_rejects_short_secret_keys(self, monkeypatch):
        """In production, SECRET_KEY shorter than 32 characters is rejected."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("ALLOW_INSECURE_DEV_AUTH", raising=False)
        monkeypatch.setenv("SECRET_KEY", "short-key-16-bytes!")

        config = validate_runtime_config(raise_error=False)
        assert config["valid"] is False
        assert any("at least 32 characters long" in err for err in config["errors"])

    def test_production_accepts_strong_secret_key(self, monkeypatch):
        """In production, a strong >=32 char secret key is accepted with valid production DATABASE_URL."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("ALLOW_INSECURE_DEV_AUTH", raising=False)
        monkeypatch.setenv("SECRET_KEY", "a-very-strong-production-secret-key-with-high-entropy-64-bytes!!")
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://pramaan:secret@localhost:5432/foodscanner_db")

        config = validate_runtime_config(raise_error=False)
        assert config["valid"] is True
        assert len(config["errors"]) == 0
        assert config["secret_key_set"] is True

    def test_production_rejects_missing_database_url(self, monkeypatch):
        """In production, missing DATABASE_URL is rejected early without falling back to SQLite."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECRET_KEY", "a-very-strong-production-secret-key-with-high-entropy-64-bytes!!")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        config = validate_runtime_config(raise_error=False)
        assert config["valid"] is False
        assert any("DATABASE_URL must be explicitly configured" in err for err in config["errors"])

    def test_production_rejects_sqlite_database_url(self, monkeypatch):
        """In production, explicit SQLite DATABASE_URL is strictly rejected."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECRET_KEY", "a-very-strong-production-secret-key-with-high-entropy-64-bytes!!")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///database/prod_test.db")

        config = validate_runtime_config(raise_error=False)
        assert config["valid"] is False
        assert any("SQLite is strictly forbidden in production" in err for err in config["errors"])

    def test_resolve_database_url_production_and_dev(self, monkeypatch):
        """Verify resolve_database_url behavior across development fallback and production enforcement."""
        from database.orm import resolve_database_url

        # 1. Dev fallback to local SQLite
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        dev_url = resolve_database_url()
        assert "sqlite:///" in dev_url

        # 2. Production missing DATABASE_URL raises RuntimeError
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="DATABASE_URL must be explicitly configured in production"):
            resolve_database_url()

        # 3. Production SQLite raises RuntimeError
        with pytest.raises(RuntimeError, match="SQLite database is strictly forbidden in production"):
            resolve_database_url("sqlite:///app/database.db")

        # 4. Production PostgreSQL URL resolves cleanly
        pg_url = resolve_database_url("postgres://prod_user:prod_pass@rds-endpoint:5432/prod_db")
        assert pg_url.startswith("postgresql+psycopg2://prod_user:prod_pass@rds-endpoint:5432/prod_db")

    def test_production_and_dev_cors_isolation(self, monkeypatch):
        """Verify CORS allows LAN regex in dev, but strictly rejects unlisted origins in production."""
        from api.main import _get_allowed_origins

        # Dev mode: includes defaults
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        dev_origins = _get_allowed_origins()
        assert "http://localhost:8081" in dev_origins
        assert "http://localhost:3000" in dev_origins

        # Prod mode: only explicitly configured allowed origins
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://pramaan.ai, https://app.pramaan.ai")
        prod_origins = _get_allowed_origins()
        assert prod_origins == ["https://pramaan.ai", "https://app.pramaan.ai"]
        assert "http://localhost:8081" not in prod_origins

    def test_get_secret_key_direct_production_rejection(self, monkeypatch):
        """get_secret_key() independently enforces production key security even if called standalone."""
        monkeypatch.setenv("ENVIRONMENT", "production")

        # Weak dev placeholder key
        monkeypatch.setenv("SECRET_KEY", "pramaan-local-dev-secret-key-minimum-32-chars")
        with pytest.raises(RuntimeError, match="insecure development placeholder"):
            get_secret_key()

        # Key too short (< 32 chars)
        monkeypatch.setenv("SECRET_KEY", "short-secret-key")
        with pytest.raises(RuntimeError, match="at least 32 characters long"):
            get_secret_key()

    def test_production_rejects_unsupported_database_schemes(self, monkeypatch):
        """In production, unsupported database schemes (MySQL, Oracle, MSSQL) are strictly rejected."""
        from database.orm import resolve_database_url

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECRET_KEY", "a-very-strong-production-secret-key-with-high-entropy-64-bytes!!")

        unsupported_schemes = [
            "mysql://user:pass@localhost:3306/db",
            "mysql+pymysql://user:pass@localhost:3306/db",
            "oracle://user:pass@localhost:1521/db",
            "mssql+pyodbc://user:pass@localhost:1433/db",
            "mongodb://user:pass@localhost:27017/db",
        ]

        for bad_url in unsupported_schemes:
            monkeypatch.setenv("DATABASE_URL", bad_url)
            # 1. validate_runtime_config check
            config = validate_runtime_config(raise_error=False)
            assert config["valid"] is False
            assert any("Unsupported database scheme in production" in err for err in config["errors"])

            # 2. resolve_database_url check
            with pytest.raises(RuntimeError, match="Unsupported database scheme in production"):
                resolve_database_url(bad_url)

    def test_production_accepts_supported_postgres_schemes(self, monkeypatch):
        """In production, all supported PostgreSQL dialects and legacy aliases are accepted and normalized."""
        from database.orm import resolve_database_url

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECRET_KEY", "a-very-strong-production-secret-key-with-high-entropy-64-bytes!!")

        test_cases = [
            ("postgresql://user:pass@host:5432/db", "postgresql+psycopg2://user:pass@host:5432/db"),
            ("postgres://user:pass@host:5432/db", "postgresql+psycopg2://user:pass@host:5432/db"),
            ("postgresql+psycopg2://user:pass@host:5432/db", "postgresql+psycopg2://user:pass@host:5432/db"),
            ("postgresql+asyncpg://user:pass@host:5432/db", "postgresql+asyncpg://user:pass@host:5432/db"),
            ("postgresql+pg8000://user:pass@host:5432/db", "postgresql+pg8000://user:pass@host:5432/db"),
        ]

        for input_url, expected_prefix in test_cases:
            monkeypatch.setenv("DATABASE_URL", input_url)
            config = validate_runtime_config(raise_error=False)
            assert config["valid"] is True, f"Failed for {input_url}: {config['errors']}"

            resolved = resolve_database_url(input_url)
            assert resolved.startswith(expected_prefix)

    def test_database_credentials_not_exposed_in_exceptions_or_redaction(self, monkeypatch):
        """Database credentials (passwords) are strictly redacted and never leaked in exceptions."""
        from database.orm import resolve_database_url
        from services.config import redact_database_url

        secret_url = "postgresql://pramaan_user:SuperSecretPassword123!@db-host:5432/foodscanner_db"
        redacted = redact_database_url(secret_url)
        assert "SuperSecretPassword123!" not in redacted
        assert "pramaan_user:***@db-host:5432/foodscanner_db" in redacted

        # In production rejection, ensure credentials are not in exception text
        monkeypatch.setenv("ENVIRONMENT", "production")
        bad_with_cred = "mysql://admin_user:TopSecretPassword999@localhost:3306/db"
        with pytest.raises(RuntimeError) as exc_info:
            resolve_database_url(bad_with_cred)
        assert "TopSecretPassword999" not in str(exc_info.value)

    def test_sqlite_compatibility_in_development_and_test(self, monkeypatch):
        """In development and test environments, SQLite is fully accepted and resolved."""
        from database.orm import resolve_database_url

        for env_name in ["development", "test"]:
            monkeypatch.setenv("ENVIRONMENT", env_name)
            # Default fallback when unset
            monkeypatch.delenv("DATABASE_URL", raising=False)
            resolved_default = resolve_database_url()
            assert "sqlite:///" in resolved_default

            # Explicit SQLite path
            resolved_explicit = resolve_database_url("sqlite:///database/test_dev.db")
            assert "sqlite:///" in resolved_explicit
            assert "test_dev.db" in resolved_explicit


class TestErrorSanitizationAndObservability:
    def test_scan_error_sanitization_no_leak(self, client, auth_headers):
        """Unhandled exceptions in lookup_product return sanitized 500 without leaking stack or raw errors."""
        with patch("api.main.lookup_product", side_effect=ValueError("CRITICAL_INTERNAL_DB_SECRET_TABLE_ERROR")):
            res = client.post(
                "/scan",
                json={"barcode": "8901030383748"},
                headers=auth_headers,
            )
            assert res.status_code == 500
            detail = res.json().get("detail", "")
            # Ensure internal secret text was sanitized and not exposed
            assert "CRITICAL_INTERNAL_DB_SECRET_TABLE_ERROR" not in detail
            assert "Failed to process product scan" in detail

    def test_grafana_dashboard_validity_and_database_panel(self):
        """Grafana overview dashboard is valid JSON and contains the database_connected query."""
        dashboard_path = BASE_DIR.parent / "monitoring" / "grafana" / "dashboards" / "pramaan_overview.json"
        assert dashboard_path.exists()
        with open(dashboard_path, "r", encoding="utf-8") as f:
            dashboard = json.load(f)

        assert "panels" in dashboard
        # Find panel querying database_connected
        db_panels = [
            p for p in dashboard["panels"]
            if any("database_connected" in str(t.get("expr", "")) for t in p.get("targets", []))
        ]
        assert len(db_panels) >= 1
        assert db_panels[0].get("title") == "Database Status"

    def test_dockerfile_healthcheck_configuration(self):
        """Dockerfile specifies non-blocking HEALTHCHECK with dynamic PORT and start grace period."""
        dockerfile_path = BASE_DIR / "Dockerfile"
        assert dockerfile_path.exists()
        content = dockerfile_path.read_text(encoding="utf-8")
        assert "HEALTHCHECK" in content
        assert "--start-period=15s" in content
        assert "urllib.request" in content
        assert "timeout=4" in content
        assert "PORT" in content

    def test_dockerignore_configuration(self):
        """Root and backend .dockerignore exclude secrets, caches, and databases without excluding source code."""
        root_dockerignore = BASE_DIR.parent / ".dockerignore"
        assert root_dockerignore.exists()
        content = root_dockerignore.read_text(encoding="utf-8")
        assert ".env" in content
        assert "*.db" in content
        assert ".venv/" in content
        assert "node_modules/" in content
        assert ".pytest_cache/" in content
        # Ensure source directories are NOT excluded
        assert "api/" not in content
        assert "services/" not in content

    @pytest.mark.anyio
    async def test_startup_handles_database_failure_gracefully(self):
        """When database is offline during startup, lifespan sets database_connected to 0.0 without crashing."""
        from api.main import lifespan
        with patch("api.main.orm_init_db", side_effect=RuntimeError("Simulated DB offline")):
            async with lifespan(app):
                # Process successfully started and set database_connected to 0.0
                assert METRICS_REGISTRY.get_sample_value("database_connected") == 0.0
        # Reset back to True
        record_database_status(True)


class TestProxyTrustAndHeaderSpoofingProtection:
    def test_direct_client_spoofed_headers_ignored_by_default(self, monkeypatch):
        """Untrusted direct clients cannot spoof identity via X-Forwarded-For or X-Real-IP under default settings."""
        from services.rate_limiter import extract_client_ip

        monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
        monkeypatch.delenv("PROXY_PROVIDER", raising=False)
        monkeypatch.delenv("RENDER", raising=False)

        mock_req = MagicMock()
        mock_req.headers = {
            "x-forwarded-for": "198.51.100.99, 10.0.0.1",
            "x-real-ip": "198.51.100.99",
            "cf-connecting-ip": "198.51.100.99",
        }
        mock_req.client.host = "203.0.113.42"

        # Under default secure settings, all forwarded headers must be ignored
        assert extract_client_ip(mock_req) == "203.0.113.42"

    def test_untrusted_direct_peer_cannot_spoof_even_in_cidr_mode(self, monkeypatch):
        """In CIDR mode, an untrusted direct peer cannot spoof headers to pretend to be another client."""
        from services.rate_limiter import extract_client_ip

        monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
        monkeypatch.setenv("TRUSTED_PROXIES", "172.20.0.0/16, 10.0.0.0/8")

        mock_req = MagicMock()
        mock_req.headers = {"x-forwarded-for": "198.51.100.1"}
        mock_req.client.host = "203.0.113.88"  # Untrusted public IP

        # Must reject forwarded header and use direct host
        assert extract_client_ip(mock_req) == "203.0.113.88"

    def test_trusted_gateway_peer_unwraps_client_ip_in_cidr_mode(self, monkeypatch):
        """In CIDR mode, a trusted proxy peer correctly unwrap the genuine client IP."""
        from services.rate_limiter import extract_client_ip

        monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
        monkeypatch.setenv("TRUSTED_PROXIES", "172.20.0.0/16")

        mock_req = MagicMock()
        mock_req.headers = {"x-forwarded-for": "198.51.100.77, 172.20.0.2"}
        mock_req.client.host = "172.20.0.2"  # Trusted gateway container IP

        assert extract_client_ip(mock_req) == "198.51.100.77"

    def test_render_mode_prefers_cf_connecting_ip_over_x_forwarded_for(self, monkeypatch):
        """In Render mode, CF-Connecting-IP is verified and prioritized over spoofed X-Forwarded-For."""
        from services.rate_limiter import extract_client_ip

        monkeypatch.setenv("PROXY_PROVIDER", "render")
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)

        mock_req = MagicMock()
        mock_req.headers = {
            "cf-connecting-ip": "198.51.100.20",
            "x-forwarded-for": "10.0.0.1, 127.0.0.1",
        }
        mock_req.client.host = "10.10.10.10"

        assert extract_client_ip(mock_req) == "198.51.100.20"
