"""Telegram handlers and business logic."""

from __future__ import annotations

import re
import time
from typing import Callable, Optional

from telegram import Update
from telegram.ext import CallbackContext

from .config import (
    ALLOWED_USERS,
    CONFIRM_OFF,
    CONFIRM_ON,
    CONFIRM_REBOOT,
    CONFIRM_TTL,
    INFO_TTL,
    POWER_TIMEOUT_HARD,
    POWER_TIMEOUT_ON,
    POWER_TIMEOUT_SOFT,
    SEARCH_WAIT_TTL,
    SUCCESS_TTL,
    VCENTERS,
)
from .state import (
    PENDING_CONFIRMS,
    PC_GUARD,
    SELECTED_VC,
    consume_search_timeout,
    chat_ops_release,
    chat_ops_try_acquire,
    is_search_await,
    new_nonce,
    set_search_await,
    throttle,
    vm_lock,
)
from .telegram_ui import (
    busy_keyboard,
    build_base_text,
    choose_vc_keyboard,
    command_keyboard,
    confirm_keyboard,
    safe_edit_message,
    set_menu_for_chat,
    vm_action_keyboard,
    whoami_text,
)
from .vcenter import (
    Disconnect,
    find_vm,
    get_all_vms,
    refresh_power_state,
    ru_power_state,
    vcenter_connect,
    wait_power_state,
)

CallbackFunc = Callable[[Update, CallbackContext], Optional[object]]

__all__ = [
    "start",
    "listvc",
    "listvm",
    "whoami",
    "searchvm",
    "any_text_handler",
    "any_command_handler",
    "cb_noop",
    "cb_choose_vc",
    "cb_info",
    "cb_reboot",
    "cb_power",
    "cb_confirm_ok",
    "cb_confirm_cancel",
    "private_only",
    "restricted",
    "rate_limited",
]


def _deny(update: Update, reason: str) -> None:
    q = getattr(update, "callback_query", None)
    if q:
        try:
            q.answer(reason, show_alert=True)
        except Exception:
            pass
        try:
            q.message.reply_text(reason)
        except Exception:
            pass
    else:
        em = update.effective_message
        if em:
            em.reply_text(reason)


def private_only(func: CallbackFunc) -> CallbackFunc:
    def wrapper(update: Update, context: CallbackContext):
        chat = update.effective_chat
        if not chat or getattr(chat, "type", "") != "private":
            _deny(update, "⛔ Этот бот работает только в личных чатах. Напишите ему напрямую.")
            return None
        return func(update, context)

    return wrapper  # type: ignore[return-value]


def restricted(func: CallbackFunc) -> CallbackFunc:
    def wrapper(update: Update, context: CallbackContext):
        chat = update.effective_chat
        if not chat or getattr(chat, "type", "") != "private":
            _deny(update, "⛔ Этот бот работает только в личных чатах. Напишите ему напрямую.")
            return None
        user = update.effective_user
        if not user or user.id not in ALLOWED_USERS:
            set_menu_for_chat(context.bot, chat.id, allowed=False)
            _deny(update, "⛔ У вас нет прав на использование этого бота.\nСвяжитесь с администратором для получения доступа.")
            return None
        set_menu_for_chat(context.bot, chat.id, allowed=True)
        return func(update, context)

    return wrapper  # type: ignore[return-value]


def rate_limited(func: CallbackFunc) -> CallbackFunc:
    def wrapper(update: Update, context: CallbackContext):
        user = update.effective_user
        if user and not throttle(user.id):
            q = getattr(update, "callback_query", None)
            if q:
                q.answer("Чуть медленнее 🙂", show_alert=False)
            else:
                message = update.effective_message
                if message:
                    message.reply_text("Чуть медленнее 🙂")
            return None
        return func(update, context)

    return wrapper  # type: ignore[return-value]


def _perform_search_all_vc(update: Update, query: str) -> None:
    total = 0
    for idx, vc in enumerate(VCENTERS):
        try:
            regex = re.compile(re.escape(query), re.IGNORECASE)
            si = vcenter_connect(idx)
            try:
                content = si.RetrieveContent()
                vms = get_all_vms(content)
                hits = [vm for vm in vms if regex.search(vm.name)]
                if hits:
                    update.message.reply_text(f"🔎 {vc['name']}: найдено {len(hits)}")
                    for vm in hits:
                        update.message.reply_text(build_base_text(vm, idx), reply_markup=vm_action_keyboard(vm, idx))
                        total += 1
            finally:
                Disconnect(si)
        except Exception as exc:
            update.message.reply_text(f"❌ Ошибка поиска на {vc['name']}: {exc}")
    if total == 0:
        update.message.reply_text(f"Ничего не найдено по «{query}».")


def _restore_card(bot, chat_id: int, msg_id: int, vcidx: int, vm_name: str) -> None:
    si = None
    try:
        si = vcenter_connect(vcidx)
        content = si.RetrieveContent()
        vm = find_vm(content, vm_name)
        if vm:
            safe_edit_message(
                bot,
                chat_id=chat_id,
                message_id=msg_id,
                text=build_base_text(vm, vcidx),
                reply_markup=vm_action_keyboard(vm, vcidx),
            )
    finally:
        if si:
            Disconnect(si)


def _present_confirm(q, vcidx: int, vm_name: str, action: str, action_text: str, jq) -> None:
    nonce = new_nonce()
    with PC_GUARD:
        PENDING_CONFIRMS[nonce] = {
            "chat_id": q.message.chat_id,
            "msg_id": q.message.message_id,
            "user_id": q.from_user.id,
            "vcidx": vcidx,
            "vm_name": vm_name,
            "action": action,
            "expires": time.time() + CONFIRM_TTL,
        }
    safe_edit_message(
        q,
        f"• {vm_name} — подтвердите действие: {action_text}\n⏳ Истечёт через {CONFIRM_TTL} сек.",
        reply_markup=confirm_keyboard(nonce, action_text),
    )

    def _expire(context: CallbackContext) -> None:
        with PC_GUARD:
            data = PENDING_CONFIRMS.pop(nonce, None)
        if not data:
            return
        try:
            _restore_card(context.bot, data["chat_id"], data["msg_id"], data["vcidx"], data["vm_name"])
        except Exception:
            pass

    jq.run_once(_expire, when=CONFIRM_TTL)


def _do_power_action(q, context: CallbackContext, vcidx: int, vm_name: str, action: str) -> None:
    chat_id = q.message.chat_id
    lock = vm_lock(vcidx, vm_name)
    if not lock.acquire(blocking=False):
        q.answer("⏳ По этой ВМ уже выполняется действие.", show_alert=True)
        return
    if not chat_ops_try_acquire(chat_id):
        lock.release()
        q.answer("Слишком много операций сразу. Подождите и попробуйте ещё раз.", show_alert=True)
        return

    si = vcenter_connect(vcidx)
    try:
        content = si.RetrieveContent()
        vm = find_vm(content, vm_name)
        if not vm:
            safe_edit_message(q, f"• {vm_name} (недоступна)")
            return

        base = build_base_text(vm, vcidx)
        safe_edit_message(q, base + f"\n\n⏳ {'Включаю' if action == 'on' else 'Выключаю'} {vm_name}…",
                          reply_markup=busy_keyboard())

        result, path = False, "-"
        if action == "on":
            if refresh_power_state(vm) != "poweredOn":
                vm.PowerOnVM_Task()
            result = wait_power_state(vm, "poweredOn", timeout=POWER_TIMEOUT_ON, poll=3)
            path = "soft"
        elif action == "off":
            if refresh_power_state(vm) != "poweredOff":
                try:
                    vm.ShutdownGuest()
                    if wait_power_state(vm, "poweredOff", timeout=POWER_TIMEOUT_SOFT, poll=3):
                        result, path = True, "soft"
                    else:
                        vm.PowerOffVM_Task()
                        result = wait_power_state(vm, "poweredOff", timeout=POWER_TIMEOUT_HARD, poll=3)
                        path = "hard" if result else "hard?"
                except Exception:
                    vm.PowerOffVM_Task()
                    result = wait_power_state(vm, "poweredOff", timeout=POWER_TIMEOUT_HARD, poll=3)
                    path = "hard" if result else "hard?"

        base = build_base_text(vm, vcidx)
        if result:
            safe_edit_message(
                q,
                base + f"\n\n✅ {vm_name}: выполнено ({path}).",
                reply_markup=vm_action_keyboard(vm, vcidx),
            )
            context.job_queue.run_once(
                _restore_card_job,
                when=SUCCESS_TTL,
                context={
                    "chat_id": q.message.chat_id,
                    "message_id": q.message.message_id,
                    "vm_name": vm.name,
                    "vcidx": vcidx,
                },
            )
        else:
            safe_edit_message(
                q,
                base + f"\n\n❌ {vm_name}: не удалось подтвердить результат. Проверьте в vCenter.",
                reply_markup=vm_action_keyboard(vm, vcidx),
            )
    finally:
        try:
            lock.release()
        except Exception:
            pass
        chat_ops_release(chat_id)
        Disconnect(si)


def _do_reboot_action(q, context: CallbackContext, vcidx: int, vm_name: str) -> None:
    chat_id = q.message.chat_id
    lock = vm_lock(vcidx, vm_name)
    if not lock.acquire(blocking=False):
        q.answer("⏳ По этой ВМ уже выполняется действие.", show_alert=True)
        return
    if not chat_ops_try_acquire(chat_id):
        lock.release()
        q.answer("Слишком много операций сразу. Подождите и попробуйте ещё раз.", show_alert=True)
        return

    si = vcenter_connect(vcidx)
    try:
        content = si.RetrieveContent()
        vm = find_vm(content, vm_name)
        if not vm:
            safe_edit_message(q, f"• {vm_name} (недоступна)")
            return

        base = build_base_text(vm, vcidx)
        safe_edit_message(q, base + f"\n\n⏳ Перезагружаю {vm_name}…", reply_markup=busy_keyboard())

        result, path = False, "-"
        try:
            vm.RebootGuest()
            result = wait_power_state(vm, "poweredOn", timeout=POWER_TIMEOUT_ON, poll=3)
            path = "soft"
        except Exception:
            try:
                vm.ResetVM_Task()
                result = wait_power_state(vm, "poweredOn", timeout=POWER_TIMEOUT_ON, poll=3)
                path = "hard"
            except Exception:
                result = False
                path = "-"

        base = build_base_text(vm, vcidx)
        if result:
            safe_edit_message(
                q,
                base + f"\n\n✅ {vm_name}: перезагрузка выполнена ({path}).",
                reply_markup=vm_action_keyboard(vm, vcidx),
            )
            context.job_queue.run_once(
                _restore_card_job,
                when=SUCCESS_TTL,
                context={
                    "chat_id": q.message.chat_id,
                    "message_id": q.message.message_id,
                    "vm_name": vm.name,
                    "vcidx": vcidx,
                },
            )
        else:
            safe_edit_message(
                q,
                base + f"\n\n❌ {vm_name}: не удалось подтвердить перезагрузку. Проверьте в vCenter.",
                reply_markup=vm_action_keyboard(vm, vcidx),
            )
    finally:
        try:
            lock.release()
        except Exception:
            pass
        chat_ops_release(chat_id)
        Disconnect(si)


def _restore_card_job(context: CallbackContext) -> None:
    data = context.job.context
    _restore_card(context.bot, data["chat_id"], data["message_id"], data["vcidx"], data["vm_name"])


def _cb_pre(update: Update, context: CallbackContext) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if uid:
        set_search_await(update.effective_chat.id, uid, False)


def _send_main_menu(update: Update, context: CallbackContext, allowed: bool) -> None:
    chat = update.effective_chat
    if not chat:
        return
    if allowed:
        text = "✅ Привет! Это бот управления vCenter.\nВыберите действие кнопками ниже."
    else:
        text = (
            "✅ Привет! Это бот управления vCenter.\n"
            "Нажмите «ℹ️ Мои права», чтобы узнать свой ID и запросить доступ."
        )
    try:
        context.bot.send_message(chat_id=chat.id, text=text, reply_markup=command_keyboard(allowed))
    except Exception:
        pass


@restricted
@rate_limited
def cb_noop(update: Update, context: CallbackContext):
    _cb_pre(update, context)
    update.callback_query.answer("…")


@restricted
@rate_limited
def cb_choose_vc(update: Update, context: CallbackContext):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, idx_s = q.data.split(":", 1)
        idx = int(idx_s)
    except Exception:
        q.answer("Некорректные данные.", show_alert=True)
        return
    SELECTED_VC[q.message.chat.id] = idx
    q.answer(f"Выбрано: {VCENTERS[idx]['name']}")
    safe_edit_message(q, f"✅ Выбран vCenter: {VCENTERS[idx]['name']}")


@restricted
@rate_limited
def cb_info(update: Update, context: CallbackContext):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, idx_s, vm_name = q.data.split(":", 2)
        vcidx = int(idx_s)
    except Exception:
        q.answer("Некорректные данные кнопки.", show_alert=True)
        return
    q.answer()

    si = vcenter_connect(vcidx)
    try:
        content = si.RetrieveContent()
        vm = find_vm(content, vm_name)
        if not vm:
            safe_edit_message(q, f"• {vm_name} (недоступна)")
            return

        annotation = (vm.config.annotation or "").strip() if vm.config else ""
        if not annotation:
            annotation = "—"

        base = build_base_text(vm, vcidx)
        info = (
            f"\n\n🔍 Информация о {vm.name}\n"
            f"Состояние: {ru_power_state(vm.runtime.powerState)}\n"
            f"CPU: {vm.config.hardware.numCPU} vCPU, RAM: {vm.config.hardware.memoryMB} MB\n"
            f"Гостевая ОС: {vm.config.guestFullName}\n"
            f"Описание: {annotation}\n"
            f"IP: {getattr(vm.guest, 'ipAddress', 'n/a')}"
        )
        safe_edit_message(q, base + info, reply_markup=vm_action_keyboard(vm, vcidx))

        context.job_queue.run_once(
            _restore_card_job,
            when=INFO_TTL,
            context={
                "chat_id": q.message.chat_id,
                "message_id": q.message.message_id,
                "vm_name": vm.name,
                "vcidx": vcidx,
            },
        )
    finally:
        Disconnect(si)


@restricted
@rate_limited
def cb_reboot(update: Update, context: CallbackContext):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, idx_s, vm_name = q.data.split(":", 2)
        vcidx = int(idx_s)
    except Exception:
        q.answer("Некорректные данные кнопки.", show_alert=True)
        return

    if CONFIRM_REBOOT:
        _present_confirm(q, vcidx, vm_name, "reboot", "перезагрузить", context.job_queue)
        return

    q.answer("Выполняю…")
    _do_reboot_action(q, context, vcidx, vm_name)


@restricted
@rate_limited
def cb_power(update: Update, context: CallbackContext):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, idx_s, vm_name, action = q.data.split(":", 3)
        vcidx = int(idx_s)
    except Exception:
        q.answer("Некорректные данные кнопки.", show_alert=True)
        return

    need_confirm = (action == "on" and CONFIRM_ON) or (action == "off" and CONFIRM_OFF)
    if need_confirm:
        _present_confirm(q, vcidx, vm_name, action, "включить" if action == "on" else "выключить", context.job_queue)
        return

    q.answer("Выполняю…")
    _do_power_action(q, context, vcidx, vm_name, action)


@restricted
@rate_limited
def cb_confirm_ok(update: Update, context: CallbackContext):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, nonce = q.data.split(":", 1)
    except Exception:
        q.answer("Некорректные данные.", show_alert=True)
        return

    with PC_GUARD:
        data = PENDING_CONFIRMS.pop(nonce, None)
    if not data:
        q.answer("Подтверждение устарело.", show_alert=True)
        return
    if q.from_user.id != data["user_id"]:
        q.answer("Подтвердить может только инициатор.", show_alert=True)
        return
    if time.time() > data["expires"]:
        q.answer("Время подтверждения истекло.", show_alert=True)
        return

    q.answer("Выполняю…")
    try:
        _restore_card(context.bot, data["chat_id"], data["msg_id"], data["vcidx"], data["vm_name"])
    except Exception:
        pass
    if data["action"] == "reboot":
        _do_reboot_action(q, context, data["vcidx"], data["vm_name"])
    else:
        _do_power_action(q, context, data["vcidx"], data["vm_name"], data["action"])


@restricted
@rate_limited
def cb_confirm_cancel(update: Update, context: CallbackContext):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, nonce = q.data.split(":", 1)
    except Exception:
        q.answer("Некорректные данные.", show_alert=True)
        return

    with PC_GUARD:
        data = PENDING_CONFIRMS.pop(nonce, None)
    if not data:
        q.answer("Уже отменено/истекло.")
        return

    q.answer("Отменено.")
    try:
        _restore_card(context.bot, data["chat_id"], data["msg_id"], data["vcidx"], data["vm_name"])
    except Exception:
        pass


@private_only
def start(update: Update, context: CallbackContext):
    uid = update.effective_user.id if update.effective_user else None
    allowed = bool(uid and uid in ALLOWED_USERS)
    set_menu_for_chat(context.bot, update.effective_chat.id, allowed=allowed)
    set_search_await(update.effective_chat.id, uid, False)
    context.user_data["menu_started"] = True
    message = update.effective_message
    if message:
        chat_id = message.chat_id
        try:
            message.delete()
        except Exception:
            pass
        try:
            context.bot.send_message(chat_id=chat_id, text="Начать")
        except Exception:
            pass
    _send_main_menu(update, context, allowed)


@restricted
@rate_limited
def listvc(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    set_search_await(update.effective_chat.id, uid, False)
    update.message.reply_text("Выберите vCenter:", reply_markup=choose_vc_keyboard())


@restricted
@rate_limited
def listvm(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    set_search_await(chat_id, uid, False)

    if chat_id not in SELECTED_VC:
        update.message.reply_text(
            "❗ Сначала выберите vCenter — нажмите кнопку «📡 Выбрать vCenter».",
            reply_markup=command_keyboard(True),
        )
        return

    vcidx = SELECTED_VC[chat_id]
    si = vcenter_connect(vcidx)
    try:
        content = si.RetrieveContent()
        vms = get_all_vms(content)
        if not vms:
            update.message.reply_text(f"💻 Список ВМ ({VCENTERS[vcidx]['name']}): пуст.")
            return
        update.message.reply_text(f"💻 Список ВМ ({VCENTERS[vcidx]['name']}):")
        for vm in vms:
            update.message.reply_text(build_base_text(vm, vcidx), reply_markup=vm_action_keyboard(vm, vcidx))
    finally:
        Disconnect(si)


@private_only
def whoami(update: Update, context: CallbackContext):
    uid = update.effective_user.id if update.effective_user else None
    set_menu_for_chat(context.bot, update.effective_chat.id, allowed=bool(uid and uid in ALLOWED_USERS))
    set_search_await(update.effective_chat.id, uid, False)
    update.message.reply_text(whoami_text(uid))


@restricted
@rate_limited
def searchvm(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    if context.args:
        query = " ".join(context.args).strip()
        if not query:
            update.message.reply_text("Использование: /searchvm <часть_имени_vm>")
            return
        _perform_search_all_vc(update, query)
        set_search_await(chat_id, uid, False)
        return

    set_search_await(chat_id, uid, True)
    update.message.reply_text(f"🔎 Введите имя ВМ (или часть имени) следующим сообщением.\nОжидание: {SEARCH_WAIT_TTL} сек.")

    def _search_timeout_job(context_job: CallbackContext) -> None:
        data = context_job.job.context
        ch, user = data["chat_id"], data["user_id"]
        if consume_search_timeout(ch, user):
            try:
                context_job.bot.send_message(
                    chat_id=ch,
                    text="⏱ Время ожидания истекло. Повторите действие кнопкой «🔍 Поиск ВМ».",
                    reply_markup=command_keyboard(user in ALLOWED_USERS),
                )
            except Exception:
                pass

    context.job_queue.run_once(_search_timeout_job, when=SEARCH_WAIT_TTL, context={"chat_id": chat_id, "user_id": uid})


@private_only
def any_command_handler(update: Update, context: CallbackContext):
    uid = update.effective_user.id if update.effective_user else None
    if uid not in ALLOWED_USERS:
        _deny(update, "⛔ У вас нет прав на использование этого бота.\nСвяжитесь с администратором для получения доступа.")


@private_only
def any_text_handler(update: Update, context: CallbackContext):
    uid = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    set_menu_for_chat(context.bot, chat_id, allowed=bool(uid and uid in ALLOWED_USERS))
    if uid not in ALLOWED_USERS:
        update.message.reply_text(
            "⛔ У вас нет прав на использование этого бота.\nСвяжитесь с администратором для получения доступа.",
            reply_markup=command_keyboard(False),
        )
        if text == "ℹ️ Мой доступ":
            context.args = []
            whoami(update, context)
        return

    context.user_data.setdefault("menu_started", True)
    button = text
    if button == "📡 Выбрать vCenter":
        context.args = []
        listvc(update, context)
        return
    if button == "💻 Список ВМ":
        context.args = []
        listvm(update, context)
        return
    if button == "🔍 Поиск ВМ":
        context.args = []
        searchvm(update, context)
        return
    if button == "ℹ️ Мой доступ":
        context.args = []
        whoami(update, context)
        return
    if not text or text.startswith("/"):
        return
    if is_search_await(chat_id, uid):
        update.message.reply_text(f"🔎 Ищу «{text}» во всех vCenter…")
        set_search_await(chat_id, uid, False)
        _perform_search_all_vc(update, text)
        return

    update.message.reply_text(
        "Я понимаю только команды. Используйте кнопки ниже.",
        reply_markup=command_keyboard(True),
    )
