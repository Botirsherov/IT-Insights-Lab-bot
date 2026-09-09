"""Application entry point: wiring, database init, run polling."""
from __future__ import annotations

import logging
import sys

from telegram.ext import Application, ApplicationBuilder

from .config import Config, load_config
from . import db
from .handlers import admin, common, registration, submit, tasks


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        level=getattr(logging, level, logging.INFO),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


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
        logging.getLogger(__name__).info("Bot authorised as @%s", me.username)
        for admin_id in cfg.admin_ids:
            try:
                await app.bot.send_message(admin_id, "🟢 IT Insights Lab bot is online.")
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
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
