"""Common handlers: /help, /myprofile, unknown-command fallback, error handler."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from .. import db
from ..config import Config
from ..utils import esc

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🎓 <b>IT Insights Lab — Commands</b>\n\n"
    "<b>Everyone</b>\n"
    "• /start — register or view your profile\n"
    "• /tasks — list published tasks\n"
    "• /submit [task_id] — submit a solution\n"
    "• /myprofile — show your profile\n"
    "• /help — this message\n\n"
    "<b>Admins</b>\n"
    "• /newtask — create & broadcast a task\n"
    "• /admin — open the admin panel"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP_TEXT)


async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = db.get_user(update.effective_user.id)
    if user is None:
        await update.message.reply_html("✋ You're not registered yet — send /start first.")
        return
    text = (
        f"👤 <b>{esc(user['full_name'])}</b> · @{esc(user['username'] or 'no handle')}\n"
        f"🏙 {esc(user['city'])} · 💼 {esc(user['occupation'])}\n"
        f"🚦 Status: <b>{esc(user['status'])}</b>\n"
        f"📅 Registered: {esc(user['registered_at'])}\n\n"
        f"📝 {esc(user['bio'])}"
    )
    await update.message.reply_html(text)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html("🤔 Unknown command. Try /help.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: log, notify the user, alert admins on repeat failures."""
    logger.error("Unhandled exception while processing an update", exc_info=context.error)
    cfg: Config | None = context.bot_data.get("config")
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_html(
                "⚠️ Something went wrong on my side. Please try again in a moment."
            )
        except Exception:
            pass
    if cfg:
        for admin_id in cfg.admin_ids:
            try:
                err = context.error
                await context.bot.send_message(
                    admin_id,
                    f"🔥 Bot error: {type(err).__name__}: {esc(str(err))[:300]}",
                )
            except Exception:
                pass


def register(application: Application) -> None:
    application.add_handler(CommandHandler("help", help_command), group=2)
    application.add_handler(CommandHandler("myprofile", my_profile), group=2)
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command), group=90)
    application.add_handler(TypeHandler(Update, on_error), group=100, block=False)
