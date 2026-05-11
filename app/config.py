"""Configuration for the Cluster Health Agent."""

import os


# Snowflake connection
CONNECTION_NAME = os.environ.get("SNOWFLAKE_CONNECTION", "default")
TARGET_SCHEMA = os.environ.get("TARGET_SCHEMA", "SPORTSBOOK_DW.WAGERS")

# Notification integration
NOTIFICATION_INTEGRATION = os.environ.get(
    "NOTIFICATION_INTEGRATION", "CLUSTER_HEALTH_EMAIL_INTEGRATION"
)

# Service URL (for building approval links in emails)
SERVICE_BASE_URL = os.environ.get("SERVICE_BASE_URL", "http://localhost:8000")

# Token settings
TOKEN_SECRET_KEY = os.environ.get("TOKEN_SECRET_KEY", "change-me-in-production")
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "24"))

# Health assessment thresholds
THRESHOLD_AVERAGE_DEPTH = 5
THRESHOLD_AVERAGE_OVERLAPS = 10
THRESHOLD_CONSTANT_RATIO = 0.5
SCAN_PCT_THRESHOLD = 0.80
QUERY_HISTORY_DAYS = 7

# Tables
RECIPIENTS_TABLE = f"{TARGET_SCHEMA}.NOTIFICATION_RECIPIENTS"
AUDIT_TABLE = f"{TARGET_SCHEMA}.REMEDIATION_AUDIT"


def severity_rating(average_depth: float) -> str:
    """Assign severity rating based on average clustering depth."""
    if average_depth <= 3:
        return "Healthy"
    elif average_depth <= 10:
        return "Warning"
    elif average_depth <= 50:
        return "Degraded"
    else:
        return "Critical"
