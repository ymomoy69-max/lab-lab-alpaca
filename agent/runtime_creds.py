"""Apply user-supplied credentials at runtime (form / API) — never commit secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from .config import SETTINGS
from .safety import LiveTradingBlocked, assert_paper_env

PAPER_ACCOUNT_URL = "https://paper-api.alpaca.markets/v2/account"


@dataclass
class CredentialStatus:
    configured: bool
    paper: bool
    armed: bool
    account: str = ""
    has_gemini: bool = False
    has_openai: bool = False
    source: str = "none"  # none | form | env

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "paper": self.paper,
            "armed": self.armed,
            "account": self.account,
            "has_gemini": self.has_gemini,
            "has_openai": self.has_openai,
            "source": self.source,
        }


_FORM_APPLIED = False


def _has_alpaca_keys() -> bool:
    key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
    return bool(key and secret)


def status() -> CredentialStatus:
    configured = _has_alpaca_keys()
    return CredentialStatus(
        configured=configured,
        paper=bool(SETTINGS.paper),
        armed=bool(SETTINGS.armed),
        account=SETTINGS.expected_account or "",
        has_gemini=bool(SETTINGS.gemini_key),
        has_openai=bool(SETTINGS.openai_key),
        source="form" if _FORM_APPLIED else ("env" if configured else "none"),
    )


def validate_alpaca_paper(api_key: str, api_secret: str) -> dict[str, Any]:
    """Verify keys against the paper trading API. Raises ValueError on failure."""
    api_key = (api_key or "").strip()
    api_secret = (api_secret or "").strip()
    if not api_key or not api_secret:
        raise ValueError("Alpaca API key and secret are required")

    try:
        r = httpx.get(
            PAPER_ACCOUNT_URL,
            headers={
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            },
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"Could not reach Alpaca paper API: {e}") from e

    if r.status_code in (401, 403):
        raise ValueError("Invalid Alpaca API key/secret (paper account required)")
    if r.status_code >= 400:
        raise ValueError(f"Alpaca paper API error {r.status_code}: {r.text[:200]}")

    data = r.json()
    acct = str(data.get("account_number") or data.get("id") or "").strip()
    if not acct:
        raise ValueError("Alpaca response missing account number")
    return {
        "account": acct,
        "equity": data.get("equity"),
        "status": data.get("status"),
        "buying_power": data.get("buying_power"),
    }


def apply_credentials(
    *,
    api_key: str,
    api_secret: str,
    expected_account: str = "",
    armed: bool = False,
    gemini_key: str = "",
    openai_key: str = "",
) -> dict[str, Any]:
    """Validate paper keys, write into process env + SETTINGS. Paper-only."""
    global _FORM_APPLIED

    info = validate_alpaca_paper(api_key, api_secret)
    acct = (expected_account or "").strip() or str(info["account"])

    os.environ["APCA_API_KEY_ID"] = api_key.strip()
    os.environ["APCA_API_SECRET_KEY"] = api_secret.strip()
    os.environ["ALPACA_API_KEY"] = api_key.strip()
    os.environ["ALPACA_SECRET_KEY"] = api_secret.strip()
    os.environ["ALPACA_PAPER"] = "true"
    os.environ["ALPACA_PAPER_TRADE"] = "true"
    os.environ["NEXUS_EXPECTED_ACCOUNT"] = acct
    os.environ["NEXUS_ARMED"] = "yes" if armed else "no"

    if gemini_key.strip():
        os.environ["GEMINI_API_KEY"] = gemini_key.strip()
        SETTINGS.gemini_key = gemini_key.strip()
    if openai_key.strip():
        os.environ["OPENAI_API_KEY"] = openai_key.strip()
        SETTINGS.openai_key = openai_key.strip()

    SETTINGS.paper = True
    SETTINGS.expected_account = acct
    SETTINGS.armed = bool(armed)

    try:
        assert_paper_env(dict(os.environ))
    except LiveTradingBlocked as e:
        clear_credentials()
        raise ValueError(str(e)) from e

    _FORM_APPLIED = True
    return {
        "ok": True,
        "account": acct,
        "equity": info.get("equity"),
        "armed": SETTINGS.armed,
        "paper": True,
    }


def clear_credentials() -> None:
    """Remove secrets from process env/settings (form-supplied only)."""
    global _FORM_APPLIED
    for k in (
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "NEXUS_EXPECTED_ACCOUNT",
        "NEXUS_ARMED",
    ):
        os.environ.pop(k, None)

    os.environ["ALPACA_PAPER"] = "true"
    os.environ["ALPACA_PAPER_TRADE"] = "true"
    SETTINGS.expected_account = ""
    SETTINGS.armed = False
    SETTINGS.paper = True
    SETTINGS.gemini_key = ""
    SETTINGS.openai_key = ""
    _FORM_APPLIED = False
