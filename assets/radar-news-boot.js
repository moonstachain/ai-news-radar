(() => {
  let started = false;
  let nearStream = false;

  function start() {
    if (started) return;
    started = true;
    document.getElementById("radarLoadStream")?.remove();
    const script = document.createElement("script");
    script.src = "./assets/app.js?v=apple-adaptive-0804";
    document.head.appendChild(script);
  }

  function signalIntent() {
    if (nearStream || window.scrollY > 120) start();
  }

  const target = document.querySelector(".list-wrap");
  const list = document.getElementById("newsList");
  if (list) {
    list.innerHTML = '<button class="radar-load-stream" id="radarLoadStream" type="button"><strong>加载完整情报流</strong><span id="radarLoadStreamMeta">全量信号仅在需要时载入</span></button>';
    document.getElementById("radarLoadStream")?.addEventListener("click", start, { once: true });
  }

  if (!("IntersectionObserver" in window) || !target) {
    nearStream = true;
  } else {
    const observer = new IntersectionObserver((entries) => {
      nearStream = entries.some((entry) => entry.isIntersecting);
      if (nearStream && window.scrollY > 120) {
        observer.disconnect();
        start();
      }
    }, { rootMargin: "0px 0px 100px 0px", threshold: 0.01 });
    observer.observe(target);
  }

  document.addEventListener("wheel", signalIntent, { passive: true, once: true });
  document.addEventListener("touchmove", signalIntent, { passive: true, once: true });
  document.addEventListener("scroll", signalIntent, { passive: true, once: true });
  document.addEventListener("keydown", (event) => {
    if (["PageDown", "End", "ArrowDown", " "].includes(event.key)) signalIntent();
  }, { once: true });
  document.getElementById("searchInput")?.addEventListener("focus", start, { once: true });
  document.addEventListener("radar:overview", (event) => {
    const count = event.detail?.overview?.metrics?.signals;
    const meta = document.getElementById("radarLoadStreamMeta");
    if (meta && count) meta.textContent = `${count} 条信号仅在需要时载入`;
  }, { once: true });
})();
