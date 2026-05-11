-- ============================================================================
-- Cluster Health Agent - Teardown Script
-- ============================================================================
-- Removes ALL resources created by setup.sql.
-- Run as ACCOUNTADMIN or a role with sufficient DROP privileges.
--
-- WARNING: This is destructive and irreversible. All data will be lost.
-- ============================================================================

-- 1. Suspend and drop the scheduled task
ALTER TASK IF EXISTS SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_CHECK_TASK SUSPEND;
DROP TASK IF EXISTS SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_CHECK_TASK;

-- 2. Drop the service function
DROP FUNCTION IF EXISTS SPORTSBOOK_DW.WAGERS.TRIGGER_HEALTH_CHECK();

-- 3. Drop the SPCS service
DROP SERVICE IF EXISTS SPORTSBOOK_DW.WAGERS.CLUSTER_HEALTH_SERVICE;

-- 4. Drop the compute pool
DROP COMPUTE POOL IF EXISTS CLUSTER_HEALTH_POOL;

-- 5. Drop the notification integration
DROP NOTIFICATION INTEGRATION IF EXISTS CLUSTER_HEALTH_EMAIL_INTEGRATION;

-- 6. Drop the warehouse
DROP WAREHOUSE IF EXISTS CLUSTER_HEALTH_WH;

-- 7. Drop the entire database (includes schema, tables, stage, image repo)
DROP DATABASE IF EXISTS SPORTSBOOK_DW;

-- 8. Drop the dedicated role
DROP ROLE IF EXISTS CLUSTER_HEALTH_AGENT_ROLE;
