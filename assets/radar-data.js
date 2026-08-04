(() => {
  const LOCAL_ROOT = "./data/";
  const CANONICAL_ROOT = "https://raw.githubusercontent.com/moonstachain/ai-news-radar/master/data/";
  const CACHE_KEY = "yuanli_radar_last_complete_overview_v1";
  const memory = new Map();
  let contextPromise;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function asTime(value) {
    const time = Date.parse(value || "");
    return Number.isFinite(time) ? time : 0;
  }

  function freshness(generatedAt, now = Date.now()) {
    const ageMinutes = Math.max(0, (now - asTime(generatedAt)) / 60000);
    let status = "LIVE";
    if (!asTime(generatedAt) || ageMinutes > 180) status = "STALE";
    else if (ageMinutes > 90) status = "DELAYED";
    return { status, age_minutes: Math.round(ageMinutes) };
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, { cache: options.cache || "default" });
    if (!response.ok) throw new Error(`${url} returned ${response.status}`);
    return response.json();
  }

  async function fetchWithRetry(url, attempts = 3) {
    let lastError;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        return await fetchJson(url, { cache: attempt ? "reload" : "no-cache" });
      } catch (error) {
        lastError = error;
        if (attempt < attempts - 1) await sleep(180 * (attempt + 1));
      }
    }
    throw lastError;
  }

  function validateManifest(manifest) {
    if (!manifest || manifest.schema_version !== 1 || !manifest.snapshot_id || !manifest.files) {
      throw new Error("invalid snapshot manifest");
    }
    return manifest;
  }

  async function createContext() {
    try {
      const manifest = validateManifest(await fetchWithRetry(`${LOCAL_ROOT}snapshot-manifest.json`, 3));
      return { manifest, root: LOCAL_ROOT, mode: "PORTAL" };
    } catch (portalError) {
      const manifest = validateManifest(await fetchWithRetry(`${CANONICAL_ROOT}snapshot-manifest.json`, 2));
      return { manifest, root: CANONICAL_ROOT, mode: "FALLBACK", portalError: String(portalError) };
    }
  }

  async function createCanonicalContext() {
    const manifest = validateManifest(await fetchWithRetry(`${CANONICAL_ROOT}snapshot-manifest.json`, 2));
    return { manifest, root: CANONICAL_ROOT, mode: "FALLBACK" };
  }

  function context() {
    if (!contextPromise) contextPromise = createContext();
    return contextPromise;
  }

  function normalizedName(name) {
    return String(name || "").replace(/^\.\//, "").replace(/^data\//, "");
  }

  function validatePayload(name, payload, ctx) {
    const expected = ctx.manifest.files?.[name];
    if (!expected) throw new Error(`${name} is not declared by snapshot ${ctx.manifest.snapshot_id}`);
    if (payload?.snapshot_id && payload.snapshot_id !== ctx.manifest.snapshot_id) {
      throw new Error(`${name} belongs to snapshot ${payload.snapshot_id}`);
    }
    if (expected.generated_at && payload?.generated_at && expected.generated_at !== payload.generated_at) {
      throw new Error(`${name} generated_at does not match manifest`);
    }
    return payload;
  }

  async function getJson(name) {
    const file = normalizedName(name);
    const ctx = await context();
    const key = `${ctx.mode}:${ctx.manifest.snapshot_id}:${file}`;
    if (!memory.has(key)) {
      const url = `${ctx.root}${file}?v=${encodeURIComponent(ctx.manifest.snapshot_id)}`;
      memory.set(key, fetchJson(url).then((payload) => validatePayload(file, payload, ctx)));
    }
    return memory.get(key);
  }

  function cachedOverview(channel) {
    try {
      const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
      if (cached?.channel === channel && cached.manifest && cached.overview) return cached;
    } catch {
      // Ignore a malformed local cache and continue with canonical data.
    }
    return null;
  }

  async function getOverview(channel) {
    const file = channel === "ai-business" ? "business-overview.json" : "news-overview.json";
    try {
      const ctx = await context();
      let overview;
      try {
        overview = await getJson(file);
      } catch (portalError) {
        if (ctx.mode === "FALLBACK") throw portalError;
        const canonical = await createCanonicalContext();
        contextPromise = Promise.resolve(canonical);
        overview = await getJson(file);
      }
      const active = await context();
      const result = {
        channel,
        manifest: active.manifest,
        overview,
        transport: active.mode,
        freshness: active.mode === "FALLBACK"
          ? { ...freshness(overview.generated_at), status: "FALLBACK" }
          : freshness(overview.generated_at),
      };
      localStorage.setItem(CACHE_KEY, JSON.stringify(result));
      return result;
    } catch (error) {
      const cached = cachedOverview(channel);
      if (cached) {
        return {
          ...cached,
          transport: "CACHED",
          freshness: { ...freshness(cached.overview.generated_at), status: "STALE" },
          error: String(error),
        };
      }
      throw error;
    }
  }

  window.RadarData = { context, freshness, getJson, getOverview };
})();
