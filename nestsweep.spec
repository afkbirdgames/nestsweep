# PyInstaller build for Nestsweep.exe  ->  pyinstaller nestsweep.spec   (output: dist/Nestsweep.exe)
from pathlib import Path

ui = Path("nestsweep/ui")
datas = [("nestsweep/rules.ini", "nestsweep")]
for f in ui.rglob("*"):
    if f.is_file() and "dev" not in f.relative_to(ui).parts:        # dev/mock.js is for browser previews only
        datas.append((str(f), str(Path("nestsweep") / f.parent.relative_to("nestsweep"))))

a = Analysis(
    ["run_nestsweep.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=["webview.platforms.edgechromium", "webview.platforms.winforms", "clr"],
    excludes=["tkinter", "_tkinter", "unittest", "pydoc_data", "pkg_resources", "setuptools", "jaraco", "webview.platforms.qt", "webview.platforms.gtk",
              "webview.platforms.cocoa", "webview.platforms.android", "webview.platforms.cef", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="Nestsweep",
    icon="assets/nestsweep.ico",
    version="version_info.txt",
    console=False,
    uac_admin=False,          # starts as the user; "Restart as administrator" elevates on demand
    upx=False,                # UPX-packed exes trip antivirus false positives
    runtime_tmpdir=None,
)
