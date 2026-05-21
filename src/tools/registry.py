from typing import Any

from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from src.config import AppConfig, MCPServerConfig

from .builtin import register_builtin_tools
from .mcp_client import load_mcp_tools_from_sse


class ToolRegistry:
    """统一管理本地工具和 MCP 工具，为 LangGraph Agent 提供统一的工具视图。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def register_builtin(self) -> None:
        tools = register_builtin_tools()
        for tool in tools:
            self.register(tool)

    async def register_mcp(self, config: MCPServerConfig) -> None:
        tools = await load_mcp_tools_from_sse(config.url)
        for tool in tools:
            self.register(tool)

    async def load_all(self, app_config: AppConfig) -> None:
        self.register_builtin()
        for mcp in app_config.mcp_servers:
            try:
                await self.register_mcp(mcp)
            except Exception:
                pass

    def get_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def get_tool(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_openai_schemas(self) -> list[dict[str, Any]]:
        return [convert_to_openai_tool(t) for t in self._tools.values()]


_registry: ToolRegistry | None = None


async def get_tool_registry(app_config: AppConfig | None = None) -> ToolRegistry:
    global _registry
    if _registry is None:
        from src.config import get_config

        cfg = app_config or get_config()
        _registry = ToolRegistry()
        await _registry.load_all(cfg)
    return _registry
