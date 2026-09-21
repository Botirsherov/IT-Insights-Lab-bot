"""Stub-based tests for the admin 'Manage Tasks' panel (list / detail / delete)."""
from __future__ import annotations

import asyncio
import types

import pytest

from bot import db
from bot.config import Config
from bot.handlers import admin

ADMIN_ID = 5705783551


def make_config() -> Config:
    return Config(
        bot_token="x", group_chat_id=-100, review_chat_id=-200,
        admin_ids=frozenset({ADMIN_ID}), invite_link="", database_path=":memory:",
        log_level="INFO",
    )


class FakeQuery:
    def __init__(self, data: str, from_id: int = ADMIN_ID):
        self.data = data
        self.from_user = types.SimpleNamespace(id=from_id)
        self.answered = 0
        self.edits = []

    async def answer(self, *args, **kwargs):
        self.answered += 1

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs.get("reply_markup")))


def make_update(query: FakeQuery):
    return types.SimpleNamespace(
        callback_query=query,
        effective_user=query.from_user,
        message=None,
        effective_message=None,
        effective_chat=types.SimpleNamespace(id=123, type="private"),
    )


def make_context():
    return types.SimpleNamespace(bot_data={"config": make_config()}, bot=types.SimpleNamespace(id=999))


@pytest.fixture()
def seeded_db(tmp_path):
    db.init_db(tmp_path / "tasks.db")
    db.register_user(user_id=1, username="st", full_name="Student", city="X",
                     occupation="Student", photo_file_id="p", bio="b")
    task_id = db.create_task(title="Pandas Basics", description="learn pandas")
    db.add_submission(user_id=1, task_id=task_id, kind="text", content="solution")
    yield task_id
    db._db_path = None


def test_task_list_shows_tasks(seeded_db):
    q = FakeQuery("admin:tasks")
    asyncio.run(admin.admin_tasks(make_update(q), make_context()))
    assert q.answered == 1
    text, markup = q.edits[0]
    assert "Manage Tasks" in text and "1 published" in text
    # one tappable row per task, with the title on the button
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert any(b.callback_data == f"adm_task:{seeded_db}" for b in buttons)
    assert any("Pandas Basics" in b.text for b in buttons)


def test_task_detail_shows_submission_count(seeded_db):
    q = FakeQuery(f"adm_task:{seeded_db}")
    asyncio.run(admin.admin_task_detail(make_update(q), make_context()))
    text, markup = q.edits[0]
    assert "Submissions: <b>1</b>" in text
    assert any(b.callback_data == f"adm_taskdel:{seeded_db}" for row in markup.inline_keyboard for b in row)


def test_delete_confirm_warns_then_deletes_with_cascade(seeded_db):
    # step 1: confirmation screen mentions the submission count
    q = FakeQuery(f"adm_taskdel:{seeded_db}")
    asyncio.run(admin.admin_task_delete_ask(make_update(q), make_context()))
    text, _ = q.edits[0]
    assert "Delete Task" in text and "1</b> submission(s)" in text

    # step 2: confirm -> task AND its submissions are gone
    q2 = FakeQuery(f"adm_taskdelgo:{seeded_db}")
    asyncio.run(admin.admin_task_delete_confirm(make_update(q2), make_context()))
    assert db.get_task(seeded_db) is None
    assert db.count_submissions(task_id=seeded_db) == 0
    assert "deleted" in q2.edits[0][0]


def test_delete_twice_is_safe(seeded_db):
    db.delete_task(seeded_db)
    q = FakeQuery(f"adm_taskdelgo:{seeded_db}")
    asyncio.run(admin.admin_task_delete_confirm(make_update(q), make_context()))
    assert "not found" in q.edits[0][0].lower()


def test_non_admin_cannot_delete(seeded_db):
    q = FakeQuery(f"adm_taskdelgo:{seeded_db}", from_id=42)
    asyncio.run(admin.admin_task_delete_confirm(make_update(q), make_context()))
    assert db.get_task(seeded_db) is not None  # untouched
