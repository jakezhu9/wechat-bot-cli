"""Shared CLI helper utilities.

Provides common functions used across all CLI commands: account resolution,
client creation, and an async-to-sync bridge.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

import click

from wechat_bot_cli.auth.token_store import TokenStore
from wechat_bot_cli.core.client import WeChatAPIClient
from wechat_bot_cli.core.constants import DEFAULT_CONFIG_DIR_NAME

logger = logging.getLogger(__name__)


def get_config_dir() -> Path:
    """Return the wechat-bot-cli configuration directory."""
    return Path.home() / DEFAULT_CONFIG_DIR_NAME


def get_token_store() -> TokenStore:
    """Return a :class:`TokenStore` using the default config directory."""
    return TokenStore(config_dir=get_config_dir())


def resolve_account(
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve an account identifier to full credential data.

    If *account* is ``None``, the default (most recently saved) account is
    used.  Exits with an error message if no matching account is found.

    Parameters
    ----------
    account:
        Account ID (raw or normalised), or ``None`` for the default.

    Returns
    -------
    dict
        Credential data including ``token``, ``base_url``, etc.
    """
    store = get_token_store()

    if account:
        data = store.load_account(account)
        if data is None:
            click.echo(f"Error: account '{account}' not found.", err=True)
            sys.exit(1)
        data["id"] = account
        return data

    data = store.get_default_account()
    if data is None:
        click.echo(
            "Error: no accounts found. Please run 'wechat-bot-cli login' first.",
            err=True,
        )
        sys.exit(1)
    return data


def create_client(account_data: Dict[str, Any]) -> WeChatAPIClient:
    """Create an authenticated :class:`WeChatAPIClient` from account data.

    Parameters
    ----------
    account_data:
        Dict with at least ``token`` and ``base_url`` keys.

    Returns
    -------
    WeChatAPIClient
    """
    return WeChatAPIClient(
        base_url=account_data.get("base_url", ""),
        token=account_data["token"],
    )


def async_run(coro: Coroutine) -> Any:
    """Run an async coroutine from synchronous CLI code.

    Uses :func:`asyncio.run` which creates a new event loop, executes the
    coroutine, and shuts down the loop cleanly.

    Parameters
    ----------
    coro:
        The coroutine to execute.

    Returns
    -------
    Any
        The return value of the coroutine.
    """
    return asyncio.run(coro)
