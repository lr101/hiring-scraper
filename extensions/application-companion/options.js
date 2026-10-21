import { localizedPreferenceKeys } from "./profile-language.js";
import { parseTexCV } from "./tex-cv.js";
import { cvFormattingPrompt, cvFormattingPromptDe } from "./tex-cv-prompt.js";
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
  "willing_to_work_onsite",
];
let pendingPackage = null;
let linked = validateProfile(emptyProfile());
let contentLanguage = 'en';
const localizedFields = [
  ['summary', 'Profile summary', 'Profilzusammenfassung', 5000],
  ['custom_instructions', 'Custom application instructions', 'Eigene Bewerbungsanweisungen', 5000],
  ['skills', 'Skills (comma separated)', 'Fähigkeiten (kommagetrennt)', 8000],
  ['desired_roles', 'Desired roles (comma separated)', 'Gewünschte Positionen (kommagetrennt)', 1600],
  ['secondary_roles', 'Adjacent roles (comma separated)', 'Weitere Positionen (kommagetrennt)', 1600],
  ['certifications', 'Certifications (comma separated)', 'Zertifikate (kommagetrennt)', 4000],
  ['career_evidence', 'Career evidence bank', 'Berufliche Beispiele und Belege', 12000],
  ['writing_samples', 'Writing samples', 'Eigene Textbeispiele', 12000],
  ['wording_to_avoid', 'Wording to avoid', 'Zu vermeidende Formulierungen', 3000],
  ['career_goals', 'Career goals', 'Berufliche Ziele', 5000],
  ['work_preferences', 'Working preferences and dealbreakers', 'Arbeitswünsche und Ausschlusskriterien', 5000],
];
const listKeys = ['skills', 'desired_roles', 'secondary_roles', 'certifications'];
const personalKeys = ['career_evidence', 'writing_samples', 'wording_to_avoid', 'career_goals', 'work_preferences'];
for (const [key, label, , limit] of localizedFields) {
  const wrapper = document.createElement('label');
  const caption = document.createElement('span'); caption.id = `caption-${key}`; caption.textContent = label;
  const input = document.createElement('textarea'); input.id = `localized-${key}`; input.maxLength = limit; input.rows = 3;
  wrapper.append(caption, input); document.querySelector('#localized-fields').append(wrapper);
}
for (const key of localizedPreferenceKeys) document.querySelector('#localized-preferences').append(document.getElementById(key).closest('label'));
for (const id of ['cv-file-en', 'cv_text', 'cv-file-de', 'cv-de']) document.querySelector('#localized-cv').append(document.getElementById(id).closest('label'));
document.querySelector('#localized-cv').append(document.querySelector('#cv-format-prompt').closest('details'));
function captureLanguage() {
  const content = { ...linked.localized_content?.[contentLanguage] };
  content.application_personalization = { ...content.application_personalization };
  for (const [key] of localizedFields) {
    const value = document.getElementById(`localized-${key}`).value;
    if (personalKeys.includes(key)) content.application_personalization[key] = value;
    else content[key] = listKeys.includes(key) ? value.split(',').map(item => item.trim()).filter(Boolean) : value;
  }
  content.application_preferences = Object.fromEntries(localizedPreferenceKeys.map(key => [key, document.getElementById(key).value]));
  linked.localized_content = { ...linked.localized_content, [contentLanguage]: content };
}
function renderLanguage() {
  const content = linked.localized_content?.[contentLanguage] ?? {};
  for (const [key, english, german] of localizedFields) {
    document.getElementById(`caption-${key}`).textContent = contentLanguage === 'de' ? german : english;
    const value = personalKeys.includes(key) ? content.application_personalization?.[key] : content[key];
    document.getElementById(`localized-${key}`).value = Array.isArray(value) ? value.join(', ') : value ?? '';
  }
  for (const key of localizedPreferenceKeys) document.getElementById(key).value = content.application_preferences?.[key] ?? (contentLanguage === 'en' ? linked.preferences?.[key] : '') ?? '';
  for (const language of ['en', 'de']) {
    document.getElementById(`content-tab-${language}`).setAttribute('aria-selected', String(language === contentLanguage));
    for (const id of language === 'en' ? ['cv-file-en', 'cv_text'] : ['cv-file-de', 'cv-de']) document.getElementById(id).closest('label').hidden = language !== contentLanguage;
  }
  document.getElementById('content-panel').setAttribute('aria-labelledby', `content-tab-${contentLanguage}`);
  document.querySelector('#cv-format-prompt').value = contentLanguage === 'de' ? cvFormattingPromptDe : cvFormattingPrompt;
}
for (const language of ['en', 'de']) document.getElementById(`content-tab-${language}`).onclick = () => {
  captureLanguage(); contentLanguage = language; renderLanguage();
};
document.querySelector("#cv-format-prompt").value = cvFormattingPrompt;
const status = (message, error = false, target = "status") => {
  const el = document.getElementById(target);
  el.textContent = message;
  el.classList.toggle("error", error);
};

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

  renderLanguage();
  document.querySelector("#cv_text").value =
    linked.base_cvs.en ||
    (Object.keys(linked.base_cvs).length ||
    !linked.cv_text.includes("% radius:entry ")
      ? ""
      : linked.cv_text);
  document.querySelector("#cv-de").value = linked.base_cvs.de || "";
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
  captureLanguage();
  const data = Object.fromEntries(
    keys.map((key) => [key, document.getElementById(key).value]),
  );
  const english = document.querySelector("#cv_text").value.trim();
  const german = document.querySelector("#cv-de").value.trim();
  data.base_cvs = {
    ...(english ? { en: english } : {}),
    ...(german ? { de: german } : {}),
  };
  data.experiences = linked.experiences;
  data.preferences = Object.fromEntries(
    preferenceText.map((key) => [key, document.getElementById(key).value]),
  );
  preferenceBool.forEach((key) => {
    const value = document.getElementById(key).value;
    data.preferences[key] = value === "" ? null : value === "true";
  });
  for (const key of localizedPreferenceKeys) data.preferences[key] = linked.localized_content?.en?.application_preferences?.[key] ?? linked.preferences[key] ?? '';
  const next = validateProfile({
    ...linked,
    ...data,
    cv_text: english || german || linked.cv_text,
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
      profile.base_cvs,
      profile.localized_content,
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
    let saved = applicant;
    if (state?.result?.connected) {
      const response = await chrome.runtime.sendMessage({
        type: "save-connected-applicant",
        applicant,
      });
      if (!response?.ok)
        throw new Error(
          response?.error || "Could not save the connected profile.",
        );
      saved = response.result.applicant;
      render(saved);
      status("Profile saved and synced with Radius.");
    } else {
      await chrome.storage.local.set({ applicant: saved });
      render(saved);
      status("Profile saved in this browser.");
    }
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

for (const [language, target] of [
  ["en", "#cv_text"],
  ["de", "#cv-de"],
]) {
  document.querySelector(`#cv-file-${language}`).onchange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      if (!/\.tex$/i.test(file.name) || file.size > 100000)
        throw new Error("Choose a .tex CV under 100,000 bytes.");
      const source = await file.text();
      parseTexCV(source);
      document.querySelector(target).value = source;
      status("LaTeX source loaded. Save profile to use it.");
    } catch (error) {
      status(error.message, true);
    }
    event.target.value = "";
  };
}

document.querySelector("#remove-base-cvs").onclick = async () => {
  if (!confirm("Remove both saved English and German base CVs?")) return;
  try {
    const state = await chrome.runtime.sendMessage({ type: "overview" });
    if (state?.result?.connected) {
      const response = await chrome.runtime.sendMessage({
        type: "remove-connected-cvs",
      });
      if (!response?.ok)
        throw new Error(response?.error || "Could not remove the base CVs.");
      render(response.result.applicant);
    } else {
      const next = validateProfile({
        ...linked,
        base_cvs: {},
        cv_text: "",
        experiences: [],
        education: [],
        application: {},
      });
      await chrome.storage.local.set({ applicant: next });
      render(next);
    }
    status("Base CVs removed. Other saved profile details were preserved.");
  } catch (error) {
    status(error.message, true);
  }
};
