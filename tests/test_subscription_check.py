"""Unit tests for mandatory channel subscription checks, REQUIRED_CHANNEL configuration,
check_subscription helper, @require_subscription decorator, and check_sub_callback handler.
"""
from __future__ import annotations

import asyncio
import os
import types
from pathlib import Path

import pytest
from telegram.error import BadRequest, TelegramError

from bot import db
from bot.config import Config, REQUIRED_CHANNEL, _normalize_channel_username
from bot.handlers import common, registration, submit, tasks
from bot.keyboards import channel_sub_keyboard
from bot.utils import check_subscription, require_subscription


class FakeUser:
    def __init__(self, user_id=777, username="sub_user", full_name="Sub User"):
        self.id = user_id
        self.username = username
        self.full_name = full_name


class FakeChat:
    type = "private"
    id = 777


class FakeMessage:
    def __init__(self, text="/start"):
        self.text = text
        self.replies = []

    async def reply_html(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return types.SimpleNamespace(message_id=101)


class FakeBot:
    def __init__(self, status="member", error: Exception | None = None):
        self.username = "itinsightslabbot"
        self.status = status
        self.error = error
        self.sent_messages = []

    async def get_me(self):
        return types.SimpleNamespace(username=self.username)

    async def get_chat_member(self, chat_id, user_id):
        if self.error:
            raise self.error
        return types.SimpleNamespace(status=self.status)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent_messages.append((chat_id, text, kwargs))
        return types.SimpleNamespace(message_id=102)


def make_config(channel: str | int = "@itinsights_lab") -> Config:
    return Config(
        bot_token="test_token",
        group_chat_id=-1001111111111,
        review_chat_id=-1002222222222,
        admin_ids=frozenset({100}),
        invite_link="https://t.me/yuqori_group",
        mandatory_channel=channel,
        database_path=Path(":memory:"),
        log_level="INFO",
    )


def make_context(status="member", error=None, channel="@itinsights_lab"):
    cfg = make_config(channel)
    bot = FakeBot(status=status, error=error)
    return types.SimpleNamespace(user_data={}, bot_data={"config": cfg}, bot=bot, args=[])


def test_channel_handle_normalization():
    """Verify stripping of t.me URL prefixes and handle formatting."""
    assert _normalize_channel_username("https://t.me/itinsights_lab") == "@itinsights_lab"
    assert _normalize_channel_username("https://t.me/itinsights_lab/") == "@itinsights_lab"
    assert _normalize_channel_username("http://t.me/itinsights_lab") == "@itinsights_lab"
    assert _normalize_channel_username("itinsights_lab") == "@itinsights_lab"
    assert _normalize_channel_username("@itinsights_lab") == "@itinsights_lab"
    assert _normalize_channel_username("-100123456789") == -100123456789
    assert _normalize_channel_username(-100123456789) == -100123456789


def test_channel_sub_keyboard_builder():
    """Verify InlineKeyboardMarkup contains correct URL and callback buttons."""
    kb = channel_sub_keyboard("https://t.me/itinsights_lab")
    assert len(kb.inline_keyboard) == 2
    url_btn = kb.inline_keyboard[0][0]
    cb_btn = kb.inline_keyboard[1][0]
    assert url_btn.url == "https://t.me/itinsights_lab"
    assert url_btn.text == "📢 Join @itinsights_lab"
    assert cb_btn.callback_data == "check_channel_sub"
    assert cb_btn.text == "✅ Check Again"


def test_check_subscription_helper_status_checks():
    """Verify check_subscription returns True for valid member statuses and False for left/kicked."""
    ctx_member = make_context(status="member")
    assert asyncio.run(check_subscription(777, ctx_member)) is True

    ctx_admin = make_context(status="administrator")
    assert asyncio.run(check_subscription(777, ctx_admin)) is True

    ctx_creator = make_context(status="creator")
    assert asyncio.run(check_subscription(777, ctx_creator)) is True

    ctx_left = make_context(status="left")
    assert asyncio.run(check_subscription(777, ctx_left)) is False

    ctx_kicked = make_context(status="kicked")
    assert asyncio.run(check_subscription(777, ctx_kicked)) is False


def test_check_subscription_helper_error_handling(caplog):
    """Verify TelegramError / BadRequest causes check_subscription to return False & log explicit warning."""
    err_bot = BadRequest("Bad Request: chat not found")
    ctx_err = make_context(error=err_bot)

    res = asyncio.run(check_subscription(777, ctx_err))
    assert res is False
    assert "Failed to query channel subscription" in caplog.text
    assert "Please ensure the bot is added as an Administrator" in caplog.text


def test_require_subscription_decorator_blocks_unsubscribed_message():
    """Verify @require_subscription intercepts commands and prompts unsubscribed users."""
    called = False

    @require_subscription
    async def dummy_command(update, context):
        nonlocal called
        called = True
        return "OK"

    msg = FakeMessage("/tasks")
    upd = types.SimpleNamespace(message=msg, effective_user=FakeUser(), effective_chat=FakeChat())
    ctx = make_context(status="left")

    result = asyncio.run(dummy_command(upd, ctx))
    assert called is False  # Execution blocked
    assert result == 0  # Blocked state / 0
    assert len(msg.replies) == 1
    reply_text, kwargs = msg.replies[0]
    assert "Mandatory Channel Subscription" in reply_text
    assert "reply_markup" in kwargs


def test_require_subscription_decorator_allows_subscribed_message():
    """Verify @require_subscription allows command execution when user is subscribed."""
    called = False

    @require_subscription
    async def dummy_command(update, context):
        nonlocal called
        called = True
        return "OK"

    msg = FakeMessage("/tasks")
    upd = types.SimpleNamespace(message=msg, effective_user=FakeUser(), effective_chat=FakeChat())
    ctx = make_context(status="member")

    result = asyncio.run(dummy_command(upd, ctx))
    assert called is True
    assert result == "OK"
    assert len(msg.replies) == 0


def test_check_sub_callback_alert_if_not_joined():
    """Verify callback query handler alerts user if re-check fails."""
    alerts = []

    async def _answer(text=None, show_alert=False):
        if text:
            alerts.append((text, show_alert))

    cb_upd = types.SimpleNamespace(
        callback_query=types.SimpleNamespace(data="check_channel_sub", answer=_answer, edit_message_text=None),
        effective_user=FakeUser(),
        effective_chat=FakeChat(),
    )
    ctx = make_context(status="left")

    state = asyncio.run(registration.check_sub_callback(cb_upd, ctx))
    assert state == registration.CHANNEL_SUB
    assert len(alerts) == 1
    assert "haven't joined" in alerts[0][0]
    assert alerts[0][1] is True  # show_alert=True
