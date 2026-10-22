"""Unit tests for the API Gateway Lambda handler using Moto mock DynamoDB."""

import json
from unittest.mock import MagicMock
import boto3
from moto import mock_aws
import pytest

from src.handlers import api_handler
from src.handlers.api_handler import (
    decode_pagination_token,
    encode_pagination_token,
    lambda_handler,
    set_repository,
)
from src.common.models import DeviceStatus, TelemetryReading
from src.repository.telemetry_repo import TelemetryRepository

TABLE_NAME = "telemetry_records"


@pytest.fixture
def mock_api_environment():
    """Setup mock DynamoDB table and wire to API handler."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "device_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "device_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        repo = TelemetryRepository(table_name=TABLE_NAME, dynamodb_resource=dynamodb)
        set_repository(repo)
        yield repo, table


def test_cors_preflight_options():
    """Verify OPTIONS request returns 200 with standard CORS headers."""
    event = {
        "httpMethod": "OPTIONS",
        "path": "/devices/esp32-01/history",
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 200
    headers = response["headers"]
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "GET,OPTIONS" in headers["Access-Control-Allow-Methods"]


def test_health_check_success(mock_api_environment):
    """Requirement 1: Test GET /health with active database returns 200."""
    event = {
        "httpMethod": "GET",
        "path": "/health",
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["success"] is True
    assert body["data"]["status"] == "HEALTHY"
    assert body["data"]["database"]["status"] == "HEALTHY"
    assert "timestamp" in body["data"]


def test_health_check_database_failure():
    """Requirement 1: Test GET /health returns 503 when database connectivity fails."""
    mock_repo = MagicMock()
    mock_repo.check_health.side_effect = Exception("Connection timed out")
    set_repository(mock_repo)

    event = {
        "httpMethod": "GET",
        "path": "/health",
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 503
    body = json.loads(response["body"])
    assert body["success"] is False
    assert "Database connectivity failure" in body["error"]["message"]
    assert body["error"]["details"]["database_status"] == "UNREACHABLE"


def test_get_latest_reading_success(mock_api_environment):
    """Requirement 2: Test GET /devices/{device_id}/latest returns most recent record."""
    repo, table = mock_api_environment
    device_id = "esp32-latest-test"

    # Insert two readings
    repo.save_reading(
        TelemetryReading(
            device_id=device_id,
            timestamp="2026-10-22T10:00:00.000Z",
            temperature=20.0,
            humidity=50.0,
            message_id="msg-1",
        )
    )
    repo.save_reading(
        TelemetryReading(
            device_id=device_id,
            timestamp="2026-10-22T12:00:00.000Z",
            temperature=25.5,
            humidity=45.0,
            message_id="msg-2",
        )
    )

    event = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/latest",
        "pathParameters": {"device_id": device_id},
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["success"] is True
    assert body["data"]["device_id"] == device_id
    assert body["data"]["temperature"] == 25.5
    assert body["data"]["message_id"] == "msg-2"


def test_get_latest_reading_not_found(mock_api_environment):
    """Requirement 2: Test GET /devices/{device_id}/latest returns 404 if device has no records."""
    event = {
        "httpMethod": "GET",
        "path": "/devices/esp32-unknown/latest",
        "pathParameters": {"device_id": "esp32-unknown"},
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["success"] is False
    assert "No telemetry records found" in body["error"]["message"]


def test_get_latest_reading_invalid_device_id(mock_api_environment):
    """Test GET /devices/{device_id}/latest returns 400 for invalid device_id syntax."""
    event = {
        "httpMethod": "GET",
        "path": "/devices/bad!id/latest",
        "pathParameters": {"device_id": "bad!id"},
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert "Invalid device_id" in body["error"]["message"]


def test_get_history_time_range_success(mock_api_environment):
    """Requirement 3: Test GET /devices/{device_id}/history with start and end filters."""
    repo, table = mock_api_environment
    device_id = "esp32-history-test"

    for hour in [8, 10, 12, 14]:
        repo.save_reading(
            TelemetryReading(
                device_id=device_id,
                timestamp=f"2026-10-22T{hour:02d}:00:00.000Z",
                temperature=20.0 + hour,
                humidity=50.0,
                message_id=f"msg-{hour}",
            )
        )

    # Query between 09:00 and 13:00 (should return 10:00 and 12:00)
    event = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/history",
        "pathParameters": {"device_id": device_id},
        "queryStringParameters": {
            "start": "2026-10-22T09:00:00.000Z",
            "end": "2026-10-22T13:00:00.000Z",
        },
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["success"] is True
    assert len(body["data"]) == 2
    assert body["meta"]["count"] == 2
    timestamps = [item["timestamp"] for item in body["data"]]
    assert timestamps == ["2026-10-22T10:00:00.000Z", "2026-10-22T12:00:00.000Z"]


def test_get_history_start_after_end_rejected(mock_api_environment):
    """Requirement 3: Test that start time after end time returns 400."""
    event = {
        "httpMethod": "GET",
        "path": "/devices/esp32-test/history",
        "pathParameters": {"device_id": "esp32-test"},
        "queryStringParameters": {
            "start": "2026-10-22T15:00:00.000Z",
            "end": "2026-10-22T10:00:00.000Z",
        },
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert "cannot be after" in body["error"]["message"]


def test_get_history_invalid_limit(mock_api_environment):
    """Requirement 3: Test invalid limits (non-integer, <1, >100) return 400."""
    invalid_limits = ["0", "101", "-5", "not-a-number"]
    for lim in invalid_limits:
        event = {
            "httpMethod": "GET",
            "path": "/devices/esp32-test/history",
            "pathParameters": {"device_id": "esp32-test"},
            "queryStringParameters": {"limit": lim},
        }
        response = lambda_handler(event)
        assert response["statusCode"] == 400, f"Expected 400 for limit='{lim}'"
        body = json.loads(response["body"])
        assert "Invalid 'limit'" in body["error"]["message"]


def test_get_history_pagination_tokens(mock_api_environment):
    """Requirement 3 & 7: Test pagination flow and opaque tokens across pages."""
    repo, table = mock_api_environment
    device_id = "esp32-pagination-test"

    for i in range(5):
        repo.save_reading(
            TelemetryReading(
                device_id=device_id,
                timestamp=f"2026-10-22T10:{i:02d}:00.000Z",
                temperature=20.0 + i,
                humidity=50.0,
                message_id=f"msg-{i}",
            )
        )

    # Page 1: limit 2
    event_p1 = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/history",
        "pathParameters": {"device_id": device_id},
        "queryStringParameters": {"limit": "2"},
    }
    resp1 = lambda_handler(event_p1)
    assert resp1["statusCode"] == 200
    body1 = json.loads(resp1["body"])
    assert len(body1["data"]) == 2
    token1 = body1["meta"]["next_token"]
    assert token1 is not None

    # Page 2: pass token1
    event_p2 = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/history",
        "pathParameters": {"device_id": device_id},
        "queryStringParameters": {"limit": "2", "next_token": token1},
    }
    resp2 = lambda_handler(event_p2)
    assert resp2["statusCode"] == 200
    body2 = json.loads(resp2["body"])
    assert len(body2["data"]) == 2
    token2 = body2["meta"]["next_token"]
    assert token2 is not None

    # Page 3: pass token2 (final item)
    event_p3 = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/history",
        "pathParameters": {"device_id": device_id},
        "queryStringParameters": {"limit": "2", "next_token": token2},
    }
    resp3 = lambda_handler(event_p3)
    assert resp3["statusCode"] == 200
    body3 = json.loads(resp3["body"])
    assert len(body3["data"]) == 1
    assert body3["meta"]["next_token"] is None


def test_pagination_token_security_and_validation():
    """Requirement 7: Test tampered, malformed, and cross-device tokens are rejected."""
    # Malformed base64
    event_bad_b64 = {
        "httpMethod": "GET",
        "path": "/devices/esp32-01/history",
        "pathParameters": {"device_id": "esp32-01"},
        "queryStringParameters": {"next_token": "!!!not-valid-base64!!!"},
    }
    resp = lambda_handler(event_bad_b64)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert "Invalid pagination token" in body["error"]["message"]

    # Cross-device token substitution attempt
    foreign_token = encode_pagination_token({"device_id": "victim-device", "timestamp": "2026-10-22T10:00:00.000Z"})
    event_substitution = {
        "httpMethod": "GET",
        "path": "/devices/attacker-device/history",
        "pathParameters": {"device_id": "attacker-device"},
        "queryStringParameters": {"next_token": foreign_token},
    }
    resp = lambda_handler(event_substitution)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert "token does not match requested device" in body["error"]["message"]


def test_unknown_route():
    """Test unmatched path returns 404."""
    event = {
        "httpMethod": "GET",
        "path": "/unknown/route/path",
    }
    response = lambda_handler(event)
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert "Route not found" in body["error"]["message"]
