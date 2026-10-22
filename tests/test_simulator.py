"""Unit tests for the ESP32 Sensor Simulator."""

from datetime import datetime, timezone
import pytest

from simulator.config import SimulatorConfig
from simulator.sensor_simulator import SensorSimulator
from src.common.models import DeviceStatus, TelemetryReading


def test_simulator_single_reading():
    """Verify that a single simulated reading produces a valid TelemetryReading."""
    sim = SensorSimulator(device_id="esp32-unit-test", seed=42)
    reading = sim.generate_reading()

    assert isinstance(reading, TelemetryReading)
    assert reading.device_id == "esp32-unit-test"
    assert -40.0 <= reading.temperature <= 85.0
    assert 0.0 <= reading.humidity <= 100.0
    assert 2.0 <= reading.battery_voltage <= 4.5
    assert reading.status in [DeviceStatus.OK, DeviceStatus.WARNING, DeviceStatus.LOW_BATTERY]


def test_diurnal_temperature_swing():
    """Verify temperature peaks in afternoon (~15:00) and cools in early morning (~06:00)."""
    cfg = SimulatorConfig(base_temperature=20.0, temperature_amplitude=8.0)
    sim = SensorSimulator(device_id="esp32-weather", config=cfg, seed=123)

    morning = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
    afternoon = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)

    temp_morning = sim.calculate_diurnal_temperature(morning)
    temp_afternoon = sim.calculate_diurnal_temperature(afternoon)

    assert temp_afternoon > temp_morning, "Afternoon temperature should exceed early morning temperature"
    # Expected swing is approx base + 8 vs base - 8 (~28°C vs ~12°C)
    assert temp_afternoon > 25.0
    assert temp_morning < 15.0


def test_correlated_humidity():
    """Verify relative humidity is inversely correlated to temperature."""
    cfg = SimulatorConfig(base_temperature=20.0, base_humidity=60.0)
    sim = SensorSimulator(device_id="esp32-humidity-test", config=cfg, seed=456)

    high_temp_humidity = sim.calculate_correlated_humidity(35.0)
    low_temp_humidity = sim.calculate_correlated_humidity(10.0)

    assert low_temp_humidity > high_temp_humidity, "Lower temperature should have higher relative humidity"


def test_anomaly_generation():
    """Verify that force_anomaly=True produces high temperature and WARNING status."""
    sim = SensorSimulator(device_id="esp32-anomaly-test", seed=999)
    reading = sim.generate_reading(force_anomaly=True)

    assert reading.temperature >= 50.0
    assert reading.status == DeviceStatus.WARNING


def test_battery_discharge():
    """Verify that battery voltage decreases over repeated readings."""
    cfg = SimulatorConfig(battery_start_voltage=4.20, battery_discharge_rate=0.01)
    sim = SensorSimulator(device_id="esp32-battery-test", config=cfg, seed=1)

    r1 = sim.generate_reading()
    r2 = sim.generate_reading()
    r3 = sim.generate_reading()

    assert r1.battery_voltage >= r2.battery_voltage >= r3.battery_voltage
    assert r3.battery_voltage < 4.20


def test_stream_readings_with_callback():
    """Verify streaming a fixed count of readings invokes callback."""
    sim = SensorSimulator(device_id="esp32-stream-test", seed=10)
    received = []

    def callback(r: TelemetryReading):
        received.append(r)

    readings = list(sim.stream_readings(count=4, interval_seconds=0.0, callback=callback))

    assert len(readings) == 4
    assert len(received) == 4
    assert received[0].device_id == "esp32-stream-test"
