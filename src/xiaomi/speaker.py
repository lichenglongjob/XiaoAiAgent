import asyncio
from typing import Any

from aiohttp import ClientSession

from src.agent import Agent, convert_openai_messages
from src.config import AppConfig

from .miservice import MiAccount, MiIOService, MiNAService


class XiaoAiSpeakerClient:
    """小爱音箱客户端：轮询检测语音输入，调用 Agent，发送 TTS 回复。

    参考 xiaogpt 核心逻辑，使用 miservice 直接与小米云服务通信。
    """

    def __init__(self, config: AppConfig, agent: Agent) -> None:
        self.config = config
        self.agent = agent
        self.xiaomi_cfg = config.xiaomi

        self.account: MiAccount | None = None
        self.mina: MiNAService | None = None
        self.miio: MiIOService | None = None
        self.device: dict[str, Any] | None = None

        self._last_query: str = ""
        self._session: ClientSession | None = None
        self._running = False

    async def _ensure_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession()
        return self._session

    async def init(self) -> None:
        """登录小米账号并查找目标音箱设备。"""
        session = await self._ensure_session()
        self.account = MiAccount(
            session=session,
            username=self.xiaomi_cfg.username,
            password=self.xiaomi_cfg.password,
        )
        self.mina = MiNAService(self.account)
        self.miio = MiIOService(self.account)

        devices = await self.mina.device_list()
        if not devices:
            raise RuntimeError("未找到任何小爱音箱设备，请检查小米账号")

        # 如果配置了 deviceID，按 deviceID 查找；否则用第一个在线设备
        target_id = self.xiaomi_cfg.did
        if target_id:
            for dev in devices:
                if dev.get("deviceID") == target_id or dev.get("did") == target_id:
                    self.device = dev
                    break
            if self.device is None:
                raise RuntimeError(f"未找到指定 deviceID 的设备: {target_id}")
        else:
            for dev in devices:
                if dev.get("online"):
                    self.device = dev
                    break
            if self.device is None:
                self.device = devices[0]

        print(f"[XiaoAi] 已连接设备: {self.device.get('name')} "
              f"({self.device.get('model')}) deviceID={self.device.get('deviceID')}")

    async def run(self) -> None:
        """主轮询循环。"""
        await self.init()
        self._running = True
        print("[XiaoAi] 开始监听音箱消息...")

        while self._running:
            try:
                await self._poll_once()
            except Exception as exc:
                print(f"[XiaoAi] 轮询异常: {exc}")
            await asyncio.sleep(self.xiaomi_cfg.polling_interval)

    async def _poll_once(self) -> None:
        """单次轮询：查询播放状态，检测新消息。"""
        if self.device is None:
            return

        device_id = self.device["deviceID"]

        if self.xiaomi_cfg.use_command:
            # L05C/L05B 模式：通过 miio_command 查询
            result = await self._miio_player_get_status(device_id)
        else:
            # LX06 等支持 ubus 的模式
            result = await self._ubus_player_get_status(device_id)

        if not result:
            return

        current_text = self._extract_text(result)
        if not current_text:
            return

        # 忽略重复消息和空消息
        if current_text == self._last_query:
            return
        self._last_query = current_text

        # 过滤掉系统提示、音乐播放等非用户输入内容
        if self._should_ignore(current_text):
            return

        # 触发词检查：如果配置了 trigger_word，只有包含触发词才走 Agent
        trigger = self.xiaomi_cfg.trigger_word
        query_text = current_text
        if trigger:
            if trigger not in current_text:
                print(f"[XiaoAi] 未包含触发词'{trigger}'，跳过: {current_text}")
                return
            # 去掉触发词，保留实际内容
            query_text = current_text.replace(trigger, "").strip()
            if not query_text:
                query_text = current_text

        print(f"[XiaoAi] 检测到用户输入: {current_text}")

        # 调用 Agent
        response = await self._chat(query_text)
        print(f"[XiaoAi] Agent 回复: {response}")

        # 发送 TTS
        if self.xiaomi_cfg.mute_xiaoai:
            # 先发送 stop 命令停止小爱原生回复
            await self._stop_playing(device_id)

        await self._speak(device_id, response)

    # ---------- 通信方法 ----------

    async def _ubus_player_get_status(self, device_id: str) -> dict[str, Any] | None:
        """通过 MiNAService ubus 查询播放状态（LX06 等支持）。"""
        if self.mina is None:
            return None
        try:
            result = await self.mina.mina_request(
                '/remote/ubus',
                {
                    'deviceId': device_id,
                    'message': '{"status":-1,"play_song_detail":1}',
                    'method': 'player_get_play_status',
                    'path': 'mediaplayer',
                }
            )
            if result and result.get('code') == 0:
                return result.get('data')
        except Exception:
            pass
        return None

    async def _miio_player_get_status(self, device_id: str) -> dict[str, Any] | None:
        """通过 MiIOService 查询播放状态（L05C 用 miio_command 方式）。"""
        if self.miio is None:
            return None
        try:
            result = await self.miio.home_request(
                self.device["did"],
                'player_get_play_status',
                {"status": -1, "play_song_detail": 1}
            )
            return result if isinstance(result, dict) else None
        except Exception:
            return None

    async def _speak(self, device_id: str, text: str) -> bool:
        """发送 TTS 让音箱朗读文本。"""
        if self.mina is None:
            return False
        try:
            if self.xiaomi_cfg.use_command:
                # L05C: 使用 miio_command 发送 TTS
                # 命令格式: [siid, aiid, text]
                cmd = self.xiaomi_cfg.tts_command + [text]
                await self.miio.home_request(
                    self.device["did"],
                    'play_specify_media',
                    cmd
                )
            else:
                # LX06: 直接 text_to_speech
                await self.mina.text_to_speech(device_id, text)
            return True
        except Exception as exc:
            print(f"[XiaoAi] TTS 发送失败: {exc}")
            return False

    async def _stop_playing(self, device_id: str) -> bool:
        """停止当前播放（用于屏蔽小爱原生回复）。"""
        if self.mina is None:
            return False
        try:
            await self.mina.ubus_request(
                device_id, 'player_play_operation', 'mediaplayer', {'action': 'stop'}
            )
            return True
        except Exception:
            return False

    # ---------- Agent 调用 ----------

    async def _chat(self, text: str) -> str:
        """调用 LangGraph Agent 生成回复。"""
        messages = convert_openai_messages([{"role": "user", "content": text}])
        try:
            response = await self.agent.ainvoke(messages)
            return response.content or ""
        except Exception as exc:
            return f"抱歉，处理出错了: {exc}"

    # ---------- 工具方法 ----------

    @staticmethod
    def _extract_text(result: dict[str, Any]) -> str:
        """从播放状态结果中提取文本内容。"""
        # 不同音箱返回格式不同，尝试多种路径
        if isinstance(result, dict):
            info = result.get("info", {})
            if isinstance(info, dict):
                return info.get("title", "") or info.get("text", "") or ""
        return ""

    @staticmethod
    def _should_ignore(text: str) -> bool:
        """判断文本是否为系统消息或非用户输入，需要忽略。"""
        ignore_patterns = [
            "正在为您",
            "小爱",
            "请稍等",
            "马上",
            "播放",
            "http",
            "https",
        ]
        for pattern in ignore_patterns:
            if pattern in text:
                return True
        return len(text) < 2  # 忽略太短的文本

    async def close(self) -> None:
        self._running = False
        if self._session and not self._session.closed:
            await self._session.close()
