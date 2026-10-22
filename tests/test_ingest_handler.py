"""Unit tests for the Ingestion Lambda handler."""

import json
from unittest.mock import MagicMock
import boto3
from moto import mock_aws
import pytest

from src.handlers import ingest_handler
from src.handlers.ingest_handler import lambda_handler, set_repository
from src.repository.telemetry_repo import TelemetryRepository

TABLE_NAME = "telemetry_records"


@pytest.fixture
def mock_dynamodb():
    """Create in-memory DynamoDB table and wire to ingest_handler repository."""
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


def test_ingest_handler_success_dict(mock_dynamodb, sample_payload_dict):
    """Test successful ingestion of a dictionary payload."""
    repo, table = mock_dynamodb
    response = lambda_handler(sample_payload_dict)

    assert response["statusCode"] == 200
    assert response["body"]["device_id"] == "esp32-sensor-01"
    assert response["body"]["message"] == "Telemetry successfully ingested"

    # Verify item is in DynamoDB
    saved = repo.get_latest_reading("esp32-sensor-01")
    assert saved is not None
    assert saved.temperature == sample_payload_dict["temperature"]


def test_ingest_handler_success_json_string(mock_dynamodb, sample_payload_dict):
    """Test successful ingestion of a serialized JSON string."""
    response = lambda_handler(json.dumps(sample_payload_dict))

    assert response["statusCode"] == 200
    assert response["body"]["device_id"] == "esp32-sensor-01"


def test_ingest_handler_anomaly_payload(mock_dynamodb, anomaly_payload_dict):
    """Test ingestion of high-temperature anomaly payload."""
    repo, table = mock_dynamodb
    response = lambda_handler(anomaly_payload_dict)

    assert response["statusCode"] == 200
    assert response["body"]["status"] == "WARNING"

    saved = repo.get_latest_reading(anomaly_payload_dict["device_id"])
    assert saved is not None
    assert saved.temperature > 50.0
    assert saved.status.value == "WARNING"


def test_ingest_handler_malformed_json_string(mock_dynamodb):
    """Test handling of unparseable JSON string."""
    response = lambda_handler("{invalid_json: true,")

    assert response["statusCode"] == 400
    assert "Malformed JSON" in response["error"]


def test_ingest_handler_invalid_payload_type(mock_dynamodb):
    """Test handling of unsupported event types (e.g., list or integer)."""
    response = lambda_handler([1, 2, 3])

    assert response["statusCode"] == 400
    assert "Unsupported event payload type" in response["error"]


def test_ingest_handler_validation_failure(mock_dynamodb, malformed_payload_dict):
    """Test that schema validation failures return 422 and do not crash."""
    response = lambda_handler(malformed_payload_dict)

    assert response["statusCode"] == 422
    assert response["error"] == "Validation failed"
    assert "details" in response


def test_ingest_handler_database_error():
    """Test that database persistence errors return 500 without unhandled exception."""
    mock_repo = MagicMock()
    mock_repo.save_reading.side_effect = Exception("Simulated DynamoDB connection failure")
    set_repository(mock_repo)

    valid_payload = {
        "device_id": "esp32-test",
        "temperature": 25.0,
        "humidity": 50.0,
    }

    response = lambda_handler(valid_payload)
    assert response["statusCode"] == 500
    assert response["error"] == "Failed to persist record to database"
