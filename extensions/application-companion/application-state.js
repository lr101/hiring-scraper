import { currentJob, buildCurrentJobPrompt } from "./current-job.js";
import { selectCVLanguage } from "./tex-cv.js";
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
  cacheJobs,
} from "./vault.js";
import {
  syncRadius,
  radiusJSON,
  radiusRequest,
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
    const next = {
      ...emptyVault(),
      ...synced,
      settings: newer
        ? { ...synced.settings, cv_updated_at: current.settings.cv_updated_at }
        : synced.settings,
      cv_observed_at: newer ? current.cv_observed_at : synced.cv_observed_at,
      bindings: same ? current.bindings : [],
      packages: same ? current.packages : {},
      forms: same ? current.forms || {} : {},
    };
    if (synced.applicant)
      await chrome.storage.local.set({ applicant: synced.applicant });
    else await chrome.storage.local.remove("applicant");
    delete next.applicant;
    return cacheJobs({ ...next, jobs: same ? current.jobs : [] }, synced.jobs, {
      preservePinned: true,
    });
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
export async function addFeed(feed) {
  const vault = await loadVault();
  if (!vault.settings) throw new Error("Connect Radius before adding a feed.");
  return radiusRequest(vault.settings, "/api/v1/feeds", {
    method: "POST",
    body: feed,
  });
}
export async function addJobToProfile(job) {
  const vault = await loadVault();
  if (!vault.settings) throw new Error("Connect Radius before adding a job.");
  if (!vault.profiles?.some((profile) => profile.id === job.profile_id))
    throw new Error("Choose a Radius profile and sync the connection again.");
  const result = await radiusRequest(vault.settings, "/api/v1/companion/jobs", {
    method: "POST",
    body: job,
  });
  if (result?.job) {
    await cacheRemoteJobs(vault.settings, { items: [result.job] }, 1);
    await refreshAllBadges();
  }
  return result;
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
  const siteEntry =
    vault.settings && (await sessionGet(siteKey(vault.settings, url)));
  const site = fresh(siteEntry) ? siteEntry.value : null;
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
    : [...(site?.items || []), ...vault.jobs]
        .filter(
          (j) =>
            (site?.items || []).some((row) => row.id === j.id) ||
            safeURL(j.url)?.hostname === host ||
            vault.bindings.some(
              (b) => b.job_id === j.id && safeURL(b.url)?.hostname === host,
            ),
        )
        .slice(0, 8);
  const items = [
    ...new Map(
      (exact.length ? exact : related).map((job) => [job.id, job]),
    ).values(),
  ].map((job) => ({
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
    site,
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
  let vault = await loadVault();
  const revision = connectionRevision;
  let remoteJobs = [];
  const text = String(query || "")
    .trim()
    .slice(0, 200);
  if (text.length < 2) return [];
  const words = text.toLowerCase().split(/\s+/);
  const matches = (job) =>
    words.every((word) =>
      (job.title + " " + job.company_name + " " + job.url)
        .toLowerCase()
        .includes(word),
    );
  if (vault.settings) {
    try {
      const result = await radiusJSON(
        vault.settings,
        `/api/v1/companion/jobs/search?q=${encodeURIComponent(text)}`,
      );
      vault = await cacheRemoteJobs(vault.settings, result, 10);
      remoteJobs = result.items.map(normalizeJob);
    } catch (error) {
      const current = await loadVault();
      if (
        revision !== connectionRevision ||
        !sameConnection(current.settings, vault.settings)
      )
        throw new Error("Radius connection changed during search.");
      vault = current;
      if (!vault.jobs.some(matches)) throw error;
    }
  }
  const { applicant } = await chrome.storage.local.get("applicant"),
    stamp = await profileStamp(applicant);
  const current = await loadVault();
  if (
    revision !== connectionRevision ||
    !sameConnection(current.settings, vault.settings)
  )
    throw new Error("Radius connection changed during search.");
  return [
    ...new Map(
      [...remoteJobs, ...vault.jobs.filter(matches)].map((job) => [
        job.id,
        job,
      ]),
    ).values(),
  ]
    .slice(0, 10)
    .map((job) => ({
      job,
      exact: false,
      ready: !!readyPackage(vault, job.id, stamp),
      has_saved: !!vault.packages?.[`${job.id}:${vault.settings?.profile_id}`],
    }));
}

function sameConnection(first, second) {
  return (
    first?.origin === second?.origin &&
    first?.api_origin === second?.api_origin &&
    first?.profile_id === second?.profile_id
  );
}

async function cacheRemoteJobs(settings, result, limit) {
  if (!Array.isArray(result?.items) || result.items.length > limit)
    throw new Error("Radius returned an invalid job lookup.");
  const jobs = result.items.map(normalizeJob);
  return changeVault((current) => {
    if (!sameConnection(current.settings, settings))
      throw new Error("Radius connection changed during lookup.");
    return cacheJobs(current, jobs);
  });
}

// Only the user's popup inspection resolves a cache miss. Badge updates never
// transmit the user's browsing URLs to Radius.
export async function lookupPage(url) {
  await lookupSite(url);
  const vault = await loadVault(),
    page = safeURL(url);
  if (
    !vault.settings ||
    !page ||
    matchJobs(vault, url).length ||
    transientURL(url)
  )
    return;
  const internal =
    page.origin === vault.settings.origin &&
    page.pathname.match(/^\/jobs\/(\d+)(?:\/apply)?\/?$/);
  if (
    !internal &&
    (page.protocol !== "https:" ||
      !persistableURL(url) ||
      page.origin === vault.settings.origin)
  )
    return;
  try {
    const result = internal
      ? {
          items: [
            await radiusJSON(vault.settings, `/api/v1/jobs/${internal[1]}`),
          ],
        }
      : await radiusJSON(
          vault.settings,
          `/api/v1/companion/jobs/lookup?url=${encodeURIComponent(urlKey(url))}`,
        );
    await cacheRemoteJobs(vault.settings, result, 100);
  } catch {
    return "Radius lookup is unavailable. You can still use cached jobs or reconnect Radius.";
  }
}
function siteKey(settings, url) {
  return `site:${settings.api_origin}:${safeURL(url)?.hostname}`;
}
const pendingSites = new Map();
async function lookupSite(url) {
  const vault = await loadVault(),
    page = safeURL(url);
  if (
    !vault.settings ||
    !page ||
    page.protocol !== "https:" ||
    page.origin === vault.settings.origin ||
    page.origin === vault.settings.api_origin
  )
    return;
  const settings = vault.settings,
    key = siteKey(settings, url);
  if (fresh(await sessionGet(key))) return;
  if (pendingSites.has(key)) return pendingSites.get(key);
  const revision = connectionRevision;
  const request = (async () => {
    try {
      const value = await radiusJSON(
        settings,
        `/api/v1/companion/site?host=${encodeURIComponent(page.hostname)}`,
      );
      if (
        !Array.isArray(value.feeds) ||
        value.feeds.length > 100 ||
        !Array.isArray(value.items) ||
        value.items.length > 8 ||
        !Number.isSafeInteger(value.job_count) ||
        value.job_count < 0
      )
        throw new Error("Invalid site status");
      const rawItems = value.items;
      value.items = value.items.map(normalizeJob);
      if (
        revision === connectionRevision &&
        sameConnection((await loadVault()).settings, settings)
      ) {
        await sessionPut(key, { value, expires: Date.now() + 300000 });
        if (rawItems.length)
          await cacheRemoteJobs(settings, { items: rawItems }, 8);
      }
    } catch {
      // Briefly back off when the API is unavailable; retain offline job matching.
      if (revision === connectionRevision)
        await sessionPut(key, { value: null, expires: Date.now() + 30000 });
    }
  })();
  pendingSites.set(key, request);
  try {
    await request;
  } finally {
    pendingSites.delete(key);
  }
}
export async function updateBadge(tabId, url) {
  try {
    if (!url) {
      const tab = await chrome.tabs.get(tabId);
      url = tab.url;
    }
    await lookupSite(url);
    const state = await pageState(tabId, url);
    const ready = state.items.filter(
      (i) => i.exact && i.ready && i.job.active !== false,
    ).length;
    const found =
      state.items.length > 0 ||
      state.site?.feeds?.length > 0 ||
      state.site?.job_count > 0;
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
          ? state.site
            ? `Radius · ${state.site.job_count} jobs · ${state.site.feeds.length} career feeds`
            : "Radius · Discovered job · Prepare your CV"
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

export async function captureQuestions(
  tabId,
  requestedJobId,
  documentId,
  openRadius = true,
) {
  const tab = await chrome.tabs.get(tabId),
    state = await pageState(tabId, tab.url);
  const exact = state.items.filter(
    (item) => item.exact && (!requestedJobId || item.job.id === requestedJobId),
  );
  if (!state.settings?.profile_id || exact.length !== 1)
    throw new Error(
      "Choose a profile and link this page to one Radius job first.",
    );
  const [result] = await chrome.scripting.executeScript({
    target: { tabId, documentIds: [documentId] },
    func: async () => {
      try {
        return await globalThis.applicationFields.describe(true, true);
      } catch (error) {
        return { error: error.message };
      }
    },
  });
  if (result?.result?.error) throw new Error(result.result.error);
  if (
    !result?.result ||
    (await chrome.tabs.get(tabId)).url !== tab.url ||
    result.result.url !== tab.url
  )
    throw new Error(
      "The application changed during scanning. Try again on the intended step.",
    );
  const form = {
    format: "radius-form-v1",
    job_id: exact[0].job.id,
    profile_id: state.settings.profile_id,
    ...result.result,
    captured_at: Date.now(),
  };
  await changeVault((current) => {
    if (!sameConnection(current.settings, state.settings))
      throw new Error("Radius connection changed during scanning.");
    const forms = {
      ...current.forms,
      [`${form.job_id}:${form.profile_id}`]: form,
    };
    const keys = Object.keys(forms).sort(
      (a, b) => forms[b].captured_at - forms[a].captured_at,
    );
    for (const key of keys.slice(20)) delete forms[key];
    return { ...current, forms };
  });
  if (openRadius) await prepareJob(form.job_id);
  return {
    message: `${form.fields.length} questions captured. Your Radius brief will include this application step.`,
  };
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
  if ((await currentCV(vault.settings)) !== saved.cv_updated_at) {
    throw new Error(
      "The original CV changed or is unavailable. Prepare a current package before filling tailored details.",
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
export async function attachPreparedDocuments(tabId, documentId, jobId) {
  const revision = connectionRevision;
  const tab = await chrome.tabs.get(tabId),
    vault = await loadVault();
  const { applicant } = await chrome.storage.local.get("applicant");
  if (!applicant) throw new Error("Save your applicant profile first.");
  const stamp = await profileStamp(applicant);
  const saved = readyPackage(vault, jobId, stamp);
  if (!saved) throw new Error("Prepare a current package for this job first.");
  // Reuse the same job binding, CV revision and one-use page identity checks as filling.
  const profile = await fillProfile(
    tabId,
    tab.url,
    jobId,
    validateProfile(applicant),
  );
  const [scan] = await chrome.scripting.executeScript({
    target: { tabId, documentIds: [documentId] },
    func: async () => {
      try {
        return await globalThis.applicationFields.documentTargets();
      } catch (error) {
        return { error: error.message };
      }
    },
  });
  const form = scan?.result;
  if (form?.error) throw new Error(form.error);
  if (!form || form.url !== tab.url)
    throw new Error("The application page changed.");
  const types = {
    pdf: "application/pdf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  };
  const classified = form.fields
    .filter((field) => field.type === "file")
    .map((field) => ({ ...field, kind: field.document_kind }));
  const files = [];
  for (const kind of ["cv", "cover_letter"]) {
    const matches = classified.filter((field) => field.kind === kind);
    if (matches.length !== 1 || !matches[0].available) continue;
    const field = matches[0];
    const accepts = field.accept
      .toLowerCase()
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    const format = (kind === "cv" ? ["pdf"] : ["pdf", "docx"]).find(
      (format) =>
        !accepts.length ||
        accepts.some((value) =>
          ["*/*", "application/*", `.${format}`, types[format]].includes(value),
        ),
    );
    if (!format) continue;
    const document = await radiusRequest(
      vault.settings,
      `/api/v1/profiles/${saved.profile_id}/application-document?kind=${kind}&format=${kind === "cv" ? "latex_pdf" : format}`,
      {
        method: "POST",
        responseType: "bytes",
        body: {
          job_id: jobId,
          cv_updated_at: saved.cv_updated_at,
          cv: saved.package.cv,
          cover_letter: saved.package.cover_letter,
          ...(saved.package.latex_documents
            ? { latex_documents: saved.package.latex_documents }
            : {}),
        },
      },
    );
    if (document.type !== types[format])
      throw new Error("Radius returned the wrong document format.");
    // Bounded bytes exist only for this action; never persist them to extension storage.
    let binary = "";
    for (let i = 0; i < document.bytes.length; i += 32768)
      binary += String.fromCharCode(...document.bytes.subarray(i, i + 32768));
    files.push({
      id: field.id,
      name: `${kind === "cv" ? "CV" : "Cover-letter"}-${jobId}.${format}`,
      type: document.type,
      base64: btoa(binary),
    });
  }
  const current = await loadVault(),
    latestApplicant = (await chrome.storage.local.get("applicant")).applicant;
  if (
    !sameConnection(vault.settings, current.settings) ||
    (await profileStamp(latestApplicant)) !== stamp ||
    stableJSON(current.packages?.[`${jobId}:${saved.profile_id}`]) !==
      stableJSON(saved) ||
    (await currentCV(current.settings)) !== saved.cv_updated_at ||
    (await chrome.tabs.get(tabId)).url !== tab.url
  )
    throw new Error(
      "Your application, CV or connection changed during export. Prepare it again.",
    );
  if (revision !== connectionRevision)
    throw new Error("Radius connection changed during export.");
  const [result] = await chrome.scripting.executeScript({
    target: { tabId, documentIds: [documentId] },
    func: async (plan) => {
      try {
        return await globalThis.applicationFields.attachDocuments(plan);
      } catch (error) {
        return { error: error.message };
      }
    },
    args: [
      {
        url: tab.url,
        form_id: form.form_id,
        page_guard: profile.page_guard,
        files,
      },
    ],
  });
  if (result?.result?.error) throw new Error(result.result.error);
  if (!result?.result)
    throw new Error("The page changed before documents could be selected.");
  const skipped = classified.length - files.length + (form.hidden_uploads || 0);
  return {
    ...result.result,
    message:
      result.result.message +
      (skipped
        ? ` ${skipped} upload fields need manual review (existing files, hidden controls, ambiguous labels or unsupported formats).`
        : ""),
  };
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
      return cacheJobs(v, [normalizeJob(job)]);
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
  if (pack.latex_documents !== undefined) {
    const sources = pack.latex_documents;
    if (
      !sources ||
      typeof sources !== "object" ||
      Array.isArray(sources) ||
      Object.entries(sources).some(
        ([key, value]) =>
          !["cv", "cover_letter"].includes(key) ||
          typeof value !== "string" ||
          !value.trim() ||
          value.length > 100000,
      )
    )
      throw new Error("Invalid LaTeX documents in the returned package.");
    await Promise.all(
      Object.keys(sources).map((kind) =>
        radiusRequest(
          settings,
          `/api/v1/profiles/${pack.profile_id}/application-document?kind=${kind}&format=tex`,
          {
            method: "POST",
            responseType: "text",
            body: {
              job_id: pack.job_id,
              cv_updated_at: pack.cv_updated_at,
              cv: pack.cv,
              cover_letter: pack.cover_letter,
              latex_documents: sources,
            },
          },
        ),
      ),
    );
  }
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
  const path = safeURL(sender.url),
    match = path.pathname.match(/^\/jobs\/(\d+)\/apply\/?$/);
  const profileId = Number(path.searchParams.get("profile_id"));
  const reply = {
    form:
      match && profileId === settings.profile_id
        ? vault.forms?.[`${match[1]}:${profileId}`] || null
        : null,
  };
  if (Date.now() - metadataAt < 10000) return reply;
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
  return reply;
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
      `brief:${id}`,
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

// Extension-owned preparation keeps the employer tab in place.
export async function prepareCurrentJob(
  tabId,
  jobId,
  requestedLanguage = "auto",
  refreshed = false,
) {
  const tab = await chrome.tabs.get(tabId);
  const state = await pageState(tabId, tab.url);
  const item = currentJob(state, jobId);
  if (!item)
    throw new Error(
      "Link this page to exactly one current job before preparing.",
    );
  if (!state.settings?.profile_id)
    throw new Error("Choose your Radius profile in extension settings.");
  if (item.job.active === false) throw new Error("This job is closed.");
  const revision = connectionRevision;
  const settings = state.settings;
  const [job, profiles] = await Promise.all([
    radiusJSON(settings, `/api/v1/jobs/${item.job.id}`),
    radiusJSON(settings, "/api/v1/profiles"),
  ]);
  const profile = profiles.items?.find(
    (profile) => profile.id === settings.profile_id,
  );
  if (!profile?.cv)
    throw new Error("Save your base LaTeX CV in the extension profile first.");
  if (settings.cv_updated_at !== profile.cv.updated_at) {
    if (refreshed)
      throw new Error(
        "Your CV is changing. Finish saving it, then prepare again.",
      );
    await refreshRadius();
    return prepareCurrentJob(tabId, jobId, requestedLanguage, true);
  }
  const language = selectCVLanguage(
    requestedLanguage,
    `${job.title}\n${job.description || ""}`,
  );
  const cv = await radiusJSON(
    settings,
    `/api/v1/profiles/${profile.id}/cv/text?language=${language}`,
  );
  const vault = await loadVault();
  if (
    revision !== connectionRevision ||
    !sameConnection(vault.settings, settings) ||
    (await chrome.tabs.get(tabId)).url !== tab.url
  )
    throw new Error(
      "The current job or Radius connection changed during preparation.",
    );
  const { applicant } = await chrome.storage.local.get("applicant");
  if (!applicant || (await currentCV(settings)) !== profile.cv.updated_at)
    throw new Error("Your saved CV changed. Sync your profile and try again.");
  const form = vault.forms?.[`${job.id}:${profile.id}`];
  const prompt = buildCurrentJobPrompt({
    job,
    profile,
    cv: { text: cv.text, updated_at: profile.cv.updated_at },
    language,
    form,
  });
  await sessionPut(`brief:${tabId}`, {
    job_id: job.id,
    profile_id: profile.id,
    cv_updated_at: profile.cv.updated_at,
    profile_stamp: await profileStamp(applicant),
    prompt,
    language,
    expires: Date.now() + 30 * 60000,
  });
  return {
    prompt,
    language,
    job_id: job.id,
    chatgpt_url: "https://chatgpt.com/",
  };
}

export async function importCurrentResult(tabId, jobId, input) {
  const tab = await chrome.tabs.get(tabId);
  const state = await pageState(tabId, tab.url);
  const item = currentJob(state, jobId);
  const pack = await readPackage(input);
  if (!item || pack.job_id !== item.job.id)
    throw new Error("This result does not belong to the current job.");
  if ((await chrome.tabs.get(tabId)).url !== tab.url)
    throw new Error("The current job page changed.");
  await storePreparedPackage(pack, "", "");
  return { message: "Application documents connected to the current job." };
}

export async function downloadCurrentDocument(tabId, jobId, kind, format) {
  if (
    !["cv", "cover_letter"].includes(kind) ||
    !["tex", "latex_pdf"].includes(format)
  )
    throw new Error("Choose a LaTeX PDF or source.");
  const tab = await chrome.tabs.get(tabId);
  const state = await pageState(tabId, tab.url);
  const item = currentJob(state, jobId);
  const { applicant } = await chrome.storage.local.get("applicant");
  const vault = await loadVault();
  const saved =
    item && readyPackage(vault, item.job.id, await profileStamp(applicant));
  if (!saved)
    throw new Error("Import a current application package for this job first.");
  if ((await currentCV(vault.settings)) !== saved.cv_updated_at)
    throw new Error("Your saved CV changed. Prepare a fresh package.");
  const revision = connectionRevision;
  const filename = `${kind === "cv" ? "CV" : "Cover-letter"}-${jobId}.${format === "tex" ? "tex" : "pdf"}`;
  const result = await radiusRequest(
    vault.settings,
    `/api/v1/profiles/${saved.profile_id}/application-document?kind=${kind}&format=${format}`,
    {
      method: "POST",
      responseType: format === "tex" ? "text" : "bytes",
      body: {
        job_id: jobId,
        cv_updated_at: saved.cv_updated_at,
        cv: saved.package.cv,
        cover_letter: saved.package.cover_letter,
        latex_documents: saved.package.latex_documents,
      },
    },
  );
  if (
    revision !== connectionRevision ||
    (await chrome.tabs.get(tabId)).url !== tab.url
  )
    throw new Error("The current job or connection changed during export.");
  if (format === "tex")
    return { filename, source: result, type: "application/x-tex" };
  let binary = "";
  for (let i = 0; i < result.bytes.length; i += 32768)
    binary += String.fromCharCode(...result.bytes.subarray(i, i + 32768));
  return { filename, type: result.type, base64: btoa(binary) };
}

export async function saveConnectedApplicant(input) {
  const applicant = validateProfile(input);
  const vault = await loadVault();
  const settings = vault.settings;
  if (!settings?.profile_id || !(await permission(settings)))
    throw new Error("Reconnect Radius before saving your profile.");
  const revision = connectionRevision;
  const result = await radiusJSON(settings, "/api/v1/profiles");
  const profile = result.items?.find(
    (profile) => profile.id === settings.profile_id,
  );
  if (!profile) throw new Error("The selected profile no longer exists.");
  const { id, cv, updated_at, ...payload } = profile;
  const {
    application,
    cv_text,
    base_cvs,
    education,
    experiences,
    source_notes,
    localized_content,
    ...details
  } = applicant;
  if (localized_content) {
    payload.localized_content = localized_content;
    // Keep the legacy English fields in sync for search and older clients.
    Object.assign(payload, localized_content.en);
    delete payload.application_preferences;
  }
  payload.application_details = {
    ...details,
    preferences: { ...details.preferences, ...localized_content?.en?.application_preferences },
    ...(profile.application_details?.experiences
      ? { experiences: profile.application_details.experiences }
      : {}),
  };
  const previous = cv
    ? await radiusJSON(settings, `/api/v1/profiles/${id}/cv/text`)
    : { variants: {} };
  if (
    Object.keys(previous.variants ?? {}).some((language) => !base_cvs[language])
  )
    throw new Error(
      "Use Remove saved base CVs to delete saved sources; blank fields do not delete them.",
    );
  if (revision !== connectionRevision)
    throw new Error("The connection changed while saving.");
  await radiusRequest(settings, `/api/v1/profiles/${id}`, {
    method: "PUT",
    body: payload,
  });
  for (const [language, source] of Object.entries(base_cvs)) {
    if (source.trim() === previous.variants?.[language]?.text?.trim()) continue;
    if (revision !== connectionRevision)
      throw new Error("The connection changed while saving.");
    await radiusRequest(
      settings,
      `/api/v1/profiles/${id}/cv?filename=base-cv-${language}.tex&language=${language}`,
      { method: "PUT", body: source, contentType: "application/x-tex" },
    );
  }
  if (revision !== connectionRevision)
    throw new Error("The connection changed while saving.");
  await refreshRadius();
  return { applicant: (await chrome.storage.local.get("applicant")).applicant };
}

export async function removeConnectedCVs() {
  const vault = await loadVault();
  if (!vault.settings?.profile_id || !(await permission(vault.settings)))
    throw new Error("Reconnect Radius first.");
  const revision = connectionRevision;
  await radiusRequest(
    vault.settings,
    `/api/v1/profiles/${vault.settings.profile_id}/cv`,
    { method: "DELETE" },
  );
  if (revision !== connectionRevision)
    throw new Error("The connection changed while removing CVs.");
  await refreshRadius();
  return { applicant: (await chrome.storage.local.get("applicant")).applicant };
}
