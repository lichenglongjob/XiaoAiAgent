from abc import ABC, abstractmethod
from typing import Any

from langchain_core.messages import AnyMessage
from langgraph.graph.state import CompiledStateGraph

from src.agent import AgentState


class Skill(ABC):
    """Skill 是 LangGraph 子图的高级封装，可被 Agent 调度执行复杂多步骤任务。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Skill 的唯一标识名，用于工具调用。"""

    @property
    @abstractmethod
    def description(self) -> str:
        """Skill 的功能描述，传递给 LLM 用于路由决策。"""

    @abstractmethod
    def build_graph(self) -> CompiledStateGraph:
        """构建并返回该 Skill 的 LangGraph 子图。"""

    async def invoke(self, state: AgentState) -> dict[str, Any]:
        """执行 Skill，返回包含 messages 的字典以合并回主图状态。"""
        graph = self.build_graph()
        result = await graph.ainvoke(state)
        return {"messages": result.get("messages", [])}

    def as_tool_schema(self) -> dict[str, Any]:
        """将 Skill 转换为 OpenAI tool schema，供 LLM 调用。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": {}},
            },
        }
