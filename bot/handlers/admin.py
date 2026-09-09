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
)

from .. import db
from ..config import Config
from ..keyboards import admin_panel_keyboard, export_keyboard, users_page_keyboard, user_detail_keyboard
from ..utils import build_submissions_csv, build_users_csv, esc

logger = logging.getLogger(__name__)

PAGE_SIZE = 8


def _is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    cfg: Config = context.bot_data["config"]
    return user_id is not None and cfg.is_admin(user_id)


def _deny(update: Update) -> None:
    if update.message:
        update.message.reply_html("⛔ This panel is for authorized admins only.")
    elif update.callback_query:
        update.callback_query.answer("Not authorized.", show_alert=True)


async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(context, update.effective_user.id):
        _deny(update)
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
        _deny(update)
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
        _deny(update)
        return
    try:
        await query.message.delete()
    except Exception:
        await query.edit_message_text("Panel closed.")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        _deny(update)
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
        _deny(update)
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
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        _deny(update)
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
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        _deny(update)
        return
    user_id = int(query.data.split(":")[1])
    if user_id in context.bot_data["config"].admin_ids:
        await query.answer("You can't ban another admin.", show_alert=True)
        return
    db.set_user_status(user_id, "banned")
    await admin_user_detail(update, context)


async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        _deny(update)
        return
    user_id = int(query.data.split(":")[1])
    db.set_user_status(user_id, "active")
    await admin_user_detail(update, context)


async def admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        _deny(update)
        return
    user_id = int(query.data.split(":")[1])
    db.delete_user(user_id)
    await query.edit_message_text(f"🗑 User {user_id} removed from the roster.")


async def admin_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(context, query.from_user.id):
        _deny(update)
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
        _deny(update)
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
        _deny(update)
        return
    data = build_submissions_csv(db.iter_submissions_for_export())
    await context.bot.send_document(
        query.message.chat_id,
        document=bytes(data),
        filename="submissions_log.csv",
        caption="📝 Submission log export",
    )


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
