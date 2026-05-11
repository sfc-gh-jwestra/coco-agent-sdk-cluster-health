"""CoCo Agent orchestration for cluster health monitoring."""

import asyncio
import logging
import time
import traceback

import snowflake.connector

from cortex_code_agent_sdk import (
    AssistantMessage,
    CortexCodeAgentOptions,
    ResultMessage,
    create_sdk_mcp_server,
    query,
)

from .config import (
    CONNECTION_NAME,
    QUERY_HISTORY_DAYS,
    SCAN_PCT_THRESHOLD,
    TARGET_SCHEMA,
    THRESHOLD_AVERAGE_DEPTH,
    THRESHOLD_AVERAGE_OVERLAPS,
    THRESHOLD_CONSTANT_RATIO,
)
from .prompt_manager import PromptManager
from .tools.recluster_table import recluster_table
from .tools.send_notification import send_notification

logger = logging.getLogger(__name__)

# Create the SDK MCP server with both tools
cluster_tools_server = create_sdk_mcp_server(
    name="cluster-health-tools",
    version="1.0.0",
    tools=[send_notification, recluster_table],
)

HEALTH_CHECK_SYSTEM_PROMPT = PromptManager.load(
    "health_check.txt",
    TARGET_SCHEMA=TARGET_SCHEMA,
    THRESHOLD_AVERAGE_DEPTH=THRESHOLD_AVERAGE_DEPTH,
    THRESHOLD_AVERAGE_OVERLAPS=THRESHOLD_AVERAGE_OVERLAPS,
    THRESHOLD_CONSTANT_RATIO=THRESHOLD_CONSTANT_RATIO,
    QUERY_HISTORY_DAYS=QUERY_HISTORY_DAYS,
    SCAN_PCT_THRESHOLD=SCAN_PCT_THRESHOLD,
)

REMEDIATION_SYSTEM_PROMPT = PromptManager.load("remediation.txt")


# ---------------------------------------------------------------------------
# Agent runner helpers
# ---------------------------------------------------------------------------


async def _collect_response(msg_iter) -> str:
    """Drain an async iterator of SDK messages, return concatenated text."""
    text_parts: list[str] = []
    msg_count = 0
    async for msg in msg_iter:
        msg_count += 1
        logger.info(f"  [msg {msg_count}] type={type(msg).__name__}")
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
                    logger.debug(f"    text block: {block.text[:100]}...")
        elif isinstance(msg, ResultMessage):
            # Capture result text if present (agent may deliver final output here)
            if msg.result:
                text_parts.append(msg.result)
            logger.info(
                f"  ResultMessage: subtype={msg.subtype}, is_error={msg.is_error}, "
                f"num_turns={msg.num_turns}, stop_reason={msg.stop_reason}, "
                f"result_len={len(msg.result) if msg.result else 0}"
            )
            if msg.is_error:
                logger.error(
                    f"  Agent session ended with error: {msg.result}\n"
                    f"  subtype={msg.subtype}, stop_reason={msg.stop_reason}"
                )
            # Explicitly close the generator to avoid anyio cancel-scope errors
            await msg_iter.aclose()
            break
    logger.info(f"  Total messages received: {msg_count}")
    return "".join(text_parts)


def _make_options(system_prompt: str, max_turns: int = 50) -> CortexCodeAgentOptions:
    """Build agent options with standard configuration."""
    return CortexCodeAgentOptions(
        connection=CONNECTION_NAME,
        permission_mode="bypassPermissions",
        allow_dangerously_skip_permissions=True,
        mcp_servers={"cluster-health-tools": cluster_tools_server},
        append_system_prompt=system_prompt,
        max_turns=max_turns,
        # Don't inherit user/project settings — prevents auth issues in headless SPCS runs
        setting_sources=[],
        # Log CLI stderr at WARNING level so errors are visible in SPCS logs
        stderr=lambda line: logger.warning(f"[cortex-cli] {line}"),
    )


async def _run_agent_with_retry(
    prompt: str,
    options: CortexCodeAgentOptions,
    max_retries: int = 2,
    phase: str = "",
    timeout_seconds: int = 900,
    check_fn=None,
) -> str | None:
    """Run a one-shot query() agent with retries and timeout.

    Args:
        prompt: The user prompt to send to the agent.
        options: CortexCodeAgentOptions for the session.
        max_retries: Number of retry attempts after the first failure.
        phase: Label for logging (e.g., "health-check", "remediation").
        timeout_seconds: Max time per attempt before timeout.
        check_fn: Optional validation callback. Returns error string if invalid, None if OK.

    Returns:
        The agent's text response, or None if all attempts failed.
    """
    last_error: str | None = None
    t0 = time.monotonic()

    logger.info(f"[{phase}] Starting agent (timeout={timeout_seconds}s, retries={max_retries})")

    for attempt in range(max_retries + 1):
        retry_note = (
            f"\n\nPrevious attempt error:\n{last_error}\nFix the issue."
            if attempt > 0
            else ""
        )
        full_prompt = prompt + retry_note

        if attempt > 0:
            logger.info(f"[{phase}] Retry attempt {attempt + 1}/{max_retries + 1}")

        try:
            result = await asyncio.wait_for(
                _collect_response(query(prompt=full_prompt, options=options)),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            logger.error(
                f"[{phase}] Agent timed out after {timeout_seconds}s "
                f"(attempt {attempt + 1}, total elapsed {elapsed:.1f}s)"
            )
            return None
        except Exception as exc:
            logger.error(
                f"[{phase}] Error on attempt {attempt + 1}: {exc}\n"
                f"{traceback.format_exc()}"
            )
            if attempt == max_retries:
                elapsed = time.monotonic() - t0
                logger.error(f"[{phase}] All attempts exhausted after {elapsed:.1f}s")
                return None
            last_error = str(exc)
            continue

        # No validation function — accept the result
        if check_fn is None:
            elapsed = time.monotonic() - t0
            logger.info(
                f"[{phase}] Completed in {elapsed:.1f}s "
                f"(attempt {attempt + 1}, result={len(result)} chars)"
            )
            return result

        # Validate the result
        error = check_fn(result)
        if error is None:
            elapsed = time.monotonic() - t0
            logger.info(
                f"[{phase}] Completed in {elapsed:.1f}s "
                f"(attempt {attempt + 1}, result={len(result)} chars)"
            )
            return result

        last_error = error
        if attempt < max_retries:
            logger.warning(
                f"[{phase}] Validation error on attempt {attempt + 1}: {error}"
            )

    elapsed = time.monotonic() - t0
    logger.error(
        f"[{phase}] Failed after {max_retries + 1} attempts in {elapsed:.1f}s: {last_error}"
    )
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_health_check_agent() -> str:
    """Run the cluster health check agent session.

    Returns:
        Summary string of the agent's findings.
    """
    logger.info("=" * 60)
    logger.info("HEALTH CHECK AGENT - Starting")
    logger.info(f"  Connection: {CONNECTION_NAME}")
    logger.info(f"  Target schema: {TARGET_SCHEMA}")
    logger.info(f"  Thresholds: depth>{THRESHOLD_AVERAGE_DEPTH}, "
                f"overlaps>{THRESHOLD_AVERAGE_OVERLAPS}, "
                f"constant_ratio<{THRESHOLD_CONSTANT_RATIO}")
    logger.info("=" * 60)

    # Connection diagnostic — verify Snowflake connectivity before agent call
    try:
        with snowflake.connector.connect(connection_name=CONNECTION_NAME) as conn:
            cur = conn.cursor()
            cur.execute("SELECT CURRENT_ACCOUNT(), CURRENT_ROLE(), CURRENT_USER()")
            row = cur.fetchone()
            logger.info(f"  Connection OK: account={row[0]}, role={row[1]}, user={row[2]}")
    except Exception as e:
        logger.error(f"  Connection test FAILED: {e}")

    options = _make_options(HEALTH_CHECK_SYSTEM_PROMPT, max_turns=50)

    prompt = (
        f"Perform a clustering health check on all tables in {TARGET_SCHEMA}. "
        "Assess each table's clustering health, correlate with query performance, "
        "estimate costs, and send notifications for any tables needing attention."
    )

    result = await _run_agent_with_retry(
        prompt=prompt,
        options=options,
        phase="health-check",
        max_retries=2,
        timeout_seconds=900,
    )

    if result:
        logger.info(f"Health check result preview: {result[:200]}...")
    else:
        logger.error("Health check agent returned no result")

    return result or "Health check failed — see logs."


async def run_remediation_agent(table_name: str) -> str:
    """Run the remediation agent for a specific table after approval.

    Args:
        table_name: Fully-qualified table name to recluster.

    Returns:
        Summary string of the remediation results.
    """
    logger.info("=" * 60)
    logger.info(f"REMEDIATION AGENT - Starting for {table_name}")
    logger.info(f"  Connection: {CONNECTION_NAME}")
    logger.info("=" * 60)

    options = _make_options(REMEDIATION_SYSTEM_PROMPT, max_turns=20)

    prompt = (
        f"A human has approved reclustering for table: {table_name}. "
        "Please proceed with the recluster_table tool and send a follow-up "
        "notification with the results."
    )

    result = await _run_agent_with_retry(
        prompt=prompt,
        options=options,
        phase="remediation",
        max_retries=2,
        timeout_seconds=600,
    )

    if result:
        logger.info(f"Remediation result preview: {result[:200]}...")
    else:
        logger.error(f"Remediation agent returned no result for {table_name}")

    return result or "Remediation failed — see logs."
