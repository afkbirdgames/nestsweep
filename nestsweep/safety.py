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


# ---------------------------------------------------------------- deleting by handle

class _Win32:
    """The kernel32 calls the handle-based delete needs, on a private kernel32 instance so the
    argtypes set here can't clash with other ctypes users. Tests swap in a fake (see _win32)."""
    DELETE, FILE_READ_ATTRIBUTES = 0x00010000, 0x80
    SHARE_READ, SHARE_WRITE, SHARE_DELETE = 0x1, 0x2, 0x4
    OPEN_EXISTING = 3
    OPEN_REPARSE_POINT, BACKUP_SEMANTICS = 0x00200000, 0x02000000
    VOLUME_NAME_NT = 0x2
    FileBasicInfo, FileDispositionInfo, FileDispositionInfoEx = 0, 4, 21
    DISPOSITION_DELETE, DISPOSITION_POSIX = 0x1, 0x2

    def __init__(self):
        from ctypes import wintypes as w
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._create = k.CreateFileW
        self._create.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        self._create.restype = w.HANDLE
        self._final = k.GetFinalPathNameByHandleW
        self._final.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
        self._final.restype = w.DWORD
        self._get = k.GetFileInformationByHandleEx
        self._get.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self._get.restype = w.BOOL
        self._set = k.SetFileInformationByHandle
        self._set.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self._set.restype = w.BOOL
        self._close = k.CloseHandle
        self._close.argtypes = [w.HANDLE]
        self._close.restype = w.BOOL

    @staticmethod
    def _fail(code=None):
        raise ctypes.WinError(code if code is not None else ctypes.get_last_error())

    def _open(self, path: str, access: int, share: int, flags: int):
        h = self._create(path, access, share, None, self.OPEN_EXISTING, flags, None)
        if h is None or h == ctypes.c_void_p(-1).value:
            self._fail()
        return h

    def open(self, path: str, is_dir: bool):
        """The item itself, not whatever a junction or link at the end of the path points to."""
        flags = self.OPEN_REPARSE_POINT | (self.BACKUP_SEMANTICS if is_dir else 0)
        return self._open(path, self.DELETE | self.FILE_READ_ATTRIBUTES,
                          self.SHARE_READ | self.SHARE_WRITE | self.SHARE_DELETE, flags)

    def open_folder(self, path: str):
        """A rule folder, held open without delete sharing: while the handle is open nobody can
        rename, delete or replace that folder itself."""
        return self._open(path, self.FILE_READ_ATTRIBUTES, self.SHARE_READ | self.SHARE_WRITE,
                          self.BACKUP_SEMANTICS)

    def attributes(self, h) -> int:
        class FILE_BASIC_INFO(ctypes.Structure):
            _fields_ = [("c", ctypes.c_longlong), ("a", ctypes.c_longlong), ("w", ctypes.c_longlong),
                        ("ch", ctypes.c_longlong), ("attrs", ctypes.c_ulong)]
        info = FILE_BASIC_INFO()
        if not self._get(h, self.FileBasicInfo, ctypes.byref(info), ctypes.sizeof(info)):
            self._fail()
        return info.attrs

    def final_path(self, h) -> str:
        """Where the opened object really is, as an NT path (\\Device\\HarddiskVolume3\\...).
        NT paths work on every volume, including RAM disks and virtual drives with no mount
        manager entry, where asking for a drive-letter path fails."""
        buf = ctypes.create_unicode_buffer(32768)
        n = self._final(h, buf, 32768, self.VOLUME_NAME_NT)
        if not 0 < n < 32768:
            self._fail()
        return buf.value

    def mark_delete(self, h) -> None:
        """POSIX-style delete (the name goes at once, like DeleteFileW on Windows 10 1809+), or the
        classic delete-on-close where the file system doesn't support it."""
        flags = ctypes.c_ulong(self.DISPOSITION_DELETE | self.DISPOSITION_POSIX)
        if self._set(h, self.FileDispositionInfoEx, ctypes.byref(flags), ctypes.sizeof(flags)):
            return
        err = ctypes.get_last_error()
        if err not in (1, 50, 87):          # invalid function, not supported, invalid parameter
            self._fail(err)
        flag = ctypes.c_ubyte(1)            # FILE_DISPOSITION_INFO.DeleteFile
        if not self._set(h, self.FileDispositionInfo, ctypes.byref(flag), ctypes.sizeof(flag)):
            self._fail()

    def close(self, h) -> None:
        self._close(h)


_WIN32 = None


def _win32():
    global _WIN32
    if _WIN32 is None:
        if os.name != "nt":
            raise OSError("deleting by handle needs Windows")
        _WIN32 = _Win32()
    return _WIN32


FILE_ATTRIBUTE_DIRECTORY = 0x10


def _win_path(path: str) -> str:
    p = os.path.abspath(path)
    if os.name == "nt" and len(p) >= 248 and not p.startswith("\\\\"):
        p = "\\\\?\\" + p
    return p


def _real(w, h) -> str:
    return os.path.normcase(w.final_path(h)).rstrip("\\/")


def folder_id(path: str) -> str:
    """The real location of a folder (as an NT path, following junctions), recorded when Cleanup
    scans a rule folder and checked again when it cleans it. Raises OSError if it can't be read."""
    w = _win32()
    h = w.open_folder(_win_path(path))
    try:
        if not w.attributes(h) & FILE_ATTRIBUTE_DIRECTORY:
            raise OSError(f"{path} isn't a folder")
        return _real(w, h)
    finally:
        w.close(h)


class PinnedFolder:
    """Hold a rule folder open while its contents are deleted, after checking it is still the
    folder that was scanned. Use as a context manager, or call close(). Raises PermissionError
    if another program's open handle stops it being held; delete_inside's own check against the
    scanned location still applies then."""

    def __init__(self, path: str, expected_id: str):
        if not expected_id:
            raise OSError(f"{path} wasn't identified when it was scanned")
        self._w = _win32()
        self._h = self._w.open_folder(_win_path(path))
        try:
            if _real(self._w, self._h) != expected_id:
                raise OSError(f"{path} isn't the folder that was scanned any more, so it was left alone")
        except BaseException:
            self.close()
            raise
        self.id = expected_id

    def close(self) -> None:
        if self._h is not None:
            self._w.close(self._h)
            self._h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def delete_inside(path: str, base_id: str, is_dir: bool = False, may_be_base: bool = False) -> None:
    """Delete one plain file, or one empty folder, only if the object actually opened is inside
    the rule folder `base_id` (from folder_id) at the moment it is deleted.

    Checking a path and then deleting it by name leaves a gap: a folder on the way can be swapped
    for a junction in between, and the delete then lands wherever the junction points (a real
    risk when Nestsweep runs as administrator over a folder users can write to, like
    C:\\Windows\\Temp). So the item is opened first, without following a reparse point at the
    end, its real location is read back from that same handle, and the delete is set on the
    handle. Whatever the path turns into afterwards, only the opened object can be deleted.
    Keep the rule folder pinned (PinnedFolder) meanwhile so it can't be swapped either.
    Any failure raises and nothing is deleted. A folder is only deleted when it is empty
    (Windows refuses otherwise); with may_be_base it may be the rule folder itself."""
    if not base_id or not path:
        raise OSError(f"no rule folder to check {path} against")
    w = _win32()
    h = w.open(_win_path(path), is_dir)
    try:
        attrs = w.attributes(h)
        if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(f"{path} is a junction or link now, so it was left alone")
        if bool(attrs & FILE_ATTRIBUTE_DIRECTORY) != is_dir:
            raise OSError(f"{path} changed type since the scan, so it was left alone")
        real = _real(w, h)
        inside = real.startswith(base_id + os.sep) or (is_dir and may_be_base and real == base_id)
        if not inside:
            raise OSError(f"{path} now leads outside its folder, so it was left alone")
        w.mark_delete(h)
    finally:
        w.close(h)


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

    Rejects relative and network paths, protected folders and anything inside Windows, and rejects a folder that contains
    another installed program's folder (a shared vendor folder or a parent like Common Files).
    """
    if not loc:
        return None
    raw = os.path.expandvars(str(loc).strip().strip('"'))
    if not re.match(r"[A-Za-z]:[\\/]", raw):
        return None            # relative ("." or "Acme"), drive-relative ("C:Acme") or a network path
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


def _is_profile_root(p: str) -> bool:
    users = os.path.dirname(norm(os.environ.get("USERPROFILE", r"C:\Users\x")))
    return os.path.dirname(p) == users


def cleanup_base_ok(base: str) -> bool:
    """Is `base` a folder whose *contents* Cleanup may delete? Temp roots are allowed; drive roots,
    shallow paths, Windows, profiles, AppData roots, known folders and their ancestors are not.
    Guards against a bad %TEMP% (e.g. TEMP=D:\\ or TEMP=%USERPROFILE%)."""
    p = norm(base)
    if not p or is_drive_root(p) or p.count("\\") < 2:
        return False
    # Never anyone's profile root, even one called TEMP: Windows signs people in to
    # C:\Users\TEMP when their own profile fails to load.
    if _is_profile_root(p):
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
