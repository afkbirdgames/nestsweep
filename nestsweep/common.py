"""Shared helpers: registry access, elevation, PowerShell, system tools and the state journal.

Every change Nestsweep makes is written to one SQLite journal so it can be reverted,
and registry keys are exported to .reg files before they are deleted.
"""
from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import subprocess
import sys
import time
import winreg
from contextlib import contextmanager
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)          # True inside the PyInstaller-built Nestsweep.exe

APP_NAME = "Nestsweep"
_LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home())
APP_DIR = _LOCAL / APP_NAME
APP_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR = APP_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)
DB_PATH = APP_DIR / "state.db"

CREATE_NO_WINDOW = 0x08000000
SYSTEM32 = os.path.join(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows", "System32")


def sys32(tool: str) -> str:
    """Full path to a Windows tool. Never rely on PATH or the current folder: when elevated,
    a planted reg.exe / sc.exe next to the downloaded exe would otherwise run as admin."""
    if tool == "powershell":
        return os.path.join(SYSTEM32, "WindowsPowerShell", "v1.0", "powershell.exe")
    return os.path.join(SYSTEM32, tool if tool.endswith(".exe") else tool + ".exe")

HIVES = {
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
    "HKCU": winreg.HKEY_CURRENT_USER,
    "HKU": winreg.HKEY_USERS,
    "HKCR": winreg.HKEY_CLASSES_ROOT,
}
HIVE_NAMES = {v: k for k, v in HIVES.items()}


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin(extra_args: list[str] | None = None) -> bool:
    """Re-run the current command line elevated (UAC prompt). Returns True if launched.

    From source, sys.executable is python.exe and argv[0] is the script/module, so argv is
    passed through whole. In the built exe, sys.executable *is* Nestsweep.exe, so argv[0] is dropped.
    """
    args = list(sys.argv[1:] if FROZEN else sys.argv) + list(extra_args or [])
    if not FROZEN and sys.argv and sys.argv[0].endswith("__main__.py"):
        args = ["-m", "nestsweep", *sys.argv[1:], *(extra_args or [])]
    params = subprocess.list2cmdline(args)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return rc > 32


def run(cmd: list[str], timeout: int = 600, check: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a command (argument list, never a shell string). System tools resolve to System32."""
    if isinstance(cmd, str):
        raise TypeError("pass an argument list, not a command string")
    cmd = list(cmd)
    if not os.path.isabs(cmd[0]) and cmd[0].lower().removesuffix(".exe") in _SYSTEM_TOOLS:
        cmd[0] = sys32(cmd[0].lower().removesuffix(".exe"))
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update({k: str(v) for k, v in env.items()})
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=CREATE_NO_WINDOW, check=check, env=full_env,
    )


_SYSTEM_TOOLS = {"powershell", "reg", "sc", "pnputil", "ipconfig", "tasklist", "icacls", "schtasks"}


def powershell(script: str, timeout: int = 600, args: dict | None = None, strict: bool = False) -> str:
    """Run a PowerShell snippet and return stdout. Raises RuntimeError on failure.

    Never format untrusted values (task names, package names, IDs) into `script`. Pass them in
    `args` instead: each becomes an environment variable the script reads as `$env:NS_<KEY>`,
    so the value is data and can't be parsed as code. `strict` makes any error fatal.
    """
    prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    if strict:
        prefix += "$ErrorActionPreference='Stop';"
    env = {f"NS_{k.upper()}": v for k, v in (args or {}).items()}
    cp = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
              "-Command", prefix + script], timeout=timeout, env=env)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or f"PowerShell exited {cp.returncode}")
    return cp.stdout


def powershell_json(script: str, timeout: int = 600, args: dict | None = None) -> list[dict]:
    out = powershell(f"$r = & {{ {script} }}; if ($r) {{ $r | ConvertTo-Json -Depth 4 -Compress }}", timeout, args)
    out = out.strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


# ---------------------------------------------------------------- registry

def split_key(path: str) -> tuple[int, str]:
    hive, _, sub = path.partition("\\")
    return HIVES[hive.upper()], sub


def reg_values(root: int, sub: str, view: int = 0) -> dict[str, tuple[object, int]]:
    out: dict[str, tuple[object, int]] = {}
    try:
        with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view) as k:
            i = 0
            while True:
                try:
                    name, val, typ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out[name] = (val, typ)
                i += 1
    except OSError:
        pass
    return out


def reg_subkeys(root: int, sub: str, view: int = 0) -> list[str]:
    keys = []
    try:
        with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view) as k:
            i = 0
            while True:
                try:
                    keys.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return keys


def reg_get(root: int, sub: str, name: str, default=None, view: int = 0):
    try:
        with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return default


def reg_key_exists(root: int, sub: str, view: int = 0) -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view))
        return True
    except OSError:
        return False


def reg_export(path: str, tag: str, view: int = 0) -> Path | None:
    """Export a key to a unique .reg file before it is modified. Returns None if the export
    failed, and callers must then leave the key alone."""
    import re
    import uuid
    safe_tag = re.sub(r"[^\w.-]+", "_", tag)[-60:]
    dest = BACKUP_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}_{safe_tag}.reg"
    cmd = ["reg", "export", path, str(dest), "/y"]
    if view & winreg.KEY_WOW64_32KEY:
        cmd.append("/reg:32")
    elif view & winreg.KEY_WOW64_64KEY:
        cmd.append("/reg:64")
    cp = run(cmd, timeout=60)
    return dest if cp.returncode == 0 and dest.is_file() and dest.stat().st_size > 0 else None


def reg_delete_tree(root: int, sub: str, view: int = 0) -> None:
    for child in reg_subkeys(root, sub, view):
        reg_delete_tree(root, sub + "\\" + child, view)
    winreg.DeleteKeyEx(root, sub, view, 0)


# ---------------------------------------------------------------- journal

SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature TEXT NOT NULL,
    subject TEXT NOT NULL,
    action TEXT NOT NULL,
    data TEXT NOT NULL,
    time TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    reverted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def journal_add(feature: str, subject: str, action: str, data: dict) -> None:
    with db() as c:
        c.execute("INSERT INTO journal (feature, subject, action, data) VALUES (?,?,?,?)",
                  (feature, subject, action, json.dumps(data)))


def journal_open(feature: str, subject: str | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM journal WHERE feature=? AND reverted=0"
    args: list = [feature]
    if subject is not None:
        q += " AND subject=?"
        args.append(subject)
    with db() as c:
        return c.execute(q + " ORDER BY id DESC", args).fetchall()


def journal_mark_reverted(row_id: int) -> None:
    with db() as c:
        c.execute("UPDATE journal SET reverted=1 WHERE id=?", (row_id,))


def setting_get(key: str, default=None):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def setting_set(key: str, value) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))


def file_company(path: str) -> str:
    """CompanyName from a PE's version resource (used to skip Microsoft components)."""
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return ""
    ptr, n = ctypes.c_void_p(), ctypes.c_uint()
    if not ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(n)) or n.value < 4:
        return ""
    lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
    sub = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\CompanyName"
    if ver.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(n)) and n.value:
        return ctypes.wstring_at(ptr).strip()
    return ""


def indirect_string(s: str) -> str:
    """Resolve '@shell32.dll,-8506' style resource strings."""
    if not s.startswith("@"):
        return s
    buf = ctypes.create_unicode_buffer(512)
    if ctypes.windll.shlwapi.SHLoadIndirectString(s, buf, 512, None) == 0:
        return buf.value
    return s


def exe_from_command(cmd: str) -> str:
    """Best-effort executable path from a command line / ImagePath."""
    path = _exe_from_command(cmd)
    if path and not os.path.isabs(path):
        import shutil
        path = shutil.which(path) or path
    return path


def _exe_from_command(cmd: str) -> str:
    cmd = os.path.expandvars(cmd.strip())
    if cmd.startswith('"'):
        return cmd[1:].split('"', 1)[0]
    low = cmd.lower()
    for ext in (".exe", ".dll", ".sys", ".bat", ".cmd", ".com"):
        i = low.find(ext)
        if i != -1:
            path = cmd[: i + len(ext)]
            if path.lower().startswith("\\systemroot\\"):
                path = os.environ["WINDIR"] + path[11:]
            elif path.startswith("\\??\\"):
                path = path[4:]
            return path
    return cmd.split(" ", 1)[0]


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)
