"""MCP tool for sending email notifications via Snowflake notification integration."""

import json
import logging
import re
from pathlib import Path

import snowflake.connector

from cortex_code_agent_sdk import tool

from ..config import (
    CONNECTION_NAME,
    NOTIFICATION_INTEGRATION,
    RECIPIENTS_TABLE,
    SERVICE_BASE_URL,
)
from ..approval_token import generate_approval_token

logger = logging.getLogger(__name__)

# Load email templates at module level
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_EMAIL_TEMPLATE = (_TEMPLATES_DIR / "approval_email.html").read_text()
_BUTTON_TEMPLATE = (_TEMPLATES_DIR / "approval_button.html").read_text()


def _render_email(body: str, tables: list[str]) -> str:
    """Render the full HTML email with approval buttons for each table."""
    # Build approval buttons
    buttons_html = ""
    for table_name in tables:
        token = generate_approval_token(table_name, "recluster")
        url = f"{SERVICE_BASE_URL}/approve/{token}"
        logger.info(f"  Generated approval URL for {table_name}: {url}")
        button = _BUTTON_TEMPLATE.replace("${TABLE_NAME}", table_name)
        button = button.replace("${APPROVE_URL}", url)
        buttons_html += button

    if not buttons_html:
        buttons_html = '<p style="color: #888;">No tables require action.</p>'

    # Render the full email
    html = _EMAIL_TEMPLATE.replace("${BODY_CONTENT}", body)
    html = html.replace("${APPROVAL_BUTTONS}", buttons_html)
    return html


@tool(
    "send_notification",
    description=(
        "Send an email notification with cluster health findings to all registered "
        "recipients. Includes approval links for remediation actions. "
        "Call this after assessing table health and determining that notification is warranted."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Email subject line summarizing the health finding.",
            },
            "body": {
                "type": "string",
                "description": (
                    "HTML email body with health assessment details, metrics, "
                    "query impact analysis, and cost estimates."
                ),
            },
            "tables_needing_action": {
                "type": "array",
                "description": (
                    "List of fully-qualified table names that need reclustering "
                    "(e.g., ['SPORTSBOOK_DW.WAGERS.BET_TRANSACTIONS']). "
                    "Each table in this list will get a clickable approval link in the email. "
                    "MUST be non-empty if any table is unhealthy — this is how recipients approve remediation."
                ),
                "items": {"type": "string"},
            },
        },
        "required": ["subject", "body", "tables_needing_action"],
    },
)
async def send_notification(args: dict) -> dict:
    """Send cluster health notification email with approval links."""
    subject = args["subject"]
    body = args["body"]
    tables = args.get("tables_needing_action", [])

    logger.info(f"send_notification called: subject={subject[:50]}, tables_needing_action={tables}")

    # Fallback: if agent passed empty tables_needing_action, try to extract from body
    if not tables:
        found = re.findall(r'SPORTSBOOK_DW\.WAGERS\.\w+', body)
        exclude = {'SPORTSBOOK_DW.WAGERS.NOTIFICATION_RECIPIENTS', 'SPORTSBOOK_DW.WAGERS.REMEDIATION_AUDIT'}
        tables = list(dict.fromkeys(t for t in found if t not in exclude))
        if tables:
            logger.info(f"  Fallback: extracted tables from body: {tables}")

    if not tables:
        logger.warning("  tables_needing_action is EMPTY — no approval links will be generated")

    # Render the full HTML email from template
    full_body = _render_email(body, tables)

    # Get recipients from table
    try:
        with snowflake.connector.connect(connection_name=CONNECTION_NAME) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT EMAIL FROM {RECIPIENTS_TABLE} WHERE ACTIVE = TRUE")
            recipients = [row[0] for row in cur.fetchall()]

            if not recipients:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "No active recipients found in NOTIFICATION_RECIPIENTS table.",
                        }
                    ]
                }

            # Build the notification payload
            email_addresses = [{"email_address": r} for r in recipients]
            payload = json.dumps(
                {
                    "subject": subject,
                    "mimeType": "text/html",
                    "body": full_body,
                    "recipients": {"toAddress": email_addresses},
                }
            )

            # Send via Snowflake notification integration
            cur.execute(
                "CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION(%s, PARSE_JSON(%s))",
                (NOTIFICATION_INTEGRATION, payload),
            )

            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Notification sent successfully to {len(recipients)} "
                            f"recipient(s): {', '.join(recipients)}. "
                            f"Approval links generated for {len(tables)} table(s)."
                        ),
                    }
                ]
            }

    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error sending notification: {str(e)}",
                }
            ]
        }
