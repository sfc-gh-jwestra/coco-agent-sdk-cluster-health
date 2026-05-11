#!/bin/bash
# Entrypoint for the Cluster Health Agent SPCS container.
# Generates ~/.snowflake/connections.toml from runtime environment variables
# provided by Snowpark Container Services, then starts the FastAPI server.
#
# SNOWFLAKE_ACCOUNT and SNOWFLAKE_HOST are automatically injected by Snowflake
# into every SPCS container — do NOT override them in the service spec.

set -e

mkdir -p /root/.snowflake

cat > /root/.snowflake/connections.toml << EOF
default_connection_name = "default"

[default]
account = "$SNOWFLAKE_ACCOUNT"
host = "$SNOWFLAKE_HOST"
authenticator = "oauth"
token_file_path = "/snowflake/session/token"
EOF

chmod 0600 /root/.snowflake/connections.toml

echo "Generated /root/.snowflake/connections.toml"
echo "--- connections.toml ---"
cat /root/.snowflake/connections.toml
echo "--- end ---"

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop asyncio
