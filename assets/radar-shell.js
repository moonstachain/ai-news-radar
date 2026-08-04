(() => {
  const channel = window.RADAR_CHANNEL || (location.pathname.includes("business") ? "ai-business" : "ai-news");
  const config = {
    "ai-news": { label: "AI News", meta: "Technology & industry", href: "./index.html", lang: "zh-CN" },
    "ai-business": { label: "AI Business", meta: "English evidence", href: "./business.html", lang: "en" },
  };
  const fmt = new Intl.NumberFormat(channel === "ai-business" ? "en-US" : "zh-CN");
  const themeKey = "yuanli_radar_theme_v1";
  let latestOverview;

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  function icon(name) {
    const paths = {
      search: '<circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path>',
      sun: '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41"></path>',
      close: '<path d="m6 6 12 12M18 6 6 18"></path>',
      external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>',
    };
    return `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths[name]}</svg>`;
  }

  function formatTime(value) {
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return "Unknown";
    return new Intl.DateTimeFormat(channel === "ai-business" ? "en" : "zh-CN", {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(date);
  }

  function applyTheme(value) {
    const theme = value === "light" || value === "dark" ? value : "auto";
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(themeKey, theme);
    const button = document.getElementById("radarThemeToggle");
    if (button) button.setAttribute("aria-label", `Theme: ${theme}. Activate to change.`);
  }

  function cycleTheme() {
    const current = document.documentElement.dataset.theme || "auto";
    applyTheme(current === "auto" ? "light" : current === "light" ? "dark" : "auto");
  }

  function buildShell() {
    applyTheme(localStorage.getItem(themeKey) || "auto");
    document.documentElement.classList.add("radar-shell-html");
    document.body.classList.add("radar-shell-body", `radar-channel-${channel}`);
    const links = Object.entries(config).map(([id, item]) => `
      <a class="radar-channel-link ${id === channel ? "active" : ""}" href="${item.href}" ${id === channel ? 'aria-current="page"' : ""}>
        <span>${esc(item.label)}</span><em>${esc(item.meta)}</em>
      </a>`).join("");
    const aside = document.createElement("aside");
    aside.className = "radar-side";
    aside.innerHTML = `
      <a class="radar-side-brand" href="./index.html"><span class="radar-side-mark">原</span><div><strong>Yuanli Radar</strong><em>Evidence to action</em></div></a>
      <nav class="radar-side-nav" aria-label="Radar channels">${links}</nav>
      <div class="radar-trust-card"><span>SNAPSHOT TRUST</span><strong id="radarSideStatus">Loading</strong><small id="radarSideUpdated">Checking manifest</small></div>
      <div class="radar-side-links"><a href="https://github.com/moonstachain/ai-news-radar" target="_blank" rel="noopener noreferrer">GitHub source ${icon("external")}</a></div>`;
    const mobile = document.createElement("nav");
    mobile.className = "radar-mobile-switch";
    mobile.setAttribute("aria-label", "Radar channels");
    mobile.innerHTML = links;
    const toolbar = document.createElement("header");
    toolbar.className = "radar-toolbar";
    toolbar.innerHTML = `
      <div><span class="radar-toolbar-kicker">YUANLI INTELLIGENCE</span><strong>${esc(config[channel].label)}</strong></div>
      <div class="radar-toolbar-actions">
        <button class="radar-icon-button" id="radarSearchButton" type="button" aria-label="Search">${icon("search")}</button>
        <span class="radar-trust-pill is-loading" id="radarTrustPill">CHECKING</span>
        <button class="radar-icon-button" id="radarThemeToggle" type="button" aria-label="Change theme">${icon("sun")}</button>
      </div>`;
    const main = document.querySelector("main");
    document.body.prepend(mobile);
    document.body.prepend(aside);
    if (main) main.prepend(toolbar);
    if (channel === "ai-news" && main) {
      const top = main.querySelector(".bole-picks-wrap");
      const controls = main.querySelector(".primary-controls");
      const stream = main.querySelector(".list-wrap");
      const community = main.querySelector(".waytoagi-wrap");
      const advanced = main.querySelector(".advanced-panel");
      const hero = main.querySelector(".hero");
      if (hero && top) hero.after(top);
      if (top) top.after(controls, stream, community, advanced);
    }
    document.body.insertAdjacentHTML("beforeend", `
      <div class="radar-drawer-backdrop" id="radarDrawerBackdrop" hidden></div>
      <aside class="radar-quick-look" id="radarQuickLook" aria-hidden="true" aria-label="Quick Look">
        <header><span>QUICK LOOK</span><button class="radar-icon-button" id="radarDrawerClose" type="button" aria-label="Close Quick Look">${icon("close")}</button></header>
        <div id="radarQuickLookBody"></div>
      </aside>`);
  }

  function renderPulse(result) {
    const { overview, freshness, transport } = result;
    const coverage = overview.coverage || {};
    const isNews = channel === "ai-news";
    const metrics = isNews
      ? [
        ["Signals", overview.metrics?.signals], ["High priority", overview.metrics?.high_priority],
        ["Briefs", overview.metrics?.briefs], ["Coverage", `${coverage.successful}/${coverage.total}`],
      ]
      : [
        ["Evidence", overview.metrics?.signals], ["Briefs", overview.metrics?.briefs],
        ["Clusters", overview.metrics?.clusters], ["Coverage", `${coverage.successful}/${coverage.total}`],
      ];
    const pulse = document.getElementById("radarPulseStrip");
    if (pulse) pulse.innerHTML = metrics.map(([label, value], index) => `
      <div class="radar-pulse-card ${index === 3 ? `is-${coverage.status}` : ""}"><span>${esc(label)}</span><strong>${esc(typeof value === "number" ? fmt.format(value) : value)}</strong>${index === 3 ? `<em>${esc(coverage.status)}</em>` : ""}</div>`).join("");
    const decision = document.getElementById("radarHeroDecision");
    if (decision) decision.textContent = overview.decision;
    const meta = document.getElementById("radarHeroDecisionMeta");
    if (meta) meta.textContent = `${freshness.status} · ${freshness.age_minutes}m · ${formatTime(overview.generated_at)}`;
    const trust = document.getElementById("radarTrustPill");
    if (trust) {
      trust.textContent = freshness.status;
      trust.className = `radar-trust-pill is-${freshness.status.toLowerCase()}`;
      trust.title = transport === "FALLBACK" ? "Portal unavailable; using GitHub canonical" : `Snapshot ${overview.snapshot_id}`;
    }
    const sideStatus = document.getElementById("radarSideStatus");
    if (sideStatus) sideStatus.textContent = `${freshness.status} · ${String(coverage.status || "unknown").toUpperCase()}`;
    const sideUpdated = document.getElementById("radarSideUpdated");
    if (sideUpdated) sideUpdated.textContent = `${formatTime(overview.generated_at)} · ${overview.snapshot_id}`;
    if (isNews) {
      const resultCount = document.getElementById("resultCount");
      const listTitle = document.getElementById("listTitle");
      if (resultCount) resultCount.textContent = `${fmt.format(overview.metrics?.signals || 0)} 条`;
      if (listTitle) listTitle.textContent = "AI 情报流";
    }
  }

  function openQuickLook(item = {}) {
    const drawer = document.getElementById("radarQuickLook");
    const backdrop = document.getElementById("radarDrawerBackdrop");
    const body = document.getElementById("radarQuickLookBody");
    if (!drawer || !body || !backdrop) return;
    const sources = item.sources || item.top_sources || [];
    body.innerHTML = `
      <p class="radar-quick-meta">${esc(item.importance_label || item.confidence || item.lane || "Evidence")}</p>
      <h2>${esc(item.title || item.thesis)}</h2>
      <p>${esc(item.judgment || item.why_it_matters || item.recommended_action || "")}</p>
      ${item.recommended_action ? `<section><span>RECOMMENDED ACTION</span><strong>${esc(item.recommended_action)}</strong></section>` : ""}
      <section><span>EVIDENCE</span><strong>${esc(item.source_count || sources.length || 1)} source${Number(item.source_count || sources.length || 1) === 1 ? "" : "s"}</strong></section>
      <ul>${sources.slice(0, 6).map((source) => `<li><a href="${esc(source.url || item.url || item.primary_url || "#")}" target="_blank" rel="noopener noreferrer">${esc(source.title || source.source || source.source_name || "Open source")}</a></li>`).join("")}</ul>
      ${(item.url || item.primary_url) ? `<a class="radar-primary-command" href="${esc(item.url || item.primary_url)}" target="_blank" rel="noopener noreferrer">Open original source ${icon("external")}</a>` : ""}`;
    drawer.classList.add("is-open");
    drawer.setAttribute("aria-hidden", "false");
    backdrop.hidden = false;
    requestAnimationFrame(() => backdrop.classList.add("is-open"));
    document.getElementById("radarDrawerClose")?.focus();
  }

  function closeQuickLook() {
    const drawer = document.getElementById("radarQuickLook");
    const backdrop = document.getElementById("radarDrawerBackdrop");
    drawer?.classList.remove("is-open");
    drawer?.setAttribute("aria-hidden", "true");
    backdrop?.classList.remove("is-open");
    window.setTimeout(() => { if (backdrop) backdrop.hidden = true; }, 180);
  }

  function renderNewsPreview(overview) {
    const root = document.getElementById("bolePicksList");
    if (!root || !overview.top_stories?.length) return;
    root.innerHTML = overview.top_stories.map((story, index) => `
      <button class="radar-story-preview" type="button" data-preview-index="${index}">
        <span>0${index + 1}</span><div><small>${esc(story.importance_label || "重点信号")} · ${esc(story.source_count || 1)} 源 · ${story.confidence === "single-source" ? "单源待验证" : "多源验证"}</small><strong>${esc(story.title)}</strong></div>
      </button>`).join("");
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-preview-index]");
      if (button) openQuickLook(overview.top_stories[Number(button.dataset.previewIndex)]);
    });
  }

  function bindControls() {
    document.getElementById("radarThemeToggle")?.addEventListener("click", cycleTheme);
    document.getElementById("radarDrawerClose")?.addEventListener("click", closeQuickLook);
    document.getElementById("radarDrawerBackdrop")?.addEventListener("click", closeQuickLook);
    document.getElementById("radarSearchButton")?.addEventListener("click", () => {
      const search = document.getElementById("searchInput");
      if (search) { search.scrollIntoView({ behavior: "smooth", block: "center" }); search.focus(); }
      else document.querySelector(".business-section")?.scrollIntoView({ behavior: "smooth" });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeQuickLook();
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.getElementById("radarSearchButton")?.click();
      }
    });
  }

  async function init() {
    buildShell();
    bindControls();
    try {
      latestOverview = await window.RadarData.getOverview(channel);
      renderPulse(latestOverview);
      if (channel === "ai-news") renderNewsPreview(latestOverview.overview);
      document.dispatchEvent(new CustomEvent("radar:overview", { detail: latestOverview }));
    } catch (error) {
      const trust = document.getElementById("radarTrustPill");
      if (trust) { trust.textContent = "UNAVAILABLE"; trust.className = "radar-trust-pill is-stale"; }
      const side = document.getElementById("radarSideUpdated");
      if (side) side.textContent = channel === "ai-business" ? "Evidence snapshot unavailable" : "情报快照暂不可用";
    }
  }

  window.RadarShell = { openQuickLook, closeQuickLook, getOverview: () => latestOverview };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
