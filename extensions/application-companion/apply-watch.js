// Observe the user's own Apply click, never click an employer link automatically.
(() => {
  if (globalThis.radiusApplyWatch) return;
  globalThis.radiusApplyWatch = true;
  document.addEventListener(
    "click",
    (event) => {
      if (!event.isTrusted) return;
      const anchor = event
        .composedPath()
        .find((el) => el instanceof HTMLAnchorElement);
      const button = event
        .composedPath()
        .find((el) => el instanceof HTMLButtonElement);
      const control = anchor || button;
      if (!control) return;
      const label = (
        control.getAttribute("aria-label") ||
        control.textContent ||
        ""
      )
        .replace(/\s+/g, " ")
        .trim();
      if (
        !/^(apply(?: now| for this (?:job|role))?|start application|bewerben|jetzt bewerben|zur bewerbung)[! .↗→]*$/i.test(
          label,
        )
      )
        return;
      const target = anchor?.href || null;
      void chrome.runtime
        .sendMessage({
          type: "apply-click",
          target,
          url: location.href,
          clicked_at: Date.now(),
        })
        .catch(() => {});
    },
    true,
  );
})();
