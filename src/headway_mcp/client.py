"""In-process MCP client and server: the analyst calls the tools through the protocol, in memory."""

import asyncio
import json
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session

from headway_mcp.server import build_server
from headway_mcp.tools import Tools


class InProcessMcp:
    """call(name, args) -> the tool's result dict, over an in-memory MCP session."""

    def __init__(self, tools: Tools) -> None:
        self._server = build_server(tools)

    async def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        async with create_connected_server_and_client_session(self._server._mcp_server) as client:
            res = await client.call_tool(name, args)
        if res.isError:
            return {"status": "invalid_input", "error": "tool call rejected"}
        if res.structuredContent is not None:
            sc = res.structuredContent
            return dict(sc.get("result", sc))
        text = "".join(getattr(c, "text", "") for c in res.content)
        doc = json.loads(text)
        return dict(doc)

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(self._call(name, args))

    def list_tools(self) -> list[str]:
        async def go() -> list[str]:
            async with create_connected_server_and_client_session(
                self._server._mcp_server
            ) as client:
                return [t.name for t in (await client.list_tools()).tools]

        return asyncio.run(go())
