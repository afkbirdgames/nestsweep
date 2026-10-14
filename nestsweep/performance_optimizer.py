"""Sleep mode: stop an installed program's background parts from running until you need it.

A program's background parts are the services, scheduled tasks and startup entries whose
executable lives inside its install folder. Putting it to sleep disables each part and records
the original state in the journal; waking restores exactly that state.

Safety: install folders that are too broad (Windows, Program Files roots, shared vendor
folders...) are skipped, and Microsoft, svchost-hosted, Windows-folder and protected
(anti-malware) services are never touched.

A part that already belongs to a sleeping program is never claimed by a second one, so waking
programs in any order always restores the true original state.
"""
from __future__ import annotations

import json
import winreg
from dataclasses import dataclass, asdict, field

from . import common, safety, startup_manager, uninstaller

SVC = r"SYSTEM\CurrentControlSet\Services"


@dataclass
class Entry:
    id: str
    entry_type: str     # service | scheduled_task | registry_run | autostart
    name: str
    path: str
    detail: dict = field(default_factory=dict)


@dataclass
class Program:
    id: str
    name: str
    install_location: str
    state: str          # found | deactivated | ignored
    impact: str         # low | medium | high
    entries: list[Entry]
    exes: list[str]


def _all_entries() -> list[Entry]:
    entries = []
    for svc in common.reg_subkeys(winreg.HKEY_LOCAL_MACHINE, SVC):
        v = {k: val for k, (val, _) in common.reg_values(winreg.HKEY_LOCAL_MACHINE, f"{SVC}\\{svc}").items()}
        if v.get("Start") in (2, 3, 4) and "ImagePath" in v and not safety.is_critical_service(svc, v) \
                and not safety.is_sensitive_service(svc, v):
            entries.append(Entry("service:" + svc, "service", svc, common.exe_from_command(str(v["ImagePath"])),
                                 {"start": v["Start"]}))
    for it in startup_manager._startup_apps():
        if it.can_toggle:
            entries.append(Entry("regrun:" + it.id, "registry_run" if it.detail["kind"] == "reg" else "autostart",
                                 it.name, common.exe_from_command(it.command), {"item": asdict(it)}))
    try:
        for it in startup_manager._scheduled_tasks():
            if not safety.is_system_task(it.detail["path"]):
                entries.append(Entry("task:" + it.detail["path"] + it.detail["name"], "scheduled_task", it.name,
                                     common.exe_from_command(it.command), {"item": asdict(it)}))
    except Exception:
        pass
    return [e for e in entries if e.path and not safety.is_inside_windows(e.path)]


def _service_running(svc: str) -> bool:
    return "RUNNING" in common.run(["sc", "query", svc]).stdout


def scan() -> list[Program]:
    ignored = set(common.setting_get("po_ignored", []))
    sleeping = {r["subject"]: json.loads(r["data"]) for r in common.journal_open("performance_optimizer")}
    entries = _all_entries()
    claimed = {e["id"] for data in sleeping.values() for e in data["entries"]}
    programs = []
    installed = uninstaller.scan(include_store_apps=False)
    all_locs = [s.install_location for s in installed if s.install_location]
    for sw in installed:
        if sw.publisher.lower().startswith("microsoft"):
            continue
        if sw.id not in sleeping and safety.is_sensitive_program(sw.name, sw.publisher, sw.install_location):
            continue
        loc = safety.safe_install_location(sw.install_location, all_locs)
        if not loc and sw.id not in sleeping:
            continue
        mine = [e for e in entries if loc and safety.is_under(e.path, loc) and e.id not in claimed]
        if sw.id in sleeping:
            mine = [Entry(**e) for e in sleeping[sw.id]["entries"]]
        if not mine:
            continue
        exes: list[str] = []
        score = sum({"service": 3, "registry_run": 2, "autostart": 2, "scheduled_task": 1}[e.entry_type] for e in mine)
        state = "deactivated" if sw.id in sleeping else "ignored" if sw.id in ignored else "found"
        programs.append(Program(sw.id, sw.name, sw.install_location, state,
                                "high" if score >= 5 else "medium" if score >= 3 else "low", mine, exes))
    return sorted(programs, key=lambda p: ({"high": 0, "medium": 1, "low": 2}[p.impact], p.name.lower()))


# ---------------------------------------------------------------- sleep / wake

def _disable_entry(e: Entry) -> dict:
    if e.entry_type == "service":
        orig = {"origStartType": e.detail["start"], "origRunning": _service_running(e.name)}
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{SVC}\\{e.name}", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "Start", 0, winreg.REG_DWORD, 4)
        if orig["origRunning"]:
            common.run(["sc", "stop", e.name])
        return orig
    item = startup_manager.StartupItem(**e.detail["item"])
    orig = {"origEnabled": item.enabled}
    if item.enabled:
        startup_manager.set_enabled(item, False)
    return orig


def _restore_entry(e: Entry, orig: dict) -> None:
    """Put one part back. A part that no longer exists (program uninstalled while asleep) counts
    as restored. Services are re-checked first: the journal lives in the user's profile, so a
    tampered row must never be able to change a Windows or security service."""
    if e.entry_type == "service":
        key = f"{SVC}\\{e.name}"
        v = {k: val for k, (val, _) in common.reg_values(winreg.HKEY_LOCAL_MACHINE, key).items()}
        if not v:
            return                                    # service is gone
        if safety.is_critical_service(e.name, v) or orig.get("origStartType") not in (2, 3, 4):
            raise PermissionError("protected service; left unchanged")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "Start", 0, winreg.REG_DWORD, orig["origStartType"])
        if orig.get("origRunning"):
            common.run(["sc", "start", e.name])
    elif orig.get("origEnabled"):
        item = startup_manager.StartupItem(**e.detail["item"])
        try:
            startup_manager.set_enabled(item, True)
        except Exception as ex:
            if not startup_manager.exists(item):
                return                                # entry is gone
            raise ex


def sleep(program: Program) -> dict:
    if not common.is_admin():
        raise PermissionError("Sleep mode changes services and machine-wide settings, so it needs administrator rights.")
    if program.state != "found":
        raise ValueError(f"{program.name} is already asleep or ignored")
    done = []
    for e in program.entries:
        try:
            done.append({"entry": asdict(e), "orig": _disable_entry(e)})
        except Exception as ex:
            done.append({"entry": asdict(e), "orig": None, "error": str(ex)})
    common.journal_add("performance_optimizer", program.id, "sleep",
                       {"name": program.name, "entries": [d["entry"] for d in done], "orig": done})
    return {"disabled": sum(1 for d in done if d["orig"] is not None),
            "failed": [f'{d["entry"]["name"]}: {d["error"]}' for d in done if d.get("error")]}


def wake(program_id: str) -> dict:
    """Restore every part to its recorded state. The journal entry is only closed when all parts
    were restored, so a failed wake can simply be retried."""
    if not common.is_admin():
        raise PermissionError("Waking programs changes services and machine-wide settings, so it needs administrator rights.")
    restored, failed = 0, []
    for row in common.journal_open("performance_optimizer", program_id):
        data = json.loads(row["data"])
        row_failed = []
        for d in data["orig"]:
            if d["orig"] is not None:
                try:
                    _restore_entry(Entry(**d["entry"]), d["orig"])
                    restored += 1
                except Exception as ex:
                    row_failed.append(f'{d["entry"]["name"]}: {ex}')
        if row_failed:
            failed += row_failed
        else:
            common.journal_mark_reverted(row["id"])
    return {"restored": restored, "failed": failed}


def revert_all() -> dict:
    total, failed = 0, []
    for subject in dict.fromkeys(r["subject"] for r in common.journal_open("performance_optimizer")):
        r = wake(subject)
        total += r["restored"]
        failed += r["failed"]
    return {"restored": total, "failed": failed}


def ignore(program_id: str, on: bool = True) -> None:
    s = set(common.setting_get("po_ignored", []))
    s.add(program_id) if on else s.discard(program_id)
    common.setting_set("po_ignored", sorted(s))
