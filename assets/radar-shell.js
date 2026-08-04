(() => {
  const channel = window.RADAR_CHANNEL || (location.pathname.includes("business") ? "ai-business" : "ai-news");
  const fmt = new Intl.NumberFormat(channel === "ai-business" ? "en-US" : "zh-CN");
  let latestOverview;
  let drawerTrigger;

  const drawerFocusable = (drawer) => [...drawer.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )];

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  function icon(name) {
    const paths = {
      external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>',
    };
    return `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths[name] || ""}</svg>`;
  }

  function formatTime(value) {
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return "Unknown";
    return new Intl.DateTimeFormat(channel === "ai-business" ? "en" : "zh-CN", {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(date);
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
      <div class="radar-pulse-card ${index === 3 ? `is-${coverage.status}` : ""}">
        <span>${esc(label)}</span>
        <strong>${esc(typeof value === "number" ? fmt.format(value) : value)}</strong>
        ${index === 3 ? `<em>${esc(coverage.status)}</em>` : ""}
      </div>`).join("");
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
    const ledgerStatus = document.getElementById("radarLedgerStatus");
    if (ledgerStatus) ledgerStatus.textContent = freshness.status;
    const ledgerCoverage = document.getElementById("radarLedgerCoverage");
    if (ledgerCoverage) ledgerCoverage.textContent = `${coverage.successful}/${coverage.total} · ${String(coverage.status || "unknown").toUpperCase()}`;
    const ledgerSnapshot = document.getElementById("radarLedgerSnapshot");
    if (ledgerSnapshot) ledgerSnapshot.textContent = overview.snapshot_id;
    if (isNews) {
      const resultCount = document.getElementById("resultCount");
      const listTitle = document.getElementById("listTitle");
      if (resultCount) resultCount.textContent = `${fmt.format(overview.metrics?.signals || 0)} 条`;
      if (listTitle) listTitle.textContent = "AI 情报流";
    }
  }

  function openQuickLook(item = {}, trigger = document.activeElement) {
    const drawer = document.getElementById("radarQuickLook");
    const backdrop = document.getElementById("radarDrawerBackdrop");
    const body = document.getElementById("radarQuickLookBody");
    if (!drawer || !body || !backdrop) return;
    drawerTrigger = trigger instanceof HTMLElement ? trigger : null;
    const sources = item.sources || item.top_sources || [];
    body.innerHTML = `
      <p class="radar-quick-meta">${esc(item.importance_label || item.confidence || item.lane || "Evidence")}</p>
      <h2 id="radarQuickLookTitle">${esc(item.title || item.thesis)}</h2>
      <p>${esc(item.judgment || item.why_it_matters || item.recommended_action || "")}</p>
      ${item.recommended_action ? `<section><span>RECOMMENDED ACTION</span><strong>${esc(item.recommended_action)}</strong></section>` : ""}
      <section><span>EVIDENCE</span><strong>${esc(item.source_count || sources.length || 1)} source${Number(item.source_count || sources.length || 1) === 1 ? "" : "s"}</strong></section>
      <ul>${sources.slice(0, 6).map((source) => `<li><a href="${esc(source.url || item.url || item.primary_url || "#")}" target="_blank" rel="noopener noreferrer">${esc(source.title || source.source || source.source_name || "Open source")}</a></li>`).join("")}</ul>
      ${(item.url || item.primary_url) ? `<a class="radar-primary-command" href="${esc(item.url || item.primary_url)}" target="_blank" rel="noopener noreferrer">Open original source ${icon("external")}</a>` : ""}`;
    drawer.classList.add("is-open");
    drawer.removeAttribute("inert");
    drawer.setAttribute("aria-hidden", "false");
    backdrop.hidden = false;
    requestAnimationFrame(() => backdrop.classList.add("is-open"));
    document.getElementById("radarDrawerClose")?.focus();
  }

  function closeQuickLook() {
    const drawer = document.getElementById("radarQuickLook");
    const backdrop = document.getElementById("radarDrawerBackdrop");
    if (!drawer?.classList.contains("is-open")) return;
    drawer?.classList.remove("is-open");
    drawer?.setAttribute("aria-hidden", "true");
    drawer?.setAttribute("inert", "");
    backdrop?.classList.remove("is-open");
    window.setTimeout(() => {
      if (backdrop) backdrop.hidden = true;
      drawerTrigger?.focus();
      drawerTrigger = null;
    }, 180);
  }

  function renderNewsPreview(overview) {
    const root = document.getElementById("bolePicksList");
    if (!root || !overview.top_stories?.length) return;
    root.innerHTML = overview.top_stories.slice(0, 3).map((story, index) => `
      <button class="radar-story-preview" type="button" data-preview-index="${index}">
        <span>0${index + 1}</span>
        <div>
          <small>${esc(story.importance_label || "重点信号")} · ${esc(story.source_count || 1)} 源 · ${story.confidence === "single-source" ? "单源待验证" : "多源验证"}</small>
          <strong>${esc(story.title)}</strong>
          <em>${channel === "ai-news" ? "打开证据档案" : "Open evidence file"}</em>
        </div>
      </button>`).join("");
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-preview-index]");
      if (button) openQuickLook(overview.top_stories[Number(button.dataset.previewIndex)], button);
    });
  }

  function toggleEvidenceLedger() {
    const ledger = document.getElementById("advancedPanel");
    if (!ledger) return;
    ledger.open = !ledger.open;
    if (ledger.open) ledger.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function bindControls() {
    document.getElementById("radarDrawerClose")?.addEventListener("click", closeQuickLook);
    document.getElementById("radarDrawerBackdrop")?.addEventListener("click", closeQuickLook);
    document.getElementById("radarFilterButton")?.addEventListener("click", toggleEvidenceLedger);
    document.getElementById("radarSearchButton")?.addEventListener("click", () => {
      const search = document.getElementById("searchInput");
      if (search) { search.scrollIntoView({ behavior: "smooth", block: "center" }); search.focus(); }
      else document.querySelector(".business-section")?.scrollIntoView({ behavior: "smooth" });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeQuickLook();
      if (event.key === "Tab") {
        const drawer = document.getElementById("radarQuickLook");
        if (drawer?.classList.contains("is-open")) {
          const focusable = drawerFocusable(drawer);
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
          }
        }
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.getElementById("radarSearchButton")?.click();
      }
    });
  }

  async function init() {
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
