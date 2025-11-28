"""vCenter event listener for audit channel."""

from __future__ import annotations

import html
import logging
import threading
import time
from typing import List, Optional, Tuple

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

EVENT_VERB_MAP = {
    "VmPoweredOnEvent": "включил",
    "VmPoweredOffEvent": "выключил",
    "VmRebootingEvent": "перезагрузил",
    "VmGuestRebootEvent": "перезагрузил",
    "VmGuestShutdownEvent": "выключил",
    "DrsVmPoweredOnEvent": "включил",
    "VmSuspendedEvent": "приостановил",
    "VmResumedEvent": "возобновил",
    "VmResettingEvent": "перезагрузил",
    "VmMigratedEvent": "переместил",
    "VmRelocatedEvent": "переместил",
    "VmBeingClonedEvent": "клонирует",
    "VmClonedEvent": "клонировал",
    "VmCreatedEvent": "создал",
    "VmRegisteredEvent": "зарегистрировал",
    "VmUnregisteredEvent": "удалил из инвентаря",
    "VmRemovedEvent": "удалил",
    "VmBeingDeployedEvent": "разворачивает",
    "VmDeployedEvent": "развернул",
    "VmBeingSuspendedEvent": "приостанавливает",
}

TASK_VERB_MAP = {
    "VirtualMachine.powerOn": "включил",
    "VirtualMachine.powerOff": "выключил",
    "VirtualMachine.rebootGuest": "перезагрузил",
    "VirtualMachine.reset": "перезагрузил",
    "VirtualMachine.shutdownGuest": "выключил",
    "VirtualMachine.suspend": "приостановил",
    "VirtualMachine.standbyGuest": "перевёл в ожидание",
    "VirtualMachine.createSnapshot": "создал снепшот",
    "VirtualMachine.removeAllSnapshots": "удалил все снепшоты",
    "VirtualMachine.removeSnapshot": "удалил снепшот",
    "VirtualMachine.revertToCurrentSnapshot": "откатил снепшот",
    "VirtualMachine.consolidateDisks": "консолидировал диски",
    "VirtualMachine.relocate": "переместил",
    "VirtualMachine.migrate": "мигрировал",
    "VirtualMachine.clone": "клонировал",
    "VirtualMachine.register": "зарегистрировал",
    "VirtualMachine.unregister": "удалил из инвентаря",
    "VirtualMachine.destroy": "удалил",
    "VirtualMachine.reconfigure": "изменил настройки",
}

TASK_NAME_VERB_MAP = {
    "Power On virtual machine": "включил",
    "Power Off virtual machine": "выключил",
    "Reset virtual machine": "перезагрузил",
    "Reboot Guest OS": "перезагрузил",
    "Shutdown guest OS": "выключил",
    "Suspend virtual machine": "приостановил",
    "Standby Guest OS": "перевёл в ожидание",
    "Create virtual machine snapshot": "создал снепшот",
    "Remove snapshot": "удалил снепшот",
    "Remove all snapshots": "удалил все снепшоты",
    "Revert virtual machine to snapshot": "откатил снепшот",
    "Consolidate virtual machine disks": "консолидировал диски",
    "Relocate virtual machine": "переместил",
    "Migrate virtual machine": "мигрировал",
    "Clone virtual machine": "клонировал",
    "Deploy template": "развернул шаблон",
    "Register virtual machine": "зарегистрировал",
    "Unregister virtual machine": "удалил из инвентаря",
    "Delete virtual machine": "удалил",
    "Destroy virtual machine": "удалил",
    "Reconfigure virtual machine": "изменил настройки",
}

TASK_GENERIC_TRANSLATIONS = {
    "Download patch definitions": "загрузил определения патчей",
    "Scheduled hardware compatibility check": "запланировал проверку аппаратной совместимости",
    "Initialize powering On": "инициализировал включение",
    "Initialize powering Off": "инициализировал выключение",
    "Apply recommendation": "применил рекомендацию",
    "Apply DRS recommendation": "применил рекомендацию DRS",
    "Apply recommendation for cluster": "применил рекомендацию для кластера",
}


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
        user_name = (getattr(event, "userName", "") or "System").strip()
        if user_name.lower() in self._ignore_users:
            return
        details = self._event_details(event)
        if details:
            verb, vm_name = details
            vc_label = f"{self.config['name']} ({self.config['host']})"
            status = "успешно (vCenter)"
            vm_part = html.escape(vm_name) if vm_name else "ресурс"
            text = (
                f"👤 <b>{html.escape(user_name)}</b> {verb} <b>{vm_part}</b> "
                f"({html.escape(vc_label)}) — {status}."
            )
        else:
            generic_text = self._generic_task_text(event, user_name)
            if generic_text:
                text = generic_text
            else:
                raw = getattr(event, "fullFormattedMessage", "") or type(event).__name__
                vm_name = getattr(getattr(event, "vm", None), "name", "")
                vm_part = f" ВМ: <b>{html.escape(vm_name)}</b>" if vm_name else ""
                text = (
                    f"🛰️ <b>{html.escape(self.config['name'])}</b> — "
                    f"👤 <b>{html.escape(user_name)}</b>{vm_part} — {html.escape(raw)}"
                )
        self._send(text)
        if type(event).__name__ != "TaskEvent":
            return None
        info = getattr(event, "info", None)
        if info is None:
            return None
        candidates = []
        for attr in ("name",):
            value = getattr(info, attr, None)
            if value:
                candidates.append(value.strip())
        desc_msg = getattr(getattr(info, "description", None), "message", None)
        if desc_msg:
            candidates.append(desc_msg.strip())
        raw = getattr(event, "fullFormattedMessage", "")
        if raw.startswith("Task:"):
            _, _, rest = raw.partition(":")
            if rest:
                candidates.append(rest.strip())
        entity_name = (
            getattr(info, "entityName", "")
            or getattr(getattr(event, "vm", None), "name", "")
            or getattr(getattr(event, "host", None), "name", "")
        )
        vc_label = f"{self.config['name']} ({self.config['host']})"
        for key in candidates:
            translation = TASK_GENERIC_TRANSLATIONS.get(key)
            if translation:
                entity_part = f" для <b>{html.escape(entity_name)}</b>" if entity_name else ""
                return (
                    f"🛰️ <b>{html.escape(vc_label)}</b> — "
                    f"👤 <b>{html.escape(user_name)}</b> {translation}{entity_part}."
                )
        return None

    def _event_details(self, event) -> Optional[Tuple[str, str]]:
        event_type = type(event).__name__
        vm_name = getattr(getattr(event, "vm", None), "name", "")
        verb = EVENT_VERB_MAP.get(event_type)
        if event_type == "TaskEvent":
            info = getattr(event, "info", None)
            desc = getattr(info, "descriptionId", "") or ""
            vm_name = vm_name or getattr(info, "entityName", "")
            verb = TASK_VERB_MAP.get(desc)
            if not verb and desc:
                desc_suffix = desc.split(".")[-1]
                verb = TASK_VERB_MAP.get(desc_suffix)
            if not verb and info is not None:
                task_name = (getattr(info, "name", "") or "").strip()
                verb = TASK_NAME_VERB_MAP.get(task_name)
                if not verb:
                    localized = getattr(getattr(info, "description", None), "message", "") or ""
                    verb = TASK_NAME_VERB_MAP.get(localized)
        if not verb:
            return None
        if not vm_name:
            vm_name = getattr(getattr(event, "info", None), "entityName", "")
        return verb, vm_name

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