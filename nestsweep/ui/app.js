/* Nestsweep UI. Plain JS: one render function per page, state kept in `S`, all work done by window.pywebview.api. */
(function () {
  "use strict";

  // ------------------------------------------------------------------ helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const I = window.ICON;

  function size(n, opts = {}) {
    n = Number(n) || 0;
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    const num = i === 0 ? String(Math.round(n)) : n >= 100 ? n.toFixed(0) : n.toFixed(1);
    return opts.split ? [num, units[i]] : `${num} ${units[i]}`;
  }
  function ago(stamp) {
    if (!stamp) return "never";
    const t = new Date(stamp.replace(" ", "T"));
    const s = (Date.now() - t.getTime()) / 1000;
    if (isNaN(s)) return stamp;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    const d = Math.round(s / 86400);
    return d === 1 ? "yesterday" : `${d} days ago`;
  }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;

  // ------------------------------------------------------------------ state
  const S = {
    page: "overview",
    info: { admin: false, version: "", data_dir: "", backup_dir: "" },
    ov: null,
    busy: {},          // feature -> label
    cc: { rules: null, picked: new Set(), result: null, loaded: false },
    un: { items: null, picked: new Set(), q: "", sort: ["name", 1] },
    po: { items: null, picked: new Set(), open: null },
    sm: { items: null, kind: "startup_app", q: "" },
    su: { items: null, picked: new Set(), patches: true, notes: [] },
    du: { items: null, picked: new Set(), only: true },
    update: null,      // latest-release check result
  };

  // ------------------------------------------------------------------ api
  async function call(name, ...args) {
    const res = await window.pywebview.api[name](...args);
    if (!res || !res.ok) {
      const err = new Error((res && res.error) || "Something went wrong.");
      err.kind = res && res.kind;
      throw err;
    }
    return res.data;
  }

  // Run a long operation for a feature: progress line, status text, error toast.
  async function work(feature, label, fn, quiet = []) {
    if (S.busy[feature]) throw Object.assign(new Error("Already working on this."), { kind: "Busy" });
    S.busy[feature] = label;
    paintBusy();
    rerender();
    try {
      return await fn();
    } catch (e) {
      if (quiet === true || quiet.includes(e.kind)) throw e;
      const admin = e.kind === "PermissionError" && !S.info.admin;
      toast("err", admin ? "Administrator rights needed" : "That didn't work", e.message, admin ? { label: "Restart as administrator", run: elevate } : null);
      throw e;
    } finally {
      delete S.busy[feature];
      // Let the caller store the result (it resumes in a microtask) before repainting.
      setTimeout(rerender, 0);
    }
  }

  window.nestsweep = {
    onProgress({ feature, text, pct }) {
      if (!S.busy[feature]) return;
      S.busy[feature] = text;
      if (PAGES[S.page].feature === feature) paintBusy(pct);
    },
  };

  function paintBusy(pct) {
    const f = PAGES[S.page].feature;
    const label = f && S.busy[f];
    const bar = $("#progress");
    bar.classList.toggle("on", !!label);
    bar.classList.toggle("indet", !!label && (pct == null));
    bar.firstElementChild.style.width = pct != null ? `${Math.max(4, pct * 100)}%` : "";
    const anyBusy = Object.keys(S.busy).length > 0;
    $("#statusLine").classList.toggle("busy", anyBusy);
    $("#statusText").textContent = label ? label + "…" : anyBusy ? Object.values(S.busy)[0] + "…" : (S.status || "Ready");
  }
  function setStatus(text) { S.status = text; paintBusy(); }

  // ------------------------------------------------------------------ toast + modal
  function toast(kind, title, text, action) {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.innerHTML = `${kind === "ok" ? I.checkCircle : kind === "err" ? I.alert : I.info}
      <div><b>${esc(title)}</b><span>${esc(text || "")}</span>
      ${action ? `<div style="margin-top:10px"><button class="btn sm">${esc(action.label)}</button></div>` : ""}</div>`;
    if (action) $("button", el).onclick = () => { el.remove(); action.run(); };
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), action ? 9000 : 5200);
  }

  function modal({ title, body, confirm = "Continue", cancel = "Cancel", danger = false, wide = false, left = "", focusCancel = false }) {
    return new Promise((resolve) => {
      const scrim = document.createElement("div");
      scrim.className = "scrim";
      scrim.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">
        <div class="modal-head"><h2>${esc(title)}</h2></div>
        <div class="modal-body">${body}</div>
        <div class="modal-foot">${left ? `<div class="left">${left}</div>` : ""}
          ${cancel ? `<button class="btn ghost" data-r="0">${esc(cancel)}</button>` : ""}
          <button class="btn ${danger ? "danger" : "primary"}" data-r="1">${esc(confirm)}</button></div></div>`;
      const close = (v) => { scrim.remove(); document.removeEventListener("keydown", key); resolve(v ? scrim : null); };
      const key = (e) => { if (e.key === "Escape") close(false); };
      scrim.addEventListener("click", (e) => { if (e.target === scrim) close(false); const b = e.target.closest("[data-r]"); if (b) close(b.dataset.r === "1"); });
      document.addEventListener("keydown", key);
      document.body.appendChild(scrim);
      $(focusCancel && cancel ? '[data-r="0"]' : '[data-r="1"]', scrim).focus();
    });
  }
  const listHtml = (items) => `<ul>${items.slice(0, 12).map((x) => `<li>${esc(x)}</li>`).join("")}${items.length > 12 ? `<li>and ${items.length - 12} more</li>` : ""}</ul>`;

  async function elevate() {
    const ok = await modal({
      title: "Restart as administrator?",
      body: "<p>Nestsweep will close and reopen with administrator rights. Windows will ask you to confirm. Anything you selected will need to be selected again.</p>",
      confirm: "Restart",
    });
    if (!ok) return;
    try { await call("restart_as_admin"); } catch (e) { toast("err", "Couldn't restart", e.message); }
  }

  // ------------------------------------------------------------------ shared bits
  const emptyState = (title, text, btn) => `<div class="empty">${bird({ cls: "bird", fill: "#3b4050" })}
      <h3>${esc(title)}</h3><p>${esc(text)}</p>${btn || ""}</div>`;

  const busyEmpty = (feature, title) => `<div class="empty">${bird({ cls: "bird", fill: "#F0B13E" })}
      <h3>${esc(title)}</h3><p>${esc(S.busy[feature] || "Working")}…</p></div>`;

  function adminNote(text) {
    if (S.info.admin) return "";
    return `<div class="note">${I.shieldOff}<div>${esc(text)} <a href="#" data-elevate style="color:var(--amber);font-weight:650">Restart as administrator</a></div></div>`;
  }

  function perch({ used, junk, free, total }) {
    const pct = (v) => Math.max(0, (v / total) * 100);
    const usedP = pct(used - junk), junkP = junk > 0 ? Math.max(1.2, pct(junk)) : 0;
    const birdAt = Math.min(97, Math.max(3, usedP + junkP));
    return `<div class="perch">
        <div class="perch-bird" style="left:${birdAt}%">${bird({ fill: "#F0B13E" })}</div>
        <div class="perch-bar" role="img" aria-label="Drive usage">
          <i class="used" style="width:${usedP}%"></i><i class="junk" style="width:${junkP}%"></i>
        </div>
      </div>
      <div class="perch-legend">
        <span><i style="background:#4a5163"></i>Used <b>${size(used - junk)}</b></span>
        <span><i style="background:var(--amber)"></i>Can be cleared <b>${junk ? size(junk) : "not measured"}</b></span>
        <span><i style="background:#101216;box-shadow:inset 0 0 0 1px var(--line-strong)"></i>Free <b>${size(free)}</b></span>
      </div>`;
  }

  function sortRows(rows, [key, dir]) {
    return rows.slice().sort((a, b) => {
      const x = a[key], y = b[key];
      if (typeof x === "number" || typeof y === "number") return ((x || 0) - (y || 0)) * dir;
      return String(x || "").localeCompare(String(y || ""), undefined, { sensitivity: "base", numeric: true }) * dir;
    });
  }
  const th = (state, key, label, cls = "") => {
    const on = state.sort[0] === key;
    return `<th class="${cls}" data-sort="${key}">${label}${on ? `<span class="arrow">${state.sort[1] > 0 ? "↑" : "↓"}</span>` : ""}</th>`;
  };

  function selbar(count, noun, buttons) {
    if (!count) return "";
    return `<div class="selbar"><span class="count">${plural(count, noun)} selected</span>
      <button class="btn ghost sm" data-act="clear-sel">Clear</button><div class="end">${buttons}</div></div>`;
  }

  // ------------------------------------------------------------------ pages
  const PAGES = {
    overview: {
      label: "Overview", icon: "overview",
      title: "Overview", sub: "How this PC is doing, and what's worth a look.",
      actions: () => `<button class="btn primary" data-act="check-all" ${anyBusy() ? "disabled" : ""}>${I.scan}Check everything</button>`,
      render: renderOverview,
    },
    cleanup: {
      label: "Cleanup", icon: "cleanup", feature: "cleanup",
      title: "Cleanup", sub: "Clear temporary files, caches and traces apps leave behind.",
      actions: () => {
        const b = !!S.busy.cleanup, none = S.cc.picked.size === 0;
        return `<button class="btn" data-act="cc-analyse" ${b || none ? "disabled" : ""}>${I.scan}Analyze</button>
          <button class="btn primary" data-act="cc-clean" ${b || none ? "disabled" : ""}>${I.cleanup}Clean selected</button>`;
      },
      render: renderCleanup,
    },
    uninstaller: {
      label: "Uninstaller", icon: "uninstall", feature: "uninstaller",
      title: "Uninstaller", sub: "Remove programs, then sweep up the files and settings they leave behind.",
      actions: () => `<label class="search">${I.search}<input id="unQ" placeholder="Search programs" value="${esc(S.un.q)}" spellcheck="false"></label>
        <button class="btn" data-act="un-scan" ${S.busy.uninstaller ? "disabled" : ""}>${I.scan}${S.un.items ? "Rescan" : "Scan"}</button>`,
      render: renderUninstaller,
    },
    performance: {
      label: "Sleep mode", icon: "performance", feature: "performance",
      title: "Sleep mode", sub: "Stop programs you rarely use from running in the background. Wake them here when you need them again.",
      actions: () => `<button class="btn" data-act="po-wake-all" ${S.busy.performance ? "disabled" : ""}>${I.sun}Wake all</button>
        <button class="btn primary" data-act="po-scan" ${S.busy.performance ? "disabled" : ""}>${I.scan}${S.po.items ? "Rescan" : "Scan"}</button>`,
      render: renderPerformance,
    },
    startup: {
      label: "Startup", icon: "startup", feature: "startup",
      title: "Startup", sub: "Choose what launches with Windows. Fewer startup items means a faster boot.",
      actions: () => `<label class="search">${I.search}<input id="smQ" placeholder="Search" value="${esc(S.sm.q)}" spellcheck="false"></label>
        <button class="btn" data-act="sm-scan" ${S.busy.startup ? "disabled" : ""}>${I.scan}${S.sm.items ? "Rescan" : "Scan"}</button>`,
      render: renderStartup,
    },
    updates: {
      label: "App updates", icon: "updates", feature: "updates",
      title: "App updates", sub: "Find newer versions of your apps through winget, plus missing Windows patches.",
      actions: () => `<label class="toggle-label"><span class="switch"><input type="checkbox" id="suPatches" ${S.su.patches ? "checked" : ""}><span></span></span>Include Windows patches</label>
        <button class="btn primary" data-act="su-scan" ${S.busy.updates ? "disabled" : ""}>${I.scan}${S.su.items ? "Check again" : "Check for updates"}</button>`,
      render: renderUpdates,
    },
    drivers: {
      label: "Drivers", icon: "drivers", feature: "drivers",
      title: "Drivers", sub: "Newer drivers from Windows Update, with a backup taken before anything changes.",
      actions: () => `<label class="toggle-label"><span class="switch"><input type="checkbox" id="duOnly" ${S.du.only ? "checked" : ""}><span></span></span>Only devices that need attention</label>
        <button class="btn primary" data-act="du-scan" ${S.busy.drivers ? "disabled" : ""}>${I.scan}${S.du.items ? "Scan again" : "Scan drivers"}</button>`,
      render: renderDrivers,
    },
    settings: {
      label: "Settings", icon: "settings",
      title: "Settings", sub: "Where Nestsweep keeps its records and backups.",
      actions: () => "",
      render: renderSettings,
    },
  };
  const NAV = ["overview", "cleanup", "uninstaller", "performance", "startup", "updates", "drivers", "-", "settings"];
  const anyBusy = () => Object.keys(S.busy).length > 0;

  // ---------------------------------------------------------------- overview
  function renderOverview() {
    const o = S.ov;
    if (!o) return busyEmpty("overview", "Looking around");
    const junk = S.cc.result ? S.cc.result.total_bytes : (o.last_analysis && o.last_analysis.bytes) || 0;
    const c = o.counts || {};
    const [freeN, freeU] = size(o.free, { split: true });
    const rows = [
      {
        page: "cleanup", icon: "cleanup", title: "Junk files",
        text: junk ? `Temporary files and caches you can clear safely.` : "Analyze to see how much space temporary files are taking.",
        state: S.busy.cleanup ? "Checking…" : junk ? size(junk) : o.last_analysis ? "All clear" : "Not checked", hot: !!junk, good: !junk && !!o.last_analysis,
        btn: junk ? "Review" : "Analyze",
      },
      {
        page: "startup", icon: "startup", title: "Startup apps",
        text: "Programs that launch every time you sign in.",
        state: S.busy.startup ? "Checking…" : c.startup_enabled == null ? "Not checked" : plural(c.startup_enabled, "app") + " on",
        hot: c.startup_enabled > 8, btn: "Manage",
      },
      {
        page: "updates", icon: "updates", title: "App updates",
        text: "Outdated apps are the most common way in for malware.",
        state: S.busy.updates ? "Checking…" : c.updates == null ? "Not checked" : c.updates ? plural(c.updates, "update") : "All up to date",
        hot: c.updates > 0, good: c.updates === 0, btn: c.updates ? "Update" : "Check",
      },
      {
        page: "performance", icon: "performance", title: "Sleep mode",
        text: "Programs put to sleep stop using memory in the background.",
        state: c.asleep == null ? "Not checked" : c.asleep ? plural(c.asleep, "program") + " asleep" : "None asleep",
        btn: "Open",
      },
      {
        page: "drivers", icon: "drivers", title: "Drivers",
        text: "Devices with a newer driver or a reported problem.",
        state: S.busy.drivers ? "Checking…" : c.drivers == null ? "Not checked" : c.drivers ? plural(c.drivers, "device") : "All good",
        hot: c.drivers > 0, good: c.drivers === 0, btn: "Open",
      },
    ];
    return `
      <div class="stack">
        <div class="panel hero">
          <div class="hero-top">
            <div><div class="big">${freeN}<small>${freeU} free</small></div>
              <div class="hero-label">on drive ${esc(o.drive)}, out of ${size(o.total)}</div></div>
            <div class="hero-side">
              <div class="kv"><b>${size(o.cleaned_total)}</b><span>Cleared with Nestsweep</span></div>
              <div class="kv"><b>${esc(ago(o.last_clean))}</b><span>Last cleanup</span></div>
            </div>
          </div>
          ${perch({ used: o.used, junk, free: o.free, total: o.total })}
        </div>
        <div class="grid-main">
          <div class="panel list">
            ${rows.map((r) => `
              <div class="row check-row click" data-go="${r.page}">
                <div class="ico ${r.hot ? "amber" : r.good ? "green" : ""}">${I[r.icon]}</div>
                <div class="cell"><div class="name">${r.title}</div><div class="meta">${r.text}</div></div>
                <div class="state ${r.state === "Not checked" ? "dim" : ""}" style="${r.hot ? "color:var(--amber)" : r.good ? "color:var(--green)" : ""}">${r.state}</div>
                <button class="btn sm" data-go="${r.page}">${r.btn}</button>
              </div>`).join("")}
          </div>
          <div class="stack">
            <div class="panel mini">
              <h3>Administrator</h3>
              ${S.info.admin
                ? `<div class="note plain">${I.shield}<div>Nestsweep is running with administrator rights, so every tool is available.</div></div>`
                : `<p>Cleaning and checking work as you are. Sleep mode, drivers, Windows patches and machine-wide changes need administrator rights.</p>
                   <div class="btns"><button class="btn sm" data-elevate>${I.shield}Restart as administrator</button></div>`}
            </div>
            <div class="panel mini">
              <h3>Undo is built in</h3>
              <p>Registry keys are exported before they're deleted, drivers are backed up before updates, and sleeping programs keep a record of how to wake them.</p>
              <div class="btns"><button class="btn sm" data-open="backups">${I.folder}Open backups</button></div>
            </div>
          </div>
        </div>
      </div>`;
  }

  async function checkAll() {
    const jobs = [];
    if (!S.cc.rules) await loadRules();
    if (S.cc.picked.size) jobs.push(ccAnalyse());
    jobs.push(smScan(), suScan());
    await Promise.allSettled(jobs);
    await refreshOverview();
    toast("ok", "Check finished", "Open any row for the details.");
  }

  async function refreshOverview() {
    try { S.ov = await call("overview"); } catch (e) { /* drive stats are best effort */ }
    rerender();
  }

  // ---------------------------------------------------------------- cleanup
  async function loadRules() {
    S.cc.rules = await call("cc_rules");
    S.cc.picked = new Set(S.cc.rules.filter((r) => r.default).map((r) => r.id));
    S.cc.loaded = true;
  }

  function renderCleanup() {
    const cc = S.cc;
    if (!cc.rules) return busyEmpty("cleanup", "Loading cleaning rules");
    const res = cc.result;
    const groups = {};
    for (const r of cc.rules) (groups[r.tags[0] || "Other"] ||= []).push(r);
    const ov = S.ov;
    const selectedBytes = res ? [...cc.picked].reduce((a, id) => a + ((res.rules[id] || {}).bytes || 0), 0) : 0;
    const [n, u] = size(res ? selectedBytes : 0, { split: true });
    const blockers = res ? Object.entries(res.blockers || {}).filter(([id]) => cc.picked.has(id)) : [];
    const ruleName = Object.fromEntries(cc.rules.map((r) => [r.id, r.name]));

    return `
      <div class="stack">
        <div class="panel hero">
          <div class="hero-top">
            <div>
              <div class="big ${res && selectedBytes ? "amber" : ""}" style="${res ? "" : "color:var(--faint)"}">${n}<small>${u}</small></div>
              <div class="hero-label">${res ? `can be cleared from ${plural(cc.picked.size, "category", "categories")}` : S.busy.cleanup ? esc(S.busy.cleanup) + "…" : "Analyze to measure what can be cleared. Nothing is deleted until you choose Clean."}</div>
            </div>
            <div class="hero-side">
              <div class="kv"><b>${res ? esc(ago(res.time)) : "Not yet"}</b><span>Last analyzed</span></div>
              <div class="kv"><b>${ov ? size(ov.cleaned_total) : "—"}</b><span>Cleared all-time</span></div>
            </div>
          </div>
          ${ov ? perch({ used: ov.used, junk: res ? selectedBytes : 0, free: ov.free, total: ov.total }) : ""}
        </div>
        <div class="grid-main">
          <div class="panel">
            ${Object.entries(groups).map(([g, rules]) => {
              const on = rules.filter((r) => cc.picked.has(r.id)).length;
              const gBytes = res ? rules.reduce((a, r) => a + ((res.rules[r.id] || {}).bytes || 0), 0) : 0;
              return `<div class="group-title">
                  <input type="checkbox" class="check" data-group="${esc(g)}" ${on === rules.length ? "checked" : ""} aria-label="Select all in ${esc(g)}">
                  <span>${esc(g)}</span><span class="end">${res ? size(gBytes) : ""}</span></div>
                <div class="list">${rules.map((r) => {
                  const s = res && res.rules[r.id];
                  const sel = cc.picked.has(r.id);
                  return `<label class="row rule-row click ${sel ? "sel" : ""}">
                    <input type="checkbox" class="check" data-rule="${esc(r.id)}" ${sel ? "checked" : ""}>
                    <div class="cell"><div class="name">${esc(r.name)}</div>
                      <div class="meta">${r.warning ? esc(r.warning) : r.tags.slice(1).map(esc).join(", ") || (r.default ? "Recommended" : "Optional")}</div></div>
                    <div class="num dim">${s ? plural(s.items, "item") : ""}</div>
                    <div class="num ${s && s.bytes ? "hot" : "dim"}">${s ? size(s.bytes) : res && sel ? "0 B" : ""}</div>
                  </label>`;
                }).join("")}</div>`;
            }).join("")}
          </div>
          <div class="stack">
            <div class="panel mini">
              <h3>Selection</h3>
              <div class="val">${cc.picked.size}<small>of ${cc.rules.length}</small></div>
              <div class="btns">
                <button class="btn sm" data-act="cc-defaults">Recommended</button>
                <button class="btn sm" data-act="cc-all">All</button>
                <button class="btn sm ghost" data-act="cc-none">None</button>
              </div>
            </div>
            ${blockers.length ? `<div class="note">${I.alert}<div>Close these apps first or their categories will be skipped: <b>${esc([...new Set(blockers.flatMap(([, p]) => p))].join(", "))}</b>
              <div style="margin-top:4px;color:var(--muted)">${blockers.map(([id]) => esc(ruleName[id] || id)).join(", ")}</div></div></div>` : ""}
            <div class="note plain">${I.info}<div>Nestsweep measures again right before it cleans, and skips files that are in use. Registry entries are backed up first.</div></div>
          </div>
        </div>
      </div>`;
  }

  async function ccAnalyse() {
    const ids = [...S.cc.picked];
    S.cc.result = await work("cleanup", "Analyzing", () => call("cc_analyse", ids));
    setStatus(`Analysis finished: ${size(S.cc.result.total_bytes)} in ${plural(S.cc.result.total_items, "item")}.`);
    refreshOverview();
  }

  async function ccClean() {
    let ids = [...S.cc.picked];
    if (!S.cc.result) await ccAnalyse();
    const blockers = await call("cc_blockers", ids);
    const names = Object.fromEntries(S.cc.rules.map((r) => [r.id, r.name]));
    const blocked = Object.keys(blockers);
    const bytes = ids.reduce((a, id) => a + (blocked.includes(id) ? 0 : ((S.cc.result.rules[id] || {}).bytes || 0)), 0);
    const body = `<p>About <b style="color:var(--text)">${size(bytes)}</b> will be permanently deleted from:</p>
      ${listHtml(ids.filter((i) => !blocked.includes(i)).map((i) => names[i]))}
      ${blocked.length ? `<div class="note">${I.alert}<div>Skipping ${esc(blocked.map((i) => names[i]).join(", "))} because ${esc([...new Set(Object.values(blockers).flat())].join(", "))} is running.</div></div>` : ""}`;
    if (!(await modal({ title: "Clean now?", body, confirm: "Clean", danger: false }))) return;
    ids = ids.filter((i) => !blocked.includes(i));
    if (!ids.length) return;
    const st = await work("cleanup", "Cleaning", () => call("cc_clean", ids));
    S.cc.result = null;
    toast("ok", `Cleared ${size(st.bytes)}`, `${plural(st.files, "file")} removed${st.skipped_in_use ? `, ${st.skipped_in_use} in use and skipped` : ""}${st.errors ? `, ${plural(st.errors, "error")}` : ""}.`);
    setStatus(`Cleaned ${size(st.bytes)}.`);
    await refreshOverview();
  }

  // ---------------------------------------------------------------- uninstaller
  function renderUninstaller() {
    const un = S.un;
    if (!un.items) return S.busy.uninstaller ? busyEmpty("uninstaller", "Listing installed programs")
      : emptyState("See everything that's installed", "Scan to list desktop programs and Store apps, with their size and install date.", `<button class="btn primary" data-act="un-scan">${I.scan}Scan programs</button>`);
    const q = un.q.toLowerCase();
    const rows = sortRows(un.items.filter((s) => !q || s.name.toLowerCase().includes(q) || (s.publisher || "").toLowerCase().includes(q)), un.sort);
    const totalSize = un.items.reduce((a, s) => a + (s.size || 0), 0);
    return `
      ${adminNote("Programs installed for every user can only be removed as administrator.")}
      <div class="panel" style="margin-top:${S.info.admin ? 0 : 14}px">
        <div class="panel-head"><h2>${plural(un.items.length, "program")}</h2><span class="sub">${size(totalSize)} reported by installers</span>
          ${q ? `<span class="end sub">${rows.length} match “${esc(un.q)}”</span>` : ""}</div>
        <table class="table" data-table="un">
          <thead><tr><th class="w-check nosort"></th>${th(un, "name", "Name", "col-main")}${th(un, "publisher", "Publisher")}${th(un, "version", "Version")}${th(un, "installed", "Installed")}${th(un, "size", "Size", "right")}</tr></thead>
          <tbody>${rows.map((s) => `
            <tr data-id="${esc(s.id)}" class="${un.picked.has(s.id) ? "sel" : ""}">
              <td class="w-check"><input type="checkbox" class="check" ${un.picked.has(s.id) ? "checked" : ""} tabindex="-1"></td>
              <td class="wrap"><span class="strong">${esc(s.name)}</span>${s.store ? '<div class="dim" style="font-size:12px">Microsoft Store app</div>' : ""}</td>
              <td class="dim">${esc(s.publisher)}</td><td class="dim">${esc(s.version)}</td><td class="dim">${esc(s.installed)}</td>
              <td class="right dim">${s.size ? size(s.size) : ""}</td></tr>`).join("")}
          </tbody></table>
        ${rows.length ? "" : `<div class="empty" style="padding:40px"><p>No programs match “${esc(un.q)}”.</p></div>`}
      </div>
      ${selbar(un.picked.size, "program", `<button class="btn danger" data-act="un-remove" ${S.busy.uninstaller ? "disabled" : ""}>${I.trash}Uninstall</button>`)}`;
  }

  async function unScan() {
    S.un.items = await work("uninstaller", "Listing installed programs", () => call("un_scan"));
    S.un.picked.clear();
    setStatus(`${plural(S.un.items.length, "program")} installed.`);
  }

  async function unRemove() {
    const picks = S.un.items.filter((s) => S.un.picked.has(s.id));
    const ok = await modal({
      title: picks.length === 1 ? `Uninstall ${picks[0].name}?` : `Uninstall ${picks.length} programs?`,
      body: `${listHtml(picks.map((p) => p.name))}<p>Each program's own uninstaller will open. Finish it, then Nestsweep looks for anything left behind and asks before removing it.</p>
        ${S.info.admin && picks.some((p) => p.per_user) ? `<div class="note">${I.alert}<div>${esc(picks.filter((p) => p.per_user).map((p) => p.name).join(", "))} was installed just for your account. Its uninstaller will run with administrator rights because Nestsweep is running as administrator.</div></div>` : ""}
        ${picks.filter((p) => p.command).length ? `<p style="font-size:12.5px">This runs: ${picks.filter((p) => p.command).map((p) => `<code style="display:block;color:var(--text);word-break:break-all;margin-top:4px">${esc(p.command)}</code>`).join("")}</p>` : ""}`,
      confirm: "Uninstall", danger: true,
    });
    if (!ok) return;
    const res = await work("uninstaller", "Running uninstallers", () => call("un_uninstall", picks.map((p) => p.id)));
    if (!res) return;
    const leftovers = res.leftovers;
    S.un.picked.clear();
    if (res.kept.length) toast("err", "Not everything was uninstalled", res.kept.join("\n"));
    if (!leftovers.length) {
      if (!res.kept.length) toast("ok", "Uninstalled", "Nothing was left behind.");
      return unScan();
    }
    const scrim = await modal({
      title: "Clean up what was left behind?", wide: true, confirm: "Delete selected", danger: true, cancel: "Keep all",
      body: `<p>The uninstaller left these on your PC. Ticked items clearly belonged to the program; check the others before ticking them. Registry keys are exported to your backups first.</p>
        <div class="list-box">${leftovers.map((l) => `<label class="row click">
          <input type="checkbox" class="check" data-lo="${esc(l.id)}" ${l.suggested ? "checked" : ""}>
          <span class="chip">${esc(l.kind)}</span><span class="cell"><span class="meta" style="color:var(--text)">${esc(l.target)}</span></span>
          <span class="num dim">${l.size ? size(l.size) : ""}</span></label>`).join("")}</div>`,
    });
    if (scrim) {
      const ids = $$("[data-lo]", scrim).filter((c) => c.checked).map((c) => c.dataset.lo);
      const st = await work("uninstaller", "Removing leftovers", () => call("un_remove_leftovers", ids));
      toast(st.errors.length ? "err" : "ok", `Removed ${plural(st.removed, "leftover")}`, st.errors.slice(0, 3).join("\n"));
    }
    unScan();
  }

  // ---------------------------------------------------------------- performance (sleep)
  function renderPerformance() {
    const po = S.po;
    if (!po.items) return S.busy.performance ? busyEmpty("performance", "Finding programs that run in the background")
      : emptyState("Let idle programs sleep", "Scan to find programs that run services, scheduled tasks or startup entries even when you're not using them.", `<button class="btn primary" data-act="po-scan">${I.scan}Scan programs</button>`);
    const impact = { high: "amber", medium: "", low: "" };
    const stateChip = (s) => s === "deactivated" ? `<span class="chip blue">${I.moon.replace("<svg", '<svg style="width:12px;height:12px"')}Asleep</span>`
      : s === "ignored" ? '<span class="chip">Ignored</span>' : '<span class="chip green"><span class="dot"></span>Awake</span>';
    const order = { deactivated: 0, found: 1, ignored: 2 }, rank = { high: 0, medium: 1, low: 2 };
    const rows = po.items.slice().sort((a, b) => order[a.state] - order[b.state] || rank[a.impact] - rank[b.impact] || a.name.localeCompare(b.name));
    const asleep = po.items.filter((p) => p.state === "deactivated").length;
    const picked = po.items.filter((p) => po.picked.has(p.id));
    return `
      ${adminNote("Putting programs to sleep changes services and machine-wide settings.")}
      <div class="panel" style="margin-top:${S.info.admin ? 0 : 14}px">
        <div class="panel-head"><h2>${plural(po.items.length, "program")} run in the background</h2><span class="sub">${asleep} asleep</span>
</div>
        <table class="table" data-table="po">
          <thead><tr><th class="w-check nosort"></th><th class="nosort col-main">Program</th><th class="nosort">Impact</th><th class="nosort right">Background parts</th><th class="nosort">State</th></tr></thead>
          <tbody>${rows.map((p) => `
            <tr data-id="${esc(p.id)}" class="${po.picked.has(p.id) ? "sel" : ""}">
              <td class="w-check"><input type="checkbox" class="check" ${po.picked.has(p.id) ? "checked" : ""} tabindex="-1"></td>
              <td class="wrap"><span class="strong">${esc(p.name)}</span><div class="dim" style="font-size:12px">${esc(p.location || "")}</div></td>
              <td><span class="chip ${impact[p.impact]}">${p.impact[0].toUpperCase() + p.impact.slice(1)}</span></td>
              <td class="right fit"><button class="btn sm ghost" data-expand="${esc(p.id)}">${p.entries.length} ${po.open === p.id ? "▴" : "▾"}</button></td>
              <td class="fit">${stateChip(p.state)}</td></tr>
            ${po.open === p.id ? `<tr class="detail"><td colspan="5"><div class="entries">${p.entries.map((e) => `<div><span>${esc(e.type.replace("_", " "))}</span><b>${esc(e.name)}</b><code>${esc(e.path)}</code></div>`).join("") || "No background parts recorded."}</div></td></tr>` : ""}`).join("")}
          </tbody></table>
      </div>
      ${selbar(picked.length, "program", `
        <button class="btn" data-act="po-ignore" ${S.busy.performance ? "disabled" : ""}>${I.eyeOff}${picked.every((p) => p.state === "ignored") ? "Stop ignoring" : "Ignore"}</button>
        <button class="btn" data-act="po-wake" ${S.busy.performance || !picked.some((p) => p.state === "deactivated") ? "disabled" : ""}>${I.sun}Wake</button>
        <button class="btn primary" data-act="po-sleep" ${S.busy.performance || !picked.some((p) => p.state === "found") ? "disabled" : ""}>${I.moon}Put to sleep</button>`)}`;
  }

  async function poScan() {
    S.po.items = await work("performance", "Finding programs that run in the background", () => call("po_scan"));
    S.po.picked.clear();
    refreshOverview();
  }
  async function poSleep() {
    const picks = S.po.items.filter((p) => S.po.picked.has(p.id) && p.state === "found");
    const ok = await modal({
      title: `Put ${picks.length === 1 ? picks[0].name : plural(picks.length, "program")} to sleep?`,
      body: `${listHtml(picks.map((p) => `${p.name} (${plural(p.entries.length, "background part")})`))}
        <p>Their services, scheduled tasks and startup entries will be turned off and recorded. Come back here and choose Wake to turn everything back on exactly as it was.</p>`,
      confirm: "Put to sleep",
    });
    if (!ok) return;
    const r = await work("performance", "Putting programs to sleep", () => call("po_sleep", picks.map((p) => p.id)));
    toast(r.failed.length ? "err" : "ok", `${plural(r.programs, "program")} asleep`, `${plural(r.entries, "background part")} turned off.${r.failed.length ? " Couldn't turn off: " + r.failed.slice(0, 3).join("; ") : ""}`);
    poScan();
  }
  async function poWake(all) {
    const ids = all ? null : S.po.items.filter((p) => S.po.picked.has(p.id) && p.state === "deactivated").map((p) => p.id);
    if (all && !(await modal({ title: "Wake every sleeping program?", body: "<p>Everything Nestsweep turned off will be turned back on, exactly as it was.</p>", confirm: "Wake all" }))) return;
    const r = await work("performance", "Waking programs", () => all ? call("po_wake_all") : call("po_wake", ids));
    toast(r.failed.length ? "err" : "ok", r.failed.length ? "Partly awake" : "Awake again", `${plural(r.restored, "background part")} restored.${r.failed.length ? " Still off (try Wake again as administrator): " + r.failed.slice(0, 3).join("; ") : ""}`);
    poScan();
  }
  async function poIgnore() {
    const picks = S.po.items.filter((p) => S.po.picked.has(p.id));
    const on = !picks.every((p) => p.state === "ignored");
    await work("performance", "Saving", () => call("po_ignore", picks.map((p) => p.id), on));
    poScan();
  }

  // ---------------------------------------------------------------- startup
  const SM_KINDS = [["startup_app", "Startup apps"], ["scheduled_task", "Scheduled tasks"], ["windows_service", "Services"], ["context_menu", "Right-click menu"]];
  function renderStartup() {
    const sm = S.sm;
    if (!sm.items) return S.busy.startup ? busyEmpty("startup", "Reading startup entries")
      : emptyState("Speed up sign-in", "Scan to see the apps, tasks, services and right-click menu entries that load with Windows.", `<button class="btn primary" data-act="sm-scan">${I.scan}Scan startup</button>`);
    const q = sm.q.toLowerCase();
    const counts = Object.fromEntries(SM_KINDS.map(([k]) => [k, sm.items.filter((i) => i.type === k).length]));
    const rows = sm.items.filter((i) => i.type === sm.kind && (!q || i.name.toLowerCase().includes(q) || (i.publisher || "").toLowerCase().includes(q)))
      .sort((a, b) => (b.enabled - a.enabled) || a.name.localeCompare(b.name));
    const errors = sm.items.filter((i) => i.type === "error");
    return `
      <div style="display:flex;gap:14px;align-items:center;margin-bottom:16px;flex-wrap:wrap">
        <div class="seg" role="tablist">${SM_KINDS.map(([k, l]) => `<button data-kind="${k}" aria-pressed="${sm.kind === k}">${l}<span class="n">${counts[k]}</span></button>`).join("")}</div>
      </div>
      ${adminNote("Entries for all users and Windows services can only be changed as administrator.")}
      ${errors.length ? `<div class="note plain" style="margin-top:10px">${I.info}<div>Some entries couldn't be read: ${esc(errors.map((e) => e.name).join("; "))}</div></div>` : ""}
      <div class="panel" style="margin-top:14px">
        <table class="table" data-table="sm">
          <thead><tr><th class="nosort fit">On</th><th class="nosort" style="width:26%">Name</th><th class="nosort" style="width:20%">Publisher</th><th class="nosort">Command</th></tr></thead>
          <tbody>${rows.map((i) => `
            <tr data-id="${esc(i.id)}" style="${i.enabled ? "" : "opacity:.62"}">
              <td class="fit"><label class="switch" title="${i.enabled ? "Turn off" : "Turn on"}"><input type="checkbox" data-toggle="${esc(i.id)}" ${i.enabled ? "checked" : ""} ${i.can_toggle && !S.busy.startup ? "" : "disabled"}><span></span></label></td>
              <td><span class="strong">${esc(i.name)}</span><div class="dim" style="font-size:12px">${esc(i.location)}</div></td>
              <td class="dim">${esc(i.publisher)}</td>
              <td class="dim" title="${esc(i.command)}">${esc(i.command)}</td>
</tr>`).join("")}
          </tbody></table>
        ${rows.length ? "" : `<div class="empty" style="padding:40px"><p>${q ? `Nothing matches “${esc(sm.q)}”.` : "Nothing here."}</p></div>`}
      </div>`;
  }
  async function smScan() {
    S.sm.items = await work("startup", "Reading startup entries", () => call("sm_scan"));
    const on = S.sm.items.filter((i) => i.type === "startup_app" && i.enabled).length;
    setStatus(`${plural(on, "app")} start with Windows.`);
  }
  async function smToggle(id, on) {
    const it = S.sm.items.find((i) => i.id === id);
    try {
      await work("startup", on ? "Turning on" : "Turning off", () => call("sm_set_enabled", id, on));
      it.enabled = on;
      setStatus(`${it.name} turned ${on ? "on" : "off"}.`);
    } catch (e) { /* toast already shown; rerender restores the switch */ }
  }


  // ---------------------------------------------------------------- updates
  const SU_STATUS = { successfully_updated: ["green", "Updated"], need_reboot_to_finish: ["amber", "Restart to finish"], failed: ["red", "Failed"], succeeded_with_errors: ["amber", "Updated with errors"], aborted: ["red", "Cancelled"] };
  function renderUpdates() {
    const su = S.su;
    if (!su.items) return S.busy.updates ? busyEmpty("updates", "Checking for updates")
      : emptyState("Keep apps current", "Check which installed apps have newer versions. Updates install silently through winget.", `<button class="btn primary" data-act="su-scan">${I.scan}Check for updates</button>`);
    const notes = su.notes.map((n) => `<div class="note plain" style="margin-bottom:12px">${I.info}<div>${esc(n)}</div></div>`).join("");
    if (!su.items.length) return notes + emptyState("Everything's up to date", "No newer versions were found. Check again any time.", "");
    const hasPatches = su.items.some((p) => p.category === "windows-patch");
    return `${notes}
      ${hasPatches ? adminNote("Windows patches and apps installed for every user update only as administrator.") : ""}
      <div class="panel" style="margin-top:${hasPatches && !S.info.admin ? 14 : 0}px">
        <div class="panel-head"><input type="checkbox" class="check" data-all="su" ${su.picked.size === su.items.length ? "checked" : ""} aria-label="Select all"><h2>${plural(su.items.length, "update")} available</h2></div>
        <table class="table" data-table="su">
          <thead><tr><th class="w-check nosort"></th><th class="nosort col-main">App</th><th class="nosort">Version</th><th class="nosort">Source</th><th class="nosort fit">Status</th><th class="nosort fit"></th></tr></thead>
          <tbody>${su.items.map((p) => {
            const st = SU_STATUS[p.status];
            return `<tr data-id="${esc(p.id)}" class="${su.picked.has(p.id) ? "sel" : ""}">
              <td class="w-check"><input type="checkbox" class="check" ${su.picked.has(p.id) ? "checked" : ""} tabindex="-1"></td>
              <td class="wrap"><span class="strong">${esc(p.name)}</span>${p.category === "windows-patch" && p.severity && p.severity !== "unknown" ? ` <span class="chip ${p.severity === "critical" || p.severity === "important" ? "red" : ""}" style="margin-left:6px">${esc(p.severity[0].toUpperCase() + p.severity.slice(1))}</span>` : ""}</td>
              <td class="fit"><span class="arrow-ver"><span class="dim">${esc(p.current || "—")}</span>${I.arrow}<b>${esc(p.available)}</b></span></td>
              <td class="dim">${esc(p.category === "windows-patch" ? "Windows Update" : p.source)}</td>
              <td class="fit">${st ? `<span class="chip ${st[0]}">${st[1]}</span>` : ""}</td>
              <td class="right fit">${p.category === "application" ? `<button class="btn sm ghost" data-skip="${esc(p.id)}">Skip version</button>` : ""}</td></tr>`;
          }).join("")}</tbody></table>
      </div>
      ${selbar(su.picked.size, "update", `<button class="btn primary" data-act="su-update" ${S.busy.updates ? "disabled" : ""}>${I.updates}Update</button>`)}`;
  }
  async function suScan() {
    const r = await work("updates", "Checking for updates", () => call("su_scan", S.su.patches));
    S.su.items = r.items; S.su.notes = r.notes;
    S.su.picked = new Set(r.items.filter((p) => p.category === "application").map((p) => p.id));
    setStatus(`${plural(r.items.length, "update")} available.`);
    refreshOverview();
  }
  async function suUpdate() {
    const picks = S.su.items.filter((p) => S.su.picked.has(p.id));
    if (!(await modal({ title: `Update ${plural(picks.length, "item")}?`, body: `${listHtml(picks.map((p) => `${p.name} ${p.available}`))}<p>Installers run silently in the background. Close the apps first if you can.</p>`, confirm: "Update" }))) return;
    const res = await work("updates", "Installing updates", () => call("su_update", picks.map((p) => p.id)));
    const byId = Object.fromEntries(res.map((p) => [p.id, p]));
    S.su.items = S.su.items.map((p) => byId[p.id] || p);
    S.su.picked.clear();
    const ok = res.filter((p) => p.status === "successfully_updated").length;
    const reboot = res.some((p) => p.status === "need_reboot_to_finish");
    toast(ok === res.length ? "ok" : "err", `${ok} of ${res.length} updated`, reboot ? "Restart your PC to finish." : "");
  }

  // ---------------------------------------------------------------- drivers
  function renderDrivers() {
    const du = S.du;
    if (!du.items) return S.busy.drivers ? busyEmpty("drivers", "Scanning devices")
      : emptyState("Check your drivers", "Scan lists your devices and asks Windows Update for newer drivers. This can take a minute.", `<button class="btn primary" data-act="du-scan">${I.scan}Scan drivers</button>`);
    const rank = (d) => d.update ? 0 : d.attention ? 1 : d.backups.length ? 2 : d.problem ? 3 : 4;
    const rows = du.items.filter((d) => !du.only || d.attention || d.backups.length)
      .sort((a, b) => rank(a) - rank(b) || a.category.localeCompare(b.category) || a.name.localeCompare(b.name));
    const nUpd = du.items.filter((d) => d.update).length;
    const picked = du.items.filter((d) => du.picked.has(d.id));
    return `
      ${adminNote("Updating, backing up and restoring drivers needs administrator rights.")}
      <div class="panel" style="margin-top:${S.info.admin ? 0 : 14}px">
        <div class="panel-head"><h2>${plural(du.items.length, "device")}</h2><span class="sub">${nUpd ? `${plural(nUpd, "newer driver")} available` : "No newer drivers on Windows Update"}</span>
          <div class="end"><button class="btn sm ghost" data-open="drivers">${I.folder}Driver backups</button></div></div>
        ${rows.length ? `<table class="table" data-table="du">
          <thead><tr><th class="w-check nosort"></th><th class="nosort col-main">Device</th><th class="nosort">Category</th><th class="nosort">Installed</th><th class="nosort">Available</th><th class="nosort fit">State</th></tr></thead>
          <tbody>${rows.map((d) => `
            <tr data-id="${esc(d.id)}" class="${du.picked.has(d.id) ? "sel" : ""}">
              <td class="w-check"><input type="checkbox" class="check" ${du.picked.has(d.id) ? "checked" : ""} tabindex="-1"></td>
              <td class="wrap"><span class="strong">${esc(d.name)}</span><div class="dim" style="font-size:12px">${esc(d.manufacturer)}</div></td>
              <td class="dim">${esc(d.category)}</td>
              <td class="dim wrap">${esc(d.version)}<div style="color:var(--faint);font-size:12px">${esc(d.date)}</div></td>
              <td>${d.update ? `<b style="color:var(--green);font-weight:650">${esc(d.update.date)}</b>` : '<span class="dim">—</span>'}</td>
              <td class="fit">${d.update ? '<span class="chip amber">Update available</span>' : d.attention ? `<span class="chip red" title="Device Manager code ${d.problem}">${esc(d.problem_text)}</span>` : d.problem ? `<span class="chip" title="Device Manager code ${d.problem}">${esc(d.problem_text)}</span>` : '<span class="chip green"><span class="dot"></span>Up to date</span>'}
                ${d.backups.length ? `<span class="chip" style="margin-left:4px">${I.archive.replace("<svg", '<svg style="width:12px;height:12px"')}Backed up</span>` : ""}</td></tr>`).join("")}
          </tbody></table>` : `<div class="empty" style="padding:44px">${bird({ cls: "bird", fill: "#4fd18b" })}<h3>Nothing needs attention</h3><p>Every driver is current and no device reports a problem. Turn off the filter to see all devices.</p></div>`}
      </div>
      ${selbar(picked.length, "device", `
        <button class="btn" data-act="du-backup" ${S.busy.drivers || !picked.some((d) => d.can_backup) ? "disabled" : ""}>${I.archive}Back up</button>
        <button class="btn" data-act="du-restore" ${S.busy.drivers || picked.length !== 1 || !picked[0].backups.length ? "disabled" : ""}>${I.restore}Restore backup</button>
        <button class="btn primary" data-act="du-update" ${S.busy.drivers || !picked.some((d) => d.update) ? "disabled" : ""}>${I.updates}Update</button>`)}`;
  }
  async function duScan() {
    S.du.items = await work("drivers", "Scanning devices", () => call("du_scan"));
    S.du.picked = new Set();      // nothing pre-selected: driver updates are always a deliberate choice
    refreshOverview();
  }
  async function duUpdate() {
    const picks = S.du.items.filter((d) => S.du.picked.has(d.id) && d.update);
    const scrim = await modal({
      title: `Update ${plural(picks.length, "driver")}?`, confirm: "Update drivers",
      body: `${listHtml(picks.map((d) => `${d.name} (${d.update.date})`))}
        <p>First a System Restore point is created and checked. Then each third-party driver is backed up so you can restore it from here; a device whose backup fails is skipped. Built-in Windows drivers stay in Windows' driver store and can be rolled back from Device Manager.</p>`,
    });
    if (!scrim) return;
    const ids = picks.map((d) => d.id);
    let res;
    try {
      res = await work("drivers", "Updating drivers", () => call("du_update", ids, true), ["RestorePointError"]);
    } catch (e) {
      if (e.kind !== "RestorePointError") return;
      const go = await modal({
        title: "No restore point was created", danger: true, confirm: "Update without a restore point", cancel: "Don't update",
        focusCancel: true,
        body: `<p>${esc(e.message)}</p><p>Nothing has been changed. You can still update: drivers are backed up first either way, but without a restore point you can't roll the whole system back if Windows has trouble starting.</p>
          <label class="toggle-label" style="color:var(--text)"><input type="checkbox" class="check" id="noRpAck">I understand and want to update anyway</label>`,
      });
      if (!go) return;
      if (!$("#noRpAck", go).checked) { toast("err", "Nothing was updated", "Tick \"I understand\" to update without a restore point."); return; }
      res = await work("drivers", "Updating drivers", () => call("du_update", ids, false));
    }
    if (!res) return;
    const ok = res.results.filter((r) => r.ResultCode === 2).length;
    const notes = [res.results.some((r) => r.RebootRequired) ? "Restart your PC to finish." : "", ...res.skipped].filter(Boolean);
    toast(ok === res.results.length && !res.skipped.length ? "ok" : "err", `${ok} of ${picks.length} drivers installed`, notes.join(" "));
    duScan();
  }
  async function duBackup() {
    const ids = S.du.items.filter((d) => S.du.picked.has(d.id) && d.can_backup).map((d) => d.id);
    const made = await work("drivers", "Backing up drivers", () => call("du_backup", ids));
    toast("ok", `${plural(made.length, "driver")} backed up`, made.length < ids.length ? "Built-in Windows drivers don't need a backup." : "Saved to Nestsweep's driver backups folder.");
    rerender();
  }
  async function duRestore() {
    const d = S.du.items.find((x) => S.du.picked.has(x.id));
    const file = d.backups[d.backups.length - 1];
    if (!(await modal({ title: `Restore ${d.name}?`, body: `<p>The driver from <b style="color:var(--text)">${esc(file)}</b> will be installed over the current one.</p>`, confirm: "Restore" }))) return;
    const r = await work("drivers", "Restoring driver", () => call("du_restore", d.id, file));
    if (!r) return;
    toast("ok", `Restored ${r.device}`, `Version ${r.version}${r.reboot_required ? ". Restart to finish." : "."}`);
    duScan();
  }

  // ---------------------------------------------------------------- settings
  function renderSettings() {
    const i = S.info;
    return `<div class="settings">
      <div class="panel about">${bird({})}
        <div><div class="word">nestsweep</div><p>Version ${esc(i.version)}. Free and open source: a cleaner, uninstaller, startup manager and updater for Windows.</p>
          <div style="display:flex;gap:8px;margin-top:12px"><button class="btn sm" data-url="${esc(i.github)}">${I.github}Source code</button></div></div>
      </div>
      <div class="panel">
        <div class="set-row"><div class="txt"><b>Administrator rights</b><span>${i.admin ? "Running as administrator. Every tool is available." : "Running as you. Some tools need administrator rights."}</span></div>
          ${i.admin ? `<span class="chip green">${I.shield.replace("<svg", '<svg style="width:13px;height:13px"')}Active</span>` : `<button class="btn sm" data-elevate>${I.shield}Restart as administrator</button>`}</div>
        <div class="set-row"><div class="txt"><b>Records</b><span>${esc(i.data_dir)}</span><span style="display:block">Every change Nestsweep makes is logged here so it can be undone.</span></div>
          <button class="btn sm" data-open="data">${I.folder}Open</button></div>
        <div class="set-row"><div class="txt"><b>Backups</b><span>${esc(i.backup_dir)}</span><span style="display:block">Registry exports and driver packages saved before changes.</span></div>
          <button class="btn sm" data-open="backups">${I.folder}Open</button></div>
      </div>
      <div class="note plain">${I.info}<div>App updates come from winget, and patches and drivers from Windows Update. Nestsweep's own update check asks GitHub for the latest version number and sends nothing about your PC.</div></div>
    </div>`;
  }

  // ------------------------------------------------------------------ chrome: nav, header, events
  function navCount(id) {
    const c = (S.ov && S.ov.counts) || {};
    if (id === "updates" && c.updates) return c.updates;
    if (id === "drivers" && c.drivers) return c.drivers;
    if (id === "cleanup" && S.cc.result && S.cc.result.total_bytes) return size(S.cc.result.total_bytes);
    return "";
  }
  function renderChrome() {
    $("#brand").innerHTML = `${bird({})}<span class="word">nestsweep</span>`;
    $("#nav").innerHTML = NAV.map((id) => id === "-" ? '<div class="sep"></div>' : (() => {
      const p = PAGES[id], n = navCount(id);
      return `<a href="#${id}" ${S.page === id ? 'aria-current="page"' : ""}>${I[p.icon]}<span>${p.label}</span>${n ? `<span class="count">${n}</span>` : ""}</a>`;
    })()).join("");
    $("#railFoot").innerHTML = `${S.info.admin ? "" : `<div class="admin-card"><div class="admin-row">${I.shieldOff.replace("<svg", '<svg style="width:16px;height:16px;color:var(--amber)"')}Limited mode</div>
        <p>Some tools need administrator rights.</p><button class="btn sm" data-elevate>Restart as administrator</button></div>`}
      <div class="ver"><span>nestsweep ${esc(S.info.version)}</span><span>${S.info.admin ? "Administrator" : ""}</span></div>`;
  }

  function renderUpdateBar() {
    const u = S.update;
    $("#updateBar").innerHTML = u && u.available ? `<div class="update-bar ${u.required ? "required" : ""}">
        ${I.updates}<span><b>Nestsweep ${esc(u.latest)} is ${u.required ? "required" : "available"}.</b>
        ${u.required ? "Tools are locked until you update." : ""}</span>
        <button class="btn sm ${u.required ? "primary" : ""}" data-update ${S.busy.update ? "disabled" : ""}>${S.busy.update ? "Updating…" : "Update now"}</button></div>` : "";
  }

  function rerender() {
    renderUpdateBar();
    const p = PAGES[S.page];
    $("#title").textContent = p.title;
    $("#subtitle").textContent = p.sub;
    const focusId = document.activeElement && document.activeElement.id;
    const caret = document.activeElement && document.activeElement.selectionStart;
    $("#actions").innerHTML = p.actions();
    const scroll = $("#body").scrollTop;
    $("#body").innerHTML = p.render();
    $("#body").scrollTop = scroll;
    if (focusId && $("#" + focusId)) { const el = $("#" + focusId); el.focus(); if (caret != null && el.setSelectionRange) el.setSelectionRange(caret, caret); }
    $$(".check[data-group]").forEach((c) => {
      const ids = S.cc.rules.filter((r) => (r.tags[0] || "Other") === c.dataset.group).map((r) => r.id);
      const on = ids.filter((id) => S.cc.picked.has(id)).length;
      c.indeterminate = on > 0 && on < ids.length;
    });
    renderChrome();
    paintBusy();
  }

  function go(page) {
    if (!PAGES[page]) page = "overview";
    S.page = page;
    if (location.hash !== "#" + page) history.replaceState(null, "", "#" + page);
    $("#body").scrollTop = 0;
    rerender();
    $("#body").focus({ preventScroll: true });
  }

  const ACTIONS = {
    "check-all": checkAll,
    "cc-analyse": ccAnalyse, "cc-clean": ccClean,
    "cc-defaults": () => { S.cc.picked = new Set(S.cc.rules.filter((r) => r.default).map((r) => r.id)); rerender(); },
    "cc-all": () => { S.cc.picked = new Set(S.cc.rules.map((r) => r.id)); rerender(); },
    "cc-none": () => { S.cc.picked.clear(); rerender(); },
    "un-scan": unScan, "un-remove": unRemove,
    "po-scan": poScan, "po-sleep": poSleep, "po-wake": () => poWake(false), "po-wake-all": () => poWake(true), "po-ignore": poIgnore,
    "sm-scan": smScan,
    "su-scan": suScan, "su-update": suUpdate,
    "du-scan": duScan, "du-update": duUpdate, "du-backup": duBackup, "du-restore": duRestore,
    "clear-sel": () => { ({ uninstaller: S.un, performance: S.po, updates: S.su, drivers: S.du })[S.page].picked.clear(); rerender(); },
  };
  const TABLE_STATE = { un: () => S.un, po: () => S.po, su: () => S.su, du: () => S.du };

  document.addEventListener("click", async (e) => {
    const t = e.target;
    const el = (sel) => t.closest(sel);
    let x;
    if ((x = el("[data-elevate]"))) { e.preventDefault(); return elevate(); }
    if ((x = el("[data-update]"))) { e.preventDefault(); return showUpdate(); }
    if ((x = el("[data-act]"))) {
      e.preventDefault();
      if (updateBlocks(x.dataset.act)) return showUpdate();
      try { await ACTIONS[x.dataset.act](); } catch (err) { /* reported */ }
      return;
    }
    if ((x = el("[data-go]"))) { e.preventDefault(); return go(x.dataset.go); }
    if ((x = el("[data-open]"))) { try { await call("open_path", x.dataset.open); } catch (err) { toast("err", "Couldn't open the folder", err.message); } return; }
    if ((x = el("[data-url]"))) { try { await call("open_url", x.dataset.url); } catch (err) { /* ignore */ } return; }
    if ((x = el("[data-kind]"))) { S.sm.kind = x.dataset.kind; return rerender(); }
    if ((x = el("[data-expand]"))) { e.stopPropagation(); S.po.open = S.po.open === x.dataset.expand ? null : x.dataset.expand; return rerender(); }
    if (el("[data-skip]") && updateBlocks("edit")) { e.stopPropagation(); return showUpdate(); }
    if ((x = el("[data-skip]"))) {
      e.stopPropagation();
      const id = x.dataset.skip;
      try { await call("su_skip", id); S.su.items = S.su.items.filter((p) => p.id !== id); S.su.picked.delete(id); rerender(); } catch (err) { toast("err", "Couldn't skip", err.message); }
      return;
    }
    if ((x = el("th[data-sort]"))) {
      const st = TABLE_STATE[x.closest("table").dataset.table]();
      if (st.sort) { st.sort = [x.dataset.sort, st.sort[0] === x.dataset.sort ? -st.sort[1] : 1]; rerender(); }
      return;
    }
    if ((x = el("tbody tr[data-id]")) && !el(".switch")) {
      const tbl = x.closest("table").dataset.table;
      if (!TABLE_STATE[tbl]) return;
      const set = TABLE_STATE[tbl]().picked, id = x.dataset.id;
      set.has(id) ? set.delete(id) : set.add(id);
      rerender();
    }
  });

  document.addEventListener("change", async (e) => {
    const t = e.target;
    if (t.dataset.rule) { t.checked ? S.cc.picked.add(t.dataset.rule) : S.cc.picked.delete(t.dataset.rule); rerender(); }
    else if (t.dataset.group) {
      for (const r of S.cc.rules) if ((r.tags[0] || "Other") === t.dataset.group) t.checked ? S.cc.picked.add(r.id) : S.cc.picked.delete(r.id);
      rerender();
    }
    else if (t.dataset.all === "su") { S.su.picked = t.checked ? new Set(S.su.items.map((p) => p.id)) : new Set(); rerender(); }
    else if (t.dataset.toggle) {
      if (!t.checked && updateBlocks("edit")) { t.checked = true; return showUpdate(); }
      await smToggle(t.dataset.toggle, t.checked);
    }
    else if (t.id === "suPatches") { S.su.patches = t.checked; }
    else if (t.id === "duOnly") { S.du.only = t.checked; rerender(); }
  });

  document.addEventListener("input", (e) => {
    if (e.target.id === "unQ") { S.un.q = e.target.value; rerender(); }
    if (e.target.id === "smQ") { S.sm.q = e.target.value; rerender(); }
  });

  window.addEventListener("hashchange", () => go(location.hash.slice(1)));

  // ------------------------------------------------------------------ boot
  async function boot() {
    try { S.info = await call("app_info"); } catch (e) { /* keep defaults */ }
    go(location.hash.slice(1) || "overview");
    checkForUpdate();
    await Promise.allSettled([refreshOverview(), loadRules().then(rerender)]);
  }

  // ------------------------------------------------------------------ updates
  // A newer release blocks every feature except waking sleeping programs and restoring a driver
  // backup (both undo earlier changes). The user can close the dialog, but it comes back the next
  // time they try to use a feature. Releases marked "[optional]" are offered without blocking.
  // Scans that lead to an undo (finding a sleeping program, a startup item, a driver backup) stay
  // available too, and so does switching a startup item back on.
  const ALWAYS_ALLOWED = new Set(["po-wake", "po-wake-all", "po-scan", "du-restore", "du-scan", "sm-scan",
    "clear-sel", "cc-defaults", "cc-all", "cc-none"]);
  const updateBlocks = (act) => !!(S.update && S.update.required) && !ALWAYS_ALLOWED.has(act);

  async function checkForUpdate() {
    try { S.update = await call("update_status"); } catch (e) { return; }
    if (S.update && S.update.available) { rerender(); showUpdate(); }
  }

  let updateOpen = false;
  async function showUpdate() {
    const u = S.update;
    if (!u || !u.available || updateOpen) return;
    updateOpen = true;
    const notes = u.notes ? `<div class="update-notes">${esc(u.notes)}</div>` : "";
    const scrim = await modal({
      title: `Nestsweep ${u.latest} is ready`,
      body: `<p>You have version ${esc(u.current)}. ${u.required
        ? "This update is required: Nestsweep's tools stay locked until it's installed, in case it fixes something that could harm your PC. Waking sleeping programs and restoring driver backups still work."
        : "This update is optional."}</p>${notes}
        <p style="font-size:12.5px">The download is checked against its published checksum before it replaces this copy. Nestsweep restarts when it's done.</p>`,
      confirm: u.can_install ? "Update now" : "Open download page",
      cancel: "Not now",
    });
    updateOpen = false;
    if (!scrim) return;
    if (!u.can_install) { try { await call("open_url", u.page); } catch (e) { /* ignore */ } return; }
    try {
      await work("update", "Updating Nestsweep", () => call("update_install"), true);
      setStatus("Restarting with the new version…");
    } catch (e) {
      // Never leave someone locked out of an app that can't update itself: after a failed attempt
      // the tools unlock for this session, and the banner stays as a reminder.
      S.update.required = false;
      rerender();
      toast("err", "The update didn't install", `${e.message} You can keep using this version for now.`,
        { label: "Open download page", run: () => call("open_url", u.page) });
    }
  }
  if (window.pywebview && window.pywebview.api) boot();
  else window.addEventListener("pywebviewready", boot, { once: true });
})();
