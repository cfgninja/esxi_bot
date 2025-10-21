# esxi_bot

Telegram-бот для управления виртуальными машинами во vCenter через pyVmomi и python-telegram-bot.

## Требования к VM

- 64-битная ОС: Linux (Ubuntu/Debian/CentOS), Windows Server 2019+ или macOS 12+.
- Python 3.10 или новее; установленный `pip`.
- 1 vCPU и 1 ГиБ RAM для небольших бот-ферм; при активном использовании планируйте 2 vCPU и 2 ГиБ RAM.
- 2 ГиБ свободного диска под логи, виртуальное окружение и зависимости.
- Сетевой доступ: исходящие соединения к `api.telegram.org` (порт 443) и к каждому vCenter (порт 443).
- Синхронизированное время (NTP) — токены Telegram и сертификаты vCenter чувствительны к времени.

## Быстрый старт

### Windows (PowerShell)

```powershell
mkdir C:\Services\esxi_bot
cd C:\Services\esxi_bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
git clone https://github.com/cfgninja/esxi_bot.git .
copy .env.template .env
pip install -r requirements.txt
notepad .env   # заполните BOT_TOKEN, VC_COUNT, VC1_* и ALLOWED_USERS
python -m esxi_bot
```

Для сервиса можно использовать `nssm install esxi_bot "C:\Services\esxi_bot\.venv\Scripts\python.exe" -m esxi_bot` и настроить автозапуск.

### Linux (bash)

```bash
sudo mkdir -p /opt/esxi_bot
sudo chown $(id -u):$(id -g) /opt/esxi_bot
cd /opt/esxi_bot
python3 -m venv .venv
source .venv/bin/activate
git clone https://github.com/cfgninja/esxi_bot.git .
cp .env.template .env
pip install -r requirements.txt
nano .env    # заполните BOT_TOKEN, VC_COUNT, VC1_* и ALLOWED_USERS
python -m esxi_bot
```

Для демона настройте systemd unit с командой `ExecStart=/opt/esxi_bot/.venv/bin/python -m esxi_bot` и `WorkingDirectory=/opt/esxi_bot`.

### macOS (zsh)

```bash
mkdir -p ~/Services/esxi_bot
cd ~/Services/esxi_bot
/usr/bin/python3 -m venv .venv
source .venv/bin/activate
git clone https://github.com/cfgninja/esxi_bot.git .
cp .env.template .env
pip install -r requirements.txt
nano .env    # заполните BOT_TOKEN, VC_COUNT, VC1_* и ALLOWED_USERS
python -m esxi_bot
```

Для фонового запуска используйте launchd или `tmux`/`screen` с той же командой.

## Запуск

- Бот поддерживает два равнозначных способа старта: `python esxi_bot.py` или `python -m esxi_bot`.
- Single-instance lock предотвращает параллельные запуски: на POSIX — через `fcntl` и файл `/var/run/esxi_bot.lock` по умолчанию, на Windows — lock-файл в `%TEMP%`.
- Все настройки подтягиваются из `.env`; при изменении конфигурации перезапустите процесс.

## Рекомендуемая структура каталога на VM

```
/opt/esxi_bot/              # root (Linux/macOS); на Windows используйте C:\Services\esxi_bot
│
├─ .env                     # конфигурация: BOT_TOKEN, VC_COUNT, VC{i}_*, ALLOWED_USERS, таймауты
├─ .env.template            # шаблон для удобства (можно удалить после настройки)
├─ requirements.txt         # зависимости проекта
├─ esxi_bot.py              # совместимый скрипт запуска
├─ esxi_bot/                # пакет с кодом бота
│  ├─ __init__.py
│  ├─ __main__.py
│  ├─ main.py
│  ├─ config.py
│  ├─ handlers.py
│  ├─ telegram_ui.py
│  ├─ vcenter.py
│  ├─ state.py
│  └─ singleton.py
└─ .venv/                   # виртуальное окружение Python (рекомендуется держать рядом с кодом)
```

На Windows путь `.venv\Scripts\python.exe` используйте в планировщике или службе. На Linux/macOS — `/opt/esxi_bot/.venv/bin/python` в systemd/launchd.