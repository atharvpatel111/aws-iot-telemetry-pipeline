"""DynamoDB Data Access Layer for IoT Telemetry records.

Follows AWS single-table design best practices:
- Partition Key (PK): device_id
- Sort Key (SK): timestamp (<ISO-8601 UTC>#<message_id>)
- Queries retrieve the latest record in O(1) via descending index traversal without scanning.
"""

from __future__ import annotations
import os
from typing import Any, Dict, List, Optional, Tuple
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from src.common.logger import get_logger
from src.common.models import TelemetryReading, ensure_utc_iso

logger = get_logger("telemetry_repo")


class TelemetryRepository:
    """Repository class managing DynamoDB persistence and query access for telemetry."""

    def __init__(
        self,
        table_name: Optional[str] = None,
        dynamodb_resource: Optional[Any] = None,
    ) -> None:
        """Initialize repository.

        Args:
            table_name: DynamoDB table name (defaults to TELEMETRY_TABLE_NAME env var or 'telemetry_records').
            dynamodb_resource: Optional pre-configured boto3 DynamoDB resource (useful for injecting Moto mocks).
        """
        self.table_name = table_name or os.getenv("TELEMETRY_TABLE_NAME", "telemetry_records")
        region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self._dynamodb = dynamodb_resource or boto3.resource("dynamodb", region_name=region)
        self.table = self._dynamodb.Table(self.table_name)

    def check_health(self) -> Dict[str, Any]:
        """Verify connectivity to DynamoDB table.

        Returns:
            Dict containing status ('HEALTHY' or 'UNHEALTHY'), table_name, and table_status.
        Raises:
            Exception if DynamoDB is unreachable.
        """
        self.table.load()
        return {
            "status": "HEALTHY",
            "table_name": self.table_name,
            "table_status": getattr(self.table, "table_status", "ACTIVE"),
        }

    def save_reading(self, reading: TelemetryReading, ttl_days: int = 30) -> Dict[str, Any]:
        """Persist a validated TelemetryReading item into DynamoDB.

        Args:
            reading: Validated TelemetryReading model instance.
            ttl_days: Number of days before DynamoDB TTL auto-deletes the record (default: 30 days).

        Returns:
            The DynamoDB PutItem response dict.
        """
        item = reading.to_dynamodb_item(ttl_days=ttl_days)
        try:
            response = self.table.put_item(Item=item)
            logger.info(
                f"Saved telemetry record for device '{reading.device_id}'",
                extra={
                    "device_id": reading.device_id,
                    "message_id": reading.message_id,
                    "extra_data": {"sort_key": item["timestamp"]},
                },
            )
            return response
        except ClientError as exc:
            logger.error(
                f"DynamoDB PutItem failed for device '{reading.device_id}': {exc.response['Error']['Message']}",
                exc_info=True,
            )
            raise

    def get_latest_reading(self, device_id: str) -> Optional[TelemetryReading]:
        """Retrieve the single most recent reading for a given device.

        Uses ScanIndexForward=False and Limit=1 to query the sort key in descending order.
        Consumes minimal read capacity units (1 RCU) with zero table scanning.

        Args:
            device_id: The target device identifier.

        Returns:
            The most recent TelemetryReading, or None if no records exist.
        """
        try:
            response = self.table.query(
                KeyConditionExpression=Key("device_id").eq(device_id),
                ScanIndexForward=False,  # Descending chronological order
                Limit=1,
            )
            items = response.get("Items", [])
            if not items:
                return None
            return TelemetryReading.from_dynamodb_item(items[0])
        except ClientError as exc:
            logger.error(
                f"DynamoDB query latest failed for device '{device_id}': {exc.response['Error']['Message']}",
                exc_info=True,
            )
            raise

    def get_history(
        self,
        device_id: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 50,
        ascending: bool = True,
        exclusive_start_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[TelemetryReading], Optional[Dict[str, Any]]]:
        """Retrieve historical telemetry readings for a device with optional time-range filtering and pagination.

        Args:
            device_id: The target device identifier.
            start_time: Optional starting ISO-8601 timestamp (inclusive).
            end_time: Optional ending ISO-8601 timestamp (inclusive).
            limit: Maximum items to return per page (default: 50, max: 200).
            ascending: Sort order (True = oldest first, False = newest first).
            exclusive_start_key: Pagination token from previous query response.

        Returns:
            Tuple of (list of TelemetryReading items, LastEvaluatedKey if more pages exist).
        """
        # Clamp limit
        clamped_limit = max(1, min(limit, 200))

        # Build KeyConditionExpression
        key_expr = Key("device_id").eq(device_id)

        if start_time and end_time:
            utc_start = ensure_utc_iso(start_time)
            # Append high character '#~' to include all composite keys within the end millisecond
            utc_end = ensure_utc_iso(end_time) + "#~"
            key_expr = key_expr & Key("timestamp").between(utc_start, utc_end)
        elif start_time:
            utc_start = ensure_utc_iso(start_time)
            key_expr = key_expr & Key("timestamp").gte(utc_start)
        elif end_time:
            utc_end = ensure_utc_iso(end_time) + "#~"
            key_expr = key_expr & Key("timestamp").lte(utc_end)

        query_kwargs: Dict[str, Any] = {
            "KeyConditionExpression": key_expr,
            "ScanIndexForward": ascending,
            "Limit": clamped_limit,
        }

        if exclusive_start_key:
            query_kwargs["ExclusiveStartKey"] = exclusive_start_key

        try:
            response = self.table.query(**query_kwargs)
            items = response.get("Items", [])
            readings = [TelemetryReading.from_dynamodb_item(item) for item in items]
            last_key = response.get("LastEvaluatedKey")
            return readings, last_key
        except ClientError as exc:
            logger.error(
                f"DynamoDB query history failed for device '{device_id}': {exc.response['Error']['Message']}",
                exc_info=True,
            )
            raise
