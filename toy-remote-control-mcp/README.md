# Toy Remote Control MCP

通过 MCP (Model Context Protocol) 让 AI 助手远程控制你的智能玩具。

不需要额外软件、不需要无线调试、不需要保持电脑开机。只需要初次配置时用浏览器的开发者工具抓取几个参数。

## 原理

大部分智能玩具的 APP 都有「邀请他人远程控制」功能，会生成一个链接。这个链接在浏览器打开后，网页通过 WebSocket 与玩具的云端服务器通信。我们把 WebSocket 的连接参数抓取下来，就可以用这个 MCP 服务器替代网页，让 AI 来控制玩具。

## 快速开始

### 1. 编译

```bash
cd toy-remote-control-mcp
go build -o toy-mcp .
```

### 2. 抓取参数

1. 在玩具 APP 中生成一个邀请/远程控制链接
2. 在电脑浏览器中打开这个链接
3. 按 `F12` 打开开发者工具（DevTools）
4. 切换到 **Network** 标签页，筛选 **WS**（WebSocket）
5. 在网页上操作一下（比如点击连接），就能看到 WebSocket 连接
6. 记录下：
   - **WebSocket URL**（`wss://...` 或 `ws://...`）
   - **请求头**（Headers）中的认证信息（如果有）
   - **发送的第一条消息**（用于握手/加入房间）
   - **控制指令的格式**（点击 Messages 标签查看发送和接收的消息）

### 3. 配置 MCP

将编译好的 `toy-mcp` 添加到你的 AI 助手的 MCP 配置中：

**Claude Desktop (`claude_desktop_config.json`)：**

```json
{
  "mcpServers": {
    "toy-remote-control": {
      "command": "/path/to/toy-mcp"
    }
  }
}
```

**Claude Code (`~/.claude/settings.json`)：**

```json
{
  "mcpServers": {
    "toy-remote-control": {
      "command": "/path/to/toy-mcp"
    }
  }
}
```

### 4. 使用

告诉 AI 你的 WebSocket 参数，它会帮你连接并控制玩具：

> "请帮我连接玩具，WebSocket 地址是 wss://api.example.com/ws?token=abc123，
> 连接后发送这条消息加入房间：{"type":"join","room":"xyz"}"

## 可用工具

| 工具 | 说明 |
|------|------|
| `connect` | 连接到玩具的 WebSocket 服务器 |
| `send` | 发送原始 JSON 消息（适配任何协议） |
| `vibrate` | 设置振动强度（0-20） |
| `stop` | 停止振动 |
| `pattern` | 执行振动模式序列 |
| `status` | 查看连接状态和最近收到的消息 |
| `disconnect` | 断开连接 |

### 工具详情

**connect** — 连接到玩具服务器
```json
{
  "url": "wss://api.example.com/ws",
  "headers": {"Authorization": "Bearer token123"},
  "handshake_message": {"type": "join", "room": "abc"}
}
```

**send** — 发送任意 JSON 消息（当默认的 vibrate/stop 格式不匹配你的玩具协议时使用）
```json
{
  "message": {"cmd": "set_motor", "speed": 50}
}
```

**vibrate** — 设置振动强度
```json
{
  "intensity": 10,
  "duration_sec": 5
}
```

**pattern** — 振动模式（异步执行）
```json
{
  "steps": [
    {"intensity": 5, "duration_ms": 500},
    {"intensity": 15, "duration_ms": 300},
    {"intensity": 0, "duration_ms": 200}
  ],
  "repeat": 3
}
```

## 适配不同玩具

每个玩具的通信协议不同。`vibrate`、`stop` 使用的是一个默认的 JSON 格式：

```json
{"action": "vibrate", "strength": 10, "timeSec": 0}
```

如果你的玩具使用不同的格式，请用 `send` 工具直接发送正确格式的消息。你可以让 AI 查看你从 DevTools 抓取的消息格式，它会自动用 `send` 工具发送匹配的指令。

## 注意事项

- 邀请链接可能有过期时间，session 超时后需要重新生成
- 如果链接只能在微信小程序打开而无法在浏览器打开，可能需要通过抓包来获取参数
- 延迟取决于玩具云端服务器的响应速度
- 这个工具仅用于控制你自己的设备

## License

MIT
