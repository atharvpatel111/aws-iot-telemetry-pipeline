"""Telemetry data models and validation logic.

Ensures strict validation of IoT payloads:
- UTC ISO-8601 timestamps
- Physical boundary checks (temperature, humidity, battery)
- Unique composite sort key generation for DynamoDB to handle duplicate timestamps safely.
"""

from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import re
import uuid
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


DEVICE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")


class DeviceStatus(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    LOW_BATTERY = "LOW_BATTERY"


def ensure_utc_iso(dt_input: Any) -> str:
    """Parse and normalize any datetime input into an ISO-8601 UTC string.

    Format: YYYY-MM-DDTHH:MM:SS.sssZ
    """
    if isinstance(dt_input, datetime):
        dt = dt_input
    elif isinstance(dt_input, str):
        # Handle 'Z' suffix for python < 3.11 compatibility if needed
        clean_str = dt_input.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(clean_str)
        except ValueError as exc:
            raise ValueError(f"Invalid timestamp format: '{dt_input}'. Expected ISO-8601.") from exc
    elif isinstance(dt_input, (int, float)):
        # Treat numeric as Unix epoch seconds
        dt = datetime.fromtimestamp(dt_input, tz=timezone.utc)
    else:
        raise ValueError(f"Unsupported timestamp type: {type(dt_input)}")

    if dt.tzinfo is None:
        # Naive datetime: assume UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # Timezone-aware: convert to UTC
        dt = dt.astimezone(timezone.utc)

    # Format with 3 decimal places (milliseconds) and standard 'Z' suffix
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class TelemetryReading(BaseModel):
    """Represents a validated telemetry reading from an IoT device."""

    device_id: str = Field(
        ...,
        description="Unique device identifier (3-64 alphanumeric, dash, or underscore)",
        examples=["esp32-sensor-01"],
    )
    timestamp: str = Field(
        default_factory=lambda: ensure_utc_iso(datetime.now(timezone.utc)),
        description="ISO-8601 UTC timestamp with millisecond precision",
        examples=["2026-10-22T21:30:00.123Z"],
    )
    temperature: float = Field(
        ...,
        ge=-40.0,
        le=85.0,
        description="Ambient temperature in degrees Celsius (DHT22 operating range -40 to 85°C)",
        examples=[23.5],
    )
    humidity: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Relative humidity percentage (0.0 to 100.0%)",
        examples=[48.2],
    )
    battery_voltage: float = Field(
        default=3.7,
        ge=2.0,
        le=5.0,
        description="Battery voltage in Volts (typical LiPo 2.5V cutoff to 4.2V max)",
        examples=[3.82],
    )
    status: DeviceStatus = Field(
        default=DeviceStatus.OK,
        description="Operational status of the device",
    )
    message_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique message identifier for idempotency and duplicate timestamp resolution",
    )
    firmware_version: str = Field(
        default="1.0.0",
        description="Firmware version running on the device",
    )

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, v: str) -> str:
        v = v.strip()
        if not DEVICE_ID_REGEX.match(v):
            raise ValueError(
                f"device_id '{v}' is invalid. Must be 3-64 chars containing only letters, numbers, hyphens, and underscores."
            )
        return v

    @field_validator("timestamp")
    @classmethod
    def validate_and_normalize_timestamp(cls, v: Any) -> str:
        return ensure_utc_iso(v)

    @field_validator("temperature", "humidity", "battery_voltage")
    @classmethod
    def round_float_values(cls, v: float) -> float:
        return round(float(v), 2)

    @model_validator(mode="after")
    def derive_status_if_not_set(self) -> TelemetryReading:
        """Automatically promote status if temperature is dangerous or battery is depleted."""
        # Battery critical condition (< 3.0V for LiPo cell)
        if self.battery_voltage < 3.0 and self.status == DeviceStatus.OK:
            self.status = DeviceStatus.LOW_BATTERY
        # High temperature alarm condition (> 50.0°C)
        elif self.temperature > 50.0 and self.status != DeviceStatus.CRITICAL:
            self.status = DeviceStatus.WARNING
        return self

    def get_composite_sort_key(self) -> str:
        """Generate a composite Sort Key: <UTC_TIMESTAMP>#<MESSAGE_ID>.

        Ensures that multiple readings from the same device in the same millisecond
        never overwrite each other in DynamoDB, while maintaining strict chronological
        lexicographical sort order.
        """
        return f"{self.timestamp}#{self.message_id}"

    def to_dynamodb_item(self, ttl_days: int = 30) -> Dict[str, Any]:
        """Convert validated reading into a DynamoDB item dictionary.

        - Converts floats to Decimal for DynamoDB Boto3 compatibility.
        - Calculates TTL epoch timestamp (seconds) for zero-cost automatic expiration.
        """
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        ttl_epoch = now_epoch + (ttl_days * 86400)

        return {
            "device_id": self.device_id,  # Partition Key (PK)
            "timestamp": self.get_composite_sort_key(),  # Sort Key (SK)
            "iso_timestamp": self.timestamp,  # Clean ISO UTC string for API responses
            "temperature": Decimal(str(self.temperature)),
            "humidity": Decimal(str(self.humidity)),
            "battery_voltage": Decimal(str(self.battery_voltage)),
            "status": self.status.value,
            "message_id": self.message_id,
            "firmware_version": self.firmware_version,
            "ttl": ttl_epoch,
        }

    @classmethod
    def from_dynamodb_item(cls, item: Dict[str, Any]) -> TelemetryReading:
        """Reconstruct TelemetryReading from DynamoDB item."""
        return cls(
            device_id=str(item["device_id"]),
            timestamp=str(item.get("iso_timestamp", item["timestamp"].split("#")[0])),
            temperature=float(item["temperature"]),
            humidity=float(item["humidity"]),
            battery_voltage=float(item.get("battery_voltage", 3.7)),
            status=DeviceStatus(str(item.get("status", "OK"))),
            message_id=str(item.get("message_id", item["timestamp"].split("#")[-1] if "#" in item["timestamp"] else str(uuid.uuid4()))),
            firmware_version=str(item.get("firmware_version", "1.0.0")),
        )
