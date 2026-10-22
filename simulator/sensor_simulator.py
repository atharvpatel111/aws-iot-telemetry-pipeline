"""Realistic ESP32 Telemetry Sensor Simulator.

Emulates an ESP32 microcontroller with a DHT22/BME280 temperature and humidity
sensor, reporting over MQTT or to local output.
"""

from __future__ import annotations
import argparse
import json
import math
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

# Add parent directory to sys.path so simulator can import src.common
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.models import DeviceStatus, TelemetryReading, ensure_utc_iso
from src.common.logger import get_logger
from simulator.config import SimulatorConfig

logger = get_logger("esp32_simulator")


class SensorSimulator:
    """Emulates telemetry generation for a single ESP32 device."""

    def __init__(self, device_id: str, config: Optional[SimulatorConfig] = None, seed: Optional[int] = None):
        self.device_id = device_id
        self.config = config or SimulatorConfig()
        self.reading_count = 0
        self.current_battery = self.config.battery_start_voltage
        self.random = random.Random(seed)

    def calculate_diurnal_temperature(self, dt: datetime) -> float:
        """Calculate diurnal temperature based on the time of day.

        Coldest around 06:00, hottest around 15:00.
        """
        # Hour of day as float (e.g. 14.5 for 14:30)
        hour_fraction = dt.hour + (dt.minute / 60.0) + (dt.second / 3600.0)
        # Shift phase so peak is around hour 15 (3 PM)
        phase = (hour_fraction - 9.0) / 24.0 * 2.0 * math.pi
        swing = math.sin(phase) * self.config.temperature_amplitude
        # Add slight natural sensor noise (standard deviation 0.3°C)
        noise = self.random.gauss(0.0, 0.3)
        return self.config.base_temperature + swing + noise

    def calculate_correlated_humidity(self, current_temp: float) -> float:
        """Relative humidity typically moves inversely to temperature."""
        temp_delta = current_temp - self.config.base_temperature
        # Inverse correlation: +1°C temp drops humidity by ~1.5%
        humidity = self.config.base_humidity - (temp_delta * 1.5)
        # Add sensor noise (standard deviation 1.0%)
        humidity += self.random.gauss(0.0, 1.0)
        # Clamp within physical limits
        return max(5.0, min(99.0, humidity))

    def generate_reading(
        self,
        timestamp: Optional[datetime] = None,
        force_anomaly: bool = False,
    ) -> TelemetryReading:
        """Generate a single validated TelemetryReading."""
        now = timestamp or datetime.now(timezone.utc)
        self.reading_count += 1

        # Check for anomaly (temperature spike)
        is_anomaly = force_anomaly or (self.random.random() < self.config.anomaly_probability)

        if is_anomaly:
            temperature = round(self.random.uniform(55.0, 75.0), 2)
            status = DeviceStatus.WARNING
        else:
            temperature = round(self.calculate_diurnal_temperature(now), 2)
            status = DeviceStatus.OK

        humidity = round(self.calculate_correlated_humidity(temperature), 2)

        # Simulate gradual battery discharge
        self.current_battery = max(2.6, self.current_battery - self.config.battery_discharge_rate)
        battery_voltage = round(self.current_battery, 2)

        if battery_voltage < 3.0 and status == DeviceStatus.OK:
            status = DeviceStatus.LOW_BATTERY

        reading = TelemetryReading(
            device_id=self.device_id,
            timestamp=ensure_utc_iso(now),
            temperature=temperature,
            humidity=humidity,
            battery_voltage=battery_voltage,
            status=status,
            firmware_version="1.0.0",
        )
        return reading

    def stream_readings(
        self,
        count: Optional[int] = None,
        interval_seconds: Optional[float] = None,
        callback: Optional[Callable[[TelemetryReading], None]] = None,
    ) -> Iterator[TelemetryReading]:
        """Yield telemetry readings at specified intervals.

        If count is None, runs indefinitely.
        """
        interval = interval_seconds if interval_seconds is not None else self.config.publish_interval_seconds
        generated = 0

        while count is None or generated < count:
            reading = self.generate_reading()
            generated += 1

            if callback:
                callback(reading)

            yield reading

            if count is None or generated < count:
                time.sleep(interval)


def print_reading_callback(reading: TelemetryReading) -> None:
    """Format and print reading as pretty JSON to stdout."""
    payload = reading.model_dump()
    print(f"[{reading.timestamp}] [{reading.device_id}] Temp: {reading.temperature} C, "
          f"Humidity: {reading.humidity}%, Battery: {reading.battery_voltage}V, Status: {reading.status.value}")
    print(f"Payload JSON: {json.dumps(payload, indent=2)}\n")


def main() -> None:
    """CLI entrypoint for running the simulator locally."""
    parser = argparse.ArgumentParser(description="ESP32 IoT Telemetry Sensor Simulator")
    parser.add_argument("--device-id", default="esp32-sensor-01", help="Device ID")
    parser.add_argument("--count", type=int, default=5, help="Number of readings to generate (0 for infinite)")
    parser.add_argument("--interval", type=float, default=2.0, help="Interval in seconds between readings")
    parser.add_argument("--anomaly", action="store_true", help="Force high-temperature anomaly")
    args = parser.parse_args()

    simulator = SensorSimulator(device_id=args.device_id)
    print(f"--- Starting ESP32 Sensor Simulator [{args.device_id}] ---")
    print(f"Count: {args.count or 'Infinite'} | Interval: {args.interval}s\n")

    if args.count == 0:
        for _ in simulator.stream_readings(count=None, interval_seconds=args.interval, callback=print_reading_callback):
            pass
    else:
        for _ in simulator.stream_readings(count=args.count, interval_seconds=args.interval, callback=print_reading_callback):
            pass


if __name__ == "__main__":
    main()
