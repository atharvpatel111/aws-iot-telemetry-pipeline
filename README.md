# Serverless IoT Telemetry Pipeline on AWS

A production-grade, serverless data pipeline built with AWS IoT Core, AWS Lambda, Amazon DynamoDB, Amazon API Gateway, and Amazon CloudWatch. The pipeline ingests telemetry (temperature, humidity, battery status) from simulated or physical ESP32 devices via MQTT, validates and stores records in DynamoDB, and exposes query endpoints for a responsive web monitoring dashboard.

---

## Architecture Overview

```
+------------------+         MQTT (TLS 8883)         +--------------------+
|  ESP32 Device /  | -----------------------------> |    AWS IoT Core    |
| Python Simulator |                                 |  (Topic Broker)    |
+------------------+                                 +--------------------+
                                                               |
                                                               | IoT SQL Rule
                                                               v
+------------------+         REST API Calls          +--------------------+
| Responsive Web   | <----------------------------- | Amazon API Gateway |
| Dashboard (HTML) |                                 +--------------------+
+------------------+                                           |
                                                               | Lambda Proxy
                                                               v
+------------------+                                 +--------------------+
| Amazon           | <--- Metrics & Alarms --------- |    AWS Lambda      |
| CloudWatch       |                                 | (Ingest & Query)   |
+------------------+                                 +--------------------+
                                                               |
                                                               | PutItem / Query
                                                               v
                                                     +--------------------+
                                                     |  Amazon DynamoDB   |
                                                     | (Telemetry Table)  |
                                                     +--------------------+
```

### Key Engineering Highlights
- **Strict Data Validation**: Pydantic models validate ISO-8601 UTC timestamps, boundary limits (-40°C to 85°C), and device identifiers.
- **Duplicate Timestamp Resilience**: DynamoDB Sort Key uses a composite `<UTC_ISO_TIMESTAMP>#<MESSAGE_ID>` pattern, preventing race-condition overwrites if multiple readings arrive in the same millisecond from the same device.
- **Zero-Cost Lifecycle**: Automatic record retirement using DynamoDB Time-To-Live (TTL) without consuming write capacity.
- **Cost & Security Conscious**: Least-privilege IAM policies, environment variable configuration, and local mock testing strategy.

---

## Project Structure

```text
aws-iot-telemetry-pipeline/
├── .gitignore
├── README.md                      # Comprehensive project guide & runbook
├── requirements.txt               # Production dependencies
├── requirements-dev.txt           # Test & development dependencies
├── simulator/                     # ESP32 sensor simulator & MQTT client
│   ├── sensor_simulator.py        # Realistic telemetry generation & CLI
│   ├── config.py                  # Simulator config & env loader
│   ├── certs/                     # X.509 certs (gitignored)
│   └── fixtures/                  # Sample test payloads (normal, anomaly, malformed)
├── src/                           # Backend application code
│   └── common/
│       ├── models.py              # Pydantic telemetry models & validators
│       ├── logger.py              # Structured JSON logging for CloudWatch
│       └── response.py            # API response formatters (Phase 5)
│   └── handlers/                  # Lambda handlers (Phases 3 & 5)
│   └── repository/                # DynamoDB access layer (Phase 3)
├── frontend/                      # Web dashboard (Phase 6)
└── tests/                         # Automated unit & integration tests
    ├── conftest.py                # Pytest fixtures
    ├── test_validation.py         # Payload & schema unit tests
    └── test_simulator.py          # Simulator logic & diurnal math tests
```

---

## Progress Checklist

- [x] **Phase 1: Workspace & Architecture Design**
  - Confirmed Python 3.13 runtime compatibility.
  - Defined single-table DynamoDB schema and composite sort key strategy.
  - Defined realistic AWS cost breakdown and security threat model.
- [x] **Phase 2: Sensor Simulator & Validation Layer**
  - Initialized repository and `.venv` with Python 3.13.
  - Implemented `TelemetryReading` model with UTC normalization and bounds checks.
  - Built diurnal ESP32 sensor simulator with anomaly and battery discharge modeling.
  - Added test fixtures and comprehensive unit test suite.
- [x] **Phase 3: Ingestion Lambda & DynamoDB Repository**
  - Implemented `TelemetryRepository` with O(1) descending query pattern for latest reading.
  - Implemented composite sort key `<ISO_UTC_MS>#<message_id>` to safely handle duplicate timestamps.
  - Implemented `ingest_handler` processing AWS IoT Core rule events with schema validation.
  - Added unit test suite using offline in-memory Moto DynamoDB mock.
- [x] **Phase 4: AWS IoT Core Rule & SAM Infrastructure as Code**
  - Implemented `template.yaml` (SAM) with DynamoDB table, Python 3.13 Ingestion Lambda, and IoT Core Topic Rule.
  - Configured least-privilege IAM policies, explicit 14-day CloudWatch log retention, and SQS Dead Letter Queue.
  - Added automated CloudFormation template validation tests (`tests/test_template.py`).
- [x] **Phase 5: API Gateway & Query Lambda**
  - Implemented `api_handler` with `/health`, `/devices/{id}/latest`, and `/devices/{id}/history` endpoints.
  - Added strict parameter validation (`start`, `end`, `limit`) and secure opaque pagination tokens.
  - Standardized CORS headers and JSON formatting in `src/common/response.py`.
  - Added comprehensive API test suite (`tests/test_api_handler.py`, 48 total tests passed).
- [x] **Phase 6: Responsive Web Dashboard**
  - Built semantic HTML5 dashboard with dark/light themes and accessible metrics (`frontend/`).
  - Integrated Chart.js for real-time temperature and humidity time-series visualization.
  - Added offline mock API server (`scripts/mock_api_server.py`) serving both frontend and REST endpoints.
  - Added mock server integration tests (`tests/test_mock_api_server.py`, 54 total tests passed).
- [x] **Phase 7: CloudWatch Logs, Metrics & Alarms**
  - Configured `HighTemperatureMetricFilter` extracting `{ $.temperature > 50 }` from structured JSON logs.
  - Configured `HighTemperatureAlarm`, `IngestionErrorsAlarm`, and `ApiGateway5xxErrorsAlarm` in `template.yaml`.
  - Added SNS `TelemetryAlertsTopic` and conditional email notification subscription.
  - Added template alarm validation tests (`tests/test_template.py`, 58 total tests passed).
- [x] **Phase 8: Deployment Guide & End-to-End Acceptance**
  - Implemented end-to-end integration test suite (`tests/test_e2e_pipeline.py`, 65 total tests passed).
  - Authored comprehensive operations runbook (`docs/runbook.md`) covering SAM deployment, certificate security, and teardown.
  - Created automated security audit script (`scripts/security_audit.py`) and verified zero credentials/keys in repo.
  - Formulated Portfolio Acceptance Checklist distinguishing local verification from cloud integration.
- [ ] **Phase 9: GitHub Publishing & Credential Audit**

---

## Portfolio Acceptance Checklist

This checklist clearly distinguishes what has been verified locally versus what requires an active AWS cloud deployment:

### 1. Verified Local Functionality (100% Tested Offline)
- [x] **Telemetry Validation**: Pydantic models enforce ISO-8601 UTC timestamps, boundary limits (-40°C to 85°C), and device ID syntax (`tests/test_validation.py`).
- [x] **Sensor Simulation**: Sinusoidal diurnal temperature fluctuation, correlated humidity decay, battery discharge, and anomaly injection verified (`tests/test_simulator.py`).
- [x] **DynamoDB Data Layer**: Zero-scan $O(1)$ descending query pattern for latest reading, time-range queries, and duplicate timestamp safety verified with Moto (`tests/test_telemetry_repo.py`).
- [x] **Ingestion Handler**: Valid payloads ingested, malformed payloads rejected with 422, unparseable JSON rejected with 400, and database failures handled gracefully (`tests/test_ingest_handler.py`).
- [x] **API Gateway Handler**: `/health`, `/devices/{id}/latest`, and `/devices/{id}/history` endpoints with time ranges, limits, and opaque base64 pagination tokens verified (`tests/test_api_handler.py`).
- [x] **End-to-End Data Path**: Full flow from simulator emission through Ingestion Lambda, DynamoDB, and API query retrieval verified (`tests/test_e2e_pipeline.py`).
- [x] **Interactive Dashboard**: Responsive desktop and mobile UI with auto-refresh, dark/light themes, and Chart.js dual-axis graph verified (`frontend/`, `tests/test_mock_api_server.py`).
- [x] **Security Audit**: Automated scan confirms zero AWS access keys, private keys, or passwords committed (`scripts/security_audit.py`).

### 2. Infrastructure as Code Validation (100% Tested Offline)
- [x] **SAM Template Syntax**: `template.yaml` parses cleanly and complies with AWS Serverless Application Model schema (`tests/test_template.py`).
- [x] **Single-Table Design**: DynamoDB table configured with PK `device_id`, SK `timestamp`, `PAY_PER_REQUEST` billing, and automatic `ttl` expiration.
- [x] **Least-Privilege IAM**: Ingestion Lambda role restricted to `dynamodb:PutItem` on table ARN; Query Lambda role restricted to `dynamodb:Query` and `dynamodb:DescribeTable` (no Scan, no PutItem, no wildcards).
- [x] **Dead-Letter Routing**: IoT Topic Rule configured with SQS Dead Letter Queue for failed deliveries.
- [x] **Cost Controls**: CloudWatch log groups explicitly configured with 14-day retention to prevent unbounded storage costs.

### 3. Real-AWS Cloud Integration (Requires Active AWS Account)
- [ ] **Live SAM Deployment**: Execution of `sam build` and `sam deploy --guided` in an active AWS account.
- [ ] **Live MQTT Ingestion**: Publishing real MQTT messages to AWS IoT Core endpoint over TLS port 8883.
- [ ] **Real CloudWatch Alarms**: In-cloud metric generation and SNS email dispatch following confirmed subscription.
- [ ] **Physical ESP32 Device**: Flashing C++ (Arduino/ESP-IDF) firmware to a physical ESP32 with X.509 client certificate.

---

## CloudWatch Monitoring & Safe Alarm Testing Guide

### 1. Alarm Specifications in `template.yaml`

| Alarm Name | Monitored Metric | Namespace | Threshold | Evaluation Window | Target Action |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `HighTemperatureAlarm` | `HighTemperatureCount` (Metric Filter) | `IoTTelemetry` | $\ge 1$ | 1 period (60 sec) | SNS Alert Topic |
| `IngestionErrorsAlarm` | `Errors` | `AWS/Lambda` | $\ge 1$ | 1 period (300 sec) | SNS Alert Topic |
| `ApiGateway5xxErrorsAlarm` | `5XXError` | `AWS/ApiGateway` | $\ge 1$ | 1 period (300 sec) | SNS Alert Topic |

### 2. Local vs. Real Cloud Verification

> [!IMPORTANT]
> **Distinction Between Local and Cloud Testing**:
> - **Local Validation**: Our automated tests in `tests/test_template.py` verify that log outputs match the JSON filter pattern `{ $.temperature > 50 }` and that SAM template alarm properties (thresholds, namespaces, dimensions) are syntactically and structurally correct. This runs 100% offline with zero cloud charges.
> - **Real Cloud Verification**: Once deployed to an AWS account, CloudWatch evaluates real logs and metrics. Follow the safe testing steps below.

### 3. Safe Alarm Testing in Real AWS (Post-Deployment)

1. **Confirm SNS Email Subscription**:
   - If an `AlertEmail` parameter was supplied during deployment, AWS sends a confirmation email titled *"AWS Notification - Subscription Confirmation"*.
   - **Critical**: You must click the confirmation link in the email. Until confirmed, SNS will not deliver alarm notifications.

2. **Safe Triggering of High-Temperature Alarm**:
   Publish a single synthetic anomaly packet using the Python simulator:
   ```powershell
   python simulator/sensor_simulator.py --anomaly --count 1
   ```
   - Ingestion Lambda logs the event with `"temperature": 58.75` and `"status": "WARNING"`.
   - The Metric Filter detects `temperature > 50` and publishes `HighTemperatureCount = 1`.
   - Within 60 seconds, `HighTemperatureAlarm` transitions from `OK` to `ALARM` and triggers the SNS alert.

3. **Dry-Run Alarm Testing via AWS CLI**:
   You can safely test that notification routing and email delivery work without injecting anomalous telemetry:
   ```powershell
   # Manually transition alarm to ALARM state for testing
   aws cloudwatch set-alarm-state `
     --alarm-name iot-high-temperature-alarm-dev `
     --state-value ALARM `
     --state-reason "Testing alert notification delivery"

   # Return alarm to OK state
   aws cloudwatch set-alarm-state `
     --alarm-name iot-high-temperature-alarm-dev `
     --state-value OK `
     --state-reason "Resetting alarm state after verification"
   ```

---

## Local Development & Setup

### 1. Prerequisites
- Python 3.13 (`py -3.13`)
- Git

### 2. Environment Setup
```powershell
# Navigate to the project directory
cd aws-iot-telemetry-pipeline

# Activate the virtual environment
.venv\Scripts\Activate.ps1

# Install development dependencies
pip install -r requirements-dev.txt
```

### 3. Running the Sensor Simulator
Run the simulator locally to view generated readings:
```powershell
# Generate 5 readings at 1-second intervals
python simulator/sensor_simulator.py --count 5 --interval 1.0

# Generate a high-temperature anomaly reading
python simulator/sensor_simulator.py --count 1 --anomaly
```

### 4. Running the Interactive Dashboard (Local Mock Mode)
You can launch the dashboard locally with full live data emulation without needing an AWS account or cloud deployment:

```powershell
# Start the local mock server (serves API and Dashboard UI)
python scripts/mock_api_server.py
```

Now open your web browser to:
👉 **[http://localhost:8000/index.html](http://localhost:8000/index.html)**

**Features to test in the UI:**
1. **Device Switching**: Change between *Server Room*, *Warehouse*, and *Greenhouse* from the dropdown.
2. **Auto-Refresh**: Live updates refresh every 5 seconds (toggleable via the `Auto` button).
3. **Time-Range Queries**: Click `1h`, `6h`, `24h`, or `7d` to inspect historical dual-axis charts.
4. **Theme Switcher**: Click the 🌙 / ☀️ icon in the top right for instant Dark/Light theme switching.

---

### 5. Running Automated Unit & Mock Tests
Execute the comprehensive offline test suite across all 6 test modules:
```powershell
pytest -v
```

---

## Portfolio Screenshots to Capture

For your portfolio, GitHub README, and interview demonstrations, capture the following screenshots:
1. **Live Dashboard Overview (Dark Mode)**: Main view showing metric cards (Temperature, Humidity, Battery bar, Device Status badge) and the dual-axis historical Chart.js graph.
2. **Dashboard Overview (Light Mode)**: Demonstrate clean theme switching and responsive design.
3. **Mobile Viewport**: Use Chrome DevTools (F12 -> Responsive Device Mode) to show mobile-friendly layout and cards wrapping neatly.
4. **Active High-Temperature Alert / Anomaly**: Run `python simulator/sensor_simulator.py --anomaly` or view an anomaly state showing the `WARNING` badge in yellow/red.
5. **Network Inspection Tab**: Browser DevTools -> Network tab showing clean `GET /devices/{id}/latest` and `GET /devices/{id}/history` JSON responses with `200 OK` status and CORS headers.
6. **Passing Test Suite**: Terminal screenshot showing `54 passed in <5s` with 100% test pass rate.

---

## Troubleshooting Guide

| Issue / Symptom | Root Cause | Solution |
| :--- | :--- | :--- |
| `running scripts is disabled on this system` | Windows PowerShell default ExecutionPolicy blocks unsigned scripts. | Run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` or invoke commands directly via `.venv\Scripts\python.exe`. |
| Port `8000` is already in use | Another application or previous mock server is listening on port 8000. | Run mock server on another port: `python scripts/mock_api_server.py --port 8080`, and update `apiBaseUrl` in `frontend/config.js`. |
| Browser shows `Unable to connect to the telemetry API` | Mock API server is not running or blocked by browser adblocker. | Verify `python scripts/mock_api_server.py` is actively running in a terminal window. Ensure `http://localhost:8000/health` returns `{"status": "HEALTHY"}`. |
| Chart.js graph is blank or not showing | CDN script blocked or no historical records in time window. | Check browser console (F12) to ensure `https://cdn.jsdelivr.net/npm/chart.js` is loaded. Select a wider range (`24h` or `7d`). |
| Missing or duplicate timestamps | Clock synchronization discrepancy. | All pipeline timestamps are normalized to ISO-8601 UTC with composite sort keys (`<timestamp>#<message_id>`) to prevent collisions. |

