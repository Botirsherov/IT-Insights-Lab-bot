"""User onboarding & registration (/start) — ConversationHandler-based FSM."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .. import db
from ..config import Config
from ..keyboards import occupation_keyboard
from ..utils import build_intro_card, esc, welcome_text

logger = logging.getLogger(__name__)

NAME, CITY, OCCUPATION, OCCUPATION_OTHER, PHOTO, BIO = range(6)

_TEXT_ONLY = filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/start — greet, branch on whether the user is already registered."""
    if update.effective_chat.type != "private":
        await update.message.reply_html(
            "👋 To register, please message me in a private chat:\n"
            f"→ @{(await context.bot.get_me()).username}"
        )
        return ConversationHandler.END
    u = update.effective_user
    cfg: Config = context.bot_data["config"]
    existing = db.get_user(u.id)
    if existing is not None:
        if existing["status"] == "banned":
            await update.message.reply_html(
                "⛔ You are banned from this cohort. Contact an admin if you believe this is a mistake."
            )
            return ConversationHandler.END
        link_line = f"\n🔗 Group: {cfg.invite_link}" if cfg.invite_link else ""
        await update.message.reply_html(
            f"👋 Welcome back, <b>{esc(existing['full_name'])}</b>! You are already registered.{link_line}\n\n"
            "📌 <b>Commands:</b>\n"
            "• /tasks — list available tasks\n"
            "• /submit — submit a solution\n"
            "• /myprofile — view your profile\n"
            "• /help — full command list"
        )
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_html(
        "🎓 <b>Welcome to IT Insights Lab!</b>\n\n"
        "I'll register you for the data analytics cohort in a few quick steps.\n\n"
        "<b>Step 1/5 — What is your full name?</b>\n"
        "<i>(send it as a text message)</i>"
    )
    return NAME


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not (2 <= len(text) <= 100):
        await update.message.reply_html("⚠️ Please send a name between 2 and 100 characters.")
        return NAME
    context.user_data["reg_full_name"] = text
    await update.message.reply_html(
        f"Nice to meet you, <b>{esc(text)}</b>!\n\n"
        "<b>Step 2/5 — Which city are you joining from?</b>"
    )
    return CITY


async def got_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not (2 <= len(text) <= 60):
        await update.message.reply_html("⚠️ Please send a city name (2–60 characters).")
        return CITY
    context.user_data["reg_city"] = text
    await update.message.reply_html(
        "Great!\n\n<b>Step 3/5 — What best describes you?</b>",
        reply_markup=occupation_keyboard(),
    )
    return OCCUPATION


async def got_occupation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]
    if choice == "__other__":
        await query.edit_message_text("✍️ Please type your occupation:")
        return OCCUPATION_OTHER
    context.user_data["reg_occupation"] = choice
    await query.edit_message_text(
        f"✅ Occupation: <b>{esc(choice)}</b>\n\n"
        "<b>Step 4/5 — Send a profile photo.</b>\n"
        "<i>(a clear photo of you — it will be used on your intro card in the cohort group)</i>",
        parse_mode=ParseMode.HTML,
    )
    return PHOTO


async def got_occupation_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text or len(text) > 60:
        await update.message.reply_html("⚠️ Please send a short occupation description (max 60 chars).")
        return OCCUPATION_OTHER
    context.user_data["reg_occupation"] = text
    await update.message.reply_html(
        "✅ Got it!\n\n<b>Step 4/5 — Send a profile photo.</b>\n"
        "<i>(it will be used on your intro card in the cohort group)</i>"
    )
    return PHOTO


async def got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo = update.message.photo
    if not photo:
        await update.message.reply_html("⚠️ That's not a photo. Please send an image (not as a file).")
        return PHOTO
    context.user_data["reg_photo_file_id"] = photo[-1].file_id  # highest resolution
    await update.message.reply_html(
        "📸 Photo received!\n\n"
        "<b>Step 5/5 — Write a short bio / introduction.</b>\n"
        "<i>(2–4 sentences: who you are, your background, and what you hope to learn)</i>"
    )
    return BIO


async def got_bio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if len(text) < 10:
        await update.message.reply_html("⚠️ Bio must be at least 10 characters.")
        return BIO
    if len(text) > 800:
        await update.message.reply_html("⚠️ Bio too long (max 800 characters).")
        return BIO

    cfg: Config = context.bot_data["config"]
    u = update.effective_user
    profile = {
        "full_name": context.user_data["reg_full_name"],
        "username": u.username,
        "city": context.user_data["reg_city"],
        "occupation": context.user_data["reg_occupation"],
        "bio": text,
    }
    db.register_user(
        user_id=u.id,
        username=u.username,
        full_name=profile["full_name"],
        city=profile["city"],
        occupation=profile["occupation"],
        photo_file_id=context.user_data["reg_photo_file_id"],
        bio=profile["bio"],
    )

    # ---- introduction card to the cohort group ----
    try:
        await context.bot.send_photo(
            chat_id=cfg.group_chat_id,
            photo=context.user_data["reg_photo_file_id"],
            caption=build_intro_card(profile),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.error("Failed to send intro card to group: %s", exc)
        await update.message.reply_html(
            "⚠️ Registration saved, but I couldn't post the intro card to the group. An admin has been notified."
        )
        for admin_id in cfg.admin_ids:
            try:
                await context.bot.send_message(
                    admin_id, f"⚠️ Intro card failed for user {u.id} ({esc(profile['full_name'])}): {exc}"
                )
            except Exception:
                pass

    await update.message.reply_html(welcome_text(profile, cfg.invite_link))
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_html("❌ Registration cancelled. Send /start to try again.")
    elif update.callback_query:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text("❌ Registration cancelled. Send /start to try again.")
    return ConversationHandler.END


async def not_a_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User sent something other than a photo during the PHOTO step."""
    await update.message.reply_html(
        "⚠️ That's not a photo. Please send an image (not as a file)."
    )
    return PHOTO


def register(application: Application) -> ConversationHandler:
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(_TEXT_ONLY, got_name)],
            CITY: [MessageHandler(_TEXT_ONLY, got_city)],
            OCCUPATION: [CallbackQueryHandler(got_occupation, pattern=r"^reg_occ:")],
            OCCUPATION_OTHER: [MessageHandler(_TEXT_ONLY, got_occupation_other)],
            PHOTO: [
                MessageHandler(filters.PHOTO, got_photo),
                # catch-all for non-photo, non-command input: show a clear error
                # instead of silently staying stuck. Commands still fall through
                # to the fallback so /cancel works.
                MessageHandler(~filters.PHOTO & ~filters.COMMAND & filters.ChatType.PRIVATE,
                               not_a_photo),
            ],
            BIO: [MessageHandler(_TEXT_ONLY, got_bio)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
        ],
        allow_reentry=True,
        name="registration",
    )
    application.add_handler(conv, group=0)
    return conv
