"""Standalone stdio MCP server for cluster health tools.

This script is launched by the cortex CLI as a subprocess via the stdio transport.
It exposes send_notification and recluster_table tools over the MCP protocol.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import jwt
import snowflake.connector
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

CONNECTION_NAME = os.environ.get("SNOWFLAKE_CONNECTION", "default")
TARGET_SCHEMA = os.environ.get("TARGET_SCHEMA", "SPORTSBOOK_DW.WAGERS")
NOTIFICATION_INTEGRATION = os.environ.get(
    "NOTIFICATION_INTEGRATION", "CLUSTER_HEALTH_EMAIL_INTEGRATION"
)
SERVICE_BASE_URL = os.environ.get("SERVICE_BASE_URL", "http://localhost:8000")
TOKEN_SECRET_KEY = os.environ.get("TOKEN_SECRET_KEY", "change-me-in-production")
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "24"))
RECIPIENTS_TABLE = f"{TARGET_SCHEMA}.NOTIFICATION_RECIPIENTS"
AUDIT_TABLE = f"{TARGET_SCHEMA}.REMEDIATION_AUDIT"
WAREHOUSE = os.environ.get("SNOWFLAKE_WAREHOUSE", "CLUSTER_HEALTH_WH")

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_EMAIL_TEMPLATE = (_TEMPLATES_DIR / "approval_email.html").read_text()
_BUTTON_TEMPLATE = (_TEMPLATES_DIR / "approval_button.html").read_text()

server = Server("cluster-health-tools", version="1.0.0")


def _generate_approval_token(table_name: str, action: str = "recluster") -> str:
    payload = {
        "table_name": table_name,
        "action": action,
        "iat": int(time.time()),
        "exp": int(time.time()) + (TOKEN_TTL_HOURS * 3600),
    }
    return jwt.encode(payload, TOKEN_SECRET_KEY, algorithm="HS256")


def _render_email(body: str, tables: list[str]) -> str:
    buttons_html = ""
    for table_name in tables:
        token = _generate_approval_token(table_name, "recluster")
        url = f"{SERVICE_BASE_URL}/approve/{token}"
        logger.info(f"  Generated approval URL for {table_name}: {url}")
        button = _BUTTON_TEMPLATE.replace("${TABLE_NAME}", table_name)
        button = button.replace("${APPROVE_URL}", url)
        buttons_html += button

    if not buttons_html:
        buttons_html = '<p style="color: #888;">No tables require action.</p>'

    html = _EMAIL_TEMPLATE.replace("${BODY_CONTENT}", body)
    html = html.replace("${APPROVAL_BUTTONS}", buttons_html)
    return html


def _get_clustering_info(cur, table_name: str) -> dict:
    cur.execute(f"SELECT SYSTEM$CLUSTERING_INFORMATION('{table_name}')")
    row = cur.fetchone()
    if row:
        return json.loads(row[0])
    return {}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="send_notification",
            description=(
                "Send an email notification with cluster health findings to all registered "
                "recipients. Includes approval links for remediation actions. "
                "Call this after assessing table health and determining that notification is warranted."
            ),
            inputSchema={
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
                            "MUST be non-empty if any table is unhealthy."
                        ),
                        "items": {"type": "string"},
                    },
                },
                "required": ["subject", "body", "tables_needing_action"],
            },
        ),
        Tool(
            name="recluster_table",
            description=(
                "Perform reclustering on a specified table. Captures before/after clustering "
                "metrics, executes the recluster operation, verifies improvement, and logs "
                "results to the remediation audit table. Only call this tool after receiving "
                "human approval via the approval link endpoint."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Fully-qualified table name to recluster (e.g., SPORTSBOOK_DW.WAGERS.BET_TRANSACTIONS).",
                    },
                },
                "required": ["table_name"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "send_notification":
        return await _handle_send_notification(arguments)
    elif name == "recluster_table":
        return await _handle_recluster_table(arguments)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _handle_send_notification(args: dict) -> list[TextContent]:
    subject = args["subject"]
    body = args["body"]
    tables = args.get("tables_needing_action", [])

    logger.info(f"send_notification called: subject={subject[:50]}, tables_needing_action={tables}")

    if not tables:
        found = re.findall(r'SPORTSBOOK_DW\.WAGERS\.\w+', body)
        exclude = {'SPORTSBOOK_DW.WAGERS.NOTIFICATION_RECIPIENTS', 'SPORTSBOOK_DW.WAGERS.REMEDIATION_AUDIT'}
        tables = list(dict.fromkeys(t for t in found if t not in exclude))
        if tables:
            logger.info(f"  Fallback: extracted tables from body: {tables}")

    if not tables:
        logger.warning("  tables_needing_action is EMPTY — no approval links will be generated")

    full_body = _render_email(body, tables)

    try:
        with snowflake.connector.connect(connection_name=CONNECTION_NAME) as conn:
            cur = conn.cursor()
            cur.execute(f"USE WAREHOUSE {WAREHOUSE}")
            cur.execute(f"SELECT EMAIL FROM {RECIPIENTS_TABLE} WHERE ACTIVE = TRUE")
            recipients = [row[0] for row in cur.fetchall()]

            if not recipients:
                return [TextContent(type="text", text="No active recipients found in NOTIFICATION_RECIPIENTS table.")]

            email_addresses = [{"email_address": r} for r in recipients]
            payload = json.dumps(
                {
                    "subject": subject,
                    "mimeType": "text/html",
                    "body": full_body,
                    "recipients": {"toAddress": email_addresses},
                }
            )

            cur.execute(
                "CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION(%s, PARSE_JSON(%s))",
                (NOTIFICATION_INTEGRATION, payload),
            )

            return [TextContent(
                type="text",
                text=(
                    f"Notification sent successfully to {len(recipients)} "
                    f"recipient(s): {', '.join(recipients)}. "
                    f"Approval links generated for {len(tables)} table(s)."
                ),
            )]

    except Exception as e:
        return [TextContent(type="text", text=f"Error sending notification: {str(e)}")]


async def _handle_recluster_table(args: dict) -> list[TextContent]:
    table_name = args["table_name"]

    try:
        with snowflake.connector.connect(connection_name=CONNECTION_NAME) as conn:
            cur = conn.cursor()
            cur.execute(f"USE WAREHOUSE {WAREHOUSE}")

            before_metrics = _get_clustering_info(cur, table_name)
            if not before_metrics:
                return [TextContent(
                    type="text",
                    text=f"Could not retrieve clustering info for {table_name}. "
                    "Verify the table exists and has a clustering key defined.",
                )]

            before_depth = before_metrics.get("average_depth", 0)
            before_overlaps = before_metrics.get("average_overlaps", 0)

            cur.execute(f"ALTER TABLE {table_name} RESUME RECLUSTER")
            cur.fetchone()

            time.sleep(5)
            after_metrics = _get_clustering_info(cur, table_name)
            after_depth = after_metrics.get("average_depth", 0)
            after_overlaps = after_metrics.get("average_overlaps", 0)

            depth_improvement = (
                ((before_depth - after_depth) / before_depth * 100)
                if before_depth > 0
                else 0
            )
            overlap_improvement = (
                ((before_overlaps - after_overlaps) / before_overlaps * 100)
                if before_overlaps > 0
                else 0
            )

            cur.execute(
                f"""
                INSERT INTO {AUDIT_TABLE} (
                    TABLE_NAME, ACTION,
                    BEFORE_AVERAGE_DEPTH, BEFORE_AVERAGE_OVERLAPS,
                    AFTER_AVERAGE_DEPTH, AFTER_AVERAGE_OVERLAPS,
                    DEPTH_IMPROVEMENT_PCT, OVERLAP_IMPROVEMENT_PCT,
                    STATUS, EXECUTED_AT
                ) VALUES (
                    '{table_name}', 'RECLUSTER',
                    {before_depth}, {before_overlaps},
                    {after_depth}, {after_overlaps},
                    {depth_improvement:.2f}, {overlap_improvement:.2f},
                    'COMPLETED', CURRENT_TIMESTAMP()
                )
                """
            )

            summary = (
                f"Reclustering completed for {table_name}.\n\n"
                f"**Before:**\n"
                f"- Average Depth: {before_depth}\n"
                f"- Average Overlaps: {before_overlaps}\n\n"
                f"**After:**\n"
                f"- Average Depth: {after_depth}\n"
                f"- Average Overlaps: {after_overlaps}\n\n"
                f"**Improvement:**\n"
                f"- Depth: {depth_improvement:.1f}% reduction\n"
                f"- Overlaps: {overlap_improvement:.1f}% reduction\n\n"
                f"Results logged to {AUDIT_TABLE}."
            )

            return [TextContent(type="text", text=summary)]

    except Exception as e:
        try:
            with snowflake.connector.connect(connection_name=CONNECTION_NAME) as conn:
                cur = conn.cursor()
                error_msg = str(e).replace("'", "''")
                cur.execute(
                    f"""
                    INSERT INTO {AUDIT_TABLE} (
                        TABLE_NAME, ACTION, STATUS, ERROR_MESSAGE, EXECUTED_AT
                    ) VALUES (
                        '{table_name}', 'RECLUSTER', 'FAILED',
                        '{error_msg}', CURRENT_TIMESTAMP()
                    )
                    """
                )
        except Exception:
            pass

        return [TextContent(type="text", text=f"Error reclustering {table_name}: {str(e)}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
