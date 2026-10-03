"""Unit tests for mandatory channel subscription, organisation selection, group routing, and migration."""
from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest
from telegram.error import BadRequest

from bot import db
from bot.config import Config
from bot.handlers import common, registration


class FakeChat:
    type = "private"
    id = 777


class FakeMessage:
    def __init__(self, text="/start"):
        self.text = text
        self.replies = []

    async def reply_html(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeUser:
    id = 777
    username = "org_tester"
    full_name = "Org Tester"


class FakeBot:
    def __init__(self, is_subbed=True):
        self.username = "itinsightslabbot"
        self.is_subbed = is_subbed
        self.sent_photos = []
        self.sent_messages = []

    async def get_me(self):
        return types.SimpleNamespace(username=self.username)

    async def get_chat_member(self, chat_id, user_id):
        if self.is_subbed:
            return types.SimpleNamespace(status="member")
        return types.SimpleNamespace(status="left")

    async def send_photo(self, chat_id, photo, caption, parse_mode=None):
        self.sent_photos.append((chat_id, photo, caption))
        return types.SimpleNamespace(message_id=123)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent_messages.append((chat_id, text, kwargs))
        return types.SimpleNamespace(message_id=124)


def make_config() -> Config:
    return Config(
        bot_token="test_token",
        group_chat_id=-1001111111111,
        review_chat_id=-1002222222222,
        admin_ids=frozenset({100}),
        invite_link="https://t.me/yuqori_group",
        aut_group_chat_id=-1004381231808,
        aut_invite_link="https://t.me/+JEgm3kwjxJtkMWZi",
        mandatory_channel="@itinsights_lab",
        database_path=Path(":memory:"),
        log_level="INFO",
    )


def make_context(is_subbed=True) -> types.SimpleNamespace:
    cfg = make_config()
    bot = FakeBot(is_subbed=is_subbed)
    return types.SimpleNamespace(user_data={}, bot_data={"config": cfg}, bot=bot, args=[])


def msg_update(text: str):
    m = FakeMessage(text)
    return types.SimpleNamespace(message=m, effective_user=FakeUser(), effective_chat=FakeChat())


@pytest.fixture()
def temp_db(tmp_path):
    db.init_db(tmp_path / "test_org.db")
    cfg = make_config()
    db.init_group_chats({
        "yuqori_chirchiq": {"chat_id": cfg.group_chat_id, "invite_link": cfg.invite_link},
        "aut_cohort": {"chat_id": cfg.aut_group_chat_id, "invite_link": cfg.aut_invite_link},
        "review": {"chat_id": cfg.review_chat_id, "invite_link": ""},
    })
    yield
    db._db_path = None


def test_channel_subscription_check_required(temp_db):
    """User not subscribed to @itinsights_lab must get CHANNEL_SUB prompt."""
    ctx = make_context(is_subbed=False)
    upd = msg_update("/start")
    state = asyncio.run(registration.start(upd, ctx))
    assert state == registration.CHANNEL_SUB
    reply_text = upd.message.replies[0][0]
    assert "@itinsights_lab" in reply_text


def test_channel_normalization_and_keyboard():
    from bot.config import _normalize_channel_username
    from bot.keyboards import channel_sub_keyboard

    assert _normalize_channel_username("https://t.me/itinsights_lab") == "@itinsights_lab"
    assert _normalize_channel_username("https://t.me/itinsights_lab/") == "@itinsights_lab"
    assert _normalize_channel_username("itinsights_lab") == "@itinsights_lab"
    assert _normalize_channel_username("@itinsights_lab") == "@itinsights_lab"

    kb = channel_sub_keyboard("@itinsights_lab")
    assert kb.inline_keyboard[0][0].url == "https://t.me/itinsights_lab"
    assert kb.inline_keyboard[0][0].text == "📢 Join @itinsights_lab"

    kb_url = channel_sub_keyboard("https://t.me/itinsights_lab")
    assert kb_url.inline_keyboard[0][0].url == "https://t.me/itinsights_lab"
    assert kb_url.inline_keyboard[0][0].text == "📢 Join @itinsights_lab"


def test_channel_subscription_check_callback_fails_if_not_joined(temp_db):
    ctx = make_context(is_subbed=False)
    alerts = []

    async def _answer(text=None, show_alert=False):
        if text:
            alerts.append(text)

    cb_upd = types.SimpleNamespace(
        callback_query=types.SimpleNamespace(answer=_answer, edit_message_text=None),
        effective_user=FakeUser(),
        effective_chat=FakeChat(),
    )
    state = asyncio.run(registration.check_sub_callback(cb_upd, ctx))
    assert state == registration.CHANNEL_SUB
    assert len(alerts) == 1
    assert "haven't joined" in alerts[0]


def test_channel_subscription_check_callback_proceeds_when_joined(temp_db):
    ctx = make_context(is_subbed=True)
    edited = []

    async def _answer(text=None, show_alert=False):
        pass

    async def _edit(text, reply_markup=None, parse_mode=None):
        edited.append((text, reply_markup))

    cb_upd = types.SimpleNamespace(
        callback_query=types.SimpleNamespace(answer=_answer, edit_message_text=_edit),
        effective_user=FakeUser(),
        effective_chat=FakeChat(),
    )
    state = asyncio.run(registration.check_sub_callback(cb_upd, ctx))
    assert state == registration.ORGANIZATION
    assert len(edited) == 1
    assert "Step 1/6" in edited[0][0]


def test_aut_cohort_routing_and_invite_link(temp_db):
    """AUT Cohort user gets intro sent to AUT group and gets AUT invite link."""
    ctx = make_context(is_subbed=True)
    asyncio.run(registration.start(msg_update("/start"), ctx))

    async def _noop(*a, **k):
        pass

    org_upd = types.SimpleNamespace(
        callback_query=types.SimpleNamespace(data="reg_org:AUT Cohort", answer=_noop, edit_message_text=_noop),
        effective_user=FakeUser(), effective_chat=FakeChat(),
    )
    assert asyncio.run(registration.got_organization(org_upd, ctx)) == registration.NAME
    assert asyncio.run(registration.got_name(msg_update("John Doe"), ctx)) == registration.CITY
    assert asyncio.run(registration.got_city(msg_update("Tashkent"), ctx)) == registration.OCCUPATION

    occ_upd = types.SimpleNamespace(
        callback_query=types.SimpleNamespace(data="reg_occ:Student", answer=_noop, edit_message_text=_noop),
        effective_user=FakeUser(), effective_chat=FakeChat(),
    )
    assert asyncio.run(registration.got_occupation(occ_upd, ctx)) == registration.PHOTO

    photo_upd = types.SimpleNamespace(
        message=types.SimpleNamespace(photo=[types.SimpleNamespace(file_id="aut_photo_123")], replies=[]),
        effective_user=FakeUser(), effective_chat=FakeChat(),
    )
    photo_upd.message.reply_html = FakeMessage().reply_html
    assert asyncio.run(registration.got_photo(photo_upd, ctx)) == registration.BIO

    bio_upd = msg_update("Hello! I am an AUT student interested in Data Science.")
    assert asyncio.run(registration.got_bio(bio_upd, ctx)) == registration.ConversationHandler.END

    # Verify intro card sent to AUT group chat id (-1004381231808)
    assert len(ctx.bot.sent_photos) == 1
    target_chat_id, photo_id, caption = ctx.bot.sent_photos[0]
    assert target_chat_id == -1004381231808
    assert "AUT Cohort" in caption

    # Verify user received AUT invite link in welcome message
    welcome_msg = bio_upd.message.replies[0][0]
    assert "https://t.me/+JEgm3kwjxJtkMWZi" in welcome_msg
    assert "AUT Cohort" in welcome_msg

    # Verify user saved in DB with organization
    user = db.get_user(777)
    assert user is not None
    assert user["organization"] == "AUT Cohort"


def test_group_chat_migration_update_and_auto_retry(temp_db):
    """Test group supergroup migration handler & auto-retry on send_photo."""
    cfg = make_config()

    # 1. Test update_group_chat_id_by_old
    old_id = cfg.aut_group_chat_id
    new_id = -100999888777666
    assert db.update_group_chat_id_by_old(old_id, new_id) is True

    chat_id, invite = db.get_group_chat_info("aut_cohort", cfg.aut_group_chat_id, cfg.aut_invite_link)
    assert chat_id == new_id

    # 2. Test status update handler in common.py
    migration_msg = types.SimpleNamespace(migrate_to_chat_id=-100111222333444)
    upd = types.SimpleNamespace(
        message=migration_msg,
        effective_chat=types.SimpleNamespace(id=new_id),
    )
    asyncio.run(common.handle_group_migration(upd, None))

    chat_id, invite = db.get_group_chat_info("aut_cohort", cfg.aut_group_chat_id, cfg.aut_invite_link)
    assert chat_id == -100111222333444

    # 3. Test safe_send_photo_with_migration auto-retry
    class MigratingBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def send_photo(self, chat_id, photo, caption, parse_mode=None):
            self.attempts += 1
            if self.attempts == 1:
                raise BadRequest("Group migrated to supergroup. New id: -1007777777777")
            return await super().send_photo(chat_id, photo, caption, parse_mode)

    mbot = MigratingBot()
    res = asyncio.run(registration.safe_send_photo_with_migration(
        bot=mbot,
        chat_id=-100111222333444,
        photo="ph1",
        caption="Test",
        org_name="AUT Cohort",
    ))
    assert res is not None
    assert mbot.attempts == 2
    assert mbot.sent_photos[0][0] == -1007777777777
    assert db.get_group_chat_info("aut_cohort", 0)[0] == -1007777777777
