"""Guards that keep a non-technical user's files and PC safe. Read-only except for files the
tests create themselves in a temp folder."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nestsweep import custom_clean as cc, driver_updater as du, safety, uninstaller as un  # noqa: E402


class PersonalFolderTest(unittest.TestCase):
    def test_every_profiles_personal_folders_are_protected(self):
        users = os.path.dirname(os.environ["USERPROFILE"])
        for sub in ("Documents", "Desktop", "Pictures", "Downloads", "Saved Games", "OneDrive - Contoso"):
            p = os.path.join(users, "SomeoneElse", sub)
            self.assertTrue(safety.is_protected_dir(p), p)
            self.assertTrue(safety.in_personal_folder(os.path.join(p, "Tools")), p)

    def test_program_files_detection(self):
        self.assertTrue(safety.under_program_files(os.path.join(os.environ["ProgramFiles"], "Vendor", "App")))
        self.assertFalse(safety.under_program_files(os.path.join(os.environ["USERPROFILE"], "Documents", "App")))


class RecycleTest(unittest.TestCase):
    def test_folder_goes_to_recycle_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "leftover")
            (d / "sub").mkdir(parents=True)
            (d / "sub" / "f.txt").write_text("x")
            safety.recycle(str(d))
            self.assertFalse(d.exists())


class CleanupDefaultsTest(unittest.TestCase):
    def test_recycle_bin_is_off_by_default(self):
        rule = next(r for r in cc.load_rules() if r.id == "windows.recyclebin")
        self.assertFalse(rule.default)
        self.assertTrue(rule.warning)

    def test_temp_rules_skip_recent_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp, "temp")
            junk.mkdir()
            (junk / "new.tmp").write_text("fresh")
            old = junk / "old.tmp"
            old.write_text("stale")
            past = time.time() - 72 * 3600
            os.utime(old, (past, past))
            rules = Path(tmp, "rules.ini")
            rules.write_text(f"[T]\nID=t\nDefault=True\nMinAgeHours=48\nFileKey1={junk}|*.tmp\n")
            orig, cc.RULES_FILE = cc.RULES_FILE, rules
            try:
                names = {os.path.basename(f.target) for f in cc.analyse() if f.kind == "file"}
            finally:
                cc.RULES_FILE = orig
            # new.tmp was just created, so it is skipped (creation time counts, not just modified time)
            self.assertNotIn("new.tmp", names)


class DriverClassTest(unittest.TestCase):
    def test_boot_input_and_network_classes_are_never_offered(self):
        for c in ("System", "HDC", "SCSIAdapter", "Net", "USB", "Keyboard", "Mouse", "Firmware"):
            self.assertFalse(du.offerable(c), c)
        for c in ("Display", "MEDIA", "Bluetooth", "Printer"):
            self.assertTrue(du.offerable(c), c)


class LeftoverPreTickTest(unittest.TestCase):
    def test_install_folder_outside_program_files_is_not_pre_ticked(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc = Path(tmp, "Games", "Some Game")
            loc.mkdir(parents=True)
            (loc / "save.dat").write_text("precious")
            sw = un.Software("x", "Some Game", "1.0", "Nobody", str(loc), "", 0, "", "", False, "HKCU\\nothing", 0)
            folders = [lo for lo in un.find_leftovers(sw, [sw]) if lo.kind == "folder"]
            self.assertTrue(folders, "the install folder should still be offered")
            self.assertFalse(any(lo.detail.get("suggested") for lo in folders))


if __name__ == "__main__":
    unittest.main()
