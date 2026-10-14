# Builds dist\Nestsweep.exe. Run from this folder in PowerShell:  ./build.ps1
# Needs Python 3.11 (64-bit) from python.org or the Microsoft Store; uses an isolated .venv-build.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Get-Command py -ErrorAction SilentlyContinue) { $py = "py -3.11" } else { $py = "python" }
if (-not (Test-Path .venv-build)) { Invoke-Expression "$py -m venv .venv-build" }
& .\.venv-build\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
& .\.venv-build\Scripts\python.exe -m PyInstaller --noconfirm --clean nestsweep.spec

$exe = Join-Path $PSScriptRoot "dist\Nestsweep.exe"
if (Test-Path $exe) {
    $hash = (Get-FileHash $exe -Algorithm SHA256).Hash
    "Built $exe"
    "SHA256 $hash"
    "$hash  Nestsweep.exe" | Set-Content (Join-Path $PSScriptRoot "dist\Nestsweep.exe.sha256")
} else { throw "Build failed: dist\Nestsweep.exe not found" }
