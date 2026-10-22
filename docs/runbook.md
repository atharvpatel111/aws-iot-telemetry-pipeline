# DevOps Runbook & Operations Guide

This runbook provides complete operational guidance for building, running, testing, deploying, and destroying the Serverless IoT Telemetry Pipeline on AWS.

---

## 1. System Architecture & Topology

```
[ ESP32 / Simulator ]
         |
         | MQTT over TLS (Port 8883)
         v
[ AWS IoT Core Topic: sensors/{device_id}/telemetry ]
         |
         | IoT SQL Rule: SELECT *, topic(2) as device_id
         v
[ Telemetry Ingestion Lambda (Python 3.13) ]
    |                        |
    | PutItem                | CloudWatch Structured JSON Logs
    v                        v
[ DynamoDB Table ]      [ CloudWatch Metric Filter: { $.temperature > 50 } ]
    ^                        |
    | Query (O(1))           v
[ Query Lambda ]        [ HighTemperatureAlarm (>= 1 in 60s) ]
    ^                        |
    | Proxy                  v
[ API Gateway REST API ] [ SNS Topic: iot-telemetry-alerts ]
    ^
    | HTTP/REST
[ Responsive Web Dashboard ]
```

---

## 2. Prerequisites & Local Environment

### Required Software
- **Python**: 3.13 (`py -3.13` verified)
- **Git**: Installed
- **AWS CLI v2** *(for cloud deployment only)*: [Install AWS CLI](https://aws.amazon.com/cli/)
- **AWS SAM CLI** *(for cloud deployment only)*: [Install AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)

### Virtual Environment Setup
```powershell
# Navigate to project root
cd aws-iot-telemetry-pipeline

# Create virtual environment with Python 3.13
py -3.13 -m venv .venv

# Activate environment (PowerShell)
.\.venv\Scripts\Activate.ps1

# Install development & test dependencies
pip install -r requirements-dev.txt
```

---

## 3. Local Development & Testing Strategy (Zero-Cloud Cost)

Before deploying anything to AWS, all components can be tested locally:

### A. Run Automated Pytest Suite
```powershell
# Run all unit, mock, and E2E integration tests
python -m pytest tests -v
```

### B. Run the Local Sensor Simulator
```powershell
# Stream 5 simulated readings to console
python simulator/sensor_simulator.py --count 5 --interval 1.0

# Generate a high-temperature anomaly reading (>50°C)
python simulator/sensor_simulator.py --count 1 --anomaly
```

### C. Launch Local Mock Server & Interactive Dashboard
```powershell
# Start local mock server (serves REST API and web dashboard)
python scripts/mock_api_server.py
```
Open **`http://localhost:8000/index.html`** in any web browser.

---

## 4. Secure AWS IoT Certificate Provisioning

> [!CAUTION]
> **Credential Security**: Never commit certificates (`*.pem`, `*.crt`, `*.key`) or environment files to Git. The project `.gitignore` strictly excludes `*.pem`, `*.crt`, `*.key`, and the `simulator/certs/` directory.

When preparing to connect real or simulated hardware to AWS IoT Core:

### Step 1: Create IoT Thing & Certificate via AWS CLI
```powershell
# Create an IoT Thing
aws iot create-thing --thing-name esp32-sensor-01

# Create X.509 certificate and private key (store files directly in simulator/certs/)
aws iot create-keys-and-certificate `
  --set-as-active `
  --certificate-pem-outfile simulator/certs/certificate.pem.crt `
  --public-key-outfile simulator/certs/public.pem.key `
  --private-key-outfile simulator/certs/private.pem.key > cert_output.json

# Retrieve Amazon Root CA
curl -o simulator/certs/AmazonRootCA1.pem https://www.amazontrust.com/en/roots/notes/AmazonRootCA1.pem
```

### Step 2: Attach Least-Privilege IoT Policy
Create a policy (`iot-policy.json`) scoped strictly to publish to its own device topic:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "iot:Connect",
      "Resource": "arn:aws:iot:*:*:client/esp32-sensor-01"
    },
    {
      "Effect": "Allow",
      "Action": "iot:Publish",
      "Resource": "arn:aws:iot:*:*:topic/sensors/esp32-sensor-01/telemetry"
    }
  ]
}
```
Attach the policy to the certificate:
```powershell
aws iot create-policy --policy-name Esp32PublishPolicy --policy-document file://iot-policy.json
aws iot attach-policy --policy-name Esp32PublishPolicy --target <CERTIFICATE_ARN>
aws iot attach-thing-principal --thing-name esp32-sensor-01 --principal <CERTIFICATE_ARN>
```

---

## 5. AWS Cloud Deployment (AWS SAM)

> [!IMPORTANT]
> Do NOT execute these commands without explicit approval and an active AWS account.

### Step 1: Build SAM Application
```powershell
sam build
```
This validates template syntax and packages Python handlers and dependencies into `.aws-sam/build/`.

### Step 2: Deploy to AWS
```powershell
sam deploy --guided
```
During the interactive prompt:
- **Stack Name**: `iot-telemetry-pipeline`
- **AWS Region**: `us-east-1` (or your preferred region)
- **Parameter Environment**: `dev`
- **Parameter AlertEmail**: `your-email@example.com` (optional SNS alert recipient)
- **Confirm changes before deploy**: `Y`
- **Allow SAM CLI IAM role creation**: `Y`
- **Save arguments to configuration file**: `Y`

### Step 3: Record Stack Outputs
SAM will output:
- `TelemetryApiEndpoint`: Base URL for the REST API (e.g., `https://<api-id>.execute-api.us-east-1.amazonaws.com/dev`).
- `TelemetryTableName`: DynamoDB table name.
- `TelemetryAlertsTopicArn`: SNS alert topic ARN.

Update `frontend/config.js` with the real `apiBaseUrl`.

---

## 6. Safe Alarm Testing & Verification

1. **Confirm SNS Subscription**:
   Check your email for the confirmation link from `no-reply@sns.amazonaws.com` and click **Confirm subscription**.
2. **Safe High-Temperature Trigger**:
   Publish an anomaly packet via the simulator:
   ```powershell
   python simulator/sensor_simulator.py --anomaly --count 1
   ```
3. **Dry-Run State Transition via AWS CLI**:
   ```powershell
   aws cloudwatch set-alarm-state `
     --alarm-name iot-high-temperature-alarm-dev `
     --state-value ALARM `
     --state-reason "Verification dry-run"
   ```
   Reset when finished:
   ```powershell
   aws cloudwatch set-alarm-state `
     --alarm-name iot-high-temperature-alarm-dev `
     --state-value OK `
     --state-reason "Verification complete"
   ```

---

## 7. Complete Teardown & Cost Elimination

To completely delete cloud resources and guarantee zero ongoing charges:

### Step 1: Delete SAM CloudFormation Stack
```powershell
sam delete --stack-name iot-telemetry-pipeline
```
This automatically deletes:
- DynamoDB table `iot-telemetry-dev`
- Ingestion Lambda & Query Lambda functions
- API Gateway REST API
- CloudWatch Log Groups & Alarms
- IoT Topic Rule
- SQS Dead Letter Queue
- SNS Topic

### Step 2: Clean Up Resources Created Outside the Stack
The following resources are created independently and must be cleaned up manually if created:
1. **IoT Thing & Certificate**:
   ```powershell
   # Detach and delete certificate
   aws iot detach-thing-principal --thing-name esp32-sensor-01 --principal <CERT_ARN>
   aws iot detach-policy --policy-name Esp32PublishPolicy --target <CERT_ARN>
   aws iot update-certificate --certificate-id <CERT_ID> --new-status INACTIVE
   aws iot delete-certificate --certificate-id <CERT_ID>
   aws iot delete-policy --policy-name Esp32PublishPolicy
   aws iot delete-thing --thing-name esp32-sensor-01
   ```
2. **SAM Deployment S3 Bucket**:
   Empty and delete the deployment artifacts bucket if no longer needed.
