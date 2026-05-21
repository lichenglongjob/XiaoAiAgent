import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class LLMConfig(BaseModel):
    provider: str = "deepseek"
    api_key: str = "${DEEPSEEK_API_KEY}"
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    temperature: float = 0.7
    extra: dict[str, Any] = Field(default_factory=dict)


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class XiaomiConfig(BaseModel):
    username: str = "${MI_USER}"
    password: str = "${MI_PASS}"
    did: str = ""  # 设备 deviceID，为空时自动选择第一个在线音箱
    hardware: str = "L05C"  # 硬件型号：L05C=Play增强版, LX06=Pro
    use_command: bool = True  # L05C/L05B 必须设为 true
    mute_xiaoai: bool = True  # 是否屏蔽小爱原生回复（推荐开启）
    trigger_word: str = ""  # 触发词，为空则所有对话都走 Agent；如填"问AI"则只有包含该词才触发
    tts_command: list = [5, 3]  # TTS 命令参数，L05C 默认为 speak action
    wake_command: list = [5, 4]  # 唤醒命令参数
    polling_interval: float = 1.0  # 轮询间隔（秒）
    debug: bool = False  # 输出轮询、过滤、TTS 等详细日志


class MCPServerConfig(BaseModel):
    name: str
    url: str


class AppConfig(BaseSettings):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    xiaomi: XiaomiConfig = Field(default_factory=XiaomiConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)

    class Config:
        env_prefix = "MYXIAOAI_"
        env_nested_delimiter = "__"

    @property
    def deepseek(self) -> LLMConfig:
        """向后兼容：旧的配置代码通过 .deepseek 访问 .llm。"""
        return self.llm


def _resolve_env(value: str) -> str:
    if value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.getenv(env_var, value)
    return value


def _resolve_env_in_dict(obj: dict | list | str) -> dict | list | str:
    if isinstance(obj, dict):
        return {k: _resolve_env_in_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_in_dict(item) for item in obj]
    if isinstance(obj, str):
        return _resolve_env(obj)
    return obj


def load_config(path: str | None = None) -> AppConfig:
    if path is None:
        path = str(Path(__file__).parent.parent / "config.yaml")

    if not os.path.exists(path):
        return AppConfig()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    resolved = _resolve_env_in_dict(raw)
    # Remove None values so Pydantic uses field defaults
    resolved = {k: v for k, v in resolved.items() if v is not None}
    return AppConfig(**resolved)


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config
