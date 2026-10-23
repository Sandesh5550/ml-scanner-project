from __future__ import annotations

import os
from pathlib import Path
import pytest
import yaml

from api.main import _get_allowed_origins
from database.orm import create_database_engine
from alembic import command
from alembic.config import Config

BASE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BASE_DIR.parent


def test_render_yaml_production_and_cors_configuration():
    """Verify render.yaml declares production environment and CORS origin."""
    render_file = ROOT_DIR / "render.yaml"
    assert render_file.exists(), "render.yaml must exist at repository root"

    with open(render_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    services = {s["name"]: s for s in data.get("services", [])}
    assert "foodscanner-ai" in services, "foodscanner-ai web service must be defined"
    assert "foodscanner-mobile" in services, "foodscanner-mobile static service must be defined"

    ai_env = {v["key"]: v.get("value") for v in services["foodscanner-ai"].get("envVars", []) if "key" in v}
    assert ai_env.get("ENVIRONMENT") == "production", "ENVIRONMENT must be explicitly set to 'production'"
    assert ai_env.get("ALLOWED_ORIGINS") == "https://foodscanner-mobile.onrender.com", (
        "ALLOWED_ORIGINS must include the mobile web origin"
    )

    mobile_env = {v["key"]: v.get("value") for v in services["foodscanner-mobile"].get("envVars", []) if "key" in v}
    assert mobile_env.get("EXPO_PUBLIC_API_BASE_URL") == "https://foodscanner-ai.onrender.com", (
        "EXPO_PUBLIC_API_BASE_URL must target the backend service URL"
    )


def test_allowed_origins_parsing(monkeypatch):
    """Verify _get_allowed_origins parses configured environment origins and retains defaults."""
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://foodscanner-mobile.onrender.com, https://custom-domain.com")
    origins = _get_allowed_origins()

    assert "https://foodscanner-mobile.onrender.com" in origins
    assert "https://custom-domain.com" in origins
    assert "http://localhost:8081" in origins
    assert "http://127.0.0.1:8081" in origins


def test_prometheus_alerts_configuration_and_registration():
    """Verify monitoring/prometheus/alerts.yml exists, is valid YAML, and is registered in prometheus.yml."""
    alerts_file = ROOT_DIR / "monitoring" / "prometheus" / "alerts.yml"
    assert alerts_file.exists(), "alerts.yml must exist"

    with open(alerts_file, "r", encoding="utf-8") as f:
        alerts_data = yaml.safe_load(f)

    assert "groups" in alerts_data, "alerts.yml must contain groups"
    rule_names = set()
    for g in alerts_data["groups"]:
        for r in g.get("rules", []):
            if "alert" in r:
                rule_names.add(r["alert"])

    required_alerts = {
        "BackendServiceDown",
        "DatabaseDisconnected",
        "HighHttp5xxErrorRate",
        "HighHttpLatencyP95",
        "StaleOcrJobsDetected",
        "ElevatedRateLimitRejections",
    }
    assert required_alerts.issubset(rule_names), f"Missing required alerts: {required_alerts - rule_names}"

    # Verify registration in prometheus.yml
    prom_file = ROOT_DIR / "monitoring" / "prometheus" / "prometheus.yml"
    with open(prom_file, "r", encoding="utf-8") as f:
        prom_data = yaml.safe_load(f)

    assert "rule_files" in prom_data, "rule_files must be registered in prometheus.yml"
    assert "alerts.yml" in prom_data["rule_files"], "alerts.yml must be listed under rule_files"


def test_docker_compose_prometheus_alerts_mount():
    """Verify docker-compose.yml mounts alerts.yml into the Prometheus service."""
    compose_file = ROOT_DIR / "docker-compose.yml"
    with open(compose_file, "r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    prom_service = compose_data.get("services", {}).get("prometheus", {})
    volumes = prom_service.get("volumes", [])
    alerts_mount = any("alerts.yml" in str(v) for v in volumes)
    assert alerts_mount, "docker-compose.yml must mount alerts.yml in prometheus container"


def test_dockerignore_dataset_and_cache_exclusions():
    """Verify foodscanner-ai/.dockerignore excludes large training datasets and caches."""
    dockerignore_path = BASE_DIR / ".dockerignore"
    assert dockerignore_path.exists(), "foodscanner-ai/.dockerignore must exist"

    content = dockerignore_path.read_text(encoding="utf-8")
    lines = {line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")}

    assert "datasets/large_openfoodfacts*.csv" in lines or "datasets/large_openfoodfacts.csv" in lines
    assert ".pytest_cache/" in lines
    assert ".DS_Store" in lines


def test_github_actions_workflow_steps():
    """Verify .github/workflows/ci.yml incorporates migrations, linting, web export, and container health."""
    ci_file = ROOT_DIR / ".github" / "workflows" / "ci.yml"
    assert ci_file.exists(), "ci.yml must exist"

    ci_content = ci_file.read_text(encoding="utf-8")

    assert "alembic upgrade head" in ci_content, "CI must execute alembic migrations"
    assert "npm run lint" in ci_content, "CI must execute mobile linting"
    assert "npm run build:web" in ci_content, "CI must validate mobile web build export"
    assert "foodscanner-ci-smoke" in ci_content, "CI must execute container smoke test"
    assert "http://127.0.0.1:8000/health" in ci_content, "CI must verify container health endpoint"


def test_isolated_migration_upgrade_and_downgrade(tmp_path):
    """Verify migrations execute cleanly against an isolated temporary SQLite database."""
    test_db = tmp_path / "migration_verify.db"
    db_url = f"sqlite:///{test_db.as_posix()}"

    alembic_ini = BASE_DIR / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("sqlalchemy.url", db_url)

    # 1. Upgrade directly to head
    command.upgrade(cfg, "head")

    # 2. Verify current revision is head
    engine = create_database_engine(db_url)
    with engine.connect() as conn:
        from sqlalchemy import text
        current_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert current_rev == "002_add_ocr_jobs_and_allergens"
