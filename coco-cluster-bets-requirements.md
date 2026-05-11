# Spec: Proactive Clustering & Partition Health Detection for Sportsbook Data

## 1. Overview

**Goal**: Use Cortex Code (CoCo) to proactively identify poorly-clustered tables in a sportsbook data warehouse, diagnose performance impact, and recommend cost-aware remediation.

**Target Persona**: Data Platform Engineer at an online sportsbook operator

**Problem Statement**: High-velocity bet ingestion from multiple sources (iOS, Android, web, third-party feeds) causes micro-partitions to accumulate data in arrival order rather than logical time order. This degrades query pruning efficiency, increases warehouse costs, and slows operational dashboards.

---

## 2. Inputs

| Input | Source | Description |
|-------|--------|-------------|
| Snowflake connection | CoCo CLI `--connection` | Active connection to the sportsbook data warehouse |
| Target schema | User or config | `SPORTSBOOK_DW.WAGERS` |
| Clustering health thresholds | Configurable | `average_depth > 5`, `constant_ratio < 0.5` |
| Query history window | `ACCOUNT_USAGE.QUERY_HISTORY` | Last 7 days of queries touching target tables |

---

## 3. Outputs

| Output | Format | Description |
|--------|--------|-------------|
| Health report | Markdown table | Per-table clustering metrics with severity rating |
| Impact diagnosis | Text + metrics | Partition scan ratios, estimated cost waste |
| Remediation plan | SQL + cost estimate | Recommended actions with estimated reclustering cost |
| Post-fix verification | Before/after comparison | Clustering metrics pre- and post-remediation |

---

### Health Assessment

Agent should look at cluster information and extract from JSON response:
- `average_depth` -- target: < 5
- `average_overlaps` -- target: < 10
- `total_constant_partition_count / total_partition_count` -- target: > 0.5
- `partition_depth_histogram` -- flag if majority in high-depth buckets

**Severity Rating**:
| average_depth | Rating |
|---------------|--------|
| 1-3 | Healthy |
| 4-10 | Warning |
| 11-50 | Degraded |
| 50+ | Critical |

---

### Requirements

1. Correlate clustering health with query performance.
2. Queries with `scan_pct > 80%` where filter predicates align with the clustering key indicate wasted scans due to poor clustering.
3. Cost Estimation: Estimate reclustering cost before recommending action.
4. Decision Gate: Present cost to user. Only proceed with remediation if a Human in the loop APPROVES.
5. Remediate when triggered with approval
6. Re-run health assessment after remediation and compare. Send email notification of improvement side-by-side.
7. Email should be configurable in a DB table.

**Success Criteria**:
- `average_depth` decreased by > 50%
- `total_constant_partition_count` increased
- Histogram shifted toward low-depth buckets

---


## 6. Architecture

Use the new cortex-code-argent-sdk 1.0.0 released on Apr 21, 2026

Snowflake (Warehouse, Snowpark Container Services)
DB > Schema > Tables
Python REST app running in Snowpark
Snowflake Task > every 5 minutes > Invoke REST API to Cortex Code Agent

CoCo Agent requests health checks across the tables and uses tools to send notifications or remedy.

Tools:
- Send email notification with findings and allow human in the loop approval for re-clustering
- Recluster Tool - performs reclustering, notifies agent of outcome



---

## 6. Demo Tables Setup

Create an email table used by the email tool to do a look up which emails to notify.

For testing/demo purposes, create a poorly-clustered table with synthetic data:

```sql
CREATE DATABASE IF NOT EXISTS SPORTSBOOK_DW;
CREATE SCHEMA IF NOT EXISTS SPORTSBOOK_DW.WAGERS;

CREATE OR REPLACE TABLE SPORTSBOOK_DW.WAGERS.BET_TRANSACTIONS (
    bet_id              NUMBER,
    bet_placed_at       TIMESTAMP_NTZ,
    player_id           NUMBER,
    sport               VARCHAR(30),
    bet_type            VARCHAR(30),
    contest_id          NUMBER,
    odds                NUMBER(8,2),
    stake_amount        NUMBER(12,2),
    potential_payout    NUMBER(12,2),
    state               VARCHAR(2),
    platform            VARCHAR(10)
) CLUSTER BY (bet_placed_at);

-- Insert 5M rows with randomized timestamps (simulates out-of-order ingestion)
INSERT INTO SPORTSBOOK_DW.WAGERS.BET_TRANSACTIONS
SELECT
    SEQ4()                                                                      AS bet_id,
    DATEADD('second', UNIFORM(0, 63072000, RANDOM()), '2022-01-01'::TIMESTAMP)  AS bet_placed_at,
    UNIFORM(1, 500000, RANDOM())                                                AS player_id,
    ARRAY_CONSTRUCT('NFL','NBA','MLB','NHL','Soccer','MMA','Golf','Tennis')[UNIFORM(0,7,RANDOM())]::VARCHAR AS sport,
    ARRAY_CONSTRUCT('spread','moneyline','over_under','parlay','prop','same_game_parlay')[UNIFORM(0,5,RANDOM())]::VARCHAR AS bet_type,
    UNIFORM(10000, 99999, RANDOM())                                             AS contest_id,
    ROUND(UNIFORM(-500, 500, RANDOM()) / 1.0, 2)                               AS odds,
    ROUND(UNIFORM(100, 100000, RANDOM()) / 100.0, 2)                            AS stake_amount,
    ROUND(UNIFORM(200, 1000000, RANDOM()) / 100.0, 2)                           AS potential_payout,
    ARRAY_CONSTRUCT('NJ','PA','IN','CO','AZ','MI','IL','VA','NY','OH')[UNIFORM(0,9,RANDOM())]::VARCHAR AS state,
    ARRAY_CONSTRUCT('ios','android','web','tablet')[UNIFORM(0,3,RANDOM())]::VARCHAR AS platform
FROM TABLE(GENERATOR(ROWCOUNT => 5000000));
```

---

## 7. Success Metrics

| Metric | Target |
|--------|--------|
| Detection latency | < 6 hours from degradation onset |
| False positive rate | < 10% of flagged tables |
| Remediation time-to-action | < 5 minutes from detection to recommended SQL |
| Cost savings | Measurable reduction in warehouse credits via improved pruning |
| Query performance improvement | > 50% reduction in partitions scanned for time-range queries |

---

## 8. Constraints & Guardrails

- **Never auto-execute remediation** without user approval (cost implications)
- **Always estimate costs** before recommending `RESUME RECLUSTER`
- **Read-only by default** -- use `--sql-read-only` for scheduled health checks
- **Tables > 2M partitions**: Note that `SYSTEM$CLUSTERING_INFORMATION` returns sampled results
- **High-cardinality keys**: Flag if clustering key has unique/near-unique values (counterproductive)
