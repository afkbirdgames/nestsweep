"""Self-update from GitHub Releases.

  check()   asks the GitHub API for the latest release (one HTTPS request, no other data sent).
  install() downloads Nestsweep.exe, checks its SHA-256 against the release's .sha256 file and,
            once releases are code-signed, that it is signed by the same publisher as the running
            copy. Then it swaps the files and restarts.

Updates are required by default: until the user installs one, the UI refuses to run features
(see app.js). A release whose notes contain "[optional]" is offered but not enforced.

A running exe can't be overwritten, but Windows lets it be renamed. So the running copy is
renamed to Nestsweep.exe.old, the new file takes its place, the new copy is started, and the
.old file is removed the next time the app starts.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from . import __version__, common

REPO = "afkbirdgames/nestsweep"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ALLOWED_DOWNLOAD_HOSTS = ("https://github.com/", "https://objects.githubusercontent.com/",
                          "https://release-assets.githubusercontent.com/")
EXE_ASSET = "Nestsweep.exe"
SUM_ASSET = "Nestsweep.exe.sha256"
MAX_EXE_BYTES = 200 * 1024 * 1024


def parse_version(tag: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _get(url: str, timeout: float, limit: int) -> bytes:
    if not url.startswith(("https://api.github.com/",) + ALLOWED_DOWNLOAD_HOSTS):
        raise ValueError(f"unexpected update URL {url}")
    req = urllib.request.Request(url, headers={"User-Agent": f"Nestsweep/{__version__}",
                                               "Accept": "application/vnd.github+json, application/octet-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        final = r.geturl()
        if not final.startswith(("https://api.github.com/",) + ALLOWED_DOWNLOAD_HOSTS):
            raise ValueError(f"update download was redirected to {final}")
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("update download is larger than expected")
    return data


def release_info(release: dict, current: str = __version__) -> dict:
    """Turn a GitHub release JSON object into what the UI needs."""
    latest = parse_version(release.get("tag_name", ""))
    cur = parse_version(current)
    assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])}
    notes = release.get("body") or ""
    available = bool(latest and cur and latest > cur and not release.get("draft") and not release.get("prerelease")
                     and assets.get(EXE_ASSET) and assets.get(SUM_ASSET))
    return {
        "current": current,
        "latest": ".".join(map(str, latest)) if latest else None,
        "available": available,
        "required": available and "[optional]" not in notes.lower(),
        "notes": notes.replace("[optional]", "").replace("[Optional]", "").strip()[:4000],
        "page": release.get("html_url") or f"https://github.com/{REPO}/releases/latest",
        "exe_url": assets.get(EXE_ASSET),
        "sum_url": assets.get(SUM_ASSET),
        "can_install": common.FROZEN,
    }


def check(timeout: float = 8) -> dict:
    """Never raises: offline or GitHub trouble just means no update is offered."""
    try:
        return release_info(json.loads(_get(LATEST_API, timeout, 1_000_000)))
    except Exception as e:
        return {"current": __version__, "latest": None, "available": False, "required": False, "error": str(e)}


# ---------------------------------------------------------------- verification

def expected_sha256(sum_text: str) -> str:
    m = re.search(r"\b([0-9a-fA-F]{64})\b", sum_text)
    if not m:
        raise ValueError("the release checksum file is unreadable")
    return m.group(1).lower()


def _signer(path: str) -> tuple[str, str]:
    """(status, signer subject) from Windows' own Authenticode check."""
    out = common.powershell(
        "$s = Get-AuthenticodeSignature -LiteralPath $env:NS_P;"
        " [pscustomobject]@{ S = [string]$s.Status; N = [string]$s.SignerCertificate.Subject } | ConvertTo-Json -Compress",
        args={"p": path}, timeout=60)
    d = json.loads(out.strip() or "{}")
    return d.get("S", ""), d.get("N", "")


def verify(new_exe: Path, sha256: str, current_exe: str | None) -> None:
    """Raise if the download isn't exactly the published file, or (once releases are signed)
    isn't signed by the same publisher as the copy that's running."""
    data_ok = new_exe.stat().st_size > 1_000_000 and new_exe.read_bytes()[:2] == b"MZ"
    if not data_ok:
        raise ValueError("the download isn't a Windows program")
    h = hashlib.sha256(new_exe.read_bytes()).hexdigest()
    if h != sha256:
        raise ValueError("the download doesn't match the published checksum, so it wasn't installed")
    if current_exe:
        cur_status, cur_signer = _signer(current_exe)
        if cur_status == "Valid":
            new_status, new_signer = _signer(str(new_exe))
            if new_status != "Valid" or new_signer != cur_signer:
                raise ValueError("the download isn't signed by the same publisher, so it wasn't installed")


# ---------------------------------------------------------------- install

def swap(current_exe: Path, new_exe: Path) -> Path:
    """Replace the running exe with the new one. Returns the path of the renamed old copy."""
    old = current_exe.with_name(current_exe.name + ".old")
    if old.exists():
        old.unlink()
    os.replace(current_exe, old)          # renaming a running exe is allowed on Windows
    try:
        os.replace(new_exe, current_exe)
    except OSError:
        os.replace(old, current_exe)      # put the working copy back
        raise
    return old


def cleanup_old() -> None:
    """Remove the previous copy left by an update (it was still running at the time)."""
    if not common.FROZEN:
        return
    old = Path(sys.executable).with_name(Path(sys.executable).name + ".old")
    try:
        if old.exists():
            old.unlink()
    except OSError:
        pass


def install(info: dict, progress=None) -> str:
    """Download, verify and swap in the new version. Returns the path to start."""
    if not common.FROZEN:
        raise RuntimeError("Running from source: update with git pull instead.")
    if not info.get("available") or not info.get("exe_url") or not info.get("sum_url"):
        raise RuntimeError("No update is available.")
    current = Path(sys.executable)
    if progress:
        progress("Downloading the checksum")
    sha = expected_sha256(_get(info["sum_url"], 30, 10_000).decode("utf-8", "replace"))
    if progress:
        progress(f"Downloading Nestsweep {info['latest']}")
    data = _get(info["exe_url"], 300, MAX_EXE_BYTES)
    if hashlib.sha256(data).hexdigest() != sha:          # check what was downloaded, before it touches disk
        raise ValueError("the download doesn't match the published checksum, so it wasn't installed")
    # Write next to the running exe so the final swap is a same-folder rename.
    try:
        fd, tmp = tempfile.mkstemp(prefix=".nestsweep-update-", suffix=".exe", dir=current.parent)
    except OSError:
        raise PermissionError(f"Nestsweep can't write to {current.parent}. Download the new version from "
                              f"{info.get('page')} instead.")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if progress:
            progress("Checking the download")
        verify(tmp_path, sha, str(current))
        swap(current, tmp_path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return str(current)


def relaunch(exe: str) -> None:
    """Start the new copy with normal rights, even if this copy was running as administrator:
    Explorer launches it as the signed-in user. The user can choose "Restart as administrator"
    again if they need it."""
    if common.is_admin():
        subprocess.Popen([os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "explorer.exe"), exe],
                         close_fds=True)
        return
    flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe], close_fds=True, creationflags=flags, cwd=os.path.dirname(exe))
