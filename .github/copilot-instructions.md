# Copilot / AI agent instructions — esxi_bot

Короткие, конкретные указания для автоматизированных кодовых агентов, работающих с этим репозиторием.

- Проект: Telegram-бот для управления VM во vCenter; код организован как пакет `esxi_bot/` плюс совместимый вход `esxi_bot.py`.
- Язык: Python (3.x). Основные зависимости: `python-telegram-bot`, `pyVmomi`/`pyVim`, `python-dotenv`.

Что важно знать сразу:

- Архитектура разбита по модулам:
  - `esxi_bot/main.py` — точка входа: настраивает логирование, вызывает `singleton.ensure_single_instance()`, поднимает `Updater` и регистрирует обработчики.
  - `esxi_bot/singleton.py` — кроссплатформенный single-instance lock (fcntl на POSIX, lock-файл в `%TEMP%` на Windows);
    очистка lock-файла регистрируется через `atexit`.
  - `esxi_bot/config.py` — загрузка `.env`, сбор конфигурации (включая VCENTERS, таймауты, флаги подтверждения, списки пользователей/ВМ) и значение `DEFAULT_LOCK_PATH`.
  - `esxi_bot/state.py` — runtime-состояние: мьютексы, rate-limit (`throttle`), очереди операций по чатам (`chat_ops_try_acquire` / `chat_ops_release`), ожидание поиска, `PENDING_CONFIRMS`, `SELECTED_VC`, генерация nonce.
  - `esxi_bot/vcenter.py` — функции подключения `vcenter_connect`, перечисление и поиск ВМ, обновление статуса питания (`refresh_power_state`, `wait_power_state`), перевод статусов (`ru_power_state`).
  - `esxi_bot/telegram_ui.py` — построение клавиатур (`vm_action_keyboard`, `choose_vc_keyboard`, `busy_keyboard`, `confirm_keyboard`), текстов (`build_base_text`, `whoami_text`), установка меню команд (`set_menu_for_chat`), безопасный `safe_edit_message` (гасит `BadRequest: Message is not modified`).
  - `esxi_bot/handlers.py` — все декораторы (`private_only`, `restricted`, `rate_limited`) и бизнес-логика хэндлеров / job queue. `_do_power_action` и `_do_reboot_action` дергают `vcenter`-helpers, используют локи из `state` и UI из `telegram_ui`.

- Потоки / синхронизация:
  - Локи и rate-limit в `state.py`; при модификациях импортируйте функции, не дублируйте глобалы.
  - Пул активных операций на чат — `chat_ops_try_acquire`/`chat_ops_release` + `MAX_ACTIVE_OPS` из конфигурации.

- Доступ и безопасность:
  - Разрешённые пользователи — CSV в `.env` (`ALLOWED_USERS` → множество int).
  - Фильтры ВМ: `ALLOWED_VMS`, `DENY_VMS` (имена); проверяются в `telegram_ui.vm_action_keyboard`.
  - `set_menu_for_chat` задаёт команды в scope чата (`BotCommandScopeChat`), поэтому вызывайте его при изменении прав.

Практические примеры / паттерны для изменений:

- Новая кнопка/команда: добавьте клавишу в `telegram_ui.vm_action_keyboard` и соответствующий callback в `handlers`, формат callback_data — `action:vcidx:vmname[:op]`.
- Новая интеграция с vCenter: расширяйте `vcenter.py`, возвращайте объекты/данные, а затем используйте их в `handlers`.
- Работа с JobQueue: используйте `context.job_queue.run_once`, как в `_present_confirm` и `_restore_card_job`; помните, что `safe_edit_message` защищает от повторных редактирований.

Важные ограничения и нюансы:

- Тестов и CI нет — любые изменения проверяйте вручную (py_compile/compileall + запуск бота).
- Бот ориентирован на Linux, но lock теперь кроссплатформенный; если требуются дополнительные элементы запуска под Windows, адаптируйте `singleton.ensure_single_instance`/служебные скрипты.
- Поддержка нескольких vCenter: `VC_COUNT` и пары `VC{i}_*` задают список. Следите за корректным индексом (`vcidx`).

Как запускать (локально):

1. Создайте `.env` (можно скопировать `.env.template`) и заполните минимум:

```
BOT_TOKEN=123:ABC
VC_COUNT=1
VC1_HOST=vc.example.local
VC1_USER=administrator@vsphere.local
VC1_PASS=secret
ALLOWED_USERS=123456789
```

2. Установите зависимости:

```
pip install -r requirements.txt
```

3. Запустите:

```
python esxi_bot.py
```

(Также можно `python -m esxi_bot`.)

Если цель изменения неочевидна (новая команда, багфикс, перенос на Windows), опишите её — подскажу, какие модули зацепить.
