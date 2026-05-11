# Cluster Health Agent

An autonomous Snowflake agent that monitors table clustering health, detects degradation, and orchestrates remediation with human-in-the-loop approval via email notifications.

Built with the [Cortex Code Agent SDK](https://docs.snowflake.com/en/developer-guide/cortex-code/agent-sdk) and deployed as a Snowpark Container Service (SPCS).

## How It Works

1. A scheduled Snowflake Task triggers the agent every 5 minutes
2. The agent queries `SYSTEM$CLUSTERING_INFORMATION` for monitored tables
3. If clustering depth or overlap exceeds thresholds, it composes a remediation plan
4. An email notification is sent to configured recipients with an approval link
5. Upon approval, the agent executes `ALTER TABLE ... RECLUSTER` and logs results to an audit table

## Project Structure

```
app/
  main.py          — FastAPI server with /health-check and /approve endpoints
  agent.py         — Cortex Code agent logic (health assessment + remediation)
  config.py        — Environment-driven configuration
  token.py         — JWT token generation/validation for approval links
  tools/
    recluster_table.py    — Tool: executes ALTER TABLE RECLUSTER
    send_notification.py  — Tool: sends email via Snowflake notification integration
deploy/
  Dockerfile       — Container image definition
  entrypoint.sh    — Generates Snowflake connection config and starts uvicorn
  service_spec.yaml — SPCS service specification
  setup.sql        — Creates all required Snowflake objects
```

## Quick Start

### 1. Clone the repository

```bash
git clone <this-repo-url>
cd coco-agent-sdk-cluster-remedy-demo
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your Snowflake account details
```

Key values to set:
- `SNOWFLAKE_ACCOUNT` / `SNOWFLAKE_HOST` — your Snowflake account
- `NOTIFICATION_EMAIL` — a verified email for receiving alerts
- `TOKEN_SECRET_KEY` — generate with `openssl rand -hex 32`

### 3. Create Snowflake objects

Edit `deploy/setup.sql` to replace `<YOUR_VERIFIED_EMAIL>` with your email, then execute the script in a Snowflake worksheet or via SnowSQL.

### 4. Build and deploy

See [deploy-steps.md](deploy-steps.md) for the full step-by-step deployment guide covering:
- Docker image build
- Push to Snowflake image registry
- SPCS service creation
- Scheduled task setup
- End-to-end verification

## Local Development

```bash
pip install -r requirements.txt

# Run locally (uses default Snowflake connection from ~/.snowflake/connections.toml)
uvicorn app.main:app --reload --port 8000
```

## Requirements

- Python 3.11+
- Snowflake account with SPCS enabled
- `snow` CLI configured with a valid connection
- Docker (for building the container image)

## License

Internal / demo project.
