import { validateProfile } from "./profile.js";
const button = document.querySelector("#fill"),
  status = document.querySelector("#status");
let tabId,
  mode = "fill",
  selectedJob = null,
  info;
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
  tabId = tab?.id;
  info = await request("inspect");
  mode = info.mode || "fill";
  const catalog = info.catalog;
  document.querySelector("#connection").textContent = catalog?.connected
    ? "Connection"
    : "Connect Radius";
  document.querySelector("#not-connected").hidden = !!catalog?.connected;
  document.querySelector("#job-search").hidden = !catalog?.connected;
  if (catalog?.settings?.profile_name && profile)
    document.querySelector("#experience-count").textContent =
      `${profile.experiences.length} work experiences · ${catalog.settings.profile_name}`;
  let searchTimer;
  document.querySelector("#job-query").oninput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      try {
        const results = await request("search-jobs", {
          query: document.querySelector("#job-query").value,
        });
        document
          .querySelector("#job-search-results")
          .replaceChildren(...results.map((item) => entry(item)));
      } catch (error) {
        message(error);
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
} catch (e) {
  document.querySelector("#page-name").textContent = "Page unavailable";
  message(e);
}
