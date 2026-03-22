# wechat-bot-cli

[English](README.md)

面向微信 Claw 机器人 API 的命令行工具和 Python SDK。

## 安装

```bash
uv tool install "wechat-bot-cli @ git+https://github.com/jakezhu9/wechat-bot-cli.git"
```

## 快速开始

**登录** — 终端显示二维码，手机扫码确认后凭证自动保存到 `~/.wechat-bot-cli/`：

```bash
wechat-bot-cli login
```

**接收消息** — 长轮询实时输出，`Ctrl+C` 停止：

```bash
wechat-bot-cli listen
```

**发送消息：**

```bash
wechat-bot-cli send <user_id> "你好！"
```

**发送文件：**

```bash
wechat-bot-cli send-file <user_id> ./document.pdf
```

## CLI 命令参考

| 命令 | 用法 | 说明 |
|------|------|------|
| `login` | `wechat-bot-cli login [--name NAME] [--base-url URL]` | 扫码登录并保存凭证 |
| `send` | `wechat-bot-cli send [--account ID] <to> <message>` | 发送文本消息 |
| `send-file` | `wechat-bot-cli send-file [--account ID] <to> <file_path>` | 上传并发送文件 |
| `listen` | `wechat-bot-cli listen [--account ID] [--json-output]` | 长轮询监听消息 |
| `typing` | `wechat-bot-cli typing [--account ID] <to>` | 发送输入状态指示 |
| `accounts` | `wechat-bot-cli accounts` | 列出已保存的账号 |
| `logout` | `wechat-bot-cli logout [--yes/-y] <account_id>` | 删除账号凭证 |

全局选项：`-v, --verbose` 启用 DEBUG 日志。多账号时用 `--account ID` 指定账号。

## 编程用法

```python
import asyncio
from wechat_bot_cli import WeChatBot

async def main():
    async with WeChatBot(token="...", account_id="...") as bot:
        await bot.send_text(to="user_id", text="Hello!")

        async for msg in bot.listen():
            print(f"[{msg.from_user_id}] {msg.item_list}")

asyncio.run(main())
```

## 开发

```bash
git clone https://github.com/jakezhu9/wechat-bot-cli.git
cd wechat-bot-cli
uv pip install -e ".[dev]"
pytest
```

## 许可证

[MIT](LICENSE)
