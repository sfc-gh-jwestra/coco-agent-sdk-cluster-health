# Cluster Health Agent — Deployment Steps

## Prerequisites

- Docker installed locally
- `snow` CLI installed and configured with your Snowflake connection
- ACCOUNTADMIN role (or equivalent privileges for creating objects)
- A verified email address in Snowflake for notifications

---

## Step 1: Create Database Objects

Edit `deploy/setup.sql` — replace `<YOUR_VERIFIED_EMAIL>` with your actual verified email address in both the `NOTIFICATION_RECIPIENTS` insert and the `ALLOWED_RECIPIENTS` list on the notification integration.

Then run the full script:

```sql
-- Execute deploy/setup.sql in a Snowflake worksheet or via SnowSQL
-- This creates: database, schema, 3 tables, 5M synthetic rows,
-- notification integration, warehouse, compute pool, image repo, and stage
```

---

## Step 2: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `SNOWFLAKE_ACCOUNT` — your account locator (e.g. `abc12345`)
- `SNOWFLAKE_HOST` — your account hostname (e.g. `myorg-myaccount.snowflakecomputing.com`)
- `SNOW_CONNECTION` — your `snow` CLI connection name
- `TOKEN_SECRET_KEY` — generate with `openssl rand -hex 32` (or leave blank and the script will auto-generate one)

---

## Step 3: Deploy (Build, Push, Create Service)

The `deploy/deploy.sh` script handles the entire deployment pipeline:

```bash
# Full deploy: build image, push to registry, render spec, create service
./deploy/deploy.sh
```

The script will:
1. Build the Docker image for `linux/amd64`
2. Login to the Snowflake image registry, tag, and push
3. Render `deploy/service_spec.yaml` from the template using your `.env` values
4. Upload the spec to the Snowflake stage and create the service

### Flags

| Flag | Effect |
|------|--------|
| `--skip-build` | Skip the Docker build step |
| `--skip-push` | Skip registry login, tag, and push |
| `--update` | ALTER existing service instead of CREATE |

---

## Step 4: Verify Service is Running

```sql
-- Check service status (wait for READY state)
SELECT SYSTEM$GET_SERVICE_STATUS('SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE');

-- Get the public endpoint URL
SHOW ENDPOINTS IN SERVICE SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE;
```

Note the `ingress_url` from the SHOW ENDPOINTS output — you'll need it in the next step.

---

## Step 5: Update SERVICE_BASE_URL

Set the `SERVICE_BASE_URL` in your `.env` to the ingress URL from Step 4, then re-deploy:

```bash
./deploy/deploy.sh --skip-build --skip-push --update
```

This re-renders the service spec with the correct URL, alters the service, and automatically creates the service function and scheduled task (if they don't already exist). The task runs every 5 minutes.

---

## Step 6: Verify End-to-End

```sql
-- Manually trigger a health check via the service function
SELECT SPORTSBOOK_DW.WAGERS.TRIGGER_HEALTH_CHECK();

-- Check service logs for errors
SELECT * FROM TABLE(
  SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE!SPCS_GET_LOGS()
)
ORDER BY TIMESTAMP DESC
LIMIT 50;

-- After clicking an approval link, check audit results
SELECT * FROM SPORTSBOOK_DW.WAGERS.REMEDIATION_AUDIT ORDER BY EXECUTED_AT DESC;
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Service not starting | Check logs: `SELECT * FROM TABLE(SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE!SPCS_GET_LOGS())` |
| Image not found | Verify path matches: `/sportsbook_dw/wagers/cluster_health_repo/cluster-health-agent:latest` |
| Notification fails | Ensure email is verified and listed in `ALLOWED_RECIPIENTS` |
| Approval link expired | Links are valid for 24 hours; trigger a new health check |
| Auth errors on push | Check role has WRITE on image repo; re-run deploy script |
| Compute pool won't start | Check quotas: `SHOW COMPUTE POOLS` and verify instance family availability |

---

## Updating the Service

After code changes, rebuild and redeploy:

```bash
./deploy/deploy.sh --update
```

This builds the image, pushes it, re-renders the spec, and runs ALTER SERVICE.

To skip the image build (e.g. config-only change):

```bash
./deploy/deploy.sh --skip-build --skip-push --update
```

---

## Teardown

To remove all resources created by this project:

```sql
-- Execute deploy/teardown.sql as ACCOUNTADMIN
```
