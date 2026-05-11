## Snowflake Objects Inventory

### Database & Schema

| Object | DDL |
|--------|-----|
| `SPORTSBOOK_DW` | `CREATE DATABASE IF NOT EXISTS SPORTSBOOK_DW` |
| `SPORTSBOOK_DW.WAGERS` | `CREATE SCHEMA IF NOT EXISTS SPORTSBOOK_DW.WAGERS` |

---

### Tables

**1. `SPORTSBOOK_DW.WAGERS.BET_TRANSACTIONS`** — Monitored table (demo data)

| Column | Type | Notes |
|--------|------|-------|
| `bet_id` | NUMBER | PK |
| `bet_placed_at` | TIMESTAMP_NTZ | Clustering key |
| `player_id` | NUMBER | |
| `sport` | VARCHAR(30) | |
| `bet_type` | VARCHAR(30) | |
| `contest_id` | NUMBER | |
| `odds` | NUMBER(8,2) | |
| `stake_amount` | NUMBER(12,2) | |
| `potential_payout` | NUMBER(12,2) | |
| `state` | VARCHAR(2) | |
| `platform` | VARCHAR(10) | |

Clustering key: `(bet_placed_at)` — 5M rows with randomized timestamps to simulate poor clustering.

---

**2. `SPORTSBOOK_DW.WAGERS.NOTIFICATION_RECIPIENTS`** — Email config table

| Column | Type | Notes |
|--------|------|-------|
| `email` | VARCHAR(256) | Recipient email address |
| `active` | BOOLEAN | Whether to send notifications |
| `notify_on_warning` | BOOLEAN | Receive Warning-level alerts |
| `notify_on_critical` | BOOLEAN | Receive Degraded/Critical alerts |

---

**3. `SPORTSBOOK_DW.WAGERS.REMEDIATION_AUDIT`** — Audit log for approvals and remediations

| Column | Type | Notes |
|--------|------|-------|
| `audit_id` | NUMBER AUTOINCREMENT | PK |
| `table_name` | VARCHAR | Fully qualified table name |
| `action` | VARCHAR(50) | `APPROVAL_REQUESTED`, `APPROVED`, `RECLUSTER_STARTED`, `RECLUSTER_COMPLETED`, `RECLUSTER_FAILED` |
| `approval_token` | VARCHAR | Signed token used for approval |
| `metrics_before` | VARIANT | JSON of clustering metrics pre-remediation |
| `metrics_after` | VARIANT | JSON of clustering metrics post-remediation |
| `estimated_cost_credits` | NUMBER(12,4) | Estimated reclustering cost |
| `requested_at` | TIMESTAMP_NTZ | When the action was initiated |
| `completed_at` | TIMESTAMP_NTZ | When the action finished |
| `requested_by` | VARCHAR | Email of approver (or 'SYSTEM' for auto-detection) |

---

### Notification Integration

**4. `CLUSTER_HEALTH_EMAIL_INTEGRATION`** — Account-level notification integration

```sql
CREATE NOTIFICATION INTEGRATION CLUSTER_HEALTH_EMAIL_INTEGRATION
  TYPE = EMAIL
  ENABLED = TRUE
  DEFAULT_SUBJECT = 'Sportsbook DW - Clustering Health Alert';
```

Used by the `send_notification` tool via `SYSTEM$SEND_SNOWFLAKE_NOTIFICATION`.

---

### Compute Pool & Service (SPCS)

**5. `CLUSTER_HEALTH_COMPUTE_POOL`** — Compute pool for the container service

```sql
CREATE COMPUTE POOL CLUSTER_HEALTH_COMPUTE_POOL
  MIN_NODES = 1
  MAX_NODES = 1
  INSTANCE_FAMILY = CPU_X64_XS;
```

**6. `SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_AGENT_SERVICE`** — SPCS service running the Python REST app

```sql
CREATE SERVICE SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_AGENT_SERVICE
  IN COMPUTE POOL CLUSTER_HEALTH_COMPUTE_POOL
  FROM @SPORTSBOOK_DW.WAGERS.APP_STAGE
  SPECIFICATION_FILE = 'service_spec.yaml';
```

---

### Stage

**7. `SPORTSBOOK_DW.WAGERS.APP_STAGE`** — Internal stage for container image spec and artifacts

```sql
CREATE STAGE IF NOT EXISTS SPORTSBOOK_DW.WAGERS.APP_STAGE
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');
```

---

### Image Repository

**8. `SPORTSBOOK_DW.WAGERS.AGENT_IMAGES`** — Container image repository

```sql
CREATE IMAGE REPOSITORY IF NOT EXISTS SPORTSBOOK_DW.WAGERS.AGENT_IMAGES;
```

---

### Snowflake Task

**9. `SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_CHECK_TASK`** — Scheduled trigger

```sql
CREATE TASK SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_CHECK_TASK
  WAREHOUSE = <warehouse_name>
  SCHEDULE = '5 MINUTE'
AS
  CALL SYSTEM$CALL_ENDPOINT(
    'CLUSTER_HEALTH_AGENT_SERVICE',
    '/health-check',
    'POST',
    '{}'
  );
```

Fires every 5 minutes, calls the REST API to trigger the CoCo Agent health assessment.

---

### Warehouse

**10. Warehouse** — Used by the Task and for query history lookups (assumes existing warehouse, or create one)

```sql
CREATE WAREHOUSE IF NOT EXISTS CLUSTER_HEALTH_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE;
```

---

### Summary Count

| Category | Count | Objects |
|----------|-------|---------|
| Database | 1 | `SPORTSBOOK_DW` |
| Schema | 1 | `WAGERS` |
| Tables | 3 | `BET_TRANSACTIONS`, `NOTIFICATION_RECIPIENTS`, `REMEDIATION_AUDIT` |
| Integration | 1 | `CLUSTER_HEALTH_EMAIL_INTEGRATION` |
| Compute Pool | 1 | `CLUSTER_HEALTH_COMPUTE_POOL` |
| Service | 1 | `CLUSTER_HEALTH_AGENT_SERVICE` |
| Stage | 1 | `APP_STAGE` |
| Image Repository | 1 | `AGENT_IMAGES` |
| Task | 1 | `CLUSTER_HEALTH_CHECK_TASK` |
| Warehouse | 1 | `CLUSTER_HEALTH_WH` (if not using existing) |
| **Total** | **12** | |
