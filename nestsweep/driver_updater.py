"""Drivers: list device drivers, find newer ones on Windows Update, back up, update, restore.

Safety net, in order:
  1. Only drivers dated *newer* than the installed one are offered (no silent downgrades).
  2. A System Restore point is created and verified to exist before anything is installed.
     If Windows refuses (System Restore off, or one was already made in the last 24 hours)
     nothing is installed unless the user explicitly continues without one.
  3. Each third-party (oem*.inf) driver is exported with pnputil first. A device whose backup
     fails is skipped. Built-in Windows drivers stay in Windows' own driver store.
  4. Backups live in %ProgramData%\\Nestsweep\\DriverBackups, writable only by administrators,
     and each one's SHA-256 is journaled. Restore refuses any file that doesn't match.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import tempfile
import time
import zipfile
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from . import common, safety, windows_update

BACKUP_DIR = Path(os.environ.get("ProgramData") or r"C:\ProgramData") / common.APP_NAME / "DriverBackups"


class RestorePointError(RuntimeError):
    """No restore point could be created; the UI asks before continuing without one."""


def _owner_is_admin(path: Path) -> bool:
    try:
        owner = common.powershell("(Get-Acl -LiteralPath $env:NS_P).GetOwner([Security.Principal.SecurityIdentifier]).Value",
                                  args={"p": str(path)}, strict=True).strip()
    except Exception:
        return False
    return owner in ("S-1-5-32-544", "S-1-5-18")       # Administrators, SYSTEM


def _ensure_backup_dir() -> Path:
    """The backup root (%ProgramData%\\Nestsweep) is readable and writable only by Administrators
    and SYSTEM, so a standard user can't plant or swap a driver package for us to install.

    Any user can pre-create folders under ProgramData, so an existing root is only trusted if an
    administrator owns it and it contains no junctions or symlinks. Otherwise it is moved aside
    and recreated. The ACL is set on the root itself (no /T), never following anything inside."""
    if not common.is_admin():
        raise PermissionError("Driver backups need administrator rights.")
    root = BACKUP_DIR.parent
    if root.exists():
        trusted = not safety.is_reparse(str(root)) and _owner_is_admin(root) and \
            not any(safety.is_reparse(os.path.join(r, n)) for r, ds, fs in os.walk(root) for n in ds + fs)
        if not trusted:
            root.rename(root.with_name(f"{root.name}.untrusted-{time.strftime('%Y%m%d-%H%M%S')}"))
    if not root.exists():
        root.mkdir(parents=True)
        common.run(["icacls", str(root), "/setowner", "*S-1-5-32-544", "/Q"])
        cp = common.run(["icacls", str(root), "/inheritance:r", "/grant:r", "*S-1-5-32-544:(OI)(CI)F",
                         "*S-1-5-18:(OI)(CI)F", "/Q"])
        if cp.returncode != 0:
            raise OSError("couldn't lock down the driver backup folder: " + (cp.stdout or cp.stderr).strip())
    BACKUP_DIR.mkdir(exist_ok=True)
    return BACKUP_DIR


@dataclass
class Device:
    id: str                    # PnP device instance id
    name: str
    category: str
    manufacturer: str
    driver_version: str
    driver_date: str           # yyyy-MM-dd
    inf_name: str
    hardware_ids: list[str]
    problem_code: int = 0
    is_microsoft_inbox: bool = False
    update: dict | None = None     # matching newer Windows Update driver
    ignored: bool = False
    backups: list[str] = field(default_factory=list)   # backup file names, oldest first


# Device Manager problem codes (CM_PROB_*) in plain words. Codes 22 and 45 are states the user
# chose or expects (disabled, unplugged), so they aren't counted as problems.
PROBLEMS = {
    1: "Not set up correctly", 3: "Driver may be damaged", 10: "Can't start", 12: "Not enough free resources",
    14: "Restart needed", 18: "Reinstall the driver", 19: "Settings in the registry are damaged",
    21: "Being removed", 22: "Disabled", 24: "Not present", 28: "No driver installed",
    29: "Disabled in firmware", 31: "Not working properly", 32: "Driver service is disabled",
    37: "Driver failed to load", 39: "Driver missing or damaged", 43: "Windows stopped this device",
    45: "Not connected", 47: "Prepared for safe removal", 48: "Driver blocked by Windows",
    52: "Driver signature can't be verified",
}
NOT_A_PROBLEM = {22, 45, 47}

# Device classes whose drivers are never updated from here: a bad driver for these can stop
# Windows from booting (storage, chipset, firmware/BIOS) or leave the PC without a keyboard,
# mouse or network to fix it. Windows Update and the PC maker's tools handle them.
NEVER_UPDATE = {"firmware", "scsiadapter", "hdc", "system", "net", "processor", "diskdrive", "volume",
                "computer", "usb", "keyboard", "mouse", "hidclass", "battery", "securitydevices",
                "softwarecomponent", "extension"}


def offerable(category: str) -> bool:
    return (category or "").lower() not in NEVER_UPDATE


def problem_text(code: int) -> str:
    return PROBLEMS.get(code, f"Problem (code {code})") if code else ""


def needs_attention(code: int) -> bool:
    return bool(code) and code not in NOT_A_PROBLEM


def scan_devices() -> list[Device]:
    rows = common.powershell_json(r"""
$err = @{}; Get-CimInstance Win32_PnPEntity | ForEach-Object { $err[$_.PNPDeviceID] = $_.ConfigManagerErrorCode }
Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceName -and $_.InfName } | ForEach-Object {
  [pscustomobject]@{ Id=$_.DeviceID; Name=$_.DeviceName; Class=[string]$_.DeviceClass; Mfr=[string]$_.Manufacturer
    Provider=[string]$_.DriverProviderName; Version=[string]$_.DriverVersion
    Date= if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { '' }
    Inf=[string]$_.InfName; HwId=[string]$_.HardWareID; Err=[int]$err[$_.DeviceID] } }""", timeout=300)
    ignored = set(common.setting_get("du_ignored", []))
    out = []
    for r in rows:
        out.append(Device(r["Id"], r["Name"], r["Class"] or "OTHER", r["Mfr"] or r["Provider"], r["Version"],
                          r["Date"], r["Inf"], [h for h in [r["HwId"]] if h], r["Err"] or 0,
                          r["Provider"] == "Microsoft" and not r["Inf"].lower().startswith("oem"),
                          ignored=r["Id"] in ignored))
    return out


def _known_backups() -> dict[str, dict]:
    """Backups this app made, from the journal: {file name: info incl. sha256}."""
    out = {}
    for row in common.journal_open("driver_updater"):
        if row["action"] == "backup":
            info = json.loads(row["data"])
            out[info["file"]] = info
    return out


def _attach_backups(devs: list[Device]) -> None:
    known = _known_backups()
    if not known or not BACKUP_DIR.is_dir():
        return
    for d in devs:
        mine = [i for i in known.values() if i.get("device_id") == d.id and (BACKUP_DIR / i["file"]).is_file()]
        d.backups = [i["file"] for i in sorted(mine, key=lambda i: i["backup_created"])]


def _is_newer(update_date: str, installed_date: str) -> bool:
    """Both are yyyy-MM-dd. Unknown installed date -> treat as newer only if the update has a date."""
    if not update_date:
        return False
    return not installed_date or update_date > installed_date


def _hw_match(wu_id: str, device_ids: list[str]) -> bool:
    w = wu_id.lower()
    return any(h.lower() == w or h.lower().startswith(w + "&") for h in device_ids)


def scan(progress=None) -> list[Device]:
    if progress:
        progress("Listing devices")
    devs = scan_devices()
    if progress:
        progress("Asking Windows Update for newer drivers")
    try:
        updates = windows_update.search("Driver")
    except Exception:
        updates = []
    for u in updates:
        hw = u.get("DriverHardwareID") or ""
        date = u.get("DriverVerDate") or ""
        for d in devs:
            if hw and _hw_match(hw, d.hardware_ids) and _is_newer(date, d.driver_date) and offerable(d.category):
                if not d.update or date > (d.update.get("DriverVerDate") or ""):
                    d.update = u
    try:
        _attach_backups(devs)
    except OSError:
        pass
    return devs


# ---------------------------------------------------------------- backup / restore

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup(dev: Device, reason: str = "manual") -> str | None:
    """Export the device's current third-party driver package into a zip, journal its hash.
    Returns the file name, or None for built-in Windows drivers (nothing to export)."""
    if not dev.inf_name.lower().startswith("oem"):
        return None
    folder = _ensure_backup_dir()
    with tempfile.TemporaryDirectory() as tmp:
        cp = common.run(["pnputil", "/export-driver", dev.inf_name, tmp], timeout=300)
        if cp.returncode != 0:
            raise RuntimeError(cp.stdout.strip() or cp.stderr.strip() or "pnputil export failed")
        safe = lambda s: re.sub(r'[^\w.-]+', "_", s)[:40]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dev_tag = hashlib.sha1(dev.id.encode()).hexdigest()[:8]
        name = f"{safe(dev.manufacturer)}_{safe(dev.name)}_{safe(dev.driver_version)}_{dev_tag}_{stamp}.zip"
        info = {"backup_created": time.strftime("%Y-%m-%dT%H:%M:%S"), "device_id": dev.id, "file": name, "reason": reason,
                "hardware_ids": dev.hardware_ids, "oem_inf": dev.inf_name, "driver_version": dev.driver_version,
                "driver_date": dev.driver_date, "manufacturer": dev.manufacturer, "name": dev.name}
        dest = folder / name
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(tmp):
                for f in files:
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, tmp))
            z.writestr("DriverInfo.json", json.dumps(info, indent=2))
    info["sha256"] = _sha256(dest)
    common.journal_add("driver_updater", dev.id, "backup", info)
    return name


def _force_install(hardware_id: str, inf: str) -> bool:
    """UpdateDriverForPlugAndPlayDevices with INSTALLFLAG_FORCE (allows going back to an older
    driver). Applies to every present device with this hardware ID, which is how Windows itself
    rolls back identical devices."""
    fn = ctypes.windll.newdev.UpdateDriverForPlugAndPlayDevicesW
    fn.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.BOOL)]
    reboot = wintypes.BOOL(False)
    ok = fn(None, hardware_id, inf, 0x1, ctypes.byref(reboot))
    if not ok:
        raise ctypes.WinError()
    return bool(reboot.value)


def restore(dev: Device, file_name: str | None = None) -> dict:
    """Reinstall a backup this app made for this device (the newest one unless named)."""
    if not common.is_admin():
        raise PermissionError("Restoring drivers requires administrator rights.")
    known = _known_backups()
    mine = sorted((i for i in known.values() if i.get("device_id") == dev.id and (BACKUP_DIR / i["file"]).is_file()),
                  key=lambda i: i["backup_created"])
    if file_name:
        mine = [i for i in mine if i["file"] == file_name]
    if not mine:
        raise FileNotFoundError(f"There's no backup of {dev.name} made by {common.APP_NAME}.")
    info = mine[-1]
    path = BACKUP_DIR / info["file"]
    if os.path.basename(info["file"]) != info["file"] or safety.is_reparse(str(path)) or _sha256(path) != info.get("sha256"):
        raise PermissionError("The backup file has been changed since it was made, so it won't be installed.")
    if not dev.hardware_ids:
        raise ValueError(f"{dev.name} has no hardware ID to install onto.")
    with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as tmp:
        z.extractall(tmp)        # zipfile strips absolute paths and ".." parts
        infs = [os.path.join(r, f) for r, _, fs in os.walk(tmp) for f in fs if f.lower().endswith(".inf")]
        if not infs:
            raise FileNotFoundError("The backup doesn't contain a driver INF.")
        cp = common.run(["pnputil", "/add-driver", infs[0]], timeout=600)
        if cp.returncode not in (0, 259, 3010):    # 259: already in the store, 3010: restart needed
            raise RuntimeError(cp.stdout.strip() or "pnputil couldn't add the driver")
        reboot = _force_install(dev.hardware_ids[0], infs[0])
    common.journal_add("driver_updater", dev.id, "restore", info)
    return {"device": dev.name, "version": info["driver_version"], "reboot_required": reboot}


def _latest_restore_point() -> int:
    """Highest restore point sequence number (0 if none). Compared before/after, because Windows
    may delete the oldest point when it adds one, so the count alone can't be trusted."""
    out = common.powershell("$p = @(Get-ComputerRestorePoint -ErrorAction SilentlyContinue);"
                            " if ($p.Count) { ($p | Measure-Object -Property SequenceNumber -Maximum).Maximum } else { 0 }",
                            timeout=120)
    return int((out.strip() or "0").split()[0])


def create_restore_point(description: str = f"{common.APP_NAME} driver update") -> None:
    """Create a restore point and confirm a new one exists. Raises RestorePointError otherwise."""
    try:
        before = _latest_restore_point()
        common.powershell("Checkpoint-Computer -Description $env:NS_DESC -RestorePointType MODIFY_SETTINGS",
                          timeout=900, args={"desc": description}, strict=True)
        after = _latest_restore_point()
    except Exception as e:
        raise RestorePointError(f"Windows couldn't create a restore point ({e}). System Restore may be turned off.")
    if after <= before:
        raise RestorePointError("Windows didn't create a restore point. It allows one every 24 hours, "
                                "or System Restore may be turned off for this drive.")


def update(devices: list[Device], restore_point: bool = True, progress=None) -> dict:
    """Install newer drivers. Returns {"results": [...], "skipped": ["Device: reason", ...]}."""
    if not common.is_admin():
        raise PermissionError("Updating drivers requires administrator rights.")
    devices = [d for d in devices if d.update and offerable(d.category)
               and _is_newer(d.update.get("DriverVerDate") or "", d.driver_date)]
    if not devices:
        return {"results": [], "skipped": []}
    if restore_point:
        if progress:
            progress("Creating a System Restore point")
        create_restore_point()
    skipped, ready = [], []
    for d in devices:
        if progress:
            progress(f"Backing up {d.name}")
        try:
            backup(d, "before_update")
            ready.append(d)
        except Exception as e:
            skipped.append(f"{d.name}: backup failed, so it wasn't updated ({e})")
    results = []
    if ready:
        if progress:
            progress("Downloading and installing drivers")
        results = windows_update.install([d.update["Id"] for d in ready])
        for r in results:
            common.journal_add("driver_updater", r["Id"], "update", r)
    return {"results": results, "skipped": skipped}


def set_ignored(device_id: str, on: bool) -> None:
    s = set(common.setting_get("du_ignored", []))
    s.add(device_id) if on else s.discard(device_id)
    common.setting_set("du_ignored", sorted(s))
