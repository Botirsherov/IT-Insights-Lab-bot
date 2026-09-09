"""Stub-based tests for the reply-based approve/reject review flow."""
from __future__ import annotations

import asyncio
import types

import pytest

from bot import db
from bot.config import Config
from bot.handlers import admin

REVIEW_CHAT = -5495049048
STUDENT_ID = 555
ADMIN_ID = 5705783551


def make_config() -> Config:
    return Config(
        bot_token="x", group_chat_id=-100, review_chat_id=REVIEW_CHAT,
        admin_ids=frozenset({ADMIN_ID}), invite_link="", database_path=":memory:",
        log_level="INFO",
    )


class FakeMessage:
    def __init__(self, text, reply_to=None, chat_id=REVIEW_CHAT, from_id=ADMIN_ID,
                 message_id=100, from_bot=False):
        self.text = text
        self.chat_id = chat_id
        self.message_id = message_id
        self.reply_to_message = reply_to
        self.from_user = types.SimpleNamespace(id=from_id, full_name="Admin",
                                               is_bot=from_bot)
        self.replies = []

    async def reply_html(self, text, **kwargs):
        self.replies.append(text)


def make_update(msg):
    return types.SimpleNamespace(message=msg, effective_user=msg.from_user,
                                 effective_chat=types.SimpleNamespace(id=msg.chat_id,
                                                                      type="group"))


class FakeBot:
    id = 999

    def __init__(self):
        self.sent = []
        self.edits = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return types.SimpleNamespace(message_id=555)

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def edit_message_caption(self, **kwargs):
        self.edits.append(kwargs)


def make_context(bot=None):
    return types.SimpleNamespace(bot_data={"config": make_config()}, bot=bot or FakeBot())


@pytest.fixture()
def temp_db(tmp_path):
    db.init_db(tmp_path / "review.db")
    db.register_user(user_id=STUDENT_ID, username="st", full_name="Student One",
                     city="X", occupation="Student", photo_file_id="p", bio="bio")
    task_id = db.create_task(title="T", description="desc")
    sub_id = db.add_submission(user_id=STUDENT_ID, task_id=task_id, kind="text",
                               content="my solution")
    db.set_submission_review_post(sub_id, REVIEW_CHAT, 100)
    yield sub_id
    db._db_path = None


def _linked_post(sub_id):
    """A bot review post linked in the DB (message_id 100)."""
    return FakeMessage("submission post", message_id=100, from_bot=True)


def _legacy_post():
    """An old post: not linked in the DB, but contains 'Submission: #N'."""
    return FakeMessage(f"📥 New Submission\n🆔 Submission: #1\nStatus: Pending Review",
                       message_id=200, from_bot=True)


@pytest.mark.parametrize("word", ["approved", "Approved", "approve", "/approve",
                                  "/approve@itinsightslabbot", "✅", "approved 👍"])
def test_approve_variants_linked_post(temp_db, word):
    sub_id = temp_db
    post = _linked_post(sub_id)
    reply = FakeMessage(word, reply_to=post)
    ctx = make_context()
    asyncio.run(admin.on_review_reply(make_update(reply), ctx))

    row = db.get_submission(sub_id)
    assert row["status"] == "approved"
    assert row["reviewed_by"] == ADMIN_ID
    # student DM + confirmation
    assert any(chat == STUDENT_ID for chat, _ in ctx.bot.sent)
    assert reply.replies and "approved" in reply.replies[-1]
    # review post got tagged
    assert ctx.bot.edits


def test_reject_variant_legacy_post(temp_db):
    sub_id = temp_db  # linked message_id=100; reply targets the unlinked 200 instead
    post = _legacy_post()
    reply = FakeMessage("/reject", reply_to=post)
    ctx = make_context()
    asyncio.run(admin.on_review_reply(make_update(reply), ctx))

    assert db.get_submission(sub_id)["status"] == "rejected"
    assert ctx.bot.edits


def test_non_admin_ignored(temp_db):
    post = _linked_post(temp_db)
    reply = FakeMessage("approved", reply_to=post, from_id=42)
    asyncio.run(admin.on_review_reply(make_update(reply), make_context()))
    assert db.get_submission(temp_db)["status"] == "pending"
    assert reply.replies == []


def test_reply_to_non_submission_post_hint(temp_db):
    post = FakeMessage("just chatting", message_id=300, from_bot=True)
    reply = FakeMessage("approved", reply_to=post)
    asyncio.run(admin.on_review_reply(make_update(reply), make_context()))
    assert "isn't a submission post" in reply.replies[0]


def test_slash_command_without_reply_hint(temp_db):
    reply = FakeMessage("/approve")
    asyncio.run(admin.on_review_reply(make_update(reply), make_context()))
    assert "Reply to the submission post" in reply.replies[0]


def test_already_reviewed(temp_db):
    db.review_submission(temp_db, "approved", ADMIN_ID)
    post = _linked_post(temp_db)
    reply = FakeMessage("approved", reply_to=post)
    asyncio.run(admin.on_review_reply(make_update(reply), make_context()))
    assert "already" in reply.replies[0]


def test_plain_chat_untouched(temp_db):
    reply = FakeMessage("hey team, what's up?", reply_to=_linked_post(temp_db))
    asyncio.run(admin.on_review_reply(make_update(reply), make_context()))
    assert reply.replies == []
    assert db.get_submission(temp_db)["status"] == "pending"
