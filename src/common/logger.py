"""Structured JSON Logger for AWS CloudWatch and local console output."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """Custom logging formatter that outputs one-line JSON documents."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }

        # Include custom extra fields if provided
        if hasattr(record, "device_id"):
            log_entry["device_id"] = record.device_id
        if hasattr(record, "message_id"):
            log_entry["message_id"] = record.message_id
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            log_entry.update(record.extra_data)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


def get_logger(name: str = "iot_telemetry", level: Optional[str] = None) -> logging.Logger:
    """Configure and return a structured JSON logger."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    log_level = getattr(logging, (level or "INFO").upper(), logging.INFO)
    logger.setLevel(log_level)
    return logger
