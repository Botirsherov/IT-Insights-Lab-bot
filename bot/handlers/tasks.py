"""Admin task creation + broadcast, and the [Get Task] viewer."""
from __future__ import annotations

import logging

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from ..keyboards import confirm_task_keyboard, get_task_keyboard, skip_keyboard
from ..utils import broadcast_header, esc, format_task_text, parse_deadline, safe_edit

logger = logging.getLogger(__name__)

TITLE, DESCRIPTION, ATTACHMENT, DEADLINE, CONFIRM = range(5)

_TEXT_ONLY = filters.TEXT & ~filters.COMMAND


def _is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    cfg: Config = context.bot_data["config"]
    return user_id is not None and cfg.is_admin(user_id)


# --------------------------------------------------------------------------- #
# Admin: create & broadcast a task
# --------------------------------------------------------------------------- #
async def newtask_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(context, update.effective_user.id):
        if update.message:
            await update.message.reply_html("⛔ This command is for admins only.")
        elif update.callback_query:
            await update.callback_query.answer("Admins only.", show_alert=True)
        return ConversationHandler.END
    prompt = (
        "📢 <b>Create a new task</b>\n\n"
        "<b>1/4 — Task title</b>\n<i>(short, e.g. “Pandas Basics — GroupBy”)</i>"
    )
    context.user_data.clear()
    if update.message:
        await update.message.reply_html(prompt)
    else:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(prompt, parse_mode=ParseMode.HTML)
    return TITLE


async def task_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not (3 <= len(text) <= 120):
        await update.message.reply_html("⚠️ Title must be 3–120 characters.")
        return TITLE
    context.user_data["task_title"] = text
    await update.message.reply_html(
        f"Title: <b>{esc(text)}</b>\n\n<b>2/4 — Task description</b>\n"
        "<i>(what students should do, expected deliverables — send as one message)</i>"
    )
    return DESCRIPTION


async def task_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if len(text) < 10:
        await update.message.reply_html("⚠️ Description must be at least 10 characters.")
        return DESCRIPTION
    context.user_data["task_description"] = text
    await update.message.reply_html(
        "✅ Description saved.\n\n<b>3/4 — Optional attachment</b>\n"
        "Send a document or photo with the task materials, or tap Skip.",
        reply_markup=skip_keyboard("task_skip:attachment"),
    )
    return ATTACHMENT


async def task_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    if msg.document:
        context.user_data["task_attachment_file_id"] = msg.document.file_id
        context.user_data["task_attachment_kind"] = "document"
        name = msg.document.file_name or "document"
    elif msg.photo:
        context.user_data["task_attachment_file_id"] = msg.photo[-1].file_id
        context.user_data["task_attachment_kind"] = "photo"
        name = "photo"
    else:
        await msg.reply_html("⚠️ Please send a document or a photo, or tap Skip.")
        return ATTACHMENT
    await msg.reply_html(
        f"✅ Attachment saved ({esc(name)}).\n\n<b>4/4 — Deadline</b>\n"
        "Send it as <code>YYYY-MM-DD HH:MM</code> (UTC) or <code>YYYY-MM-DD</code>, or tap Skip.",
        reply_markup=skip_keyboard("task_skip:deadline"),
    )
    return DEADLINE


async def task_attachment_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("task_attachment_file_id", None)
    context.user_data.pop("task_attachment_kind", None)
    await query.edit_message_text(
        "Skipped attachment.\n\n<b>4/4 — Deadline</b>\n"
        "Send <code>YYYY-MM-DD HH:MM</code> (UTC) or <code>YYYY-MM-DD</code>, or tap Skip.",
        parse_mode=ParseMode.HTML,
    )
    return DEADLINE


async def task_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    try:
        deadline = parse_deadline(text)
    except ValueError as exc:
        await update.message.reply_html(f"⚠️ {exc}")
        return DEADLINE
    context.user_data["task_deadline"] = deadline
    return await _ask_confirm(update, context)


async def task_deadline_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["task_deadline"] = None
    await query.edit_message_text("Skipped deadline.")
    return await _ask_confirm(update, context)


async def _ask_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    d = context.user_data
    summary = (
        f"📋 <b>Task preview</b>\n\n"
        f"<b>Title:</b> {esc(d['task_title'])}\n"
        f"<b>Description:</b> {esc(d['task_description'])}\n"
        f"<b>Attachment:</b> {esc(d.get('task_attachment_kind') or 'none')}\n"
        f"<b>Deadline:</b> {esc(d.get('task_deadline') or 'none')}\n\n"
        "Broadcast to all active students?"
    )
    if update.message:
        await update.message.reply_html(summary, reply_markup=confirm_task_keyboard())
    else:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(summary, reply_markup=confirm_task_keyboard(), parse_mode=ParseMode.HTML)
    return CONFIRM


async def task_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "task_confirm:no":
        context.user_data.clear()
        await query.edit_message_text("❌ Task discarded.")
        return ConversationHandler.END

    d = context.user_data
    task_id = db.create_task(
        title=d["task_title"],
        description=d["task_description"],
        attachment_file_id=d.get("task_attachment_file_id"),
        attachment_kind=d.get("task_attachment_kind"),
        deadline=d.get("task_deadline"),
        created_by=update.effective_user.id,
    )
    task = db.get_task(task_id)
    header = broadcast_header(task)
    keyboard = get_task_keyboard(task_id)

    user_ids = db.list_all_user_ids(active_only=True)
    sent = failed = 0
    for uid in user_ids:
        try:
            await _send_task_broadcast(context, uid, task, header, keyboard)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("Broadcast to %s failed: %s", uid, exc)
        if sent and sent % 25 == 0:
            await asyncio.sleep(1.0)  # stay under Telegram rate limits

    context.user_data.clear()
    await query.edit_message_text(
        f"🚀 Task #{task_id} broadcast!\n✅ Delivered: {sent}   ⚠️ Failed: {failed}",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Student: [Get Task] viewer + /tasks list
# --------------------------------------------------------------------------- #
async def _send_task_broadcast(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                               task, header: str, keyboard) -> None:
    """Send the broadcast: attachment + caption when the task has a file, else text."""
    if task["attachment_file_id"]:
        send = (
            context.bot.send_photo
            if task["attachment_kind"] == "photo"
            else context.bot.send_document
        )
        await send(chat_id, task["attachment_file_id"], caption=header,
                   parse_mode=ParseMode.HTML, reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id, header, parse_mode=ParseMode.HTML,
                                       reply_markup=keyboard)


async def show_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    # callback data looks like "task:get:12" — the id is the LAST segment
    task_id = int(query.data.split(":")[-1])
    task = db.get_task(task_id)
    if not task:
        # the button may sit on a media broadcast (photo/doc + caption)
        await safe_edit(query, "⚠️ This task no longer exists.")
        return
    text = format_task_text(task)
    if task["attachment_file_id"]:
        if task["attachment_kind"] == "photo":
            await context.bot.send_photo(query.message.chat_id, task["attachment_file_id"], caption=text,
                                         parse_mode=ParseMode.HTML, reply_markup=get_task_keyboard(task_id))
        else:
            await context.bot.send_document(query.message.chat_id, task["attachment_file_id"], caption=text,
                                            parse_mode=ParseMode.HTML, reply_markup=get_task_keyboard(task_id))
        try:
            await query.message.delete()
        except Exception:
            pass
    else:
        await query.edit_message_text(text, reply_markup=get_task_keyboard(task_id), parse_mode=ParseMode.HTML)


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tasks = db.list_tasks()
    if not tasks:
        await update.message.reply_html("📭 No tasks have been published yet.")
        return
    lines = ["📚 <b>Available tasks</b>", ""]
    for t in tasks[:10]:
        deadline = f" · ⏰ {esc(t['deadline'])}" if t["deadline"] else ""
        lines.append(f"#{t['id']} — <b>{esc(t['title'])}</b>{deadline}")
    lines.append("")
    lines.append("Tap a task below to see the full description:")
    keyboard_rows = [
        [InlineKeyboardButton(f"#{t['id']} — {t['title'][:40]}", callback_data=f"task:get:{t['id']}")]
        for t in tasks[:10]
    ]
    await update.message.reply_html("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard_rows))


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register(application: Application) -> None:
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("newtask", newtask_entry),
            CallbackQueryHandler(newtask_entry, pattern=r"^admin:newtask$"),
        ],
        states={
            TITLE: [MessageHandler(_TEXT_ONLY, task_title)],
            DESCRIPTION: [MessageHandler(_TEXT_ONLY, task_description)],
            ATTACHMENT: [
                MessageHandler(filters.Document.ALL | filters.PHOTO, task_attachment),
                CallbackQueryHandler(task_attachment_skip, pattern=r"^task_skip:attachment$"),
            ],
            DEADLINE: [
                MessageHandler(_TEXT_ONLY, task_deadline),
                CallbackQueryHandler(task_deadline_skip, pattern=r"^task_skip:deadline$"),
            ],
            CONFIRM: [CallbackQueryHandler(task_confirm, pattern=r"^task_confirm:")],
        },
        fallbacks=[CommandHandler("cancel", _cancel_task)],
        allow_reentry=True,
        name="task_creation",
    )
    application.add_handler(conv, group=0)
    application.add_handler(CallbackQueryHandler(show_task, pattern=r"^task:get:\d+$"), group=1)
    application.add_handler(CommandHandler("tasks", tasks_command), group=2)


async def _cancel_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_html("❌ Task creation cancelled.")
    return ConversationHandler.END
