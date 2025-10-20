"""Helpers for interacting with vCenter via pyVmomi."""

from __future__ import annotations

import ssl
import time
from typing import Iterable, List

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim

from .config import VCENTERS

__all__ = [
    "vcenter_connect",
    "get_all_vms",
    "find_vm",
    "refresh_power_state",
    "wait_power_state",
    "ru_power_state",
    "Disconnect",
]


def ru_power_state(state: str) -> str:
    s = str(state)
    if s == "poweredOn":
        return "Включен"
    if s == "poweredOff":
        return "Выключен"
    return s


def vcenter_connect(index: int = 0):
    vc = VCENTERS[index]
    if vc.get("cafile"):
        ctx = ssl.create_default_context(cafile=vc["cafile"])
    else:
        ctx = ssl._create_unverified_context()
    return SmartConnect(host=vc["host"], user=vc["user"], pwd=vc["pass"], sslContext=ctx)


def get_all_vms(content) -> List[vim.VirtualMachine]:
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        return sorted(view.view, key=lambda v: v.name.lower())
    finally:
        view.Destroy()


def find_vm(content, name: str):
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        for vm in view.view:
            if vm.name == name:
                return vm
        return None
    finally:
        view.Destroy()


def refresh_power_state(vm) -> str:
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
