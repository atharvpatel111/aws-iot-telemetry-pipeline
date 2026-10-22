"""AWS Lambda handler for ingesting IoT Core telemetry records.

Triggered by AWS IoT Core SQL Rule (e.g. SELECT * FROM 'sensors/+/telemetry').
Validates incoming payload schema and writes normalized records to DynamoDB.
"""

from __future__ import annotations
import json
from typing import Any, Dict, Optional
from pydantic import ValidationError

from src.common.logger import get_logger
from src.common.models import TelemetryReading
from src.repository.telemetry_repo import TelemetryRepository

logger = get_logger("ingest_handler")

# Reusable repository instance across warm Lambda invocations
_repo: Optional[TelemetryRepository] = None


def get_repository() -> TelemetryRepository:
    """Lazy-load or reuse singleton TelemetryRepository for Lambda container reuse."""
    global _repo
    if _repo is None:
        _repo = TelemetryRepository()
    return _repo


def set_repository(repo: TelemetryRepository) -> None:
    """Set custom repository instance (useful for unit testing with mocks)."""
    global _repo
    _repo = repo


def lambda_handler(event: Any, context: Any = None) -> Dict[str, Any]:
    """Entrypoint for AWS IoT Core Rule invocations.

    Args:
        event: Dict or JSON string matching the TelemetryReading schema.
        context: Lambda execution context object.

    Returns:
        Structured response dictionary with statusCode and processing summary.
    """
    logger.info("Received IoT telemetry ingestion event")

    # 1. Parse event if received as serialized JSON string
    if isinstance(event, str):
        try:
            payload_dict = json.loads(event)
        except json.JSONDecodeError as exc:
            logger.error(f"Malformed JSON payload: {str(exc)}")
            return {
                "statusCode": 400,
                "error": "Malformed JSON payload",
                "details": str(exc),
            }
    elif isinstance(event, dict):
        payload_dict = event
    else:
        logger.error(f"Unsupported event payload type: {type(event)}")
        return {
            "statusCode": 400,
            "error": "Unsupported event payload type",
        }

    # 2. Schema validation via Pydantic model
    try:
        reading = TelemetryReading(**payload_dict)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            f"Payload validation failed: {str(exc)}",
            extra={
                "extra_data": {
                    "raw_payload": payload_dict,
                    "validation_errors": str(exc),
                }
            },
        )
        return {
            "statusCode": 422,
            "error": "Validation failed",
            "details": str(exc),
        }

    # 3. Persist to DynamoDB via Repository
    repo = get_repository()
    try:
        repo.save_reading(reading)
    except Exception as exc:
        logger.error(
            f"Failed to persist telemetry to DynamoDB: {str(exc)}",
            exc_info=True,
            extra={
                "device_id": reading.device_id,
                "message_id": reading.message_id,
            },
        )
        return {
            "statusCode": 500,
            "error": "Failed to persist record to database",
            "device_id": reading.device_id,
        }

    logger.info(
        f"Telemetry successfully ingested for device '{reading.device_id}'",
        extra={
            "device_id": reading.device_id,
            "message_id": reading.message_id,
            "extra_data": {
                "temperature": reading.temperature,
                "humidity": reading.humidity,
                "status": reading.status.value,
            },
        },
    )

    return {
        "statusCode": 200,
        "body": {
            "message": "Telemetry successfully ingested",
            "device_id": reading.device_id,
            "timestamp": reading.timestamp,
            "message_id": reading.message_id,
            "status": reading.status.value,
        },
    }
