"""Checks for the 2026-10 safety pass: deleting by handle, pinned update downloads, exact driver
matching, shared install folders, TEMP profiles, relative install locations and backups of removed
services and tasks. Only touches data the tests create themselves."""
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nestsweep import common, custom_clean as cc, driver_updater as du, safety, uninstaller as un, updater  # noqa: E402

REPARSE, DIRECTORY = safety.FILE_ATTRIBUTE_REPARSE_POINT, safety.FILE_ATTRIBUTE_DIRECTORY


class FakeWin32:
    """Stands in for the kernel32 calls: `objects` maps an opened path to (real location, attributes)."""
    def __init__(self, objects, fail=None):
        self.objects, self.fail = objects, fail or set()
        self.deleted, self.closed, self.opened = [], 0, 0

    def open(self, path, is_dir):
        if "open" in self.fail or path not in self.objects:
            raise PermissionError(32, "in use")
        self.opened += 1
        return path

    def open_folder(self, path):
        return self.open(path, True)

    def attributes(self, h):
        return self.objects[h][1]

    def final_path(self, h):
        return self.objects[h][0]

    def mark_delete(self, h):
        if "delete" in self.fail:
            raise OSError(145, "directory not empty")
        self.deleted.append(self.objects[h][0])

    def close(self, h):
        self.closed += 1


class DeleteInsideLogicTest(unittest.TestCase):
    """base_id is the rule folder's real location; final paths are where the opened object really is."""
    B = os.path.normcase(os.path.join(os.sep + "Device", "HarddiskVolume3", "Windows", "Temp"))
    E = os.path.normcase(os.path.join(os.sep + "Device", "HarddiskVolume3", "Windows", "System32"))

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = os.path.join(self.tmp, "Temp")

    def p(self, *parts):
        return os.path.abspath(os.path.join(self.base, *parts))

    def run_with(self, fake, fn, *args, **kw):
        with mock.patch.object(safety, "_win32", lambda: fake):
            return fn(*args, **kw)

    def test_file_inside_is_deleted(self):
        fake = FakeWin32({self.p("a.tmp"): (os.path.join(self.B, "a.tmp"), 0)})
        self.run_with(fake, safety.delete_inside, self.p("a.tmp"), self.B)
        self.assertEqual(len(fake.deleted), 1)
        self.assertEqual(fake.closed, 1)

    def test_swapped_parent_is_refused(self):
        # The path still says Temp\x\important.dll, but the opened object is really in System32.
        fake = FakeWin32({self.p("x", "important.dll"): (os.path.join(self.E, "important.dll"), 0)})
        with self.assertRaises(OSError):
            self.run_with(fake, safety.delete_inside, self.p("x", "important.dll"), self.B)
        self.assertEqual(fake.deleted, [])
        self.assertEqual(fake.closed, 1)

    def test_lookalike_sibling_folder_is_refused(self):
        fake = FakeWin32({self.p("a.tmp"): (os.path.join(self.B + "2", "a.tmp"), 0)})
        with self.assertRaises(OSError):
            self.run_with(fake, safety.delete_inside, self.p("a.tmp"), self.B)
        self.assertEqual(fake.deleted, [])

    def test_link_or_type_change_is_refused(self):
        for attrs, is_dir in ((REPARSE | DIRECTORY, True), (DIRECTORY, False), (0, True)):
            fake = FakeWin32({self.p("j"): (os.path.join(self.B, "j"), attrs)})
            with self.assertRaises(OSError):
                self.run_with(fake, safety.delete_inside, self.p("j"), self.B, is_dir=is_dir)
            self.assertEqual(fake.deleted, [])
            self.assertEqual(fake.closed, 1)

    def test_rule_folder_itself_only_when_asked(self):
        fake = FakeWin32({os.path.abspath(self.base): (self.B, DIRECTORY)})
        with self.assertRaises(OSError):
            self.run_with(fake, safety.delete_inside, self.base, self.B, is_dir=True)
        self.run_with(fake, safety.delete_inside, self.base, self.B, is_dir=True, may_be_base=True)
        self.assertEqual(len(fake.deleted), 1)

    def test_failures_delete_nothing_and_close_the_handle(self):
        objs = {self.p("a.tmp"): (os.path.join(self.B, "a.tmp"), 0)}
        fake = FakeWin32(objs, fail={"open"})
        with self.assertRaises(PermissionError):           # Cleanup counts this as "in use"
            self.run_with(fake, safety.delete_inside, self.p("a.tmp"), self.B)
        fake = FakeWin32(objs, fail={"delete"})
        with self.assertRaises(OSError):
            self.run_with(fake, safety.delete_inside, self.p("a.tmp"), self.B)
        self.assertEqual(fake.closed, 1)

    def test_no_base_means_no_delete(self):
        fake = FakeWin32({})
        with self.assertRaises(OSError):
            self.run_with(fake, safety.delete_inside, self.p("a.tmp"), "")
        self.assertEqual(fake.opened, 0)

    def test_pinned_folder_must_still_be_the_scanned_one(self):
        fake = FakeWin32({os.path.abspath(self.base): (self.B, DIRECTORY)})
        pin = self.run_with(fake, safety.PinnedFolder, self.base, self.B)
        self.assertEqual(fake.closed, 0)                   # held open until released
        pin.close()
        self.assertEqual(fake.closed, 1)
        fake = FakeWin32({os.path.abspath(self.base): (self.E, DIRECTORY)})   # swapped since the scan
        with self.assertRaises(OSError):
            self.run_with(fake, safety.PinnedFolder, self.base, self.B)
        self.assertEqual(fake.closed, 1)
        with self.assertRaises(OSError):
            self.run_with(fake, safety.PinnedFolder, self.base, "")

    def test_folder_that_cant_be_held_is_still_cleaned_with_item_checks(self):
        objs = {self.p("a.tmp"): (os.path.join(self.B, "a.tmp"), 0),
                self.p("x", "important.dll"): (os.path.join(self.E, "important.dll"), 0)}
        fake = FakeWin32(objs)          # the rule folder itself isn't in objs: holding it fails as "in use"
        fs = [cc.Finding("t", "file", self.p(n), 5, self.base, self.B) for n in ("a.tmp", os.path.join("x", "important.dll"))]
        with mock.patch.object(cc, "_still_safe", lambda f: True), mock.patch.object(common, "journal_add"):
            stats = self.run_with(fake, cc.clean, fs)
        self.assertEqual((stats["files"], stats["errors"]), (1, 1))
        self.assertEqual(fake.deleted, [os.path.join(self.B, "a.tmp")])

    def test_clean_counts_refusals_and_keeps_going(self):
        objs = {os.path.abspath(self.base): (self.B, DIRECTORY),
                self.p("a.tmp"): (os.path.join(self.B, "a.tmp"), 0),
                self.p("x", "important.dll"): (os.path.join(self.E, "important.dll"), 0),
                self.p("b.tmp"): (os.path.join(self.B, "b.tmp"), 0)}
        fake = FakeWin32(objs)
        fs = [cc.Finding("t", "file", self.p(n), 5, self.base, self.B) for n in ("a.tmp", os.path.join("x", "important.dll"), "b.tmp")]
        fs.append(cc.Finding("t", "file", self.p("c.tmp"), 5, self.base, ""))      # never identified: refused
        with mock.patch.object(cc, "_still_safe", lambda f: True), mock.patch.object(common, "journal_add"):
            stats = self.run_with(fake, cc.clean, fs)
        self.assertEqual((stats["files"], stats["errors"]), (2, 2))
        self.assertEqual(len(fake.deleted), 2)
        self.assertEqual(fake.closed, fake.opened)        # every handle, the pinned folder's too


@unittest.skipUnless(os.name == "nt", "real Windows handles")
class DeleteInsideWindowsTest(unittest.TestCase):
    def junction(self, link, target):
        cp = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
        if cp.returncode or not os.path.isdir(link):
            self.skipTest("couldn't create a junction here")

    def test_plain_file_and_empty_folder_are_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp, "Temp")
            (base / "d").mkdir(parents=True)
            (base / "a.tmp").write_text("x")
            bid = safety.folder_id(str(base))
            with safety.PinnedFolder(str(base), bid):
                safety.delete_inside(str(base / "a.tmp"), bid)
                safety.delete_inside(str(base / "d"), bid, is_dir=True)
            self.assertFalse((base / "a.tmp").exists())
            self.assertFalse((base / "d").exists())

    def test_folder_with_contents_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp, "Temp")
            (base / "d").mkdir(parents=True)
            (base / "d" / "keep.txt").write_text("x")
            with self.assertRaises(OSError):
                safety.delete_inside(str(base / "d"), safety.folder_id(str(base)), is_dir=True)
            self.assertTrue((base / "d" / "keep.txt").exists())

    def test_folder_swapped_for_a_junction_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, victim = Path(tmp, "Temp"), Path(tmp, "System32")
            base.mkdir()
            victim.mkdir()
            (victim / "important.dll").write_text("system file")
            bid = safety.folder_id(str(base))
            self.junction(str(base / "x"), str(victim))        # Temp\x now leads to System32
            with self.assertRaises(OSError):
                safety.delete_inside(str(base / "x" / "important.dll"), bid)
            with self.assertRaises(OSError):                   # and the junction itself isn't removed
                safety.delete_inside(str(base / "x"), bid, is_dir=True)
            self.assertTrue((victim / "important.dll").exists())
            os.rmdir(base / "x")                               # removes just the junction

    def test_rule_folder_swapped_after_the_scan_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, victim = Path(tmp, "Temp"), Path(tmp, "System32")
            base.mkdir()
            victim.mkdir()
            (victim / "important.dll").write_text("system file")
            bid = safety.folder_id(str(base))
            os.rmdir(base)
            self.junction(str(base), str(victim))              # the rule folder itself now leads to System32
            with self.assertRaises(OSError):
                safety.PinnedFolder(str(base), bid)
            with self.assertRaises(OSError):
                safety.delete_inside(str(base / "important.dll"), bid)
            self.assertTrue((victim / "important.dll").exists())
            os.rmdir(base)

    def test_pinned_folder_cannot_be_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp, "Temp")
            base.mkdir()
            with safety.PinnedFolder(str(base), safety.folder_id(str(base))):
                with self.assertRaises(OSError):
                    os.rename(base, Path(tmp, "Moved"))
            os.rename(base, Path(tmp, "Moved"))                # fine once released

    def test_read_only_file_is_not_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp, "Temp")
            base.mkdir()
            f = base / "ro.tmp"
            f.write_text("x")
            os.chmod(f, stat.S_IREAD)
            try:
                with self.assertRaises(OSError):
                    safety.delete_inside(str(f), safety.folder_id(str(base)))
                self.assertTrue(f.exists())
            finally:
                os.chmod(f, stat.S_IWRITE | stat.S_IREAD)


class CleanupBaseTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_profile_root_called_temp_is_refused(self):
        users = os.path.dirname(os.environ["USERPROFILE"])
        temp_profile = os.path.join(users, "TEMP")
        with mock.patch.dict(os.environ, {"USERPROFILE": temp_profile}):
            safety.protected_folders.cache_clear()
            try:
                self.assertFalse(safety.cleanup_base_ok(temp_profile))
                self.assertFalse(safety.cleanup_base_ok(os.path.join(users, "tmp")))
            finally:
                safety.protected_folders.cache_clear()
        self.assertTrue(safety.cleanup_base_ok(os.path.join(os.environ["USERPROFILE"], "AppData", "Local", "Temp")))


class InstallLocationTest(unittest.TestCase):
    def test_relative_drive_relative_and_network_paths_are_rejected(self):
        for loc in (".", "Acme", "Acme\\App", "C:Acme", "\\\\server\\share\\Acme", "\\Acme"):
            self.assertIsNone(safety.safe_install_location(loc), loc)

    def test_shared_vendor_folder_is_not_pre_ticked(self):
        a = un.Software("a", "Acme Suite A", "1.0", "Acme", os.path.join(r"C:\Program Files", "Acme"), "", 0, "", "", False, "", 0)
        b = un.Software("b", "Acme B", "2.0", "Acme", "", "", 0, "MsiExec.exe /X{0}", "", False, "", 0)
        own = un.Software("c", "Acme Paint", "1.0", "Acme", os.path.join(r"C:\Program Files", "Acme Paint"), "", 0, "", "", False, "", 0)
        other = un.Software("d", "Other Tool", "1.0", "Someone", "", "", 0, "", "", False, "", 0)
        self.assertTrue(un._maybe_shared(a.install_location, a, [a, other], un._tokens(a)))   # named after Acme
        self.assertTrue(un._maybe_shared(own.install_location, own, [own, b], un._tokens(own)))  # B: location unknown
        self.assertFalse(un._maybe_shared(own.install_location, own, [own, other], un._tokens(own)))


class UpdateSourceTest(unittest.TestCase):
    def rel(self, base):
        return {"tag_name": "v9.0.0", "body": "", "assets": [
            {"name": n, "browser_download_url": f"{base}/v9.0.0/{n}"} for n in (updater.EXE_ASSET, updater.SUM_ASSET)]}

    def test_only_this_repos_release_files_are_offered(self):
        self.assertTrue(updater.release_info(self.rel(updater.RELEASE_DOWNLOAD.rstrip("/")), "1.0.0")["available"])
        for base in ("https://github.com/someone/nestsweep/releases/download",
                     "https://github.com/afkbirdgames/nestsweep-fork/releases/download",
                     "https://objects.githubusercontent.com/x",
                     "https://github.com/afkbirdgames/nestsweep/releases/download/../../../someone/x"):
            self.assertFalse(updater.release_info(self.rel(base), "1.0.0")["available"], base)

    def test_other_urls_are_never_fetched(self):
        for url in ("https://github.com/someone/nestsweep/releases/download/v9/Nestsweep.exe",
                    "https://objects.githubusercontent.com/Nestsweep.exe",
                    "https://api.github.com/repos/someone/nestsweep/releases/latest"):
            with self.assertRaises(ValueError):
                updater._get(url, 1, 10)

    def test_install_refuses_a_foreign_url_even_if_marked_available(self):
        info = {"available": True, "latest": "9.0.0", "exe_url": "https://github.com/someone/x/releases/download/v9/Nestsweep.exe",
                "sum_url": updater.RELEASE_DOWNLOAD + "v9/Nestsweep.exe.sha256"}
        with mock.patch.object(common, "FROZEN", True), self.assertRaises(RuntimeError):
            updater.install(info)

    def test_old_copy_is_removed_only_once_the_ui_has_loaded(self):
        src = Path(updater.__file__).with_name("app.py").read_text(encoding="utf-8")
        self.assertNotIn("cleanup_old", src.split("def main")[1])
        api_src = Path(updater.__file__).with_name("api.py").read_text(encoding="utf-8")
        self.assertIn("updater.cleanup_old()", api_src.split("def app_info")[1].split("@_endpoint")[0])


class DriverMatchTest(unittest.TestCase):
    PARENT = du.Device(r"USB\VID_1234&PID_5678\0001", "USB Composite Device", "USB", "Acme", "1.0", "2019-01-01",
                       "usb.inf", [r"USB\VID_1234&PID_5678&REV_0100", r"USB\VID_1234&PID_5678"])
    CHILD = du.Device(r"USB\VID_1234&PID_5678&MI_00\1", "Acme USB Audio", "MEDIA", "Acme", "1.0", "2019-01-01",
                      "oem12.inf", [r"USB\VID_1234&PID_5678&REV_0100&MI_00", r"USB\VID_1234&PID_5678&MI_00"])

    def test_parent_driver_does_not_land_on_the_child(self):
        wu = r"USB\VID_1234&PID_5678"
        self.assertTrue(du._hw_match(wu, self.PARENT.hardware_ids))
        self.assertFalse(du._hw_match(wu, self.CHILD.hardware_ids))
        self.assertTrue(du._hw_match(r"usb\vid_1234&pid_5678&mi_00", self.CHILD.hardware_ids))
        self.assertFalse(du._hw_match("", self.CHILD.hardware_ids))

    def test_update_class_is_checked_too(self):
        self.assertFalse(du._offer_ok({"DriverClass": "USB"}, self.CHILD))
        self.assertTrue(du._offer_ok({"DriverClass": "MEDIA"}, self.CHILD))
        self.assertFalse(du._offer_ok({"DriverClass": "MEDIA"}, self.PARENT))

    def test_hardware_id_list_keeps_the_installed_one_first(self):
        row = {"HwId": r"PCI\VEN_1&DEV_2&SUBSYS_3", "HwIds": [r"PCI\VEN_1&DEV_2&SUBSYS_3", r"PCI\VEN_1&DEV_2", None]}
        self.assertEqual(du._hardware_ids(row), [r"PCI\VEN_1&DEV_2&SUBSYS_3", r"PCI\VEN_1&DEV_2"])
        self.assertEqual(du._hardware_ids({"HwId": "", "HwIds": r"PCI\VEN_1"}), [r"PCI\VEN_1"])
        self.assertEqual(du._hardware_ids({"HwId": None}), [])


@unittest.skipUnless(os.name == "nt" and os.environ.get("GITHUB_ACTIONS"), "registers a scheduled task; runs in CI")
class TaskBackupTest(unittest.TestCase):
    def test_task_is_exported_before_it_is_removed(self):
        name = "NestsweepSelfTest" + hashlib.sha1(os.urandom(8)).hexdigest()[:6]
        try:
            common.powershell("$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c exit';"
                              " Register-ScheduledTask -TaskPath '\\' -TaskName $env:NS_NAME -Action $a | Out-Null",
                              args={"name": name}, strict=True)
        except Exception as e:
            self.skipTest(f"couldn't register a test task: {e}")
        before = set(common.BACKUP_DIR.glob("*.xml"))
        try:
            stats = un.remove_leftovers([un.Leftover("task", "\\" + name, {"path": "\\", "name": name})])
            self.assertEqual(stats["errors"], [])
            new = [p for p in set(common.BACKUP_DIR.glob("*.xml")) - before if name in p.name]
            self.assertEqual(len(new), 1)
            self.assertIn("cmd.exe", new[0].read_text(encoding="utf-16"))
        finally:
            try:          # normally already gone: remove_leftovers removed it
                common.powershell("Unregister-ScheduledTask -TaskPath '\\' -TaskName $env:NS_NAME -Confirm:$false"
                                  " -ErrorAction SilentlyContinue", args={"name": name})
            except RuntimeError:
                pass


if __name__ == "__main__":
    unittest.main()
