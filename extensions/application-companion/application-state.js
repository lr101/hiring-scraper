import {
  emptyVault,
  normalizeJob,
  validateSettings,
  stableJSON,
  urlKey,
  sameJobReference,
  safeURL,
  transientURL,
  persistableURL,
  matchJobs,
  addBinding,
  readyPackage,
  profileStamp,
  applicationURL,
} from "./vault.js";
import {
  syncRadius,
  radiusJSON,
  currentCV,
  cancelRadiusRequests,
} from "./radius-client.js";
import { readPackage, connectSources } from "./sources.js";
import { validateProfile } from "./profile.js";
let queue = Promise.resolve();
let connectionRevision = 0;
let activeSync;
export const loadVault = async () =>
  (await chrome.storage.local.get("radiusVault")).radiusVault || emptyVault();
export function changeVault(change) {
  const work = queue.then(async () => {
    const next = await change(await loadVault());
    await chrome.storage.local.set({ radiusVault: next });
    return next;
  });
  queue = work.catch(() => {});
  return work;
}
const sessionGet = async (key) => (await chrome.storage.session.get(key))[key];
const sessionPut = (key, value) => chrome.storage.session.set({ [key]: value });
const fresh = (value) => value && value.expires > Date.now();
const permission = async (settings) =>
  chrome.permissions.contains({
    origins: [...new Set([settings.origin, settings.api_origin])].map(
      (origin) => origin + "/*",
    ),
  });
export async function connectRadius(input, revision = ++connectionRevision) {
  activeSync?.abort();
  cancelRadiusRequests();
  const controller = new AbortController();
  activeSync = controller;
  const settings = validateSettings(input);
  if (!(await permission(settings)))
    throw new Error(
      "Allow access to this Radius origin using Connect Radius. Employer sites do not need persistent access.",
    );
  const synced = await syncRadius(settings, controller.signal);
  const saved = await changeVault(async (current) => {
    if (revision !== connectionRevision)
      throw new Error(
        "A newer Radius connection or profile selection replaced this sync.",
      );
    const same =
      current.settings?.origin === settings.origin &&
      current.settings?.api_origin === settings.api_origin;
    if (!same) await chrome.storage.session.clear();
    const newer =
      same &&
      current.settings?.profile_id === synced.settings.profile_id &&
      current.cv_observed_at > synced.cv_observed_at;
    return {
      ...emptyVault(),
      ...synced,
      settings: newer
        ? { ...synced.settings, cv_updated_at: current.settings.cv_updated_at }
        : synced.settings,
      cv_observed_at: newer ? current.cv_observed_at : synced.cv_observed_at,
      bindings: same ? current.bindings : [],
      packages: same ? current.packages : {},
    };
  });
  await refreshAllBadges();
  await attachRadiusTabs();
  return saved;
}
export async function refreshRadius() {
  const revision = ++connectionRevision;
  const vault = await loadVault();
  if (!vault.settings) throw new Error("Connect Radius first.");
  return connectRadius(vault.settings, revision);
}
export async function disconnectRadius() {
  connectionRevision++;
  activeSync?.abort();
  cancelRadiusRequests();
  watched.clear();
  const { applicant } = await chrome.storage.local.get("applicant");
  if (applicant)
    await chrome.storage.local.set({
      applicant: validateProfile({ ...applicant, application: {} }),
    });
  await changeVault(() => emptyVault());
  await chrome.storage.session.clear();
  await refreshAllBadges();
}
export async function selectedProfile(profileId) {
  const revision = ++connectionRevision;
  const vault = await loadVault();
  return connectRadius({ ...vault.settings, profile_id: profileId }, revision);
}
async function pageIdentity(tabId) {
  const [injected] = await chrome.scripting.executeScript({
    target: { tabId },
    files: ["page-context.js"],
  });
  const [result] = await chrome.scripting.executeScript({
    target: { tabId, documentIds: [injected.documentId] },
    func: () => globalThis.applicationPageContext.identity(),
  });
  return { documentId: result.documentId, identity: result.result };
}
export async function pageState(tabId, url) {
  const [vault, { applicant }, linked, recent] = await Promise.all([
    loadVault(),
    chrome.storage.local.get("applicant"),
    sessionGet(`page:${tabId}`),
    sessionGet("recentApplication"),
  ]);
  const stamp = await profileStamp(applicant);
  let exact = matchJobs(vault, url);
  if (
    fresh(linked) &&
    urlKey(linked.url) === urlKey(url) &&
    !transientURL(url)
  ) {
    let matches = true;
    if (!persistableURL(url)) {
      try {
        const current = await pageIdentity(tabId);
        matches =
          current.documentId === linked.documentId &&
          current.identity === linked.identity;
      } catch {
        matches = false;
      }
    }
    const job = matches && vault.jobs.find((j) => j.id === linked.job_id);
    if (job) exact = [job];
  }
  const host = safeURL(url)?.hostname;
  const related = exact.length
    ? []
    : vault.jobs
        .filter(
          (j) =>
            safeURL(j.url)?.hostname === host ||
            vault.bindings.some(
              (b) => b.job_id === j.id && safeURL(b.url)?.hostname === host,
            ),
        )
        .slice(0, 8);
  const items = (exact.length ? exact : related).map((job) => ({
    job,
    exact: exact.some((j) => j.id === job.id),
    ready: !!readyPackage(vault, job.id, stamp),
    has_saved: !!vault.packages?.[`${job.id}:${vault.settings?.profile_id}`],
  }));
  const continuation =
    fresh(recent) && !items.some((i) => i.job.id === recent.job_id)
      ? vault.jobs.find((j) => j.id === recent.job_id)
      : null;
  return {
    connected: !!vault.settings,
    settings: vault.settings,
    profiles: vault.profiles || [],
    synced_at: vault.synced_at,
    total: vault.jobs.length,
    truncated: !!vault.truncated,
    items,
    continuation: continuation
      ? {
          job: continuation,
          ready: !!readyPackage(vault, continuation.id, stamp),
        }
      : null,
  };
}
export async function searchJobs(query) {
  const vault = await loadVault(),
    { applicant } = await chrome.storage.local.get("applicant"),
    stamp = await profileStamp(applicant);
  const words = String(query || "")
    .trim()
    .toLowerCase()
    .split(/\s+/);
  if (words.join(" ").length < 2) return [];
  return vault.jobs
    .filter((job) =>
      words.every((word) =>
        (job.title + " " + job.company_name + " " + job.url)
          .toLowerCase()
          .includes(word),
      ),
    )
    .slice(0, 10)
    .map((job) => ({
      job,
      exact: false,
      ready: !!readyPackage(vault, job.id, stamp),
      has_saved: !!vault.packages?.[`${job.id}:${vault.settings?.profile_id}`],
    }));
}
export async function updateBadge(tabId, url) {
  try {
    if (!url) {
      const tab = await chrome.tabs.get(tabId);
      url = tab.url;
    }
    const state = await pageState(tabId, url);
    const ready = state.items.filter(
      (i) => i.exact && i.ready && i.job.active !== false,
    ).length;
    const found = state.items.length > 0;
    await chrome.action.setBadgeText({
      tabId,
      text: ready ? String(Math.min(ready, 99)) : found ? "•" : "",
    });
    await chrome.action.setBadgeBackgroundColor({
      tabId,
      color: ready ? "#354e41" : "#89918e",
    });
    await chrome.action.setTitle({
      tabId,
      title: ready
        ? `Radius · ${ready} application${ready === 1 ? "" : "s"} ready`
        : found
          ? "Radius · Discovered job · Prepare your CV"
          : "Radius applications",
    });
  } catch {
    /* Closed/unsupported tab. */
  }
}
export async function refreshAllBadges() {
  const tabs = await chrome.tabs.query({});
  await Promise.all(tabs.map((t) => updateBadge(t.id, t.url)));
}
export async function overview() {
  const v = await loadVault();
  return {
    connected: !!v.settings,
    settings: v.settings,
    profiles: v.profiles || [],
    total: v.jobs.length,
    truncated: !!v.truncated,
    synced_at: v.synced_at,
  };
}
export async function prepareJob(jobId) {
  const vault = await loadVault();
  if (!vault.settings?.profile_id)
    throw new Error("Choose your Radius profile in Connection settings.");
  const job = vault.jobs.find((j) => j.id === jobId);
  if (!job) throw new Error("This job is no longer in the synced Radius list.");
  await sessionPut("recentApplication", {
    job_id: jobId,
    expires: Date.now() + 30 * 60000,
  });
  await chrome.tabs.create({ url: applicationURL(vault, jobId) });
}
export async function usePage(tabId, jobId, url) {
  const vault = await loadVault();
  if (!vault.jobs.some((j) => j.id === jobId))
    throw new Error("Unknown Radius job.");
  if (!safeURL(url) || transientURL(url))
    throw new Error(
      "Open the application form after login before linking this page.",
    );
  if (persistableURL(url))
    await changeVault((v) => addBinding(v, jobId, url, "confirmed"));
  const pin = !persistableURL(url) ? await pageIdentity(tabId) : {};
  await sessionPut(`page:${tabId}`, {
    ...pin,
    job_id: jobId,
    url,
    expires: Date.now() + 30 * 60000,
  });
  await sessionPut("recentApplication", {
    job_id: jobId,
    expires: Date.now() + 30 * 60000,
  });
  await updateBadge(tabId, url);
}
export async function fillProfile(tabId, url, jobId, base) {
  const state = await pageState(tabId, url);
  const exact = state.items.filter((i) => i.exact);
  const selected = jobId || (exact.length === 1 ? exact[0].job.id : null);
  if (jobId && !exact.some((i) => i.job.id === jobId))
    throw new Error(
      "Link this page to the selected Radius job before filling.",
    );
  if (exact.length > 1 && !jobId)
    throw new Error(
      "Several Radius jobs share this page. Choose the application to fill.",
    );
  if (!selected)
    return state.connected
      ? {
          ...base,
          application: {
            url: "",
            cover_letter: "",
            application_answers: "",
            experience_bullets: [],
          },
        }
      : base; // Standalone v0.3 profile filling remains available.
  const vault = await loadVault(),
    saved = readyPackage(vault, selected, await profileStamp(base));
  if (!saved)
    return {
      ...base,
      application: {
        url: "",
        cover_letter: "",
        application_answers: "",
        experience_bullets: [],
      },
    };
  if (
    vault.settings?.cv_storage === "browser" &&
    (await currentCV(vault.settings)) !== saved.cv_updated_at
  ) {
    throw new Error(
      "The original CV changed or its Radius tab is closed. Open Radius and prepare a current package.",
    );
  }
  const guard = !persistableURL(url) ? await sessionGet(`page:${tabId}`) : null;
  if (!persistableURL(url))
    await chrome.storage.session.remove(`page:${tabId}`);
  const connected = connectSources(
    { ...base, application: {} },
    base.cv_text || saved.source_cv_text || "",
    saved.package,
    url,
  );
  return guard ? { ...connected, page_guard: guard.identity } : connected;
}
export async function captureDraft(source, sender) {
  let vault = await loadVault();
  const settings = vault.settings;
  if (
    !settings ||
    safeURL(sender.url)?.origin !== settings.origin ||
    sender.frameId !== 0
  )
    throw new Error("The draft did not come from the connected Radius page.");
  const path = safeURL(sender.url),
    match = path.pathname.match(/^\/jobs\/(\d+)\/apply\/?$/),
    profileId = Number(path.searchParams.get("profile_id"));
  const pack = await readPackage(JSON.stringify(source?.package));
  if (
    !match ||
    Number(match[1]) !== pack.job_id ||
    profileId !== pack.profile_id ||
    pack.profile_id !== settings.profile_id
  )
    throw new Error(
      "This Radius draft is for a different selected job/profile.",
    );
  return storePreparedPackage(pack, source.cv_text || "");
}
export async function importReturnedPackage(input, url = "") {
  return storePreparedPackage(await readPackage(input), "", url);
}
async function storePreparedPackage(pack, cvText, url = "") {
  let vault = await loadVault();
  const settings = vault.settings;
  if (!settings?.profile_id || pack.profile_id !== settings.profile_id)
    throw new Error(
      "Choose the matching Radius profile before importing this package.",
    );
  if (!(await permission(settings)))
    throw new Error("Reconnect Radius to allow access to its CV metadata.");
  if (!vault.jobs.some((j) => j.id === pack.job_id)) {
    const job = await radiusJSON(settings, `/api/v1/jobs/${pack.job_id}`);
    vault = await changeVault((v) => {
      if (
        v.settings?.origin !== settings.origin ||
        v.settings?.api_origin !== settings.api_origin ||
        v.settings?.profile_id !== settings.profile_id
      )
        throw new Error("Radius connection changed during import.");
      return {
        ...v,
        jobs: [...v.jobs.filter((row) => row.id !== job.id), normalizeJob(job)],
      };
    });
  }
  // Check CV metadata against Radius at capture time, not just the last sync.
  const checkedAt = Date.now();
  const profiles = await radiusJSON(settings, "/api/v1/profiles");
  const current = profiles.items?.find((p) => p.id === pack.profile_id);
  if (!current || (await currentCV(settings, profiles)) !== pack.cv_updated_at)
    throw new Error(
      "The original CV changed. Prepare a current Radius package.",
    );
  const { applicant } = await chrome.storage.local.get("applicant");
  if (!applicant)
    throw new Error(
      "Save your reusable applicant facts once in Edit profile before connecting a draft.",
    );
  const base = validateProfile(applicant);
  // Validate applicant identity and paragraph/job bindings without changing the reusable profile.
  connectSources(
    { ...base, application: {} },
    base.cv_text || cvText,
    pack,
    "",
  );
  const stamp = await profileStamp(base);
  const previous = vault.packages?.[`${pack.job_id}:${pack.profile_id}`];
  if (
    previous &&
    previous.profile_stamp !== stamp &&
    stableJSON(previous.package) === stableJSON(pack)
  )
    throw new Error(
      "Your reusable details changed. Prepare a fresh package; the older draft was not rebound to edited facts.",
    );
  const record = {
    job_id: pack.job_id,
    profile_id: pack.profile_id,
    cv_updated_at: pack.cv_updated_at,
    profile_stamp: stamp,
    saved_at: Date.now(),
    package: pack,
    source_cv_text: base.cv_text ? "" : cvText,
  };
  await changeVault(async (v) => {
    const { applicant: latestApplicant } =
      await chrome.storage.local.get("applicant");
    if ((await profileStamp(latestApplicant)) !== stamp)
      throw new Error("Your reusable facts changed during import. Try again.");
    if (
      v.settings?.origin !== settings.origin ||
      v.settings?.api_origin !== settings.api_origin ||
      v.settings.profile_id !== pack.profile_id
    )
      throw new Error("The Radius connection changed while importing.");
    if (
      (v.settings.cv_updated_at !== settings.cv_updated_at &&
        v.settings.cv_updated_at !== pack.cv_updated_at) ||
      (v.cv_observed_at > checkedAt &&
        v.settings.cv_updated_at !== pack.cv_updated_at)
    )
      throw new Error(
        "The Radius CV changed during import. Prepare a current package.",
      );
    const key = `${pack.job_id}:${pack.profile_id}`,
      latest = v.packages?.[key];
    // Radius rebuilds its draft using only standard return fields. A later bridge
    // pulse must not discard richer bullets captured from the original URL.
    const projection = (value) =>
      stableJSON({
        cv: value.cv,
        cover_letter: value.cover_letter,
        application_answers: value.application_answers,
      });
    if (
      latest?.profile_stamp === stamp &&
      latest.cv_updated_at === pack.cv_updated_at
    ) {
      if (
        !pack.experience_bullets &&
        latest.package.experience_bullets &&
        projection(pack) === projection(latest.package)
      )
        record.package = {
          ...pack,
          experience_bullets: latest.package.experience_bullets,
        };
      if (!record.source_cv_text)
        record.source_cv_text = latest.source_cv_text || "";
    }
    const packages = { ...v.packages, [key]: record };
    // Retain at most 100 packages. Documents stay in local trusted extension storage.
    const keys = Object.keys(packages).sort(
      (a, b) => packages[b].saved_at - packages[a].saved_at,
    );
    let bytes = 0;
    for (const [index, key] of keys.entries()) {
      bytes += new TextEncoder().encode(JSON.stringify(packages[key])).length;
      if (index >= 100 || bytes > 3000000) delete packages[key];
    }
    const next = {
      ...v,
      settings: { ...v.settings, cv_updated_at: pack.cv_updated_at },
      cv_observed_at: Math.max(checkedAt, v.cv_observed_at || 0),
      packages,
    };
    return url && persistableURL(url)
      ? addBinding(next, pack.job_id, url, "confirmed")
      : next;
  });
  await sessionPut("recentApplication", {
    job_id: pack.job_id,
    expires: Date.now() + 30 * 60000,
  });
  await refreshAllBadges();
  return {
    message: "Application CV connected. Return to the job tab to continue.",
  };
}
let metadataAt = 0;
export async function radiusState(sender, theme) {
  const vault = await loadVault(),
    settings = vault.settings;
  if (
    !settings ||
    sender.frameId !== 0 ||
    safeURL(sender.url)?.origin !== settings.origin ||
    !(await permission(settings))
  )
    return;
  if (["light", "dark", "system"].includes(theme) && theme !== settings.theme)
    await changeVault((v) =>
      v.settings?.origin === settings.origin
        ? { ...v, settings: { ...v.settings, theme } }
        : v,
    );
  if (Date.now() - metadataAt < 10000) return;
  metadataAt = Date.now();
  const checkedAt = Date.now();
  const data = await radiusJSON(settings, "/api/v1/profiles");
  const selected = data.items?.find((p) => p.id === settings.profile_id);
  const version = selected ? await currentCV(settings, data) : "";
  await changeVault((v) =>
    v.settings?.origin === settings.origin &&
    v.settings?.api_origin === settings.api_origin &&
    v.settings?.profile_id === settings.profile_id &&
    !(v.cv_observed_at > checkedAt)
      ? {
          ...v,
          settings: { ...v.settings, cv_updated_at: version },
          cv_observed_at: checkedAt,
        }
      : v,
  );
}
export async function attachRadius(tab) {
  const vault = await loadVault();
  if (!vault.settings || safeURL(tab.url)?.origin !== vault.settings.origin)
    return;
  if (!(await permission(vault.settings))) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["radius.js", "bridge.js"],
    });
  } catch {
    /* Page not ready or host access revoked. */
  }
}
async function attachRadiusTabs() {
  const tabs = await chrome.tabs.query({});
  await Promise.all(tabs.map(attachRadius));
}

// A navigation association begins only with an explicit Apply click/open action.
// No domain-wide guessing or automatic form filling occurs in these listeners.
const buffered = [];
const watched = new Map();
let navQueue = Promise.resolve();
const restoredWatches = chrome.storage.session
  .get("watchedPages")
  .then((saved) => {
    for (const [id, expires] of Object.entries(saved.watchedPages || {}))
      if (expires > Date.now())
        watched.set(
          Number(id),
          Math.max(watched.get(Number(id)) || 0, expires),
        );
  });
const serialNavigation = (task) => {
  const result = navQueue.then(task);
  navQueue = result.catch(() => {});
  return result;
};
export function watchPage(tabId) {
  watched.set(tabId, Date.now() + 30 * 60000);
  void restoredWatches.then(() =>
    chrome.storage.session.set({ watchedPages: Object.fromEntries(watched) }),
  );
}

function remember(event) {
  if (
    !(
      (watched.get(event.tabId) || 0) > Date.now() ||
      (watched.get(event.sourceTabId) || 0) > Date.now()
    )
  )
    return false;
  if (event.sourceTabId !== undefined)
    watched.set(event.tabId, Date.now() + 15000);
  buffered.push({ ...event, recorded: Date.now() });
  while (buffered.length > 100 || buffered[0]?.recorded < Date.now() - 5000)
    buffered.shift();
  return true;
}
const journeyKey = (id) => `journey:${id}`;
export function applyClick(sender, message) {
  return serialNavigation(() => handleApplyClick(sender, message));
}
async function handleApplyClick(sender, message) {
  const url = sender.url;
  if (
    sender.frameId !== 0 ||
    !safeURL(url) ||
    urlKey(url) !== urlKey(message.url)
  )
    return;
  const state = await pageState(sender.tab.id, url);
  const jobs = state.items.filter((i) => i.exact);
  if (jobs.length !== 1) return;
  watchPage(sender.tab.id);
  if (message.target && !safeURL(message.target)) return;
  const journey = {
    job_id: jobs[0].job.id,
    target: message.target || "",
    from: url,
    started_at: Date.now(),
    seen: false,
    expires: Date.now() + 15000,
  };
  await sessionPut(journeyKey(sender.tab.id), journey);
  await sessionPut("recentApplication", {
    job_id: journey.job_id,
    expires: Date.now() + 30 * 60000,
  });
  for (const event of [...buffered])
    if (
      event.recorded > Date.now() - 1500 &&
      (event.sourceTabId === sender.tab.id || event.tabId === sender.tab.id)
    )
      await navigation(event, false);
}
async function associate(tabId, journey, url) {
  if (!safeURL(url) || transientURL(url) || !persistableURL(url)) return;
  watchPage(tabId);
  if (persistableURL(url))
    await changeVault((v) => addBinding(v, journey.job_id, url, "apply"));
  await sessionPut(`page:${tabId}`, {
    job_id: journey.job_id,
    url,
    expires: Date.now() + 30 * 60000,
  });
  await updateBadge(tabId, url);
}
async function navigation(event, buffer = true) {
  if (event.frameId !== undefined && event.frameId !== 0) return;
  if (buffer) remember(event);
  if (event.sourceTabId !== undefined) {
    const parent = await sessionGet(journeyKey(event.sourceTabId));
    if (!fresh(parent)) return;
    const seen =
      parent.seen ||
      (parent.target && urlKey(parent.target) === urlKey(event.url));
    await sessionPut(journeyKey(event.tabId), { ...parent, seen });
    for (const child of [...buffered])
      if (
        child.tabId === event.tabId &&
        child.sourceTabId === undefined &&
        child.recorded >= event.recorded
      )
        await navigation(child, false);
    return;
  }
  const journey = await sessionGet(journeyKey(event.tabId));
  if (!fresh(journey)) {
    if (event.kind === "committed") {
      const linked = await sessionGet(`page:${event.tabId}`);
      if (fresh(linked) && sameJobReference(linked.url, event.url))
        await associate(event.tabId, linked, event.url);
    }
    return;
  }
  if (urlKey(event.url) === urlKey(journey.from)) return;
  if (event.timeStamp && event.timeStamp < journey.started_at - 100) return;
  if (event.kind === "before") {
    if (journey.target && urlKey(journey.target) === urlKey(event.url))
      await sessionPut(journeyKey(event.tabId), { ...journey, seen: true });
    else if (
      !journey.target &&
      /\b(apply|application|bewerbung)\b/i.test(
        safeURL(event.url)?.pathname || "",
      )
    )
      await sessionPut(journeyKey(event.tabId), {
        ...journey,
        seen: true,
        target: event.url,
      });
    return;
  }
  const target = journey.target && urlKey(journey.target) === urlKey(event.url);
  const redirected = (event.transitionQualifiers || []).some(
    (q) => q === "server_redirect" || q === "client_redirect",
  );
  if (
    (event.transitionType === "typed" ||
      event.transitionType === "auto_bookmark") &&
    !target
  ) {
    await chrome.storage.session.remove(journeyKey(event.tabId));
    return;
  }
  const step = journey.seen && sameJobReference(journey.target, event.url);
  if (target || step || (journey.seen && redirected)) {
    await associate(event.tabId, journey, event.url);
    await sessionPut(journeyKey(event.tabId), {
      ...journey,
      seen: true,
      target: event.url,
    });
  }
}
export async function openApplication(tabId, jobId) {
  const tab = await chrome.tabs.get(tabId),
    vault = await loadVault();
  const job = vault.jobs.find((j) => j.id === jobId);
  if (!job) throw new Error("Unknown Radius job.");
  let url = vault.bindings.find(
    (b) => b.job_id === jobId && b.kind === "apply",
  )?.url;
  if (!url && matchJobs(vault, tab.url).some((j) => j.id === jobId)) {
    try {
      const [result] = await chrome.scripting.executeScript({
        target: { tabId },
        func: () =>
          [...document.querySelectorAll("a[href]")]
            .filter(
              (a) =>
                a.getClientRects().length &&
                /^(apply(?: now| for this (?:job|role))?|jetzt bewerben|bewerben|zur bewerbung)[! .↗→]*$/i.test(
                  (a.getAttribute("aria-label") || a.textContent).trim(),
                ),
            )
            .map((a) => a.href),
      });
      if (result.result?.length === 1 && safeURL(result.result[0]))
        url = result.result[0];
    } catch {
      /* Use the original listing if its Apply control is not accessible. */
    }
  }
  url = url || job.url;
  const target = await chrome.tabs.create({
    url: "about:blank",
    openerTabId: tabId,
  });
  watchPage(target.id);
  await sessionPut(journeyKey(target.id), {
    job_id: jobId,
    target: url,
    from: job.url,
    started_at: Date.now(),
    seen: false,
    expires: Date.now() + 15000,
  });
  await sessionPut("recentApplication", {
    job_id: jobId,
    expires: Date.now() + 30 * 60000,
  });
  await chrome.tabs.update(target.id, { url });
}
function queueNavigation(event) {
  void restoredWatches
    .then(() => {
      if (remember(event))
        return serialNavigation(() => navigation(event, false));
    })
    .catch(() => {});
}
const returnedLinks = new Map();
async function captureReturnNavigation(tabId, url) {
  // Browser metadata retains the fragment even when Radius consumes it at load.
  // Read only the connected preparation route, never arbitrary return origins.
  const page = safeURL(url);
  if (
    !page ||
    !/^#application(?:z)?=/.test(page.hash) ||
    !/^\/jobs\/\d+\/apply\/?$/.test(page.pathname)
  )
    return;
  const vault = await loadVault();
  if (
    page.origin !== vault.settings?.origin ||
    !(await permission(vault.settings))
  )
    return;
  const digest = [
    ...new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(url)),
    ),
  ]
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
  const key = tabId + ":" + digest;
  if (returnedLinks.has(key)) return;
  returnedLinks.set(key, true);
  if (returnedLinks.size > 50)
    returnedLinks.delete(returnedLinks.keys().next().value);
  try {
    await captureDraft(
      { package: await readPackage(url) },
      { url, frameId: 0, tab: { id: tabId } },
    );
  } catch {
    // Stale/invalid packages remain grey; no private link is logged.
    returnedLinks.delete(key);
  }
}
export function installNavigation() {
  chrome.tabs.onActivated.addListener((info) => {
    void updateBadge(info.tabId);
  });
  chrome.tabs.onUpdated.addListener((id, change, tab) => {
    if (change.url) void captureReturnNavigation(id, change.url);
    if (change.url || change.status === "complete")
      void updateBadge(id, tab.url);
    if (change.status === "complete") void attachRadius(tab);
  });
  chrome.webNavigation.onBeforeNavigate.addListener((e) => {
    queueNavigation({ ...e, kind: "before" });
  });
  chrome.webNavigation.onCommitted.addListener((e) => {
    if (e.frameId === 0) void captureReturnNavigation(e.tabId, e.url);
    queueNavigation({ ...e, kind: "committed" });
  });
  chrome.webNavigation.onCreatedNavigationTarget.addListener((e) => {
    queueNavigation({ ...e, kind: "target" });
  });
  chrome.tabs.onRemoved.addListener((id) => {
    watched.delete(id);
    void restoredWatches.then(() =>
      chrome.storage.session.set({ watchedPages: Object.fromEntries(watched) }),
    );
    void chrome.storage.session.remove([
      `page:${id}`,
      journeyKey(id),
      `report:${id}`,
    ]);
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && (changes.radiusVault || changes.applicant))
      void refreshAllBadges();
  });
  chrome.permissions.onRemoved.addListener(() => {
    void refreshAllBadges();
  });
  void refreshAllBadges();
  void attachRadiusTabs();
}
