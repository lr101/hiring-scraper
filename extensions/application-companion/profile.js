import { validateLocalizedContent } from "./profile-language.js";
import { parseTexCV, validateTailoredCV } from "./tex-cv.js";
// Shared by the editor and service worker. No inferred dates or answers.
const contacts = [
  "salutation",
  "birth_name",
  "birth_date",
  "birth_place",
  "nationality",
  "driving_licence",
  "additional_information",
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
export const emptyProfile = () => ({
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
  linkedin: "",
  experiences: [],
});

export function validateProfile(input) {
  const fail = (message) => {
    throw new Error(message);
  };
  if (!input || typeof input !== "object" || Array.isArray(input))
    fail("Import a profile object.");
  const allowed = new Set([
    ...contacts,
    "experiences",
    "cv_text",
    "base_cvs",
    "localized_content",
    "education",
    "application",
    "source_notes",
    "preferences",
  ]);
  if (Object.keys(input).some((key) => !allowed.has(key)))
    fail("The profile contains unsupported fields. Use the example format.");
  const text = (value, label, limit = 300) => {
    if (value === undefined || value === null) return "";
    if (typeof value !== "string" || value.length > limit)
      fail(`${label}: enter text up to ${limit} characters.`);
    return value.trim();
  };
  const profile = Object.fromEntries(
    contacts.map((key) => [
      key,
      text(input[key], key, key === "additional_information" ? 10000 : 300),
    ]),
  );
  if (
    profile.birth_date &&
    (!/^[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}$/.test(profile.birth_date) ||
      !Number.isFinite(Date.parse(profile.birth_date)) ||
      new Date(profile.birth_date).toISOString().slice(0, 10) !==
        profile.birth_date)
  )
    fail(
      "birth_date: provide a real complete date (YYYY-MM-DD), or leave it blank.",
    );
  if (profile.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(profile.email))
    fail("Enter a valid email address or leave it blank.");
  for (const name of ["linkedin", "website", "portfolio", "github"])
    if (profile[name]) {
      let url;
      try {
        url = new URL(profile[name]);
      } catch {
        fail("Enter a complete HTTPS website URL.");
      }
      if (url.protocol !== "https:" || url.username || url.password)
        fail("Enter a complete HTTPS website URL.");
    }
  if (input.localized_content !== undefined)
    profile.localized_content = validateLocalizedContent(
      input.localized_content,
    );
  profile.base_cvs = {};
  for (const [language, source] of Object.entries(input.base_cvs ?? {})) {
    if (!["en", "de"].includes(language))
      fail("Use English and German base CVs.");
    parseTexCV(source);
    profile.base_cvs[language] = source;
  }
  if (profile.base_cvs.en && profile.base_cvs.de)
    validateTailoredCV(profile.base_cvs.en, profile.base_cvs.de);
  const history = input.cv_text?.includes("\\begin{document}")
    ? parseTexCV(input.cv_text)
    : null;
  profile.education = history?.education ?? [];
  const entries = history?.experiences ?? input.experiences ?? [];
  if (!Array.isArray(entries) || entries.length > 20)
    fail("Use at most 20 work experiences.");
  const date = (value, label) => {
    if (
      typeof value !== "string" ||
      !/^[1-9][0-9]{3}(?:-(0[1-9]|1[0-2]))?$/.test(value)
    )
      fail(`${label}: provide a known year or month (YYYY or YYYY-MM).`);
    return value;
  };
  profile.experiences = entries.map((entry, index) => {
    const label = `Experience ${index + 1}`;
    if (!entry || typeof entry !== "object" || Array.isArray(entry))
      fail(`${label}: invalid entry.`);
    if (
      Object.keys(entry).some(
        (key) =>
          ![
            "id",
            "company",
            "title",
            "location",
            "description",
            "start",
            "end",
            "current",
            "cv_bullet_ids",
          ].includes(key),
      )
    )
      fail(`${label}: unsupported fields.`);
    const company = text(entry.company, label + " company");
    const title = text(entry.title, label + " title");
    if (!company || !title)
      fail(`${label}: company and job title are required.`);
    if (entry.current !== undefined && typeof entry.current !== "boolean")
      fail(`${label}: current must be true or false.`);
    const current = entry.current ?? false;
    const start = date(entry.start, label + " start");
    if (current && entry.end)
      fail(`${label}: a current role must not have an end date.`);
    const end = current ? null : date(entry.end, label + " end");
    if (
      end &&
      (end.slice(0, 4) < start.slice(0, 4) ||
        (end.length === 7 && start.length === 7 && end < start))
    )
      fail(`${label}: the end precedes the start.`);
    const id = text(entry.id ?? `job-${index + 1}`, label + " ID", 100);
    if (!/^[a-zA-Z0-9_-]+$/.test(id))
      fail(
        `${label}: use letters, numbers, hyphens or underscores for the ID.`,
      );
    const bulletIDs = entry.cv_bullet_ids ?? [];
    if (
      !Array.isArray(bulletIDs) ||
      bulletIDs.length > 50 ||
      bulletIDs.some((id) => !Number.isInteger(id) || id < 0) ||
      new Set(bulletIDs).size !== bulletIDs.length
    )
      fail(`${label}: use unique numeric CV bullet paragraph IDs.`);
    return {
      id,
      location: text(entry.location, label + " location"),
      cv_bullet_ids: bulletIDs,
      company,
      title,
      description: text(entry.description, label + " description", 10000),
      start,
      end,
      current,
    };
  });
  if (
    new Set(profile.experiences.map((e) => e.id)).size !==
    profile.experiences.length
  )
    fail("Use unique work experience IDs.");
  const bulletIDs = profile.experiences.flatMap((e) => e.cv_bullet_ids);
  if (new Set(bulletIDs).size !== bulletIDs.length)
    fail("Assign each CV bullet paragraph to only one job.");
  const prefs = input.preferences ?? {};
  if (!prefs || typeof prefs !== "object" || Array.isArray(prefs))
    fail("Invalid common application answers.");
  const preferenceText = [
    "available_from",
    "notice_period",
    "salary_amount",
    "salary_currency",
    "salary_period",
    "authorization_country",
    "employment_type",
    "weekly_hours",
  ];
  const preferenceBool = [
    "authorized_to_work",
    "sponsorship_required",
    "willing_to_relocate",
    "willing_to_work_onsite",
  ];
  if (
    Object.keys(prefs).some(
      (key) => ![...preferenceText, ...preferenceBool].includes(key),
    )
  )
    fail("Unsupported common application answer.");
  profile.preferences = Object.fromEntries(
    preferenceText.map((key) => [key, text(prefs[key], key)]),
  );
  for (const key of preferenceBool) {
    if (
      prefs[key] !== undefined &&
      prefs[key] !== null &&
      typeof prefs[key] !== "boolean"
    )
      fail(`${key}: choose Yes, No or leave unknown.`);
    profile.preferences[key] = prefs[key] ?? null;
  }
  const pref = profile.preferences;
  if (
    pref.available_from &&
    (!/^\d{4}-\d{2}-\d{2}$/.test(pref.available_from) ||
      !Number.isFinite(Date.parse(pref.available_from)) ||
      new Date(pref.available_from).toISOString().slice(0, 10) !==
        pref.available_from)
  )
    fail("Availability: enter a valid calendar date.");
  if (pref.salary_amount && !/^\d+(?:\.\d{1,2})?$/.test(pref.salary_amount))
    fail("Salary: enter a numeric amount without currency or separators.");
  if (pref.salary_currency && !/^[A-Z]{3}$/.test(pref.salary_currency))
    fail("Salary currency: use a three-letter code such as EUR.");
  if (
    pref.salary_period &&
    !["year", "month", "hour"].includes(pref.salary_period)
  )
    fail("Salary period: choose year, month or hour.");
  if (
    pref.weekly_hours &&
    (!/^\d+(?:\.\d)?$/.test(pref.weekly_hours) ||
      Number(pref.weekly_hours) > 168)
  )
    fail("Weekly hours: use a number between 0 and 168.");
  const identities = profile.experiences.map((e) =>
    JSON.stringify([e.company.toLowerCase(), e.title.toLowerCase(), e.start]),
  );
  if (new Set(identities).size !== identities.length)
    fail("Remove duplicate work experiences.");
  profile.cv_text = text(input.cv_text, "Original CV text", 100000);
  profile.source_notes = text(input.source_notes, "Source notes", 5000);
  const application = input.application || {};
  if (typeof application !== "object" || Array.isArray(application))
    fail("Invalid application context.");
  profile.application = {
    url: text(application.url, "Application URL", 2000),
    cover_letter: text(application.cover_letter, "Cover letter", 100000),
    application_answers: text(
      application.application_answers,
      "Application answers",
      100000,
    ),
    job_id: application.job_id ?? null,
    profile_id: application.profile_id ?? null,
    cv_updated_at: text(application.cv_updated_at, "CV version", 100),
    experience_bullets: validateBullets(application.experience_bullets ?? []),
    ...(application.form_answers === undefined
      ? {}
      : { form_answers: validateFormAnswers(application.form_answers) }),
  };
  if (profile.application.url) {
    let url;
    try {
      url = new URL(profile.application.url);
    } catch {
      fail("Use a complete application URL.");
    }
    if (
      !["https:", "http:"].includes(url.protocol) ||
      url.username ||
      url.password
    )
      fail("Use an HTTP(S) application URL without credentials.");
  }
  return profile;
}

export function validateFormAnswers(input) {
  if (
    !input ||
    !/^[a-f0-9]{64}$/.test(input.form_id) ||
    !Array.isArray(input.answers) ||
    input.answers.length > 100
  )
    throw new Error("Invalid prepared form answers.");
  const ids = new Set();
  for (const answer of input.answers) {
    if (
      !answer ||
      !/^q\d{1,2}$/.test(answer.id) ||
      ids.has(answer.id) ||
      typeof answer.value !== "string" ||
      !answer.value.trim() ||
      answer.value.length > 10000
    )
      throw new Error("Invalid or duplicate prepared question answer.");
    ids.add(answer.id);
    if (
      /[\u0000-\u0008\u000B\u000C\u000E-\u001F]|\[(?:CONFIRM\b|TODO\b|TBD\b|INSERT\b|YOUR\b)[^\]]*\]|(?:^|\n)\s*(?:TODO|TBD)\s*[:—-]|(?:please|can you) confirm (?:your|the following)|awaiting your (?:confirmation|approval)/i.test(
        answer.value,
      )
    )
      throw new Error(
        "Prepared question answers contain unfinished or invalid text.",
      );
  }
  return {
    form_id: input.form_id,
    answers: input.answers.map((answer) => ({
      id: answer.id,
      value: answer.value,
    })),
  };
}

// Shared validation for the optional ChatGPT extension to radius-application-v1.
export function validateBullets(rows) {
  if (!Array.isArray(rows) || rows.length > 20)
    throw new Error("Use at most 20 tailored experience bullet groups.");
  const ids = new Set();
  return rows.map((row) => {
    if (
      !row ||
      typeof row.experience_id !== "string" ||
      !/^[a-zA-Z0-9_-]{1,100}$/.test(row.experience_id) ||
      ids.has(row.experience_id)
    )
      throw new Error("Invalid or duplicate experience bullet ID.");
    ids.add(row.experience_id);
    if (
      !Array.isArray(row.bullets) ||
      !row.bullets.length ||
      row.bullets.length > 50 ||
      row.bullets.some(
        (b) => typeof b !== "string" || !b.trim() || b.length > 5000,
      ) ||
      row.bullets.join("\n").length > 10000
    )
      throw new Error(
        "Use nonempty experience bullets totaling at most 10,000 characters per job.",
      );
    return {
      experience_id: row.experience_id,
      bullets: row.bullets.map((b) => b.trim()),
    };
  });
}
