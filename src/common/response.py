"""Standardized API Gateway response formatters with CORS support."""

from __future__ import annotations
import json
from decimal import Decimal
from typing import Any, Dict, Optional


class DecimalEncoder(json.JSONEncoder):
    """Custom JSON encoder converting Decimal to float/int for API serialization."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            # Convert to int if whole number, else float
            return int(obj) if obj % 1 == 0 else float(obj)
        return super().default(obj)


DEFAULT_CORS_HEADERS: Dict[str, str] = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Content-Type": "application/json",
}


def build_response(
    status_code: int,
    body: Any,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Construct an API Gateway proxy response with standard CORS headers.

    Args:
        status_code: HTTP status code.
        body: Python dict/list to be JSON-serialized, or raw string.
        headers: Optional extra or override headers.

    Returns:
        Dictionary matching the AWS API Gateway Lambda Proxy integration response format.
    """
    merged_headers = dict(DEFAULT_CORS_HEADERS)
    if headers:
        merged_headers.update(headers)

    if isinstance(body, str):
        payload_str = body
    else:
        payload_str = json.dumps(body, cls=DecimalEncoder)

    return {
        "statusCode": status_code,
        "headers": merged_headers,
        "body": payload_str,
    }


def success_response(
    data: Any,
    status_code: int = 200,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Helper for successful responses with consistent structure."""
    response_body: Dict[str, Any] = {
        "success": True,
        "data": data,
    }
    if meta is not None:
        response_body["meta"] = meta
    return build_response(status_code=status_code, body=response_body)


def error_response(
    message: str,
    status_code: int = 400,
    details: Optional[Any] = None,
) -> Dict[str, Any]:
    """Helper for error responses with consistent structure."""
    response_body: Dict[str, Any] = {
        "success": False,
        "error": {
            "message": message,
            "code": status_code,
        },
    }
    if details is not None:
        response_body["error"]["details"] = details
    return build_response(status_code=status_code, body=response_body)
