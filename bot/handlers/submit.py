"""Homework submission (/submit) — ConversationHandler-based FSM."""
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
from ..keyboards import submission_kind_keyboard, task_pick_keyboard
from ..utils import esc, profile_link

logger = logging.getLogger(__name__)

PICK_TASK, KIND, CONTENT = range(3)

_TEXT_ONLY = filters.TEXT & ~filters.COMMAND


async def _deny_banned(update: Update, user_row) -> bool:
    if user_row is not None and user_row["status"] == "banned":
        if update.message:
            await update.message.reply_html("⛔ You are banned and cannot submit solutions.")
        elif update.callback_query:
            await update.callback_query.answer("You are banned.", show_alert=True)
        return True
    return False


def _ask_kind_text(task_id: int, title: str) -> str:
    return (
        f"🎯 <b>Task #{task_id}: {esc(title)}</b>\n\n"
        "How would you like to submit your solution?"
    )


async def submit_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/submit or the [Submit Solution] button."""
    user_row = db.get_user(update.effective_user.id)
    if user_row is None:
        await update.message.reply_html("✋ Please register first with /start.")
        return ConversationHandler.END
    if await _deny_banned(update, user_row):
        return ConversationHandler.END

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        task_id = int(query.data.split(":")[1])
        task = db.get_task(task_id)
        if not task:
            await query.edit_message_text("⚠️ This task no longer exists.")
            return ConversationHandler.END
        context.user_data["sub_task_id"] = task_id
        context.user_data["sub_task_title"] = task["title"]
        await query.edit_message_text(
            _ask_kind_text(task_id, task["title"]),
            reply_markup=submission_kind_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return KIND

    tasks = db.list_tasks()
    if not tasks:
        await update.message.reply_html("📭 No tasks available to submit against yet.")
        return ConversationHandler.END

    if context.args:
        try:
            task_id = int(context.args[0])
            task = db.get_task(task_id)
            if task:
                context.user_data["sub_task_id"] = task_id
                context.user_data["sub_task_title"] = task["title"]
                await update.message.reply_html(
                    _ask_kind_text(task_id, task["title"]),
                    reply_markup=submission_kind_keyboard(),
                )
                return KIND
        except (ValueError, IndexError):
            pass

    await update.message.reply_html(
        "📤 <b>Submit a solution</b>\n\nWhich task is this for?",
        reply_markup=task_pick_keyboard(tasks),
    )
    return PICK_TASK


async def pick_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    task = db.get_task(task_id)
    if not task:
        await query.edit_message_text("⚠️ This task no longer exists.")
        return ConversationHandler.END
    context.user_data["sub_task_id"] = task_id
    context.user_data["sub_task_title"] = task["title"]
    await query.edit_message_text(
        _ask_kind_text(task_id, task["title"]),
        reply_markup=submission_kind_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return KIND


async def submission_kind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    kind = query.data.split(":")[1]
    context.user_data["sub_kind"] = kind
    prompts = {
        "file": "📎 Send your solution as a <b>file</b> (document, notebook, archive…):",
        "link": "🔗 Send the <b>link</b> (GitHub, Colab, Drive…):",
        "text": "✍️ Paste your solution as <b>text</b> (up to 4000 characters):",
    }
    await query.edit_message_text(
        prompts.get(kind, "Send your solution:"),
        parse_mode=ParseMode.HTML,
    )
    return CONTENT


async def got_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_row = db.get_user(update.effective_user.id)
    if user_row is None:
        await update.message.reply_html("✋ Please register first with /start.")
        return ConversationHandler.END
    if await _deny_banned(update, user_row):
        return ConversationHandler.END

    msg = update.message
    cfg: Config = context.bot_data["config"]
    chosen = context.user_data.get("sub_kind")

    if chosen == "file":
        if msg.document:
            kind, file_id, content = "file", msg.document.file_id, msg.document.file_name
        else:
            await msg.reply_html("⚠️ Please send a document/file (or /cancel).")
            return CONTENT
    elif chosen == "link":
        text = (msg.text or "").strip()
        if not text.startswith(("http://", "https://")):
            await msg.reply_html("⚠️ Please send a valid link starting with http:// or https://.")
            return CONTENT
        kind, file_id, content = "link", None, text
    else:  # text
        text = (msg.text or "").strip()
        if not text:
            await msg.reply_html("⚠️ Please send your solution as text (or /cancel).")
            return CONTENT
        kind, file_id, content = "text", None, text

    task_id = context.user_data.get("sub_task_id")
    task = db.get_task(task_id)
    if not task:
        await msg.reply_html("⚠️ The task seems to have disappeared. Please /submit again.")
        return ConversationHandler.END

    submission_id = db.add_submission(
        user_id=user_row["id"],
        task_id=task_id,
        kind=kind,
        content=content,
        file_id=file_id,
    )

    # ---- notify the reviewers group ----
    handle = f"@{esc(user_row['username'])}" if user_row["username"] else esc(user_row["full_name"])
    header = (
        f"📥 <b>New Submission — Pending Review</b>\n\n"
        f"👤 <b>Student:</b> {profile_link(user_row['id'], user_row['full_name'])} · {handle}\n"
        f"🗂 <b>Task:</b> #{task['id']} — {esc(task['title'])}\n"
        f"🆔 <b>Submission:</b> #{submission_id}\n"
        f"🕐 <b>Status:</b> Pending Review"
    )
    try:
        await context.bot.send_message(cfg.review_chat_id, header, parse_mode=ParseMode.HTML)
        if kind == "file" and file_id:
            await context.bot.send_document(
                cfg.review_chat_id, file_id,
                caption=f"📎 Attachment for submission #{submission_id}",
            )
        elif kind == "link" and content:
            await context.bot.send_message(cfg.review_chat_id, f"🔗 {esc(content)}")
        elif kind == "text" and content:
            await context.bot.send_message(cfg.review_chat_id, f"📝 Solution:\n\n{esc(content)}")
    except Exception as exc:
        logger.error("Failed to notify reviewers: %s", exc)
        await msg.reply_html(
            "✅ Submission saved, but reviewers could not be notified. An admin has been alerted."
        )
        for admin_id in cfg.admin_ids:
            try:
                await context.bot.send_message(
                    admin_id, f"⚠️ Review notify failed: submission #{submission_id}: {esc(str(exc))}"
                )
            except Exception:
                pass
        return ConversationHandler.END

    context.user_data.clear()
    await msg.reply_html(
        f"✅ <b>Submission received!</b>\n\n"
        f"🆔 Submission ID: <b>#{submission_id}</b>\n"
        f"🗂 Task: #{task['id']} — {esc(task['title'])}\n"
        "🕐 Status: <b>Pending Review</b>\n\n"
        "You'll be notified here when a reviewer responds. Good luck! 🍀"
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_html("❌ Submission cancelled.")
    return ConversationHandler.END


def register(application: Application) -> None:
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("submit", submit_entry),
            CallbackQueryHandler(submit_entry, pattern=r"^submit_for:\d+$"),
        ],
        states={
            PICK_TASK: [CallbackQueryHandler(pick_task, pattern=r"^sub_task:\d+$")],
            KIND: [CallbackQueryHandler(submission_kind, pattern=r"^sub_kind:(file|link|text)$")],
            CONTENT: [MessageHandler(filters.Document.ALL | _TEXT_ONLY, got_content)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        name="submission",
    )
    application.add_handler(conv, group=0)
