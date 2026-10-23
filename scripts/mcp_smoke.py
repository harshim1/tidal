#!/usr/bin/env python3
"""Talk to the Tidal MCP server the way Alexa+ would: list tools, call them.

    python scripts/mcp_smoke.py                       # http://127.0.0.1:8001/mcp
    TIDAL_MCP_TOKEN=… python scripts/mcp_smoke.py https://my-host/mcp
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def check_auth(url: str, headers: dict | None) -> None:
    """Fail with a plain message on a bad token — the MCP client only reports a generic error."""
    probe = httpx2.post(url, json={}, headers={**(headers or {}), "Accept": "application/json, text/event-stream"},
                        timeout=90)
    if probe.status_code == 401:
        raise SystemExit("401 from the server: set TIDAL_MCP_TOKEN to the token from the host "
                         "(Render: the service's Environment tab), not a placeholder.")


async def main(url: str) -> None:
    token = os.environ.get("TIDAL_MCP_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else None
    check_auth(url, headers)
    async with httpx2.AsyncClient(headers=headers, timeout=60) as http, \
            streamable_http_client(url, http_client=http) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"connected: {init.server_info.name} v{init.server_info.version} "
                  f"(protocol {init.protocol_version})")
            tools = await session.list_tools()
            print("tools:", ", ".join(t.name for t in tools.tools))
            for name, args in [
                ("get_night_summary", {"hours": 12, "timezone": "America/New_York"}),
                ("get_latest_vitals", {"timezone": "America/New_York"}),
                ("list_attention_events", {"hours": 24, "timezone": "America/New_York"}),
            ]:
                res = await session.call_tool(name, args)
                payload = res.structured_content or json.loads(res.content[0].text)
                print(f"\n▶ {name}\n  🗣  {payload.get('speech')}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001/mcp"))
