"""Configuration loading and validation for the IT Insights Lab bot.

All settings come from environment variables, normally via a .env file
(see .env.example). The bot refuses to start with invalid or missing
configuration so failures are obvious and immediate.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Raised when the environment configuration is invalid or incomplete."""


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration."""

    bot_token: str
    group_chat_id: int
    review_chat_id: int
    admin_ids: frozenset[int]
    invite_link: str
    database_path: Path
    log_level: str
    timezone: str = "UTC"
    registration_messages: dict[str, str] = field(default_factory=dict)

    @property
    def is_admin(self) -> "callable":
        """Return a predicate telling whether a Telegram user id is an admin."""
        admin_ids = self.admin_ids

        def _check(user_id: int | None) -> bool:
            return user_id is not None and user_id in admin_ids

        return _check


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value or not value.strip():
        raise ConfigError(
            f"Missing required environment variable {name}. "
            "Copy .env.example to .env and fill in the values."
        )
    return value.strip()


def _parse_chat_id(raw: str, name: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer chat id, got {raw!r}") from exc


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ConfigError(f"ADMIN_IDS contains a non-integer entry: {part!r}") from exc
    if not ids:
        raise ConfigError("ADMIN_IDS must list at least one Telegram user id.")
    return frozenset(ids)


def load_config(env_file: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration from the environment (and optional .env file)."""
    load_dotenv(env_file)  # silently ignores a missing file

    token = _require("BOT_TOKEN")
    group_chat_id = _parse_chat_id(_require("GROUP_CHAT_ID"), "GROUP_CHAT_ID")
    review_chat_id = _parse_chat_id(_require("REVIEW_CHAT_ID"), "REVIEW_CHAT_ID")
    admin_ids = _parse_admin_ids(_require("ADMIN_IDS"))
    invite_link = os.getenv("COHORT_GROUP_INVITE_LINK", "").strip()
    if not invite_link:
        logger.warning("COHORT_GROUP_INVITE_LINK is not set; welcome messages will omit the link.")

    database_path = Path(os.getenv("DATABASE_PATH", "insights_lab.db")).expanduser()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError(f"LOG_LEVEL must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL, got {log_level!r}")

    timezone = os.getenv("TIMEZONE", "UTC").strip() or "UTC"

    registration_messages: dict[str, str] = {}
    for key in ("WELCOME_HEADER", "WELCOME_FOOTER"):
        value = os.getenv(key, "").strip()
        if value:
            registration_messages[key] = value

    return Config(
        bot_token=token,
        group_chat_id=group_chat_id,
        review_chat_id=review_chat_id,
        admin_ids=admin_ids,
        invite_link=invite_link,
        database_path=database_path,
        log_level=log_level,
        timezone=timezone,
        registration_messages=registration_messages,
    )
