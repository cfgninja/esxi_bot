# Copilot / AI agent instructions — esxi_bot

Короткие, конкретные указания для автоматизированных кодовых агентов, работающих с этим репозиторием.

- Проект: простой single-file Telegram-бот для управления VM в одном или нескольких vCenter; основной файл — `esxi_bot.py`.
- Язык: Python (3.x). Основные зависимости (импортируются в коде): `python-telegram-bot`, `pyVmomi`/`pyVim`, `python-dotenv`.

Что важно знать сразу:

- Архитектура: монолитный сценарий — почти вся логика в `esxi_bot.py`. Компоненты:
  - конфигурация через `.env` (`BOT_TOKEN`, `VC_COUNT`, `VC1_HOST/USER/PASS`, `ALLOWED_USERS`, `ALLOWED_VMS`, `DENY_VMS`, таймауты и флаги подтверждения);
  - подключение к vCenter: функция `vcenter_connect(index)` возвращает объект подключения `SmartConnect` (используется `vc["cafile"]` — если не задано, создаётся unverified SSL context);
  - поиск/перечисление ВМ: `get_all_vms`, `find_vm`;
  - операции питания: `_do_power_action`, `wait_power_state`, `refresh_power_state`;
  - UI в Telegram: клавиатуры строятся в `vm_action_keyboard`, `choose_vc_keyboard`, `busy_keyboard`, подтверждения через `_present_confirm` и `PENDING_CONFIRMS`.

- Потоки / синхронизация:
  - глобальные мьютексы/локи: `VM_LOCKS` (по ключу `(vcidx, vm_name)`), `CHAT_OPS_GUARD`, `SEARCH_AWAIT_GUARD`, `PC_GUARD`.
  - Ограничение параллельных операций по чату: `CHAT_ACTIVE_OPS` + `MAX_ACTIVE_OPS`.
  - Rate-limit на пользователя — `LAST_BY_USER` и функция `throttle(user_id)` с конфигом `RATE_LIMIT_SECONDS`.

- Доступ и безопасность:
  - Доступ по списку Telegram ID в `ALLOWED_USERS` (env содержит CSV, код приводит к int).
  - Дополнительные фильтры VM: `ALLOWED_VMS` и `DENY_VMS` (имена VM).
  - Скрипт использует POSIX-файл-блокировку: `LOCK_PATH = "/var/run/esxi_bot.lock"` и `fcntl` — это важно для запуска на Linux; на Windows lock не сработает (файл `esxi_bot.py` ожидает POSIX).

Практические примеры / паттерны для изменений:

- Добавление новой командной кнопки: посмотрите `vm_action_keyboard` — клавиши добавляются через `InlineKeyboardButton(..., callback_data="action:vcidx:vmname:op")`. Обработчики ожидают callback_data с двоеточиями.
- Новая vCenter-конфигурация: обновите `.env` — увеличьте `VC_COUNT` и добавьте `VC2_HOST`, `VC2_USER`, `VC2_PASS`, `VC2_NAME` (код собирает `VCENTERS` из переменных `VC{i}_*`).
- Асинхронные/долгие операции: код держит соединение `si = vcenter_connect(idx)` и явно вызывает `Disconnect(si)` в `finally` — сохраняйте этот паттерн.

Важные ограничения и нюансы:

- Монолитность: большинство изменений будет происходить в одном файле. Избегайте хрупких глобальных изменений — уважайте существующие глобальные структуры (локи, словари состояния).
- Тестов и CI нет в репозитории — любые изменения нужно вручную запускать и проверять. Инструментальных тестов/фейковых vCenter нет.
- Windows: из коробки скрипт ориентирован на Linux (fcntl, `/var/run`). Для работы на Windows потребуется переписать механизм single-instance lock и проверить поддержку SSL/pyVmomi в окружении.

Как запускать (локально для разработчика):

1) Создать `.env` рядом с `esxi_bot.py` с минимумом переменных:

```
BOT_TOKEN=123:ABC
VC_COUNT=1
VC1_HOST=vc.example.local
VC1_USER=administrator@vsphere.local
VC1_PASS=secret
ALLOWED_USERS=123456789
```

2) Установить зависимости (пример):

```
pip install python-telegram-bot pyvmomi python-dotenv
```

3) Запустить:

```
python esxi_bot.py
```

Ключевые файлы для быстрого просмотра при изменениях:

- `esxi_bot.py` — весь проект; ищите там `VCENTERS`, `vcenter_connect`, `vm_action_keyboard`, `_do_power_action`, декораторы `restricted`, `rate_limited`.

Если что-то неясно, укажите цель изменения (новая команда, багфикс, перенести на Windows) и я подскажу точнее, какие участки править.
