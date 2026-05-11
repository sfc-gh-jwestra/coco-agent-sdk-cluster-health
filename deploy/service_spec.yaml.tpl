spec:
  containers:
    - name: cluster-health-agent
      image: /sportsbook_dw/wagers/cluster_health_repo/cluster-health-agent:latest
      env:
        SNOWFLAKE_CONNECTION: default
        SNOWFLAKE_DATABASE: ${SNOWFLAKE_DATABASE}
        SNOWFLAKE_SCHEMA: ${SNOWFLAKE_SCHEMA}
        SNOWFLAKE_WAREHOUSE: ${SNOWFLAKE_WAREHOUSE}
        TARGET_SCHEMA: ${SNOWFLAKE_DATABASE}.${SNOWFLAKE_SCHEMA}
        NOTIFICATION_INTEGRATION: ${NOTIFICATION_INTEGRATION}
        SERVICE_BASE_URL: "${SERVICE_BASE_URL}"
        TOKEN_SECRET_KEY: "${TOKEN_SECRET_KEY}"
        TOKEN_TTL_HOURS: "${TOKEN_TTL_HOURS}"
      resources:
        requests:
          cpu: 0.5
          memory: 2Gi
        limits:
          cpu: 2
          memory: 6Gi
  endpoints:
    - name: cluster-health-endpoint
      port: 8000
      public: true
