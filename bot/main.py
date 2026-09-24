"""Application entry point: wiring, database init, run polling."""
from __future__ import annotations

import asyncio
import html
import logging
import sys
import warnings

from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder
from telegram.warnings import PTBUserWarning

from .config import Config, load_config
from . import db
from .handlers import admin, common, registration, submit, tasks

__version__ = "1.3.1"  # bug-fixes: asyncio import, catch-up in post_init, no double error-handler


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        level=getattr(logging, level, logging.INFO),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # ConversationHandler's per_message=False info warning is expected for our
    # design (mixed message+callback states); filter it from the console.
    warnings.filterwarnings("ignore", category=PTBUserWarning,
                            message=".*'CallbackQueryHandler' will not be tracked.*")


def create_application(cfg: Config) -> Application:
    db.init_db(cfg.database_path)
    app = (
        ApplicationBuilder()
        .token(cfg.bot_token)
        .post_init(_make_post_init(cfg))
        .build()
    )
    app.bot_data["config"] = cfg

    # Global error handler — notifies every admin and the reviewers group
    # with the full traceback for any unhandled exception.
    app.add_error_handler(common.on_error)

    registration.register(app)   # group 0 — /start conversation
    tasks.register(app)          # group 0 — /newtask conversation + task viewers
    submit.register(app)         # group 0 — /submit conversation
    admin.register(app)          # groups 1-2 — panel callbacks & command
    common.register(app)         # groups 2, 90 — help, fallback
    return app


def _make_post_init(cfg: Config):
    async def post_init(app: Application) -> None:
        me = await app.bot.get_me()
        log = logging.getLogger(__name__)
        log.info("Bot authorised as @%s (v%s)", me.username, __version__)
        await common.setup_bot_commands(app, cfg)

        # Notify admins that the bot is online.
        for admin_id in cfg.admin_ids:
            try:
                await app.bot.send_message(
                    admin_id,
                    f"🟢 IT Insights Lab bot v{__version__} is online.\n"
                    "DM me /start here to register, or /help for all commands.",
                )
            except Exception:
                pass

        # Bring the reviewers group up to date: forward any submissions
        # that were submitted while the bot was offline (i.e. never posted).
        pending = db.catch_up_pending_submissions()
        if pending:
            log.info("Catching up %d unposted submission(s)…", len(pending))
        for row in pending:
            try:
                name = row["full_name"] or f"User #{row['user_id']}"
                handle = f"@{row['username']}" if row["username"] else name
                body = (
                    f"📥 <b>Recovered submission (bot was offline)</b>\n\n"
                    f"👤 <b>Student:</b> {html.escape(name)} · {html.escape(handle)}\n"
                    f"🗂 <b>Task:</b> #{row['task_id']} — {html.escape(row['task_title'])}\n"
                    f"🆔 <b>Submission:</b> #{row['id']}\n"
                    f"📅 <b>Submitted:</b> {html.escape(row['submitted_at'])}\n"
                    f"🕐 <b>Status:</b> Pending Review"
                )
                if row["kind"] == "file" and row["file_id"]:
                    caption = body + f"\n\n📎 <b>File attached</b>"
                    sent_msg = await app.bot.send_document(
                        cfg.review_chat_id, row["file_id"],
                        caption=caption, parse_mode=ParseMode.HTML,
                    )
                else:
                    if row["kind"] == "link" and row["content"]:
                        solution = f"\n\n🔗 <b>Solution:</b> {html.escape(row['content'])}"
                    elif row["content"]:
                        solution = f"\n\n📝 <b>Solution:</b>\n\n{html.escape(row['content'])}"
                    else:
                        solution = ""
                    sent_msg = await app.bot.send_message(
                        cfg.review_chat_id, body + solution, parse_mode=ParseMode.HTML,
                    )
                db.mark_submission_review_sent(row["id"], cfg.review_chat_id, sent_msg.message_id)
                log.info("Catch-up: posted submission #%s to review group", row["id"])
            except Exception:
                log.exception("Catch-up failed for submission #%s", row["id"])

    return post_init



def main() -> None:
    try:
        cfg = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    _setup_logging(cfg.log_level)
    app = create_application(cfg)
    logging.getLogger(__name__).info("Starting bot (polling)…")
    # drop_pending_updates=False: messages sent while the bot was offline (e.g.
    # a /start) are processed on launch instead of being silently discarded.
    app.run_polling(drop_pending_updates=False, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
