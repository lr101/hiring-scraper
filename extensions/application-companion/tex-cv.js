import { profileForLanguage } from "./profile-language.js";
// Explicit, non-printing markers are the only source of career facts.
export function parseTexCV(source) {
  if (
    typeof source !== "string" ||
    source.length > 100000 ||
    !source.includes("\\begin{document}") ||
    !source.includes("\\end{document}")
  )
    throw new Error("Use a complete LaTeX CV under 100,000 characters.");
  const experiences = [],
    education = [],
    ids = new Set();
  let entry = null,
    description = null;
  const fail = () => {
    throw new Error(
      "Invalid CV history markers. Use the CV formatting prompt; check IDs, dates and entry boundaries.",
    );
  };
  for (const line of source.split(/\r?\n/)) {
    const marker = line.match(/^\s*% radius:(.*)$/);
    if (marker) {
      if (marker[1].startsWith("entry ")) {
        if (entry) fail();
        try {
          entry = JSON.parse(marker[1].slice(6));
        } catch {
          fail();
        }
        const keys = [
          "id",
          "kind",
          "company",
          "title",
          "location",
          "start",
          "end",
          "current",
        ];
        if (
          !entry ||
          Object.keys(entry).some((key) => !keys.includes(key)) ||
          !/^[a-zA-Z0-9_-]{1,100}$/.test(entry.id) ||
          ids.has(entry.id) ||
          !["work", "study"].includes(entry.kind)
        )
          fail();
        for (const key of ["company", "title", "location"])
          if (
            typeof entry[key] !== "string" ||
            entry[key].length > 300 ||
            (key !== "location" && !entry[key].trim())
          )
            fail();
        const date = (value) =>
          typeof value === "string" &&
          /^[1-9][0-9]{3}(?:-(?:0[1-9]|1[0-2]))?$/.test(value);
        if (
          !date(entry.start) ||
          typeof entry.current !== "boolean" ||
          (entry.current ? entry.end !== null : !date(entry.end)) ||
          (entry.end &&
            (entry.end.slice(0, 4) < entry.start.slice(0, 4) ||
              (entry.end.length === 7 &&
                entry.start.length === 7 &&
                entry.end < entry.start)))
        )
          fail();
        ids.add(entry.id);
        description = null;
      } else if (marker[1] === "responsibilities") {
        if (!entry || description !== null) fail();
        description = [];
      } else if (marker[1] === "end") {
        if (!entry || description === null) fail();
        const text = texText(description.join("\n"));
        if (text.length > 10000) fail();
        const { kind, ...facts } = entry;
        (kind === "work" ? experiences : education).push({
          ...facts,
          description: text,
          cv_bullet_ids: [],
        });
        entry = null;
        description = null;
      } else fail();
    } else if (description !== null) description.push(line);
  }
  if (entry || !ids.size || experiences.length > 20 || education.length > 20)
    fail();
  return { experiences, education };
}

function texText(value) {
  // Do not expand commands or read referenced files. Known inert formatting only.
  const escapes = [];
  value = value
    .replace(
      /\\([&%$#_{}])|\\(textbackslash|textasciitilde|textasciicircum)\{\}/g,
      (_, char, name) => {
        escapes.push(
          char ||
            { textbackslash: "\\", textasciitilde: "~", textasciicircum: "^" }[
              name
            ],
        );
        return `\u0000${escapes.length - 1}\u0000`;
      },
    )
    .replace(/%[^\n]*/g, "")
    .replace(/\\(?:begin|end)\{itemize\}/g, "")
    .replace(/\\(?:item|par)\b/g, "\n")
    .replace(/\\(?:textbf|emph)\{/g, "{");
  if (/\\[a-zA-Z]/.test(value))
    throw new Error(
      "Use plain text, itemize, textbf, emph and par inside responsibility blocks.",
    );
  return value
    .replace(/[{}]/g, "")
    .replace(/\u0000(\d+)\u0000/g, (_, index) => escapes[Number(index)])
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .join("\n");
}

export function selectCVLanguage(language = "auto", posting = "") {
  if (/^(en|english)$/i.test(language)) return "en";
  if (/^(de|german|deutsch)$/i.test(language)) return "de";
  if (
    /(?:apply|application|cv|resume|cover letter)[^.\n]{0,90}\bin (?:german|deutsch)\b|bewerbung[^.\n]{0,90}auf deutsch/i.test(
      posting,
    )
  )
    return "de";
  if (
    /(?:apply|application|cv|resume|cover letter)[^.\n]{0,90}\bin english\b|bewerbung[^.\n]{0,90}auf englisch/i.test(
      posting,
    )
  )
    return "en";
  const german =
    posting.match(
      /\b(wir|suchen|einen|eine|für|und|sie|ihre|aufgaben|kenntnisse|bewerbung|unser)\b/gi,
    ) || [];
  const english =
    posting.match(
      /\b(we|are|looking|for|and|you|your|the|experience|application|our)\b/gi,
    ) || [];
  return german.length > english.length ? "de" : "en";
}

export function withCVLanguage(profile, language) {
  const selected = selectCVLanguage(language?.split("-")[0]);
  const localized = profileForLanguage({ application_details: { preferences: profile.preferences }, localized_content: profile.localized_content }, selected);
  profile = { ...profile, preferences: localized.application_details.preferences };
  if (!Object.keys(profile.base_cvs ?? {}).length) return profile;
  const source = profile.base_cvs[selected];
  if (!source)
    throw new Error(
      `Add the ${selected === "de" ? "German" : "English"} base LaTeX CV before filling this form.`,
    );
  return { ...profile, cv_text: source, ...parseTexCV(source) };
}

export function validateTailoredCV(original, tailored) {
  if (typeof tailored !== "string" || !tailored.trim())
    throw new Error("Return the full marked LaTeX CV in latex_documents.cv.");
  const history = parseTexCV(tailored);
  const base = parseTexCV(original);
  for (const kind of ["experiences", "education"]) {
    const facts = (rows) =>
      rows
        .map(({ id, company, start, end, current }) =>
          JSON.stringify([id, company, start, end, current]),
        )
        .sort()
        .join("\n");
    if (facts(base[kind]) !== facts(history[kind]))
      throw new Error(
        "Returned CV changed work/study facts or dates. Keep all original history IDs, employers and dates.",
      );
  }
  return history;
}
