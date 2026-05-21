from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.client.sse import sse_client


async def load_mcp_tools_from_sse(url: str) -> list[BaseTool]:
    """通过 SSE 连接远程 MCP 服务，加载工具列表。"""
    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            return tools
