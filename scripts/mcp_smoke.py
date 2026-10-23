#!/usr/bin/env python3
"""Talk to the Tidal MCP server the way Alexa+ would: list tools, call them.

    python scripts/mcp_smoke.py                       # http://127.0.0.1:8001/mcp
    python scripts/mcp_smoke.py https://my-host/mcp
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(url: str) -> None:
    async with streamable_http_client(url) as streams:
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
