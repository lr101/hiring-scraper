import { validateProfile } from "./profile.js";
import { transientURL, urlKey } from "./vault.js";
const button = document.querySelector("#fill"),
  status = document.querySelector("#status");
let tabId,
  mode = "fill",
  selectedJob = null,
  info,
  activeTab;
async function request(type, extra = {}) {
  const result = await chrome.runtime.sendMessage({ type, tabId, ...extra });
  if (!result?.ok)
    throw new Error(result?.error || "The extension did not respond.");
  return result.result;
}
function message(error) {
  status.textContent = error.message;
  status.classList.add("error");
}
function suggestedPageUrl(value) {
  if (transientURL(value)) return "";
  const key = urlKey(value);
  return key && !new URL(key).hash ? key : "";
}
function showReport(report) {
  document.querySelector("#result").hidden = false;
  document.querySelector("#result-summary").textContent =
    `${report.filled || 0} fields filled · ${report.experiences_added || 0} experiences added · ${report.unresolved?.length || 0} unresolved`;
  document.querySelector("#result-error").textContent = report.error
    ? `${report.partial_fill ? "Partly filled. " : ""}${report.error}`
    : "";
  document.querySelector("#remaining").replaceChildren(
    ...(report.unresolved || []).map((field) => {
      const li = document.createElement("li"),
        name = document.createElement("strong"),
        reason = document.createElement("small");
      name.textContent = field.label + (field.required ? " (required)" : "");
      reason.textContent = field.reason;
      li.append(name, reason);
      return li;
    }),
  );
  document.querySelector("#field-results").replaceChildren(
    ...(report.fields || []).map((field) => {
      const li = document.createElement("li");
      li.textContent = `${field.label}: ${field.status}${field.source ? " · " + field.source : ""}`;
      return li;
    }),
  );
}
function action(text, fn, primary = false) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  if (primary) b.className = "entry-primary";
  b.onclick = async () => {
    b.disabled = true;
    try {
      await fn();
    } catch (e) {
      message(e);
    } finally {
      b.disabled = false;
    }
  };
  return b;
}
function entry(item, continuation = false) {
  const { job, ready, exact } = item;
  const card = document.createElement("article");
  card.className = "job-entry " + (ready && exact ? "prepared" : "discovered");
  card.dataset.jobId = job.id;
  const top = document.createElement("button");
  top.type = "button";
  top.className = "entry-top entry-main";
  const mark = document.createElement("span");
  mark.className = "document-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = ready ? "✓" : "○";
  const body = document.createElement("span"),
    title = document.createElement("span"),
    company = document.createElement("span"),
    state = document.createElement("span");
  title.className = "entry-title";
  company.className = "entry-company";
  title.textContent = job.title;
  company.textContent = job.company_name;
  state.className = "entry-state";
  state.textContent =
    job.active === false
      ? "Role closed"
      : ready
        ? exact
          ? "CV prepared"
          : "CV prepared · page not linked"
        : item.has_saved
          ? "Update your prepared CV"
          : "Discovered in Radius";
  body.append(company, title, state);
  top.append(mark, body);
  card.append(top);
  const links = document.createElement("div");
  links.className = "entry-links";
  const prepare = async () => {
    if (!exact) await request("use-page", { jobId: job.id });
    await request("prepare-job", { jobId: job.id });
  };
  const use = async () => {
    await request("use-page", { jobId: job.id });
    selectedJob = job.id;
    info = await request("inspect");
    button.hidden = false;
    button.disabled = !info.supported;
    await fill();
  };
  top.onclick = async () => {
    top.disabled = true;
    try {
      if (!ready) await prepare();
      else if (continuation || !exact) await use();
      else if (info.supported) {
        if ((info.catalog?.items || []).filter((i) => i.exact).length > 1)
          await request("use-page", { jobId: job.id });
        selectedJob = job.id;
        await fill();
      } else await request("open-application", { jobId: job.id });
    } catch (e) {
      message(e);
    } finally {
      top.disabled = false;
    }
  };
  top.disabled = job.active === false;
  top.setAttribute(
    "aria-label",
    (ready ? "Use prepared application for " : "Prepare CV for ") + job.title,
  );
  if (job.active !== false) {
    if (ready && (continuation || !exact))
      links.append(action("Use on this page", use, true));
    else if (ready && !info.supported)
      links.append(
        action(
          "Open application",
          () => request("open-application", { jobId: job.id }),
          true,
        ),
      );
    else if (!ready)
      links.append(action("Prepare with ChatGPT", prepare, true));
    else if (
      (info.catalog?.items || []).filter((i) => i.exact && i.ready).length > 1
    )
      links.append(
        action(
          "Fill this application",
          async () => {
            if ((info.catalog?.items || []).filter((i) => i.exact).length > 1)
              await request("use-page", { jobId: job.id });
            selectedJob = job.id;
            await fill();
          },
          true,
        ),
      );
    if (ready)
      links.append(
        action("View documents", () =>
          request("prepare-job", { jobId: job.id }),
        ),
      );
    if (
      exact &&
      mode !== "import" &&
      new URL(activeTab.url).origin !== info.catalog?.settings?.origin
    )
      links.append(
        action("Prepare from these questions", async () => {
          const result = await request("scan-form", { jobId: job.id });
          status.textContent = result.message;
        }),
      );
    if (exact && ready && info.supported)
      links.append(
        action("Attach prepared documents", async () => {
          status.classList.remove("error");
          status.textContent = "Preparing documents…";
          const result = await request("attach-documents", { jobId: job.id });
          status.textContent = result.message;
        }),
      );
  }
  const source = document.createElement("small");
  source.className = "entry-source";
  try {
    source.textContent =
      new URL(job.url).hostname + (exact ? " · Linked page" : "");
  } catch {}
  card.append(links, source);
  return card;
}
async function fill() {
  button.disabled = true;
  status.classList.remove("error");
  status.textContent = "Filling known details…";
  try {
    if (mode === "import") {
      const result = await request("capture");
      status.textContent = result.message;
      return;
    }
    const report = await request("fill", { jobId: selectedJob });
    showReport(report);
    status.textContent = report.error
      ? "Stopped at a conflict. Review the partial draft."
      : `Filled known details. ${report.unresolved?.length || 0} fields need your attention.`;
  } catch (e) {
    message(e);
  } finally {
    button.disabled = false;
  }
}
button.onclick = fill;
document.querySelector("#edit").onclick = () =>
  chrome.runtime.openOptionsPage();
document.querySelector("#connection").onclick = () =>
  chrome.tabs.create({
    url: chrome.runtime.getURL("options.html#radius-connection"),
  });
const addKind = document.querySelector("#add-kind"),
  addForm = document.querySelector("#add-job-form"),
  addStatus = document.querySelector("#add-status"),
  addJobFields = document.querySelector("#add-job-fields"),
  addFeedFields = document.querySelector("#add-feed-fields"),
  addSubmit = document.querySelector("#add-submit");
function setAddKind() {
  const isJob = addKind.value === "job";
  addJobFields.hidden = !isJob;
  addFeedFields.hidden = isJob;
  document.querySelector("#add-job-url").required = isJob;
  document.querySelector("#add-job-title").required = isJob;
  document.querySelector("#add-job-profile").required = isJob;
  document.querySelector("#add-feed-url").required = !isJob;
  addSubmit.textContent = isJob ? "Add job to profile" : "Track company";
  document.querySelector("#add-to-radius .add-description").textContent = isJob
    ? "Save this posting to a profile and connect it to the company by domain."
    : "Radius finds the company by domain or creates it and starts tracking its jobs.";
}
addKind.onchange = setAddKind;
setAddKind();
addForm.onsubmit = async (event) => {
  event.preventDefault();
  addSubmit.disabled = true;
  addStatus.classList.remove("error");
  addStatus.textContent =
    addKind.value === "job" ? "Adding this job…" : "Adding this feed…";
  try {
    if (addKind.value === "job") {
      const profileId = Number(
        document.querySelector("#add-job-profile").value,
      );
      const result = await request("add-job", {
        job: {
          profile_id: profileId,
          url: document.querySelector("#add-job-url").value.trim(),
          title: document.querySelector("#add-job-title").value.trim(),
          company_name: document.querySelector("#add-job-company").value.trim(),
          website_url:
            document.querySelector("#add-company-website").value.trim() ||
            undefined,
        },
      });
      addStatus.textContent = result.already_added
        ? `${result.job.title} is already tracked on ${result.profile_name} (${result.status.replaceAll("_", " ")}).`
        : `Added ${result.job.title} to ${result.profile_name} as a new application.`;
    } else {
      const result = await request("add-feed", {
        feed: {
          feed_url: document.querySelector("#add-feed-url").value.trim(),
          company_name: document
            .querySelector("#add-feed-company")
            .value.trim(),
          provider: document.querySelector("#add-feed-provider").value,
          website_url:
            document.querySelector("#add-company-website").value.trim() ||
            undefined,
          track_company: true,
        },
      });
      addStatus.textContent = result.already_tracked
        ? `${result.company_name} and this job feed are already tracked.`
        : result.company_created
          ? `Now tracking ${result.company_name}. Its first job scan is queued.`
          : `Added this job feed to ${result.company_name}. Its first scan is queued.`;
    }
  } catch (error) {
    addStatus.textContent = error.message;
    addStatus.classList.add("error");
  } finally {
    addSubmit.disabled = false;
  }
};
try {
  const { applicant } = await chrome.storage.local.get("applicant"),
    profile = applicant ? validateProfile(applicant) : null;
  if (profile) {
    document.querySelector("#applicant-name").textContent =
      [profile.first_name, profile.last_name].filter(Boolean).join(" ") ||
      "Saved applicant";
    document.querySelector("#experience-count").textContent =
      `${profile.experiences.length} work experiences saved`;
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTab = tab;
  tabId = tab?.id;
  info = await request("inspect");
  mode = info.mode || "fill";
  const catalog = info.catalog;
  document.querySelector("#connection").textContent = catalog?.connected
    ? "Connection"
    : "Connect Radius";
  document.querySelector("#not-connected").hidden = !!catalog?.connected;
  document.querySelector("#job-search").hidden = !catalog?.connected;
  document.querySelector("#add-to-radius").hidden = !catalog?.connected;
  document.querySelector("#add-to-radius").open =
    !!catalog?.connected && !catalog?.items?.length;
  const profilePicker = document.querySelector("#add-job-profile"),
    profilePlaceholder = document.createElement("option");
  profilePlaceholder.value = "";
  profilePlaceholder.textContent = "Choose a profile";
  profilePicker.replaceChildren(profilePlaceholder);
  for (const profile of catalog?.profiles || []) {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.name;
    profilePicker.append(option);
  }
  if (catalog?.settings?.profile_id)
    profilePicker.value = String(catalog.settings.profile_id);
  const exactJob = (catalog?.items || []).find((item) => item.exact)?.job,
    pageURL = exactJob?.url || activeTab?.url || "",
    sourceUrl = suggestedPageUrl(pageURL);
  document.querySelector("#add-job-url").value = sourceUrl;
  document.querySelector("#add-feed-url").value = sourceUrl;
  document.querySelector("#add-job-title").value =
    exactJob?.title || activeTab?.title || "";
  document.querySelector("#add-job-company").value =
    exactJob?.company_name || "";
  setAddKind();
  if (catalog?.settings?.profile_name && profile)
    document.querySelector("#experience-count").textContent =
      `${profile.experiences.length} work experiences · ${catalog.settings.profile_name}`;
  let searchTimer;
  let searchVersion = 0;
  document.querySelector("#job-query").oninput = () => {
    const version = ++searchVersion;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      try {
        const results = await request("search-jobs", {
          query: document.querySelector("#job-query").value,
        });
        if (version !== searchVersion) return;
        document
          .querySelector("#job-search-results")
          .replaceChildren(...results.map((item) => entry(item)));
      } catch (error) {
        if (version === searchVersion) message(error);
      }
    }, 200);
  };
  const items = catalog?.items || [];
  if (items.length) {
    document.querySelector("#jobs-section").hidden = false;
    document.querySelector("#jobs-caption").textContent = items.some(
      (i) => i.exact,
    )
      ? `${items.length} matching ${items.length === 1 ? "role" : "roles"}`
      : "Other roles on this site";
    document
      .querySelector("#job-entries")
      .replaceChildren(...items.map((i) => entry(i)));
  }
  if (catalog?.continuation) {
    document.querySelector("#continue-section").hidden = false;
    document
      .querySelector("#continue-entry")
      .replaceChildren(entry(catalog.continuation, true));
  }
  button.textContent =
    mode === "import"
      ? "Connect ChatGPT result"
      : catalog?.connected && !items.some((i) => i.exact && i.ready)
        ? "Fill reusable details"
        : "Fill known details";
  button.disabled =
    !profile || !info.supported || items.filter((i) => i.exact).length > 1;
  button.hidden = mode !== "import" && items.length > 0 && !info.supported;
  document.querySelector("#page-name").textContent = info.supported
    ? info.name
    : "Open the application to fill";
  document.querySelector("#scope").textContent =
    !info.supported && items.some((i) => i.exact)
      ? items.some((i) => i.exact && i.ready)
        ? "Your CV is ready. Open the employer application to fill your details."
        : "Prepare your application with the original CV saved in Radius."
      : info.scope || info.reason;
  if (info.lastReport) showReport(info.lastReport);
  if (!profile) status.textContent = "Choose Edit profile to add your details.";
  else if (catalog?.connected && !catalog.settings?.profile_id)
    status.textContent =
      "Choose your Radius profile in Connection to prepare applications.";
  else if (catalog?.lookup_error) status.textContent = catalog.lookup_error;
} catch (e) {
  document.querySelector("#page-name").textContent = "Page unavailable";
  message(e);
}
