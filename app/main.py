"""FastAPI application for the Cluster Health Agent service."""

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import jwt
import snowflake.connector
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .agent import run_health_check_agent, run_remediation_agent
from .config import CONNECTION_NAME
from .approval_token import validate_approval_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Enable debug logging for the cortex-code-agent-sdk internals
logging.getLogger("cortex_code_agent_sdk").setLevel(logging.DEBUG)


def _verify_snowflake_connection() -> dict:
    """Test Snowflake connectivity and return connection details."""
    try:
        with snowflake.connector.connect(connection_name=CONNECTION_NAME) as conn:
            cur = conn.cursor()
            cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE()")
            row = cur.fetchone()
            return {
                "connected": True,
                "user": row[0],
                "role": row[1],
                "warehouse": row[2],
            }
    except Exception as e:
        return {"connected": False, "error": str(e)}


def _verify_cortex_cli() -> dict:
    """Verify the cortex CLI is available and check its version."""
    import shutil
    import subprocess

    cli_path = shutil.which("cortex")
    if not cli_path:
        return {"available": False, "error": "cortex CLI not found in PATH"}
    try:
        result = subprocess.run(
            [cli_path, "--version"], capture_output=True, text=True, timeout=10
        )
        version = result.stdout.strip() or result.stderr.strip()
        return {"available": True, "path": cli_path, "version": version}
    except Exception as e:
        return {"available": True, "path": cli_path, "error": str(e)}


def _verify_connections_toml() -> dict:
    """Check that ~/.snowflake/connections.toml exists and has the expected connection."""
    config_path = Path.home() / ".snowflake" / "connections.toml"
    if not config_path.exists():
        return {"exists": False, "path": str(config_path)}
    content = config_path.read_text()
    has_default = "[default]" in content
    has_token = "/snowflake/session/token" in content
    return {
        "exists": True,
        "path": str(config_path),
        "has_default_connection": has_default,
        "has_token_file": has_token,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle — verify connectivity on boot."""
    logger.info("=" * 60)
    logger.info("STARTUP - Verifying environment")

    # Check connections.toml
    toml_status = _verify_connections_toml()
    logger.info(f"  connections.toml: {toml_status}")

    # Check cortex CLI
    cli_status = _verify_cortex_cli()
    logger.info(f"  cortex CLI: {cli_status}")

    # Check Snowflake connection
    sf_status = _verify_snowflake_connection()
    if sf_status["connected"]:
        logger.info(f"  Snowflake: connected as {sf_status['user']} "
                    f"role={sf_status['role']} wh={sf_status['warehouse']}")
    else:
        logger.error(f"  Snowflake: FAILED — {sf_status['error']}")

    logger.info("=" * 60)
    yield


app = FastAPI(
    title="Cluster Health Agent",
    description="Proactive Snowflake table clustering health monitor with CoCo Agent",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/status")
async def status():
    """Service health check endpoint with connectivity details."""
    sf_status = _verify_snowflake_connection()
    cli_status = _verify_cortex_cli()
    return {
        "status": "healthy" if sf_status.get("connected") else "degraded",
        "service": "cluster-health-agent",
        "timestamp": datetime.utcnow().isoformat(),
        "snowflake": sf_status,
        "cortex_cli": cli_status,
    }


@app.post("/health-check")
async def health_check(request: Request):
    """Trigger a cluster health check via the CoCo Agent.

    This endpoint is called by the Snowflake Task via a service function.
    Snowflake service functions require the response to be in the format:
        {"data": [[0, "result_value"]]}
    where each inner array is [row_number, return_value].
    """
    logger.info("Health check triggered")
    try:
        result = await run_health_check_agent()
        # Return in Snowflake service function response format
        response_value = json.dumps({
            "status": "completed",
            "timestamp": datetime.utcnow().isoformat(),
            "summary": result,
        })
        return JSONResponse(content={"data": [[0, response_value]]})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        error_msg = json.dumps({"status": "error", "detail": str(e)})
        return JSONResponse(content={"data": [[0, error_msg]]})


@app.post("/approve/{token}")
async def approve_remediation(token: str):
    """Handle remediation approval via signed token link.

    This endpoint is called when a user clicks the approval link in the
    notification email.
    """
    logger.info("Approval request received")

    try:
        payload = validate_approval_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=410,
            detail="This approval link has expired. Request a new health check.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail="Invalid approval token.",
        )

    table_name = payload["table_name"]
    action = payload["action"]

    if action != "recluster":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported action: {action}",
        )

    logger.info(f"Approved reclustering for {table_name}")

    try:
        result = await run_remediation_agent(table_name)
        return {
            "status": "completed",
            "table": table_name,
            "action": action,
            "timestamp": datetime.utcnow().isoformat(),
            "summary": result,
        }
    except Exception as e:
        logger.error(f"Remediation failed for {table_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/approve/{token}", response_class=HTMLResponse)
async def approve_remediation_browser(token: str):
    """Browser-friendly approval page for email link clicks.

    Shows confirmation before triggering the actual remediation.
    """
    try:
        payload = validate_approval_token(token)
    except jwt.ExpiredSignatureError:
        return HTMLResponse(
            content="<h1>Link Expired</h1><p>This approval link has expired.</p>",
            status_code=410,
        )
    except jwt.InvalidTokenError:
        return HTMLResponse(
            content="<h1>Invalid Link</h1><p>This approval link is invalid.</p>",
            status_code=401,
        )

    table_name = payload["table_name"]

    html = f"""
    <html>
    <head><title>Approve Reclustering</title></head>
    <body style="font-family: sans-serif; max-width: 600px; margin: 50px auto;">
        <h1>Approve Reclustering</h1>
        <p>You are about to approve reclustering for:</p>
        <p><strong>{table_name}</strong></p>
        <form method="POST" action="/approve/{token}">
            <button type="submit" style="padding: 12px 24px; font-size: 16px;
                    background: #0066cc; color: white; border: none; border-radius: 4px;
                    cursor: pointer;">
                Confirm Reclustering
            </button>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
