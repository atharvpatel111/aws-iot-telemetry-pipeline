"""Pytest configuration and shared test fixtures."""

import json
import os
from pathlib import Path
from typing import Any, Dict
import pytest

# Ensure mocked AWS credentials in the environment so no real AWS calls can succeed
os.environ["AWS_ACCESS_KEY_ID"] = "testing-mock-key"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing-mock-secret"
os.environ["AWS_SECURITY_TOKEN"] = "testing-mock-token"
os.environ["AWS_SESSION_TOKEN"] = "testing-mock-session"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def fixtures_dir() -> Path:
    """Return path to simulator/fixtures directory."""
    return Path(__file__).resolve().parent.parent / "simulator" / "fixtures"


@pytest.fixture
def sample_payload_dict(fixtures_dir: Path) -> Dict[str, Any]:
    """Load valid sample payload fixture."""
    with open(fixtures_dir / "sample_payload.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def anomaly_payload_dict(fixtures_dir: Path) -> Dict[str, Any]:
    """Load anomaly payload fixture."""
    with open(fixtures_dir / "anomaly_payload.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def malformed_payload_dict(fixtures_dir: Path) -> Dict[str, Any]:
    """Load malformed payload fixture."""
    with open(fixtures_dir / "malformed_payload.json", "r", encoding="utf-8") as f:
        return json.load(f)
