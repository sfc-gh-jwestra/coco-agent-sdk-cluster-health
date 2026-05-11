FROM python:3.12-slim

WORKDIR /app

# Install the Cortex Code CLI (required by cortex-code-agent-sdk)
RUN apt-get update && apt-get install -y curl && \
    curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh | sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:${PATH}"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Copy entrypoint (generates connections.toml from env vars at runtime)
COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Expose the service port
EXPOSE 8000

# Entrypoint generates Snowflake connection config then starts uvicorn
CMD ["/app/entrypoint.sh"]
