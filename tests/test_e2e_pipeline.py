"""End-to-End local pipeline integration tests.

Exercises the entire serverless data path completely offline:
Sensor Simulator -> Ingestion Lambda -> Mocked DynamoDB -> API Gateway Handler -> Dashboard Payload.
"""

from datetime import datetime, timedelta, timezone
import json
import boto3
from moto import mock_aws
import pytest

from simulator.sensor_simulator import SensorSimulator
from src.handlers import api_handler, ingest_handler
from src.repository.telemetry_repo import TelemetryRepository

TABLE_NAME = "telemetry_records"


@pytest.fixture
def e2e_pipeline_env():
    """Setup in-memory DynamoDB table and link both Ingestion and API handlers to it."""
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

        # Shared repository instance between both Lambda handlers
        repo = TelemetryRepository(table_name=TABLE_NAME, dynamodb_resource=dynamodb)
        ingest_handler.set_repository(repo)
        api_handler.set_repository(repo)

        yield repo, table


def test_e2e_normal_telemetry_flow(e2e_pipeline_env):
    """Test full flow: simulator reading -> Ingest Lambda -> DynamoDB -> API /latest -> output."""
    repo, table = e2e_pipeline_env
    device_id = "esp32-e2e-normal"
    simulator = SensorSimulator(device_id=device_id, seed=101)

    # 1. Simulator emits reading
    reading = simulator.generate_reading()
    payload_dict = reading.model_dump()

    # 2. Ingestion Lambda processes IoT event
    ingest_resp = ingest_handler.lambda_handler(payload_dict)
    assert ingest_resp["statusCode"] == 200
    assert ingest_resp["body"]["device_id"] == device_id
    assert ingest_resp["body"]["message"] == "Telemetry successfully ingested"

    # 3. API Gateway Handler serves GET /devices/{device_id}/latest
    api_event = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/latest",
        "pathParameters": {"device_id": device_id},
    }
    api_resp = api_handler.lambda_handler(api_event)
    assert api_resp["statusCode"] == 200

    api_body = json.loads(api_resp["body"])
    assert api_body["success"] is True
    data = api_body["data"]
    assert data["device_id"] == device_id
    assert data["temperature"] == reading.temperature
    assert data["humidity"] == reading.humidity
    assert data["status"] == "OK"
    assert "timestamp" in data


def test_e2e_high_temperature_anomaly_flow(e2e_pipeline_env):
    """Test anomaly flow: high-temp anomaly -> Ingest Lambda -> status WARNING -> API returns WARNING."""
    repo, table = e2e_pipeline_env
    device_id = "esp32-e2e-anomaly"
    simulator = SensorSimulator(device_id=device_id, seed=202)

    # 1. Force anomaly (>50°C)
    reading = simulator.generate_reading(force_anomaly=True)
    assert reading.temperature >= 50.0

    # 2. Ingestion Lambda processes event
    ingest_resp = ingest_handler.lambda_handler(reading.model_dump())
    assert ingest_resp["statusCode"] == 200
    assert ingest_resp["body"]["status"] == "WARNING"

    # 3. API returns WARNING status
    api_event = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/latest",
        "pathParameters": {"device_id": device_id},
    }
    api_resp = api_handler.lambda_handler(api_event)
    assert api_resp["statusCode"] == 200
    data = json.loads(api_resp["body"])["data"]
    assert data["status"] == "WARNING"
    assert data["temperature"] >= 50.0

    # 4. Metric filter evaluation logic check
    # Note: In real AWS, CloudWatch evaluates { $.temperature > 50 } against Ingestion logs.
    assert reading.temperature > 50.0, "Metric filter condition verified for anomaly"


def test_e2e_malformed_payload_rejected_cleanly(e2e_pipeline_env):
    """Test that malformed payloads are rejected by Ingestion Lambda and do not pollute the database."""
    repo, table = e2e_pipeline_env
    bad_payload = {
        "device_id": "esp32-e2e-bad",
        "temperature": 999.0,  # Physically impossible for sensor
        "humidity": -20.0,     # Negative humidity
    }

    # 1. Ingestion rejects with 422
    ingest_resp = ingest_handler.lambda_handler(bad_payload)
    assert ingest_resp["statusCode"] == 422
    assert ingest_resp["error"] == "Validation failed"

    # 2. Database remains clean; API returns 404
    api_event = {
        "httpMethod": "GET",
        "path": "/devices/esp32-e2e-bad/latest",
        "pathParameters": {"device_id": "esp32-e2e-bad"},
    }
    api_resp = api_handler.lambda_handler(api_event)
    assert api_resp["statusCode"] == 404


def test_e2e_missing_device(e2e_pipeline_env):
    """Test querying a device that was never ingested returns 404."""
    api_event = {
        "httpMethod": "GET",
        "path": "/devices/esp32-never-seen/latest",
        "pathParameters": {"device_id": "esp32-never-seen"},
    }
    api_resp = api_handler.lambda_handler(api_event)
    assert api_resp["statusCode"] == 404
    body = json.loads(api_resp["body"])
    assert "No telemetry records found" in body["error"]["message"]


def test_e2e_time_range_history_flow(e2e_pipeline_env):
    """Test streaming multiple readings and querying historical time range."""
    repo, table = e2e_pipeline_env
    device_id = "esp32-e2e-range"
    simulator = SensorSimulator(device_id=device_id, seed=303)

    base_time = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)

    # Ingest 5 hourly readings: 12:00, 13:00, 14:00, 15:00, 16:00
    for hour_offset in range(5):
        t = base_time + timedelta(hours=hour_offset)
        r = simulator.generate_reading(timestamp=t)
        ingest_handler.lambda_handler(r.model_dump())

    # Query range between 12:30 and 15:30 (should return 13:00, 14:00, 15:00)
    api_event = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/history",
        "pathParameters": {"device_id": device_id},
        "queryStringParameters": {
            "start": "2026-10-22T12:30:00.000Z",
            "end": "2026-10-22T15:30:00.000Z",
        },
    }
    api_resp = api_handler.lambda_handler(api_event)
    assert api_resp["statusCode"] == 200

    body = json.loads(api_resp["body"])
    assert body["success"] is True
    assert len(body["data"]) == 3
    timestamps = [item["timestamp"] for item in body["data"]]
    assert timestamps == [
        "2026-10-22T13:00:00.000Z",
        "2026-10-22T14:00:00.000Z",
        "2026-10-22T15:00:00.000Z",
    ]


def test_e2e_history_pagination_flow(e2e_pipeline_env):
    """Test full multi-page pagination lifecycle using opaque next_token."""
    repo, table = e2e_pipeline_env
    device_id = "esp32-e2e-pagination"
    simulator = SensorSimulator(device_id=device_id, seed=404)

    base_time = datetime(2026, 10, 22, 8, 0, 0, tzinfo=timezone.utc)

    # Ingest 6 readings
    for i in range(6):
        t = base_time + timedelta(minutes=i * 10)
        r = simulator.generate_reading(timestamp=t)
        ingest_handler.lambda_handler(r.model_dump())

    all_retrieved = []

    # Page 1 (limit 2)
    p1_event = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/history",
        "pathParameters": {"device_id": device_id},
        "queryStringParameters": {"limit": "2"},
    }
    resp1 = api_handler.lambda_handler(p1_event)
    assert resp1["statusCode"] == 200
    body1 = json.loads(resp1["body"])
    assert len(body1["data"]) == 2
    token1 = body1["meta"]["next_token"]
    assert token1 is not None
    all_retrieved.extend(body1["data"])

    # Page 2 (limit 2, with token1)
    p2_event = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/history",
        "pathParameters": {"device_id": device_id},
        "queryStringParameters": {"limit": "2", "next_token": token1},
    }
    resp2 = api_handler.lambda_handler(p2_event)
    assert resp2["statusCode"] == 200
    body2 = json.loads(resp2["body"])
    assert len(body2["data"]) == 2
    token2 = body2["meta"]["next_token"]
    assert token2 is not None
    all_retrieved.extend(body2["data"])

    # Page 3 (limit 2, with token2)
    p3_event = {
        "httpMethod": "GET",
        "path": f"/devices/{device_id}/history",
        "pathParameters": {"device_id": device_id},
        "queryStringParameters": {"limit": "2", "next_token": token2},
    }
    resp3 = api_handler.lambda_handler(p3_event)
    assert resp3["statusCode"] == 200
    body3 = json.loads(resp3["body"])
    assert len(body3["data"]) == 2
    assert body3["meta"]["next_token"] is None  # End of stream
    all_retrieved.extend(body3["data"])

    # Verify total retrieved
    assert len(all_retrieved) == 6
    # Ensure no duplicates
    msg_ids = [item["message_id"] for item in all_retrieved]
    assert len(set(msg_ids)) == 6


def test_e2e_health_check_endpoint(e2e_pipeline_env):
    """Test health check endpoint behavior with healthy and broken database."""
    repo, table = e2e_pipeline_env

    # 1. Healthy state
    health_event = {"httpMethod": "GET", "path": "/health"}
    resp = api_handler.lambda_handler(health_event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["data"]["status"] == "HEALTHY"
    assert body["data"]["database"]["status"] == "HEALTHY"

    # 2. Broken database state (simulate table deletion)
    table.delete()
    resp_down = api_handler.lambda_handler(health_event)
    assert resp_down["statusCode"] == 503
    body_down = json.loads(resp_down["body"])
    assert body_down["success"] is False
    assert "Database connectivity failure" in body_down["error"]["message"]
