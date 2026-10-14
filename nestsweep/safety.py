"""Guards that every destructive operation goes through.

The rules here are deliberately conservative. When in doubt, a path or item is treated as
protected and skipped, because a cleaner that leaves 50 MB behind is fine and one that deletes
the wrong folder is not.
"""
from __future__ import annotations

import ctypes
import os
import re
import stat
from functools import lru_cache

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


# ---------------------------------------------------------------- path normalisation

def long_path(path: str) -> str:
    """Expand 8.3 short names (C:\\PROGRA~1 -> C:\\Program Files)."""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetLongPathNameW(path, buf, 32768)
        if 0 < n < 32768:
            return buf.value
    except (AttributeError, OSError):
        pass
    return path


def norm(path: str) -> str:
    """Absolute, long-form, symlink/junction-resolved, case-folded path without a trailing slash."""
    if not path:
        return ""
    p = os.path.expandvars(str(path).strip().strip('"'))
    p = os.path.realpath(long_path(os.path.abspath(p)))
    p = os.path.normcase(p)
    return p if len(p) <= 3 else p.rstrip("\\/")


def is_under(path: str, folder: str) -> bool:
    """True if `path` is strictly inside `folder`, after resolving junctions and short names."""
    p, f = norm(path), norm(folder)
    return bool(p and f) and p.startswith(f.rstrip("\\") + "\\")


def is_reparse(path_or_entry) -> bool:
    """Junctions, symlinks and other reparse points. os.walk/islink miss junctions on Windows."""
    try:
        st = path_or_entry.stat(follow_symlinks=False) if isinstance(path_or_entry, os.DirEntry) \
            else os.lstat(path_or_entry)
    except OSError:
        return True   # can't tell: treat as unsafe
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def walk(top: str, recurse: bool = True):
    """os.walk replacement that never descends into junctions or symlinks and never yields them."""
    stack = [top]
    while stack:
        root = stack.pop()
        dirs, files = [], []
        try:
            with os.scandir(root) as it:
                for e in it:
                    if is_reparse(e):
                        continue
                    try:
                        (dirs if e.is_dir(follow_symlinks=False) else files).append(e.name)
                    except OSError:
                        continue
        except OSError:
            continue
        yield root, dirs, files
        if recurse:
            stack.extend(os.path.join(root, d) for d in reversed(dirs))


# ---------------------------------------------------------------- protected folders

def _known_folder(fid: str) -> str:
    try:
        import uuid
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(fid).bytes_le)
        buf = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buf)) == 0:
            path = buf.value
            ctypes.windll.ole32.CoTaskMemFree(buf)
            return path or ""
    except (AttributeError, OSError, ValueError):
        pass
    return ""


_KNOWN = [
    "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",   # Desktop
    "FDD39AD0-238F-46AF-ADB4-6C85480369C7",   # Documents
    "374DE290-123F-4565-9164-39C4925E467B",   # Downloads
    "33E28130-4E1E-4676-835A-98395C3BC3BB",   # Pictures
    "4BD8D571-6D19-48D3-BE97-422220080E43",   # Music
    "18989B1D-99B5-455B-841C-AB7C74E4DDFC",   # Videos
    "A520A1A4-1780-4FF6-BD18-167343C5AF16",   # LocalLow
    "A77F5D77-2E2B-44C3-A6A2-ABA601054A51",   # Start Menu\Programs
    "0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8",   # Common Start Menu\Programs
    "B97D20BB-F46A-4C97-BA10-5E3608430854",   # Startup
    "82A5EA35-D9CD-47C5-9629-E15D2F714E6E",   # Common Startup
]


@lru_cache(maxsize=1)
def protected_folders() -> frozenset[str]:
    env = os.environ.get
    windir = env("SystemRoot") or env("WINDIR") or r"C:\Windows"
    profile = env("USERPROFILE") or ""
    users = os.path.dirname(profile) if profile else r"C:\Users"
    raw = [
        windir, os.path.join(windir, "System32"), os.path.join(windir, "SysWOW64"), os.path.join(windir, "WinSxS"),
        os.path.join(windir, "Temp"),
        env("ProgramFiles"), env("ProgramFiles(x86)"), env("ProgramW6432"),
        env("CommonProgramFiles"), env("CommonProgramFiles(x86)"), env("CommonProgramW6432"),
        env("ProgramData"), os.path.join(env("ProgramData") or "", "Microsoft"),
        users, os.path.join(users, "Public"), os.path.join(users, "Default"),
        profile, env("APPDATA"), env("LOCALAPPDATA"), env("TEMP"), env("TMP"),
        os.path.join(env("APPDATA") or "", "Microsoft"), os.path.join(env("LOCALAPPDATA") or "", "Microsoft"),
        os.path.join(env("LOCALAPPDATA") or "", "Programs"),
        os.path.join(env("LOCALAPPDATA") or "", "Packages"),
        *(_known_folder(f) for f in _KNOWN),
    ]
    return frozenset(n for n in (norm(p) for p in raw if p) if n)


def is_drive_root(path: str) -> bool:
    p = norm(path)
    return len(p) <= 3 or bool(re.fullmatch(r"[a-z]:\\?", p)) or p.startswith("\\\\") and p.count("\\") <= 3


def is_protected_dir(path: str) -> bool:
    """A folder that must never be deleted, emptied wholesale, or used as an install location:
    drive roots, Windows, Program Files and Common Files roots, ProgramData, the Users folder,
    any profile root, AppData roots, Temp roots and the user's known folders, or any ancestor
    of those."""
    p = norm(path)
    if not p or is_drive_root(p):
        return True
    for prot in protected_folders():
        if p == prot or prot.startswith(p + "\\"):
            return True
    # Every profile on the PC, not just the current one (an admin elevating for a standard user
    # runs with the admin's profile): profile roots, AppData roots and personal folders.
    users = os.path.dirname(norm(os.environ.get("USERPROFILE", r"C:\Users\x")))
    rel = p[len(users) + 1:] if p.startswith(users + "\\") else None
    if rel is not None and (rel.count("\\") == 0 or re.fullmatch(
            r"[^\\]+\\(appdata(\\(local|roaming|locallow))?|" + _PERSONAL + r")", rel)):
        return True
    return False


_PERSONAL = r"(desktop|documents|pictures|videos|music|downloads|saved games|favorites|contacts|onedrive[^\\]*)"


def in_personal_folder(path: str) -> bool:
    """Inside anyone's Desktop, Documents, Pictures, Videos, Music, Downloads, Saved Games or
    OneDrive, or the current user's known folders wherever they've been redirected."""
    p = norm(path)
    users = os.path.dirname(norm(os.environ.get("USERPROFILE", r"C:\Users\x")))
    if p.startswith(users + "\\") and re.match(r"[^\\]+\\" + _PERSONAL + r"(\\|$)", p[len(users) + 1:]):
        return True
    return any(k and (p == k or p.startswith(k + "\\")) for k in (norm(_known_folder(f)) for f in _KNOWN[:6]))


def under_program_files(path: str) -> bool:
    p = norm(path)
    roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    roots.append(os.path.join(os.environ.get("LOCALAPPDATA") or "", "Programs"))
    return any(r and p.startswith(norm(r) + "\\") for r in roots if r)


def recycle(path: str) -> None:
    """Send a file or folder to the Recycle Bin, so a mistake can be undone from the desktop.
    If something is too big for the bin, Windows asks before deleting it permanently."""
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI, FOF_WANTNUKEWARNING = 0x4, 0x10, 0x40, 0x400, 0x4000
    op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(path) + "\0", None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI | FOF_WANTNUKEWARNING,
                         False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted or os.path.lexists(path):
        raise OSError(f"couldn't move {path} to the Recycle Bin (code {rc})")


def is_inside_windows(path: str) -> bool:
    windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    p, w = norm(path), norm(windir)
    return p == w or p.startswith(w + "\\")


def safe_install_location(loc: str, other_locations: list[str] = ()) -> str | None:
    """Return the normalised install folder if it is specific enough to act on, else None.

    Rejects protected folders and anything inside Windows, and rejects a folder that contains
    another installed program's folder (a shared vendor folder or a parent like Common Files).
    """
    if not loc:
        return None
    raw = os.path.expandvars(str(loc).strip().strip('"'))
    p = norm(loc)
    if not p or not os.path.isdir(p) or is_reparse(raw) or is_protected_dir(p) or is_inside_windows(p):
        return None
    for other in other_locations:
        o = norm(other)
        if o and o != p and o.startswith(p + "\\"):
            return None
    return p


# ---------------------------------------------------------------- services, tasks and binaries

def is_microsoft_binary(path: str) -> bool:
    from . import common
    try:
        return os.path.isfile(path) and common.file_company(path).lower().startswith("microsoft")
    except OSError:
        return False


def is_critical_service(name: str, values: dict) -> bool:
    """Services that must not be disabled, deleted or switched to manual: kernel/file-system
    drivers, anything hosted in svchost or living in Windows, protected (anti-malware) services,
    and Microsoft binaries."""
    if values.get("Type") not in (0x10, 0x20, 0x50, 0x60):
        return True
    if values.get("LaunchProtected") not in (None, 0):
        return True
    from . import common
    img = str(values.get("ImagePath") or "")
    exe = common.exe_from_command(img)
    if not exe or "svchost.exe" in exe.lower() or is_inside_windows(exe) or is_microsoft_binary(exe):
        return True
    return False


def is_system_task(task_path: str) -> bool:
    return task_path.lower().startswith("\\microsoft\\")


TASK_PATH_RE = re.compile(r"^\\([^\\/:*?\"<>|]+\\)*$")


def check_task_ref(task_path: str, task_name: str) -> None:
    """Task Scheduler paths/names go to PowerShell through environment variables, but reject
    anything that isn't a plausible path anyway."""
    if not TASK_PATH_RE.match(task_path or "") or not task_name or any(c in task_name for c in "\\/\r\n\0"):
        raise ValueError(f"unexpected scheduled task reference {task_path!r} {task_name!r}")


def cleanup_base_ok(base: str) -> bool:
    """Is `base` a folder whose *contents* Cleanup may delete? Temp roots are allowed; drive roots,
    shallow paths, Windows, profiles, AppData roots, known folders and their ancestors are not.
    Guards against a bad %TEMP% (e.g. TEMP=D:\\ or TEMP=%USERPROFILE%)."""
    p = norm(base)
    if not p or is_drive_root(p) or p.count("\\") < 2:
        return False
    is_temp = os.path.basename(p) in ("temp", "tmp")
    for prot in protected_folders():
        if prot.startswith(p + "\\"):
            return False                       # an ancestor of something protected
        if p == prot and not is_temp:
            return False
    return True


# ---------------------------------------------------------------- programs Sleep mode must never touch

_SENSITIVE_WORDS = ("antivirus", "anti-virus", "security", "defender", "firewall", "malware", "endpoint",
                    "vpn", "audio", "sound", "maxxaudio", "waves", "nahimic", "backup", "bitlocker",
                    "encrypt", "password", "smartcard", "biometric", "fingerprint", "touchpad", "synaptics")


@lru_cache(maxsize=1)
def security_product_dirs() -> tuple[str, ...]:
    """Folders of the antivirus / firewall / anti-spyware products Windows Security Center knows."""
    from . import common
    dirs = []
    try:
        rows = common.powershell_json(
            "foreach ($c in 'AntiVirusProduct','FirewallProduct','AntiSpywareProduct') {"
            " Get-CimInstance -Namespace root/SecurityCenter2 -ClassName $c -ErrorAction SilentlyContinue |"
            " ForEach-Object { [pscustomobject]@{ A = [string]$_.pathToSignedProductExe; B = [string]$_.pathToSignedReportingExe } } }",
            timeout=60)
        for r in rows:
            for k in ("A", "B"):
                exe = os.path.expandvars(r.get(k) or "")
                if exe and os.path.isabs(exe):
                    dirs.append(norm(os.path.dirname(exe)))
    except Exception:
        pass
    return tuple(d for d in dirs if d and not is_protected_dir(d))


def is_sensitive_program(name: str, publisher: str, folder: str) -> bool:
    """Security software, VPNs, audio and input helpers and backup tools stay awake: putting them
    to sleep can switch off protection or break sound, networking or the touchpad."""
    text = f"{name} {publisher}".lower()
    if any(w in text for w in _SENSITIVE_WORDS):
        return True
    f = norm(folder) if folder else ""
    return bool(f) and any(f == d or d.startswith(f + "\\") or f.startswith(d + "\\") for d in security_product_dirs())


def is_sensitive_service(name: str, values: dict) -> bool:
    text = " ".join(str(values.get(k) or "") for k in ("DisplayName", "Group", "ImagePath", "Description")) + " " + name
    if any(w in text.lower() for w in _SENSITIVE_WORDS):
        return True
    deps = values.get("DependOnService") or []
    deps = [deps] if isinstance(deps, str) else list(deps)
    return any(str(d).lower() in ("audiosrv", "audioendpointbuilder", "ndisuio", "rasman", "bfe", "mpssvc") for d in deps)
