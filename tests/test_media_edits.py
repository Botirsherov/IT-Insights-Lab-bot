"""Regression tests for editing buttons that sit on MEDIA messages.

Telegram forbids edit_message_text() on photo/document posts (they only have
a caption) and raises BadRequest("There is no text in the message to edit").
These tests pin the safe_edit() fallback ladder and the user-facing flows
that hit media messages (task broadcasts with attachments).
"""
from __future__ import annotations

import asyncio
import types

import pytest
from telegram.error import BadRequest

from bot import db
from bot.config import Config
from bot.handlers import submit, tasks
from bot.utils import safe_edit

STUDENT_ID = 555
CHAT_ID = 100


def make_config() -> Config:
    return Config(
        bot_token="x", group_chat_id=-100, review_chat_id=-200,
        admin_ids=frozenset({1}), invite_link="", database_path=":memory:",
        log_level="INFO",
    )


class FakeBot:
    def __init__(self):
        self.sent = []

    async def get_chat_member(self, chat_id, user_id):
        return types.SimpleNamespace(status="member")

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return types.SimpleNamespace(message_id=1)


class TextQuery:
    """Button on a normal text message: edit_message_text works."""

    def __init__(self, data):
        self.data = data
        self.from_user = types.SimpleNamespace(id=STUDENT_ID)
        self.message = types.SimpleNamespace(chat_id=CHAT_ID)
        self.text_edits = []
        self.caption_edits = []

    async def answer(self, *a, **k):
        pass

    async def edit_message_text(self, text, **kwargs):
        self.text_edits.append((text, kwargs.get("reply_markup")))

    async def edit_message_caption(self, caption=None, **kwargs):
        self.caption_edits.append((caption, kwargs.get("reply_markup")))


class MediaQuery(TextQuery):
    """Button on a photo/document post: text edits raise the real BadRequest."""

    async def edit_message_text(self, text, **kwargs):
        raise BadRequest("There is no text in the message to edit")


class HopelessQuery(MediaQuery):
    """Media message whose caption edit also fails (e.g. message too old)."""

    async def edit_message_caption(self, caption=None, **kwargs):
        raise BadRequest("Message is not modified")


@pytest.fixture()
def seeded_db(tmp_path):
    db.init_db(tmp_path / "media.db")
    db.register_user(user_id=STUDENT_ID, username="st", full_name="Student",
                     city="X", occupation="Student", photo_file_id="p", bio="b")
    task_id = db.create_task(title="Pandas Basics", description="learn pandas")
    yield task_id
    db._db_path = None


def make_context():
    return types.SimpleNamespace(
        bot_data={"config": make_config()},
        bot=FakeBot(),
        user_data={},
    )


# --------------------------------------------------------------------------- #
# safe_edit fallback ladder
# --------------------------------------------------------------------------- #
def test_safe_edit_text_message_uses_edit_text():
    q = TextQuery("x")
    asyncio.run(safe_edit(q, "hello"))
    assert q.text_edits and q.text_edits[0][0] == "hello"
    assert q.caption_edits == []


def test_safe_edit_media_message_falls_back_to_caption():
    q = MediaQuery("x")
    asyncio.run(safe_edit(q, "hello"))
    assert q.caption_edits and q.caption_edits[0][0] == "hello"


def test_safe_edit_total_failure_sends_new_message():
    q = HopelessQuery("x")
    bot = FakeBot()
    q.bot = bot
    asyncio.run(safe_edit(q, "hello"))
    assert any(t == "hello" for _, t in bot.sent)


# --------------------------------------------------------------------------- #
# User flows that hit media broadcasts
# --------------------------------------------------------------------------- #
def test_submit_button_on_media_broadcast_reaches_kind_prompt(seeded_db):
    q = MediaQuery(f"submit_for:{seeded_db}")
    update = types.SimpleNamespace(
        callback_query=q, effective_user=q.from_user,
        message=None, effective_message=None,
    )
    state = asyncio.run(submit.submit_entry(update, make_context()))
    assert state == submit.KIND
    text, markup = q.caption_edits[0]
    assert f"Task #{seeded_db}" in text
    assert markup is not None  # File/Link/Text keyboard preserved


def test_submit_kind_pick_on_media_message(seeded_db):
    q = MediaQuery("sub_kind:file")
    ctx = make_context()
    state = asyncio.run(submit.submission_kind(
        types.SimpleNamespace(callback_query=q, effective_user=q.from_user,
                              message=None, effective_message=None),
        ctx,
    ))
    assert state == submit.CONTENT
    assert ctx.user_data["sub_kind"] == "file"
    assert "file" in q.caption_edits[0][0]


def test_get_task_button_on_media_after_task_deleted(seeded_db):
    db.delete_task(seeded_db)
    q = MediaQuery(f"task:get:{seeded_db}")
    asyncio.run(tasks.show_task(
        types.SimpleNamespace(callback_query=q, effective_user=q.from_user,
                              message=None, effective_message=None),
        make_context(),
    ))
    assert q.caption_edits and "no longer exists" in q.caption_edits[0][0]


def test_submit_button_on_text_message_unaffected(seeded_db):
    """Plain-text broadcasts keep the old behaviour (edit_message_text)."""
    q = TextQuery(f"submit_for:{seeded_db}")
    update = types.SimpleNamespace(
        callback_query=q, effective_user=q.from_user,
        message=None, effective_message=None,
    )
    state = asyncio.run(submit.submit_entry(update, make_context()))
    assert state == submit.KIND
    assert q.text_edits and "Task #" in q.text_edits[0][0]
    assert q.caption_edits == []
