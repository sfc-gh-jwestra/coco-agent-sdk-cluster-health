# How the MCP Server is Started and Tools are Exposed

## Overview

There is **no separate MCP server process**. The tools run **in-process** inside the FastAPI application. The `cortex-code-agent-sdk` provides a mechanism to define MCP tools that execute directly in your Python app's memory space, bridged to the CoCo agent via stdio.

---

## The Flow

### 1. Tools are defined with the `@tool` decorator

In `app/tools/send_notification.py` and `app/tools/recluster_table.py`, each tool is defined as an async function decorated with `@tool(name, description, input_schema)`. This produces an `SdkMcpTool` object — not a running server, just a registered definition.

```python
# app/tools/send_notification.py
from cortex_code_agent_sdk import tool

@tool(
    "send_notification",
    description="Send an email notification with cluster health findings...",
    input_schema={
        "type": "object",
        "properties": {
            "subject": {"type": "string", ...},
            "body": {"type": "string", ...},
            "tables_needing_action": {"type": "array", ...},
        },
        "required": ["subject", "body", "tables_needing_action"],
    },
)
async def send_notification(args: dict) -> dict:
    # Tool logic here — runs in your FastAPI process
    return {"content": [{"type": "text", "text": "Notification sent..."}]}
```

### 2. `create_sdk_mcp_server()` bundles tools into a config

In `app/agent.py`:

```python
from cortex_code_agent_sdk import create_sdk_mcp_server

cluster_tools_server = create_sdk_mcp_server(
    name="cluster-health-tools",
    version="1.0.0",
    tools=[send_notification, recluster_table],
)
```

This creates an `mcp.server.Server` instance (from the `mcp` Python package) with `list_tools` and `call_tool` handlers registered, then wraps it in a dict:

```python
{"type": "sdk", "name": "cluster-health-tools", "instance": <mcp.server.Server>}
```

### 3. The config is passed to `CortexCodeAgentOptions`

```python
options = CortexCodeAgentOptions(
    connection=CONNECTION_NAME,
    permission_mode="bypassPermissions",
    allow_dangerously_skip_permissions=True,
    mcp_servers={"cluster-health-tools": cluster_tools_server},
    append_system_prompt=HEALTH_CHECK_SYSTEM_PROMPT,
    max_turns=50,
)
```

### 4. The SDK spawns the `cortex` CLI and bridges MCP

When `CortexCodeSDKClient.connect()` is called:

1. The SDK's `SubprocessCLITransport._build_command()` serializes the MCP server configs into `--mcp-config '{"mcpServers": {...}}'` on the CLI command line
2. For `type: "sdk"` servers, the actual `Server` instance stays in your Python process
3. The SDK sets up a **stdio bridge** between the `cortex` CLI subprocess and your in-process `mcp.server.Server`
4. When the CoCo agent inside the CLI decides to call `send_notification` or `recluster_table`, the request travels through the bridge

---

## Request Flow

```
┌─────────────────────────────────────────────────────────┐
│  FastAPI app (Python process)                           │
│                                                         │
│  ┌─────────────────────────────────────────────┐        │
│  │  In-process MCP Server ("cluster-health-tools")     │
│  │    ├── send_notification (async handler)     │        │
│  │    └── recluster_table   (async handler)     │        │
│  └──────────────────┬──────────────────────────┘        │
│                     │ stdio bridge                       │
│  ┌──────────────────▼──────────────────────────┐        │
│  │  CortexCodeSDKClient                        │        │
│  │    └── SubprocessCLITransport               │        │
│  │         └── `cortex` CLI subprocess          │        │
│  │              (CoCo agent runs here)          │        │
│  └─────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────┘
```

**Tool call path:**

```
CoCo Agent (in CLI) decides to use "send_notification"
  → CLI writes tool_use to stdout (stream-json)
  → SDK transport reads it
  → SDK routes to in-process MCP Server
  → MCP Server calls send_notification handler
  → Handler executes (queries Snowflake, generates tokens, etc.)
  → Handler returns {"content": [{"type": "text", "text": "..."}]}
  → SDK writes tool_result back to CLI stdin
  → CoCo Agent receives the result and continues
```

---

## Key Design Points

| Aspect | Detail |
|--------|--------|
| **No network ports** | MCP tools don't expose any HTTP/TCP ports |
| **No separate process** | Tools run in the same Python process as FastAPI |
| **Direct memory access** | Tools can import and use your app's config, modules, and state |
| **Async execution** | Tool handlers are `async` functions, compatible with FastAPI's event loop |
| **Tool discovery** | The CoCo agent discovers tools via `list_tools` MCP protocol at session start |
| **Input validation** | The `input_schema` (JSON Schema) is sent to the agent so it knows how to call the tool |
| **Return format** | Handlers return `{"content": [{"type": "text", "text": "..."}]}` |

---

## Why This Matters

Because tools run in-process:

- `send_notification` can call `generate_approval_token()` from `app/token.py` directly
- `recluster_table` can query `REMEDIATION_AUDIT` using the same Snowflake connection config
- No serialization overhead for passing data between processes
- No need to deploy or manage a separate MCP server container
- The CoCo agent gets tool schemas automatically and knows how to invoke them
