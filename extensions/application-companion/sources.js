import { parseTexCV, validateTailoredCV } from "./tex-cv.js";
import {
  validateProfile,
  validateBullets,
  validateFormAnswers,
} from "./profile.js";

// Radius return links are decoded locally. Never navigate or fetch the link.
export async function readPackage(input) {
  let text = input.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/, "$1");
  if (text.length > 300000) throw new Error("Use a package under 300 KB.");
  if (/^https?:\/\//.test(text) || text.startsWith("#application")) {
    const hash = text.startsWith("#") ? text : new URL(text).hash;
    if (hash.startsWith("#applicationz=")) {
      const token = hash.slice(14);
      if (!/^[A-Za-z0-9_-]+$/.test(token) || token.length % 4 === 1)
        throw new Error("Invalid return-link encoding.");
      const bytes = Uint8Array.from(
        atob(token.replace(/-/g, "+").replace(/_/g, "/")),
        (c) => c.charCodeAt(0),
      );
      const reader = new Blob([bytes])
        .stream()
        .pipeThrough(new DecompressionStream("deflate"))
        .getReader();
      const chunks = [];
      let size = 0;
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 200000)
            throw new Error("The decompressed package exceeds 200 KB.");
          chunks.push(value);
        }
      } finally {
        void reader.cancel().catch(() => {});
      }
      const result = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        result.set(chunk, offset);
        offset += chunk.byteLength;
      }
      text = new TextDecoder("utf-8", { fatal: true }).decode(result);
    } else if (hash.startsWith("#application="))
      text = decodeURIComponent(hash.slice(13));
    else
      throw new Error(
        "The link does not contain a Radius application package.",
      );
  }
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("Paste the full Radius JSON package or return link.");
  }
  if (
    data?.format !== "radius-application-v1" ||
    !Number.isInteger(data.job_id) ||
    !Number.isInteger(data.profile_id) ||
    typeof data.cv_updated_at !== "string"
  )
    throw new Error(
      "Expected a radius-application-v1 package with job, profile and CV version.",
    );
  for (const key of ["cover_letter", "application_answers"]) {
    if (typeof data[key] !== "string" || data[key].length > 100000)
      throw new Error(`Invalid ${key} in package.`);
  }
  if (typeof data.cv !== "string" && !Array.isArray(data.cv))
    throw new Error("The package must contain CV text or template edits.");
  if (Array.isArray(data.cv)) {
    const ids = new Set();
    if (data.cv.length > 1000) throw new Error("Too many CV paragraph edits.");
    for (const row of data.cv) {
      if (
        !row ||
        !Number.isInteger(row.id) ||
        ids.has(row.id) ||
        typeof row.text !== "string" ||
        row.text.length > 5000
      )
        throw new Error("Invalid or duplicate CV paragraph edits.");
      ids.add(row.id);
    }
  }
  validateBullets(data.experience_bullets ?? []);
  if (data.form_answers !== undefined) validateFormAnswers(data.form_answers);
  return data;
}

export function connectSources(
  base,
  cvText,
  applicationPackage,
  applicationURL,
) {
  const next = validateProfile(base);
  if (cvText.length > 100000)
    throw new Error("Use CV text under 100,000 characters.");
  if (applicationPackage && cvText.includes("% radius:entry ")) {
    const history = validateTailoredCV(
      cvText,
      applicationPackage.latex_documents?.cv,
    );
    next.experiences = history.experiences;
    next.education = history.education;
    cvText = applicationPackage.latex_documents.cv;
  }
  next.cv_text = cvText.trim();
  if (next.cv_text.includes("\\begin{document}")) {
    const history = parseTexCV(next.cv_text);
    next.experiences = history.experiences;
    next.education = history.education;
  }
  const email = cvText.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i)?.[0];
  const notes = [];
  if (email && !next.email) next.email = email;
  else if (email && next.email.toLowerCase() !== email.toLowerCase())
    notes.push(
      "The CV email differs from the editor; the editor value was preserved.",
    );
  // Never infer employers, names, exact months, work permission or salary from prose.
  if (applicationPackage) {
    const candidateText = [
      applicationPackage.cover_letter,
      applicationPackage.application_answers,
      JSON.stringify(applicationPackage.experience_bullets || []),
      ...(applicationPackage.form_answers?.answers || []).map(
        (answer) => answer.value,
      ),
      typeof applicationPackage.cv === "string"
        ? applicationPackage.cv
        : applicationPackage.cv.map((row) => row.text).join("\n"),
    ].join("\n");
    const returnedEmails =
      candidateText.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || [];
    if (
      next.email &&
      returnedEmails.some(
        (email) => email.toLowerCase() !== next.email.toLowerCase(),
      )
    )
      throw new Error(
        "The returned package contains a different applicant email or contact address. Load the matching CV profile or check the returned contact information before connecting.",
      );
    if (
      next.application.job_id !== null &&
      next.application.job_id !== applicationPackage.job_id &&
      applicationURL.trim() === next.application.url
    ) {
      applicationURL = "";
      notes.push("The previous job link was cleared for this new package.");
    }
    const bulletRows = validateBullets(
      applicationPackage.experience_bullets ?? [],
    );
    const jobs = new Map(next.experiences.map((e) => [e.id, e]));
    for (const row of bulletRows)
      if (!jobs.has(row.experience_id))
        throw new Error(
          `Unknown experience ID: ${row.experience_id}. Connect the matching structured profile.`,
        );
    const paragraphs = new Map(
      Array.isArray(applicationPackage.cv)
        ? applicationPackage.cv.map((row) => [row.id, row.text])
        : [],
    );
    for (const entry of next.experiences) {
      if (
        !entry.cv_bullet_ids.length ||
        bulletRows.some((row) => row.experience_id === entry.id)
      )
        continue;
      const missing = entry.cv_bullet_ids.filter((id) => !paragraphs.has(id));
      if (missing.length)
        throw new Error(
          `Missing CV bullet paragraph IDs for ${entry.company}: ${missing.join(", ")}. Request these bullets or update their mapping.`,
        );
      bulletRows.push({
        experience_id: entry.id,
        bullets: entry.cv_bullet_ids.map((id) => paragraphs.get(id)),
      });
    }
    next.application = {
      url: applicationURL.trim(),
      cover_letter: applicationPackage.cover_letter,
      application_answers: applicationPackage.application_answers,
      job_id: applicationPackage.job_id,
      profile_id: applicationPackage.profile_id,
      cv_updated_at: applicationPackage.cv_updated_at,
      experience_bullets: bulletRows,
      ...(applicationPackage.form_answers === undefined
        ? {}
        : {
            form_answers: validateFormAnswers(applicationPackage.form_answers),
          }),
    };
    notes.push(
      `Connected ChatGPT package for Radius job ${applicationPackage.job_id}, profile ${applicationPackage.profile_id}.`,
    );
    if (Array.isArray(applicationPackage.cv))
      notes.push(
        "CV paragraph edits were retained in the Radius workflow; the original CV facts remain authoritative here.",
      );
    if (!applicationURL.trim())
      notes.push(
        "Set the employer application URL to enable job-specific answers and cover-letter filling.",
      );
  }
  next.source_notes = notes.join("\n");
  return validateProfile(next);
}

export function bulletPrompt(profile) {
  const data = validateProfile(profile);
  return (
    'Tailor ONLY the work-experience bullet points for this application. Keep every experience_id exactly as supplied. Do not change company, title, location or dates and do not invent achievements. Return the usual radius-application-v1 package (preserve the job_id, profile_id and cv_updated_at from the application-preparation prompt) with this optional field: "experience_bullets": [{"experience_id":"...","bullets":["..."]}]. Return plain bullet text without bullet markers. Each job below has original_description as source material. If there is no evidence for an achievement, omit it.\n\nStructured experience facts:\n' +
    JSON.stringify(
      data.experiences.map((e) => ({
        experience_id: e.id,
        company: e.company,
        title: e.title,
        location: e.location,
        start: e.start,
        end: e.end,
        current: e.current,
        original_description: e.description,
      })),
      null,
      2,
    )
  );
}
