<p align="center"><img src="assets/nestsweep-icon-1024.png" width="120" alt=""></p>

# Nestsweep

Free, open-source PC care for Windows. Clear junk files, uninstall programs along with what they
leave behind, put idle background apps to sleep, choose what starts with Windows, and keep apps
and drivers up to date.

## Download

Get `Nestsweep.exe` from the [latest release](https://github.com/afkbirdgames/nestsweep/releases/latest)
and run it. There's no installer and no account, and nothing about your PC is sent anywhere.
Each release lists the exe's SHA-256 so you can check your download.

Needs Windows 10 (1809 or later) or Windows 11 with the Microsoft Edge WebView2 Runtime, which
Windows 11 and current Windows 10 already include.

Nestsweep starts with your normal rights. Choose **Restart as administrator** in the app for the
tools that change machine-wide settings: Sleep mode, drivers, Windows patches, and programs
installed for all users.

## What it does

| Section | What it does |
|---|---|
| Overview | Free space, what's been cleared, and one button to check everything below |
| Cleanup | Clears temp files, caches, logs and privacy traces using the rules in `nestsweep/rules.ini`. Analyze first, then clean |
| Uninstaller | Runs a program's own uninstaller, then lists leftover folders, registry keys, services, tasks and shortcuts for you to confirm |
| Sleep mode | Turns off a program's services, scheduled tasks and startup entries and records how to turn them back on |
| Startup | Switches startup apps, scheduled tasks, services and right-click menu entries on and off |
| App updates | Newer app versions through **winget**, plus missing patches from Windows Update |
| Drivers | Newer drivers from Windows Update, with a restore point and a backup first |

## Safety

Nestsweep is built to leave something behind rather than delete the wrong thing.

- **Everything is recorded.** Changes go into a journal at `%LOCALAPPDATA%\Nestsweep\state.db`.
  Registry keys are exported to `%LOCALAPPDATA%\Nestsweep\backups` before they're deleted, and a
  key that can't be exported is left alone.
- **Cleanup**
  - It never follows junctions or symbolic links.
  - It skips a rule if that rule's path uses an environment variable that isn't set.
  - It won't empty drive roots, Windows, Program Files, user profiles, AppData roots or your
    personal folders.
  - It checks every path again right before deleting it.
- **Uninstaller**
  - It only looks for leftovers when the program really is gone. If you cancel the vendor
    uninstaller, nothing is swept.
  - It ignores install folders that are too broad or shared with another program, anything
    inside Windows, and Microsoft services and tasks.
  - Only items that clearly belonged to the program come pre-ticked.
- **Sleep mode and Startup** never touch Microsoft, svchost, Windows-folder or protected
  (anti-malware) services, or tasks under `\Microsoft\`.
- **Drivers**
  - Only drivers newer than the installed one are offered.
  - A System Restore point is created and confirmed first. If Windows won't make one, nothing
    is installed unless you choose to continue.
  - Each third-party driver is backed up with `pnputil`. A device whose backup fails is
    skipped.
  - Backups are kept in an administrators-only folder (`%ProgramData%\Nestsweep\DriverBackups`)
    and checked against their recorded SHA-256 before they're restored.
- **Commands**
  - Windows tools are always run from System32.
  - Names that come from the system, such as task or package names, are passed to PowerShell
    as data, never as part of a script.

Found a security problem? Please report it privately through the repository's **Security** tab
rather than opening a public issue.

## Run from source

```powershell
py -3.11 -m pip install -r requirements.txt
py -3.11 -m nestsweep
```

Add `--debug` to open the WebView developer tools. The UI is plain HTML, CSS and JS in
`nestsweep/ui`. To work on it with sample data and no Windows APIs, open
`nestsweep/ui/index.html?mock` in a browser.

## Build the exe

```powershell
./build.ps1        # creates .venv-build, installs the pinned dependencies, runs PyInstaller -> dist\Nestsweep.exe
```

Every push runs `.github/workflows/build.yml` on a Windows runner: tests, then a build. Publishing
a GitHub release (for example tag `v1.0.0`) does the same and attaches `Nestsweep.exe` and its
SHA-256 checksum to that release.

## Tests

```powershell
py -3.11 -m unittest discover -s tests -v
```

The tests only touch files and registry values they create themselves.

## License

[MIT](LICENSE). Bundled components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
