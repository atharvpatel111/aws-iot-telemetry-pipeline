"""Automated validation tests for AWS SAM infrastructure-as-code template.

Ensures that template.yaml strictly complies with:
- Single-table DynamoDB schema (PK: device_id, SK: timestamp, TTL: ttl)
- Python 3.13 Lambda runtime, 128MB memory, 15s timeout
- AWS IoT Core rule with topic(2) SQL extraction
- Least-privilege IAM policies without wildcards
- Explicit 14-day CloudWatch log retention
- Scoped Lambda permission policy for IoT Core
- Dead Letter Queue error routing
"""

import json
from pathlib import Path
from typing import Any, Dict
import pytest
import yaml

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "template.yaml"


class CloudFormationLoader(yaml.SafeLoader):
    """YAML loader that understands CloudFormation / SAM intrinsic shorthand tags."""
    pass


def _cfn_tag_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> Dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        return {f"Fn::{tag_suffix}": loader.construct_scalar(node)}
    elif isinstance(node, yaml.SequenceNode):
        return {f"Fn::{tag_suffix}": [loader.construct_object(child, deep=True) for child in node.value]}
    elif isinstance(node, yaml.MappingNode):
        return {f"Fn::{tag_suffix}": loader.construct_mapping(node)}
    return {f"Fn::{tag_suffix}": None}


# Register CloudFormation shorthand tags including condition functions
CFN_TAGS = [
    "Ref", "Sub", "GetAtt", "Join", "Select", "Split", "FindInMap",
    "Base64", "ImportValue", "Not", "Equals", "And", "Or", "Condition", "If"
]
for tag in CFN_TAGS:
    CloudFormationLoader.add_constructor(f"!{tag}", lambda loader, node, t=tag: _cfn_tag_constructor(loader, t, node))


@pytest.fixture(scope="module")
def sam_template() -> Dict[str, Any]:
    """Parse and return template.yaml."""
    assert TEMPLATE_PATH.exists(), f"template.yaml not found at {TEMPLATE_PATH}"
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        data = yaml.load(f, Loader=CloudFormationLoader)
    assert isinstance(data, dict), "Parsed template must be a dictionary"
    return data


def test_template_header_and_transform(sam_template: Dict[str, Any]):
    """Verify SAM template header and transform."""
    assert sam_template.get("AWSTemplateFormatVersion") == "2010-09-09"
    assert sam_template.get("Transform") == "AWS::Serverless-2016-10-31"
    assert "Resources" in sam_template


def test_dynamodb_table_specification(sam_template: Dict[str, Any]):
    """Requirement 1: Verify DynamoDB table keys, types, billing mode, and TTL."""
    resources = sam_template["Resources"]
    assert "TelemetryTable" in resources, "TelemetryTable resource missing"

    table = resources["TelemetryTable"]
    assert table["Type"] == "AWS::DynamoDB::Table"
    props = table["Properties"]

    # Billing Mode
    assert props.get("BillingMode") == "PAY_PER_REQUEST"

    # Key Schema
    key_schema = {k["AttributeName"]: k["KeyType"] for k in props["KeySchema"]}
    assert key_schema.get("device_id") == "HASH", "Partition key must be device_id (HASH)"
    assert key_schema.get("timestamp") == "RANGE", "Sort key must be timestamp (RANGE)"

    # Attribute Definitions
    attr_defs = {a["AttributeName"]: a["AttributeType"] for a in props["AttributeDefinitions"]}
    assert attr_defs.get("device_id") == "S", "device_id must be String (S)"
    assert attr_defs.get("timestamp") == "S", "timestamp must be String (S)"

    # TTL Specification
    ttl_spec = props.get("TimeToLiveSpecification")
    assert ttl_spec is not None, "TimeToLiveSpecification must be configured"
    assert ttl_spec.get("AttributeName") == "ttl", "TTL attribute must be 'ttl'"
    assert ttl_spec.get("Enabled") is True, "TTL must be enabled"


def test_ingestion_lambda_specification(sam_template: Dict[str, Any]):
    """Requirement 2: Verify Lambda runtime, memory, timeout, and environment variables."""
    resources = sam_template["Resources"]
    assert "IngestionFunction" in resources, "IngestionFunction resource missing"

    func = resources["IngestionFunction"]
    assert func["Type"] in ["AWS::Serverless::Function", "AWS::Lambda::Function"]
    props = func["Properties"]

    assert props.get("Runtime") == "python3.13", "Lambda runtime must be python3.13"
    assert props.get("MemorySize") == 128, "MemorySize must be 128 MB"
    assert props.get("Timeout") == 15, "Timeout must be 15 seconds"
    assert props.get("Handler") == "src.handlers.ingest_handler.lambda_handler"

    # Environment variables
    env_vars = props.get("Environment", {}).get("Variables", {})
    assert "TELEMETRY_TABLE_NAME" in env_vars, "TELEMETRY_TABLE_NAME must be set in environment"


def test_cloudwatch_log_retention(sam_template: Dict[str, Any]):
    """Requirement 5: Verify explicit 14-day CloudWatch log retention."""
    resources = sam_template["Resources"]
    assert "IngestionFunctionLogGroup" in resources, "IngestionFunctionLogGroup missing"

    log_group = resources["IngestionFunctionLogGroup"]
    assert log_group["Type"] == "AWS::Logs::LogGroup"
    props = log_group["Properties"]

    # Retention should reference LogRetentionInDays parameter (default 14) or explicitly 14
    retention = props.get("RetentionInDays")
    if isinstance(retention, dict) and "Fn::Ref" in retention:
        param_name = retention["Fn::Ref"]
        param_default = sam_template["Parameters"][param_name]["Default"]
        assert param_default == 14, "Default log retention must be 14 days"
    else:
        assert retention == 14, "Log retention must be 14 days"


def test_least_privilege_lambda_iam_role(sam_template: Dict[str, Any]):
    """Requirement 4: Verify Lambda execution role has least-privilege policies (no wildcard actions or admin)."""
    resources = sam_template["Resources"]
    assert "IngestionFunctionRole" in resources, "IngestionFunctionRole missing"

    role = resources["IngestionFunctionRole"]
    assert role["Type"] == "AWS::IAM::Role"
    policies = role["Properties"]["Policies"]

    all_actions = []
    has_dynamodb_put_only = False
    has_cloudwatch_logging_only = False

    for p in policies:
        statements = p["PolicyDocument"]["Statement"]
        for stmt in statements:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            all_actions.extend(actions)

            # Check DynamoDB policy
            if any("dynamodb" in a for a in actions):
                assert actions == ["dynamodb:PutItem"], f"DynamoDB policy should only grant PutItem, found {actions}"
                # Target must not be wildcard
                resource = stmt.get("Resource")
                assert resource != "*", "DynamoDB resource must NOT be wildcard '*'"
                has_dynamodb_put_only = True

            # Check CloudWatch Logs policy
            if any("logs" in a for a in actions):
                assert set(actions).issubset({"logs:CreateLogStream", "logs:PutLogEvents"}), f"Unexpected logs actions: {actions}"
                resource = stmt.get("Resource")
                assert resource != "*", "CloudWatch Logs resource must NOT be wildcard '*'"
                has_cloudwatch_logging_only = True

    # Critical assertions
    assert "*" not in all_actions, "Wildcard action '*' is strictly prohibited"
    assert has_dynamodb_put_only, "Role must contain scoped DynamoDB PutItem policy"
    assert has_cloudwatch_logging_only, "Role must contain scoped CloudWatch logging policy"


def test_iot_topic_rule_and_sql_extraction(sam_template: Dict[str, Any]):
    """Requirements 3 & 7: Verify IoT topic rule, topic pattern, and SQL topic(2) device_id extraction."""
    resources = sam_template["Resources"]
    assert "TelemetryIoTRule" in resources, "TelemetryIoTRule missing"

    rule = resources["TelemetryIoTRule"]
    assert rule["Type"] == "AWS::IoT::TopicRule"
    payload = rule["Properties"]["TopicRulePayload"]

    sql = payload.get("Sql", "")
    assert "FROM 'sensors/+/telemetry'" in sql, f"SQL must target topic 'sensors/+/telemetry', found: {sql}"
    assert "topic(2)" in sql and "device_id" in sql, f"SQL must extract device_id using topic(2), found: {sql}"

    # Action targets Ingestion Lambda
    actions = payload.get("Actions", [])
    assert len(actions) >= 1, "IoT rule must have at least one action"
    assert "Lambda" in actions[0], "Primary IoT rule action must be Lambda"

    # Dead Letter Queue error action
    error_action = payload.get("ErrorAction")
    assert error_action is not None, "IoT Rule must configure an ErrorAction (DLQ)"
    assert "Sqs" in error_action, "ErrorAction must route to SQS DLQ"


def test_lambda_resource_permission_scoped_to_iot_rule(sam_template: Dict[str, Any]):
    """Requirement 6: Verify Lambda permission restricts invocation to the specific IoT rule and account."""
    resources = sam_template["Resources"]
    assert "IoTRuleLambdaPermission" in resources, "IoTRuleLambdaPermission missing"

    perm = resources["IoTRuleLambdaPermission"]
    assert perm["Type"] == "AWS::Lambda::Permission"
    props = perm["Properties"]

    assert props.get("Action") == "lambda:InvokeFunction"
    assert props.get("Principal") == "iot.amazonaws.com"
    assert "SourceAccount" in props, "SourceAccount must be restricted to prevent confused deputy"
    assert "SourceArn" in props, "SourceArn must be restricted to the specific IoT Rule ARN"


def test_api_gateway_and_query_lambda_specification(sam_template: Dict[str, Any]):
    """Phase 5 Requirement: Verify API Gateway, QueryFunction routes, and least-privilege read IAM."""
    resources = sam_template["Resources"]

    # 1. API Gateway
    assert "TelemetryApi" in resources, "TelemetryApi resource missing"
    api_props = resources["TelemetryApi"]["Properties"]
    assert "Cors" in api_props, "API Gateway must have CORS enabled"

    # 2. Query Function
    assert "QueryFunction" in resources, "QueryFunction resource missing"
    query_props = resources["QueryFunction"]["Properties"]
    assert query_props.get("Runtime") == "python3.13"
    assert query_props.get("Handler") == "src.handlers.api_handler.lambda_handler"
    assert query_props.get("MemorySize") == 128
    assert query_props.get("Timeout") == 15

    # Verify API Routes
    events = query_props.get("Events", {})
    route_paths = {e["Properties"]["Path"] for e in events.values() if e.get("Type") == "Api"}
    assert "/health" in route_paths
    assert "/devices/{device_id}/latest" in route_paths
    assert "/devices/{device_id}/history" in route_paths

    # 3. Query IAM Role (Least Privilege Read-Only)
    assert "QueryFunctionRole" in resources, "QueryFunctionRole missing"
    role_props = resources["QueryFunctionRole"]["Properties"]
    policies = role_props.get("Policies", [])

    has_query_only = False
    for p in policies:
        statements = p["PolicyDocument"]["Statement"]
        for stmt in statements:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            if any("dynamodb" in a for a in actions):
                assert "dynamodb:Scan" not in actions, "Scan permission is strictly forbidden for Query Lambda"
                assert "dynamodb:PutItem" not in actions, "PutItem permission is forbidden for Query Lambda"
                assert "dynamodb:Query" in actions, "Must include dynamodb:Query"
                has_query_only = True
                assert stmt.get("Resource") != "*", "DynamoDB resource must not be wildcard"

    assert has_query_only, "Query Lambda must have scoped DynamoDB read policy"


def test_cloudwatch_metric_filter_specification(sam_template: Dict[str, Any]):
    """Phase 7 Requirement 1: Verify metric filter extracting temperature > 50 from JSON logs."""
    resources = sam_template["Resources"]
    assert "HighTemperatureMetricFilter" in resources, "HighTemperatureMetricFilter resource missing"

    mf = resources["HighTemperatureMetricFilter"]
    assert mf["Type"] == "AWS::Logs::MetricFilter"
    props = mf["Properties"]

    assert props.get("FilterPattern") == "{ $.temperature > 50 }", "FilterPattern must match '{ $.temperature > 50 }'"
    trans = props.get("MetricTransformations", [])
    assert len(trans) == 1, "Must have exactly 1 metric transformation"

    t = trans[0]
    assert t.get("MetricNamespace") == "IoTTelemetry"
    assert t.get("MetricName") == "HighTemperatureCount"
    assert t.get("MetricValue") == "1"
    assert t.get("DefaultValue") == 0


def test_metric_filter_pattern_against_representative_logs():
    """Phase 7 Requirement 1: Test metric filter logic against real structured JSON log output."""
    import logging
    from src.common.logger import JSONFormatter

    formatter = JSONFormatter()

    # 1. Normal log event (temperature 23.5)
    rec_normal = logging.LogRecord(
        name="ingest_handler",
        level=logging.INFO,
        pathname="ingest_handler.py",
        lineno=50,
        msg="Telemetry ingested",
        args=(),
        exc_info=None,
    )
    rec_normal.device_id = "esp32-sensor-01"
    rec_normal.extra_data = {"temperature": 23.5, "humidity": 48.0, "status": "OK"}
    log_normal = json.loads(formatter.format(rec_normal))

    # 2. Anomaly log event (temperature 58.75)
    rec_anomaly = logging.LogRecord(
        name="ingest_handler",
        level=logging.WARNING,
        pathname="ingest_handler.py",
        lineno=50,
        msg="Telemetry anomaly detected",
        args=(),
        exc_info=None,
    )
    rec_anomaly.device_id = "esp32-sensor-01"
    rec_anomaly.extra_data = {"temperature": 58.75, "humidity": 22.1, "status": "WARNING"}
    log_anomaly = json.loads(formatter.format(rec_anomaly))

    # Evaluate CloudWatch JSON filter rule: { $.temperature > 50 }
    def evaluate_cw_temp_filter(log_json: dict) -> bool:
        return "temperature" in log_json and log_json["temperature"] > 50

    assert evaluate_cw_temp_filter(log_normal) is False, "Normal temperature must not match filter"
    assert evaluate_cw_temp_filter(log_anomaly) is True, "Anomaly temperature (>50°C) must trigger metric filter"


def test_cloudwatch_alarms_specification(sam_template: Dict[str, Any]):
    """Phase 7 Requirements 2, 3, 4: Validate HighTemperatureAlarm, IngestionErrorsAlarm, and ApiGateway5xxErrorsAlarm."""
    resources = sam_template["Resources"]

    # 1. HighTemperatureAlarm
    assert "HighTemperatureAlarm" in resources, "HighTemperatureAlarm missing"
    ht_alarm = resources["HighTemperatureAlarm"]["Properties"]
    assert ht_alarm.get("MetricName") == "HighTemperatureCount"
    assert ht_alarm.get("Namespace") == "IoTTelemetry"
    assert ht_alarm.get("Threshold") == 1
    assert ht_alarm.get("ComparisonOperator") == "GreaterThanOrEqualToThreshold"
    assert ht_alarm.get("Period") == 60
    assert ht_alarm.get("EvaluationPeriods") == 1
    assert "AlarmActions" in ht_alarm

    # 2. IngestionErrorsAlarm
    assert "IngestionErrorsAlarm" in resources, "IngestionErrorsAlarm missing"
    ie_alarm = resources["IngestionErrorsAlarm"]["Properties"]
    assert ie_alarm.get("MetricName") == "Errors"
    assert ie_alarm.get("Namespace") == "AWS/Lambda"
    assert ie_alarm.get("Threshold") == 1
    assert ie_alarm.get("ComparisonOperator") == "GreaterThanOrEqualToThreshold"
    assert ie_alarm.get("Period") == 300
    dims = {d["Name"]: d["Value"] for d in ie_alarm.get("Dimensions", [])}
    assert "FunctionName" in dims

    # 3. ApiGateway5xxErrorsAlarm
    assert "ApiGateway5xxErrorsAlarm" in resources, "ApiGateway5xxErrorsAlarm missing"
    api_alarm = resources["ApiGateway5xxErrorsAlarm"]["Properties"]
    assert api_alarm.get("MetricName") == "5XXError"
    assert api_alarm.get("Namespace") == "AWS/ApiGateway"
    assert api_alarm.get("Threshold") == 1
    assert api_alarm.get("ComparisonOperator") == "GreaterThanOrEqualToThreshold"
    api_dims = {d["Name"]: d["Value"] for d in api_alarm.get("Dimensions", [])}
    assert "ApiName" in api_dims
    assert "Stage" in api_dims


def test_sns_alerts_topic_and_subscription(sam_template: Dict[str, Any]):
    """Phase 7 Requirement 2: Verify SNS topic and conditional email subscription."""
    resources = sam_template["Resources"]
    assert "TelemetryAlertsTopic" in resources, "TelemetryAlertsTopic missing"
    assert resources["TelemetryAlertsTopic"]["Type"] == "AWS::SNS::Topic"

    assert "AlertEmailSubscription" in resources, "AlertEmailSubscription missing"
    sub = resources["AlertEmailSubscription"]
    assert sub["Type"] == "AWS::SNS::Subscription"
    assert sub.get("Condition") == "HasAlertEmail"
    assert sub["Properties"].get("Protocol") == "email"


