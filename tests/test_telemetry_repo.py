"""Unit tests for the DynamoDB TelemetryRepository using Moto mock."""

import boto3
from moto import mock_aws
import pytest

from src.common.models import DeviceStatus, TelemetryReading
from src.repository.telemetry_repo import TelemetryRepository

TABLE_NAME = "telemetry_records"


@pytest.fixture
def mock_dynamodb_table():
    """Create an in-memory mocked DynamoDB table for testing."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "device_id", "KeyType": "HASH"},  # Partition Key
                {"AttributeName": "timestamp", "KeyType": "RANGE"},  # Sort Key
            ],
            AttributeDefinitions=[
                {"AttributeName": "device_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield dynamodb, table


def test_save_and_retrieve_latest_reading(mock_dynamodb_table):
    """Test saving a reading and retrieving the single latest reading."""
    dynamodb, table = mock_dynamodb_table
    repo = TelemetryRepository(table_name=TABLE_NAME, dynamodb_resource=dynamodb)

    reading = TelemetryReading(
        device_id="esp32-sensor-01",
        timestamp="2026-10-22T21:30:00.000Z",
        temperature=23.5,
        humidity=48.0,
        battery_voltage=3.95,
        status=DeviceStatus.OK,
        message_id="msg-001",
    )

    repo.save_reading(reading)

    latest = repo.get_latest_reading("esp32-sensor-01")
    assert latest is not None
    assert latest.device_id == "esp32-sensor-01"
    assert latest.temperature == 23.5
    assert latest.humidity == 48.0
    assert latest.message_id == "msg-001"


def test_get_latest_reading_nonexistent_device(mock_dynamodb_table):
    """Test that querying a device with no records returns None."""
    dynamodb, table = mock_dynamodb_table
    repo = TelemetryRepository(table_name=TABLE_NAME, dynamodb_resource=dynamodb)

    result = repo.get_latest_reading("nonexistent-device")
    assert result is None


def test_get_latest_reading_returns_most_recent(mock_dynamodb_table):
    """Verify that get_latest_reading returns the most recent record even with multiple entries."""
    dynamodb, table = mock_dynamodb_table
    repo = TelemetryRepository(table_name=TABLE_NAME, dynamodb_resource=dynamodb)

    device_id = "esp32-chronological-test"
    readings = [
        TelemetryReading(
            device_id=device_id,
            timestamp="2026-10-22T10:00:00.000Z",
            temperature=18.0,
            humidity=60.0,
            message_id="msg-1",
        ),
        TelemetryReading(
            device_id=device_id,
            timestamp="2026-10-22T12:00:00.000Z",
            temperature=22.0,
            humidity=50.0,
            message_id="msg-2",
        ),
        TelemetryReading(
            device_id=device_id,
            timestamp="2026-10-22T14:00:00.000Z",
            temperature=26.5,
            humidity=40.0,
            message_id="msg-3",
        ),
    ]

    for r in readings:
        repo.save_reading(r)

    latest = repo.get_latest_reading(device_id)
    assert latest is not None
    assert latest.timestamp == "2026-10-22T14:00:00.000Z"
    assert latest.temperature == 26.5
    assert latest.message_id == "msg-3"


def test_duplicate_timestamps_are_safely_preserved(mock_dynamodb_table):
    """Verify that duplicate timestamps within the same millisecond do NOT overwrite each other."""
    dynamodb, table = mock_dynamodb_table
    repo = TelemetryRepository(table_name=TABLE_NAME, dynamodb_resource=dynamodb)

    device_id = "esp32-collision-test"
    exact_same_timestamp = "2026-10-22T21:30:00.123Z"

    reading_1 = TelemetryReading(
        device_id=device_id,
        timestamp=exact_same_timestamp,
        temperature=22.0,
        humidity=50.0,
        message_id="uuid-first-reading",
    )
    reading_2 = TelemetryReading(
        device_id=device_id,
        timestamp=exact_same_timestamp,
        temperature=22.4,
        humidity=49.0,
        message_id="uuid-second-reading",
    )

    repo.save_reading(reading_1)
    repo.save_reading(reading_2)

    # Fetch history for this device
    history, _ = repo.get_history(device_id, limit=10)
    assert len(history) == 2, "Both records must be preserved without overwriting"
    message_ids = {h.message_id for h in history}
    assert message_ids == {"uuid-first-reading", "uuid-second-reading"}


def test_get_history_time_range_filtering(mock_dynamodb_table):
    """Test time range filtering with start_time and end_time."""
    dynamodb, table = mock_dynamodb_table
    repo = TelemetryRepository(table_name=TABLE_NAME, dynamodb_resource=dynamodb)

    device_id = "esp32-range-test"
    hours = ["08", "09", "10", "11", "12", "13", "14"]
    for h in hours:
        repo.save_reading(
            TelemetryReading(
                device_id=device_id,
                timestamp=f"2026-10-22T{h}:00:00.000Z",
                temperature=20.0 + int(h),
                humidity=50.0,
                message_id=f"msg-{h}",
            )
        )

    # Query range between 09:30 and 12:30 (should include 10:00, 11:00, 12:00)
    history, _ = repo.get_history(
        device_id=device_id,
        start_time="2026-10-22T09:30:00.000Z",
        end_time="2026-10-22T12:30:00.000Z",
    )

    assert len(history) == 3
    timestamps = [h.timestamp for h in history]
    assert timestamps == [
        "2026-10-22T10:00:00.000Z",
        "2026-10-22T11:00:00.000Z",
        "2026-10-22T12:00:00.000Z",
    ]


def test_get_history_pagination(mock_dynamodb_table):
    """Test pagination using limit and exclusive_start_key."""
    dynamodb, table = mock_dynamodb_table
    repo = TelemetryRepository(table_name=TABLE_NAME, dynamodb_resource=dynamodb)

    device_id = "esp32-page-test"
    for i in range(10):
        repo.save_reading(
            TelemetryReading(
                device_id=device_id,
                timestamp=f"2026-10-22T10:{i:02d}:00.000Z",
                temperature=20.0,
                humidity=50.0,
                message_id=f"msg-{i}",
            )
        )

    # Page 1: limit 4
    page_1, last_key = repo.get_history(device_id=device_id, limit=4)
    assert len(page_1) == 4
    assert last_key is not None

    # Page 2: next 4 using token
    page_2, last_key_2 = repo.get_history(
        device_id=device_id, limit=4, exclusive_start_key=last_key
    )
    assert len(page_2) == 4
    assert last_key_2 is not None

    # Verify no item overlap between pages
    page_1_ids = {p.message_id for p in page_1}
    page_2_ids = {p.message_id for p in page_2}
    assert page_1_ids.isdisjoint(page_2_ids)
