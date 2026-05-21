import asyncio
import json
import time
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from src.agent import Agent, convert_openai_messages
from src.config import AppConfig

from .miservice import MiAccount, MiIOService, MiNAService


LATEST_ASK_API = (
    "https://userprofile.mina.mi.com/device_profile/v2/conversation"
    "?source=dialogu&hardware={hardware}&timestamp={timestamp}&limit=2"
)


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
        self.device_id: str = ""
        self.mi_did: str = ""

        self._last_query: str = ""
        self._seen_request_ids: set[str] = set()
        self._history_primed = False
        self._poll_count = 0
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
                ids = (dev.get("deviceID"), dev.get("did"), dev.get("miotDID"))
                if target_id in ids:
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

        self.device_id = str(self.device.get("deviceID") or "")
        self.mi_did = await self._resolve_mi_did(devices)

        print(f"[XiaoAi] 已连接设备: {self.device.get('name')} "
              f"({self.device.get('model')}) deviceID={self.device_id} miotDID={self.mi_did or '未知'}")
        print(
            "[XiaoAi] 监听配置: "
            f"hardware={self.xiaomi_cfg.hardware}, use_command={self.xiaomi_cfg.use_command}, "
            f"mute_xiaoai={self.xiaomi_cfg.mute_xiaoai}, "
            f"trigger_word={self.xiaomi_cfg.trigger_word or '空'}, "
            f"polling_interval={self.xiaomi_cfg.polling_interval}s, "
            f"debug={self.xiaomi_cfg.debug}"
        )
        if self.xiaomi_cfg.use_command and not self.mi_did:
            print("[XiaoAi] 警告: 未找到 miotDID，L05C 的 TTS/MIOT 命令可能无法发送")
        await self._prime_history()

    async def _resolve_mi_did(self, mina_devices: list[dict[str, Any]]) -> str:
        """查找 MIOT 命令需要使用的数字 did。"""
        if self.device is None:
            return ""

        target_id = (self.xiaomi_cfg.did or "").strip()
        if target_id.isdigit():
            return target_id

        for key in ("miotDID", "miotDid", "did"):
            value = self.device.get(key)
            if value:
                return str(value)

        if self.miio is None:
            return ""

        try:
            miio_devices = await self.miio.device_list("full")
        except Exception as exc:
            self._debug(f"读取 MIOT 设备列表失败: {type(exc).__name__}: {exc}")
            return ""

        model = self.device.get("model")
        name = self.device.get("name")
        for dev in miio_devices:
            if model and dev.get("model") == model:
                return str(dev.get("did") or "")
            if name and dev.get("name") == name:
                return str(dev.get("did") or "")
        return ""

    async def run(self, initialized: bool = False) -> None:
        """主轮询循环。"""
        if not initialized:
            await self.init()
        self._running = True
        print("[XiaoAi] 开始监听音箱消息...")

        try:
            while self._running:
                try:
                    await self._poll_once()
                except Exception as exc:
                    print(f"[XiaoAi] 轮询异常: {type(exc).__name__}: {exc}")
                await asyncio.sleep(self.xiaomi_cfg.polling_interval)
        finally:
            await self.close()

    async def _poll_once(self) -> None:
        """单次轮询：查询最近对话，检测新用户消息。"""
        if self.device is None:
            return

        record = await self._get_latest_ask()
        if not record:
            return

        current_text = str(record.get("query") or "").strip()
        if not current_text:
            self._debug(f"最近对话没有 query 字段: {self._compact(record)}")
            return

        # 忽略重复消息和空消息
        if current_text == self._last_query:
            self._debug(f"跳过重复输入: {current_text}")
            return
        self._last_query = current_text

        # 过滤掉系统提示、音乐播放等非用户输入内容
        ignore_reason = self._should_ignore(current_text)
        if ignore_reason:
            print(f"[XiaoAi] 忽略消息({ignore_reason}): {current_text}")
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
        self._debug(f"对话记录: {self._compact(record)}")

        if self.xiaomi_cfg.mute_xiaoai:
            await self._stop_playing(self.device_id)

        # 调用 Agent
        response = await self._chat(query_text)
        print(f"[XiaoAi] Agent 回复: {response}")

        # 发送 TTS
        await self._speak(self.device_id, response)

    # ---------- 通信方法 ----------

    async def _prime_history(self) -> None:
        """启动时标记已有对话，避免重放历史问题。"""
        records = await self._fetch_conversation_records()
        if records is None:
            return
        for record in records:
            self._seen_request_ids.add(self._record_id(record))
        self._history_primed = True
        self._debug(f"已标记 {len(records)} 条历史对话，等待新的用户输入")

    async def _get_latest_ask(self) -> dict[str, Any] | None:
        """从小米对话记录接口读取最近一次用户提问。"""
        records = await self._fetch_conversation_records()
        if records is None:
            return None

        if not self._history_primed:
            for record in records:
                self._seen_request_ids.add(self._record_id(record))
            self._history_primed = True
            self._debug(f"首次轮询已标记 {len(records)} 条历史对话")
            return None

        unseen = [
            record
            for record in records
            if self._record_id(record) not in self._seen_request_ids
        ]
        if not unseen:
            if self.xiaomi_cfg.debug and self._poll_count % 10 == 0:
                self._debug(f"暂无新的用户输入，最近记录: {self._compact(records[:2])}")
            return None

        unseen.sort(key=lambda item: int(item.get("time") or 0))
        record = unseen[0]
        self._seen_request_ids.add(self._record_id(record))
        return record

    async def _fetch_conversation_records(self) -> list[dict[str, Any]] | None:
        """读取并解析小米最近对话记录。"""
        if not self.account or not self.account.token:
            return None

        token = self.account.token
        mico_token = token.get("micoapi")
        if not self.device_id or not mico_token:
            self._debug("缺少 deviceID 或 micoapi token，无法读取对话记录")
            return None

        self._poll_count += 1
        timestamp = int(time.time() * 1000)
        url = LATEST_ASK_API.format(
            hardware=self.xiaomi_cfg.hardware,
            timestamp=timestamp,
        )
        cookies = {
            "deviceId": self.device_id,
            "serviceToken": mico_token[1],
            "userId": token.get("userId", ""),
        }
        headers = {
            "User-Agent": (
                "MiHome/6.0.103 (com.xiaomi.mihome; build:6.0.103.1; "
                "iOS 14.4.0) MICO/iOSApp/appStore/6.0.103"
            )
        }

        session = await self._ensure_session()
        try:
            async with session.get(
                url,
                cookies=cookies,
                headers=headers,
                timeout=ClientTimeout(total=15),
            ) as resp:
                raw = await resp.text()
                if resp.status != 200:
                    print(f"[XiaoAi] 读取对话记录失败: HTTP {resp.status} {raw[:200]}")
                    return None
        except Exception as exc:
            print(f"[XiaoAi] 读取对话记录异常: {type(exc).__name__}: {exc}")
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"[XiaoAi] 对话记录响应不是 JSON: {raw[:200]}")
            return None

        records = self._extract_records(data)
        if records is None and self.xiaomi_cfg.debug and self._poll_count % 10 == 0:
            self._debug(f"对话记录响应格式异常: {self._compact(data)}")
        return records

    def _extract_records(self, data: dict[str, Any]) -> list[dict[str, Any]] | None:
        payload = data.get("data", data)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                self._debug(f"data 字段不是 JSON: {payload[:200]}")
                return None

        if not isinstance(payload, dict):
            return None

        records = payload.get("records") or []
        return [record for record in records if isinstance(record, dict)]

    @staticmethod
    def _record_id(record: dict[str, Any]) -> str:
        request_id = str(record.get("requestId") or "").strip()
        if request_id:
            return request_id
        return f"{record.get('time', '')}:{record.get('query', '')}"

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
        except Exception as exc:
            self._debug(f"ubus 播放状态查询失败: {type(exc).__name__}: {exc}")
        return None

    async def _miio_player_get_status(self, device_id: str) -> dict[str, Any] | None:
        """通过 MiIOService 查询播放状态（L05C 用 miio_command 方式）。"""
        if self.miio is None:
            return None
        if not self.mi_did:
            self._debug("缺少 miotDID，跳过 MIOT 播放状态查询")
            return None
        try:
            result = await self.miio.home_request(
                self.mi_did,
                'player_get_play_status',
                {"status": -1, "play_song_detail": 1}
            )
            return result if isinstance(result, dict) else None
        except Exception as exc:
            self._debug(f"MIOT 播放状态查询失败: {type(exc).__name__}: {exc}")
            return None

    async def _speak(self, device_id: str, text: str) -> bool:
        """发送 TTS 让音箱朗读文本。"""
        if self.mina is None:
            return False
        try:
            if self.xiaomi_cfg.use_command:
                if self.miio is None or not self.mi_did:
                    print("[XiaoAi] TTS 发送失败: 缺少 MIOT 服务或 miotDID")
                    return False
                cmd = tuple(self.xiaomi_cfg.tts_command)
                code = await self.miio.miot_action(self.mi_did, cmd, [text])
                if code != 0:
                    print(f"[XiaoAi] TTS 发送失败: MIOT action 返回 code={code}")
                    return False
            else:
                # LX06: 直接 text_to_speech
                await self.mina.text_to_speech(device_id, text)
            print("[XiaoAi] TTS 已发送")
            return True
        except Exception as exc:
            print(f"[XiaoAi] TTS 发送失败: {exc}")
            return False

    async def _stop_playing(self, device_id: str) -> bool:
        """停止当前播放（用于屏蔽小爱原生回复）。"""
        if self.mina is None:
            return False
        try:
            ok = await self.mina.ubus_request(
                device_id, 'player_play_operation', 'mediaplayer', {'action': 'stop'}
            )
            self._debug(f"发送 stop 命令结果: {ok}")
            return bool(ok)
        except Exception as exc:
            self._debug(f"发送 stop 命令失败: {type(exc).__name__}: {exc}")
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

    def _should_ignore(self, text: str) -> str | None:
        """判断文本是否为系统消息或非用户输入，需要忽略。"""
        normalized = text.replace("，", "").replace(",", "").strip()
        if normalized in {"小爱", "小爱同学"}:
            return "唤醒词"

        ignore_patterns = [
            "正在为您",
            "请稍等",
            "马上",
            "播放",
            "http",
            "https",
        ]
        for pattern in ignore_patterns:
            if pattern in text:
                return pattern
        if len(text) < 2:
            return "文本过短"
        return None

    def _debug(self, message: str) -> None:
        if self.xiaomi_cfg.debug:
            print(f"[XiaoAi][debug] {message}")

    @staticmethod
    def _compact(value: Any, limit: int = 800) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str)
        return text if len(text) <= limit else text[:limit] + "..."

    async def close(self) -> None:
        self._running = False
        if self._session and not self._session.closed:
            await self._session.close()
