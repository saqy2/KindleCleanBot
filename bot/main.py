"""Telegram novel conversion bot — main entry point."""

import logging
import signal
import sys

from telegram import BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import load_config
from .handler import (
    IDLE,
    READY,
    start,
    handle_document,
    handle_requirement,
    help_cmd,
    cancel_cmd,
    done_cmd,
    mail_cmd,
    setmail_cmd,
    mymail_cmd,
)
from .persistence import SQLitePersistence

logger = logging.getLogger(__name__)


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "开始使用"),
        BotCommand("help", "使用帮助"),
        BotCommand("cancel", "取消当前操作"),
        BotCommand("done", "完成并清除文件"),
        BotCommand("setmail", "设置接收邮箱"),
        BotCommand("mail", "发送到邮箱"),
        BotCommand("mymail", "查看当前邮箱"),
    ])


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = load_config()
    token = config.get("telegram", {}).get("token", "")
    if not token:
        raise ValueError("Telegram bot token not configured.")

    persistence = SQLitePersistence(db_path="data/bot.db")

    app = Application.builder().token(token)\
        .persistence(persistence)\
        .post_init(post_init)\
        .build()

    # ── Standalone commands (work without active conversation) ──
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("mail", mail_cmd))
    app.add_handler(CommandHandler("setmail", setmail_cmd))
    app.add_handler(CommandHandler("mymail", mymail_cmd))

    # ── Conversation: file → scan → requirement → convert ──
    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Document.TXT, handle_document),
        ],
        states={
            READY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_requirement),
            ],
        },
        fallbacks=[
            CommandHandler("help", help_cmd),
            CommandHandler("cancel", cancel_cmd),
            CommandHandler("done", done_cmd),
            CommandHandler("finish", done_cmd),
            CommandHandler("mail", mail_cmd),
            CommandHandler("setmail", setmail_cmd),
            CommandHandler("mymail", mymail_cmd),
        ],
        per_chat=True,
        per_message=False,
        allow_reentry=True,
    )

    app.add_handler(conv)

    logger.info("Bot starting... (Ctrl+C to stop)")

    def shutdown(signum, frame):
        logger.info("Shutting down...")
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        app.run_polling()
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down...")
    except Exception:
        logger.exception("Fatal error")
    finally:
        logger.info("Bot stopped.")


if __name__ == "__main__":
    main()
