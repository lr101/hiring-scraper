import { emptyProfile, validateProfile } from "./profile.js";
import { readPackage, connectSources, bulletPrompt } from "./sources.js";

const keys = [
  "first_name",
  "last_name",
  "email",
  "phone",
  "linkedin",
  "city",
  "country",
  "street",
  "postal_code",
  "website",
  "portfolio",
  "github",
];
const preferenceText = [
  "available_from",
  "notice_period",
  "salary_amount",
  "salary_currency",
  "salary_period",
  "weekly_hours",
  "employment_type",
  "authorization_country",
];
const preferenceBool = [
  "authorized_to_work",
  "sponsorship_required",
  "willing_to_relocate",
];
let pendingPackage = null;
let linked = validateProfile(emptyProfile());
const container = document.querySelector("#experiences");
const status = (message, error = false, target = "status") => {
  const el = document.getElementById(target);
  el.textContent = message;
  el.classList.toggle("error", error);
};

function addExperience(entry = {}) {
  if (container.children.length >= 20) {
    status("Use at most 20 work experiences.", true);
    return;
  }
  const row = document.createElement("fieldset");
  row.dataset.id = entry.id || crypto.randomUUID();
  // Only fixed markup is parsed. Applicant values are assigned through .value.
  row.innerHTML = `<legend></legend><div class="grid">
    <label>Company<input data-key="company" required maxlength="300"></label>
    <label>Job title<input data-key="title" required maxlength="300"></label>
    <label class="wide">Location<input data-key="location" maxlength="300" placeholder="City, country or Remote"></label>
    <label class="wide">Original responsibilities<textarea data-key="description" maxlength="10000"></textarea></label>
    <label>Start year or month<input data-key="start" required pattern="[1-9][0-9]{3}(-[0-9]{2})?" placeholder="YYYY or YYYY-MM"></label>
    <label>End year or month<input data-key="end" required pattern="[1-9][0-9]{3}(-[0-9]{2})?" placeholder="YYYY or YYYY-MM"></label>
    <label class="wide">CV bullet paragraph IDs (optional)<input data-key="cv_bullet_ids" placeholder="For existing CV edits, e.g. 12, 13, 14"></label></div><label class="check"><input data-key="current" type="checkbox">I currently work here</label><button class="remove" type="button">Remove experience</button>`;
  for (const key of [
    "company",
    "title",
    "location",
    "description",
    "start",
    "end",
  ])
    row.querySelector(`[data-key="${key}"]`).value = entry[key] || "";
  row.querySelector("[data-key=cv_bullet_ids]").value = (
    entry.cv_bullet_ids || []
  ).join(", ");
  const current = row.querySelector('[data-key="current"]');
  current.checked = entry.current || false;
  const toggle = () => {
    row.querySelector('[data-key="end"]').disabled = current.checked;
  };
  current.addEventListener("change", toggle);
  toggle();
  row.querySelector(".remove").onclick = () => {
    row.remove();
    renumber();
    status("Experience removed from editor. Save to keep the change.");
  };
  container.append(row);
  renumber();
}
function renumber() {
  [...container.children].forEach((row, index) => {
    row.querySelector("legend").textContent = `Experience ${index + 1}`;
    row
      .querySelector(".remove")
      .setAttribute("aria-label", `Remove experience ${index + 1}`);
  });
}
function render(profile, keepPackage = false) {
  if (!keepPackage) pendingPackage = null;
  linked = validateProfile(profile);
  keys.forEach((key) => {
    document.getElementById(key).value = linked[key] || "";
  });
  preferenceText.forEach(
    (key) => (document.getElementById(key).value = linked.preferences[key]),
  );
  preferenceBool.forEach(
    (key) =>
      (document.getElementById(key).value =
        linked.preferences[key] === null
          ? ""
          : String(linked.preferences[key])),
  );
  container.replaceChildren();
  linked.experiences.forEach(addExperience);
  document.querySelector("#cv_text").value = linked.cv_text;
  document.querySelector("#application_url").value = linked.application.url;
  document.querySelector("#application_answers").value =
    linked.application.application_answers;
  document.querySelector("#cover_letter").value =
    linked.application.cover_letter;
  document.querySelector("#source-notes").textContent = linked.source_notes;
  document.querySelector("#package-input").value = "";
  document.querySelector("#bullet-prompt").value = "";
  const preview = document.querySelector("#tailored-bullets");
  preview.replaceChildren();
  for (const group of linked.application.experience_bullets) {
    const details = document.createElement("details"),
      summary = document.createElement("summary"),
      list = document.createElement("ul");
    summary.textContent =
      "Tailored bullets: " +
      (linked.experiences.find((e) => e.id === group.experience_id)?.company ||
        group.experience_id);
    for (const bullet of group.bullets) {
      const li = document.createElement("li");
      li.textContent = bullet;
      list.append(li);
    }
    details.append(summary, list);
    preview.append(details);
  }
}
function readEditor() {
  const data = Object.fromEntries(
    keys.map((key) => [key, document.getElementById(key).value]),
  );
  data.experiences = [...container.children].map((row) => {
    const entry = Object.fromEntries(
      ["company", "title", "location", "description", "start", "end"].map(
        (key) => [key, row.querySelector(`[data-key="${key}"]`).value],
      ),
    );
    entry.id = row.dataset.id;
    const ids = row.querySelector("[data-key=cv_bullet_ids]").value.trim();
    if (ids && !/^\d+(?:\s*,\s*\d+)*$/.test(ids))
      throw new Error("Use comma-separated numeric CV bullet paragraph IDs.");
    entry.cv_bullet_ids = ids ? ids.split(",").map(Number) : [];
    entry.current = row.querySelector('[data-key="current"]').checked;
    if (entry.current) entry.end = null;
    return entry;
  });
  data.preferences = Object.fromEntries(
    preferenceText.map((key) => [key, document.getElementById(key).value]),
  );
  preferenceBool.forEach((key) => {
    const value = document.getElementById(key).value;
    data.preferences[key] = value === "" ? null : value === "true";
  });
  const next = validateProfile({
    ...linked,
    ...data,
    cv_text: document.querySelector("#cv_text").value,
    application: {
      ...linked.application,
      url: document.querySelector("#application_url").value,
      application_answers: document.querySelector("#application_answers").value,
      cover_letter: document.querySelector("#cover_letter").value,
    },
  });
  if (
    linked.application.application_answers !==
    next.application.application_answers
  )
    delete next.application.form_answers;
  const facts = (profile) =>
    JSON.stringify([
      ...keys.map((key) => profile[key]),
      profile.preferences,
      profile.experiences,
      profile.cv_text,
    ]);
  if (
    (linked.application.url &&
      linked.application.url !== next.application.url) ||
    facts(linked) !== facts(next)
  ) {
    next.application = {
      ...next.application,
      cover_letter: "",
      application_answers: "",
      experience_bullets: [],
      form_answers: undefined,
      job_id: null,
      profile_id: null,
      cv_updated_at: "",
    };
    next.source_notes =
      "Profile facts or the application link changed. Previous ChatGPT text was cleared; connect a fresh package for this profile and application.";
  }
  return next;
}
async function connectedEditor() {
  const data = readEditor();
  const input = document.querySelector("#package-input").value.trim();
  if (input) {
    pendingPackage = await readPackage(input);
    return connectSources(
      data,
      data.cv_text,
      pendingPackage,
      data.application.url,
    );
  }
  if (data.application.job_id === null) pendingPackage = null;
  return data;
}
document.querySelector("#prepare-bullets").onclick = () => {
  try {
    document.querySelector("#bullet-prompt").value = bulletPrompt(readEditor());
    status(
      "Request prepared. Copy it into ChatGPT with your application-preparation prompt.",
    );
  } catch (error) {
    status(error.message, true);
  }
};
document.querySelector("#connect").onclick = async () => {
  try {
    const data = await connectedEditor();
    render(data, true);
    status(
      "CV facts and ChatGPT text connected in the editor. Save profile to use them.",
    );
  } catch (error) {
    status(error.message, true);
  }
};

document.querySelector("#profile-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const applicant = await connectedEditor(),
      pack = pendingPackage;
    const state = await chrome.runtime.sendMessage({ type: "overview" });
    // Connected mode keeps application documents only in the validated per-job
    // vault. Reusable facts are saved even if an import fails; stale documents
    // never enter the standalone profile as a side effect of a failed import.
    const saved = state?.result?.connected
      ? validateProfile({ ...applicant, application: {} })
      : applicant;
    await chrome.storage.local.set({ applicant: saved });
    render(saved);
    status("Profile saved in this browser.");
    if (pack && state?.result?.connected) {
      const response = await chrome.runtime.sendMessage({
        type: "import-package",
        input: JSON.stringify(pack),
        url: applicant.application.url,
      });
      if (!response?.ok) {
        document.querySelector("#radius-status").textContent = response.error;
        document.querySelector("#radius-status").classList.add("error");
        status(
          "Profile saved. Application CV could not be connected: " +
            response.error,
          true,
        );
      }
    }
  } catch (error) {
    status(error.message, true);
  }
};
document.querySelector("#add").onclick = () => {
  addExperience();
  container.lastElementChild?.querySelector("input").focus();
};
document.querySelector("#example").onclick = async () => {
  try {
    const data = await (
      await fetch(chrome.runtime.getURL("example.json"))
    ).json();
    render(validateProfile(data));
    status("Fictional example loaded. Save profile to use it.");
  } catch {
    status("Could not load the example profile.", true);
  }
};
document.querySelector("#structured-example").onclick = async () => {
  try {
    render(
      validateProfile(
        await (
          await fetch(chrome.runtime.getURL("structured-example.json"))
        ).json(),
      ),
    );
    status(
      "Fictional jobs, locations and common answers loaded. Save profile to use them.",
    );
  } catch {
    status("Could not load the structured example.", true);
  }
};
document.querySelector("#dummy-return").onclick = async () => {
  try {
    const cv = await (
      await fetch(chrome.runtime.getURL("dummy-cv.json"))
    ).json();
    const link = await (
      await fetch(chrome.runtime.getURL("dummy-return-link.txt"))
    ).text();
    render(validateProfile(cv));
    document.querySelector("#package-input").value = link.trim();
    status(
      "Paired fictional CV and compressed return link loaded. Enter a test application URL, then Connect sources. Saved data has not changed.",
    );
  } catch {
    status("Could not load the paired fictional example.", true);
  }
};
document.querySelector("#import").onclick = () =>
  document.querySelector("#import-file").click();
document.querySelector("#import-file").onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 300000)
      throw new Error("Choose a JSON profile smaller than 300 KB.");
    render(validateProfile(JSON.parse(await file.text())));
    status(
      "Profile imported into the editor. Review it, then Save profile.",
      false,
      "transfer-status",
    );
  } catch (error) {
    status(
      error instanceof SyntaxError
        ? "This file is not valid JSON."
        : error.message,
      true,
      "transfer-status",
    );
  }
  event.target.value = "";
};
document.querySelector("#export").onclick = async () => {
  try {
    const { applicant } = await chrome.storage.local.get("applicant");
    if (!applicant) throw new Error("Save a profile before exporting.");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(validateProfile(applicant), null, 2) + "\n"], {
        type: "application/json",
      }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "applicant.private.json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status(
      "Saved profile exported. This file contains your applicant details.",
      false,
      "transfer-status",
    );
  } catch (error) {
    status(error.message, true, "transfer-status");
  }
};
document.querySelector("#clear").onclick = async () => {
  if (!confirm("Delete your saved applicant profile and clear this editor?"))
    return;
  await chrome.storage.local.remove("applicant");
  const { radiusVault } = await chrome.storage.local.get("radiusVault");
  if (radiusVault)
    await chrome.storage.local.set({
      radiusVault: { ...radiusVault, packages: {} },
    });
  await chrome.storage.session.clear();
  render(emptyProfile());
  status("Saved profile deleted.");
};
try {
  const { applicant } = await chrome.storage.local.get("applicant");
  render(applicant ? validateProfile(applicant) : emptyProfile());
} catch {
  status(
    "The saved profile is invalid. Import a valid profile or delete it.",
    true,
  );
}
