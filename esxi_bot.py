import ssl, os, time, threading, re, logging, fcntl, sys, uuid

from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    BotCommandScopeChat,
)
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
from dotenv import load_dotenv

# ---------- SINGLE INSTANCE LOCK ----------
LOCK_PATH = "/var/run/esxi_bot.lock"
try:
    _lock_file = open(LOCK_PATH, "w")
    fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_file.write(str(os.getpid()))
    _lock_file.flush()
except OSError:
    sys.stderr.write("Another esxi_bot instance is already running. Exiting.\n")
    sys.exit(1)
# ------------------------------------------

# ---------- LOGGING ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("esxi_bot")
# -----------------------------

# ========== CONFIG ==========
load_dotenv()

BOT_TOKEN        = os.getenv("BOT_TOKEN")
ALLOWED_USERS    = set(map(int, filter(None, os.getenv("ALLOWED_USERS", "").split(","))))
ALLOWED_VMS      = set(filter(None, os.getenv("ALLOWED_VMS", "").split(",")))
DENY_VMS         = set(filter(None, os.getenv("DENY_VMS", "").split(",")))  # опционально

# Таймауты/лимиты
POWER_TIMEOUT_SOFT = int(os.getenv("POWER_TIMEOUT_SOFT", "90"))
POWER_TIMEOUT_HARD = int(os.getenv("POWER_TIMEOUT_HARD", "45"))
POWER_TIMEOUT_ON   = int(os.getenv("POWER_TIMEOUT_ON",   "60"))
INFO_TTL           = int(os.getenv("INFO_TTL", "90"))
SUCCESS_TTL        = int(os.getenv("SUCCESS_TTL", "2"))
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "0.5"))
MAX_ACTIVE_OPS     = int(os.getenv("MAX_ACTIVE_OPS", "10"))
SEARCH_WAIT_TTL    = int(os.getenv("SEARCH_WAIT_TTL", "60"))

# Подтверждения действий
CONFIRM_ON      = int(os.getenv("CONFIRM_ON", "0")) == 1
CONFIRM_OFF     = int(os.getenv("CONFIRM_OFF", "1")) == 1
CONFIRM_REBOOT  = int(os.getenv("CONFIRM_REBOOT", "1")) == 1
CONFIRM_TTL     = int(os.getenv("CONFIRM_TTL", "15"))

# ----------- MULTI vCenter -----------
VCENTERS = []
VC_COUNT = int(os.getenv("VC_COUNT", "1"))
for i in range(1, VC_COUNT + 1):
    host = os.getenv(f"VC{i}_HOST")
    user = os.getenv(f"VC{i}_USER")
    pwd  = os.getenv(f"VC{i}_PASS")
    name = os.getenv(f"VC{i}_NAME", f"vCenter-{i}")
    caf  = os.getenv(f"VC{i}_CA_FILE", "")
    if host and user and pwd:
        VCENTERS.append({"name": name, "host": host, "user": user, "pass": pwd, "cafile": caf})
if not VCENTERS:
    raise SystemExit("No vCenters configured. Please set VC_COUNT and VC*_HOST/USER/PASS in .env")
SELECTED_VC = {}  # chat_id -> vc index
# -------------------------------------

# ========== Locks / Throttle ==========
VM_LOCKS = {}
VM_LOCKS_GUARD = threading.Lock()
LAST_BY_USER = {}
CHAT_ACTIVE_OPS = {}
CHAT_OPS_GUARD = threading.Lock()
SEARCH_AWAIT = {}
SEARCH_AWAIT_GUARD = threading.Lock()

def vm_lock(vcidx: int, name: str) -> threading.Lock:
    key = (vcidx, name)
    with VM_LOCKS_GUARD:
        VM_LOCKS.setdefault(key, threading.Lock())
        return VM_LOCKS[key]

def throttle(user_id: int) -> bool:
    now = time.time()
    last = LAST_BY_USER.get(user_id, 0.0)
    if now - last < RATE_LIMIT_SECONDS:
        return False
    LAST_BY_USER[user_id] = now
    return True

def chat_ops_try_acquire(chat_id: int) -> bool:
    with CHAT_OPS_GUARD:
        cur = CHAT_ACTIVE_OPS.get(chat_id, 0)
        if cur >= MAX_ACTIVE_OPS:
            return False
        CHAT_ACTIVE_OPS[chat_id] = cur + 1
        return True

def chat_ops_release(chat_id: int):
    with CHAT_OPS_GUARD:
        cur = CHAT_ACTIVE_OPS.get(chat_id, 0)
        CHAT_ACTIVE_OPS[chat_id] = max(0, cur - 1)

def set_search_await(chat_id: int, user_id: int, enabled: bool, ttl: int = SEARCH_WAIT_TTL):
    key = (chat_id, user_id)
    with SEARCH_AWAIT_GUARD:
        if enabled:
            SEARCH_AWAIT[key] = time.time() + ttl
        else:
            SEARCH_AWAIT.pop(key, None)

def is_search_await(chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    with SEARCH_AWAIT_GUARD:
        exp = SEARCH_AWAIT.get(key)
        if not exp:
            return False
        if time.time() > exp:
            SEARCH_AWAIT.pop(key, None)
            return False
        return True

# ========== Helpers ==========
def ru_power_state(state: str) -> str:
    s = str(state)
    return "Включен" if s == "poweredOn" else "Выключен" if s == "poweredOff" else s

def vcenter_connect(index=0):
    vc = VCENTERS[index]
    if vc.get("cafile"):
        ctx = ssl.create_default_context(cafile=vc["cafile"])
    else:
        ctx = ssl._create_unverified_context()
    return SmartConnect(host=vc["host"], user=vc["user"], pwd=vc["pass"], sslContext=ctx)

def get_all_vms(content):
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        return sorted(view.view, key=lambda v: v.name.lower())
    finally:
        view.Destroy()

def find_vm(content, name):
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        for vm in view.view:
            if vm.name == name:
                return vm
        return None
    finally:
        view.Destroy()

def refresh_power_state(vm):
    try:
        vm.UpdateViewData(['runtime.powerState'])
    except Exception:
        pass
    return str(vm.runtime.powerState)

def wait_power_state(vm, desired: str, timeout: int, poll: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if refresh_power_state(vm) == desired:
            return True
        time.sleep(poll)
    return False

def build_base_text(vm, vcidx: int | None = None):
    suffix = f" — {VCENTERS[vcidx]['name']}" if vcidx is not None else ""
    return f"• {vm.name} ({ru_power_state(vm.runtime.powerState)}){suffix}"

def vm_action_keyboard(vm, vcidx: int):
    state = str(vm.runtime.powerState)
    rows = []
    manage_allowed = (not DENY_VMS or vm.name not in DENY_VMS) and (not ALLOWED_VMS or vm.name in ALLOWED_VMS)
    if manage_allowed:
        if state == "poweredOn":
            rows.append([InlineKeyboardButton("⏻ Выключить", callback_data=f"power:{vcidx}:{vm.name}:off")])
        else:
            rows.append([InlineKeyboardButton("⚡ Включить",  callback_data=f"power:{vcidx}:{vm.name}:on")])
        rows.append([
            InlineKeyboardButton("🔄 Перезагрузить", callback_data=f"reboot:{vcidx}:{vm.name}"),
            InlineKeyboardButton("ℹ️ Информация",   callback_data=f"info:{vcidx}:{vm.name}")
        ])
    else:
        rows.append([InlineKeyboardButton("ℹ️ Информация", callback_data=f"info:{vcidx}:{vm.name}")])
    return InlineKeyboardMarkup(rows)

def choose_vc_keyboard():
    rows = [[InlineKeyboardButton(f"{i+1}. {vc['name']} ({vc['host']})", callback_data=f"choosevc:{i}")]
            for i, vc in enumerate(VCENTERS)]
    return InlineKeyboardMarkup(rows)

def busy_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏳ Выполняется…", callback_data="noop")]])

def is_private_chat(update) -> bool:
    chat = update.effective_chat
    return bool(chat and getattr(chat, "type", "") == "private")

def _deny(update, reason: str):
    q = getattr(update, "callback_query", None)
    if q:
        try: q.answer(reason, show_alert=True)
        except Exception: pass
        try: q.message.reply_text(reason)
        except Exception: pass
    else:
        em = update.effective_message
        if em:
            em.reply_text(reason)

def whoami_text(user_id: int) -> str:
    allowed = "да" if user_id in ALLOWED_USERS else "нет"
    return f"👤 Ваш Telegram ID: {user_id}\nДоступ к боту (allowed): {allowed}"

def set_menu_for_chat(bot, chat_id: int, allowed: bool):
    if allowed:
        cmds = [
            BotCommand("start",   "Начать"),
            BotCommand("listvc",  "Выбрать vCenter"),
            BotCommand("listvm",  "Список ВМ выбранного vCenter"),
            BotCommand("searchvm","Поиск ВМ во всех vCenter"),
            BotCommand("whoami",  "Показать ваш ID и права"),
        ]
    else:
        cmds = [BotCommand("start", "Начать"), BotCommand("whoami", "Показать ваш ID и права")]
    try:
        bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id))
    except Exception:
        pass

# ---------- SEARCH over all vCenters ----------
def _perform_search_all_vc(update, query: str):
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
        except Exception as e:
            update.message.reply_text(f"❌ Ошибка поиска на {vc['name']}: {e}")
    if total == 0:
        update.message.reply_text(f"Ничего не найдено по «{query}».")

# ========== Access / Scope decorators ==========
def private_only(func):
    def wrapper(update, context):
        if not is_private_chat(update):
            _deny(update, "⛔ Этот бот работает только в личных чатах. Напишите ему напрямую.")
            return
        return func(update, context)
    return wrapper

def restricted(func):
    def wrapper(update, context):
        if not is_private_chat(update):
            _deny(update, "⛔ Этот бот работает только в личных чатах. Напишите ему напрямую.")
            return
        user = update.effective_user
        if not user or user.id not in ALLOWED_USERS:
            set_menu_for_chat(context.bot, update.effective_chat.id, allowed=False)
            _deny(update, "⛔ У вас нет прав на использование этого бота.\nСвяжитесь с администратором для получения доступа.")
            return
        set_menu_for_chat(context.bot, update.effective_chat.id, allowed=True)
        return func(update, context)
    return wrapper

def rate_limited(func):
    def wrapper(update, context):
        user = update.effective_user
        if user and not throttle(user.id):
            q = getattr(update, "callback_query", None)
            if q:
                q.answer("Чуть медленнее 🙂", show_alert=False)
            else:
                em = update.effective_message
                if em:
                    em.reply_text("Чуть медленнее 🙂")
            return
        return func(update, context)
    return wrapper

# ========== Подтверждения ==========
PENDING_CONFIRMS = {}
PC_GUARD = threading.Lock()

def _new_nonce() -> str:
    return uuid.uuid4().hex[:10]

def _confirm_keyboard(nonce: str, action_text: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Подтвердить: {action_text}", callback_data=f"cOK:{nonce}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cNO:{nonce}")]
    ])

def _restore_card(bot, chat_id, msg_id, vcidx, vm_name):
    si = None
    try:
        si = vcenter_connect(vcidx)
        content = si.RetrieveContent()
        vm = find_vm(content, vm_name)
        if vm:
            bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=build_base_text(vm, vcidx),
                reply_markup=vm_action_keyboard(vm, vcidx)
            )
    finally:
        if si: Disconnect(si)

def _present_confirm(q, vcidx: int, vm_name: str, action: str, action_text: str, jq):
    nonce = _new_nonce()
    with PC_GUARD:
        PENDING_CONFIRMS[nonce] = {
            "chat_id": q.message.chat_id,
            "msg_id":  q.message.message_id,
            "user_id": q.from_user.id,
            "vcidx":   vcidx,
            "vm_name": vm_name,
            "action":  action,   # 'on' | 'off' | 'reboot'
            "expires": time.time() + CONFIRM_TTL
        }
    q.edit_message_text(
        f"• {vm_name} — подтвердите действие: {action_text}\n⏳ Истечёт через {CONFIRM_TTL} сек.",
        reply_markup=_confirm_keyboard(nonce, action_text)
    )

    def _expire(context):
        with PC_GUARD:
            data = PENDING_CONFIRMS.pop(nonce, None)
        if not data:
            return
        try:
            _restore_card(context.bot, data["chat_id"], data["msg_id"], data["vcidx"], data["vm_name"])
        except Exception:
            pass

    jq.run_once(_expire, when=CONFIRM_TTL)

# ====== Внутренние функции выполнения операций (используются и Confirm-OK, и обычными хендлерами) ======
def _do_power_action(q, context, vcidx: int, vm_name: str, action: str):
    chat_id = q.message.chat_id
    lock = vm_lock(vcidx, vm_name)
    if not lock.acquire(blocking=False):
        q.answer("⏳ По этой ВМ уже выполняется действие.", show_alert=True); return
    if not chat_ops_try_acquire(chat_id):
        lock.release()
        q.answer("Слишком много операций сразу. Подождите и попробуйте ещё раз.", show_alert=True); return

    si = vcenter_connect(vcidx)
    try:
        content = si.RetrieveContent()
        vm = find_vm(content, vm_name)
        if not vm:
            q.edit_message_text(f"• {vm_name} (недоступна)"); return

        base = build_base_text(vm, vcidx)
        q.edit_message_text(base + f"\n\n⏳ {'Включаю' if action=='on' else 'Выключаю'} {vm_name}…",
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
            q.edit_message_text(base + f"\n\n✅ {vm_name}: выполнено ({path}).",
                                reply_markup=vm_action_keyboard(vm, vcidx))
            context.job_queue.run_once(_restore_card_job, when=SUCCESS_TTL,
                context={"chat_id": q.message.chat_id, "message_id": q.message.message_id,
                         "vm_name": vm.name, "vcidx": vcidx})
        else:
            q.edit_message_text(base + f"\n\n❌ {vm_name}: не удалось подтвердить результат. Проверьте в vCenter.",
                                reply_markup=vm_action_keyboard(vm, vcidx))
    finally:
        try: lock.release()
        except Exception: pass
        chat_ops_release(chat_id)
        Disconnect(si)

def _do_reboot_action(q, context, vcidx: int, vm_name: str):
    chat_id = q.message.chat_id
    lock = vm_lock(vcidx, vm_name)
    if not lock.acquire(blocking=False):
        q.answer("⏳ По этой ВМ уже выполняется действие.", show_alert=True); return
    if not chat_ops_try_acquire(chat_id):
        lock.release()
        q.answer("Слишком много операций сразу. Подождите и попробуйте ещё раз.", show_alert=True); return

    si = vcenter_connect(vcidx)
    try:
        content = si.RetrieveContent()
        vm = find_vm(content, vm_name)
        if not vm:
            q.edit_message_text(f"• {vm_name} (недоступна)"); return

        base = build_base_text(vm, vcidx)
        q.edit_message_text(base + f"\n\n⏳ Перезагружаю {vm_name}…", reply_markup=busy_keyboard())

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
                result = False; path = "-"

        base = build_base_text(vm, vcidx)
        if result:
            q.edit_message_text(base + f"\n\n✅ {vm_name}: перезагрузка выполнена ({path}).",
                                reply_markup=vm_action_keyboard(vm, vcidx))
            context.job_queue.run_once(_restore_card_job, when=SUCCESS_TTL,
                context={"chat_id": q.message.chat_id, "message_id": q.message.message_id,
                         "vm_name": vm.name, "vcidx": vcidx})
        else:
            q.edit_message_text(base + f"\n\n❌ {vm_name}: не удалось подтвердить перезагрузку. Проверьте в vCenter.",
                                reply_markup=vm_action_keyboard(vm, vcidx))
    finally:
        try: lock.release()
        except Exception: pass
        chat_ops_release(chat_id)
        Disconnect(si)

# ========== JobQueue helper ==========
def _restore_card_job(context):
    data = context.job.context
    _restore_card(context.bot, data["chat_id"], data["message_id"], data["vcidx"], data["vm_name"])

# ========== Inline callbacks ==========
def _cb_pre(update, context):
    uid = update.effective_user.id if update.effective_user else None
    if uid:
        set_search_await(update.effective_chat.id, uid, False)

@restricted
@rate_limited
def cb_noop(update, context):
    _cb_pre(update, context)
    update.callback_query.answer("…")

@restricted
@rate_limited
def cb_choose_vc(update, context):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, idx_s = q.data.split(":", 1)
        idx = int(idx_s)
    except Exception:
        q.answer("Некорректные данные.", show_alert=True); return
    SELECTED_VC[q.message.chat.id] = idx
    q.answer(f"Выбрано: {VCENTERS[idx]['name']}")
    q.edit_message_text(f"✅ Выбран vCenter: {VCENTERS[idx]['name']}")

@restricted
@rate_limited
def cb_info(update, context):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, idx_s, vm_name = q.data.split(":", 2)
        vcidx = int(idx_s)
    except Exception:
        q.answer("Некорректные данные кнопки.", show_alert=True); return
    q.answer()

    si = vcenter_connect(vcidx)
    try:
        content = si.RetrieveContent()
        vm = find_vm(content, vm_name)
        if not vm: q.edit_message_text(f"• {vm_name} (недоступна)"); return

        annotation = (vm.config.annotation or "").strip() if vm.config else ""
        if not annotation: annotation = "—"

        base = build_base_text(vm, vcidx)
        info = (f"\n\n🔍 Информация о {vm.name}\n"
                f"Состояние: {ru_power_state(vm.runtime.powerState)}\n"
                f"CPU: {vm.config.hardware.numCPU} vCPU, RAM: {vm.config.hardware.memoryMB} MB\n"
                f"Гостевая ОС: {vm.config.guestFullName}\n"
                f"Описание: {annotation}\n"
                f"IP: {getattr(vm.guest, 'ipAddress', 'n/a')}")
        q.edit_message_text(base + info, reply_markup=vm_action_keyboard(vm, vcidx))

        context.job_queue.run_once(_restore_card_job, when=INFO_TTL,
            context={"chat_id": q.message.chat_id, "message_id": q.message.message_id,
                     "vm_name": vm.name, "vcidx": vcidx})
    finally:
        Disconnect(si)

@restricted
@rate_limited
def cb_reboot(update, context):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, idx_s, vm_name = q.data.split(":", 2)
        vcidx = int(idx_s)
    except Exception:
        q.answer("Некорректные данные кнопки.", show_alert=True); return

    if CONFIRM_REBOOT:
        _present_confirm(q, vcidx, vm_name, "reboot", "перезагрузить", context.job_queue)
        return

    q.answer("Выполняю…")
    _do_reboot_action(q, context, vcidx, vm_name)

@restricted
@rate_limited
def cb_power(update, context):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, idx_s, vm_name, action = q.data.split(":", 3)
        vcidx = int(idx_s)
    except Exception:
        q.answer("Некорректные данные кнопки.", show_alert=True); return

    need_confirm = ((action == "on" and CONFIRM_ON) or (action == "off" and CONFIRM_OFF))
    if need_confirm:
        _present_confirm(q, vcidx, vm_name, action, "включить" if action == "on" else "выключить", context.job_queue)
        return

    q.answer("Выполняю…")
    _do_power_action(q, context, vcidx, vm_name, action)

# --- подтверждение ---
@restricted
@rate_limited
def cb_confirm_ok(update, context):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, nonce = q.data.split(":", 1)
    except Exception:
        q.answer("Некорректные данные.", show_alert=True); return

    with PC_GUARD:
        data = PENDING_CONFIRMS.pop(nonce, None)
    if not data:
        q.answer("Подтверждение устарело.", show_alert=True); return
    if q.from_user.id != data["user_id"]:
        q.answer("Подтвердить может только инициатор.", show_alert=True); return
    if time.time() > data["expires"]:
        q.answer("Время подтверждения истекло.", show_alert=True); return

    q.answer("Выполняю…")
    # сразу показать «⏳»
    try:
        _restore_card(context.bot, data["chat_id"], data["msg_id"], data["vcidx"], data["vm_name"])
    except Exception:
        pass
    # выполнить действие
    if data["action"] == "reboot":
        _do_reboot_action(q, context, data["vcidx"], data["vm_name"])
    else:
        _do_power_action(q, context, data["vcidx"], data["vm_name"], data["action"])

@restricted
@rate_limited
def cb_confirm_cancel(update, context):
    _cb_pre(update, context)
    q = update.callback_query
    try:
        _, nonce = q.data.split(":", 1)
    except Exception:
        q.answer("Некорректные данные.", show_alert=True); return

    with PC_GUARD:
        data = PENDING_CONFIRMS.pop(nonce, None)
    if not data:
        q.answer("Уже отменено/истекло."); return

    q.answer("Отменено.")
    try:
        _restore_card(context.bot, data["chat_id"], data["msg_id"], data["vcidx"], data["vm_name"])
    except Exception:
        pass

# ========== Commands ==========
@private_only
def start(update, context):
    uid = update.effective_user.id if update.effective_user else None
    allowed = uid in ALLOWED_USERS
    set_menu_for_chat(context.bot, update.effective_chat.id, allowed=allowed)
    set_search_await(update.effective_chat.id, uid, False)
    text_allowed = (
        "✅ Привет! Это бот управления vCenter.\n"
        "• /listvc — выбрать vCenter.\n"
        "• /listvm — список ВМ выбранного vCenter.\n"
        "• /searchvm — поиск ВМ по всем vCenter.\n"
        "• /whoami — показать ваш ID и права."
    )
    text_denied = "✅ Привет! Это бот управления vCenter.\n• /whoami — показать ваш ID и права."
    update.message.reply_text(text_allowed if allowed else text_denied, reply_markup=ReplyKeyboardRemove())

@restricted
@rate_limited
def listvc(update, context):
    uid = update.effective_user.id
    set_search_await(update.effective_chat.id, uid, False)
    update.message.reply_text("Выберите vCenter:", reply_markup=choose_vc_keyboard())

@restricted
@rate_limited
def listvm(update, context):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    set_search_await(chat_id, uid, False)

    if chat_id not in SELECTED_VC:
        update.message.reply_text("❗ Сначала выберите vCenter командой /listvc.")
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
def whoami(update, context):
    uid = update.effective_user.id if update.effective_user else None
    set_menu_for_chat(context.bot, update.effective_chat.id, allowed=(uid in ALLOWED_USERS))
    set_search_await(update.effective_chat.id, uid, False)
    update.message.reply_text(whoami_text(uid))

@restricted
@rate_limited
def searchvm(update, context):
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

    def _search_timeout_job(context_job):
        data = context_job.job.context
        ch, u = data["chat_id"], data["user_id"]
        if is_search_await(ch, u):
            set_search_await(ch, u, False)
            try:
                context_job.bot.send_message(chat_id=ch, text="⏱ Время ожидания истекло. Повторите команду /searchvm.")
            except Exception:
                pass

    context.job_queue.run_once(_search_timeout_job, when=SEARCH_WAIT_TTL, context={"chat_id": chat_id, "user_id": uid})

# ========== Message handlers ==========
@private_only
def any_command_handler(update, context):
    uid = update.effective_user.id if update.effective_user else None
    if uid not in ALLOWED_USERS:
        _deny(update, "⛔ У вас нет прав на использование этого бота.\nСвяжитесь с администратором для получения доступа.")
        return

@private_only
def any_text_handler(update, context):
    uid = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    set_menu_for_chat(context.bot, chat_id, allowed=(uid in ALLOWED_USERS))
    if uid not in ALLOWED_USERS:
        _deny(update, "⛔ У вас нет прав на использование этого бота.\nСвяжитесь с администратором для получения доступа.")
        return
    if not text or text.startswith("/"):
        return
    if is_search_await(chat_id, uid):
        update.message.reply_text(f"🔎 Ищу «{text}» во всех vCenter…")
        set_search_await(chat_id, uid, False)
        _perform_search_all_vc(update, text)

# ========== Main ==========
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start",   start),     group=1)
    dp.add_handler(CommandHandler("listvc",  listvc),    group=1)
    dp.add_handler(CommandHandler("listvm",  listvm),    group=1)
    dp.add_handler(CommandHandler("whoami",  whoami),    group=1)
    dp.add_handler(CommandHandler("searchvm",searchvm),  group=1)

    dp.add_handler(MessageHandler(Filters.text & (~Filters.command), any_text_handler), group=0)

    dp.add_handler(CallbackQueryHandler(cb_noop,           pattern=r"^noop$"),     group=2)
    dp.add_handler(CallbackQueryHandler(cb_choose_vc,      pattern=r"^choosevc:"), group=2)
    dp.add_handler(CallbackQueryHandler(cb_info,           pattern=r"^info:"),     group=2)
    dp.add_handler(CallbackQueryHandler(cb_reboot,         pattern=r"^reboot:"),   group=2)
    dp.add_handler(CallbackQueryHandler(cb_power,          pattern=r"^power:"),    group=2)
    dp.add_handler(CallbackQueryHandler(cb_confirm_ok,     pattern=r"^cOK:"),      group=2)
    dp.add_handler(CallbackQueryHandler(cb_confirm_cancel,  pattern=r"^cNO:"),      group=2)

    unknown_cmd_filter = Filters.command & ~Filters.regex(r'^/(start|listvc|listvm|whoami|searchvm)(\s|$)')
    dp.add_handler(MessageHandler(unknown_cmd_filter, any_command_handler), group=3)

    try:
        updater.bot.set_my_commands([BotCommand("start","Начать"), BotCommand("whoami","Показать ваш ID и права")])
    except Exception:
        pass

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
