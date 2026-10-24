import os
from datetime import datetime, timezone
from decimal import Decimal

os.environ["DATABASE_URL"] = "postgresql+psycopg://acme:acme@localhost:5432/procurement_test"
os.environ["EAGER_WORKER"] = "true"
os.environ["WORKER_THREAD"] = "false"
os.environ["SEED_ON_START"] = "false"
os.environ["LLM_MODE"] = "deterministic"
os.environ["LLM_API_KEY"] = ""
os.environ["PHOTON_MODE"] = "mock"
os.environ["EMAIL_MODE"] = "sandbox"
os.environ["APP_ENV"] = "development"
os.environ["DEMO_MODE"] = "true"
os.environ["DEMO_CLOCK"] = "2026-10-24T15:00:00+00:00"
os.environ["PHOTON_WEBHOOK_SECRET"] = "dev-webhook-secret"
os.environ["EMAIL_WEBHOOK_SECRET"] = "dev-email-secret"

import pytest
from fastapi.testclient import TestClient

from app.adapters.photon import delivery_hook
from app.bootstrap import seed
from app.clock import clock
from app.main import app
from app.migrate import apply
from app.settings import settings


@pytest.fixture(scope="session", autouse=True)
def _migrated():
    apply()


@pytest.fixture(autouse=True)
def _fresh_db():
    clock.freeze(datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc))
    settings.app_env = "development"
    settings.demo_mode = True
    settings.eager_worker = True
    settings.worker_thread = False
    settings.photon_mode = "mock"
    settings.email_mode = "sandbox"
    settings.llm_mode = "deterministic"
    settings.llm_api_key = ""
    settings.manager_approval_limit = Decimal("200000.00")
    delivery_hook["next"] = None
    delivery_hook["email"] = None
    seed(reset=True, include_demo_task=False)
    yield
    clock.unfreeze()
    settings.app_env = "development"
    settings.demo_mode = True
    settings.eager_worker = True
    settings.manager_approval_limit = Decimal("200000.00")
    delivery_hook["next"] = None
    delivery_hook["email"] = None


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client
