---
name: "fix-mcp-tools-not-visible"
created: "2026-05-11T22:09:26.601Z"
status: pending
---

# Plan: Fix MCP Tools Not Visible to Agent

## Context

The agent runs successfully in SPCS and performs the clustering health check (queries tables, checks clustering info, correlates with query performance), but when it reaches the notification step, it reports:

> "There is no MCP `send_notification` tool available in this environment. No MCP servers are registered."

### Architecture (how it should work)

```
sequenceDiagram
    participant FastAPI as FastAPI_Process
    participant SDK as CortexCodeSDKClient
    participant CLI as cortex_CLI_subprocess
    participant LLM as CoCo_Agent_LLM

    FastAPI->>SDK: query(prompt, options)
    SDK->>CLI: spawn with --mcp-config {"mcpServers":{"cluster-health-tools":{"type":"sdk",...}}}
    CLI->>SDK: control_request(mcp_message, tools/list)
    SDK->>CLI: control_response(tools: [send_notification, recluster_table])
    CLI->>LLM: "You have these tools available: send_notification, recluster_table"
    LLM->>CLI: tool_use(send_notification, {...})
    CLI->>SDK: control_request(mcp_message, tools/call)
    SDK->>FastAPI: calls send_notification handler in-process
    FastAPI->>SDK: returns result
    SDK->>CLI: control_response(result)
    CLI->>LLM: tool_result
```

### Key Findings

1. **Container CLI version is v1.0.73**, local is v1.0.80. The SDK MCP bridge (control protocol) requires the CLI to send `control_request` with `subtype: "mcp_message"` to discover SDK tools. Older CLI versions may not implement this.

2. **Docker layer caching** — the `RUN apt-get update && curl ... | sh` step is cached, so even though the install script would pull a newer version, the cached layer from the initial build is being used.

3. **No `--allowed-tools`** is passed — the `allowed_tools` list in options is empty (default). Some CLI versions require explicit tool allow-listing when `--dangerously-allow-all-tool-calls` is used with MCP servers.

4. **`--setting-sources ""`** — passing an empty string for setting-sources may confuse the CLI into resetting all settings including MCP tool visibility.

## Implementation Steps

### 1. Bust Docker cache for CLI install

In deploy/Dockerfile, add a cache-busting ARG before the CLI install:

```
ARG CLI_VERSION=latest
RUN apt-get update && apt-get install -y curl && \
    curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh | sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
```

And on rebuild, use `--no-cache` for the specific layer or `--build-arg CLI_VERSION=$(date +%s)`.

Simpler alternative: just pass `--no-cache` to `docker build` in deploy.sh.

### 2. Add debug stderr capture

In app/agent.py `_make_options()`, add `"debug-to-stderr"` to `extra_args` to see CLI-level MCP bridge diagnostics:

```
extra_args={"debug-to-stderr": None},
```

This will log all CLI internal debug messages (including MCP initialization) via the `stderr` callback which is already set up.

### 3. Explicitly set allowed\_tools

In app/agent.py `_make_options()`, add the tool names in the format the CLI expects for MCP tools:

```
allowed_tools=[
    "mcp__cluster-health-tools__send_notification",
    "mcp__cluster-health-tools__recluster_table",
],
```

This ensures the CLI knows these tools should be presented to the LLM, even if auto-discovery has issues.

### 4. Fix setting\_sources empty list

In app/agent.py, change `setting_sources=[]` to `setting_sources=None` or remove it, OR ensure the CLI handles the empty string correctly. The current code produces `--setting-sources ""` which may be interpreted as "no sources at all" including MCP. Alternatively keep `[]` but verify behavior.

### 5. Rebuild and verify

```
./deploy/deploy.sh --update   # will rebuild with --no-cache
```

Then check logs for:

- CLI version >= 1.0.80
- Debug lines showing `mcp_message` control requests flowing
- Agent calling `send_notification` tool successfully

## Verification

1. After deploy, check `SYSTEM$GET_SERVICE_STATUS` for READY

2. Trigger health check via the task or direct endpoint call

3. Check container logs for:

   - `[cortex-cli]` debug lines showing MCP initialization
   - Absence of "No MCP servers are registered" in agent output
   - `send_notification called:` log line from the tool handler

4. Verify email is received with approval links

## Critical Files

- deploy/Dockerfile — CLI install layer needs cache busting
- app/agent.py — `_make_options()` needs `extra_args`, `allowed_tools` fixes
- deploy/deploy.sh — Add `--no-cache` to docker build command
- app/tools/send\_notification.py — Tool handler (verify it gets called)
