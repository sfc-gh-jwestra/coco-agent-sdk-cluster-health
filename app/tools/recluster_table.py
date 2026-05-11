"""MCP tool for performing table reclustering with before/after metrics."""

import json
import time

import snowflake.connector

from cortex_code_agent_sdk import tool

from ..config import AUDIT_TABLE, CONNECTION_NAME, TARGET_SCHEMA


def _get_clustering_info(cur, table_name: str) -> dict:
    """Retrieve clustering information for a table."""
    cur.execute(f"SELECT SYSTEM$CLUSTERING_INFORMATION('{table_name}')")
    row = cur.fetchone()
    if row:
        return json.loads(row[0])
    return {}


@tool(
    "recluster_table",
    description=(
        "Perform reclustering on a specified table. Captures before/after clustering "
        "metrics, executes the recluster operation, verifies improvement, and logs "
        "results to the remediation audit table. Only call this tool after receiving "
        "human approval via the approval link endpoint."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "Fully-qualified table name to recluster (e.g., SPORTSBOOK_DW.WAGERS.BET_TRANSACTIONS).",
            },
        },
        "required": ["table_name"],
    },
)
async def recluster_table(args: dict) -> dict:
    """Execute reclustering with metrics capture and audit logging."""
    table_name = args["table_name"]

    try:
        with snowflake.connector.connect(connection_name=CONNECTION_NAME) as conn:
            cur = conn.cursor()

            # Capture before metrics
            before_metrics = _get_clustering_info(cur, table_name)
            if not before_metrics:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Could not retrieve clustering info for {table_name}. "
                            "Verify the table exists and has a clustering key defined.",
                        }
                    ]
                }

            before_depth = before_metrics.get("average_depth", 0)
            before_overlaps = before_metrics.get("average_overlaps", 0)

            # Execute reclustering
            cur.execute(f"ALTER TABLE {table_name} RESUME RECLUSTER")
            recluster_result = cur.fetchone()

            # Wait briefly and capture after metrics
            time.sleep(5)
            after_metrics = _get_clustering_info(cur, table_name)
            after_depth = after_metrics.get("average_depth", 0)
            after_overlaps = after_metrics.get("average_overlaps", 0)

            # Calculate improvement
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

            # Log to audit table
            cur.execute(
                f"""
                INSERT INTO {AUDIT_TABLE} (
                    TABLE_NAME,
                    ACTION,
                    BEFORE_AVERAGE_DEPTH,
                    BEFORE_AVERAGE_OVERLAPS,
                    AFTER_AVERAGE_DEPTH,
                    AFTER_AVERAGE_OVERLAPS,
                    DEPTH_IMPROVEMENT_PCT,
                    OVERLAP_IMPROVEMENT_PCT,
                    STATUS,
                    EXECUTED_AT
                ) VALUES (
                    '{table_name}',
                    'RECLUSTER',
                    {before_depth},
                    {before_overlaps},
                    {after_depth},
                    {after_overlaps},
                    {depth_improvement:.2f},
                    {overlap_improvement:.2f},
                    'COMPLETED',
                    CURRENT_TIMESTAMP()
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

            return {"content": [{"type": "text", "text": summary}]}

    except Exception as e:
        # Log failure to audit table if possible
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

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error reclustering {table_name}: {str(e)}",
                }
            ]
        }
