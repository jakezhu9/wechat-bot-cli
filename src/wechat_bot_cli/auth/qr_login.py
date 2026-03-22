"""QR-code login flow for the WeChat iLink bot API.

This module implements the interactive QR-code authentication that allows
a user to connect a WeChat account to the bot.  The flow is:

1. Request a QR code from the API.
2. Render it in the terminal so the user can scan it with WeChat.
3. Poll the API until the status transitions through
   ``wait`` -> ``scaned`` -> ``confirmed``, handling ``expired`` by
   refreshing the QR code (up to ``MAX_QR_REFRESH_COUNT`` times).
4. Return the resulting credentials (token, account_id, etc.).

Usage::

    login = QRLogin(base_url="https://ilinkai.weixin.qq.com")
    creds = await login.login()

    # Or login and persist in one step:
    store = TokenStore()
    creds = await login.login_and_save(store)
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import time
from typing import Any, Dict, Optional

from wechat_bot_cli.core.client import WeChatAPIClient
from wechat_bot_cli.core.constants import DEFAULT_BASE_URL, MAX_QR_REFRESH_COUNT
from wechat_bot_cli.core.exceptions import LoginTimeoutError, QRCodeExpiredError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# QR rendering helpers
# ---------------------------------------------------------------------------


def _render_qr_terminal(url: str) -> bool:
    """Render *url* as a QR code in the terminal.

    Attempts to use the ``qrcode`` library first (with a compact Unicode
    renderer).  Returns ``True`` if the QR code was printed, ``False`` if
    no suitable library is available.
    """
    # Strategy 1: qrcode library (default dependency)
    try:
        import qrcode  # type: ignore[import-untyped]

        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)

        # Try print_tty first (needs a real TTY).
        try:
            qr.print_tty(out=sys.stderr)
            return True
        except Exception:  # noqa: BLE001
            pass

        # Fallback to print_ascii (works on any stream).
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        output = buf.getvalue()
        if output.strip():
            sys.stderr.write(output)
            sys.stderr.flush()
            return True
    except ImportError:
        pass

    return False


def _print_status(msg: str) -> None:
    """Write a status message to stderr (won't interfere with piped stdout)."""
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# QRLogin
# ---------------------------------------------------------------------------


class QRLogin:
    """Interactive QR-code login for WeChat iLink API.

    Parameters
    ----------
    base_url:
        The iLink API base URL.  Defaults to
        :data:`~wechat_bot_cli.core.constants.DEFAULT_BASE_URL`.
    timeout:
        Overall login timeout in seconds.  After this period the login
        attempt is aborted with a :class:`LoginTimeoutError`.  The default
        is 480 seconds (8 minutes) to allow for slow scanners.
    poll_interval:
        Seconds to wait between successive status polls.  Defaults to 2.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 480,
        poll_interval: float = 2.0,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout = max(timeout, 10)
        self._poll_interval = max(poll_interval, 0.5)

    # -- public API ----------------------------------------------------------

    async def login(self) -> Dict[str, Any]:
        """Run the full QR-code login flow.

        Returns
        -------
        dict
            A credential dict with the following keys:

            - ``token`` (str): Bot authentication token.
            - ``account_id`` (str): The ``ilink_bot_id`` from the API.
            - ``base_url`` (str): API base URL used for this login.
            - ``user_id`` (str | None): WeChat user ID of the scanner.

        Raises
        ------
        LoginTimeoutError
            If the overall timeout elapses before the login is confirmed.
        QRCodeExpiredError
            If the QR code expires more than ``MAX_QR_REFRESH_COUNT``
            times without being scanned.
        """
        client = WeChatAPIClient(base_url=self._base_url)
        try:
            return await self._run_login(client)
        finally:
            await client.close()

    async def login_and_save(
        self,
        store: "TokenStore",  # noqa: F821 – forward reference
        *,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Login and persist the resulting credentials.

        This is a convenience wrapper around :meth:`login` that saves the
        account via the provided :class:`~wechat_bot_cli.auth.token_store.TokenStore`.

        Parameters
        ----------
        store:
            Token store instance for persisting credentials.
        name:
            Optional human-readable label for the account.

        Returns
        -------
        dict
            The same credential dict returned by :meth:`login`.
        """
        creds = await self.login()

        store.save_account(
            creds["account_id"],
            token=creds["token"],
            base_url=creds.get("base_url", self._base_url),
            user_id=creds.get("user_id"),
            name=name,
        )
        _print_status(f"Account saved: {creds['account_id']}")

        return creds

    # -- internal flow -------------------------------------------------------

    async def _run_login(self, client: WeChatAPIClient) -> Dict[str, Any]:
        """Core login loop (separated from client lifecycle management)."""
        deadline = time.monotonic() + self._timeout
        qr_refresh_count = 0
        scanned_printed = False

        # -- Step 1: fetch the initial QR code ------------------------------
        qr_data = await self._fetch_and_display_qr(client)
        qr_code = qr_data["qrcode"]

        # -- Step 2: poll until confirmed -----------------------------------
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("Login timed out after %.0f seconds", self._timeout)
                raise LoginTimeoutError(
                    f"Login timed out after {self._timeout:.0f} seconds. "
                    "Please restart the login process."
                )

            await asyncio.sleep(min(self._poll_interval, remaining))

            status_data = await client.get_qr_status(qr_code)
            status = status_data.get("status", "wait")
            logger.debug(
                "QR status: %s (has_token=%s)",
                status,
                bool(status_data.get("bot_token")),
            )

            if status == "wait":
                continue

            elif status == "scaned":
                if not scanned_printed:
                    _print_status("QR code scanned. Please confirm on your phone...")
                    scanned_printed = True

            elif status == "expired":
                qr_refresh_count += 1
                if qr_refresh_count >= MAX_QR_REFRESH_COUNT:
                    logger.warning(
                        "QR code expired %d times, giving up",
                        MAX_QR_REFRESH_COUNT,
                    )
                    raise QRCodeExpiredError(
                        f"QR code expired {MAX_QR_REFRESH_COUNT} times. "
                        "Please restart the login process."
                    )

                _print_status(
                    f"QR code expired. Refreshing... "
                    f"({qr_refresh_count}/{MAX_QR_REFRESH_COUNT})"
                )
                qr_data = await self._fetch_and_display_qr(client)
                qr_code = qr_data["qrcode"]
                scanned_printed = False

            elif status == "confirmed":
                token = status_data.get("bot_token")
                account_id = status_data.get("ilink_bot_id")
                user_id = status_data.get("ilink_user_id")

                if not token or not account_id:
                    logger.error(
                        "Login confirmed but missing required fields: "
                        "bot_token=%s, ilink_bot_id=%s",
                        bool(token),
                        bool(account_id),
                    )
                    raise LoginTimeoutError(
                        "Login confirmed but the server did not return "
                        "valid credentials. Please try again."
                    )

                _print_status(f"Login successful! Account: {account_id}")
                logger.info(
                    "Login confirmed: account_id=%s, user_id=%s",
                    account_id,
                    user_id or "(none)",
                )

                return {
                    "token": token,
                    "account_id": account_id,
                    "base_url": self._base_url,
                    "user_id": user_id,
                }

            else:
                logger.warning("Unknown QR status: %s", status)

    async def _fetch_and_display_qr(
        self, client: WeChatAPIClient
    ) -> Dict[str, Any]:
        """Fetch a QR code from the API and display it to the user.

        Returns the raw API response dict (containing ``qrcode`` and
        ``qrcode_url`` keys).
        """
        _print_status("Requesting QR code...")
        qr_data = await client.get_qr_code()

        qr_url = qr_data.get("qrcode_img_content") or qr_data.get("qrcode", "")
        qr_code = qr_data.get("qrcode", "")

        if not qr_code:
            raise LoginTimeoutError(
                "Server returned an empty QR code. Please try again."
            )

        logger.info("QR code received (len=%d)", len(qr_code))

        # Try to render in the terminal.
        rendered = _render_qr_terminal(qr_url)

        if not rendered:
            _print_status(
                "Could not render QR code in terminal. "
                "Please reinstall with: uv tool install wechat-bot-cli --reinstall"
            )

        # Always print the URL as a fallback / for accessibility.
        _print_status(f"Or open this URL to scan:\n  {qr_url}")

        return qr_data
