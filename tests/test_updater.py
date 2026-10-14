"""Self-update logic. Nothing here touches the network or the running exe."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nestsweep import updater  # noqa: E402


def release(tag, body="", assets=("Nestsweep.exe", "Nestsweep.exe.sha256"), **extra):
    return {"tag_name": tag, "body": body, "html_url": "https://github.com/afkbirdgames/nestsweep/releases/tag/" + tag,
            "assets": [{"name": a, "browser_download_url": f"https://github.com/afkbirdgames/nestsweep/releases/download/{tag}/{a}"}
                       for a in assets], **extra}


class ReleaseInfoTest(unittest.TestCase):
    def test_newer_release_is_required_by_default(self):
        info = updater.release_info(release("v1.2.0", "Fixes things"), "1.0.2")
        self.assertTrue(info["available"])
        self.assertTrue(info["required"])

    def test_optional_marker(self):
        info = updater.release_info(release("v1.2.0", "Small tweaks [optional]"), "1.0.2")
        self.assertTrue(info["available"])
        self.assertFalse(info["required"])
        self.assertNotIn("[optional]", info["notes"])

    def test_same_older_draft_or_incomplete_releases_are_ignored(self):
        self.assertFalse(updater.release_info(release("v1.0.2"), "1.0.2")["available"])
        self.assertFalse(updater.release_info(release("v1.0.1"), "1.0.2")["available"])
        self.assertFalse(updater.release_info(release("v2.0.0", prerelease=True), "1.0.2")["available"])
        self.assertFalse(updater.release_info(release("v2.0.0", assets=("Nestsweep.exe",)), "1.0.2")["available"])
        self.assertFalse(updater.release_info(release("latest"), "1.0.2")["available"])

    def test_version_compare_is_numeric(self):
        self.assertTrue(updater.release_info(release("v1.0.10"), "1.0.9")["available"])


class VerifyAndSwapTest(unittest.TestCase):
    def test_checksum_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp, "new.exe")
            exe.write_bytes(b"MZ" + b"\0" * 2_000_000)
            good = hashlib.sha256(exe.read_bytes()).hexdigest()
            updater.verify(exe, good, None)                      # matches: no error
            with self.assertRaises(ValueError):
                updater.verify(exe, "0" * 64, None)

    def test_non_program_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "new.exe")
            f.write_bytes(b"<html>" + b"\0" * 2_000_000)
            with self.assertRaises(ValueError):
                updater.verify(f, hashlib.sha256(f.read_bytes()).hexdigest(), None)

    def test_checksum_file_parsing(self):
        h = "A" * 64
        self.assertEqual(updater.expected_sha256(f"{h}  Nestsweep.exe\r\n"), h.lower())
        with self.assertRaises(ValueError):
            updater.expected_sha256("not a checksum")

    def test_swap_keeps_old_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            cur, new = Path(tmp, "Nestsweep.exe"), Path(tmp, ".nestsweep-update-1.exe")
            cur.write_bytes(b"old"); new.write_bytes(b"new")
            old = updater.swap(cur, new)
            self.assertEqual(cur.read_bytes(), b"new")
            self.assertEqual(old.read_bytes(), b"old")
            self.assertFalse(new.exists())

    def test_only_github_urls_are_fetched(self):
        with self.assertRaises(ValueError):
            updater._get("https://example.com/Nestsweep.exe", 1, 10)


@unittest.skipUnless(__import__("os").environ.get("GITHUB_ACTIONS"), "downloads the real release; runs in CI")
class LiveReleaseTest(unittest.TestCase):
    """End to end against the published release: check, download through GitHub's redirects,
    verify the checksum and swap into a scratch folder."""

    def test_download_verify_swap(self):
        import json
        import urllib.error
        try:
            rel = json.loads(updater._get(updater.LATEST_API, 30, 1_000_000))
        except urllib.error.URLError as e:          # includes HTTPError, e.g. GitHub's API rate limit
            self.skipTest(f"GitHub API unavailable from this runner: {e}")
        info = updater.release_info(rel, "0.0.1")
        if not info["exe_url"]:
            self.skipTest("the latest release's exe is still being built and attached")
        self.assertTrue(info["available"], info)
        sha = updater.expected_sha256(updater._get(info["sum_url"], 30, 10_000).decode())
        data = updater._get(info["exe_url"], 300, updater.MAX_EXE_BYTES)
        with tempfile.TemporaryDirectory() as tmp:
            new, cur = Path(tmp, ".nestsweep-update-x.exe"), Path(tmp, "Nestsweep.exe")
            new.write_bytes(data)
            cur.write_bytes(b"old")
            updater.verify(new, sha, str(cur))
            updater.swap(cur, new)
            self.assertEqual(hashlib.sha256(cur.read_bytes()).hexdigest(), sha)


if __name__ == "__main__":
    unittest.main()
