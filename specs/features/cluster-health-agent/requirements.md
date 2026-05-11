---
status: approved
created: 2026-04-30
modified: 2026-04-30
feature: Proactive Clustering & Partition Health Detection for Sportsbook Data
---

# Requirements: Proactive Clustering & Partition Health Detection Agent

## Context

A CoCo Agent (Python REST app on Snowpark Container Services) that proactively monitors clustering health of tables in the `SPORTSBOOK_DW.WAGERS` schema, correlates poor clustering with query performance, estimates remediation costs, and performs reclustering only after human-in-the-loop approval via email.

**Architecture**: Python REST app using `cortex-code-agent-sdk 1.0.0`, deployed to SPCS, triggered every 5 minutes by a Snowflake Task.

---

## Requirements

### REQ-001: Scheduled Health Check Invocation

**Type**: Event-Driven

**Statement**:
WHEN a Snowflake Task fires (every 5 minutes)
THE SYSTEM SHALL invoke the REST API endpoint which triggers the CoCo Agent to perform a clustering health assessment on all tables in the configured schema
SO THAT clustering degradation is detected within minutes of onset.

**Acceptance Criteria**:
- [ ] A Snowflake Task exists that runs every 5 minutes
- [ ] The task calls the REST API endpoint hosted in SPCS
- [ ] The REST endpoint instantiates the CoCo Agent via `cortex-code-agent-sdk`
- [ ] The agent receives a prompt instructing it to check clustering health

---

### REQ-002: Table Discovery

**Type**: Ubiquitous

**Statement**:
THE SYSTEM SHALL automatically discover and assess all tables in the `SPORTSBOOK_DW.WAGERS` schema that have a clustering key defined
SO THAT no clustered table is overlooked during health assessments.

**Acceptance Criteria**:
- [ ] Agent queries `INFORMATION_SCHEMA.TABLES` or `SHOW TABLES` to find all tables in the schema
- [ ] Only tables with clustering keys are assessed (skip unclustered tables)
- [ ] Newly added tables are automatically picked up on the next run

---

### REQ-003: Clustering Health Assessment

**Type**: Ubiquitous

**Statement**:
THE SYSTEM SHALL call `SYSTEM$CLUSTERING_INFORMATION` for each discovered table and extract: `average_depth`, `average_overlaps`, `total_constant_partition_count / total_partition_count` (constant ratio), and `partition_depth_histogram`
SO THAT clustering health is quantified with actionable metrics.

**Acceptance Criteria**:
- [ ] Extracts `average_depth` — target: < 5
- [ ] Extracts `average_overlaps` — target: < 10
- [ ] Computes `constant_ratio` = `total_constant_partition_count / total_partition_count` — target: > 0.5
- [ ] Extracts `partition_depth_histogram` — flags if majority in high-depth buckets
- [ ] Assigns severity rating per table:

| average_depth | Rating |
|---------------|--------|
| 1-3 | Healthy |
| 4-10 | Warning |
| 11-50 | Degraded |
| 50+ | Critical |

---

### REQ-004: Query Performance Correlation

**Type**: State-Driven

**Statement**:
WHEN a table is rated Warning, Degraded, or Critical
THE SYSTEM SHALL query `ACCOUNT_USAGE.QUERY_HISTORY` (last 7 days) to identify queries where filter predicates align with the clustering key and `partitions_scanned / partitions_total > 0.80` (scan_pct > 80%)
SO THAT wasted scans due to poor clustering are identified and quantified.

**Acceptance Criteria**:
- [ ] Queries `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` for the past 7 days
- [ ] Filters to queries referencing the unhealthy table
- [ ] Computes `scan_pct = partitions_scanned / partitions_total`
- [ ] Identifies queries with `scan_pct > 80%` where predicates align with clustering key
- [ ] Reports count of wasteful queries and estimated credit waste

---

### REQ-005: Reclustering Cost Estimation

**Type**: State-Driven

**Statement**:
WHEN a table is rated Degraded or Critical AND correlated query waste is confirmed
THE SYSTEM SHALL estimate the reclustering cost (based on table size, partition count, and current depth metrics)
SO THAT the human approver can make an informed cost-benefit decision.

**Acceptance Criteria**:
- [ ] Estimates cost using table byte size and partition metrics
- [ ] Presents cost estimate in Snowflake credits
- [ ] Includes current metrics and projected post-recluster improvement
- [ ] Cost estimate is included in the approval notification

---

### REQ-006: Email Notification with Findings

**Type**: Event-Driven

**Statement**:
WHEN health assessment completes with tables rated Warning or worse
THE SYSTEM SHALL send an email notification via Snowflake Notification Integration to all recipients configured in the email lookup table, containing: health report (markdown table), impact diagnosis, remediation plan with cost estimate, and an approval link for each Degraded/Critical table
SO THAT stakeholders are informed and can approve remediation without logging into Snowflake.

**Acceptance Criteria**:
- [ ] Email recipients are read from a configurable DB table (`SPORTSBOOK_DW.WAGERS.NOTIFICATION_RECIPIENTS`)
- [ ] Email uses Snowflake Notification Integration
- [ ] Email body contains per-table health metrics with severity ratings
- [ ] Email body contains query performance correlation findings
- [ ] For Degraded/Critical tables: includes cost estimate and an approval URL
- [ ] Approval URL points to the REST API endpoint with a signed/tokenized approval action

---

### REQ-007: Human-in-the-Loop Approval via Email Link

**Type**: Event-Driven

**Statement**:
WHEN a human clicks the approval link in the notification email
THE SYSTEM SHALL validate the approval token, confirm the table and action, and trigger the reclustering remediation
SO THAT remediation only proceeds with explicit human authorization (never auto-executed).

**Acceptance Criteria**:
- [ ] Approval link encodes: table name, action type, and a time-limited signed token
- [ ] REST endpoint validates the token (prevents replay/tampering)
- [ ] Token expires after a configurable TTL (default: 24 hours)
- [ ] On valid approval: triggers the Recluster Tool
- [ ] On invalid/expired token: returns error response, does not recluster
- [ ] Approval events are logged in a DB audit table

---

### REQ-008: Reclustering Remediation

**Type**: Event-Driven

**Statement**:
WHEN remediation is approved for a specific table
THE SYSTEM SHALL execute `ALTER TABLE ... RESUME RECLUSTER` (or appropriate reclustering command) and monitor the operation until completion
SO THAT clustering health is restored for the degraded table.

**Acceptance Criteria**:
- [ ] Executes reclustering via appropriate Snowflake SQL command
- [ ] Monitors recluster progress (polls clustering info until stable)
- [ ] Records start time, end time, and outcome in an audit table
- [ ] Handles errors gracefully (e.g., table locked, insufficient privileges)

---

### REQ-009: Post-Remediation Verification & Notification

**Type**: Event-Driven

**Statement**:
WHEN reclustering completes for a table
THE SYSTEM SHALL re-run the health assessment for that table, compare before/after metrics side-by-side, and send an email notification with the improvement comparison
SO THAT stakeholders can verify remediation success without manual investigation.

**Acceptance Criteria**:
- [ ] Re-runs `SYSTEM$CLUSTERING_INFORMATION` after reclustering
- [ ] Compares: `average_depth` (before vs. after), `constant_ratio` (before vs. after), histogram shift
- [ ] Success criteria: `average_depth` decreased by > 50%, `total_constant_partition_count` increased, histogram shifted toward low-depth buckets
- [ ] Sends comparison email to all configured recipients
- [ ] Email clearly indicates SUCCESS or PARTIAL IMPROVEMENT

---

### REQ-010: Email Configuration Table

**Type**: Ubiquitous

**Statement**:
THE SYSTEM SHALL read email notification recipients from a database table (`SPORTSBOOK_DW.WAGERS.NOTIFICATION_RECIPIENTS`) that stores email addresses and notification preferences
SO THAT recipients can be managed via SQL without code changes.

**Acceptance Criteria**:
- [ ] Table schema: `email VARCHAR`, `active BOOLEAN`, `notify_on_warning BOOLEAN`, `notify_on_critical BOOLEAN`
- [ ] Only active recipients receive notifications
- [ ] Recipients with `notify_on_warning = TRUE` receive Warning-level alerts
- [ ] All active recipients receive Degraded/Critical alerts

---

### REQ-011: SDK MCP Tools Architecture

**Type**: Ubiquitous

**Statement**:
THE SYSTEM SHALL expose two SDK MCP tools to the CoCo Agent: (1) `send_notification` — sends email notifications with findings and approval links, and (2) `recluster_table` — performs reclustering and reports outcome
SO THAT the CoCo Agent can orchestrate the workflow using tools registered via `create_sdk_mcp_server()`.

**Acceptance Criteria**:
- [ ] `send_notification` tool accepts: subject, body (markdown), recipients list, and optional approval links
- [ ] `recluster_table` tool accepts: table name, and returns before/after metrics
- [ ] Both tools are registered via `@tool` decorator and `create_sdk_mcp_server()`
- [ ] Agent system prompt instructs it to use these tools as part of the health check workflow

---

### REQ-012: Demo Data Setup

**Type**: Ubiquitous

**Statement**:
THE SYSTEM SHALL create the `SPORTSBOOK_DW.WAGERS` schema with a `BET_TRANSACTIONS` table containing 5M rows of randomized-timestamp synthetic data (simulating out-of-order ingestion) and a `NOTIFICATION_RECIPIENTS` table
SO THAT the demo can showcase detection of poor clustering on realistic data.

**Acceptance Criteria**:
- [ ] Creates `SPORTSBOOK_DW` database and `WAGERS` schema
- [ ] Creates `BET_TRANSACTIONS` table with clustering key on `bet_placed_at`
- [ ] Inserts 5M rows with randomized timestamps spanning 2 years
- [ ] Creates `NOTIFICATION_RECIPIENTS` table with at least one test email
- [ ] Clustering metrics show Degraded or Critical severity after insertion

---

## Constraints & Guardrails

| Constraint | Description |
|-----------|-------------|
| C-001 | NEVER auto-execute remediation without human approval (cost implications) |
| C-002 | ALWAYS estimate costs before recommending recluster |
| C-003 | Tables > 2M partitions: note that `SYSTEM$CLUSTERING_INFORMATION` returns sampled results |
| C-004 | Flag if clustering key has unique/near-unique values (counterproductive clustering) |
| C-005 | Approval tokens must be time-limited and cryptographically signed |
| C-006 | Agent uses `permission_mode="bypassPermissions"` since it runs unattended in SPCS |

---

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────┐
│  Snowflake Task (every 5 min)                               │
│  └── Calls REST API (SPCS)                                  │
├─────────────────────────────────────────────────────────────┤
│  Python REST App (FastAPI/Uvicorn on SPCS)                  │
│  ├── /health-check endpoint → triggers CoCo Agent           │
│  ├── /approve/<token> endpoint → validates & triggers fix   │
│  ├── SDK MCP Server (in-process)                            │
│  │   ├── send_notification tool                             │
│  │   └── recluster_table tool                               │
│  └── CortexCodeSDKClient (cortex-code-agent-sdk 1.0.0)     │
├─────────────────────────────────────────────────────────────┤
│  Snowflake Objects                                          │
│  ├── SPORTSBOOK_DW.WAGERS.BET_TRANSACTIONS (monitored)     │
│  ├── SPORTSBOOK_DW.WAGERS.NOTIFICATION_RECIPIENTS (config) │
│  ├── SPORTSBOOK_DW.WAGERS.REMEDIATION_AUDIT (audit log)    │
│  └── Notification Integration (email)                       │
└─────────────────────────────────────────────────────────────┘
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Detection latency | < 6 hours from degradation onset |
| False positive rate | < 10% of flagged tables |
| Remediation time-to-action | < 5 minutes from detection to recommended SQL |
| Cost savings | Measurable reduction in warehouse credits via improved pruning |
| Query performance improvement | > 50% reduction in partitions scanned for time-range queries |
