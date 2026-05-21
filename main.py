import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage

from src.agent import (
    Agent,
    convert_lc_messages_to_openai,
    convert_openai_messages,
    create_agent,
)
from src.config import AppConfig, get_config
from src.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChoiceDelta,
    ModelInfo,
    ModelsResponse,
)
from src.tools.registry import ToolRegistry, get_tool_registry
from src.xiaomi.speaker import XiaoAiSpeakerClient

_agent: Agent | None = None
_config: AppConfig | None = None
_speaker_task: asyncio.Task | None = None


def _is_configured_value(value: str | None) -> bool:
    if not value:
        return False
    value = value.strip()
    return not (value.startswith("${") and value.endswith("}"))


def _has_xiaomi_credentials(config: AppConfig) -> bool:
    return _is_configured_value(config.xiaomi.username) and _is_configured_value(
        config.xiaomi.password
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent, _config, _speaker_task
    _config = get_config()
    registry = await get_tool_registry(_config)
    _agent = create_agent(_config, registry)

    # 如果配置了小米账号，同时启动音箱监听
    if _has_xiaomi_credentials(_config):
        speaker = XiaoAiSpeakerClient(_config, _agent)
        try:
            await speaker.init()
        except Exception as exc:
            print(f"[Main] 小米音箱监听启动失败: {type(exc).__name__}: {exc}")
            await speaker.close()
        else:
            _speaker_task = asyncio.create_task(speaker.run(initialized=True))
            print("[Main] 小米音箱监听已启动")
    else:
        print("[Main] 未配置有效小米账号，仅启动 FastAPI 服务")

    yield

    if _speaker_task:
        _speaker_task.cancel()
        try:
            await _speaker_task
        except asyncio.CancelledError:
            pass
    _agent = None


app = FastAPI(title="MyXiaoAI Agent", lifespan=lifespan)


@app.get("/v1/models")
async def list_models():
    return ModelsResponse(
        data=[
            ModelInfo(id="deepseek-chat"),
            ModelInfo(id="myxiaoai-agent"),
        ]
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if _agent is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Agent not initialized"},
        )

    messages = convert_openai_messages(
        [msg.model_dump(exclude_none=True) for msg in request.messages]
    )

    if request.stream:
        return StreamingResponse(
            _stream_response(request.model, messages),
            media_type="text/event-stream",
        )

    try:
        response_message = await _agent.ainvoke(messages)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": {"message": str(exc), "type": type(exc).__name__}},
        )

    openai_msg = convert_lc_messages_to_openai([response_message])[0]

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=request.model,
        choices=[
            Choice(
                message=ChatMessage(
                    role="assistant",
                    content=openai_msg.get("content", ""),
                    tool_calls=openai_msg.get("tool_calls"),
                ),
                finish_reason="stop",
            )
        ],
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )


async def _stream_response(
    model: str, messages: list[Any]
) -> AsyncGenerator[str, None]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    first_chunk = ChatCompletionChunk(
        id=completion_id,
        created=created,
        model=model,
        choices=[ChoiceDelta(delta={"role": "assistant"})],
    )
    yield f"data: {first_chunk.model_dump_json()}\n\n"

    full_content = ""
    async for msg, _ in _agent.astream(messages):
        if isinstance(msg, AIMessage) and msg.content:
            delta = msg.content[len(full_content) :]
            full_content = msg.content
            if delta:
                chunk = ChatCompletionChunk(
                    id=completion_id,
                    created=created,
                    model=model,
                    choices=[ChoiceDelta(delta={"content": delta})],
                )
                yield f"data: {chunk.model_dump_json()}\n\n"

    final_chunk = ChatCompletionChunk(
        id=completion_id,
        created=created,
        model=model,
        choices=[ChoiceDelta(delta={}, finish_reason="stop")],
    )
    yield f"data: {final_chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


async def _list_xiaomi_devices() -> None:
    """查询小米账号下的所有设备列表，用于获取 DID。"""
    from aiohttp import ClientSession
    from src.xiaomi.miservice import MiAccount, MiNAService

    cfg = get_config()
    if not _has_xiaomi_credentials(cfg):
        print("错误：请先配置小米账号和密码（config.yaml 或环境变量 MI_USER / MI_PASS）")
        return

    print(f"正在登录小米账号: {cfg.xiaomi.username} ...")
    try:
        async with ClientSession() as session:
            account = MiAccount(
                session=session,
                username=cfg.xiaomi.username,
                password=cfg.xiaomi.password,
            )
            mina = MiNAService(account)
            devices = await mina.device_list()
    except Exception as exc:
        print(f"登录或查询失败: {exc}")
        print()
        print("常见原因及解决方法：")
        print("  1. 账号密码错误 — 确认 config.yaml 或环境变量中的 MI_USER / MI_PASS 正确")
        print("  2. 账号格式 — 手机号不要带 +86，邮箱账号确保能正常登录小米官网")
        print("  3. 两步验证 — 在小米账号安全设置中关闭登录二次验证")
        print("  4. 新设备验证 — 先在浏览器登录 https://account.xiaomi.com 完成验证")
        print("  5. 密码特殊字符 — 如果密码含 # $ % 等特殊字符，尝试用环境变量传入而非 config.yaml")
        print()
        print("也可以先用 xiaogpt 官方工具测试账号是否正常：")
        print("  pip install xiaogpt[locked]")
        print("  xiaogpt --hardware L05C --account 你的账号 --password 你的密码")
        return

    if not devices:
        print("未找到任何设备，请检查账号是否正确，或设备是否在线。")
        return

    print(f"\n找到 {len(devices)} 个设备:\n")
    print(f"{'序号':<4} {'名称':<22} {'型号':<25} {'deviceID':<20} {'在线':<6}")
    print("-" * 80)
    for i, dev in enumerate(devices, 1):
        name = dev.get("name", "N/A")
        model = dev.get("model", "N/A")
        did = dev.get("deviceID") or dev.get("did", "N/A")
        online = "是" if dev.get("online") else "否"
        print(f"{i:<4} {name:<22} {model:<25} {did:<20} {online:<6}")

    print("\n将目标设备的 deviceID 填入 config.yaml 的 xiaomi.did 字段即可指定该设备。")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MyXiaoAI Agent")
    parser.add_argument("--list-devices", action="store_true", help="查询小米账号下的设备列表")
    args = parser.parse_args()

    if args.list_devices:
        asyncio.run(_list_xiaomi_devices())
        return

    import uvicorn

    cfg = get_config()
    uvicorn.run(
        "main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
