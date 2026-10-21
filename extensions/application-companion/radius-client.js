import { localizedKeys } from "./profile-language.js";
import { validateProfile } from "./profile.js";
import { normalizeJob, validateSettings } from "./vault.js";

let connectionRequests = new AbortController();
export function cancelRadiusRequests() {
  connectionRequests.abort();
  connectionRequests = new AbortController();
}

// Bound bytes while streaming, before allocating a complete response body.
export async function radiusRequest(
  settings,
  path,
  {
    method = "GET",
    body,
    signal,
    responseType = "json",
    contentType = "application/json",
  } = {},
) {
  const origin = validateSettings(settings).api_origin;
  const headers = body === undefined ? {} : { "Content-Type": contentType };
  const response = await fetch(new URL(path, origin), {
    method,
    headers,
    ...(body === undefined
      ? {}
      : {
          body:
            contentType === "application/json" ? JSON.stringify(body) : body,
        }),
    credentials: "include",
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.any([
      connectionRequests.signal,
      AbortSignal.timeout(15000),
      ...(signal ? [signal] : []),
    ]),
  });
  if (response.ok && response.status === 204) return null;
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > (responseType === "bytes" && response.ok ? 5242880 : 500000))
        throw new Error("Radius returned too much data for one page.");
      chunks.push(value);
    }
  } finally {
    void reader.cancel().catch(() => {});
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  if (responseType === "bytes" && response.ok) {
    const type = response.headers.get("content-type")?.split(";")[0];
    if (
      ![
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      ].includes(type)
    )
      throw new Error("Radius did not return a PDF or DOCX document.");
    if (!bytes.length) throw new Error("Radius returned an empty document.");
    return { bytes, type };
  }
  if (response.ok && responseType === "text")
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  let result;
  try {
    result = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(bytes),
    );
  } catch {
    if (!response.ok)
      throw new Error(
        `Radius returned ${response.status}. Check the connection and sign in to Radius in this browser.`,
      );
    throw new Error(
      "Radius did not return API JSON. Check the API address under Connection details.",
    );
  }
  if (!response.ok) {
    const detail = Array.isArray(result?.detail)
      ? result.detail
          .map((issue) => issue.msg || issue.type || "Invalid request")
          .join("; ")
      : result?.detail;
    throw new Error(
      detail ||
        `Radius returned ${response.status}. Check the connection and sign in to Radius in this browser.`,
    );
  }
  return result;
}

export async function radiusJSON(settings, path, signal) {
  return radiusRequest(settings, path, { signal });
}

// Legacy browser-local CVs stay scoped to the explicitly connected app origin.
async function localCVData(settings) {
  if (!settings.profile_id) return "";
  const tabs = await chrome.tabs.query({ url: settings.origin + "/*" });
  for (const tab of tabs) {
    try {
      const [result] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (profileId) => {
          try {
            const raw = localStorage.getItem(
              `radius:application-cv:v1:${profileId}`,
            );
            if (!raw || raw.length > 150000) return "";
            const cv = JSON.parse(raw);
            return typeof cv.text === "string" &&
              cv.text.trim() &&
              cv.text.length <= 100000 &&
              typeof cv.updated_at === "string" &&
              cv.updated_at.length <= 200
              ? { text: cv.text, updated_at: cv.updated_at }
              : null;
          } catch {
            return "";
          }
        },
        args: [settings.profile_id],
      });
      if (result?.result?.updated_at && typeof result.result.text === "string")
        return result.result;
    } catch {
      /* Closing/navigating tab: try the next connected Radius tab. */
    }
  }
  return "";
}
export async function localCVVersion(settings) {
  return (await localCVData(settings))?.updated_at || "";
}
export async function currentCV(settings, profiles) {
  profiles ||= await radiusJSON(settings, "/api/v1/profiles");
  const selected = profiles.items?.find((p) => p.id === settings.profile_id);
  if (!selected) return "";
  // Supports older Radius installations that exposed CV metadata as well.
  if (typeof selected.cv?.updated_at === "string")
    return selected.cv.updated_at;
  return localCVVersion(settings);
}

export async function syncRadius(settings, signal) {
  const checkedAt = Date.now();
  const profilesResult = await radiusJSON(settings, "/api/v1/profiles", signal);
  if (!Array.isArray(profilesResult.items))
    throw new Error("Radius profiles are unavailable.");
  const profiles = profilesResult.items
    .filter((p) => Number.isInteger(p.id) && p.id > 0)
    .map((p) => ({
      id: p.id,
      name: String(p.name || `Profile ${p.id}`).slice(0, 300),
      has_cv: !!p.cv,
    }));
  const profile =
    profiles.find((p) => p.id === settings.profile_id) ||
    (!settings.profile_id && profiles.length === 1 ? profiles[0] : null);
  const selectedSettings = {
    ...validateSettings(settings),
    profile_id: profile?.id || null,
    profile_name: profile?.name || "",
  };
  selectedSettings.cv_storage = profilesResult.items.find(
    (p) => p.id === profile?.id,
  )?.cv
    ? "api"
    : "browser";
  selectedSettings.cv_updated_at = profile
    ? await currentCV(selectedSettings, profilesResult)
    : "";
  if (profile) profile.has_cv = !!selectedSettings.cv_updated_at;
  const source = profilesResult.items.find((p) => p.id === profile?.id);
  const cvData =
    profile && source?.cv
      ? await radiusJSON(
          settings,
          `/api/v1/profiles/${profile.id}/cv/text`,
          signal,
        )
      : null;
  const applicant = profile
    ? validateProfile({
        ...(source?.application_details || {}),
        localized_content: {
          en: {
            ...Object.fromEntries(
              localizedKeys
                .filter((key) => source?.[key] !== undefined)
                .map((key) => [key, source[key]]),
            ),
            application_preferences: Object.fromEntries(
              ["notice_period", "employment_type"].map((key) => [
                key,
                source?.application_details?.preferences?.[key] ?? "",
              ]),
            ),
          },
          ...source?.localized_content,
        },
        cv_text:
          cvData?.text ?? (await localCVData(selectedSettings))?.text ?? "",
        base_cvs: Object.fromEntries(
          Object.entries(cvData?.variants ?? {}).map(([language, cv]) => [
            language,
            cv.text,
          ]),
        ),
      })
    : null;
  const jobs = [];
  let after = 0,
    jobBytes = 0,
    truncated = false;
  // At most 2,000 small catalog rows / 20 requests / 60 seconds per sync.
  const syncSignal = signal
    ? AbortSignal.any([signal, AbortSignal.timeout(60000)])
    : AbortSignal.timeout(60000);
  for (let page = 0; page < 20; page++) {
    const result = await radiusJSON(
      settings,
      `/api/v1/companion/jobs?limit=100&after_id=${after}`,
      syncSignal,
    );
    if (!Array.isArray(result.items) || result.items.length > 100)
      throw new Error("Radius returned an invalid job catalog page.");
    for (const job of result.items) {
      try {
        const row = normalizeJob(job);
        jobBytes += new TextEncoder().encode(JSON.stringify(row)).length;
        if (jobBytes > 2000000) {
          truncated = true;
          break;
        }
        jobs.push(row);
      } catch {
        /* Invalid source URLs cannot acquire a generated application. */
      }
    }
    if (result.next_after_id === null || truncated) break;
    if (
      !Number.isSafeInteger(result.next_after_id) ||
      result.next_after_id <= after
    )
      throw new Error("Radius returned an invalid catalog cursor.");
    after = result.next_after_id;
    if (page === 19) truncated = true;
  }
  const unique = [...new Map(jobs.map((j) => [j.id, j])).values()];
  return {
    applicant,
    jobs: unique,
    profiles,
    total: unique.length,
    truncated,
    settings: selectedSettings,
    cv_observed_at: checkedAt,
    synced_at: Date.now(),
  };
}
