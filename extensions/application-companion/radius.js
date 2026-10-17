// Read only the explicitly selected Radius application's saved draft.
(() => {
  if (globalThis.radiusApplicationSource) return;
  function read() {
    const match = location.pathname.match(/^\/jobs\/(\d+)\/apply\/?$/);
    const profileId = Number(
      new URLSearchParams(location.search).get("profile_id"),
    );
    if (
      !match ||
      !profileId ||
      !document.querySelector(".application-cv-ready")
    )
      return null;
    let saved;
    try {
      const raw = localStorage.getItem(
        `radius:application-draft:v1:${match[1]}:${profileId}`,
      );
      if (!raw || raw.length > 450000) return null;
      saved = JSON.parse(raw);
    } catch {
      return null;
    }
    const pack = saved.package;
    if (
      pack?.format !== "radius-application-v1" ||
      pack.job_id !== Number(match[1]) ||
      pack.profile_id !== profileId ||
      saved.context?.job_id !== pack.job_id ||
      saved.context?.profile_id !== pack.profile_id ||
      saved.context?.cv_updated_at !== pack.cv_updated_at ||
      !Number.isFinite(saved.saved_at) ||
      saved.saved_at > Date.now() ||
      Date.now() - saved.saved_at > 7 * 86400000
    )
      return null;
    let cvText = "";
    const prompt =
      document.querySelector(
        'textarea[aria-label="Prepared application prompt"]',
      )?.value || "";
    const marker = "SOURCE PAYLOAD (quoted data):";
    const index = prompt.lastIndexOf(marker);
    if (index >= 0 && prompt.length < 300000) {
      try {
        const payload = JSON.parse(prompt.slice(index + marker.length).trim());
        if (typeof payload.original_cv_text === "string")
          cvText = payload.original_cv_text;
      } catch {}
    }
    return { package: pack, cv_text: cvText };
  }
  globalThis.radiusApplicationSource = { read };
})();
