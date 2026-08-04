(() => {
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
  let overview;
  let fullLatest;

  function tags(values = []) {
    return `<div class="business-tags">${values.slice(0, 4).map((value) => `<span>${esc(value)}</span>`).join("")}</div>`;
  }

  function renderBrief(items = []) {
    const root = document.getElementById("businessBrief");
    root.innerHTML = items.slice(0, 4).map((item, index) => `
      <button class="business-card" type="button" data-brief-index="${index}">
        <div class="business-rank">0${item.rank}</div>
        <small>${esc(item.risk_level || "EVIDENCE")}</small>
        <h3>${esc(item.title)}</h3>
        <p>${esc(item.judgment)}</p>
        ${tags(item.yuanli_mapping)}
        <div class="business-action">${esc(item.recommended_action)}</div>
      </button>`).join("");
    root.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-brief-index]");
      if (!button) return;
      const item = items[Number(button.dataset.briefIndex)];
      if (!fullLatest) fullLatest = await window.RadarData.getJson("business-latest-24h.json");
      const byId = new Map((fullLatest.items || []).map((signal) => [signal.signal_id, signal]));
      window.RadarShell.openQuickLook({
        ...item,
        sources: (item.evidence_refs || []).map((id) => byId.get(id)).filter(Boolean),
      }, button);
    });
  }

  function renderActions(items = []) {
    document.getElementById("businessActionQueue").innerHTML = items.slice(0, 5).map((item, index) => `
      <article class="business-action-card"><span>0${index + 1} · ${esc(item.label)}</span><strong>${esc(item.title)}</strong><p>${esc(item.context)}</p></article>`).join("");
  }

  function renderClusters(items = []) {
    const root = document.getElementById("businessClusters");
    root.innerHTML = items.map((item, index) => `
      <button class="business-cluster" type="button" data-cluster-index="${index}">
        <div class="business-cluster-score">${esc(item.importance_score)}</div>
        <div><small>${esc(item.lane)} · ${esc(item.confidence)} · ${esc(item.source_count)} sources</small><h3>${esc(item.thesis)}</h3><p>${esc(item.why_it_matters)}</p>${tags(item.yuanli_mapping)}<div class="business-action">${esc(item.recommended_action)}</div></div>
      </button>`).join("");
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-cluster-index]");
      if (button) window.RadarShell.openQuickLook(items[Number(button.dataset.clusterIndex)], button);
    });
  }

  function renderCases(payload) {
    const cases = payload.cases || [];
    document.getElementById("businessCases").innerHTML = cases.slice(0, 12).map((item) => `
      <article class="business-case"><small>${esc(item.business_model)}</small><h3><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.company || item.title)}</a></h3><p>${esc(item.title)}</p>${tags(item.yuanli_mapping)}<details><summary>Reusable lesson</summary><div class="business-action">${esc(item.reusable_lesson)}</div></details></article>`).join("");
    return cases.length;
  }

  function renderSources(catalog) {
    document.getElementById("businessSources").innerHTML = (catalog || []).map((source) => {
      const state = source.current ? "ok" : source.verified && source.fresh ? "watch" : "bad";
      return `<div class="business-source-row ${state}"><span>${esc(source.name)}<small>${esc(source.lane)} · reachable ${source.reachable ? "yes" : "no"} · verified ${source.verified ? "yes" : "no"} · fresh ${source.fresh ? "yes" : "no"} · 24h ${source.current ? "yes" : "no"}</small></span><strong>${esc(source.health_status)}</strong></div>`;
    }).join("");
    return (catalog || []).length;
  }

  function lazySecondary() {
    const archive = document.getElementById("businessArchive");
    let loaded = false;
    const load = async () => {
      if (loaded) return;
      loaded = true;
      const [cases, catalog] = await Promise.all([
        window.RadarData.getJson("business-case-bank.json"),
        window.RadarData.getJson("business-source-catalog.json"),
      ]);
      const caseCount = renderCases(cases);
      const sourceCount = renderSources(catalog);
      const summary = document.getElementById("businessArchiveSummary");
      if (summary) summary.textContent = `${caseCount} cases · ${sourceCount} sources`;
    };
    if (!archive) { load(); return; }
    archive.addEventListener("toggle", () => { if (archive.open) load(); });
  }

  async function init() {
    try {
      const result = window.RadarShell.getOverview() || await window.RadarData.getOverview("ai-business");
      overview = result.overview;
      document.getElementById("businessUpdated").textContent = `${result.freshness.status} · ${result.freshness.age_minutes}m`;
      renderBrief(overview.brief);
      renderActions(overview.actions);
      renderClusters(overview.clusters);
      lazySecondary();
    } catch {
      document.getElementById("businessBrief").innerHTML = '<article class="business-card"><h3>Evidence snapshot unavailable</h3><p>The last complete view could not be restored. Please retry shortly.</p></article>';
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => window.setTimeout(init, 0), { once: true });
  else init();
})();
