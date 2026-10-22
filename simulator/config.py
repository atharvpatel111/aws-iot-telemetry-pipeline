"""Configuration loader for the ESP32 Sensor Simulator."""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class SimulatorConfig:
    """Simulator runtime configuration with environment variable overrides."""

    # Simulation parameters
    device_ids: List[str] = field(
        default_factory=lambda: os.getenv("DEVICE_IDS", "esp32-sensor-01,esp32-sensor-02").split(",")
    )
    publish_interval_seconds: float = float(os.getenv("PUBLISH_INTERVAL_SECONDS", "5.0"))
    base_temperature: float = float(os.getenv("BASE_TEMPERATURE", "22.0"))  # °C
    temperature_amplitude: float = float(os.getenv("TEMPERATURE_AMPLITUDE", "6.0"))  # Diurnal swing ±6°C
    base_humidity: float = float(os.getenv("BASE_HUMIDITY", "55.0"))  # %
    battery_start_voltage: float = float(os.getenv("BATTERY_START_VOLTAGE", "4.15"))  # V
    battery_discharge_rate: float = float(os.getenv("BATTERY_DISCHARGE_RATE", "0.0005"))  # V per reading
    anomaly_probability: float = float(os.getenv("ANOMALY_PROBABILITY", "0.05"))  # 5% chance of temp spike

    # Operational mode: 'local' (stdout/file) or 'mqtt' (AWS IoT Core)
    mode: str = os.getenv("SIMULATOR_MODE", "local")

    # AWS IoT Core MQTT parameters (required only when mode == 'mqtt')
    iot_endpoint: str = os.getenv("AWS_IOT_ENDPOINT", "")
    iot_port: int = int(os.getenv("AWS_IOT_PORT", "8883"))
    topic_prefix: str = os.getenv("MQTT_TOPIC_PREFIX", "sensors")
    root_ca_path: str = os.getenv("ROOT_CA_PATH", "")
    private_key_path: str = os.getenv("PRIVATE_KEY_PATH", "")
    certificate_path: str = os.getenv("CERTIFICATE_PATH", "")
