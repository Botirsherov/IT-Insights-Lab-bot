"""Common handlers: /help, /myprofile, unknown-command fallback, error handler."""
from __future__ import annotations

import logging

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
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
    "• /newtask — create &amp; broadcast a task\n"
    "• /admin — open the admin panel\n\n"
    "<b>Reviewing (in the reviewers group)</b>\n"
    "Reply to a submission post with:\n"
    "• <b>approved</b> — approve the submission\n"
    "• <b>rejected</b> — reject the submission\n"
    "• <code>rejected izoh: yaxshilab qayta yozing</code> — reject with a note\n"
    "• <code>approved izoh: zo'r ish!</code> — approve with a note\n"
    "The student is notified automatically."
)

# Commands the bot itself implements — the unknown-command fallback must not
# react to any of these (it runs in a later handler group for every command).
_KNOWN_COMMANDS = {
    "/start", "/cancel", "/tasks", "/submit", "/myprofile", "/help",
    "/newtask", "/admin", "/approve", "/approved", "/reject", "/rejected",
}


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP_TEXT)


async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = db.get_user(update.effective_user.id)
    if user is None:
        await update.message.reply_html("✋ You're not registered yet — send /start first.")
        return
    org = user["organization"] if "organization" in user.keys() else "Yuqori Chirchiq TIM"
    text = (
        f"👤 <b>{esc(user['full_name'])}</b> · @{esc(user['username'] or 'no handle')}\n"
        f"🏫 Organisation: <b>{esc(org)}</b>\n"
        f"🏙 {esc(user['city'])} · 💼 {esc(user['occupation'])}\n"
        f"🚦 Status: <b>{esc(user['status'])}</b>\n"
        f"📅 Registered: {esc(user['registered_at'])}\n\n"
        f"📝 {esc(user['bio'])}"
    )
    await update.message.reply_html(text)


async def handle_group_migration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.migrate_to_chat_id:
        return
    old_id = update.effective_chat.id
    new_id = update.message.migrate_to_chat_id
    logger.info("Group migration detected: old_id=%s -> new_id=%s", old_id, new_id)
    db.update_group_chat_id_by_old(old_id, new_id)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply only to unrecognized commands, and only in private chats."""
    msg = update.message
    if msg is None or not msg.text:
        return
    first = msg.text.split()[0]
    if not first.startswith("/"):
        return
    # strip @botname suffix (e.g. /help@itinsightslabbot)
    base = first.split("@", 1)[0].lower()
    if base in _KNOWN_COMMANDS:
        return  # a real handler (earlier group) owns this command
    if update.effective_chat.type != "private":
        return  # stay quiet in groups
    await msg.reply_html("🤔 Unknown command. Try /help.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: log, notify the user, and alert every admin
    with the full traceback so a freeze/crash is never silent."""
    err = context.error
    from telegram.error import NetworkError
    from traceback import format_exception

    if isinstance(err, NetworkError):
        logger.warning("Network/Proxy error encountered (%s): %s", type(err).__name__, err)
        return

    cfg: Config | None = context.bot_data.get("config")
    logger.error(
        "Unhandled exception while processing an update\n%s",
        "".join(format_exception(type(err), err, err.__traceback__)),
    )

    # Tell the user (but only if we got a message to reply to).
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_html(
                "⚠️ Something went wrong on my side. Please try again in a moment."
            )
        except Exception:
            pass

    # Alert every human admin of the failure, with a short one-liner.
    # Error reports are sent only to admin DMs — never to the reviewers group.
    if cfg:
        for admin_id in cfg.admin_ids:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"🔥 IT Insights Lab bot error: {type(err).__name__}: {esc(str(err))[:300]}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass


USER_COMMANDS = [
    BotCommand("start", "Register or see your profile"),
    BotCommand("tasks", "List available tasks"),
    BotCommand("submit", "Submit a solution"),
    BotCommand("myprofile", "Show your profile"),
    BotCommand("help", "Show all commands"),
]

ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("newtask", "Create & broadcast a task (admin)"),
    BotCommand("admin", "Open the admin panel (admin)"),
]


async def setup_bot_commands(application: Application, cfg: "Config") -> None:
    """Register the command menu so '/' and the menu button list commands.

    Admins additionally get /newtask and /admin in their private-chat menu.
    """
    try:
        await application.bot.set_my_commands(USER_COMMANDS)
    except Exception as exc:
        logger.warning("Could not set default command menu: %s", exc)
    for admin_id in cfg.admin_ids:
        try:
            await application.bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id)
            )
        except Exception as exc:
            logger.warning("Could not set admin command menu for %s: %s", admin_id, exc)


def register(application: Application) -> None:
    application.add_handler(CommandHandler("help", help_command), group=2)
    application.add_handler(CommandHandler("myprofile", my_profile), group=2)
    application.add_handler(MessageHandler(filters.StatusUpdate.MIGRATE, handle_group_migration), group=0)
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command), group=90)
    # NOTE: on_error is already registered as error handler in main.create_application().
    # Do NOT call application.add_error_handler(on_error) here again — it would fire twice.
