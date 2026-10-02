"""Stub-based smoke test: /start must lead with the registration prompt."""
from __future__ import annotations

import asyncio
import types

import pytest

from bot import db
from bot.config import Config
from bot.handlers import registration


class FakeChat:
    type = "private"
    id = 555


class FakeMessage:
    def __init__(self, text="/start"):
        self.text = text
        self.replies = []

    async def reply_html(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeUser:
    id = 555
    username = "tester"
    full_name = "Test User"


class FakeBot:
    username = "itinsightslabbot"

    async def get_me(self):
        return types.SimpleNamespace(username=self.username)

    async def get_chat_member(self, chat_id, user_id):
        return types.SimpleNamespace(status="member")


def make_config() -> Config:
    return Config(
        bot_token="x",
        group_chat_id=-100,
        review_chat_id=-100,
        admin_ids=frozenset({1}),
        invite_link="https://t.me/+x",
        database_path=":memory:",
        log_level="INFO",
    )


def make_context() -> types.SimpleNamespace:
    return types.SimpleNamespace(user_data={}, bot_data={"config": make_config()},
                                 bot=FakeBot(), args=[])


def msg_update(text: str):
    m = FakeMessage(text)
    return types.SimpleNamespace(message=m, effective_user=FakeUser(), effective_chat=FakeChat())


@pytest.fixture()
def temp_db(tmp_path):
    db.init_db(tmp_path / "smoke.db")
    yield
    db._db_path = None


def test_start_prompts_for_organization(temp_db):
    assert db.get_user(555) is None
    upd = msg_update("/start")
    state = asyncio.run(registration.start(upd, make_context()))
    assert state == registration.ORGANIZATION
    text = upd.message.replies[0][0]
    assert "Step 1/6" in text and "organisation" in text


def test_full_registration_flow_saves_user(temp_db):
    ctx = make_context()
    asyncio.run(registration.start(msg_update("/start"), ctx))

    async def _noop(*a, **k):
        pass

    org_upd = types.SimpleNamespace(
        callback_query=types.SimpleNamespace(data="reg_org:AUT Cohort", answer=_noop, edit_message_text=_noop),
        effective_user=FakeUser(), effective_chat=FakeChat(),
    )
    assert asyncio.run(registration.got_organization(org_upd, ctx)) == registration.NAME
    assert asyncio.run(registration.got_name(msg_update("Ada Lovelace"), ctx)) == registration.CITY
    assert asyncio.run(registration.got_city(msg_update("London"), ctx)) == registration.OCCUPATION

    occ_upd = types.SimpleNamespace(
        callback_query=types.SimpleNamespace(data="reg_occ:Student", answer=_noop, edit_message_text=_noop),
        effective_user=FakeUser(), effective_chat=FakeChat(),
    )
    assert asyncio.run(registration.got_occupation(occ_upd, ctx)) == registration.PHOTO

    photo_upd = types.SimpleNamespace(
        message=types.SimpleNamespace(photo=[types.SimpleNamespace(file_id="ph1")], replies=[]),
        effective_user=FakeUser(), effective_chat=FakeChat(),
    )
    photo_upd.message.reply_html = FakeMessage().reply_html
    assert asyncio.run(registration.got_photo(photo_upd, ctx)) == registration.BIO

    # final step: bio (group send fails harmlessly against the fake bot)
    assert asyncio.run(registration.got_bio(msg_update("I love data and analytics!"), ctx)) == \
        registration.ConversationHandler.END

    row = db.get_user(555)
    assert row is not None
    assert row["full_name"] == "Ada Lovelace"
    assert row["city"] == "London"
    assert row["organization"] == "AUT Cohort"


def test_unknown_command_ignores_known_commands(temp_db):
    """Regression: the fallback used to reply after every valid command."""
    from bot.handlers.common import unknown_command

    ctx = make_context()
    for known in ("/start", "/tasks", "/submit", "/admin", "/help@itinsightslabbot"):
        upd = msg_update(known)
        asyncio.run(unknown_command(upd, ctx))
        assert upd.message.replies == [], f"replied to known command {known}"

    upd = msg_update("/nonsense")
    asyncio.run(unknown_command(upd, ctx))
    assert len(upd.message.replies) == 1  # genuinely unknown -> one hint
