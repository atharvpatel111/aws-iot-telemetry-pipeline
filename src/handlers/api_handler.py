"""AWS Lambda handler for API Gateway REST API endpoints.

Endpoints:
- GET /health: Liveness and DynamoDB connectivity probe.
- GET /devices/{device_id}/latest: O(1) query for latest reading.
- GET /devices/{device_id}/history: Validated time-range queries with opaque token pagination.
"""

from __future__ import annotations
import base64
import json
from datetime import datetime, timezone
import re
from typing import Any, Dict, Optional, Tuple

from src.common.logger import get_logger
from src.common.models import DEVICE_ID_REGEX, ensure_utc_iso
from src.common.response import build_response, error_response, success_response
from src.repository.telemetry_repo import TelemetryRepository

logger = get_logger("api_handler")

# Reusable repository instance for warm container execution
_repo: Optional[TelemetryRepository] = None


def get_repository() -> TelemetryRepository:
    """Lazy-load repository for container reuse."""
    global _repo
    if _repo is None:
        _repo = TelemetryRepository()
    return _repo


def set_repository(repo: TelemetryRepository) -> None:
    """Set custom repository instance (useful for unit testing)."""
    global _repo
    _repo = repo


def encode_pagination_token(exclusive_start_key: Dict[str, Any]) -> str:
    """Encode DynamoDB LastEvaluatedKey into a safe, opaque URL-safe base64 string.

    Avoids exposing DynamoDB internal schema/types to frontend clients.
    """
    compact_json = json.dumps(exclusive_start_key, separators=(",", ":"))
    return base64.urlsafe_b64encode(compact_json.encode("utf-8")).decode("utf-8")


def decode_pagination_token(token: str, expected_device_id: str) -> Dict[str, Any]:
    """Decode and strictly validate an opaque pagination token.

    Rejects tampered tokens, non-base64 input, and cross-device token substitution.
    """
    try:
        raw_json = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        data = json.loads(raw_json)
    except Exception as exc:
        raise ValueError("Invalid pagination token: malformed encoding") from exc

    if not isinstance(data, dict):
        raise ValueError("Invalid pagination token: expected object")

    # Validate that required keys are present
    if "device_id" not in data or "timestamp" not in data:
        raise ValueError("Invalid pagination token: missing key attributes")

    # Prevent token substitution across different devices
    token_device = data.get("device_id")
    if token_device != expected_device_id:
        raise ValueError("Invalid pagination token: token does not match requested device")

    return data


def handle_health(repo: TelemetryRepository) -> Dict[str, Any]:
    """GET /health handler."""
    try:
        db_health = repo.check_health()
        return success_response(
            data={
                "status": "HEALTHY",
                "timestamp": ensure_utc_iso(datetime.now(timezone.utc)),
                "database": db_health,
            },
            status_code=200,
        )
    except Exception as exc:
        logger.error(f"Health check failed: {str(exc)}", exc_info=True)
        return error_response(
            message="Service Unhealthy: Database connectivity failure",
            status_code=503,
            details={"database_status": "UNREACHABLE", "error": str(exc)},
        )


def handle_get_latest(device_id: str, repo: TelemetryRepository) -> Dict[str, Any]:
    """GET /devices/{device_id}/latest handler."""
    if not DEVICE_ID_REGEX.match(device_id):
        return error_response(
            message=f"Invalid device_id '{device_id}'. Must be 3-64 alphanumeric characters, hyphens, or underscores.",
            status_code=400,
        )

    try:
        latest = repo.get_latest_reading(device_id)
        if latest is None:
            return error_response(
                message=f"No telemetry records found for device '{device_id}'",
                status_code=404,
            )
        return success_response(data=latest.model_dump(), status_code=200)
    except Exception as exc:
        logger.error(f"Failed to fetch latest reading for '{device_id}': {str(exc)}", exc_info=True)
        return error_response(
            message="Internal server error fetching latest reading",
            status_code=500,
            details=str(exc),
        )


def handle_get_history(
    device_id: str,
    query_params: Dict[str, str],
    repo: TelemetryRepository,
) -> Dict[str, Any]:
    """GET /devices/{device_id}/history handler with validation and pagination."""
    if not DEVICE_ID_REGEX.match(device_id):
        return error_response(
            message=f"Invalid device_id '{device_id}'. Must be 3-64 alphanumeric characters, hyphens, or underscores.",
            status_code=400,
        )

    # 1. Parse and validate limit
    limit_str = query_params.get("limit", "50")
    try:
        limit = int(limit_str)
        if limit < 1 or limit > 100:
            return error_response(
                message=f"Invalid 'limit' parameter: {limit}. Must be an integer between 1 and 100.",
                status_code=400,
            )
    except ValueError:
        return error_response(
            message=f"Invalid 'limit' parameter: '{limit_str}'. Must be an integer.",
            status_code=400,
        )

    # 2. Parse and validate start & end timestamps
    start_param = query_params.get("start")
    end_param = query_params.get("end")
    start_iso = None
    end_iso = None

    if start_param:
        try:
            start_iso = ensure_utc_iso(start_param)
        except ValueError as exc:
            return error_response(
                message=f"Invalid 'start' timestamp: '{start_param}'. Must be ISO-8601 format.",
                status_code=400,
                details=str(exc),
            )

    if end_param:
        try:
            end_iso = ensure_utc_iso(end_param)
        except ValueError as exc:
            return error_response(
                message=f"Invalid 'end' timestamp: '{end_param}'. Must be ISO-8601 format.",
                status_code=400,
                details=str(exc),
            )

    if start_iso and end_iso and start_iso > end_iso:
        return error_response(
            message=f"'start' time ({start_iso}) cannot be after 'end' time ({end_iso}).",
            status_code=400,
        )

    # 3. Parse and validate pagination token
    exclusive_start_key = None
    next_token_param = query_params.get("next_token")
    if next_token_param:
        try:
            exclusive_start_key = decode_pagination_token(next_token_param, expected_device_id=device_id)
        except ValueError as exc:
            return error_response(
                message=str(exc),
                status_code=400,
            )

    # 4. Query DynamoDB
    try:
        readings, last_key = repo.get_history(
            device_id=device_id,
            start_time=start_iso,
            end_time=end_iso,
            limit=limit,
            ascending=True,
            exclusive_start_key=exclusive_start_key,
        )

        next_token = encode_pagination_token(last_key) if last_key else None

        return success_response(
            data=[r.model_dump() for r in readings],
            status_code=200,
            meta={
                "device_id": device_id,
                "count": len(readings),
                "limit": limit,
                "next_token": next_token,
            },
        )
    except Exception as exc:
        logger.error(f"Failed to fetch history for '{device_id}': {str(exc)}", exc_info=True)
        return error_response(
            message="Internal server error fetching device history",
            status_code=500,
            details=str(exc),
        )


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Entrypoint for API Gateway REST API Proxy requests."""
    logger.info("Received API Gateway request", extra={"extra_data": {"path": event.get("path")}})

    # Support CORS preflight
    http_method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if http_method.upper() == "OPTIONS":
        return build_response(status_code=200, body={"message": "OK"})

    path = event.get("path") or event.get("rawPath") or ""
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    repo = get_repository()

    try:
        # Route: GET /health
        if path.rstrip("/") == "/health" or path.endswith("/health"):
            return handle_health(repo)

        # Route: GET /devices/{device_id}/latest
        if "/devices/" in path and path.endswith("/latest"):
            device_id = path_params.get("device_id")
            if not device_id:
                # Fallback parse from path if pathParameters is missing
                match = re.search(r"/devices/([^/]+)/latest", path)
                device_id = match.group(1) if match else ""
            return handle_get_latest(device_id, repo)

        # Route: GET /devices/{device_id}/history
        if "/devices/" in path and path.endswith("/history"):
            device_id = path_params.get("device_id")
            if not device_id:
                # Fallback parse from path
                match = re.search(r"/devices/([^/]+)/history", path)
                device_id = match.group(1) if match else ""
            return handle_get_history(device_id, query_params, repo)

        # Catch-all: Route Not Found
        return error_response(
            message=f"Route not found: {http_method} {path}",
            status_code=404,
        )
    except Exception as exc:
        logger.error(f"Unhandled exception in API handler: {str(exc)}", exc_info=True)
        return error_response(
            message="Internal server error",
            status_code=500,
            details=str(exc),
        )
