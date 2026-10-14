"""Thin wrapper over the Windows Update Agent COM API (Microsoft.Update.Session).

Used by App updates (Type='Software' patches) and Drivers (Type='Driver').
"""
from __future__ import annotations

import re

from . import common

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def search(kind: str) -> list[dict]:
    if kind not in ("Driver", "Software"):
        raise ValueError(kind)
    script = f"""
$s = New-Object -ComObject Microsoft.Update.Session
$r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0 and Type='{kind}'")
foreach ($u in $r.Updates) {{
  [pscustomobject]@{{
    Id = $u.Identity.UpdateID; Title = $u.Title; KB = ($u.KBArticleIDs -join ',')
    Severity = [string]$u.MsrcSeverity; Size = [int64]$u.MaxDownloadSize
    Categories = (($u.Categories | ForEach-Object {{ $_.Name }}) -join ', ')
    Optional = -not $u.AutoSelectOnWebSites
    DriverModel = [string]$u.DriverModel; DriverManufacturer = [string]$u.DriverManufacturer
    DriverClass = [string]$u.DriverClass
    DriverVerDate = if ($u.DriverVerDate) {{ ([datetime]$u.DriverVerDate).ToString('yyyy-MM-dd') }} else {{ '' }}
    DriverHardwareID = [string]$u.DriverHardwareID
  }}
}}"""
    return common.powershell_json(script, timeout=900)


def install(update_ids: list[str]) -> list[dict]:
    """Download + install selected updates. Requires administrator."""
    if not common.is_admin():
        raise PermissionError("Installing Windows updates requires administrator rights.")
    if not update_ids or not all(_GUID.match(i or "") for i in update_ids):
        raise ValueError("unexpected Windows Update id")
    ids = ",".join(f"'{i}'" for i in update_ids)     # validated GUIDs only
    script = f"""
$want = @({ids})
$s = New-Object -ComObject Microsoft.Update.Session
$r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0")
$col = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $r.Updates) {{ if ($want -contains $u.Identity.UpdateID) {{ if (-not $u.EulaAccepted) {{ $u.AcceptEula() }}; [void]$col.Add($u) }} }}
if ($col.Count -eq 0) {{ return }}
$d = $s.CreateUpdateDownloader(); $d.Updates = $col; [void]$d.Download()
$i = $s.CreateUpdateInstaller(); $i.Updates = $col; $res = $i.Install()
for ($n = 0; $n -lt $col.Count; $n++) {{
  $ur = $res.GetUpdateResult($n)
  [pscustomobject]@{{ Id = $col.Item($n).Identity.UpdateID; Title = $col.Item($n).Title
    ResultCode = [int]$ur.ResultCode; RebootRequired = [bool]$ur.RebootRequired }}
}}"""
    return common.powershell_json(script, timeout=3600)


RESULT = {0: "not_started", 1: "in_progress", 2: "successfully_updated", 3: "succeeded_with_errors", 4: "failed", 5: "aborted"}
