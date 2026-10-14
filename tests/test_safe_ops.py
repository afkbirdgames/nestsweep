"""End-to-end checks that only touch throwaway data created by the test itself.

Run:  python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nestsweep import common, custom_clean as cc, safety, startup_manager as sm  # noqa: E402

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "NestsweepSelfTest"


class CustomCleanTest(unittest.TestCase):
    def test_analyse_then_clean_respects_masks_and_excludes(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp, "junk")
            (junk / "sub").mkdir(parents=True)
            for p in ("a.tmp", "b.log", "keep.txt", "sub/c.tmp"):
                (junk / p).write_text("x" * 1000)
            rules = Path(tmp, "rules.ini")
            rules.write_text(
                "[Rule Vars]\nJUNK=" + str(junk) + "\n"
                "[Test Junk]\nID=test.junk\nDefault=True\n"
                "FileKey1=<JUNK>|*.tmp;*.log|RECURSE\n"
                f"ExcludeKey1=FILE|{junk}\\b.log\n")
            old, cc.RULES_FILE = cc.RULES_FILE, rules
            try:
                found = cc.analyse()
                # Guard: never let this test clean anything outside its own temp folder
                outside = [f.target for f in found if not os.path.abspath(f.target).startswith(str(junk))]
                self.assertEqual(outside, [], "analysis escaped the test folder; refusing to clean")
                files = sorted(os.path.relpath(f.target, junk) for f in found if f.kind == "file")
                self.assertEqual(files, ["a.tmp", os.path.join("sub", "c.tmp")])
                stats = cc.clean(found)
                self.assertEqual(stats["files"], 2)
                left = sorted(str(p.relative_to(junk)) for p in junk.rglob("*"))
                self.assertEqual(left, ["b.log", "keep.txt"])      # empty sub/ removed too
            finally:
                cc.RULES_FILE = old

    def test_shipped_rules_parse(self):
        rules = cc.load_rules()
        self.assertGreater(len(rules), 10)
        self.assertEqual(len({r.id for r in rules}), len(rules))


class StartupManagerTest(unittest.TestCase):
    def setUp(self):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, r'"C:\Windows\notepad.exe"')

    def tearDown(self):
        for key in (RUN, sm.APPROVED + r"\Run"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, NAME)
            except OSError:
                pass

    def _item(self):
        return next((i for i in sm._startup_apps() if i.name == NAME), None)

    def test_disable_enable_delete(self):
        item = self._item()
        self.assertTrue(item.enabled)
        sm.set_enabled(item, False)
        self.assertFalse(self._item().enabled)
        flag = common.reg_get(winreg.HKEY_CURRENT_USER, sm.APPROVED + r"\Run", NAME)
        self.assertEqual(flag[0], 0x03)                       # same flag Task Manager writes
        sm.set_enabled(item, True)
        self.assertTrue(self._item().enabled)
        sm.delete(item)
        self.assertIsNone(self._item())


class SafetyTest(unittest.TestCase):
    def test_protected_folders(self):
        for p in (os.environ["SystemDrive"] + "\\", os.environ["WINDIR"], os.environ["ProgramFiles"],
                  os.environ["USERPROFILE"], os.environ["APPDATA"], os.path.dirname(os.environ["USERPROFILE"])):
            self.assertTrue(safety.is_protected_dir(p), p)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(safety.is_protected_dir(os.path.join(tmp)))

    def test_bad_temp_is_refused(self):
        self.assertFalse(safety.cleanup_base_ok(os.environ["SystemDrive"] + "\\"))
        self.assertFalse(safety.cleanup_base_ok(os.environ["USERPROFILE"]))
        self.assertFalse(safety.cleanup_base_ok(os.environ["WINDIR"]))
        self.assertTrue(safety.cleanup_base_ok(os.path.join(os.environ["WINDIR"], "Temp")))

    def test_unset_variable_expands_to_nothing(self):
        self.assertEqual(cc.expand(r"%NESTSWEEP_NOT_SET%\Temp"), "")

    def test_cleanup_never_follows_junctions(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk, outside = Path(tmp, "junk"), Path(tmp, "outside")
            junk.mkdir(); outside.mkdir()
            (junk / "a.tmp").write_text("x")
            (outside / "precious.tmp").write_text("keep me")
            import _winapi
            try:
                _winapi.CreateJunction(str(outside), str(junk / "link"))
            except OSError as e:
                self.skipTest(f"junctions not supported here: {e}")
            self.assertTrue(safety.is_reparse(str(junk / "link")))
            rules = Path(tmp, "rules.ini")
            rules.write_text("[Test]\nID=test.junction\nDefault=True\nFileKey1=" + str(junk) + "|*|RECURSE\n")
            old, cc.RULES_FILE = cc.RULES_FILE, rules
            try:
                found = cc.analyse()
                self.assertEqual([os.path.basename(f.target) for f in found if f.kind == "file"], ["a.tmp"])
                cc.clean(found)
                self.assertTrue((outside / "precious.tmp").exists())
            finally:
                cc.RULES_FILE = old
                os.rmdir(junk / "link")


if __name__ == "__main__":
    unittest.main()
