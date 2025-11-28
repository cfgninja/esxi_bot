"""vCenter event listener for audit channel."""

from __future__ import annotations

import html
import logging
import threading
import time
from typing import List, Optional

from pyVmomi import vim

from .config import (
    AUDIT_CHANNEL_ID,
    VCENTERS,
    VC_EVENT_BATCH_SIZE,
    VC_EVENT_POLL_INTERVAL,
    VC_EVENT_TYPES,
    VC_EVENT_USER_IGNORE,
)
from .vcenter import Disconnect, vcenter_connect

log = logging.getLogger(__name__)


class VCEventListener:
    def __init__(self, bot, idx: int):
        self.bot = bot
        self.idx = idx
        self.config = VCENTERS[idx]
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self._last_event_key: Optional[int] = None
        self._ignore_users = {u.lower() for u in VC_EVENT_USER_IGNORE}
        self._ignore_users.add(self.config["user"].lower())

    def start(self) -> None:
        if not AUDIT_CHANNEL_ID or not VC_EVENT_TYPES:
            return
        self.thread = threading.Thread(target=self._run, name=f"vc-events-{self.idx}", daemon=True)
        self.thread.start()
        log.info("Started VC event listener for %s", self.config["name"])

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                si = vcenter_connect(self.idx)
                try:
                    self._loop_events(si)
                finally:
                    try:
                        Disconnect(si)
                    except Exception:
                        pass
            except Exception:
                log.exception("VC event listener connection error for %s", self.config["name"])
                time.sleep(VC_EVENT_POLL_INTERVAL)

    def _loop_events(self, si) -> None:
        event_manager = si.content.eventManager
        if not event_manager:
            log.warning("Event manager unavailable for %s", self.config["name"])
            time.sleep(VC_EVENT_POLL_INTERVAL)
            return

        filter_spec = vim.event.EventFilterSpec()
        filter_spec.eventTypeId = list(VC_EVENT_TYPES)

        collector = event_manager.CreateCollectorForEvents(filter_spec)
        try:
            if self._last_event_key is None:
                latest = collector.latestPage
                if latest:
                    self._last_event_key = latest[-1].key
            while not self.stop_event.is_set():
                events = collector.ReadNextEvents(VC_EVENT_BATCH_SIZE)
                if not events:
                    time.sleep(VC_EVENT_POLL_INTERVAL)
                    continue
                for event in events:
                    if self._last_event_key and event.key <= self._last_event_key:
                        continue
                    self._last_event_key = event.key
                    self._handle_event(event)
        finally:
            try:
                collector.Destroy()
            except Exception:
                pass

    def _handle_event(self, event) -> None:
        user_name = (getattr(event, "userName", "") or "system").strip()
        if user_name.lower() in self._ignore_users:
            return
        vm_name = getattr(getattr(event, "vm", None), "name", "")
        action = getattr(event, "fullFormattedMessage", "") or type(event).__name__
        message = self._format_message(user_name, vm_name, action)
        self._send(message)

    def _format_message(self, user: str, vm_name: str, action: str) -> str:
        parts = [f"🛰️ <b>{html.escape(self.config['name'])}</b>"]
        parts.append(f"👤 <b>{html.escape(user)}</b>")
        if vm_name:
            parts.append(f"ВМ: <b>{html.escape(vm_name)}</b>")
        parts.append(html.escape(action))
        return " — ".join(parts)

    def _send(self, text: str) -> None:
        if not AUDIT_CHANNEL_ID:
            return
        try:
            self.bot.send_message(chat_id=AUDIT_CHANNEL_ID, text=text, parse_mode="HTML")
        except Exception:
            log.exception("Failed to send VC event message for %s", self.config['name'])


def start_listeners(bot) -> List[VCEventListener]:
    listeners: List[VCEventListener] = []
    if not AUDIT_CHANNEL_ID:
        log.info("AUDIT_CHANNEL_ID not configured; skipping VC event listeners")
        return listeners
    if not VC_EVENT_TYPES:
        log.info("VC_EVENT_TYPES not configured; skipping VC event listeners")
        return listeners
    for idx, _ in enumerate(VCENTERS):
        listener = VCEventListener(bot, idx)
        listener.start()
        listeners.append(listener)
    return listeners


def stop_listeners(listeners: List[VCEventListener]) -> None:
    for listener in listeners:
        try:
            listener.stop()
        except Exception:
            log.exception("Failed to stop VC event listener for %s", listener.config['name'])