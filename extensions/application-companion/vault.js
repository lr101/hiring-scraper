import { validateProfile } from "./profile.js";
export const PACKAGE_AGE = 7 * 86400000;
export const emptyVault = () => ({
  settings: null,
  jobs: [],
  bindings: [],
  packages: {},
  synced_at: 0,
});
export function safeURL(value) {
  try {
    const u = new URL(value);
    return ["http:", "https:"].includes(u.protocol) &&
      !u.username &&
      !u.password
      ? u
      : null;
  } catch {
    return null;
  }
}
export function urlKey(value) {
  const u = safeURL(value);
  if (!u) return null;
  for (const key of [...u.searchParams.keys()])
    if (
      /^utm_/i.test(key) ||
      ["gclid", "fbclid", "msclkid", "mc_cid", "mc_eid"].includes(
        key.toLowerCase(),
      )
    )
      u.searchParams.delete(key);
  u.searchParams.sort();
  if (["#apply", "#application", "#top"].includes(u.hash.toLowerCase()))
    u.hash = "";
  return u.href;
}
export function transientURL(value) {
  const u = safeURL(value);
  if (!u) return true;
  return (
    /(?:^|\/)(?:log-?in|sign-?in|auth|oauth|sso|logout|consent)(?:\/|$)/i.test(
      u.pathname,
    ) ||
    [...u.searchParams.keys()].some((k) =>
      /^(?:token|access_token|id_token|code|session|sessionid|password|state|samlresponse)$/i.test(
        k,
      ),
    )
  );
}
export function persistableURL(value) {
  const u = safeURL(value);
  if (!u || transientURL(value)) return false;
  return (
    !/^\/(?:apply|application|candidate|form)\/?$/i.test(u.pathname) ||
    [...u.searchParams.keys()].some((k) =>
      /^(jobid|job_id|requisitionid|reqid|postingid|jid|gh_jid)$/i.test(k),
    )
  );
}
export function validateSettings(input) {
  const origin = (value) => {
    const u = safeURL(value);
    if (
      !u ||
      (u.protocol !== "https:" &&
        !["localhost", "127.0.0.1", "[::1]"].includes(u.hostname))
    )
      throw new Error(
        "Use HTTPS, or a loopback HTTP address for local Radius.",
      );
    return u.origin;
  };
  const value = {
    origin: origin(input.origin),
    api_origin: origin(input.api_origin || input.origin),
    profile_id: input.profile_id ?? null,
    cv_updated_at: input.cv_updated_at || "",
    cv_storage: input.cv_storage === "browser" ? "browser" : "api",
    profile_name: input.profile_name || "",
    theme: ["dark", "light", "system"].includes(input.theme)
      ? input.theme
      : "system",
  };
  if (
    value.profile_id !== null &&
    (!Number.isInteger(value.profile_id) || value.profile_id < 1)
  )
    throw new Error("Choose a valid Radius profile.");
  return value;
}
export function normalizeJob(job) {
  if (
    !job ||
    !Number.isInteger(job.id) ||
    job.id < 1 ||
    !urlKey(job.url) ||
    job.url.length > 2048
  )
    throw new Error("Radius returned a job without a valid ID or URL.");
  return {
    id: job.id,
    title: String(job.title || "Untitled role").slice(0, 300),
    company_name: String(job.company_name || job.company?.name || "").slice(
      0,
      300,
    ),
    url: safeURL(job.url).href,
    active: job.is_active !== false && !job.expired,
  };
}
export function matchJobs(vault, url) {
  const key = urlKey(url);
  if (!key || transientURL(url)) return [];
  const ids = new Set(
    (vault.bindings || [])
      .filter((b) => urlKey(b.url) === key)
      .map((b) => b.job_id),
  );
  const page = safeURL(url);
  const internal =
    vault.settings?.origin === page?.origin
      ? page.pathname.match(/^\/jobs\/(\d+)(?:\/apply)?\/?$/)
      : null;
  if (internal) ids.add(Number(internal[1]));
  return (vault.jobs || []).filter(
    (j) => urlKey(j.url) === key || ids.has(j.id),
  );
}
// Newly resolved rows take priority; retain prepared/linked jobs before old cache rows.
export function cacheJobs(vault, incoming, { preservePinned = false } = {}) {
  const pinned = new Set([
    ...Object.values(vault.packages || {}).map((pack) => pack.job_id),
    ...(vault.bindings || []).map((binding) => binding.job_id),
  ]);
  const existing = [...vault.jobs].reverse();
  const latest = new Map(incoming.map((job) => [job.id, job]));
  const pinnedRows = existing
    .filter((job) => pinned.has(job.id))
    .map((job) => latest.get(job.id) || job);
  const candidates = preservePinned
    ? [...pinnedRows, ...incoming]
    : [...incoming, ...pinnedRows, ...existing];
  const seen = new Set(),
    jobs = [];
  let bytes = 2,
    truncated = !!vault.truncated;
  for (const job of candidates) {
    if (seen.has(job.id)) continue;
    seen.add(job.id);
    const size =
      new TextEncoder().encode(JSON.stringify(job)).length +
      (jobs.length ? 1 : 0);
    if (jobs.length >= 2000 || bytes + size > 2000000) {
      truncated = true;
      continue;
    }
    jobs.push(job);
    bytes += size;
  }
  return { ...vault, jobs: jobs.sort((a, b) => a.id - b.id), truncated };
}
export function addBinding(vault, jobId, url, kind = "apply") {
  if (!vault.jobs.some((job) => job.id === jobId))
    throw new Error("Unknown Radius job.");
  if (!persistableURL(url))
    throw new Error(
      "A login, session or generic application URL is transient; link it only to this tab.",
    );
  const key = urlKey(url);
  const existing = (vault.bindings || []).filter(
    (b) => !(b.job_id === jobId && urlKey(b.url) === key),
  );
  if (existing.filter((b) => b.job_id === jobId).length >= 30)
    throw new Error(
      "This job already has many linked pages. Reset its page links first.",
    );
  return {
    ...vault,
    bindings: [...existing, { job_id: jobId, url: key, kind }],
  };
}
export async function profileStamp(input) {
  if (!input) return "";
  const profile = validateProfile(input);
  delete profile.application;
  delete profile.source_notes;
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(profile)),
  );
  return [...new Uint8Array(bytes)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
export function readyPackage(vault, jobId, stamp, now = Date.now()) {
  const settings = vault.settings;
  if (!settings?.profile_id || !stamp) return null;
  const saved = vault.packages?.[`${jobId}:${settings.profile_id}`];
  if (
    !saved ||
    saved.job_id !== jobId ||
    saved.package?.job_id !== jobId ||
    saved.package?.profile_id !== settings.profile_id ||
    saved.package?.cv_updated_at !== settings.cv_updated_at ||
    saved.profile_stamp !== stamp ||
    saved.cv_updated_at !== settings.cv_updated_at ||
    saved.profile_id !== settings.profile_id ||
    !Number.isFinite(saved.saved_at) ||
    saved.saved_at > now ||
    now - saved.saved_at > PACKAGE_AGE
  )
    return null;
  const cv = saved.package?.cv;
  if (!(typeof cv === "string" ? cv.trim() : Array.isArray(cv))) return null;
  return saved;
}
export function applicationURL(vault, jobId) {
  const job = vault.jobs.find((j) => j.id === jobId);
  if (!job) throw new Error("This Radius job is unavailable.");
  return new URL(
    `/jobs/${jobId}/apply?profile_id=${vault.settings.profile_id}`,
    vault.settings.origin,
  ).href;
}

export function sameJobReference(first, second) {
  const a = safeURL(first),
    b = safeURL(second);
  if (
    !a ||
    !b ||
    a.origin !== b.origin ||
    transientURL(first) ||
    transientURL(second)
  )
    return false;
  const refs = (u) =>
    [...u.searchParams]
      .filter(
        ([k, v]) =>
          /^(jobid|job_id|requisitionid|reqid|postingid|jid|gh_jid)$/i.test(
            k,
          ) && v,
      )
      .map(([k, v]) => k.toLowerCase() + "=" + v);
  const before = refs(a),
    after = refs(b);
  return before.length === 1 && after.length === 1 && before[0] === after[0];
}

export function stableJSON(value) {
  const order = (v) =>
    Array.isArray(v)
      ? v.map(order)
      : v && typeof v === "object"
        ? Object.fromEntries(
            Object.keys(v)
              .sort()
              .map((k) => [k, order(v[k])]),
          )
        : v;
  return JSON.stringify(order(value));
}
