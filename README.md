# wechat-bot-cli

[简体中文](README.zh_CN.md)

Command-line tool and Python SDK for the WeChat Claw bot API.

## Installation

```bash
uv tool install "wechat-bot-cli @ git+https://github.com/jakezhu9/wechat-bot-cli.git"
```

## Quick Start

**Login** — a QR code appears in the terminal; scan with your phone to authenticate. Credentials are saved to `~/.wechat-bot-cli/`:

```bash
wechat-bot-cli login
```

**Receive messages** — long-poll to stdout in real time, `Ctrl+C` to stop:

```bash
wechat-bot-cli listen
```

**Send a message:**

```bash
wechat-bot-cli send <user_id> "Hello!"
```

**Send a file:**

```bash
wechat-bot-cli send-file <user_id> ./document.pdf
```

## CLI Command Reference

| Command | Usage | Description |
|---------|-------|-------------|
| `login` | `wechat-bot-cli login [--name NAME] [--base-url URL]` | QR code login and save credentials |
| `send` | `wechat-bot-cli send [--account ID] <to> <message>` | Send a text message |
| `send-file` | `wechat-bot-cli send-file [--account ID] <to> <file_path>` | Upload and send a file |
| `listen` | `wechat-bot-cli listen [--account ID] [--json-output]` | Listen for incoming messages |
| `typing` | `wechat-bot-cli typing [--account ID] <to>` | Send a typing indicator |
| `accounts` | `wechat-bot-cli accounts` | List saved accounts |
| `logout` | `wechat-bot-cli logout [--yes/-y] <account_id>` | Remove account credentials |

Global option: `-v, --verbose` enables DEBUG logging. Use `--account ID` to select an account in multi-account setups.

## Programmatic Usage

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

## Development

```bash
git clone https://github.com/jakezhu9/wechat-bot-cli.git
cd wechat-bot-cli
uv pip install -e ".[dev]"
pytest
```

## License

[MIT](LICENSE)
