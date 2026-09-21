"""Admin panel (/admin) — stats, user management, CSV export, task entry point."""
from __future__ import annotations

import logging

from telegram import Update
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
from ..keyboards import (
    admin_panel_keyboard,
    export_keyboard,
    task_admin_keyboard,
    task_delete_confirm_keyboard,
    tasks_admin_keyboard,
    user_detail_keyboard,
    users_page_keyboard,
)
from ..utils import build_submissions_csv, build_users_csv, esc

logger = logging.getLogger(__name__)

PAGE_SIZE = 8


def _is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    cfg: Config = context.bot_data["config"]
    return user_id is not None and cfg.is_admin(user_id)


async def _deny(update: Update) -> None:
    if update.message:
        await update.message.reply_html("⛔ This panel is for authorized admins only.")
    elif update.callback_query:
        await update.callback_query.answer("Not authorized.", show_alert=True)


async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(context, update.effective_user.id):
        await _deny(update)
        return
    await update.message.reply_html(
        "🛠 <b>IT Insights Lab — Admin Panel</b>\n\n"
        f"👥 Students: {db.count_users()} ({db.count_users('banned')} banned)\n"
        f"🗂 Tasks: {db.count_tasks()}\n"
        f"📝 Submissions: {db.count_submissions()} total, {db.count_pending_submissions()} pending\n\n"
        "Choose an action:",
        reply_markup=admin_panel_keyboard(),
    )


async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    await query.edit_message_text(
        "🛠 <b>Admin Panel</b>\n\nChoose an action:",
        reply_markup=admin_panel_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def admin_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    try:
        await query.message.delete()
    except Exception:
        await query.edit_message_text("Panel closed.")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return

    per_task = db.count_submissions_per_task()
    task_lines = []
    for row in per_task[:15]:
        task_lines.append(f"• #{row['id']} {esc(row['title'])} — {row['submissions']} submission(s)")
    per_task_text = "\n".join(task_lines) if task_lines else "• (no tasks yet)"

    text = (
        "📊 <b>Analytics & Stats</b>\n\n"
        f"👥 Total registered: <b>{db.count_users()}</b>\n"
        f"✅ Active students: <b>{db.count_users('active')}</b>\n"
        f"🚫 Banned: <b>{db.count_users('banned')}</b>\n"
        f"🗂 Tasks published: <b>{db.count_tasks()}</b>\n"
        f"📝 Submissions: <b>{db.count_submissions()}</b> total · "
        f"<b>{db.count_pending_submissions()}</b> pending review\n\n"
        "<b>Submissions per task:</b>\n"
        f"{per_task_text}"
    )
    await query.edit_message_text(
        text,
        reply_markup=admin_panel_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return

    rows = db.list_users(offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    total = db.count_users()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    listing = "\n".join(
        f"• {esc(r['full_name'])} ({r['status']})" for r in rows
    ) or "No users registered yet."
    text = (
        f"👥 <b>User Management</b> — page {page + 1}/{total_pages}\n"
        f"<i>{total} registered</i>\n\n{listing}\n\nTap a student for details & actions:"
    )
    await query.edit_message_text(
        text,
        reply_markup=users_page_keyboard(page, total_pages, rows),
        parse_mode=ParseMode.HTML,
    )


async def admin_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    page = int(query.data.split(":")[1])
    await admin_users(update, context, page)


async def admin_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass  # already answered by the ban/unban caller re-rendering this view
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    user_id = int(query.data.split(":")[1])
    user = db.get_user(user_id)
    if not user:
        await query.edit_message_text("⚠️ User not found.")
        return
    text = (
        f"👤 <b>{esc(user['full_name'])}</b> · @{esc(user['username'] or 'no handle')}\n"
        f"🆔 <code>{user['id']}</code>\n"
        f"🏙 {esc(user['city'])} · 💼 {esc(user['occupation'])}\n"
        f"📅 Registered: {esc(user['registered_at'])}\n"
        f"🚦 Status: <b>{esc(user['status'])}</b>\n\n"
        f"📝 {esc(user['bio'])}\n\n"
        f"📝 Submissions: {db.count_submissions(user_id=user_id)}"
    )
    await query.edit_message_text(
        text,
        reply_markup=user_detail_keyboard(user_id, user["status"]),
        parse_mode=ParseMode.HTML,
    )


async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = int(query.data.split(":")[1])
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    try:
        await query.answer()
    except Exception:
        pass
    db.set_user_status(user_id, "banned")
    await admin_user_detail(update, context)


async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    user_id = int(query.data.split(":")[1])
    db.set_user_status(user_id, "active")
    await admin_user_detail(update, context)


async def admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    user_id = int(query.data.split(":")[1])
    db.delete_user(user_id)
    await query.edit_message_text(f"🗑 User {user_id} removed from the roster.")


async def admin_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    await query.edit_message_text(
        "📦 <b>Export Data</b>\n\nChoose what to export as CSV:",
        reply_markup=export_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def admin_export_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    data = build_users_csv(db.iter_users_for_export())
    await context.bot.send_document(
        query.message.chat_id,
        document=bytes(data),
        filename="students_roster.csv",
        caption="👥 Student roster export",
    )


async def admin_export_submissions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    data = build_submissions_csv(db.iter_submissions_for_export())
    await context.bot.send_document(
        query.message.chat_id,
        document=bytes(data),
        filename="submissions_log.csv",
        caption="📝 Submission log export",
    )


async def admin_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Panel view: list all published tasks."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    rows = db.list_tasks()
    if rows:
        text = f"🗂 <b>Manage Tasks</b> — {len(rows)} published\n\nTap a task for details & actions:"
    else:
        text = "🗂 <b>Manage Tasks</b>\n\nNo tasks yet — create one with 📢 Create & Broadcast Task or /newtask."
    await query.edit_message_text(text, reply_markup=tasks_admin_keyboard(rows), parse_mode=ParseMode.HTML)


async def admin_task_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Panel view: one task's details with a delete action."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    task_id = int(query.data.split(":")[1])
    task = db.get_task(task_id)
    if not task:
        await query.edit_message_text(
            "⚠️ Task not found (it may already have been deleted).",
            reply_markup=tasks_admin_keyboard(db.list_tasks()),
            parse_mode=ParseMode.HTML,
        )
        return
    attachment = f"\n📎 Attachment: {esc(task['attachment_kind'] or 'file')}" if task["attachment_file_id"] else ""
    text = (
        f"🎯 <b>Task #{task['id']}: {esc(task['title'])}</b>\n\n"
        f"{esc(task['description'])}\n"
        f"{attachment}\n"
        f"⏰ Deadline: {esc(task['deadline'] or '—')}\n"
        f"📅 Created: {esc(task['created_at'])}\n"
        f"📝 Submissions: <b>{db.count_submissions(task_id=task_id)}</b>\n\n"
        "⚠️ Deleting removes the task <b>and all of its submissions</b>."
    )
    await query.edit_message_text(text, reply_markup=task_admin_keyboard(task_id), parse_mode=ParseMode.HTML)


async def admin_task_delete_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirmation step before deleting a task (destructive + irreversible)."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    task_id = int(query.data.split(":")[1])
    task = db.get_task(task_id)
    if not task:
        await query.edit_message_text(
            "⚠️ Task not found (it may already have been deleted).",
            reply_markup=tasks_admin_keyboard(db.list_tasks()),
            parse_mode=ParseMode.HTML,
        )
        return
    subs = db.count_submissions(task_id=task_id)
    await query.edit_message_text(
        f"🗑 <b>Delete Task #{task['id']}: {esc(task['title'])}?</b>\n\n"
        f"This permanently removes the task and its <b>{subs}</b> submission(s).\n"
        "This cannot be undone.",
        reply_markup=task_delete_confirm_keyboard(task_id),
        parse_mode=ParseMode.HTML,
    )


async def admin_task_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        await _deny(update)
        return
    task_id = int(query.data.split(":")[1])
    if db.delete_task(task_id):
        remaining = db.list_tasks()
        text = f"🗑 Task #{task_id} deleted along with all of its submissions.\n\n🗂 Remaining tasks: {len(remaining)}"
    else:
        remaining = db.list_tasks()
        text = "⚠️ Task not found (it may already have been deleted)."
    await query.edit_message_text(text, reply_markup=tasks_admin_keyboard(remaining), parse_mode=ParseMode.HTML)


APPROVE_WORDS = {"approved", "approve", "/approve", "/approved", "✅", "yes"}
REJECT_WORDS = {"rejected", "reject", "/reject", "/rejected", "❌", "no"}


def _review_word(raw: str) -> str | None:
    """Normalise a reviewer's reply to 'approved' / 'rejected' / None.

    Accepts plain words, the /approve & /reject commands (with or without
    a @botname suffix), emojis, and trailing punctuation.
    """
    w = (raw or "").strip().lower()
    if w.startswith("/"):
        w = w.split("@", 1)[0]  # /approve@itinsightslabbot -> /approve
    w = w.strip(".! ")
    if w in APPROVE_WORDS:
        return "approved"
    if w in REJECT_WORDS:
        return "rejected"
    # tolerate trailing words/emojis, e.g. "approved 👍"
    first = w.split()[0].strip(".! ") if w.split() else ""
    if first in APPROVE_WORDS:
        return "approved"
    if first in REJECT_WORDS:
        return "rejected"
    return None


import re

# matches 'Submission:</b> #3', 'Submission: #1', 'submission #2' (HTML-aware gap)
_SUBMISSION_REF = re.compile(r"submission\b.*?#(\d+)", re.IGNORECASE)


async def on_review_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reviewers approve/reject by replying to the submission post in the review group."""
    cfg: Config = context.bot_data["config"]
    msg = update.message
    if msg is None or not msg.text:
        return
    if msg.reply_to_message is None:
        if _review_word(msg.text) and msg.chat_id == cfg.review_chat_id:
            await msg.reply_html("↩️ Reply to the submission post with that word.")
        return
    if msg.chat_id != cfg.review_chat_id:
        return
    user = update.effective_user
    if not _is_admin(context, user.id):
        return  # only authorized admins decide

    word = _review_word(msg.text)
    if word is None:
        # near-miss diagnostics: helps spot why a review didn't register
        low = msg.text.strip().lower()
        if "approv" in low or "reject" in low:
            logger.info("Review-like message not matched: %r", msg.text)
        return

    logger.info("Review verdict %r for a reply by admin %s", word, user.id)

    replied = msg.reply_to_message
    sub = None
    post_msg = None
    for cand in (replied, replied.reply_to_message):
        if cand is None:
            continue
        sub = db.get_submission_by_review_message(cand.chat_id, cand.message_id)
        if sub is None:
            # fallback for posts created before message-linking existed:
            # parse the submission number out of the post text/caption
            haystack = getattr(cand, "text", None) or getattr(cand, "caption", None) or ""
            m = _SUBMISSION_REF.search(haystack)
            if m:
                sub = db.get_submission(int(m.group(1)))
        if sub is not None:
            post_msg = cand
            break
    if sub is None:
        await msg.reply_html("⚠️ That message isn't a submission post — reply to the submission instead.")
        return
    if sub["status"] != "pending":
        await msg.reply_html(f"ℹ️ Submission #{sub['id']} was already <b>{esc(sub['status'])}</b>.")
        return

    approved = word == "approved"
    db.review_submission(sub["id"], word, update.effective_user.id)

    # tag the verdict on the original review post (bot's own message → editable)
    tag = f"\n\n{'✅ APPROVED' if approved else '❌ REJECTED'} · by {esc(update.effective_user.full_name)}"
    try:
        if post_msg.text:
            await context.bot.edit_message_text(
                chat_id=post_msg.chat_id, message_id=post_msg.message_id,
                text=post_msg.text + tag, parse_mode=ParseMode.HTML,
            )
        elif post_msg.caption:
            await context.bot.edit_message_caption(
                chat_id=post_msg.chat_id, message_id=post_msg.message_id,
                caption=post_msg.caption + tag, parse_mode=ParseMode.HTML,
            )
    except Exception as exc:
        logger.warning("Could not tag verdict on review post: %s", exc)

    # notify the student
    verdict = (
        (f"✅ <b>Your submission was approved!</b>\n\n"
         f"🗂 Task: #{sub['task_id']}\n"
         f"🆔 Submission: #{sub['id']}\n"
         f"👨‍⚖️ Reviewed by: {esc(update.effective_user.full_name)}")
        if approved else
        (f"❌ <b>Your submission was rejected.</b>\n\n"
         f"🗂 Task: #{sub['task_id']}\n"
         f"🆔 Submission: #{sub['id']}\n"
         f"👨‍⚖️ Reviewed by: {esc(update.effective_user.full_name)}\n\n"
         "Feel free to improve and resubmit with /submit!")
    )
    try:
        await context.bot.send_message(sub["user_id"], verdict, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.warning("Could not DM verdict to %s: %s", sub["user_id"], exc)
        await msg.reply_html("⚠️ Couldn't DM the student (they may have never started the bot).")

    await msg.reply_html(f"{'✅' if approved else '❌'} Submission #{sub['id']} marked "
                         f"<b>{word}</b> — student notified.")


def register(application: Application) -> None:
    application.add_handler(CommandHandler("admin", admin_entry), group=2)
    application.add_handler(CallbackQueryHandler(admin_back, pattern=r"^admin:back$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_close, pattern=r"^admin:close$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_stats, pattern=r"^admin:stats$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_users, pattern=r"^admin:users$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_users_page, pattern=r"^adm_users_page:\d+$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_user_detail, pattern=r"^adm_user:\d+$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_ban, pattern=r"^adm_ban:\d+$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_unban, pattern=r"^adm_unban:\d+$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_remove, pattern=r"^adm_remove:\d+$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_export, pattern=r"^admin:export$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_export_users, pattern=r"^adm_export:users$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_export_submissions, pattern=r"^adm_export:submissions$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_tasks, pattern=r"^admin:tasks$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_task_detail, pattern=r"^adm_task:\d+$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_task_delete_ask, pattern=r"^adm_taskdel:\d+$"), group=1)
    application.add_handler(CallbackQueryHandler(admin_task_delete_confirm, pattern=r"^adm_taskdelgo:\d+$"), group=1)
    # /approve & /reject as explicit commands (always delivered in groups,
    # regardless of privacy mode), plus plain-word replies.
    application.add_handler(
        CommandHandler(["approve", "approved", "reject", "rejected"], on_review_reply),
        group=3,
    )
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, on_review_reply),
        group=3,
    )
