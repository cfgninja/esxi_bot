"""Telegram-specific UI helpers."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.error import BadRequest

from .config import ALLOWED_USERS, ALLOWED_VMS, DENY_VMS, VCENTERS
from .vcenter import ru_power_state

log = logging.getLogger("esxi_bot")

__all__ = [
    "build_base_text",
    "vm_action_keyboard",
    "choose_vc_keyboard",
    "busy_keyboard",
    "confirm_keyboard",
    "safe_edit_message",
    "set_menu_for_chat",
    "whoami_text",
    "command_keyboard",
]


def build_base_text(vm, vcidx: Optional[int] = None) -> str:
    suffix = f" — {VCENTERS[vcidx]['name']}" if vcidx is not None else ""
    return f"• {vm.name} ({ru_power_state(vm.runtime.powerState)}){suffix}"


def vm_action_keyboard(vm, vcidx: int) -> InlineKeyboardMarkup:
    state = str(vm.runtime.powerState)
    rows = []
    manage_allowed = (not DENY_VMS or vm.name not in DENY_VMS) and (not ALLOWED_VMS or vm.name in ALLOWED_VMS)
    if manage_allowed:
        if state == "poweredOn":
            rows.append([InlineKeyboardButton("⏻ Выключить", callback_data=f"power:{vcidx}:{vm.name}:off")])
        else:
            rows.append([InlineKeyboardButton("⚡ Включить", callback_data=f"power:{vcidx}:{vm.name}:on")])
        rows.append([
            InlineKeyboardButton("🔄 Перезагрузить", callback_data=f"reboot:{vcidx}:{vm.name}"),
            InlineKeyboardButton("ℹ️ Информация", callback_data=f"info:{vcidx}:{vm.name}")
        ])
    else:
        rows.append([InlineKeyboardButton("ℹ️ Информация", callback_data=f"info:{vcidx}:{vm.name}")])
    return InlineKeyboardMarkup(rows)


def choose_vc_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{i + 1}. {vc['name']} ({vc['host']})", callback_data=f"choosevc:{i}")]
            for i, vc in enumerate(VCENTERS)]
    return InlineKeyboardMarkup(rows)


def busy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏳ Выполняется…", callback_data="noop")]])


def confirm_keyboard(nonce: str, action_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Подтвердить: {action_text}", callback_data=f"cOK:{nonce}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cNO:{nonce}")]
    ])


def safe_edit_message(target, *args, **kwargs):
    """Call edit_message_text and suppress harmless "Message is not modified" errors."""
    try:
        return target.edit_message_text(*args, **kwargs)
    except BadRequest as exc:  # pragma: no cover - depends on Telegram API responses
        if "Message is not modified" in str(exc):
            log.debug("Ignored BadRequest: Message is not modified")
            return None
        raise


def set_menu_for_chat(bot, chat_id: int, allowed: bool) -> None:
    if allowed:
        cmds = [
            BotCommand("start", "Начать"),
            BotCommand("listvc", "Выбрать vCenter"),
            BotCommand("listvm", "Список ВМ выбранного vCenter"),
            BotCommand("searchvm", "Поиск ВМ во всех vCenter"),
            BotCommand("whoami", "Показать ваш ID и права"),
        ]
    else:
        cmds = [BotCommand("start", "Начать"), BotCommand("whoami", "Показать ваш ID и права")]
    try:
        bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id))
    except Exception:
        pass


def whoami_text(user_id: Optional[int]) -> str:
    allowed = "да" if user_id and user_id in ALLOWED_USERS else "нет"
    return f"👤 Ваш Telegram ID: {user_id}\nДоступ к боту (allowed): {allowed}"


def command_keyboard(allowed: bool) -> ReplyKeyboardMarkup:
    if allowed:
        rows = [["📡 Выбрать vCenter", "💻 Список ВМ"], ["🔍 Поиск ВМ", "ℹ️ Мои права"]]
    else:
        rows = [["ℹ️ Мои права"]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)
