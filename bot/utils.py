"""Shared helpers: HTML escaping, task text, intro card, CSV export builders."""
from __future__ import annotations

import csv
import html
import io
import logging
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from telegram.constants import ParseMode
from telegram.error import BadRequest

logger = logging.getLogger(__name__)


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
