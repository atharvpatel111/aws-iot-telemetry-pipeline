/**
 * Dashboard Configuration
 * Centralizes the API endpoint, polling intervals, and default settings.
 */

const CONFIG = {
  // Local Mock Server: 'http://localhost:8000'
  // AWS API Gateway: 'https://<api-id>.execute-api.<region>.amazonaws.com/<stage>'
  apiBaseUrl: window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : 'https://api.yourdomain.com/dev',

  // Available devices in the system
  defaultDevices: [
    { id: 'esp32-sensor-01', name: 'ESP32 Sensor 01 (Server Room)' },
    { id: 'esp32-sensor-02', name: 'ESP32 Sensor 02 (Warehouse)' },
    { id: 'esp32-greenhouse-01', name: 'ESP32 Greenhouse 01 (Agriculture)' },
  ],

  // Auto-refresh interval in milliseconds (default: 5 seconds)
  refreshIntervalMs: 5000,

  // Chart configuration defaults
  chartMaxPoints: 60,
};

// Freeze configuration to prevent accidental modification
Object.freeze(CONFIG);
