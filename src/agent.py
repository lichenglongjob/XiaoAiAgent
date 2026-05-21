import time
from typing import Annotated, Any

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    convert_to_messages,
    convert_to_openai_messages,
)
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from src.config import AppConfig
from src.llm_factory import create_llm
from src.tools.registry import ToolRegistry


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


class Agent:
    def __init__(self, config: AppConfig, registry: ToolRegistry) -> None:
        self.config = config
        self.registry = registry
        self.tools = registry.get_tools()
        self.llm = create_llm(config.llm, tools=self.tools or None)

        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(AgentState)

        graph.add_node("agent", self._agent_node)

        if self.tools:
            tool_node = ToolNode(self.tools)
            graph.add_node("tools", tool_node)

        graph.set_entry_point("agent")

        if self.tools:
            graph.add_conditional_edges(
                "agent",
                self._should_continue,
                {"tools": "tools", END: END},
            )
            graph.add_edge("tools", "agent")
        else:
            graph.add_edge("agent", END)

        return graph.compile()

    async def _agent_node(self, state: AgentState) -> dict[str, Any]:
        response = await self.llm.ainvoke(state["messages"])
        return {"messages": [response]}

    def _should_continue(self, state: AgentState) -> str:
        last_message = state["messages"][-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        return END

    async def ainvoke(self, messages: list[AnyMessage]) -> AIMessage:
        result = await self.graph.ainvoke({"messages": messages})
        last = result["messages"][-1]
        if isinstance(last, AIMessage):
            return last
        raise RuntimeError(f"Unexpected final message type: {type(last)}")

    async def astream(self, messages: list[AnyMessage]):
        async for chunk in self.graph.astream({"messages": messages}, stream_mode="messages"):
            yield chunk


def convert_openai_messages(messages: list[dict[str, Any]]) -> list[AnyMessage]:
    """将 OpenAI 格式的消息列表转换为 LangChain 消息对象（使用内置兼容层）。"""
    return convert_to_messages(messages)


def convert_lc_messages_to_openai(messages: list[AnyMessage]) -> list[dict[str, Any]]:
    """将 LangChain 消息列表转换回 OpenAI 原始格式（使用内置兼容层）。"""
    return convert_to_openai_messages(messages)


def create_agent(config: AppConfig, registry: ToolRegistry) -> Agent:
    return Agent(config, registry)
