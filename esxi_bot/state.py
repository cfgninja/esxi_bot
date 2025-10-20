"""Runtime state and synchronization utilities."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Dict, Tuple

from .config import MAX_ACTIVE_OPS, RATE_LIMIT_SECONDS, SEARCH_WAIT_TTL

__all__ = [
    "VM_LOCKS",
    "vm_lock",
    "throttle",
    "chat_ops_try_acquire",
    "chat_ops_release",
    "set_search_await",
    "is_search_await",
    "new_nonce",
    "PENDING_CONFIRMS",
    "PC_GUARD",
    "SELECTED_VC",
]


VM_LOCKS: Dict[Tuple[int, str], threading.Lock] = {}
VM_LOCKS_GUARD = threading.Lock()
LAST_BY_USER: Dict[int, float] = {}
CHAT_ACTIVE_OPS: Dict[int, int] = {}
CHAT_OPS_GUARD = threading.Lock()
SEARCH_AWAIT: Dict[Tuple[int, int], float] = {}
SEARCH_AWAIT_GUARD = threading.Lock()
PENDING_CONFIRMS: Dict[str, Dict[str, int | float]] = {}
PC_GUARD = threading.Lock()
SELECTED_VC: Dict[int, int] = {}


def vm_lock(vcidx: int, name: str) -> threading.Lock:
    key = (vcidx, name)
    with VM_LOCKS_GUARD:
        return VM_LOCKS.setdefault(key, threading.Lock())


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


def chat_ops_release(chat_id: int) -> None:
    with CHAT_OPS_GUARD:
        CHAT_ACTIVE_OPS[chat_id] = max(0, CHAT_ACTIVE_OPS.get(chat_id, 0) - 1)


def set_search_await(chat_id: int, user_id: int, enabled: bool, ttl: int = SEARCH_WAIT_TTL) -> None:
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


def new_nonce() -> str:
    return uuid.uuid4().hex[:10]
