"""CLI entry point for wechat-bot-cli.

Registers all top-level commands: login, send, send-file, listen, typing,
accounts, and logout.  The Click group ``cli`` is also the console-script
entry point declared in ``pyproject.toml``.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import click

from wechat_bot_cli.cli.helpers import (
    async_run,
    create_client,
    get_token_store,
    resolve_account,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def cli(verbose: bool) -> None:
    """wechat-bot-cli -- command-line interface for the WeChat Claw bot API."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--name", default=None, help="Human-readable label for the account.")
@click.option("--base-url", default=None, help="Override the API base URL.")
def login(name: Optional[str], base_url: Optional[str]) -> None:
    """Authenticate via QR code and save credentials."""
    from wechat_bot_cli.auth.qr_login import QRLogin

    store = get_token_store()
    qr = QRLogin(base_url=base_url)

    async def _login():
        return await qr.login_and_save(store, name=name)

    try:
        creds = async_run(_login())
        click.echo(f"Logged in as {creds['account_id']}")
    except KeyboardInterrupt:
        click.echo("\nLogin cancelled.", err=True)
        sys.exit(130)
    except Exception as exc:
        click.echo(f"Login failed: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("to")
@click.argument("message")
@click.option("--account", default=None, help="Account ID to send from.")
def send(to: str, message: str, account: Optional[str]) -> None:
    """Send a text message to a user."""
    from wechat_bot_cli.messaging.context_store import ContextStore
    from wechat_bot_cli.messaging.sender import MessageSender

    acct = resolve_account(account)
    client = create_client(acct)

    async def _send():
        try:
            ctx_store = ContextStore()
            sender = MessageSender(client, ctx_store, account_id=acct.get("id", ""))
            result = await sender.send_text(to, message)
            click.echo(f"Sent (client_id={result.client_id})")
        finally:
            await client.close()

    try:
        async_run(_send())
    except Exception as exc:
        click.echo(f"Send failed: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# send-file
# ---------------------------------------------------------------------------


@cli.command("send-file")
@click.argument("to")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--account", default=None, help="Account ID to send from.")
def send_file(to: str, file_path: str, account: Optional[str]) -> None:
    """Upload and send a file to a user."""
    import base64

    from wechat_bot_cli.media.mime_utils import guess_media_type
    from wechat_bot_cli.media.uploader import CDNUploader
    from wechat_bot_cli.messaging.context_store import ContextStore
    from wechat_bot_cli.messaging.sender import MessageSender
    from wechat_bot_cli.core.models import UploadMediaType

    acct = resolve_account(account)
    client = create_client(acct)

    async def _send_file():
        try:
            media_type = guess_media_type(Path(file_path).name)
            uploader = CDNUploader(client, cdn_base_url=acct.get("cdn_base_url") or "")
            upload_result = await uploader.upload_file(file_path, to_user_id=to)

            ctx_store = ContextStore()
            sender = MessageSender(client, ctx_store, account_id=acct.get("id", ""))

            # aes_key: base64-encode the hex string's UTF-8 bytes (matches TS implementation)
            aes_key_b64 = base64.b64encode(
                upload_result.aes_key_hex.encode("utf-8")
            ).decode("ascii")

            upload_info = {
                "download_encrypted_query_param": upload_result.download_encrypted_query_param,
                "aes_key": aes_key_b64,
                "file_size": upload_result.file_size,
                "file_size_ciphertext": upload_result.file_size_ciphertext,
                "file_name": Path(file_path).name,
            }
            if upload_result.thumb_download_encrypted_query_param:
                upload_info["thumb_download_encrypted_query_param"] = (
                    upload_result.thumb_download_encrypted_query_param
                )
                upload_info["thumb_aes_key"] = aes_key_b64

            # Route to the correct send method based on media type
            if media_type == UploadMediaType.IMAGE:
                result = await sender.send_image(to, upload_info)
            elif media_type == UploadMediaType.VIDEO:
                result = await sender.send_video(to, upload_info)
            else:
                result = await sender.send_file(
                    to, upload_info, file_name=Path(file_path).name
                )
            click.echo(f"File sent (client_id={result.client_id})")
        finally:
            await client.close()

    try:
        async_run(_send_file())
    except Exception as exc:
        click.echo(f"Send file failed: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# listen
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--account", default=None, help="Account ID to listen on.")
@click.option("--json-output", "json_out", is_flag=True, help="Output messages as JSON lines.")
def listen(account: Optional[str], json_out: bool) -> None:
    """Listen for incoming messages (long-poll)."""
    from wechat_bot_cli.messaging.context_store import ContextStore
    from wechat_bot_cli.messaging.listener import MessageListener

    acct = resolve_account(account)
    client = create_client(acct)

    async def _listen():
        try:
            ctx_store = ContextStore()
            listener = MessageListener(
                client,
                context_store=ctx_store,
                account_id=acct.get("id", ""),
            )
            click.echo("Listening for messages... (Ctrl+C to stop)", err=True)
            async for msg in listener.listen():
                if json_out:
                    click.echo(json.dumps(msg.raw, ensure_ascii=False))
                else:
                    text_parts = []
                    for item in msg.item_list:
                        if item.text_item:
                            text_parts.append(item.text_item.text)
                    text = " ".join(text_parts) if text_parts else "(non-text message)"
                    click.echo(f"[{msg.from_user_id}] {text}")
        except KeyboardInterrupt:
            click.echo("\nStopped listening.", err=True)
        finally:
            await client.close()

    try:
        async_run(_listen())
    except KeyboardInterrupt:
        click.echo("\nStopped listening.", err=True)
    except Exception as exc:
        click.echo(f"Listen failed: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# typing
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("to")
@click.option("--account", default=None, help="Account ID to send from.")
def typing(to: str, account: Optional[str]) -> None:
    """Send a typing indicator to a user."""
    acct = resolve_account(account)
    client = create_client(acct)

    async def _typing():
        try:
            await client.send_typing(ilink_user_id=to)
            click.echo("Typing indicator sent.")
        finally:
            await client.close()

    try:
        async_run(_typing())
    except Exception as exc:
        click.echo(f"Typing failed: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------


@cli.command()
def accounts() -> None:
    """List all saved accounts."""
    store = get_token_store()
    entries = store.list_accounts()
    if not entries:
        click.echo("No accounts saved. Run 'wechat-bot-cli login' to add one.")
        return
    for entry in entries:
        aid = entry.get("id", "?")
        name = entry.get("name")
        label = f" ({name})" if name else ""
        click.echo(f"  {aid}{label}")


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("account_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def logout(account_id: str, yes: bool) -> None:
    """Remove a saved account's credentials."""
    store = get_token_store()

    if not yes:
        click.confirm(
            f"Remove account '{account_id}' and its credentials?",
            abort=True,
        )

    removed = store.remove_account(account_id)
    if removed:
        click.echo(f"Account '{account_id}' removed.")
    else:
        click.echo(f"Account '{account_id}' not found.", err=True)
        sys.exit(1)
