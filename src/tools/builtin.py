from datetime import datetime

from langchain_core.tools import tool


@tool
def get_weather(location: str) -> str:
    """查询指定城市的天气情况。"""
    return f"{location}今天天气晴朗，气温 24-32°C，适合外出。"


@tool
def get_current_time() -> str:
    """获取当前的日期和时间。"""
    now = datetime.now()
    return now.strftime("现在是 %Y年%m月%d日 %H时%M分%S秒")


def register_builtin_tools() -> list:
    return [get_weather, get_current_time]
