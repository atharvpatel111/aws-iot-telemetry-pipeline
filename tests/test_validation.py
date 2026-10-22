"""Unit tests for telemetry data models and validation layer."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from pydantic import ValidationError

from src.common.models import (
    DeviceStatus,
    TelemetryReading,
    ensure_utc_iso,
)


def test_valid_payload_parsing(sample_payload_dict):
    """Test that valid telemetry payload parses correctly."""
    reading = TelemetryReading(**sample_payload_dict)
    assert reading.device_id == "esp32-sensor-01"
    assert reading.temperature == 23.45
    assert reading.humidity == 48.20
    assert reading.battery_voltage == 3.92
    assert reading.status == DeviceStatus.OK
    assert reading.firmware_version == "1.0.0"


def test_timestamp_utc_conversion():
    """Verify that various timestamp formats normalize to ISO-8601 UTC."""
    # 1. Standard ISO string with Z
    ts1 = ensure_utc_iso("2026-10-22T21:30:00.000Z")
    assert ts1 == "2026-10-22T21:30:00.000Z"

    # 2. Non-UTC timezone (e.g. IST +05:30) converted to UTC
    ts_ist = "2026-10-22T21:30:00.000+05:30"
    ts2 = ensure_utc_iso(ts_ist)
    assert ts2 == "2026-10-22T16:00:00.000Z"

    # 3. Unix epoch timestamp
    epoch = 1761148800.5
    ts3 = ensure_utc_iso(epoch)
    assert ts3.endswith("Z")

    # 4. Invalid timestamp string
    with pytest.raises(ValueError, match="Invalid timestamp format"):
        ensure_utc_iso("not-a-timestamp")


def test_temperature_boundary_validation():
    """Test temperature min/max boundaries (-40°C to 85°C)."""
    # Valid boundaries
    reading_min = TelemetryReading(device_id="esp32-01", temperature=-40.0, humidity=50.0)
    assert reading_min.temperature == -40.0

    reading_max = TelemetryReading(device_id="esp32-01", temperature=85.0, humidity=50.0)
    assert reading_max.temperature == 85.0

    # Below minimum
    with pytest.raises(ValidationError):
        TelemetryReading(device_id="esp32-01", temperature=-40.1, humidity=50.0)

    # Above maximum
    with pytest.raises(ValidationError):
        TelemetryReading(device_id="esp32-01", temperature=85.1, humidity=50.0)


def test_humidity_boundary_validation():
    """Test relative humidity boundaries (0.0% to 100.0%)."""
    # Negative humidity
    with pytest.raises(ValidationError):
        TelemetryReading(device_id="esp32-01", temperature=20.0, humidity=-0.1)

    # Humidity > 100%
    with pytest.raises(ValidationError):
        TelemetryReading(device_id="esp32-01", temperature=20.0, humidity=100.1)


def test_device_id_validation():
    """Test strict device_id regex and character filtering."""
    # Valid IDs
    valid_ids = ["esp32-01", "sensor_node_99", "DEV-12345", "esp32"]
    for d_id in valid_ids:
        r = TelemetryReading(device_id=d_id, temperature=20.0, humidity=50.0)
        assert r.device_id == d_id

    # Invalid IDs
    invalid_ids = ["e", "ab", "bad id with space", "device@home", "!@#$", "a" * 65]
    for bad_id in invalid_ids:
        with pytest.raises(ValidationError):
            TelemetryReading(device_id=bad_id, temperature=20.0, humidity=50.0)


def test_duplicate_timestamp_safe_composite_sort_key():
    """Test that simultaneous readings with identical timestamps have unique sort keys."""
    same_timestamp = "2026-10-22T21:30:00.123Z"

    reading_1 = TelemetryReading(
        device_id="esp32-sensor-01",
        timestamp=same_timestamp,
        temperature=22.0,
        humidity=45.0,
        message_id="msg-uuid-0001",
    )
    reading_2 = TelemetryReading(
        device_id="esp32-sensor-01",
        timestamp=same_timestamp,
        temperature=22.5,
        humidity=46.0,
        message_id="msg-uuid-0002",
    )

    sk1 = reading_1.get_composite_sort_key()
    sk2 = reading_2.get_composite_sort_key()

    assert sk1 == "2026-10-22T21:30:00.123Z#msg-uuid-0001"
    assert sk2 == "2026-10-22T21:30:00.123Z#msg-uuid-0002"
    assert sk1 != sk2, "Duplicate timestamps must have distinct composite sort keys"


def test_dynamodb_item_serialization():
    """Test conversion to DynamoDB item dict with Decimals and TTL."""
    reading = TelemetryReading(
        device_id="esp32-sensor-01",
        timestamp="2026-10-22T21:30:00.000Z",
        temperature=24.5,
        humidity=52.3,
        battery_voltage=3.85,
        status=DeviceStatus.OK,
        message_id="msg-test-123",
    )

    item = reading.to_dynamodb_item(ttl_days=30)

    assert item["device_id"] == "esp32-sensor-01"
    assert item["timestamp"] == "2026-10-22T21:30:00.000Z#msg-test-123"
    assert item["iso_timestamp"] == "2026-10-22T21:30:00.000Z"
    assert isinstance(item["temperature"], Decimal)
    assert item["temperature"] == Decimal("24.5")
    assert isinstance(item["ttl"], int)
    # TTL should be approximately 30 days in seconds from now
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    assert (item["ttl"] - now_epoch) >= (29 * 86400)

    # Test roundtrip reconstruction
    restored = TelemetryReading.from_dynamodb_item(item)
    assert restored.device_id == reading.device_id
    assert restored.temperature == reading.temperature
    assert restored.message_id == reading.message_id


def test_anomaly_payload_detection(anomaly_payload_dict):
    """Test that anomaly payload automatically promotes status to WARNING."""
    reading = TelemetryReading(**anomaly_payload_dict)
    assert reading.temperature > 50.0
    assert reading.status == DeviceStatus.WARNING


def test_malformed_payload_fails_validation(malformed_payload_dict):
    """Test that malformed JSON payload fails validation with clear errors."""
    with pytest.raises(ValidationError):
        TelemetryReading(**malformed_payload_dict)
