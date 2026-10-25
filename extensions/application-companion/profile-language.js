// Narrative content is independent per language; identity and factual answers are shared.
export const localizedKeys = [
  "summary",
  "custom_instructions",
  "application_personalization",
  "skills",
  "desired_roles",
  "secondary_roles",
  "skill_evidence",
  "education",
  "certifications",
];
export const localizedPreferenceKeys = ["notice_period", "employment_type"];
export function profileForLanguage(profile, language = "en") {
  if (!profile) return profile;
  const localized = profile.localized_content?.[language];
  const result = { ...profile };
  delete result.localized_content;
  for (const key of localizedKeys) {
    // Legacy profiles keep their English content. Missing German prose stays unknown.
    result[key] =
      localized?.[key] ?? (language === "en" ? profile[key] : undefined);
  }
  if (profile.application_details) {
    const preferences = { ...profile.application_details.preferences };
    for (const key of localizedPreferenceKeys) {
      if (localized?.application_preferences?.[key] !== undefined)
        preferences[key] = localized.application_preferences[key];
      else if (language !== "en" && key in preferences) preferences[key] = "";
    }
    result.application_details = {
      ...profile.application_details,
      preferences,
    };
  }
  return result;
}

/** Validate only translatable wording; shared identity and numeric facts cannot be overridden. */
export function validateLocalizedContent(input) {
  const object = (value, allowed) => {
    if (
      !value ||
      typeof value !== "object" ||
      Array.isArray(value) ||
      Object.keys(value).some((key) => !allowed.includes(key))
    )
      throw new Error("Invalid language-specific profile content.");
    return value;
  };
  const text = (value, limit) => {
    if (typeof value !== "string" || value.length > limit)
      throw new Error("Invalid or oversized language-specific text.");
    return value.trim();
  };
  const terms = (value, limit) => {
    if (!Array.isArray(value) || value.length > limit)
      throw new Error("Invalid language-specific list.");
    return value.map((item) => {
      const result = text(item, 80);
      if (!result)
        throw new Error("Language-specific list items must not be empty.");
      return result;
    });
  };
  const result = {};
  for (const [language, content] of Object.entries(
    object(input, ["en", "de"]),
  )) {
    object(content, [...localizedKeys, "application_preferences"]);
    const next = {};
    for (const [key, value] of Object.entries(content)) {
      if (["summary", "custom_instructions"].includes(key))
        next[key] = text(value, 5000);
      else if (
        [
          "skills",
          "desired_roles",
          "secondary_roles",
          "certifications",
        ].includes(key)
      )
        next[key] = terms(
          value,
          {
            skills: 100,
            desired_roles: 20,
            secondary_roles: 20,
            certifications: 50,
          }[key],
        );
      else if (key === "application_personalization") {
        const limits = {
          career_evidence: 12000,
          writing_samples: 12000,
          cover_letter_example: 16000,
          wording_to_avoid: 3000,
          career_goals: 5000,
          work_preferences: 5000,
        };
        next[key] = Object.fromEntries(
          Object.entries(object(value, Object.keys(limits))).map(
            ([key, value]) => [key, text(value, limits[key])],
          ),
        );
      } else if (key === "application_preferences") {
        next[key] = Object.fromEntries(
          Object.entries(object(value, localizedPreferenceKeys)).map(
            ([key, value]) => [key, text(value, 300)],
          ),
        );
      } else if (key === "skill_evidence") {
        if (
          !value ||
          typeof value !== "object" ||
          Array.isArray(value) ||
          Object.keys(value).length > 100
        )
          throw new Error("Invalid skill evidence.");
        next[key] = Object.fromEntries(
          Object.entries(value).map(([skill, evidence]) => {
            terms([skill], 1);
            object(evidence, ["context", "note"]);
            return [
              skill,
              {
                context: text(evidence.context ?? "", 120),
                note: text(evidence.note ?? "", 2000),
              },
            ];
          }),
        );
      } else if (key === "education") {
        if (!Array.isArray(value) || value.length > 20)
          throw new Error("Invalid education.");
        next[key] = value.map((row) => {
          object(row, ["level", "field", "note"]);
          if (
            !["vocational", "bachelor", "master", "doctorate"].includes(
              row.level,
            )
          )
            throw new Error("Invalid qualification level.");
          return {
            level: row.level,
            field: text(row.field ?? "", 200),
            note: text(row.note ?? "", 2000),
          };
        });
      }
    }
    result[language] = next;
  }
  return result;
}
