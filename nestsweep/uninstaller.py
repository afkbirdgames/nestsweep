"""Uninstaller: list installed software, run its own uninstaller, then offer to sweep leftovers.

Flow: launch the vendor uninstaller -> wait for it -> check the program is really gone ->
look for leftover tasks, services, registry keys, shortcuts and folders -> the user confirms
each item -> registry keys are exported first, then everything confirmed is removed.

Leftover detection is conservative (see safety.py): install folders that are too broad,
shared with another program, or inside Windows are ignored; Microsoft services/tasks and
anything that also matches another installed program are never offered.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import winreg
from dataclasses import dataclass, asdict, field

from . import common, safety

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
SOURCES = [
    (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
    (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
    (winreg.HKEY_CURRENT_USER, 0),
]


@dataclass
class Software:
    id: str
    name: str
    version: str
    publisher: str
    install_location: str
    install_date: str
    size_bytes: int
    uninstall_string: str
    quiet_uninstall_string: str
    is_store_app: bool = False
    reg_path: str = ""
    reg_view: int = 0


@dataclass
class Leftover:
    kind: str          # folder | registry | service | task | shortcut
    target: str
    detail: dict = field(default_factory=dict)


def scan(include_store_apps: bool = True) -> list[Software]:
    out: dict[str, Software] = {}
    for hive, view in SOURCES:
        for key in common.reg_subkeys(hive, UNINSTALL, view):
            path = f"{UNINSTALL}\\{key}"
            v = {k: val for k, (val, _) in common.reg_values(hive, path, view).items()}
            name = v.get("DisplayName")
            if not name or v.get("SystemComponent") == 1 or v.get("ParentKeyName") or v.get("ReleaseType") in ("Update", "Hotfix", "Security Update"):
                continue
            if not (v.get("UninstallString") or v.get("QuietUninstallString")):
                continue
            sid = f"{common.HIVE_NAMES[hive]}:{view}:{key}"
            dedupe = (str(name).lower(), str(v.get("DisplayVersion", "")))
            if any((s.name.lower(), s.version) == dedupe for s in out.values()):
                continue
            out[sid] = Software(
                sid, str(name), str(v.get("DisplayVersion", "")), str(v.get("Publisher", "")),
                str(v.get("InstallLocation", "")).strip('"'), _fmt_date(v.get("InstallDate")),
                int(v.get("EstimatedSize", 0) or 0) * 1024,
                str(v.get("UninstallString", "")), str(v.get("QuietUninstallString", "")),
                False, f"{common.HIVE_NAMES[hive]}\\{path}", view,
            )
    items = list(out.values())
    if include_store_apps:
        items += _store_apps()
    return sorted(items, key=lambda s: s.name.lower())


def _fmt_date(d) -> str:
    d = str(d or "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if re.fullmatch(r"\d{8}", d) else d


def _store_publisher(cert: str) -> str:
    """Store packages are signed CN=<GUID> by the Store; show that as "Microsoft Store"."""
    cn = re.sub(r"^CN=([^,]+).*", r"\1", cert)
    return "Microsoft Store" if re.fullmatch(r"[0-9A-Fa-f-]{36}", cn) else cn


def _store_apps() -> list[Software]:
    try:
        rows = common.powershell_json(
            "Get-AppxPackage | Where-Object { -not $_.IsFramework -and $_.SignatureKind -eq 'Store' -and -not $_.NonRemovable } |"
            " Select-Object Name, PackageFullName, Version, Publisher, InstallLocation", timeout=120)
    except Exception:
        return []
    return [Software("appx:" + r["PackageFullName"], r["Name"], r["Version"],
                     _store_publisher(r.get("Publisher") or ""), r.get("InstallLocation") or "",
                     "", 0, "", "", True) for r in rows]


# ---------------------------------------------------------------- remove

def run_uninstaller(sw: Software, quiet: bool = False, progress=None) -> int:
    """Run the vendor uninstaller and wait for it (and children it spawns) to exit."""
    _remember_location(sw)
    if progress:
        progress("launching_installer")
    if sw.is_store_app:
        common.powershell("Remove-AppxPackage -Package $env:NS_PKG", args={"pkg": sw.id[5:]}, strict=True)
        return 0
    cmd = sw.quiet_uninstall_string if quiet and sw.quiet_uninstall_string else sw.uninstall_string
    # MsiExec /I{GUID} opens a modify dialog; /X{GUID} removes.
    cmd = re.sub(r"(?i)(msiexec(?:\.exe)?\s+)/I\s*(\{)", r"\1/X\2", cmd)
    # Uninstall strings are command lines written by the vendor's installer, so they go through
    # cmd.exe as Windows itself runs them, but via the System32 copy with AutoRun disabled (/d).
    before = _helpers()
    proc = subprocess.Popen(f'"{common.sys32("cmd")}" /d /s /c "{cmd}"')
    if progress:
        progress("waiting_for_installer")
    proc.wait()
    # Many uninstallers re-launch themselves from %TEMP% and exit immediately (Au_.exe, Un_A.exe ...).
    deadline = time.time() + 600
    while time.time() < deadline and (_helpers() - before):
        time.sleep(1)
    return proc.returncode


def _helpers() -> set[tuple[str, str]]:
    """Running uninstaller helper processes as (name, pid), so only ones started by this
    uninstall are waited for."""
    cp = common.run(["tasklist", "/fo", "csv", "/nh"])
    out = set()
    for line in cp.stdout.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[0].lower() in {"au_.exe", "un_a.exe", "un_b.exe", "uninstall.exe", "unins000.exe"}:
            out.add((parts[0].lower(), parts[1]))
    return out


def _remember_location(sw: Software) -> None:
    common.journal_add("uninstaller", sw.id, "remove", asdict(sw))


def still_installed(sw: Software) -> bool:
    """After the vendor uninstaller exits: is the program still registered? (cancelled or failed)"""
    if sw.is_store_app:
        try:
            return bool(common.powershell_json("Get-AppxPackage | Where-Object { $_.PackageFullName -eq $env:NS_PKG }",
                                               args={"pkg": sw.id[5:]}))
        except Exception:
            return True
    hive_name, _, sub = sw.reg_path.partition("\\")
    return bool(sub) and common.reg_key_exists(common.HIVES[hive_name], sub, sw.reg_view)


def find_leftovers(sw: Software, others: list[Software] = ()) -> list[Leftover]:
    """Look for what the vendor uninstaller left behind. `others` are the other installed
    programs; anything that could belong to one of them is never offered.

    Each leftover carries detail["suggested"]: True only for items that clearly belonged to this
    program (its own install folder, its registry keys, its shortcuts). Name-matched folders,
    services and tasks are listed unticked so the user decides.
    """
    if sw.is_store_app:
        return []
    out: list[Leftover] = []
    tokens = _tokens(sw)
    other_locs = [o.install_location for o in others if o.install_location and o.id != sw.id]
    other_names = set()
    for o in others:
        if o.id != sw.id:
            other_names |= _tokens(o)["name"]
    loc = safety.safe_install_location(sw.install_location, other_locs)
    # Other programs' folders, but only the specific ones: a program that (wrongly) reports
    # "C:\\Program Files" as its location must not make everything inside look like it's taken.
    other_safe = [n for n in (safety.safe_install_location(o) for o in other_locs) if n]

    def owned_by_other(path: str, own_folder: bool = False) -> bool:
        n = safety.norm(path)
        for o in other_safe:
            if n == o or o.startswith(n + "\\") or n.startswith(o + "\\"):
                return True
        # a folder found only by name is skipped when another installed program has the same name
        return not own_folder and _matches(os.path.basename(n), other_names)

    # 1. registry: Software\<Publisher>\<Product>  (the Uninstall key itself is gone by now)
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for vendor in common.reg_subkeys(hive, "Software"):
            if vendor.lower() in tokens["publisher"] and vendor.lower() not in ("microsoft", "classes", "policies", "wow6432node"):
                for prod in common.reg_subkeys(hive, f"Software\\{vendor}"):
                    if _matches(prod, tokens["name"]) and not _matches(prod, other_names):
                        out.append(Leftover("registry", f"{common.HIVE_NAMES[hive]}\\Software\\{vendor}\\{prod}",
                                            {"view": 0, "suggested": True}))
    # 2. folders: the install folder + AppData/ProgramData/Program Files folders named after the product
    cands: dict[str, bool] = {}
    if loc:
        cands[sw.install_location.strip().strip('"').rstrip("\\")] = True
    for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMDATA"),
                 os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if not base or not os.path.isdir(base):
            continue
        for d in os.listdir(base):
            if _matches(d, tokens["name"]):
                cands.setdefault(os.path.join(base, d), False)
            elif d.lower() in tokens["publisher"]:
                pub = os.path.join(base, d)
                if os.path.isdir(pub) and not safety.is_reparse(pub):
                    for x in os.listdir(pub):
                        if _matches(x, tokens["name"]):
                            cands.setdefault(os.path.join(pub, x), False)
    seen = set()
    for c, suggested in cands.items():
        key = safety.norm(c)
        if key in seen:
            continue
        seen.add(key)
        if (os.path.isdir(c) and not safety.is_reparse(c) and not safety.is_protected_dir(c)
                and not safety.is_inside_windows(c) and not owned_by_other(c, own_folder=(key == loc))):
            out.append(Leftover("folder", c, {"size": _dir_size(c), "suggested": suggested}))
    # 3. services and scheduled tasks whose program lives in the (verified) install folder
    if loc:
        base = r"SYSTEM\CurrentControlSet\Services"
        for svc in common.reg_subkeys(winreg.HKEY_LOCAL_MACHINE, base):
            v = {k: val for k, (val, _) in common.reg_values(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{svc}").items()}
            img = str(v.get("ImagePath") or "")
            if img and safety.is_under(common.exe_from_command(img), loc) and not safety.is_critical_service(svc, v):
                out.append(Leftover("service", svc, {"suggested": False}))
        try:
            for t in common.powershell_json(
                    "Get-ScheduledTask | ForEach-Object { $e=($_.Actions|Select-Object -First 1).Execute;"
                    " [pscustomobject]@{Path=$_.TaskPath;Name=$_.TaskName;Exec=[string]$e} }"):
                if (t["Exec"] and not safety.is_system_task(t["Path"])
                        and safety.is_under(os.path.expandvars(t["Exec"].strip('"')), loc)):
                    out.append(Leftover("task", t["Path"] + t["Name"], {"path": t["Path"], "name": t["Name"], "suggested": False}))
        except Exception:
            pass
    # 4. Start Menu shortcuts named after the product
    for menu in (os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
                 os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs")):
        if not os.path.isdir(menu):
            continue
        for root, _, files in safety.walk(menu):
            for f in files:
                if f.lower().endswith(".lnk") and _matches(f[:-4], tokens["name"]) and not _matches(f[:-4], other_names):
                    out.append(Leftover("shortcut", os.path.join(root, f), {"suggested": True}))
    return out


def _tokens(sw: Software) -> dict:
    name = re.sub(r"\s*[\d.]+(\s*\(.*\))?$", "", sw.name).strip().lower()
    pub = sw.publisher.lower()
    pub_words = {pub, pub.split(",")[0].strip(), pub.split(" ")[0]} - {""}
    return {"name": {name, name.replace(" ", "")}, "publisher": pub_words}


def _matches(candidate: str, names: set[str]) -> bool:
    c = candidate.lower()
    return any(n and len(n) >= 4 and (c == n or c.replace(" ", "") == n) for n in names)


def _dir_size(p: str) -> int:
    total = 0
    for root, _, files in safety.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def remove_leftovers(leftovers: list[Leftover], progress=None) -> dict:
    stats = {"removed": 0, "errors": []}
    order = {"task": 0, "service": 1, "registry": 2, "shortcut": 3, "folder": 4}
    for lo in sorted(leftovers, key=lambda l: order[l.kind]):
        if progress:
            progress({"task": "deleting_tasks", "service": "deleting_services", "registry": "deleting_registry_keys",
                      "shortcut": "deleting_startup_objects", "folder": "deleting_files"}[lo.kind])
        try:
            if lo.kind == "task":
                safety.check_task_ref(lo.detail["path"], lo.detail["name"])
                if safety.is_system_task(lo.detail["path"]):
                    raise PermissionError("Windows tasks are never removed")
                common.powershell("$t = @(Get-ScheduledTask | Where-Object { $_.TaskPath -eq $env:NS_PATH -and"
                                  " $_.TaskName -eq $env:NS_NAME }); if ($t.Count -ne 1) { throw 'task not found' };"
                                  " $t[0] | Unregister-ScheduledTask -Confirm:$false",
                                  args={"path": lo.detail["path"], "name": lo.detail["name"]}, strict=True)
            elif lo.kind == "service":
                v = {k: val for k, (val, _) in common.reg_values(
                    winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Services\\{lo.target}").items()}
                if safety.is_critical_service(lo.target, v):
                    raise PermissionError("protected service")
                common.run(["sc", "stop", lo.target])
                cp = common.run(["sc", "delete", lo.target])
                if cp.returncode:
                    raise OSError(cp.stdout.strip())
            elif lo.kind == "registry":
                view = lo.detail.get("view", 0)
                if not common.reg_export(lo.target, "leftover_" + lo.target, view):
                    raise OSError("couldn't back up the key, so it was left alone")
                root, sub = common.split_key(lo.target)
                common.reg_delete_tree(root, sub, view)
            elif lo.kind == "shortcut":
                os.remove(lo.target)
            elif lo.kind == "folder":
                if (safety.is_reparse(lo.target) or safety.is_protected_dir(lo.target)
                        or safety.is_inside_windows(lo.target)):
                    raise PermissionError("protected folder")
                shutil.rmtree(lo.target, ignore_errors=False)   # rmtree removes junctions without following them
            stats["removed"] += 1
        except Exception as e:
            stats["errors"].append(f"{lo.kind} {lo.target}: {e}")
    common.journal_add("uninstaller", "leftovers", "cleanup", {"removed": stats["removed"], "items": [asdict(l) for l in leftovers]})
    return stats
