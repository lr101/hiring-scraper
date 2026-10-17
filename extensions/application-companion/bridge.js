// Runs only on the explicitly connected Radius origin. No page messages accepted.
(() => {
  if (globalThis.radiusExtensionBridge) return;
  globalThis.radiusExtensionBridge = true;
  let last = "",
    busy = false,
    timer,
    retryAfter = 0,
    lastPulse = 0,
    lastTheme = "";
  async function update() {
    if (busy || Date.now() < retryAfter) return;
    busy = true;
    try {
      const theme = document.documentElement.dataset.theme || "system";
      if (Date.now() - lastPulse > 15000 || theme !== lastTheme) {
        lastPulse = Date.now();
        lastTheme = theme;
        void chrome.runtime
          .sendMessage({ type: "radius-state", theme })
          .catch(() => {});
      }
      const source = globalThis.radiusApplicationSource?.read();
      if (!source) return;
      const signature = JSON.stringify(source.package);
      if (signature === last) return;
      const response = await chrome.runtime.sendMessage({
        type: "radius-draft",
        source,
      });
      if (!response?.ok) retryAfter = Date.now() + 30000;
      if (response?.ok) {
        last = signature;
        document.dispatchEvent(new CustomEvent("radius-extension-ready"));
      }
    } catch {
      /* Extension reload/navigation: reconnect on the next page load. */
    } finally {
      busy = false;
    }
  }
  const observer = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(update, 250);
  });
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  window.addEventListener("storage", update);
  window.addEventListener("hashchange", update);
  // localStorage writes do not emit storage in their own tab; bounded polling of
  // the selected job's one key catches React draft saves without scanning storage.
  const interval = setInterval(update, 1500);
  window.addEventListener(
    "pagehide",
    () => {
      clearInterval(interval);
      observer.disconnect();
    },
    { once: true },
  );
  void update();
})();
