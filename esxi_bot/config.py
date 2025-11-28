"""Configuration loading for esxi_bot."""

from __future__ import annotations

import os
from typing import List

from dotenv import load_dotenv

load_dotenv()

__all__ = [
    "BOT_TOKEN",
    "ALLOWED_USERS",
    "ALLOWED_VMS",
    "DENY_VMS",
    "POWER_TIMEOUT_SOFT",
    "POWER_TIMEOUT_HARD",
    "POWER_TIMEOUT_ON",
    "INFO_TTL",
    "SUCCESS_TTL",
    "RATE_LIMIT_SECONDS",
    "MAX_ACTIVE_OPS",
    "SEARCH_WAIT_TTL",
    "CONFIRM_ON",
    "CONFIRM_OFF",
    "CONFIRM_REBOOT",
    "CONFIRM_TTL",
    "VCENTERS",
    "VC_COUNT",
    "DEFAULT_LOCK_PATH",
    "AUDIT_CHANNEL_ID",
    "USER_PHONES",
    "VC_EVENT_TYPES",
    "VC_EVENT_POLL_INTERVAL",
    "VC_EVENT_BATCH_SIZE",
    "VC_EVENT_USER_IGNORE",
]


def _split_csv(value: str | None) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


BOT_TOKEN = os.getenv("BOT_TOKEN")

ALLOWED_USERS = {int(x) for x in _split_csv(os.getenv("ALLOWED_USERS"))}
ALLOWED_VMS = set(_split_csv(os.getenv("ALLOWED_VMS")))
DENY_VMS = set(_split_csv(os.getenv("DENY_VMS")))

POWER_TIMEOUT_SOFT = int(os.getenv("POWER_TIMEOUT_SOFT", "90"))
POWER_TIMEOUT_HARD = int(os.getenv("POWER_TIMEOUT_HARD", "45"))
POWER_TIMEOUT_ON = int(os.getenv("POWER_TIMEOUT_ON", "60"))
INFO_TTL = int(os.getenv("INFO_TTL", "90"))
SUCCESS_TTL = int(os.getenv("SUCCESS_TTL", "2"))
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "0.5"))
MAX_ACTIVE_OPS = int(os.getenv("MAX_ACTIVE_OPS", "10"))
SEARCH_WAIT_TTL = int(os.getenv("SEARCH_WAIT_TTL", "60"))

CONFIRM_ON = int(os.getenv("CONFIRM_ON", "0")) == 1
CONFIRM_OFF = int(os.getenv("CONFIRM_OFF", "1")) == 1
CONFIRM_REBOOT = int(os.getenv("CONFIRM_REBOOT", "1")) == 1
CONFIRM_TTL = int(os.getenv("CONFIRM_TTL", "15"))

VC_COUNT = int(os.getenv("VC_COUNT", "1"))
VCENTERS = []
for i in range(1, VC_COUNT + 1):
    host = os.getenv(f"VC{i}_HOST")
    user = os.getenv(f"VC{i}_USER")
    pwd = os.getenv(f"VC{i}_PASS")
    name = os.getenv(f"VC{i}_NAME", f"vCenter-{i}")
    caf = os.getenv(f"VC{i}_CA_FILE", "")
    if host and user and pwd:
        VCENTERS.append({"name": name, "host": host, "user": user, "pass": pwd, "cafile": caf})

if not VCENTERS:
    raise SystemExit("No vCenters configured. Please set VC_COUNT and VC*_HOST/USER/PASS in .env")

DEFAULT_LOCK_PATH = os.getenv("LOCK_PATH", "/var/run/esxi_bot.lock")

_audit_channel_raw = os.getenv("AUDIT_CHANNEL_ID")
AUDIT_CHANNEL_ID = int(_audit_channel_raw) if _audit_channel_raw else None


def _parse_user_phones(value: str | None) -> dict[int, str]:
    phones: dict[int, str] = {}
    if not value:
        return phones
    for item in value.split(","):
        pair = item.strip()
        if not pair:
            continue
        try:
            uid_raw, phone = pair.split(":", 1)
            uid = int(uid_raw.strip())
            phone_clean = phone.strip()
            if phone_clean:
                phones[uid] = phone_clean
        except ValueError:
            continue
    return phones


USER_PHONES = _parse_user_phones(os.getenv("USER_PHONES"))

VC_EVENT_TYPES = tuple(_split_csv(os.getenv("VC_EVENT_TYPES")))
VC_EVENT_POLL_INTERVAL = int(os.getenv("VC_EVENT_POLL_INTERVAL", "10"))
VC_EVENT_BATCH_SIZE = int(os.getenv("VC_EVENT_BATCH_SIZE", "50"))
VC_EVENT_USER_IGNORE = set(_split_csv(os.getenv("VC_EVENT_USER_IGNORE")))
