/* Line icons (24px grid, 1.8 stroke) and the bird mark. */
(function () {
  const p = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  window.ICON = {
    overview: p('<path d="M4 13a8 8 0 0 1 16 0"/><path d="M12 13l4-4"/><path d="M4 17h16"/>'),
    cleanup: p('<path d="M14.5 3.5l-5 9"/><path d="M6.5 11.5l7 4"/><path d="M6.5 11.5c-2 1.5-3 4.5-3 8.5h9.5c.5-1.5 1-3 .5-4.5"/><path d="M8 20c.3-1.5.8-2.8 1.5-4"/><path d="M19 5l.6 1.4L21 7l-1.4.6L19 9l-.6-1.4L17 7l1.4-.6z"/>'),
    uninstall: p('<path d="M3.5 7.5L12 3l8.5 4.5v9L12 21l-8.5-4.5z"/><path d="M3.5 7.5L12 12l8.5-4.5"/><path d="M12 12v9"/>'),
    performance: p('<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/><path d="M15 4h4l-4 4h4"/>'),
    startup: p('<path d="M12 3v8"/><path d="M6.6 6.6a8 8 0 1 0 10.8 0"/>'),
    updates: p('<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 20v-4h-4"/>'),
    drivers: p('<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx=".5"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>'),
    settings: p('<path d="M4 7h10"/><path d="M18 7h2"/><circle cx="16" cy="7" r="2"/><path d="M4 17h2"/><path d="M10 17h10"/><circle cx="8" cy="17" r="2"/>'),
    shield: p('<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.1-7.5 9.5-4.3-1.4-7.5-4.9-7.5-9.5V6z"/><path d="M9 12l2 2 4-4"/>'),
    shieldOff: p('<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.1-7.5 9.5-4.3-1.4-7.5-4.9-7.5-9.5V6z"/><path d="M12 8v4.5"/><path d="M12 15.5v.5"/>'),
    search: p('<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>'),
    scan: p('<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8"/><path d="M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8"/><path d="M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16"/><path d="M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/><path d="M4 12h16"/>'),
    trash: p('<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v5M14 11v5"/>'),
    folder: p('<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4.5l2 2H19a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>'),
    external: p('<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>'),
    check: p('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
    checkCircle: p('<circle cx="12" cy="12" r="8.5"/><path d="M8.5 12.3l2.4 2.4 4.7-4.9"/>'),
    alert: p('<path d="M12 4l9 16H3z"/><path d="M12 10v4.5"/><path d="M12 17.5v.5"/>'),
    info: p('<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5"/><path d="M12 8v.5"/>'),
    x: p('<path d="M6 6l12 12M18 6L6 18"/>'),
    arrow: p('<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>'),
    moon: p('<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>'),
    sun: p('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>'),
    eyeOff: p('<path d="M3 3l18 18"/><path d="M10.6 6.1A9.6 9.6 0 0 1 12 6c5 0 8.5 4.5 9.5 6-.5.8-1.5 2.1-2.9 3.3M6.4 7.4C4.6 8.6 3.2 10.4 2.5 12c1 1.5 4.5 6 9.5 6 1.5 0 2.9-.4 4.1-1"/>'),
    archive: p('<rect x="3.5" y="4" width="17" height="4.5" rx="1"/><path d="M5 8.5v10A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5v-10"/><path d="M10 12.5h4"/>'),
    restore: p('<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5"/><path d="M4 4v4.5h4.5"/><path d="M12 8v4l3 2"/>'),
    github: p('<path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>'),
  };

  // The bird mark.
  let n = 0;
  window.bird = function (opts = {}) {
    const id = "bm" + (n++);
    const fill = opts.fill || "#F0B13E";
    const eye = opts.variant === "sleep"
      ? '<path d="M61 59 Q69 66 77 59"/>'
      : '<path d="M62 50 L76 64"/><path d="M76 50 L62 64"/>';
    return `<svg class="${opts.cls || ""}" viewBox="0 0 120 120" aria-hidden="true">
      <defs><mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" width="120" height="120">
        <rect width="120" height="120" fill="#fff"/>
        <g stroke="#000" stroke-width="8" stroke-linecap="round" fill="none">${eye}</g></mask></defs>
      <g mask="url(#${id})" fill="${fill}">
        <circle cx="58" cy="66" r="40"/><path d="M90 47 L118 64 L90 81 Z"/>
        <path d="M30 40 L2 26 L44 30 Z"/><path d="M40 31 L20 4 L58 27 Z"/><path d="M18 70 L8 108 L58 106 Z"/>
      </g></svg>`;
  };
})();
