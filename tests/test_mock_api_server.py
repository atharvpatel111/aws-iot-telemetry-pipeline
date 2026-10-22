"""Unit and integration tests for scripts/mock_api_server.py."""

from http.server import HTTPServer
import json
import threading
import time
import urllib.request
import urllib.error
import pytest

from scripts.mock_api_server import MockApiRequestHandler

TEST_HOST = "127.0.0.1"
TEST_PORT = 8123
BASE_URL = f"http://{TEST_HOST}:{TEST_PORT}"


@pytest.fixture(scope="module", autouse=True)
def run_test_server():
    """Start mock HTTP server in a background thread for testing."""
    server = HTTPServer((TEST_HOST, TEST_PORT), MockApiRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)  # Allow server to bind
    yield
    server.shutdown()
    server.server_close()


def test_mock_server_health_endpoint():
    """Verify mock GET /health returns 200 and HEALTHY."""
    req = urllib.request.Request(f"{BASE_URL}/health")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["success"] is True
        assert data["data"]["status"] == "HEALTHY"
        assert data["data"]["database"]["table_status"] == "ACTIVE"


def test_mock_server_devices_endpoint():
    """Verify mock GET /devices returns registered test devices."""
    req = urllib.request.Request(f"{BASE_URL}/devices")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["success"] is True
        assert "esp32-sensor-01" in data["data"]


def test_mock_server_latest_telemetry():
    """Verify mock GET /devices/{id}/latest returns valid telemetry."""
    req = urllib.request.Request(f"{BASE_URL}/devices/esp32-sensor-01/latest")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["success"] is True
        reading = data["data"]
        assert reading["device_id"] == "esp32-sensor-01"
        assert -40.0 <= reading["temperature"] <= 85.0
        assert 0.0 <= reading["humidity"] <= 100.0


def test_mock_server_latest_telemetry_not_found():
    """Verify mock GET /devices/{unknown}/latest returns 404."""
    req = urllib.request.Request(f"{BASE_URL}/devices/unknown-device/latest")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 404


def test_mock_server_history_endpoint():
    """Verify mock GET /devices/{id}/history returns records and meta."""
    req = urllib.request.Request(f"{BASE_URL}/devices/esp32-sensor-01/history?limit=15")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["success"] is True
        assert len(data["data"]) <= 15
        assert data["meta"]["device_id"] == "esp32-sensor-01"


def test_mock_server_serves_frontend():
    """Verify mock server serves index.html static content for local preview."""
    req = urllib.request.Request(f"{BASE_URL}/index.html")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "IoT Telemetry Monitor" in content
        assert "telemetryChart" in content
