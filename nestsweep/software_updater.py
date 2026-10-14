"""App updates: find outdated applications (winget) and missing Windows patches (Windows Update).

Flow: scan -> the user picks -> each app is upgraded silently by winget, patches are
installed through the Windows Update Agent. Skipped versions and ignored apps are remembered.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass

from . import common, windows_update


@dataclass
class Product:
    id: str
    name: str
    current_version: str
    available_version: str
    source: str
    category: str = "application"     # application | windows-patch
    severity: str = "unknown"
    size_bytes: int = 0
    ignored: bool = False
    status: str = ""


_WINGET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def winget_path() -> str | None:
    """winget is an App Installer alias in the user's WindowsApps folder. Use that exact path,
    never a PATH/current-folder lookup, so a planted winget.exe can't run elevated."""
    import os
    alias = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "winget.exe")
    return alias if os.environ.get("LOCALAPPDATA") and os.path.exists(alias) else None


def winget_available() -> bool:
    return winget_path() is not None


def _parse_winget_table(text: str) -> list[dict]:
    lines = [l.rstrip() for l in text.splitlines()]
    # winget draws a spinner with \r; keep the last frame of each line
    lines = [l.split("\r")[-1] for l in lines]
    for i, l in enumerate(lines):
        if re.match(r"^-{10,}$", l.strip()) and i > 0:
            header = lines[i - 1]
            break
    else:
        return []
    cols = [(m.group(0).strip(), m.start()) for m in re.finditer(r"\S+\s*", header)]
    rows = []
    for l in lines[i + 1:]:
        if not l.strip() or re.match(r"^\d+ (upgrades?|packages?) ", l) or l.startswith(("The following", "No ")):
            continue
        row = {}
        for n, (name, start) in enumerate(cols):
            end = cols[n + 1][1] if n + 1 < len(cols) else None
            row[name] = l[start:end].strip() if len(l) > start else ""
        rows.append(row)
    return rows


def scan_apps() -> list[Product]:
    if not winget_available():
        raise FileNotFoundError("winget (App Installer) is not installed")
    cp = common.run([winget_path(), "upgrade", "--include-unknown", "--accept-source-agreements",
                     "--disable-interactivity"], timeout=300)
    skipped = common.setting_get("su_skipped", {})
    ignored = set(common.setting_get("su_ignored", []))
    out = []
    for r in _parse_winget_table(cp.stdout):
        pid, avail = r.get("Id", ""), r.get("Available", "")
        if not pid or not avail or not _WINGET_ID.match(pid):
            continue          # also drops ARP\\... / MSIX\\... entries winget can't upgrade by id
        if skipped.get(pid) == avail:
            continue
        out.append(Product(pid, r.get("Name", pid), r.get("Version", ""), avail, r.get("Source", "winget"),
                           ignored=pid in ignored))
    return out


def scan_patches() -> list[Product]:
    out = []
    for u in windows_update.search("Software"):
        sev = (u.get("Severity") or "").lower() or ("low" if u.get("Optional") else "moderate")
        out.append(Product(u["Id"], u["Title"], "", "KB" + u["KB"] if u.get("KB") else "", "Windows Update",
                           "windows-patch", sev, int(u.get("Size") or 0)))
    return out


def scan(include_patches: bool = True) -> list[Product]:
    items = scan_apps()
    if include_patches:
        try:
            items += scan_patches()
        except Exception:
            pass
    return items


def update(products: list[Product], progress=None) -> list[Product]:
    apps = [p for p in products if p.category == "application"]
    patches = [p for p in products if p.category == "windows-patch"]
    for n, p in enumerate(apps):
        if progress:
            progress(n, len(products), p.name)
        if not _WINGET_ID.match(p.id):
            p.status = "failed"
            continue
        cp = common.run([winget_path(), "upgrade", "--id", p.id, "--exact", "--silent", "--disable-interactivity",
                         "--accept-package-agreements", "--accept-source-agreements"], timeout=3600)
        out = cp.stdout.lower()
        if cp.returncode == 0:
            p.status = "need_reboot_to_finish" if "restart" in out else "successfully_updated"
        else:
            p.status = "failed"
        common.journal_add("software_updater", p.id, "update", {"from": p.current_version, "to": p.available_version, "status": p.status})
    if patches:
        if progress:
            progress(len(apps), len(products), "Windows patches")
        results = {r["Id"]: r for r in windows_update.install([p.id for p in patches])}
        for p in patches:
            r = results.get(p.id)
            if not r:
                p.status = "failed"
                continue
            p.status = windows_update.RESULT.get(r["ResultCode"], "failed")
            if r.get("RebootRequired"):
                p.status = "need_reboot_to_finish"
    return products


def skip_update(pid: str, version: str) -> None:
    s = common.setting_get("su_skipped", {})
    s[pid] = version
    common.setting_set("su_skipped", s)


def set_ignored(pid: str, on: bool) -> None:
    s = set(common.setting_get("su_ignored", []))
    s.add(pid) if on else s.discard(pid)
    common.setting_set("su_ignored", sorted(s))
