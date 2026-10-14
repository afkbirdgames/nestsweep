/* Browser-only stand-in for nestsweep.api so the UI can be previewed without Windows (index.html?mock). Not bundled. */
(function () {
  const GB = 1024 ** 3, MB = 1024 ** 2;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const ok = (data) => ({ ok: true, data });
  const admin = location.search.includes("admin");
  const rules = [
    ["windows.user_temp", "User temporary files", ["System", "Temp"], true],
    ["windows.system_temp", "System temporary files", ["System", "Temp"], true],
    ["windows.recyclebin", "Empty Recycle Bin", ["System"], true],
    ["windows.thumbnails", "Thumbnail cache", ["System"], false],
    ["windows.wer", "Windows error reports", ["System", "Logs"], true],
    ["windows.update_logs", "Windows Update logs", ["System", "Logs"], true],
    ["chrome.cache", "Chrome cache", ["Browsers"], true],
    ["edge.cache", "Edge cache", ["Browsers"], true],
    ["brave.cache", "Brave cache", ["Browsers"], true],
    ["chrome.history", "Chrome history", ["Browsers", "Privacy"], false],
    ["discord.cache", "Discord cache", ["Apps"], true],
    ["steam.logs", "Steam logs and dumps", ["Apps"], true],
    ["nvidia.cache", "NVIDIA shader cache", ["Apps"], false],
    ["windows.dns", "DNS cache", ["Network"], true],
  ].map(([id, name, tags, d]) => ({ id, name, tags, default: d, warning: null, processes: id.startsWith("discord") ? ["discord.exe"] : [] }));
  const sizes = { "windows.user_temp": 1.12 * GB, "windows.system_temp": 214 * MB, "windows.recyclebin": 640 * MB, "windows.thumbnails": 86 * MB, "windows.wer": 68 * MB, "windows.update_logs": 20 * MB, "chrome.cache": 905 * MB, "edge.cache": 410 * MB, "brave.cache": 0, "chrome.history": 12 * MB, "discord.cache": 213 * MB, "steam.logs": 44 * MB, "nvidia.cache": 302 * MB, "windows.dns": 0 };
  const programs = [
    ["Discord", "Ubisoft", "1.0.9163", "2026-08-02", 412 * MB], ["Steam", "Valve Corporation", "2.10.91.91", "2025-11-19", 5.6 * GB],
    ["Visual Studio Code", "Microsoft Corporation", "1.104.2", "2026-09-30", 391 * MB], ["OBS Studio", "OBS Project", "31.1.2", "2026-07-11", 512 * MB],
    ["Logitech G HUB", "Logitech", "2026.6.7832", "2026-06-15", 1.3 * GB], ["Epic Games Launcher", "Epic Games, Inc.", "18.4.0", "2026-03-02", 220 * MB],
    ["7-Zip 24.09 (x64)", "Igor Pavlov", "24.09", "2025-02-14", 5.9 * MB], ["Python 3.11.9 (64-bit)", "Python Software Foundation", "3.11.9150.0", "2025-01-08", 104 * MB],
    ["Spotify", "Spotify AB", "1.2.70.409", "2026-10-01", 0], ["Voicemod", "Voicemod S.L.", "3.12.24", "2026-09-28", 640 * MB],
    ["NVIDIA App", "NVIDIA Corporation", "11.0.4.526", "2026-09-02", 912 * MB], ["Microsoft Teams", "Microsoft Corporation", "25241.203", "2026-08-21", 0],
  ].map(([name, publisher, version, installed, size], i) => ({ id: "u" + i, name, publisher: name === "Discord" ? "Discord Inc." : publisher, version, installed, size, store: name === "Spotify" || name === "Microsoft Teams", location: "", command: `"C:\\Program Files\\${name}\\uninstall.exe"`, per_user: false }));

  const api = {
    app_info: async () => ok({ name: "Nestsweep", version: "1.0.2", admin, data_dir: "C:\\Users\\you\\AppData\\Local\\Nestsweep", backup_dir: "C:\\Users\\you\\AppData\\Local\\Nestsweep\\backups", frozen: true, github: "https://github.com/afkbirdgames/nestsweep" }),
    overview: async () => ok({ drive: "C:", total: 931 * GB, used: 249 * GB, free: 682 * GB, cleaned_total: 1.1 * GB, last_clean: "2026-10-11 21:40:00", last_analysis: null, counts: window.__counts, admin }),
    cc_rules: async () => { await wait(150); return ok(rules); },
    cc_analyse: async (ids) => {
      for (let i = 0; i < 10; i++) { await wait(90); window.nestsweep.onProgress({ feature: "cleanup", text: "Checking " + rules[i % rules.length].name, pct: i / 10 }); }
      const r = {}; let t = 0, n = 0;
      ids.forEach((id) => { const b = Math.round(sizes[id] || 0); const items = b ? Math.max(1, Math.round(b / 180000)) : 0; r[id] = { items, bytes: b }; t += b; n += items; });
      return ok({ rules: r, total_bytes: t, total_items: n, blockers: ids.includes("discord.cache") ? { "discord.cache": ["discord.exe"] } : {}, time: new Date().toISOString().slice(0, 19).replace("T", " ") });
    },
    cc_blockers: async (ids) => ok(ids.includes("discord.cache") ? { "discord.cache": ["discord.exe"] } : {}),
    cc_clean: async (ids) => { await wait(900); return ok({ files: 4210, bytes: ids.reduce((a, i) => a + (sizes[i] || 0), 0), errors: 0, skipped_in_use: 37 }); },
    un_scan: async () => { await wait(500); return ok(programs); },
    un_uninstall: async () => { await wait(800); return ok({ kept: [], leftovers: [{ id: "0:0", program: "Epic Games Launcher", kind: "folder", target: "C:\\Program Files (x86)\\Epic Games\\Launcher", size: 88 * MB, suggested: true }, { id: "0:1", program: "Epic Games Launcher", kind: "registry", target: "HKCU\\Software\\Epic Games\\EOS", size: 0, suggested: true }, { id: "0:2", program: "Epic Games Launcher", kind: "folder", target: "C:\\Users\\you\\AppData\\Local\\EpicGamesLauncher", size: 12 * MB, suggested: false }] }); },
    un_remove_leftovers: async (ids) => ok({ removed: ids.length, errors: [] }),
    po_scan: async () => { await wait(600); window.__counts.asleep = 1; return ok([
      { id: "p1", name: "Logitech G HUB", state: "found", impact: "high", location: "C:\\Program Files\\LGHUB", exes: 3, entries: [{ type: "service", name: "LGHUBUpdaterService", path: "C:\\Program Files\\LGHUB\\lghub_updater.exe" }, { type: "registry_run", name: "LGHUB", path: "C:\\Program Files\\LGHUB\\lghub.exe --background" }] },
      { id: "p2", name: "Epic Games Launcher", state: "deactivated", impact: "medium", location: "C:\\Program Files (x86)\\Epic Games", exes: 1, entries: [{ type: "registry_run", name: "EpicGamesLauncher", path: "EpicGamesLauncher.exe -silent" }] },
      { id: "p3", name: "Voicemod", state: "found", impact: "high", location: "C:\\Program Files\\Voicemod V3", exes: 2, entries: [{ type: "service", name: "VoicemodDesktopService", path: "VoicemodDesktopService.exe" }, { type: "scheduled_task", name: "\\Voicemod\\Updater", path: "updater.exe" }] },
      { id: "p4", name: "Steam", state: "found", impact: "low", location: "C:\\Program Files (x86)\\Steam", exes: 1, entries: [{ type: "service", name: "Steam Client Service", path: "SteamService.exe" }] },
      { id: "p5", name: "Adobe Genuine Service", state: "ignored", impact: "medium", location: "", exes: 1, entries: [{ type: "service", name: "AGSService", path: "AGSService.exe" }] },
    ]); },
    po_sleep: async (ids) => { await wait(500); return ok({ programs: ids.length, entries: ids.length * 2, failed: [] }); },
    po_wake: async () => ok({ restored: 1, failed: [] }), po_wake_all: async () => ok({ restored: 1, failed: [] }), po_ignore: async () => ok(true),
    sm_scan: async () => { await wait(500); const items = [
      ["Discord", "Discord Inc.", "\"C:\\Users\\you\\AppData\\Local\\Discord\\Update.exe\" --processStart Discord.exe", true],
      ["Steam", "Valve Corporation", "\"C:\\Program Files (x86)\\Steam\\steam.exe\" -silent", true],
      ["Spotify", "Spotify AB", "C:\\Users\\you\\AppData\\Roaming\\Spotify\\Spotify.exe --autostart", false],
      ["OneDrive", "Microsoft Corporation", "\"C:\\Program Files\\Microsoft OneDrive\\OneDrive.exe\" /background", true],
      ["Logitech G HUB", "Logitech", "\"C:\\Program Files\\LGHUB\\lghub.exe\" --background", true],
      ["Voicemod", "Voicemod S.L.", "\"C:\\Program Files\\Voicemod V3\\Voicemod.exe\" -s", false],
    ].map(([name, publisher, command, enabled], i) => ({ id: "s" + i, type: "startup_app", name, publisher, command, enabled, location: "HKCU\\...\\Run", can_toggle: true }));
      items.push({ id: "t1", type: "scheduled_task", name: "\\GoogleUpdateTaskMachineUA", publisher: "Google LLC", command: "GoogleUpdate.exe /ua", enabled: true, location: "Task Scheduler", can_toggle: true });
      items.push({ id: "w1", type: "windows_service", name: "Steam Client Service", publisher: "Valve Corporation", command: "SteamService.exe", enabled: false, location: "Manual", can_toggle: true });
      window.__counts.startup_enabled = 4; return ok(items); },
    sm_set_enabled: async () => { await wait(200); return admin ? ok(true) : ok(true); },
    sm_delete: async () => ok(true),
    su_scan: async () => { await wait(700); window.__counts.updates = 4; return ok({ notes: [], items: [
      { id: "Discord.Discord", name: "Discord", current: "1.0.9163", available: "1.0.9171", source: "winget", category: "application", severity: "unknown", size: 0, status: "" },
      { id: "OBSProject.OBSStudio", name: "OBS Studio", current: "31.1.2", available: "32.0.1", source: "winget", category: "application", severity: "unknown", size: 0, status: "" },
      { id: "7zip.7zip", name: "7-Zip", current: "24.09", available: "25.01", source: "winget", category: "application", severity: "unknown", size: 0, status: "" },
      { id: "kb1", name: "2026-10 Cumulative Update for Windows 11 Version 24H2 (KB5066835)", current: "", available: "KB5066835", source: "Windows Update", category: "windows-patch", severity: "critical", size: 0, status: "" },
    ] }); },
    su_update: async (ids) => { await wait(900); return ok(ids.map((id) => ({ id, name: id, current: "", available: "", source: "winget", category: "application", status: "successfully_updated" }))); },
    su_skip: async () => ok(true),
    du_scan: async () => { await wait(800); window.__counts.drivers = 2; return ok([
      { id: "d1", name: "NVIDIA GeForce RTX 4070", category: "Display", manufacturer: "NVIDIA", version: "32.0.15.6094", date: "2025-08-14", problem: 0, attention: true, update: { title: "NVIDIA - Display - 32.0.15.8129", date: "2026-09-02" }, backups: [], can_backup: true },
      { id: "d2", name: "Realtek PCIe 2.5GbE Family Controller", category: "Net", manufacturer: "Realtek", version: "10.68.815.2024", date: "2024-08-15", problem: 0, attention: true, update: { title: "Realtek - Net - 10.72", date: "2026-05-20" }, backups: ["Realtek_PCIe_10.68.zip"], can_backup: true },
      { id: "d3", name: "USB Composite Device", category: "USB", manufacturer: "(Standard USB Host Controller)", version: "10.0.26100.1", date: "2006-06-21", problem: 28, problem_text: "No driver installed", attention: true, update: null, backups: [], can_backup: false },
      { id: "d5", name: "Realtek High Definition Audio", category: "MEDIA", manufacturer: "Realtek", version: "6.0.9549.1", date: "2023-09-14", problem: 22, problem_text: "Disabled", attention: false, update: null, backups: [], can_backup: true },
      { id: "d4", name: "Logitech G Pro Wheel", category: "HIDClass", manufacturer: "Logitech", version: "2026.6.1", date: "2026-06-01", problem: 0, update: null, backups: [], can_backup: true },
    ]); },
    du_update: async (ids, rp) => { await wait(900); if (rp) return { ok: false, kind: "RestorePointError", error: "Windows didn't create a restore point. It allows one every 24 hours, or System Restore may be turned off for this drive." }; return ok({ results: ids.map((id) => ({ Id: id, ResultCode: 2, RebootRequired: true })), skipped: [] }); },
    du_backup: async (ids) => ok(ids.map((i) => i + ".zip")),
    du_restore: async () => ok({ device: "Realtek PCIe", version: "10.68", reboot_required: false }),
    update_status: async () => ok(location.search.includes("update") ? { current: "1.0.2", latest: "1.0.3", available: true, required: !location.search.includes("optional"), can_install: true, page: "https://github.com/afkbirdgames/nestsweep/releases/tag/v1.0.3", notes: "Fixes a case where Cleanup could skip files it should have removed.\nSafer handling of Store apps in the Uninstaller." } : { current: "1.0.2", latest: "1.0.2", available: false, required: false }),
    update_install: async () => { await wait(1500); return { ok: false, kind: "ValueError", error: "the download doesn't match the published checksum, so it wasn't installed" }; },
    restart_as_admin: async () => ok(true), open_path: async () => ok(true), open_url: async () => ok(true),
  };
  window.__counts = { startup_enabled: null, updates: null, asleep: null, drivers: null };
  window.pywebview = { api };
  window.dispatchEvent(new Event("pywebviewready"));
})();
