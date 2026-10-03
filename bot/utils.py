"""Shared helpers: HTML escaping, task text, intro card, CSV export builders."""
from __future__ import annotations

import csv
import html
import io
import logging
from functools import wraps
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from .config import REQUIRED_CHANNEL, _normalize_channel_username

logger = logging.getLogger(__name__)


async def check_subscription(user_id: int, context: Any) -> bool:
    """Helper to verify if user is subscribed to REQUIRED_CHANNEL.

    Considers user subscribed if member.status is in ['member', 'administrator', 'creator'].
    Catches TelegramError/BadRequest, logs explicit warning, and returns False.
    """
    cfg = None
    if hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
        cfg = context.bot_data.get("config")

    raw_channel = getattr(cfg, "mandatory_channel", None) if cfg else None
    if not raw_channel:
        raw_channel = REQUIRED_CHANNEL

    channel = _normalize_channel_username(raw_channel)
    if not channel:
        return True

    bot = getattr(context, "bot", context)
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        status = getattr(member.status, "value", member.status)
        if status in ("member", "administrator", "creator", "owner"):
            return True
        if status == "restricted":
            return getattr(member, "is_member", False)
        return False
    except (TelegramError, BadRequest, Exception) as exc:
        logger.warning(
            "CRITICAL: Failed to query channel subscription for user_id=%s on channel %s: %s. "
            "Please ensure the bot is added as an Administrator in channel %s!",
            user_id,
            channel,
            exc,
            channel,
        )
        return False


def require_subscription(func: Callable) -> Callable:
    """Decorator to enforce mandatory channel subscription before executing a handler."""
    @wraps(func)
    async def wrapper(update: Update | Any, context: ContextTypes.DEFAULT_TYPE | Any, *args, **kwargs):
        u = getattr(update, "effective_user", None)
        if not u:
            return await func(update, context, *args, **kwargs)

        cb = getattr(update, "callback_query", None)
        if cb and getattr(cb, "data", None) == "check_channel_sub":
            return await func(update, context, *args, **kwargs)

        is_subbed = await check_subscription(u.id, context)
        if not is_subbed:
            cfg = context.bot_data.get("config") if hasattr(context, "bot_data") and isinstance(context.bot_data, dict) else None
            raw_channel = getattr(cfg, "mandatory_channel", None) if cfg else None
            if not raw_channel:
                raw_channel = REQUIRED_CHANNEL
            channel = _normalize_channel_username(raw_channel)
            from .keyboards import channel_sub_keyboard
            keyboard = channel_sub_keyboard(channel)
            prompt = (
                "📢 <b>Mandatory Channel Subscription</b>\n\n"
                f"To use the IT Insights Lab bot, you must first join our channel: <b>{esc(str(channel))}</b>.\n\n"
                "After joining, tap the button below to verify."
            )

            msg = getattr(update, "message", None)
            if cb:
                if hasattr(cb, "answer"):
                    await cb.answer("⚠️ Subscription required! Please join the channel first.", show_alert=True)
                await safe_edit(cb, prompt, reply_markup=keyboard)
            elif msg:
                await msg.reply_html(prompt, reply_markup=keyboard)

            return 0  # CHANNEL_SUB state / 0

        return await func(update, context, *args, **kwargs)

    return wrapper


def esc(value: Any) -> str:
    """Escape user-supplied text for safe use in HTML parse-mode messages."""
    return html.escape(str(value if value is not None else ""), quote=False)


def handle_of(username: str | None, user_id: int | None = None) -> str:
    """Return a mentionable Telegram handle string."""
    if username:
        return f"@{username}"
    if user_id is not None:
        return f"tg://user?id={user_id}"
    return "no handle"


def profile_link(user_id: int, name: str) -> str:
    """Clickable mention for a user (works even without a username)."""
    return f'<a href="tg://user?id={user_id}">{esc(name)}</a>'


def format_task_text(task: Mapping[str, Any]) -> str:
    """Build the full task card shown when a user taps [Get Task]."""
    lines = [f"🎯 <b>Task #{task['id']}: {esc(task['title'])}</b>", ""]
    lines.append(esc(task["description"]))
    lines.append("")
    if task["deadline"]:
        lines.append(f"⏰ <b>Deadline:</b> {esc(task['deadline'])} (UTC)")
    else:
        lines.append("⏰ <b>Deadline:</b> —")
    lines.append("📤 Submit with /submit")
    return "\n".join(lines)


def broadcast_header(task: Mapping[str, Any]) -> str:
    """Short broadcast announcement for a new task."""
    deadline = f"\n⏰ Deadline: {esc(task['deadline'])}" if task["deadline"] else ""
    return (
        "📣 <b>New Task Available!</b>\n\n"
        f"🎯 <b>Task #{task['id']}: {esc(task['title'])}</b>{deadline}"
    )


def build_intro_card(user: Mapping[str, Any]) -> str:
    """Introduction card text posted to the cohort group after registration."""
    username = user.get("username")
    handle = f" · @{esc(username)}" if username else ""
    org = user.get("organization") or "Yuqori Chirchiq TIM"
    return (
        "🎉 <b>New member joined IT Insights Lab!</b>\n\n"
        f"👤 <b>{esc(user['full_name'])}</b>{handle}\n"
        f"🏫 Organisation: <b>{esc(org)}</b>\n"
        f"🏙 City: {esc(user['city'])}\n"
        f"💼 Occupation: {esc(user['occupation'])}\n\n"
        f"📝 {esc(user['bio'])}"
    )


def welcome_text(user: Mapping[str, Any], invite_link: str) -> str:
    """Private welcome message with the group invite link."""
    link_line = f"👉 {invite_link}" if invite_link else "(ask an admin for the invite link)"
    org = user.get("organization") or "Yuqori Chirchiq TIM"
    return (
        "✅ <b>Registration complete — welcome aboard!</b>\n\n"
        f"👤 {esc(user['full_name'])}\n"
        f"🏫 Organisation: <b>{esc(org)}</b>\n"
        f"🏙 {esc(user['city'])} · 💼 {esc(user['occupation'])}\n\n"
        f"🔗 <b>Join your cohort group:</b>\n{link_line}\n\n"
        "📌 <b>Commands:</b>\n"
        "• /tasks — list available tasks\n"
        "• /submit — submit a solution\n"
        "• /myprofile — view your profile\n"
        "• /help — full command list"
    )


def parse_deadline(raw: str) -> str | None:
    """Validate and normalise a deadline string 'YYYY-MM-DD HH:MM'.

    Returns the canonical string, or None when invalid. Empty input -> None.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d %H:%M") if fmt == "%Y-%m-%d %H:%M" else dt.strftime("%Y-%m-%d 23:59")
        except ValueError:
            continue
    raise ValueError(
        "Deadline must look like 2025-09-30 23:59 (or just 2025-09-30)."
    )


def build_users_csv(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """Student roster CSV as bytes (for sending as a document)."""
    headers = [
        "user_id", "username", "full_name", "city", "occupation", "organization",
        "status", "registered_at", "banned_at",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        org = r["organization"] if "organization" in r.keys() else "Yuqori Chirchiq TIM"
        writer.writerow([
            r["id"], r["username"] or "", r["full_name"], r["city"], r["occupation"],
            org, r["status"], r["registered_at"], r["banned_at"] or "",
        ])
    return buf.getvalue().encode("utf-8-sig")


def build_submissions_csv(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """Submission log CSV as bytes (for sending as a document)."""
    headers = [
        "submission_id", "user_id", "student_name", "username",
        "task_id", "task_title", "kind", "content", "file_id", "status", "submitted_at",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([
            r["id"], r["user_id"], r["full_name"], r["username"] or "",
            r["task_id"], r["task_title"], r["kind"], r["content"] or "", r["file_id"] or "",
            r["status"], r["submitted_at"],
        ])
    return buf.getvalue().encode("utf-8-sig")


def rows_to_dicts(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """Convert sqlite3.Row objects to plain dicts."""
    return [dict(r) for r in rows]


async def safe_edit(query, text: str, reply_markup=None) -> None:
    """Edit the message a button sits on, whether it is text or media.

    Telegram forbids edit_message_text() on media posts (a photo/document with
    a caption) and raises BadRequest("There is no text in the message to edit").
    Strategy: edit the text; if that fails because the message is media, edit
    the caption instead; if that also fails, send a fresh message.
    All bot texts are HTML, so parse_mode is fixed to HTML.
    """
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest:
        try:
            await query.edit_message_caption(
                caption=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
            )
        except BadRequest as exc:
            logger.warning("safe_edit fell back to a new message: %s", exc)
            await query.bot.send_message(
                query.message.chat_id, text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
            )
