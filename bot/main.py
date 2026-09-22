"""Application entry point: wiring, database init, run polling."""
from __future__ import annotations

import logging
import sys

import warnings

from telegram.ext import Application, ApplicationBuilder
from telegram.warnings import PTBUserWarning

from .config import Config, load_config
from . import db
from .handlers import admin, common, registration, submit, tasks

__version__ = "1.3.0"  # auto-forward & bot-message guards + private-chat FSM scoping


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

    registration.register(app)   # group 0 — /start conversation
    tasks.register(app)          # group 0 — /newtask conversation + task viewers
    submit.register(app)         # group 0 — /submit conversation
    admin.register(app)          # groups 1-2 — panel callbacks & command
    common.register(app)         # groups 2, 90, 100 — help, fallback, errors
    return app


def _make_post_init(cfg: Config):
    async def post_init(app: Application) -> None:
        me = await app.bot.get_me()
        logging.getLogger(__name__).info("Bot authorised as @%s (v%s)", me.username, __version__)
        await common.setup_bot_commands(app, cfg)
        for admin_id in cfg.admin_ids:
            try:
                await app.bot.send_message(
                    admin_id,
                    f"🟢 IT Insights Lab bot v{__version__} is online.\n"
                    "DM me /start here to register, or /help for all commands.",
                )
            except Exception:
                pass
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
