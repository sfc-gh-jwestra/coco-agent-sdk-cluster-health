* Here are the REST API endpoints exposed by the Python app running in SPCS:

  ────────────────────────────────────────

  REST API Endpoints

  1. POST /health-check

  Purpose: Triggers the CoCo Agent to perform a full clustering health assessment cycle.

  Called by: Snowflake Task (every 5 minutes)

  Flow:

  1. Instantiates CortexCodeSDKClient with the agent system prompt and MCP tools
  2. Sends a prompt instructing the agent to: discover tables → assess clustering health → correlate with query performance → estimate costs → send notification email (with
   approval links for Degraded/Critical tables)
  3. Returns a summary of findings

  Request: No body required (or empty {})

  Response:

    {
      "status": "completed",
      "tables_assessed": 3,
      "tables_healthy": 1,
      "tables_warning": 1,
      "tables_critical": 1,
      "notifications_sent": true
    }

  Why: This is the main entry point for the scheduled health monitoring loop. The Snowflake Task calls this endpoint every 5 minutes to keep clustering health under
  continuous surveillance.

  ────────────────────────────────────────

  2. POST /approve/{token}

  Purpose: Validates a human-in-the-loop approval and triggers reclustering for a specific table.

  Called by: Human clicking the approval link in the notification email

  Flow:

  1. Validates the signed, time-limited token (checks expiry, signature, table name)
  2. Looks up the corresponding REMEDIATION_AUDIT record
  3. If valid: instantiates CortexCodeSDKClient and prompts the agent to recluster the specified table using the recluster_table tool
  4. After reclustering: agent re-assesses clustering health and sends a before/after comparison email
  5. If invalid/expired: returns an error response, no action taken

  Request: Token is in the URL path. No body required.

  Response (success):

    {
      "status": "approved",
      "table": "SPORTSBOOK_DW.WAGERS.BET_TRANSACTIONS",
      "recluster_initiated": true,
      "message": "Reclustering started. You will receive a comparison email upon completion."
    }

  Response (failure):

    {
      "status": "denied",
      "reason": "Token expired or invalid"
    }

  Why: This is the human-in-the-loop gate. It ensures remediation NEVER happens without explicit human authorization. The email link provides a frictionless approval UX —
  one click to approve from the notification email.

  ────────────────────────────────────────

  3. GET /status

  Purpose: Health/liveness check for the SPCS service itself.

  Called by: SPCS runtime (liveness probe), monitoring, or manual checks

  Response:

    {
      "status": "healthy",
      "service": "cluster-health-agent",
      "version": "1.0.0"
    }

  Why: Required by SPCS for container health monitoring. Also useful for debugging whether the service is running before the Task fires.

  ────────────────────────────────────────

  Summary Table

  ┌──────────────────┬────────┬────────────────────────┬─────────────────────────────────────────────────┐
  │ Endpoint         │ Method │ Called By              │ Purpose                                         │
  ├──────────────────┼────────┼────────────────────────┼─────────────────────────────────────────────────┤
  │ /health-check    │ POST   │ Snowflake Task (5 min) │ Trigger full clustering health assessment cycle │
  ├──────────────────┼────────┼────────────────────────┼─────────────────────────────────────────────────┤
  │ /approve/{token} │ POST   │ Human (email link)     │ Validate approval, trigger reclustering         │
  ├──────────────────┼────────┼────────────────────────┼─────────────────────────────────────────────────┤
  │ /status          │ GET    │ SPCS liveness probe    │ Service health check                            │
  └──────────────────┴────────┴────────────────────────┴─────────────────────────────────────────────────┘

  ────────────────────────────────────────

  The key design point: the app itself is stateless — all state lives in Snowflake tables (REMEDIATION_AUDIT, NOTIFICATION_RECIPIENTS). The REST endpoints just orchestrate
  CoCo Agent sessions and validate approval tokens. The agent does the actual work (querying clustering info, correlating performance, sending notifications) via its MCP
  tools.