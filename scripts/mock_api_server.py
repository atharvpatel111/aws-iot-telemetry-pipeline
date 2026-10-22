"""Lightweight Local Mock API Server for the IoT Telemetry Dashboard.

Allows full local dashboard development and interactive testing completely offline
without AWS credentials or incurring any cloud charges.

Features:
- GET /health: Returns simulated database health probe.
- GET /devices: Returns list of active simulated sensors.
- GET /devices/{device_id}/latest: Returns realistic live telemetry.
- GET /devices/{device_id}/history: Returns paginated time-series telemetry.
- Static file server: Directly serves frontend/ at http://localhost:8000/
"""

from __future__ import annotations
import argparse
import base64
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import math
import mimetypes
from pathlib import Path
import random
import re
import sys
from urllib.parse import parse_qs, urlparse

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"

MOCK_DEVICES = {
    "esp32-sensor-01": {"name": "ESP32 Sensor 01 (Server Room)", "base_temp": 22.5, "base_hum": 45.0},
    "esp32-sensor-02": {"name": "ESP32 Sensor 02 (Warehouse)", "base_temp": 18.0, "base_hum": 60.0},
    "esp32-greenhouse-01": {"name": "ESP32 Greenhouse 01 (Agriculture)", "base_temp": 26.0, "base_hum": 75.0},
}


def generate_mock_reading(device_id: str, timestamp: datetime, seed_offset: int = 0) -> dict:
    """Generate a realistic mock reading matching the production TelemetryReading schema."""
    dev_info = MOCK_DEVICES.get(device_id, {"base_temp": 22.0, "base_hum": 50.0})
    base_t = dev_info["base_temp"]
    base_h = dev_info["base_hum"]

    # Diurnal fluctuation based on hour of day
    hour = timestamp.hour + (timestamp.minute / 60.0)
    temp_swing = math.sin((hour - 9) / 24 * 2 * math.pi) * 5.0
    rand = random.Random(int(timestamp.timestamp()) + seed_offset)
    noise = rand.gauss(0, 0.4)
    temperature = round(base_t + temp_swing + noise, 2)

    # Inversely correlated humidity
    humidity = round(max(10.0, min(95.0, base_h - (temp_swing * 1.5) + rand.gauss(0, 1.0))), 2)

    # Battery
    battery_voltage = round(max(3.2, 4.15 - (hour * 0.015)), 2)
    status = "OK"
    if temperature > 50.0:
        status = "WARNING"
    elif battery_voltage < 3.3:
        status = "LOW_BATTERY"

    iso_str = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    msg_id = f"mock-msg-{int(timestamp.timestamp())}-{seed_offset}"

    return {
        "device_id": device_id,
        "timestamp": iso_str,
        "temperature": temperature,
        "humidity": humidity,
        "battery_voltage": battery_voltage,
        "status": status,
        "message_id": msg_id,
        "firmware_version": "1.0.0",
    }


class MockApiRequestHandler(SimpleHTTPRequestHandler):
    """Handles both REST API mock endpoints and serves frontend static files."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def _set_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Amz-Date, X-Api-Key")

    def _send_json_response(self, status_code: int, payload: dict):
        body_bytes = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status_code)
        self._set_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_OPTIONS(self):
        """Handle CORS preflight options."""
        self.send_response(200)
        self._set_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        """Route GET requests to API endpoints or static files."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query_params = parse_qs(parsed_url.query)

        # 1. API Route: GET /health
        if path == "/health":
            self._send_json_response(200, {
                "success": True,
                "data": {
                    "status": "HEALTHY",
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "database": {
                        "status": "HEALTHY",
                        "table_name": "mock-telemetry-records",
                        "table_status": "ACTIVE",
                    },
                },
            })
            return

        # 2. API Route: GET /devices
        if path == "/devices":
            self._send_json_response(200, {
                "success": True,
                "data": list(MOCK_DEVICES.keys()),
            })
            return

        # 3. API Route: GET /devices/{device_id}/latest
        match_latest = re.match(r"^/devices/([^/]+)/latest$", path)
        if match_latest:
            device_id = match_latest.group(1)
            if device_id not in MOCK_DEVICES:
                self._send_json_response(404, {
                    "success": False,
                    "error": {"message": f"No telemetry records found for device '{device_id}'", "code": 404},
                })
                return

            reading = generate_mock_reading(device_id, datetime.now(timezone.utc))
            self._send_json_response(200, {
                "success": True,
                "data": reading,
            })
            return

        # 4. API Route: GET /devices/{device_id}/history
        match_history = re.match(r"^/devices/([^/]+)/history$", path)
        if match_history:
            device_id = match_history.group(1)
            if device_id not in MOCK_DEVICES:
                self._send_json_response(404, {
                    "success": False,
                    "error": {"message": f"Device '{device_id}' not found", "code": 404},
                })
                return

            # Parse limit
            limit_param = query_params.get("limit", ["50"])[0]
            try:
                limit = int(limit_param)
                if limit < 1 or limit > 100:
                    raise ValueError()
            except ValueError:
                self._send_json_response(400, {
                    "success": False,
                    "error": {"message": f"Invalid limit '{limit_param}'. Must be integer between 1 and 100.", "code": 400},
                })
                return

            # Parse start and end times
            now = datetime.now(timezone.utc)
            start_str = query_params.get("start", [None])[0]
            end_str = query_params.get("end", [None])[0]

            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00")) if start_str else (now - timedelta(hours=1))
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00")) if end_str else now
            except ValueError:
                self._send_json_response(400, {
                    "success": False,
                    "error": {"message": "Invalid date format. Expected ISO-8601.", "code": 400},
                })
                return

            if start_dt > end_dt:
                self._send_json_response(400, {
                    "success": False,
                    "error": {"message": "Start time cannot be after end time.", "code": 400},
                })
                return

            # Generate synthetic history points
            records = []
            duration_seconds = max(60, int((end_dt - start_dt).total_seconds()))
            step_seconds = max(10, duration_seconds // 30)

            current_t = start_dt
            idx = 0
            while current_t <= end_dt:
                records.append(generate_mock_reading(device_id, current_t, seed_offset=idx))
                current_t += timedelta(seconds=step_seconds)
                idx += 1

            # Slice by limit
            paged_records = records[:limit]
            next_token = None
            if len(records) > limit:
                raw_token = json.dumps({"device_id": device_id, "timestamp": paged_records[-1]["timestamp"]})
                next_token = base64.urlsafe_b64encode(raw_token.encode("utf-8")).decode("utf-8")

            self._send_json_response(200, {
                "success": True,
                "data": paged_records,
                "meta": {
                    "device_id": device_id,
                    "count": len(paged_records),
                    "limit": limit,
                    "next_token": next_token,
                },
            })
            return

        # Fallback: Serve static files from frontend/
        super().do_GET()


def run_mock_server(host: str = "127.0.0.1", port: int = 8000):
    """Run local mock server indefinitely."""
    server_address = (host, port)
    httpd = HTTPServer(server_address, MockApiRequestHandler)
    print(f"\n==================================================================")
    print(f"  Serverless IoT Telemetry Pipeline - Local Mock Server")
    print(f"  Running at: http://{host}:{port}/")
    print(f"  Dashboard UI: http://{host}:{port}/index.html")
    print(f"  API Health:   http://{host}:{port}/health")
    print(f"  Press Ctrl+C to stop.")
    print(f"==================================================================\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping mock server.")
        httpd.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local Mock API Server for IoT Telemetry Pipeline")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    args = parser.parse_args()
    run_mock_server(host=args.host, port=args.port)
