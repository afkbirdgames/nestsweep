"""Startup: list and toggle what starts with Windows.

  startup apps   Run / RunOnce (HKCU, HKLM, both registry views) and the Startup folders.
                 They are switched off with Explorer\\StartupApproved, the same flag Task
                 Manager's Startup tab uses, so Windows shows the same state.
  tasks          Task Scheduler tasks outside \\Microsoft\\ (first exec action is the program).
  services       Third-party auto-start services. Microsoft, svchost, Windows-folder and
                 protected (anti-malware) services are not listed.
  context menu   Shell verbs (LegacyDisable) and ShellEx handlers (Shell Extensions\\Blocked)
                 for files, folders and drives; Microsoft components are not listed.
Every change is journaled; deleted entries are backed up first.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import time
import winreg
from dataclasses import dataclass, asdict

from . import common, safety

APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
BLOCKED = r"Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Blocked"
RUN_LOCATIONS = [
    # (hive, key, view, StartupApproved subkey or None)
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, "Run"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", 0, None),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", winreg.KEY_WOW64_64KEY, "Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", winreg.KEY_WOW64_32KEY, "Run32"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", winreg.KEY_WOW64_64KEY, None),
]
CONTEXT_CLASSES = ["*", "Directory", "Directory\\Background", "Folder", "Drive", "AllFileSystemObjects"]


@dataclass
class StartupItem:
    id: str
    item_type: str      # startup_app | scheduled_task | context_menu | windows_service
    name: str
    command: str
    publisher: str
    location: str
    enabled: bool
    can_toggle: bool = True
    detail: dict | None = None


def _id(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def _publisher(cmd: str) -> str:
    exe = common.exe_from_command(cmd)
    return common.file_company(exe) if os.path.isfile(exe) else ""


def _approved_state(hive: int, sub: str, name: str) -> bool:
    data = common.reg_get(hive, APPROVED + "\\" + sub, name)
    # First byte: 0x02/0x06 enabled, 0x03/0x07 disabled (bit 0 = disabled)
    return not (isinstance(data, bytes) and data and data[0] & 1)


def _set_approved(hive: int, sub: str, name: str, enable: bool) -> None:
    if enable:
        data = bytes([0x02]) + bytes(11)
    else:
        ft = int((time.time() + 11644473600) * 10_000_000)
        data = bytes([0x03, 0, 0, 0]) + struct.pack("<Q", ft)
    with winreg.CreateKeyEx(hive, APPROVED + "\\" + sub, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, data)


# ---------------------------------------------------------------- detectors

def _startup_apps() -> list[StartupItem]:
    items = []
    for hive, key, view, approved in RUN_LOCATIONS:
        for name, (cmd, _) in common.reg_values(hive, key, view).items():
            if not name:
                continue
            loc = f"{common.HIVE_NAMES[hive]}\\{key}" + (" (32-bit)" if view == winreg.KEY_WOW64_32KEY else "")
            items.append(StartupItem(
                _id("run", hive, key, view, name), "startup_app", name, str(cmd), _publisher(str(cmd)), loc,
                _approved_state(hive, approved, name) if approved else True, approved is not None,
                {"kind": "reg", "hive": hive, "key": key, "view": view, "approved": approved, "value": name},
            ))
    folders = [
        (winreg.HKEY_CURRENT_USER, os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")),
        (winreg.HKEY_LOCAL_MACHINE, os.path.join(os.environ["PROGRAMDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")),
    ]
    for hive, folder in folders:
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            if f.lower() == "desktop.ini":
                continue
            path = os.path.join(folder, f)
            items.append(StartupItem(
                _id("folder", path), "startup_app", os.path.splitext(f)[0], path, "", folder,
                _approved_state(hive, "StartupFolder", f), True,
                {"kind": "file", "hive": hive, "path": path, "approved": "StartupFolder", "value": f},
            ))
    return items


def _scheduled_tasks() -> list[StartupItem]:
    rows = common.powershell_json(
        "Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' } | ForEach-Object {"
        " $a = $_.Actions | Where-Object { $_.Execute } | Select-Object -First 1;"
        " [pscustomobject]@{ Name=$_.TaskName; Path=$_.TaskPath; State=[string]$_.State;"
        " Exec=[string]$a.Execute; Args=[string]$a.Arguments; Author=[string]$_.Author } }")
    items = []
    for r in rows:
        cmd = f'{r["Exec"]} {r["Args"]}'.strip()
        items.append(StartupItem(
            _id("task", r["Path"], r["Name"]), "scheduled_task", r["Name"], cmd,
            _publisher(r["Exec"]) if r["Exec"] else r.get("Author", ""), r["Path"],
            r["State"] != "Disabled", True, {"kind": "task", "path": r["Path"], "name": r["Name"]},
        ))
    return items


def _services() -> list[StartupItem]:
    base = r"SYSTEM\CurrentControlSet\Services"
    items = []
    for svc in common.reg_subkeys(winreg.HKEY_LOCAL_MACHINE, base):
        v = {k: val for k, (val, _) in common.reg_values(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{svc}").items()}
        if v.get("Type") not in (0x10, 0x20, 0x50, 0x60) or "ImagePath" not in v:
            continue
        start = v.get("Start")
        if start not in (2, 3, 4) or safety.is_critical_service(svc, v) or safety.is_sensitive_service(svc, v):
            continue
        exe = common.exe_from_command(str(v["ImagePath"]))
        company = common.file_company(exe) if os.path.isfile(exe) else ""
        # A user-disabled auto service is shown as disabled; manual ones that never auto-start are skipped
        orig = _journaled_start(svc)
        if start == 3 and orig is None:
            continue
        name = v.get("DisplayName", svc)
        if str(name).startswith("@"):
            name = svc
        items.append(StartupItem(
            _id("svc", svc), "windows_service", str(name), str(v["ImagePath"]), company, f"{base}\\{svc}",
            start == 2, True, {"kind": "service", "service": svc, "start": start},
        ))
    return items


def _journaled_start(svc: str):
    rows = common.journal_open("startup_manager", "svc:" + svc)
    return __import__("json").loads(rows[0]["data"])["start"] if rows else None


def _class_key(cls: str, sub: str) -> tuple[int, str] | None:
    """Locate where an HKCR key physically lives (per-user classes win)."""
    for hive, prefix in ((winreg.HKEY_CURRENT_USER, r"Software\Classes"), (winreg.HKEY_LOCAL_MACHINE, r"Software\Classes")):
        path = f"{prefix}\\{cls}\\{sub}"
        if common.reg_key_exists(hive, path):
            return hive, path
    return None


def _clsid_dll(clsid: str) -> str:
    return common.reg_get(winreg.HKEY_CLASSES_ROOT, f"CLSID\\{clsid}\\InprocServer32", "", "") or ""


def _context_menu() -> list[StartupItem]:
    items, seen = [], set()
    blocked = set(common.reg_values(winreg.HKEY_CURRENT_USER, BLOCKED)) | set(common.reg_values(winreg.HKEY_LOCAL_MACHINE, BLOCKED))
    blocked = {b.upper() for b in blocked}
    for cls in CONTEXT_CLASSES:
        # ShellEx handlers (COM DLLs)
        for name in common.reg_subkeys(winreg.HKEY_CLASSES_ROOT, f"{cls}\\ShellEx\\ContextMenuHandlers"):
            clsid = name if name.startswith("{") else common.reg_get(
                winreg.HKEY_CLASSES_ROOT, f"{cls}\\ShellEx\\ContextMenuHandlers\\{name}", "", "")
            if not clsid or (cls, clsid.upper()) in seen:
                continue
            seen.add((cls, clsid.upper()))
            dll = os.path.expandvars(_clsid_dll(clsid))
            company = common.file_company(dll) if os.path.isfile(dll) else ""
            if not dll or company.startswith("Microsoft"):
                continue
            items.append(StartupItem(
                _id("cmh", cls, clsid), "context_menu", name.lstrip("-"), dll, company,
                f"HKCR\\{cls}\\ShellEx\\ContextMenuHandlers", clsid.upper() not in blocked, True,
                {"kind": "shellex", "clsid": clsid, "class": cls},
            ))
        # Static verbs
        for verb in common.reg_subkeys(winreg.HKEY_CLASSES_ROOT, f"{cls}\\shell"):
            cmd = common.reg_get(winreg.HKEY_CLASSES_ROOT, f"{cls}\\shell\\{verb}\\command", "", "")
            if not cmd:
                continue
            exe = common.exe_from_command(cmd)
            company = common.file_company(exe) if os.path.isfile(exe) else ""
            if company.startswith("Microsoft") or "system32" in exe.lower():
                continue
            disabled = common.reg_get(winreg.HKEY_CLASSES_ROOT, f"{cls}\\shell\\{verb}", "LegacyDisable") is not None
            label = common.reg_get(winreg.HKEY_CLASSES_ROOT, f"{cls}\\shell\\{verb}", "MUIVerb") or \
                common.reg_get(winreg.HKEY_CLASSES_ROOT, f"{cls}\\shell\\{verb}", "", "") or verb
            items.append(StartupItem(
                _id("verb", cls, verb), "context_menu", common.indirect_string(str(label)).replace("&", ""), cmd, company,
                f"HKCR\\{cls}\\shell\\{verb}", not disabled, True, {"kind": "verb", "class": cls, "verb": verb},
            ))
    return items


def scan() -> list[StartupItem]:
    items: list[StartupItem] = []
    for detector in (_startup_apps, _scheduled_tasks, _services, _context_menu):
        try:
            items.extend(detector())
        except Exception as e:  # one failing detector must not hide the others
            items.append(StartupItem(_id("err", detector.__name__), "error", detector.__name__, str(e), "", "", False, False))
    return items


# ---------------------------------------------------------------- operations

_TASK_LOOKUP = "Get-ScheduledTask | Where-Object { $_.TaskPath -eq $env:NS_PATH -and $_.TaskName -eq $env:NS_NAME }"


def exists(item: StartupItem) -> bool:
    """Is the entry still there? (Used when waking a program whose entry may have been removed.)"""
    d = item.detail or {}
    kind = d.get("kind")
    try:
        if kind == "reg":
            return d["value"] in common.reg_values(d["hive"], d["key"], d["view"])
        if kind == "file":
            return os.path.exists(d["path"])
        if kind == "task":
            return bool(common.powershell_json(_TASK_LOOKUP, args={"path": d["path"], "name": d["name"]}))
    except Exception:
        return True          # can't tell: report the failure rather than hide it
    return True


def set_enabled(item: StartupItem, enable: bool) -> None:
    d = item.detail or {}
    kind = d.get("kind")
    if kind in ("reg", "file"):
        if not d.get("approved"):
            raise ValueError("RunOnce entries can only be deleted")
        _set_approved(d["hive"], d["approved"], d["value"], enable)
    elif kind == "task":
        safety.check_task_ref(d["path"], d["name"])
        if safety.is_system_task(d["path"]):
            raise PermissionError("Windows tasks can't be changed here")
        verb = "Enable" if enable else "Disable"
        # Exact match with -eq: -TaskPath/-TaskName treat [ ] * ? as wildcards.
        common.powershell(f"$t = @({_TASK_LOOKUP}); if ($t.Count -ne 1) {{ throw 'task not found' }};"
                          f" $t | {verb}-ScheduledTask | Out-Null",
                          args={"path": d["path"], "name": d["name"]}, strict=True)
    elif kind == "service":
        svc = d["service"]
        v = {k: val for k, (val, _) in common.reg_values(
            winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Services\\{svc}").items()}
        if safety.is_critical_service(svc, v):
            raise PermissionError("Windows and security services can't be changed here")
        if enable:
            rows = common.journal_open("startup_manager", "svc:" + svc)
            # Back to what it was before we turned it off; if we never changed it, Manual (3)
            # rather than Automatic, so a service someone else disabled isn't forced on at boot.
            start = __import__("json").loads(rows[0]["data"])["start"] if rows else 3
            for r in rows:
                common.journal_mark_reverted(r["id"])
        else:
            common.journal_add("startup_manager", "svc:" + svc, "disable", {"start": d["start"]})
            start = 3   # demand start: stops auto-start without breaking apps that start it on demand
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Services\\{svc}", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "Start", 0, winreg.REG_DWORD, start)
    elif kind == "verb":
        hive, path = _class_key(d["class"], f"shell\\{d['verb']}") or (None, None)
        if hive is None:
            raise FileNotFoundError("verb key not found")
        with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                try:
                    winreg.DeleteValue(k, "LegacyDisable")
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(k, "LegacyDisable", 0, winreg.REG_SZ, "")
    elif kind == "shellex":
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, BLOCKED, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                try:
                    winreg.DeleteValue(k, d["clsid"])
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(k, d["clsid"], 0, winreg.REG_SZ, item.name)
    else:
        raise ValueError(f"cannot toggle {item.item_type}")
    common.journal_add("startup_manager", item.id, "enable" if enable else "disable", asdict(item))


def delete(item: StartupItem) -> None:
    """Remove the entry, keeping a backup in the journal / backups folder."""
    d = item.detail or {}
    kind = d.get("kind")
    if kind == "reg":
        with winreg.OpenKey(d["hive"], d["key"], 0, winreg.KEY_SET_VALUE | d["view"]) as k:
            winreg.DeleteValue(k, d["value"])
    elif kind == "file":
        if safety.is_reparse(d["path"]):
            raise PermissionError("not a regular startup file")
        dest = common.BACKUP_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{os.path.basename(d['path'])}"
        shutil.move(d["path"], dest)
    elif kind == "task":
        safety.check_task_ref(d["path"], d["name"])
        if safety.is_system_task(d["path"]):
            raise PermissionError("Windows tasks can't be deleted here")
        dest = common.BACKUP_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_task_{_id(d['path'], d['name'])}.xml"
        # Export first; strict mode stops before Unregister if the backup couldn't be written.
        common.powershell(f"$t = @({_TASK_LOOKUP}); if ($t.Count -ne 1) {{ throw 'task not found' }};"
                          " $t[0] | Export-ScheduledTask | Set-Content -Encoding Unicode -LiteralPath $env:NS_DEST;"
                          " if (-not (Test-Path -LiteralPath $env:NS_DEST)) { throw 'backup failed' };"
                          " $t[0] | Unregister-ScheduledTask -Confirm:$false",
                          args={"path": d["path"], "name": d["name"], "dest": str(dest)}, strict=True)
    else:
        raise ValueError(f"delete is not offered for {item.item_type}; disable it instead")
    common.journal_add("startup_manager", item.id, "delete", asdict(item))
