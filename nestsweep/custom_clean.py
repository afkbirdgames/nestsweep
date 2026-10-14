"""Cleanup: rule-driven junk and privacy cleaning.

Rules live in rules.ini (a winapp2-style format):
  [Rule Name]
  ID=windows.tempfiles          stable identifier the UI selects by
  Default=True                  pre-checked in the UI
  Tags=...                      grouping
  Detect=HKCU\\...  DetectFile=%path%  DetectOS=6.0   rule only shown if present
  FileKeyN=<folder>|<mask;mask>[|RECURSE|REMOVESELF]
  RegKeyN=<HIVE\\key>[|<value>]
  ExcludeKeyN=FILE|<path>   or   PATH|<folder>[|<mask>]
  ActionN=EmptyRecycleBin | CleanClipboard | CleanDNSCache | RemoveEmptyDirectories|<folder>
  ActiveProcesses=a.exe,b.exe   app must be closed first
  [Rule Vars]  NAME=path,path    referenced as <NAME> inside paths

Safety: folders are walked without following junctions or symlinks, a rule whose path uses
an unset environment variable is skipped, recursive rules refuse dangerous base folders (see
safety.cleanup_base_ok), and every path is re-checked right before it is deleted.
"""
from __future__ import annotations

import configparser
import ctypes
import fnmatch
import glob
import os
import re
import subprocess
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from . import common, safety

RULES_FILE = Path(__file__).with_name("rules.ini")


@dataclass
class Rule:
    name: str
    id: str
    default: bool
    tags: list[str]
    file_keys: list[tuple[str, list[str], set[str]]] = field(default_factory=list)
    reg_keys: list[tuple[str, str | None]] = field(default_factory=list)
    exclude: list[tuple[str, str, str | None]] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    detect: list[str] = field(default_factory=list)
    detect_file: list[str] = field(default_factory=list)
    detect_os: str | None = None
    active_processes: list[str] = field(default_factory=list)
    warning: str | None = None


@dataclass
class Finding:
    rule_id: str
    kind: str          # file | dir | reg | action
    target: str
    size: int = 0
    base: str = ""     # the rule folder this file/dir was found under


# ------------------------------------------------------------- variables

def _known_folder(fid: str) -> str:
    guid = ctypes.c_byte * 16
    buf = ctypes.c_wchar_p()
    g = guid.from_buffer_copy(__import__("uuid").UUID(fid).bytes_le)
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(buf)) == 0:
        path = buf.value
        ctypes.windll.ole32.CoTaskMemFree(buf)
        return path
    return ""


def _env_join(var: str, *rest: str) -> str:
    base = os.environ.get(var, "")
    return os.path.join(base, *rest) if base else ""


_SPECIAL = {
    "systemdirectory": lambda: _env_join("WINDIR", "System32"),
    "commonappdata": lambda: os.environ.get("PROGRAMDATA", ""),
    "locallowappdata": lambda: _known_folder("A520A1A4-1780-4FF6-BD18-167343C5AF16"),
    "recent": lambda: _env_join("APPDATA", r"Microsoft\Windows\Recent"),
    "documents": lambda: _known_folder("FDD39AD0-238F-46AF-ADB4-6C85480369C7"),
    "mydocuments": lambda: _known_folder("FDD39AD0-238F-46AF-ADB4-6C85480369C7"),
    "desktop": lambda: _known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641"),
    "startmenu": lambda: _env_join("APPDATA", r"Microsoft\Windows\Start Menu"),
    "commonstartmenu": lambda: _env_join("PROGRAMDATA", r"Microsoft\Windows\Start Menu"),
}


def expand(path: str) -> str:
    """Expand %VARS%. Returns "" if any variable is unset or empty, so a rule can never fall
    back to a different folder (an empty %WinDir% must not turn "\\Temp" into "C:\\Temp")."""
    missing = False

    def sub(m: re.Match) -> str:
        nonlocal missing
        name = m.group(1)
        f = _SPECIAL.get(name.lower())
        val = f() if f else os.environ.get(name, "")
        if not val:
            missing = True
        return val
    out = re.sub(r"%([A-Za-z_()0-9]+)%", sub, path)
    return "" if missing or "%" in out or "<" in out else out


def _expand_vars(value: str, rule_vars: dict[str, list[str]]) -> list[str]:
    """`<NAME>\\sub` -> one entry per path in [Rule Vars] NAME."""
    m = re.match(r"<([A-Z_]+)>(.*)", value)
    if not m or m.group(1) not in rule_vars:
        return [value]
    return [p + m.group(2) for p in rule_vars[m.group(1)]]


def _glob_dirs(folder: str) -> list[str]:
    """Folder paths may contain wildcards (e.g. `User Data\\*`)."""
    folder = expand(folder)
    if not folder or not os.path.isabs(folder):
        return []
    if any(c in folder for c in "*?"):
        found = glob.glob(folder)
        # the fixed part of the path, before the first wildcard
        static = os.path.dirname(folder[:min(i for i in (folder.find("*"), folder.find("?")) if i >= 0)])
    else:
        found, static = [folder], folder

    def plain(p: str) -> bool:
        """No junction/symlink anywhere between the fixed prefix and the matched folder."""
        cur = p
        while len(cur) > len(static):
            if safety.is_reparse(cur):
                return False
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return not safety.is_reparse(p)
    return [p for p in found if os.path.isdir(p) and plain(p)]


def _temp_base_ok(spec: str, base: str) -> bool:
    """A rule built on %TEMP%/%TMP% only runs if that variable really points at a Temp folder,
    so TEMP=D:\\Work can never be emptied."""
    if not spec.upper().startswith(("%TEMP%", "%TMP%")):
        return True
    return os.path.basename(safety.norm(base)) in ("temp", "tmp")


# ------------------------------------------------------------- parsing

def load_rules(path: Path | None = None) -> list[Rule]:
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read(path or RULES_FILE, encoding="utf-8-sig")
    rule_vars = {}
    if cp.has_section("Rule Vars"):
        rule_vars = {k: [p.strip() for p in v.split(",")] for k, v in cp.items("Rule Vars")}

    rules = []
    for sec in cp.sections():
        if sec == "Rule Vars":
            continue
        items = dict(cp.items(sec))
        r = Rule(
            name=sec,
            id=items.get("ID", sec.lower().replace(" ", "_")),
            default=items.get("Default", "False").lower() == "true",
            tags=[t for t in items.get("Tags", "").split(",") if t],
            detect_os=items.get("DetectOS"),
            warning=items.get("Warning"),
        )
        for k, v in sorted(items.items(), key=lambda kv: _keynum(kv[0])):
            base = re.sub(r"\d+$", "", k).lower()
            if base == "filekey":
                parts = v.split("|")
                masks = parts[1].split(";") if len(parts) > 1 else ["*"]
                flags = {p.strip().upper() for p in parts[2:]}
                for folder in _expand_vars(parts[0], rule_vars):
                    r.file_keys.append((folder, masks, flags))
            elif base == "regkey":
                key, _, val = v.partition("|")
                r.reg_keys.append((key, val or None))
            elif base == "excludekey":
                parts = v.split("|")
                r.exclude.append((parts[0].upper(), expand(parts[1]), parts[2] if len(parts) > 2 else None))
            elif base == "action":
                r.actions.append(v)
            elif base == "detect":
                r.detect.append(v)
            elif base == "detectfile":
                r.detect_file.extend(_expand_vars(v, rule_vars))
            elif base == "activeprocesses":
                r.active_processes = [p.strip().lower() for p in v.split(",") if p.strip()]
        rules.append(r)
    return rules


def _keynum(k: str) -> tuple[str, int]:
    m = re.match(r"(.*?)(\d+)$", k)
    return (m.group(1), int(m.group(2))) if m else (k, 0)


def is_applicable(r: Rule) -> bool:
    if r.detect_os:
        ver = tuple(int(x) for x in r.detect_os.split("|")[0].split(".") if x)
        win = __import__("sys").getwindowsversion()
        if (win.major, win.minor) < ver:
            return False
    if not r.detect and not r.detect_file:
        return True
    for d in r.detect:
        try:
            root, sub = common.split_key(d)
            if common.reg_key_exists(root, sub):
                return True
        except KeyError:
            pass
    return any(_glob_dirs(p) or os.path.exists(expand(p)) for p in r.detect_file)


# ------------------------------------------------------------- scanning

def _excluded(path: str, r: Rule) -> bool:
    lp = path.lower()
    for kind, target, mask in r.exclude:
        t = target.lower()
        if kind == "FILE" and fnmatch.fnmatch(lp, t):
            return True
        if kind == "PATH" and lp.startswith(t.rstrip("\\") + "\\"):
            if mask is None or fnmatch.fnmatch(os.path.basename(lp), mask.lower()):
                return True
    return False


def _broad(masks: list[str], flags: set[str]) -> bool:
    return bool(flags & {"RECURSE", "REMOVESELF"}) or any(m.strip() in ("*", "*.*") for m in masks)


def _scan_file_key(r: Rule, folder: str, masks: list[str], flags: set[str], out: list[Finding]) -> None:
    recurse = bool(flags & {"RECURSE", "REMOVESELF"})
    for base in _glob_dirs(folder):
        if safety.is_drive_root(base) or not _temp_base_ok(folder, base) or \
                (_broad(masks, flags) and not safety.cleanup_base_ok(base)):
            continue
        dirs_seen = []
        for root, dirs, files in safety.walk(base, recurse):
            dirs_seen.append(root)
            for f in files:
                if any(fnmatch.fnmatch(f.lower(), m.lower()) for m in masks):
                    p = os.path.join(root, f)
                    if _excluded(p, r):
                        continue
                    try:
                        out.append(Finding(r.id, "file", p, os.path.getsize(p), base))
                    except OSError:
                        pass
        if "REMOVESELF" in flags and not safety.is_protected_dir(base):
            # deepest first so empty parents can be removed after their children
            for d in sorted(dirs_seen, key=len, reverse=True):
                out.append(Finding(r.id, "dir", d, 0, base))
        elif recurse:
            for d in sorted(dirs_seen[1:], key=len, reverse=True):
                out.append(Finding(r.id, "dir", d, 0, base))


def _scan_reg_key(r: Rule, key: str, value: str | None, out: list[Finding]) -> None:
    try:
        root, sub = common.split_key(key)
    except KeyError:
        return
    if not common.reg_key_exists(root, sub):
        return
    if value is None:
        out.append(Finding(r.id, "reg", key))
    elif value in common.reg_values(root, sub):
        out.append(Finding(r.id, "reg", f"{key}|{value}"))


def recycle_bin_size() -> int:
    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]
    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO))
    ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    return info.i64Size


def analyse(rule_ids: list[str] | None = None, progress=None) -> list[Finding]:
    rules = [r for r in load_rules() if (rule_ids is None and r.default) or (rule_ids and r.id in rule_ids)]
    out: list[Finding] = []
    for i, r in enumerate(rules):
        if progress:
            progress(i, len(rules), r.name)
        if not is_applicable(r):
            continue
        for folder, masks, flags in r.file_keys:
            _scan_file_key(r, folder, masks, flags, out)
        for key, value in r.reg_keys:
            _scan_reg_key(r, key, value, out)
        for a in r.actions:
            size = recycle_bin_size() if a.split("|")[0] == "EmptyRecycleBin" else 0
            out.append(Finding(r.id, "action", a, size))
    return out


def running_blockers(rule_ids: list[str]) -> dict[str, list[str]]:
    """Rules whose ActiveProcesses are running (UI asks the user to close them)."""
    cp = common.run(["tasklist", "/fo", "csv", "/nh"])
    running = {line.split('","')[0].strip('"').lower() for line in cp.stdout.splitlines() if line}
    out = {}
    for r in load_rules():
        if r.id in rule_ids:
            hits = [p for p in r.active_processes if p in running]
            if hits:
                out[r.id] = hits
    return out


# ------------------------------------------------------------- cleaning

def _run_action(action: str) -> None:
    name, _, arg = action.partition("|")
    if name == "EmptyRecycleBin":
        SHERB_NOCONFIRMATION, SHERB_NOPROGRESSUI, SHERB_NOSOUND = 1, 2, 4
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND)
    elif name == "CleanClipboard":
        u = ctypes.windll.user32
        if u.OpenClipboard(None):
            u.EmptyClipboard()
            u.CloseClipboard()
    elif name == "CleanDNSCache":
        common.run(["ipconfig", "/flushdns"])
    elif name == "RemoveEmptyDirectories":
        for root_dir in arg.split("|"):
            top = expand(root_dir)
            if not top or not safety.cleanup_base_ok(top):
                continue
            for d, _, _ in sorted(safety.walk(top), key=lambda t: len(t[0]), reverse=True):
                if d != top:
                    try:
                        os.rmdir(d)          # only succeeds when empty
                    except OSError:
                        pass
    else:
        raise NotImplementedError(name)


def clean(findings: list[Finding], progress=None) -> dict:
    stats = {"files": 0, "bytes": 0, "errors": 0, "skipped_in_use": 0}
    backed_up = set()
    for key in {f.target.split("|")[0] for f in findings if f.kind == "reg"}:
        if common.reg_export(key, "cleanup_" + key):
            backed_up.add(key)
    for i, f in enumerate(findings):
        if progress and i % 50 == 0:
            progress(i, len(findings), f.target)
        if f.kind in ("file", "dir") and not _still_safe(f):
            stats["errors"] += 1
            continue
        if f.kind == "reg" and f.target.split("|")[0] not in backed_up:
            stats["errors"] += 1          # never delete a key we couldn't back up
            continue
        try:
            if f.kind == "file":
                os.remove(f.target)
                stats["files"] += 1
                stats["bytes"] += f.size
            elif f.kind == "dir":
                os.rmdir(f.target)          # only removes if now empty
            elif f.kind == "reg":
                key, _, value = f.target.partition("|")
                root, sub = common.split_key(key)
                if value:
                    with winreg.OpenKey(root, sub, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.DeleteValue(k, value)
                else:
                    common.reg_delete_tree(root, sub)
            elif f.kind == "action":
                _run_action(f.target)
                stats["bytes"] += f.size
        except PermissionError:
            stats["skipped_in_use"] += 1
        except OSError:
            stats["errors"] += 1
        except NotImplementedError:
            stats["errors"] += 1
    common.journal_add("custom_clean", "run", "clean", stats)
    return stats


def _still_safe(f: Finding) -> bool:
    """Re-check right before deleting: the item must still be a plain file/folder (not swapped
    for a junction or symlink since the analysis) and must still resolve inside its rule folder."""
    if not f.base or safety.is_reparse(f.target):
        return False
    return safety.is_under(f.target, f.base) or (f.kind == "dir" and safety.norm(f.target) == safety.norm(f.base))


def summarize(findings: list[Finding]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in findings:
        s = out.setdefault(f.rule_id, {"items": 0, "bytes": 0})
        if f.kind != "dir":
            s["items"] += 1
        s["bytes"] += f.size
    return out
