# esxi_bot

Простой Telegram-бот для управления виртуальными машинами через vCenter (pyVmomi).

Quick start

1. Создайте виртуальное окружение и установите зависимости:

```powershell
# PowerShell (Windows)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Скопируйте `.env.template` в `.env` и заполните значения (BOT_TOKEN, VC_COUNT, VC1_* и ALLOWED_USERS).

3. Запустите бота:

```powershell
python esxi_bot.py
```

Особенности

- Конфигурация через `.env`.
- Поддержка нескольких vCenter (настройки `VC{n}_*`).
- Single-instance lock: на Linux использует `/var/run/esxi_bot.lock` + fcntl; на Windows — lock-файл в temp.

Если нужно добавить поддержку systemd/service unit или Docker, могу подготовить пример.
