import {
  pageState,
  searchJobs,
  overview,
  connectRadius,
  refreshRadius,
  selectedProfile,
  addFeed,
  addJobToProfile,
  disconnectRadius,
  prepareJob,
  usePage,
  openApplication,
  fillProfile,
  captureDraft,
  watchPage,
  importReturnedPackage,
  radiusState,
  applyClick,
  installNavigation,
  lookupPage,
} from "./application-state.js";
import { validateProfile } from "./profile.js";
import { readPackage, connectSources } from "./sources.js";

// Only extension pages can read the saved applicant profile directly.
const storageReady = chrome.storage.local.setAccessLevel({
  accessLevel: "TRUSTED_CONTEXTS",
});
const running = new Set();
class UserError extends Error {}

async function inspect(tabId, linkedApplication = false) {
  const injected = await chrome.scripting.executeScript({
    target: { tabId },
    files: [
      "page-context.js",
      "radius.js",
      "history.js",
      "best-effort.js",
      "content.js",
      "apply-watch.js",
    ],
  });
  const documentId = injected[0]?.documentId;
  if (!documentId)
    throw new UserError("Open an application page and try again.");
  const [result] = await chrome.scripting.executeScript({
    target: { tabId, documentIds: [documentId] },
    func: (linked) => globalThis.applicationCompanion.inspect(linked),
    args: [linkedApplication],
  });
  return { ...result.result, documentId };
}

async function handle(message) {
  await storageReady;
  if (message.type === "add-feed") return addFeed(message.feed);
  if (message.type === "add-job") return addJobToProfile(message.job);
  if (message.type === "search-jobs") return searchJobs(message.query);
  if (message.type === "import-package")
    return importReturnedPackage(message.input, message.url);
  if (message.type === "overview") return overview();
  if (message.type === "connect-radius") return connectRadius(message.settings);
  if (message.type === "refresh-radius") return refreshRadius();
  if (message.type === "select-profile")
    return selectedProfile(message.profileId);
  if (message.type === "disconnect-radius") {
    await disconnectRadius();
    return {};
  }
  if (message.type === "prepare-job") {
    await prepareJob(message.jobId);
    return {};
  }
  const tabId = message.tabId;
  if (!Number.isInteger(tabId))
    throw new UserError("Select an application tab first.");
  if (message.type === "inspect") {
    const tab = await chrome.tabs.get(tabId);
    const lookupError = await lookupPage(tab.url);
    const catalog = await pageState(tabId, tab.url);
    catalog.lookup_error = lookupError;
    if (catalog.items.some((item) => item.exact)) watchPage(tabId);
    let info;
    try {
      info = await inspect(
        tabId,
        catalog.items.some((item) => item.exact && item.ready),
      );
    } catch {
      info = {
        supported: false,
        reason: "Open an employer application page to fill its fields.",
      };
    }
    const saved = (await chrome.storage.session.get(`report:${tabId}`))[
      `report:${tabId}`
    ];
    return {
      ...info,
      catalog,
      lastReport: saved?.documentId === info.documentId ? saved.report : null,
    };
  }
  if (message.type === "use-page") {
    const tab = await chrome.tabs.get(tabId);
    await usePage(tabId, message.jobId, tab.url);
    return {};
  }
  if (message.type === "open-application") {
    await openApplication(tabId, message.jobId);
    return {};
  }
  if (message.type === "capture") {
    const info = await inspect(tabId);
    if (info.mode !== "import")
      throw new UserError("Open the prepared Radius application draft first.");
    const [result] = await chrome.scripting.executeScript({
      target: { tabId, documentIds: [info.documentId] },
      func: () => globalThis.radiusApplicationSource.read(),
    });
    if (!result.result)
      throw new UserError(
        "No current saved package was found on this Radius page.",
      );
    const catalog = await overview();
    if (catalog.connected) {
      const tab = await chrome.tabs.get(tabId);
      return captureDraft(result.result, { url: tab.url, frameId: 0, tab });
    }
    const pack = await readPackage(JSON.stringify(result.result.package));
    const { applicant } = await chrome.storage.local.get("applicant");
    if (!applicant)
      throw new UserError("Import your CV facts into the profile first.");
    const current = validateProfile(applicant);
    const url =
      current.application.job_id === pack.job_id ? current.application.url : "";
    let connected;
    try {
      connected = connectSources(
        current,
        result.result.cv_text || current.cv_text,
        pack,
        url,
      );
    } catch (error) {
      throw new UserError(error.message);
    }
    await chrome.storage.local.set({ applicant: connected });
    return {
      message:
        "ChatGPT result connected to your saved CV facts. Set the employer application URL in Edit profile if it is not linked yet.",
    };
  }
  if (message.type !== "fill") throw new UserError("Unknown extension action.");
  if (running.has(tabId))
    throw new UserError("This application is already being filled.");
  running.add(tabId);
  try {
    const tab = await chrome.tabs.get(tabId);
    const catalog = await pageState(tabId, tab.url);
    const info = await inspect(
      tabId,
      catalog.items.some(
        (item) =>
          item.exact &&
          item.ready &&
          (!message.jobId || item.job.id === message.jobId),
      ),
    );
    if (!info.supported) throw new UserError(info.reason);
    const currentTab = await chrome.tabs.get(tabId);
    if (currentTab.url !== tab.url)
      throw new UserError(
        "The application page changed. Inspect it before filling again.",
      );
    const { applicant } = await chrome.storage.local.get("applicant");
    if (!applicant) throw new UserError("Save your applicant profile first.");
    let profile;
    try {
      profile = validateProfile(applicant);
    } catch (error) {
      throw new UserError(error.message);
    }
    try {
      profile = await fillProfile(tabId, tab.url, message.jobId, profile);
    } catch (error) {
      throw new UserError(error.message);
    }
    // Pin the selected document: navigation must not send the profile to a new page.
    const [result] = await chrome.scripting.executeScript({
      target: { tabId, documentIds: [info.documentId] },
      func: (data) => globalThis.applicationCompanion.run(data),
      args: [{ ...profile, page_url: tab.url, cv_text: "", source_notes: "" }],
    });
    if (!result?.result)
      throw new UserError(
        "The page changed while filling. Inspect the draft before trying again.",
      );
    await chrome.storage.session.set({
      [`report:${tabId}`]: {
        documentId: info.documentId,
        report: result.result,
      },
    });
    return result.result;
  } finally {
    running.delete(tabId);
  }
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return false;
  const internal = sender.url?.startsWith(chrome.runtime.getURL(""));
  if (!internal) {
    if (
      !sender.tab ||
      sender.frameId !== 0 ||
      !["radius-draft", "radius-state", "apply-click"].includes(message?.type)
    )
      return false;
    const task =
      message.type === "radius-draft"
        ? captureDraft(message.source, sender)
        : message.type === "radius-state"
          ? radiusState(sender, message.theme)
          : applyClick(sender, message);
    task
      .then((result) => reply({ ok: true, result }))
      .catch((error) => reply({ ok: false, error: error.message }));
    return true;
  }
  if (
    ![
      "search-jobs",
      "import-package",
      "inspect",
      "fill",
      "capture",
      "overview",
      "connect-radius",
      "refresh-radius",
      "select-profile",
      "disconnect-radius",
      "prepare-job",
      "add-feed",
      "add-job",
      "use-page",
      "open-application",
    ].includes(message?.type)
  )
    return false;
  handle(message)
    .then((result) => reply({ ok: true, result }))
    .catch((error) =>
      reply({
        ok: false,
        error:
          error instanceof UserError ||
          [
            "import-package",
            "connect-radius",
            "refresh-radius",
            "select-profile",
            "add-feed",
            "add-job",
            "use-page",
            "prepare-job",
            "open-application",
          ].includes(message.type)
            ? error.message
            : "Could not complete this action. Open the application tab and click the extension toolbar icon again. Chrome pages cannot be filled; file pages require Allow access to file URLs. If filling had started, inspect the draft before retrying.",
      }),
    );
  return true;
});
installNavigation();
