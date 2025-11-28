"""Application entry point for esxi_bot."""

from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import CallbackQueryHandler, CommandHandler, Filters, MessageHandler, Updater

from .config import BOT_TOKEN
from .handlers import (
    any_command_handler,
    any_text_handler,
    cb_choose_vc,
    cb_confirm_cancel,
    cb_confirm_ok,
    cb_info,
    cb_noop,
    cb_power,
    cb_reboot,
    listvc,
    listvm,
    searchvm,
    start,
    whoami,
)
from .singleton import ensure_single_instance
from .vc_events import start_listeners, stop_listeners

log = logging.getLogger("esxi_bot")

__all__ = ["run"]


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


def run() -> None:
    ensure_single_instance()
    _configure_logging()

    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not configured")

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start), group=1)
    dp.add_handler(CommandHandler("listvc", listvc), group=1)
    dp.add_handler(CommandHandler("listvm", listvm), group=1)
    dp.add_handler(CommandHandler("whoami", whoami), group=1)
    dp.add_handler(CommandHandler("searchvm", searchvm), group=1)

    dp.add_handler(MessageHandler(Filters.text & (~Filters.command), any_text_handler), group=0)

    dp.add_handler(CallbackQueryHandler(cb_noop, pattern=r"^noop$"), group=2)
    dp.add_handler(CallbackQueryHandler(cb_choose_vc, pattern=r"^choosevc:"), group=2)
    dp.add_handler(CallbackQueryHandler(cb_info, pattern=r"^info:"), group=2)
    dp.add_handler(CallbackQueryHandler(cb_reboot, pattern=r"^reboot:"), group=2)
    dp.add_handler(CallbackQueryHandler(cb_power, pattern=r"^power:"), group=2)
    dp.add_handler(CallbackQueryHandler(cb_confirm_ok, pattern=r"^cOK:"), group=2)
    dp.add_handler(CallbackQueryHandler(cb_confirm_cancel, pattern=r"^cNO:"), group=2)

    unknown_cmd_filter = Filters.command & ~Filters.regex(r"^/(start|listvc|listvm|whoami|searchvm)(\s|$)")
    dp.add_handler(MessageHandler(unknown_cmd_filter, any_command_handler), group=3)

    try:
        updater.bot.set_my_commands(
            [BotCommand("start", "Начать"), BotCommand("whoami", "Показать ваш ID и права")]
        )
    except Exception:
        log.debug("Failed to set default bot commands", exc_info=True)

    listeners = start_listeners(updater.bot)
    try:
        updater.start_polling()
        updater.idle()
    finally:
        stop_listeners(listeners)
