"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    discord_webhook_url: str | None
    test_mode: bool
    state_file: Path
    request_timeout: float
    user_agent: str

    @classmethod
    def from_env(cls) -> "Config":
        timeout_raw = os.getenv("REQUEST_TIMEOUT", "15")
        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise ValueError("REQUEST_TIMEOUT 必須是數字。") from exc
        if timeout <= 0:
            raise ValueError("REQUEST_TIMEOUT 必須大於 0。")

        return cls(
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL") or None,
            test_mode=_as_bool(os.getenv("TEST_MODE")),
            state_file=Path(os.getenv("STATE_FILE", "data/state.json")),
            request_timeout=timeout,
            user_agent=os.getenv(
                "USER_AGENT", "MapleClassicDiscordCenter/1.0 (+GitHub Actions)"
            ),
        )
