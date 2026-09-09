"""Inline keyboard builders for the bot."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

OCCUPATION_PRESETS = ["Student", "Working Professional", "Data Analyst"]


def occupation_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"🎓 {o}", callback_data=f"reg_occ:{o}")] for o in OCCUPATION_PRESETS]
    rows.append([InlineKeyboardButton("✍️ Other (type it)", callback_data="reg_occ:__other__")])
    return InlineKeyboardMarkup(rows)


def confirm_task_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🚀 Broadcast", callback_data="task_confirm:yes"),
            InlineKeyboardButton("❌ Discard", callback_data="task_confirm:no"),
        ]]
    )


def get_task_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📤 Submit Solution", callback_data=f"submit_for:{task_id}")]]
    )


def submission_kind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("📎 File", callback_data="sub_kind:file"),
            InlineKeyboardButton("🔗 Link", callback_data="sub_kind:link"),
            InlineKeyboardButton("✍️ Text", callback_data="sub_kind:text"),
        ]]
    )


def task_pick_keyboard(tasks: list) -> InlineKeyboardMarkup:
    """Keyboard listing up to 10 recent tasks for the submit flow."""
    rows = [
        [InlineKeyboardButton(f"#{t['id']} — {t['title'][:40]}", callback_data=f"sub_task:{t['id']}")]
        for t in tasks[:10]
    ]
    return InlineKeyboardMarkup(rows) if rows else None


def skip_keyboard(callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip", callback_data=callback)]])


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Create & Broadcast Task", callback_data="admin:newtask")],
            [InlineKeyboardButton("📊 Analytics & Stats", callback_data="admin:stats")],
            [InlineKeyboardButton("👥 User Management", callback_data="admin:users")],
            [InlineKeyboardButton("📦 Export Data", callback_data="admin:export")],
            [InlineKeyboardButton("❌ Close", callback_data="admin:close")],
        ]
    )


def users_page_keyboard(page: int, total_pages: int, user_rows: list) -> InlineKeyboardMarkup:
    """User list page: one row per user plus pagination controls."""
    rows = [
        [InlineKeyboardButton(f"👤 {r['full_name'][:28]} ({r['status']})", callback_data=f"adm_user:{r['id']}")]
        for r in user_rows
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"adm_users_page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"adm_users_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Back to panel", callback_data="admin:back")])
    return InlineKeyboardMarkup(rows)


def user_detail_keyboard(user_id: int, status: str) -> InlineKeyboardMarkup:
    toggle = (
        InlineKeyboardButton("✅ Unban", callback_data=f"adm_unban:{user_id}")
        if status == "banned"
        else InlineKeyboardButton("🚫 Ban", callback_data=f"adm_ban:{user_id}")
    )
    return InlineKeyboardMarkup(
        [
            [toggle, InlineKeyboardButton("🗑 Remove", callback_data=f"adm_remove:{user_id}")],
            [InlineKeyboardButton("⬅️ Back to list", callback_data="admin:users")],
        ]
    )


def export_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥 Students CSV", callback_data="adm_export:users")],
            [InlineKeyboardButton("📝 Submissions CSV", callback_data="adm_export:submissions")],
            [InlineKeyboardButton("⬅️ Back to panel", callback_data="admin:back")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
