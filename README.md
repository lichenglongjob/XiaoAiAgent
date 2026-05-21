# XiaoAi Agent

小爱音箱 Play 增强版（L05C）+ LangGraph Agent，内置小米音箱通信，一条命令启动。

## 架构

```
小爱音箱 Play 增强版 (L05C)
    ↓ 语音交互
XiaoAiSpeakerClient（内置 miservice，轮询小米对话记录）
    ↓ 检测到用户语音
LangGraph Agent（DeepSeek / 可切换）
    ├─ 本地 Tools（Python 函数注册）
    ├─ MCP Tools（远程 SSE 接入）
    └─ Skills（LangGraph 子图）
    ↓ 生成回复
XiaoAiSpeakerClient —— TTS 发送回音箱

FastAPI（OpenAI-compatible，可选，供外部调用）
```

项目已将 [xiaogpt](https://github.com/yihong0618/xiaogpt) 的核心通信逻辑（miservice）内置，**无需单独安装 xiaogpt**。

## 安装

```bash
# 克隆项目后进入目录
cd myXiaoAi

# 安装依赖（推荐用 uv）
pip install -e .

# 或
uv pip install -e .
```

## 配置

编辑 `config.yaml`：

```yaml
llm:
  provider: "deepseek"          # 可选: deepseek, openai, openai-compatible, ollama, anthropic
  api_key: "${DEEPSEEK_API_KEY}" # 会从环境变量读取
  model: "deepseek-chat"
  base_url: "https://api.deepseek.com"
  temperature: 0.7

server:
  host: "0.0.0.0"
  port: 8000

# MCP 服务配置（远程 SSE，按需添加）
mcp_servers:
# - name: "filesystem"
#   url: "http://localhost:3001/sse"
```

或在启动前设置环境变量：

```bash
export DEEPSEEK_API_KEY="sk-xxxxxxxx"
```

## 切换模型

只需修改 `config.yaml` 中的 `llm` 配置节即可切换不同的大模型。

### DeepSeek（默认）

```yaml
llm:
  provider: "deepseek"
  api_key: "${DEEPSEEK_API_KEY}"
  model: "deepseek-chat"
  base_url: "https://api.deepseek.com"
```

> `deepseek-chat` 支持工具调用，`deepseek-reasoner` 不支持。

### OpenAI

```yaml
llm:
  provider: "openai"
  api_key: "${OPENAI_API_KEY}"
  model: "gpt-4o-mini"
  base_url: "https://api.openai.com/v1"
```

### 通用 OpenAI-compatible（硅基流动、OneAPI、本地 vLLM 等）

```yaml
llm:
  provider: "openai-compatible"
  api_key: "你的API Key"
  model: "Qwen/Qwen2.5-7B-Instruct"
  base_url: "https://api.siliconflow.cn/v1"
```

任何兼容 OpenAI `/v1/chat/completions` 的接口都可以复用此 provider。

### Ollama（本地模型）

```bash
# 先安装额外依赖
pip install langchain-ollama
```

```yaml
llm:
  provider: "ollama"
  model: "qwen2.5:7b"
  base_url: "http://localhost:11434"
```

### Anthropic Claude

```bash
# 先安装额外依赖
pip install langchain-anthropic
```

```yaml
llm:
  provider: "anthropic"
  api_key: "${ANTHROPIC_API_KEY}"
  model: "claude-3-5-sonnet-20241022"
```

### 其他参数

`extra` 字段可传入任意模型支持的额外参数：

```yaml
llm:
  provider: "deepseek"
  model: "deepseek-chat"
  extra:
    max_tokens: 2048
    top_p: 0.9
```

## 启动

### 配置

编辑 `config.yaml`，填入小米账号和 LLM API Key：

```yaml
llm:
  provider: "deepseek"
  api_key: "${DEEPSEEK_API_KEY}"   # 或直接在引号内填写
  model: "deepseek-chat"

xiaomi:
  username: "${MI_USER}"           # 小米账号
  password: "${MI_PASS}"           # 小米密码
  did: ""                           # 设备ID（多设备时填，留空自动选）
  hardware: "L05C"                 # Play增强版=L05C, Pro=LX06
  use_command: true                # L05C 必须 true
  mute_xiaoai: false               # 是否屏蔽小爱原生回复（见下方说明）
  trigger_word: ""                 # 触发词，如"问AI"，为空则所有对话都走Agent
  tts_command: [5, 3]              # L05C TTS action
  wake_command: [5, 4]
  debug: false                     # 排查监听时可改为 true
```

或在启动前设置环境变量：

```bash
export DEEPSEEK_API_KEY="sk-xxx"
export MI_USER="你的小米账号"
export MI_PASS="你的小米密码"
```

### 一键启动

```bash
source .venv/Scripts/activate
python main.py
```

服务同时做两件事：

1. **监听小爱音箱** — 检测到语音输入后调用 Agent，TTS 回复
2. **FastAPI 服务** — 提供 OpenAI-compatible 接口（`http://localhost:8000`）

### 两种工作模式

#### 模式A：Agent 接管全部回答（推荐）

```yaml
xiaomi:
  mute_xiaoai: true      # 屏蔽小爱原生回复
  trigger_word: ""       # 留空，所有对话都走 Agent
```

原理：你说出问题后，Agent 立即生成回答，同时发送 `stop` 命令**打断**小爱音箱自己的语音，然后由 Agent 朗读回复。体验上就是你问、Agent
答，小爱不会插嘴。

#### 模式B：保留小爱原生能力 + 触发词

如果你希望平时小爱正常用，只在特定场景走 Agent：

```yaml
xiaomi:
  mute_xiaoai: false     # 不屏蔽小爱原生回复
  trigger_word: "帮我"   # 只有包含这个词才走 Agent
```

使用时：

- "小爱同学，北京天气" → 小爱原生回答
- "小爱同学，**帮我** 北京天气" → Agent 回答

### 使用小爱音箱交互

对小爱说"小爱同学"唤醒，然后直接说出你的问题。

> 小爱音箱 Play 增强版（L05C）不支持连续对话，每次唤醒后一问一答。

### 获取设备 DID（可选）

如果有多台小爱音箱，需要指定 DID。先配置好小米账号，然后运行：

```bash
python main.py --list-devices
```

输出示例：

```
找到 3 个设备:

序号 名称                 型号                       DID                  在线
--------------------------------------------------------------------------------
1    小爱音箱Pro          xiaomi.wifispeaker.lx06    123456789            是
2    小爱音箱Play增强版   xiaomi.wifispeaker.l05c    987654321            是
3    米家台灯             yeelink.light.lamp1        555555555            否

将目标设备的 DID 填入 config.yaml 的 xiaomi.did 字段即可指定该设备。
```

### Docker Compose（可选）

```bash
cp .env.example .env
# 编辑 .env 填入配置
docker compose up -d
```

## 接口测试

```bash
# 测试模型列表
curl http://localhost:8000/v1/models

# 测试对话（非流式）
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "北京天气怎么样"}]
  }'

# 测试流式
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

## 扩展工具

### 本地工具

在 `src/tools/builtin.py` 中添加：

```python
from langchain_core.tools import tool


@tool
def my_tool(param: str) -> str:
    """工具描述，会被 LLM 看到。"""
    return f"结果: {param}"
```

然后在 `register_builtin_tools()` 中返回它。

### MCP 工具

在 `config.yaml` 的 `mcp_servers` 中添加 SSE 端点即可自动加载。

### Skill

继承 `src/skills/base.py` 中的 `Skill` 类，实现 `name`、`description`、`build_graph()` 方法，然后将 Skill 作为工具注册到
Agent。

## 故障排查

### 登录失败

运行 `python main.py --list-devices` 提示登录失败：

| 现象                | 原因          | 解决                                      |
|-------------------|-------------|-----------------------------------------|
| `小米账号或密码错误`       | 账号密码填错      | 检查 config.yaml 或环境变量                    |
| `需要验证码`           | 新设备/新 IP 登录 | 先用浏览器登录 https://account.xiaomi.com 完成验证 |
| 无明确错误码            | 两步验证开启      | 在小米账号安全设置中关闭二次验证                        |
| 密码含 `# $ %` 等特殊字符 | YAML 解析错误   | 用环境变量 `MI_PASS=xxx` 传入，而非 config.yaml   |

**测试账号是否正常：**

```bash
pip install xiaogpt[locked]
xiaogpt --hardware L05C --account 你的账号 --password 你的密码
```

如果 xiaogpt 官方工具能登录成功但本项目失败，请提 issue。

### 监听没有触发

先把 `config.yaml` 里的 `xiaomi.debug` 改成 `true`，重启后观察日志：

- 看到 `[Main] 小米音箱监听启动失败`：登录、验证码、设备 ID 或网络阶段失败。
- 看到 `[XiaoAi][debug] 已标记 N 条历史对话`：启动时已跳过旧对话，之后的新语音才会触发 Agent。
- 看到 `[XiaoAi][debug] 暂无新的用户输入`：监听已运行，但小米对话记录里还没有新 query。
- 看到 `[XiaoAi] 忽略消息(...)`：命中了过滤条件，日志里会写具体原因。
- 看到 `[XiaoAi] 检测到用户输入` 但没有声音：优先检查 `tts_command`、`miotDID` 和 `use_command`。

### 其他

- L05C 不支持连续对话，每次问答为一个独立回合
- 工具调用需要模型本身支持 function calling，不是所有模型都可用
- MCP SSE 调用有网络延迟，请确保 MCP 服务稳定
- L05C 使用 miio_command 协议，响应略慢于 LX06
- 首次登录会生成 `.mi.token` 文件保存登录态，后续启动无需重新登录
- 如果设备列表为空，检查小米账号是否正确，或尝试在米家 App 中重新绑定设备
