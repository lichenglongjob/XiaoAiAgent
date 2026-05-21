from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool

from src.config import LLMConfig


def create_llm(config: LLMConfig, tools: list[BaseTool] | None = None) -> BaseChatModel:
    """根据配置动态创建 LangChain ChatModel 实例。"""

    provider = config.provider.lower()
    kwargs: dict[str, Any] = {
        "model": config.model,
        "temperature": config.temperature,
    }
    if config.api_key:
        kwargs["api_key"] = config.api_key
    if config.base_url:
        kwargs["base_url"] = config.base_url
    kwargs.update(config.extra)

    if provider == "deepseek":
        from langchain_deepseek import ChatDeepSeek

        llm = ChatDeepSeek(**kwargs)

    elif provider in ("openai", "openai-compatible"):
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(**kwargs)

    elif provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:
            raise ImportError(
                "使用 Ollama  provider 需要安装 langchain-ollama：pip install langchain-ollama"
            ) from exc
        # Ollama 使用 base_url 作为 host，model 作为 model
        ollama_kwargs = {"model": config.model, "temperature": config.temperature}
        if config.base_url:
            ollama_kwargs["base_url"] = config.base_url
        ollama_kwargs.update(config.extra)
        llm = ChatOllama(**ollama_kwargs)

    elif provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise ImportError(
                "使用 Anthropic provider 需要安装 langchain-anthropic：pip install langchain-anthropic"
            ) from exc
        llm = ChatAnthropic(**kwargs)

    else:
        raise ValueError(
            f"不支持的 LLM provider: {config.provider}。"
            f"支持的选项: deepseek, openai, openai-compatible, ollama, anthropic"
        )

    if tools:
        llm = llm.bind_tools(tools)

    return llm
