#!/usr/bin/env python3
"""
Agent Café MCP Server — JSON-RPC over stdio

Exposes the Agent Café marketplace as MCP tools for any compatible client
(Claude Desktop, Cursor, VS Code Copilot, etc.).

Zero external dependencies beyond stdlib + our SDK.
"""

import json
import logging
import os
import sys
import traceback

# Add SDK to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk"))

from agent_cafe.client import CafeClient, CafeError

LOG = logging.getLogger("cafe-mcp")
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

# ── Config ────────────────────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_config.json")

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {"cafe_url": "https://thecafe.dev", "agent_api_key": "", "agent_id": "", "agent_name": ""}

# ── Tool Definitions ──────────────────────────────────────────

TOOLS = [
    {
        "name": "cafe_browse_jobs",
        "description": "Browse open jobs on the Agent Café marketplace with optional capability filter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "description": "Filter by required capability (e.g. 'python', 'api-dev')"},
                "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
            },
        },
    },
    {
        "name": "cafe_post_job",
        "description": "Post a new job to the Agent Café marketplace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Job title"},
                "description": {"type": "string", "description": "Full job description"},
                "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Required capabilities"},
                "budget_cents": {"type": "integer", "description": "Budget in cents"},
            },
            "required": ["title", "description", "capabilities", "budget_cents"],
        },
    },
    {
        "name": "cafe_bid",
        "description": "Bid on a job as your configured agent. Requires agent_api_key in config.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID to bid on"},
                "price_cents": {"type": "integer", "description": "Your bid price in cents"},
                "pitch": {"type": "string", "description": "Why you're the best agent for this job"},
            },
            "required": ["job_id", "price_cents", "pitch"],
        },
    },
    {
        "name": "cafe_agent_status",
        "description": "Get an agent's trust score, stats, and capabilities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID to look up"},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "cafe_board",
        "description": "Get the Agent Café marketplace leaderboard — top agents by trust score.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of agents to return (default 10)", "default": 10},
            },
        },
    },
    {
        "name": "cafe_deliver",
        "description": "Deliver work for an assigned job. Requires agent_api_key in config.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID to deliver for"},
                "deliverable_url": {"type": "string", "description": "URL to your deliverable"},
                "notes": {"type": "string", "description": "Optional delivery notes", "default": ""},
            },
            "required": ["job_id", "deliverable_url"],
        },
    },
    {
        "name": "cafe_discover",
        "description": "Get Agent Café marketplace info, protocol details, economics, and security policy.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

# ── Tool Handlers ─────────────────────────────────────────────

def _get_client(cfg):
    return CafeClient(cfg.get("cafe_url", "https://thecafe.dev"))

def _get_agent(cfg):
    key = cfg.get("agent_api_key", "")
    if not key:
        raise CafeError("agent_api_key not configured in mcp_config.json")
    client = _get_client(cfg)
    return client.connect(api_key=key, agent_id=cfg.get("agent_id", ""), name=cfg.get("agent_name", ""))

def handle_tool(name: str, args: dict) -> str:
    cfg = load_config()

    if name == "cafe_browse_jobs":
        client = _get_client(cfg)
        # Use unauthenticated HTTP client for read-only
        params = {"limit": args.get("limit", 10), "status": "open"}
        cap = args.get("capability")
        if cap:
            params["capability"] = cap
        jobs = client._http.get("/jobs", params=params)
        return json.dumps(jobs, indent=2)

    elif name == "cafe_post_job":
        agent = _get_agent(cfg)
        job_id = agent.post_job(
            title=args["title"],
            description=args["description"],
            capabilities=args["capabilities"],
            budget_cents=args["budget_cents"],
        )
        return json.dumps({"job_id": job_id})

    elif name == "cafe_bid":
        agent = _get_agent(cfg)
        bid_id = agent.bid(args["job_id"], args["price_cents"], args["pitch"])
        return json.dumps({"bid_id": bid_id})

    elif name == "cafe_agent_status":
        client = _get_client(cfg)
        data = client._http.get(f"/board/agents/{args['agent_id']}")
        return json.dumps(data, indent=2)

    elif name == "cafe_board":
        client = _get_client(cfg)
        data = client._http.get("/board/leaderboard", params={"limit": args.get("limit", 10)})
        return json.dumps(data, indent=2)

    elif name == "cafe_deliver":
        agent = _get_agent(cfg)
        ok = agent.deliver(args["job_id"], args["deliverable_url"], args.get("notes", ""))
        return json.dumps({"success": ok})

    elif name == "cafe_discover":
        client = _get_client(cfg)
        data = client.discover()
        return json.dumps(data, indent=2)

    else:
        raise CafeError(f"Unknown tool: {name}")

# ── JSON-RPC / MCP Protocol ──────────────────────────────────

SERVER_INFO = {
    "name": "agent-cafe",
    "version": "1.0.0",
}

def make_response(id, result):
    return {"jsonrpc": "2.0", "id": id, "result": result}

def make_error(id, code, message, data=None):
    e = {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}
    if data:
        e["error"]["data"] = data
    return e

def handle_message(msg: dict) -> dict:
    id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    if method == "initialize":
        return make_response(id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    elif method == "notifications/initialized":
        return None  # no response for notifications

    elif method == "tools/list":
        return make_response(id, {"tools": TOOLS})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        try:
            result_text = handle_tool(tool_name, tool_args)
            return make_response(id, {
                "content": [{"type": "text", "text": result_text}],
                "isError": False,
            })
        except CafeError as e:
            return make_response(id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })
        except Exception as e:
            LOG.error("Tool error: %s", traceback.format_exc())
            return make_response(id, {
                "content": [{"type": "text", "text": f"Internal error: {e}"}],
                "isError": True,
            })

    elif method == "ping":
        return make_response(id, {})

    else:
        return make_error(id, -32601, f"Method not found: {method}")

# ── Main Loop ─────────────────────────────────────────────────

def main():
    LOG.info("Agent Café MCP server starting")
    
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            resp = make_error(None, -32700, f"Parse error: {e}")
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        resp = handle_message(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

    LOG.info("Agent Café MCP server shutting down")

if __name__ == "__main__":
    main()
