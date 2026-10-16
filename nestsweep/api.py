"""Bridge between the web UI (nestsweep/ui) and the cleaning engine.

pywebview exposes every public method of `Api` to JavaScript as
`window.pywebview.api.<name>(...)`, each returning a Promise. Calls run on their own
thread, so long scans don't freeze the window. Every method returns plain JSON:
`{"ok": true, "data": ...}` or `{"ok": false, "error": "...", "kind": "PermissionError"}`.
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import threading
import time
import traceback

from . import __version__, common, custom_clean, driver_updater, performance_optimizer as po, software_updater, \
    startup_manager, uninstaller, updater

GITHUB_URL = "https://github.com/afkbirdgames/nestsweep"


def _endpoint(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return {"ok": True, "data": fn(self, *args, **kwargs)}
        except Exception as e:  # surfaced to the UI as a readable message
            traceback.print_exc()
            return {"ok": False, "error": str(e) or type(e).__name__, "kind": type(e).__name__}
    return wrapper


class Api:
    def __init__(self):
        self._window = None
        self._lock = threading.Lock()
        self._update: dict | None = None
        self._update_lock = threading.Lock()
        self._old_cleaned = False
        self._cache: dict[str, dict] = {"un": {}, "lo": {}, "po": {}, "sm": {}, "su": {}, "du": {}}
        self._counts: dict[str, int | None] = {"startup_enabled": None, "updates": None, "asleep": None, "drivers": None}

    # ------------------------------------------------------------- plumbing

    def _attach(self, window) -> None:
        self._window = window

    def _progress(self, feature: str, text: str, pct: float | None = None) -> None:
        if self._window is None:
            return
        payload = json.dumps({"feature": feature, "text": text, "pct": pct})
        try:
            self._window.evaluate_js(f"window.nestsweep && window.nestsweep.onProgress({payload})")
        except Exception:
            pass

    # ------------------------------------------------------------- app

    @_endpoint
    def app_info(self):
        if not self._old_cleaned:          # the UI loaded and reached Python: this version works
            self._old_cleaned = True
            updater.cleanup_old()
        return {"name": "Nestsweep", "version": __version__, "admin": common.is_admin(),
                "data_dir": str(common.APP_DIR), "backup_dir": str(common.BACKUP_DIR),
                "frozen": common.FROZEN, "github": GITHUB_URL}

    # ------------------------------------------------------------- self-update

    @_endpoint
    def update_status(self, refresh: bool = False):
        """Latest-release check, done once per launch unless refresh is asked for."""
        with self._update_lock:
            if refresh or self._update is None:
                self._update = updater.check()
        return {k: v for k, v in self._update.items() if k not in ("exe_url", "sum_url")}

    @_endpoint
    def update_install(self):
        info = self._update or updater.check()
        exe = updater.install(info, progress=lambda m: self._progress("update", m))
        updater.relaunch(exe)
        threading.Timer(0.5, lambda: self._window and self._window.destroy()).start()
        return True

    @_endpoint
    def restart_as_admin(self):
        if common.relaunch_as_admin():
            threading.Timer(0.3, lambda: self._window and self._window.destroy()).start()
            return True
        return False

    @_endpoint
    def open_path(self, which: str):
        target = {"data": common.APP_DIR, "backups": common.BACKUP_DIR,
                  "drivers": driver_updater.BACKUP_DIR}.get(which)
        if target is None:
            raise ValueError(f"unknown folder {which!r}")
        if not os.path.isdir(target):
            raise FileNotFoundError("Nothing has been backed up there yet.")
        os.startfile(str(target))
        return True

    @_endpoint
    def open_url(self, url: str):
        if url not in (GITHUB_URL, GITHUB_URL + "/releases", GITHUB_URL + "/issues") and \
                not url.startswith(GITHUB_URL + "/releases/"):
            raise ValueError("unknown link")
        os.startfile(url)
        return True

    @_endpoint
    def overview(self):
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        du = shutil.disk_usage(drive)
        cleaned = 0
        last_clean = None
        with common.db() as c:
            for row in c.execute("SELECT data, time FROM journal WHERE feature='custom_clean' AND action='clean' ORDER BY id"):
                cleaned += json.loads(row["data"]).get("bytes", 0)
                last_clean = row["time"]
        return {"drive": drive.rstrip("\\"), "total": du.total, "used": du.used, "free": du.free,
                "cleaned_total": cleaned, "last_clean": last_clean,
                "last_analysis": common.setting_get("last_analysis"), "counts": self._counts,
                "admin": common.is_admin()}

    # ------------------------------------------------------------- cleanup

    @_endpoint
    def cc_rules(self):
        return [{"id": r.id, "name": r.name, "tags": r.tags, "default": r.default, "warning": r.warning,
                 "processes": r.active_processes}
                for r in custom_clean.load_rules() if custom_clean.is_applicable(r)]

    @_endpoint
    def cc_analyse(self, ids: list[str]):
        if not ids:
            raise ValueError("Pick at least one category to analyze.")

        def prog(i, n, name):
            self._progress("cleanup", f"Checking {name}", i / max(n, 1))
        findings = custom_clean.analyse(ids, progress=prog)
        summ = custom_clean.summarize(findings)
        total = sum(s["bytes"] for s in summ.values())
        result = {"rules": summ, "total_bytes": total, "total_items": sum(s["items"] for s in summ.values()),
                  "blockers": custom_clean.running_blockers(ids), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        common.setting_set("last_analysis", {"bytes": total, "time": result["time"]})
        return result

    @_endpoint
    def cc_blockers(self, ids: list[str]):
        return custom_clean.running_blockers(ids)

    @_endpoint
    def cc_clean(self, ids: list[str]):
        """Re-analyse right before deleting, so nothing is removed that wasn't just measured."""
        if not ids:
            raise ValueError("Pick at least one category to clean.")
        with self._lock:
            self._progress("cleanup", "Measuring what will be removed", 0)
            findings = custom_clean.analyse(ids)
            stats = custom_clean.clean(findings, progress=lambda i, n, t: self._progress(
                "cleanup", f"Removing {os.path.basename(t) or t}", i / max(n, 1)))
        common.setting_set("last_analysis", {"bytes": 0, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        return stats

    # ------------------------------------------------------------- uninstaller

    @_endpoint
    def un_scan(self):
        items = uninstaller.scan()
        self._cache["un"] = {s.id: s for s in items}
        return [{"id": s.id, "name": s.name, "publisher": s.publisher, "version": s.version,
                 "installed": s.install_date, "size": s.size_bytes, "store": s.is_store_app,
                 "location": s.install_location, "command": "" if s.is_store_app else s.uninstall_string,
                 "per_user": s.reg_path.startswith("HKCU")} for s in items]

    @_endpoint
    def un_uninstall(self, ids: list[str]):
        """Run each uninstaller. Leftovers are only looked for when the program is really gone,
        so cancelling a vendor uninstaller never leads to its files being swept."""
        leftovers, kept = [], []
        self._cache["lo"] = {}
        everything = list(self._cache["un"].values())
        for n, sid in enumerate(ids):
            sw = self._cache["un"].get(sid)
            if sw is None:
                kept.append(f"{sid}: no longer in the list; scan again")
                continue
            self._progress("uninstaller", f"Waiting for the {sw.name} uninstaller to finish", n / len(ids))
            try:
                uninstaller.run_uninstaller(sw)
            except Exception as e:
                kept.append(f"{sw.name}: {e}")
                continue
            if uninstaller.still_installed(sw):
                kept.append(f"{sw.name} is still installed (the uninstaller was cancelled or didn't finish)")
                continue
            self._progress("uninstaller", f"Looking for anything {sw.name} left behind", (n + 0.5) / len(ids))
            try:
                found = uninstaller.find_leftovers(sw, everything)
            except Exception as e:
                kept.append(f"{sw.name} was uninstalled, but leftovers couldn't be checked: {e}")
                continue
            for k, lo in enumerate(found):
                lid = f"{n}:{k}"
                self._cache["lo"][lid] = lo
                leftovers.append({"id": lid, "program": sw.name, "kind": lo.kind, "target": lo.target,
                                  "size": lo.detail.get("size", 0) if lo.kind == "folder" else 0,
                                  "suggested": bool(lo.detail.get("suggested"))})
        return {"leftovers": leftovers, "kept": kept}

    @_endpoint
    def un_remove_leftovers(self, ids: list[str]):
        picks = [self._cache["lo"][i] for i in ids if i in self._cache["lo"]]
        return uninstaller.remove_leftovers(picks)

    # ------------------------------------------------------------- performance (sleep mode)

    def _po_json(self, p):
        return {"id": p.id, "name": p.name, "state": p.state, "impact": p.impact, "location": p.install_location,
                "exes": len(p.exes), "entries": [{"type": e.entry_type, "name": e.name, "path": e.path} for e in p.entries]}

    @_endpoint
    def po_scan(self):
        progs = po.scan()
        self._cache["po"] = {p.id: p for p in progs}
        self._counts["asleep"] = sum(p.state == "deactivated" for p in progs)
        return [self._po_json(p) for p in progs]

    @_endpoint
    def po_sleep(self, ids: list[str]):
        res = []
        for pid in ids:
            p = self._cache["po"].get(pid)
            if p is not None and p.state == "found":
                self._progress("performance", f"Putting {p.name} to sleep")
                res.append(po.sleep(p))
        return {"programs": len(res), "entries": sum(r["disabled"] for r in res),
                "failed": [f for r in res for f in r["failed"]]}

    @_endpoint
    def po_wake(self, ids: list[str]):
        out = {"restored": 0, "failed": []}
        for pid in ids:
            r = po.wake(pid)
            out["restored"] += r["restored"]
            out["failed"] += r["failed"]
        return out

    @_endpoint
    def po_wake_all(self):
        return po.revert_all()

    @_endpoint
    def po_ignore(self, ids: list[str], on: bool = True):
        for pid in ids:
            po.ignore(pid, on)
        return True

    # ------------------------------------------------------------- startup

    @_endpoint
    def sm_scan(self):
        items = startup_manager.scan()
        self._cache["sm"] = {i.id: i for i in items}
        self._counts["startup_enabled"] = sum(1 for i in items if i.item_type == "startup_app" and i.enabled)
        return [{"id": i.id, "type": i.item_type, "name": i.name, "command": i.command, "publisher": i.publisher,
                 "location": i.location, "enabled": i.enabled, "can_toggle": i.can_toggle} for i in items]

    @_endpoint
    def sm_set_enabled(self, item_id: str, on: bool):
        item = self._cache["sm"][item_id]
        startup_manager.set_enabled(item, on)
        item.enabled = on
        return True

    # ------------------------------------------------------------- software updates

    def _su_json(self, p):
        return {"id": p.id, "name": p.name, "current": p.current_version, "available": p.available_version,
                "source": p.source, "category": p.category, "severity": p.severity, "size": p.size_bytes,
                "status": p.status}

    @_endpoint
    def su_scan(self, include_patches: bool = True):
        items, problems = [], []
        if software_updater.winget_available():
            self._progress("updates", "Asking winget which apps have newer versions")
            items += software_updater.scan_apps()
        else:
            problems.append("winget (App Installer) isn't installed, so only Windows patches were checked.")
        if include_patches:
            self._progress("updates", "Checking Windows Update for missing patches")
            try:
                items += software_updater.scan_patches()
            except Exception as e:
                problems.append(f"Windows Update couldn't be checked: {e}")
        items = [p for p in items if not p.ignored]
        self._cache["su"] = {p.id: p for p in items}
        self._counts["updates"] = len(items)
        return {"items": [self._su_json(p) for p in items], "notes": problems}

    @_endpoint
    def su_update(self, ids: list[str]):
        picks = [self._cache["su"][i] for i in ids if i in self._cache["su"]]
        res = software_updater.update(picks, progress=lambda i, n, name: self._progress(
            "updates", f"Updating {name}", i / max(n, 1)))
        return [self._su_json(p) for p in res]

    @_endpoint
    def su_skip(self, item_id: str):
        p = self._cache["su"].pop(item_id)
        software_updater.skip_update(p.id, p.available_version)
        return True

    # ------------------------------------------------------------- drivers

    def _du_json(self, d):
        u = d.update or {}
        return {"id": d.id, "name": d.name, "category": d.category, "manufacturer": d.manufacturer,
                "version": d.driver_version, "date": d.driver_date, "problem": d.problem_code,
                "problem_text": driver_updater.problem_text(d.problem_code),
                "attention": bool(d.update) or driver_updater.needs_attention(d.problem_code),
                "update": {"title": u.get("Title", ""), "date": (u.get("DriverVerDate") or "")[:10]} if d.update else None,
                "backups": list(d.backups), "can_backup": d.inf_name.lower().startswith("oem")}

    @_endpoint
    def du_scan(self):
        devs = driver_updater.scan(progress=lambda m: self._progress("drivers", m))
        self._cache["du"] = {d.id: d for d in devs}
        self._counts["drivers"] = sum(1 for d in devs if d.update or driver_updater.needs_attention(d.problem_code))
        return [self._du_json(d) for d in devs]

    @_endpoint
    def du_update(self, ids: list[str], restore_point: bool = True):
        """restore_point=False is only sent after the user saw a RestorePointError and chose to go on."""
        picks = [self._cache["du"][i] for i in ids if i in self._cache["du"] and self._cache["du"][i].update]
        return driver_updater.update(picks, restore_point, progress=lambda m: self._progress("drivers", m))

    @_endpoint
    def du_backup(self, ids: list[str]):
        made = []
        for i in ids:
            d = self._cache["du"].get(i)
            if d is not None:
                self._progress("drivers", f"Backing up {d.name}")
                name = driver_updater.backup(d)
                if name:
                    made.append(name)
                    d.backups.append(name)
        return made

    @_endpoint
    def du_restore(self, item_id: str, file_name: str):
        return driver_updater.restore(self._cache["du"][item_id], file_name)
