// Field discovery and conservative value mapping. No values are retained globally.
(() => {
  if (globalThis.applicationFields) return;
  // A choice can trigger a slow network-driven form update. Only an explicit
  // question capture acknowledges that state; another fill must not bypass it.
  let requiresCapture = false;
  const choicePause =
    "A choice may change later questions asynchronously. Let the page finish updating, then use Prepare from these questions before filling again.";
  const normalize = (text) =>
    text
      .replace(/ß/g, "ss")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  const visible = (el) =>
    el.getClientRects().length &&
    !el.closest('[hidden],[aria-hidden="true"]') &&
    getComputedStyle(el).visibility !== "hidden";
  // A semantic wrapper may contain just one question. Look for related
  // questions in enclosing groups, without crossing this control's form.
  function* relatedScopes(el) {
    for (let parent = el.parentElement; parent; parent = parent.parentElement) {
      if (parent.matches('fieldset,section,[role="group"],form')) yield parent;
      if (parent === el.form) return;
    }
    yield document;
  }
  const read = (el) =>
    el instanceof HTMLInputElement ||
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLSelectElement
      ? el.value
      : el.textContent.trim();
  const groupLabel = (el) => {
    const group = el.closest(".form-group");
    if (
      group &&
      group.querySelectorAll("input,select,textarea,[role=combobox]").length ===
        1
    ) {
      const labels = group.querySelectorAll(":scope > label");
      if (labels.length === 1) return labels[0];
    }
    return null;
  };
  const labelsFor = (el) => {
    // Some Bootstrap ATS templates copy label[for] from another question.
    // Recover the single local question and reject misplaced labels belonging
    // to another single-control group. Preserve other valid native labels so
    // contradictory meanings still prevent filling.
    const local = groupLabel(el);
    const associated = [...(el.labels || [])].filter((node) => {
      const group = node.closest(".form-group");
      if (!group || group.contains(el)) return true;
      const controls = group.querySelectorAll(
        "input,select,textarea,[role=combobox]",
      );
      return controls.length !== 1 || groupLabel(controls[0]) !== node;
    });
    if (local && !associated.includes(local)) return [local, ...associated];
    if (associated.length) return associated;
    const sibling = el.previousElementSibling;
    // Some ATS templates omit label[for]. Only infer a direct preceding
    // label in a container with one control, never a neighboring question.
    const adjacent =
      sibling?.matches("label") &&
      (!sibling.htmlFor || !document.getElementById(sibling.htmlFor)) &&
      visible(sibling) &&
      el.parentElement.querySelectorAll("input,select,textarea,[role=combobox]")
        .length === 1
        ? [sibling]
        : [];
    if (adjacent.length) return adjacent;
    const paragraph = el.closest("p");
    if (
      paragraph &&
      [...paragraph.querySelectorAll("label[for]")].some((label) => {
        const target = document.getElementById(label.htmlFor);
        return target && target !== el;
      })
    )
      return [];
    return paragraph &&
      paragraph.querySelectorAll("input,select,textarea").length === 1
      ? [paragraph]
      : [];
  };
  const label = (el) => {
    const clean = (node) => {
      const clone = node.cloneNode(true);
      clone
        .querySelectorAll("input,textarea,select,button")
        .forEach((n) => n.remove());
      return clone.textContent;
    };
    const labelled = el
      .getAttribute("aria-labelledby")
      ?.split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent || "")
      .join(" ");
    const name = (
      el.getAttribute("aria-label") ||
      labelled ||
      labelsFor(el).map(clean).join(" ") ||
      el.getAttribute("placeholder") ||
      el.name ||
      el.id ||
      el.getAttribute("role") ||
      el.tagName
    )
      .replace(/\s+/g, " ")
      .trim();
    const row = el.closest("[data-experience]");
    const history = globalThis.applicationHistory?.describe(el);
    if (history)
      return `Experience ${history.index + 1}: ${name}`.slice(0, 180);
    return (
      row ? `Experience ${Number(row.dataset.experience) + 1}: ${name}` : name
    ).slice(0, 180);
  };
  const aliases = {
    salutation: ["salutation", "anrede", "honorific prefix"],
    birth_name: ["birth name", "maiden name", "geburtsname"],
    birth_date: ["birth date", "date of birth", "geburtsdatum"],
    birth_place: ["birth place", "place of birth", "geburtsort"],
    nationality: [
      "nationality",
      "citizenship",
      "staatsangehorigkeit",
      "staatsangehoerigkeit",
      "kennung staatsangehoerigkeit",
    ],
    driving_licence: [
      "driving licence",
      "driving license",
      "driver s license",
      "fuhrerschein",
      "fuehrerschein",
    ],
    additional_information: [
      "additional information",
      "further information",
      "weitere informationen",
      "mochten sie uns noch weitere informationen mitteilen",
    ],
    first_name: ["first", "first name", "given name", "forename", "vorname"],
    last_name: ["last", "last name", "surname", "family name", "nachname"],
    full_name: [
      "full name",
      "complete name",
      "name",
      "vollstandiger name",
      "voller name",
    ],
    email: [
      "email",
      "e mail",
      "email address",
      "e mail address",
      "e mail adresse",
      "email adresse",
      "contact email address",
    ],
    phone: [
      "phone",
      "phone number",
      "telephone",
      "telephone number",
      "telefon",
      "telefonnummer",
      "mobile phone",
      "mobile cell phone",
      "phone including country code",
    ],
    city: [
      "city",
      "town",
      "wohnort",
      "ort",
      "current location",
      "where are you based",
      "city town",
      "town or city",
      "home town",
      "current city of residence",
      "wohnsitz stadt",
    ],
    country: ["country", "land", "country of residence", "wohnland"],
    street: [
      "street address",
      "street",
      "strasse",
      "adresse",
      "address line 1",
      "strasse hausnummer",
    ],
    postal_city: [
      "plz ort",
      "plz wohnort",
      "postleitzahl und wohnort",
      "postal code and city",
    ],
    postal_address: [
      "full postal address",
      "full address",
      "vollstandige anschrift",
      "vollstandige adresse",
    ],
    house_number: ["house number", "hausnummer"],
    postal_code: [
      "postal code",
      "zip code",
      "postcode",
      "post code",
      "postleitzahl",
      "plz",
    ],
    linkedin: [
      "linkedin",
      "linkedin profile",
      "linkedin url",
      "linkedin profile url",
      "link to your linkedin profile",
    ],
    website: ["website", "personal website", "webseite"],
    portfolio: ["portfolio", "portfolio url", "portfolio link"],
    github: ["github", "github url", "github profile"],
    "pref:available_from": [
      "available from",
      "earliest start date",
      "start availability",
      "fruhester eintrittstermin",
      "verfugbar ab",
    ],
    "pref:notice_period": ["notice period", "kundigungsfrist"],
    "pref:weekly_hours": ["hours per week", "weekly hours", "wochenstunden"],
    "pref:employment_type": [
      "employment type",
      "desired employment type",
      "beschaftigungsart",
    ],
    "pref:sponsorship_required": [
      "do you require visa sponsorship",
      "will you require visa sponsorship",
      "will you now or in the future require sponsorship",
      "benotigen sie ein arbeitgebervisum",
    ],
    "pref:willing_to_relocate": [
      "willing to relocate",
      "are you willing to relocate",
      "umzugsbereit",
      "sind sie umzugsbereit",
    ],
    cover_letter: [
      "cover letter",
      "anschreiben",
      "motivation letter",
      "motivationsschreiben",
    ],
  };
  const auto = {
    "given-name": "first_name",
    "honorific-prefix": "salutation",
    bday: "birth_date",
    "family-name": "last_name",
    name: "full_name",
    email: "email",
    tel: "phone",
    "address-level2": "city",
    country: "country",
    "country-name": "country",
    "postal-code": "postal_code",
    "address-line1": "street",
    "street-address": "street",
  };
  const referencedText = (el) =>
    (el?.getAttribute("aria-labelledby") || "")
      .split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent || "")
      .join(" ")
      .trim();
  const sectionContext = (el) => {
    const names = [];
    for (let parent = el.parentElement; parent; parent = parent.parentElement) {
      if (
        !parent.matches('fieldset,section,[role="group"],[role="region"]') &&
        !(
          parent.matches("div") &&
          parent.querySelector(":scope > h2,:scope > h3,:scope > h4")
        )
      )
        continue;
      names.push(
        parent.getAttribute("aria-label") ||
          referencedText(parent) ||
          parent.querySelector(
            ":scope > legend,:scope > h2,:scope > h3,:scope > h4",
          )?.textContent ||
          "",
      );
    }
    return names.map(normalize);
  };
  const choiceGroup = (el) =>
    el.closest('fieldset,[role="radiogroup"],.application-question');
  const questionLabel = (el) => {
    const group = choiceGroup(el);
    return (
      group &&
      (group.getAttribute("aria-label") ||
        referencedText(group) ||
        group.querySelector(":scope > legend,.application-label")?.textContent)
    );
  };
  const radioOptions = (el) =>
    [...(el.form || document).querySelectorAll('input[type="radio"]')].filter(
      (r) =>
        (el.name ? r.name === el.name : r === el) &&
        r.form === el.form &&
        visible(r),
    );
  const cleanHint = (text) =>
    normalize(text.replace(/([a-z])([A-Z])/g, "$1 $2"))
      .replace(/\blinked in\b/g, "linkedin")
      .replace(/\bgit hub\b/g, "github")
      .replace(/^(?:(?:required|optional|erforderlich|pflichtfeld)\s+)+/, "")
      .replace(/(?:\s+(?:required|optional|erforderlich|pflichtfeld))+$/, "")
      .replace(
        /^(?:what is |please (?:enter|provide) |enter |bitte geben sie |geben sie )/,
        "",
      )
      .replace(/^(?:your |ihren? |ihre )/, "")
      .replace(/ ein$/, "");
  function confirmationFact(name) {
    const match = name.match(
      /^(?:confirm|re enter|repeat) (?:your )?(.+)$|^(.+?) (?:confirmation|bestatigen|wiederholen)$/,
    );
    if (!match) return null;
    const base = match[1] || match[2];
    if (/^(?:e mail|email)(?: address| adresse)?$/.test(base)) return "email";
    if (
      /^(?:phone(?: number)?|mobile phone(?: number)?|telephone(?: number)?|telefonnummer)$/.test(
        base,
      )
    )
      return "phone";
    return null;
  }
  function matchHint(name) {
    const confirmation = confirmationFact(name);
    if (confirmation) return confirmation;
    for (const [key, values] of Object.entries(aliases))
      if (values.includes(name)) return key;
    const patterns = {
      full_name:
        /^(?:first and last name|vor und nachname|vollstaendiger name)$/,
      first_name: /^(?:first names?|given names?|vorname[n]?)$/,
      last_name:
        /^(?:last names?|family names?|surnames?|familienname[n]?|nachname[n]?)$/,
      city: /^(?:where (?:do you (?:currently )?live|are you (?:currently )?(?:based|located))|(?:which|what) city (?:do you (?:currently )?live in|are you (?:currently )?based in)|(?:in welcher stadt|wo) (?:wohnen|leben) sie(?: aktuell)?|city of residence|residence city|stadt|stadt des wohnsitzes)$/,
      phone:
        /^(?:mobile(?: phone)?(?: number)?|cell(?: phone)?(?: number)?|contact (?:phone )?number|handy(?:nummer)?|mobil(?:telefon)?nummer|telefon mobil)$/,
      street:
        /^(?:street and (?:house )?number|strasse und hausnummer|street address|anschrift|address)$/,
      postal_code: /^(?:zip|zip postal code|postal zip code|postal code zip)$/,
      "pref:weekly_hours":
        /^(?:how many hours per week (?:would you like to|can you) work|gewunschte wochenstunden)$/,
      "pref:available_from":
        /^(?:when (?:can|could) you start|when are you available(?: to start)?|start date|earliest (?:possible )?start date|ab wann (?:sind sie verfugbar|konnen sie (?:anfangen|beginnen))|eintrittstermin|fruhestmoglicher eintrittstermin|fruhestmoglicher beginn|gewunschtes eintrittsdatum|moglicher eintrittstermin kundigungsfrist)$/,
      "pref:willing_to_work_onsite":
        /^(?:(?:are you (?:willing|able|available) to |are you (?:comfortable |open to )|can you |would you (?:be willing to )?)?work(?:ing)? (?:on site|onsite|(?:in|from) (?:the |our )?office)|(?:sind sie bereit |konnen sie )?vor ort (?:zu )?arbeiten|bereitschaft zur arbeit vor ort)$/,
      "pref:authorized_to_work":
        /^(?:are you (?:legally )?authori[sz]ed to work in |work authori[sz]ation |durfen sie in .+ arbeiten)/,
      "pref:salary_amount":
        /^(?:expected |desired |salary expectation|gehaltsvorstellung)/,
    };
    for (const [key, pattern] of Object.entries(patterns))
      if (
        pattern.test(name) &&
        (key !== "pref:salary_amount" || /salary|gehalt/.test(name))
      )
        return key;
    return null;
  }
  function equivalentChoice(value, text, key) {
    const normalized = normalize(text);
    if (typeof value === "boolean")
      return (
        value
          ? ["yes", "ja", "true", "yes i am", "yes i do", "ja gerne"]
          : ["no", "nein", "false", "no i am not", "no i do not"]
      ).includes(normalized);
    if (normalize(value) === normalized) return true;
    const terms =
      key === "pref:employment_type"
        ? [
            ["part time", "teilzeit"],
            ["full time", "vollzeit"],
            ["internship", "praktikum"],
            ["working student", "werkstudent", "werkstudententatigkeit"],
          ]
        : ["country", "nationality"].includes(key)
          ? [["germany", "deutschland", "de", "ger"]]
          : [];
    return terms.some(
      (group) => group.includes(normalize(value)) && group.includes(normalized),
    );
  }
  const foreignPersonScope = (section) =>
    section.some((name) =>
      /\b(references?|referees?|emergency contacts?|referenz(?:en|person(?:en)?)?|notfallkontakt(?:e)?)\b/.test(
        name,
      ),
    );
  const historyScope = (section) =>
    section.some(
      (name) =>
        !/\b(?:education|employment|academic) application\b/.test(name) &&
        /\b(work experience|professional experience|employment history|previous employment|employment experience|work history|education|academic history|academic qualifications|berufserfahrung|beruflicher werdegang|ausbildung|bildung|studium)\b|^employment(?: \d+)?$/.test(
          name,
        ),
    );
  function experienceYearsControl(el) {
    if (!(el instanceof HTMLSelectElement)) return false;
    const hints = [
      el.id || "",
      el.name || "",
      el.getAttribute("aria-label") || "",
      referencedText(el),
      ...labelsFor(el).map((node) => node.textContent),
    ].map(cleanHint);
    return hints.some((hint) =>
      [
        "job experience",
        "years of experience",
        "years of professional experience",
        "total years of experience",
        "berufserfahrung",
      ].includes(hint),
    );
  }
  function key(el) {
    const section = sectionContext(el);
    if (foreignPersonScope(section)) return null;
    const history = globalThis.applicationHistory?.describe(el);
    if (history?.key) return "experience:" + history.key;
    const presentation = globalThis.applicationHistory?.presentationKey(el);
    if (presentation) return "history:" + presentation;
    if (experienceYearsControl(el)) return "experience_years";
    // "Name", "Email" and "City" also occur in other people's contact
    // cards, schools and employers. Only a structured adapter can establish
    // the identity of a history row; applicant contacts are not a fallback.
    if (historyScope(section)) return null;
    // Read independent signals; a misleading attribute must not override a
    // contradictory label. Never use a whole container's text as a field hint.
    const question = el.type === "radio" ? questionLabel(el) : null;
    const labels = labelsFor(el).map((node) => {
      const clone = node.cloneNode(true);
      clone
        .querySelectorAll("input,textarea,select,button")
        .forEach((n) => n.remove());
      return clone.textContent;
    });
    const semantic = question
      ? [question]
      : [el.getAttribute("aria-label") || "", referencedText(el), ...labels];
    const metadata = [el.name || "", el.id || ""].map((text) =>
      cleanHint(text).replace(
        /^(?:(?:applicant|candidate|application|contact|personal|address) )+/,
        "",
      ),
    );
    const hints = [
      ...semantic.map(cleanHint),
      ...metadata,
      cleanHint(el.getAttribute("placeholder") || ""),
    ];
    if (
      hints.some((hint) =>
        /\b(company|employer|reference|referee|emergency|signature|consent|privacy|unternehmen|arbeitgeber|referenz|datenschutz|unterschrift)\b/.test(
          hint,
        ),
      )
    )
      return null;
    const meaningfulLabels = semantic
      .map(cleanHint)
      .filter(
        (hint) =>
          hint &&
          !/^(?:answer|response|value|text|antwort|eingabe)$/.test(hint),
      );
    // A real question such as "Location" is not explained by an unrelated
    // legacy ID like "website". Attribute fallback is for unlabeled controls
    // or generic answer prompts, not a license to disregard the question.
    if (meaningfulLabels.length && !meaningfulLabels.some(matchHint))
      return el.type === "email" ? "email" : el.type === "tel" ? "phone" : null;
    const matches = new Set(hints.map(matchHint).filter(Boolean));
    const autocomplete = el.getAttribute("autocomplete")?.split(/\s+/).at(-1);
    if (auto[autocomplete]) matches.add(auto[autocomplete]);
    // HTML input types are a fallback, not evidence against a specific label.
    if (!matches.size && el.type === "email") matches.add("email");
    if (!matches.size && el.type === "tel") matches.add("phone");
    const bareSurnameConflict =
      matches.size === 2 &&
      matches.has("full_name") &&
      matches.has("last_name");
    if (matches.size !== 1 && !bareSurnameConflict) return null;
    const result = matches.has("full_name") ? "full_name" : [...matches][0];
    if (
      result === "street" &&
      /^(adresse|address)$/.test(cleanHint(label(el))) &&
      /\b\d{5}\s+[A-Za-zÄÖÜäöü]/.test(el.getAttribute("placeholder") || "")
    )
      return "postal_address";
    // Bare German Name next to one unambiguous Vorname is a surname prompt.
    // Explicit full-name labels and autocomplete=name retain their meaning.
    if (
      result === "full_name" &&
      hints.includes("name") &&
      !hints.some((hint) =>
        /^(full name|complete name|vollstandiger name|voller name)$/.test(hint),
      ) &&
      meaningfulLabels.every((hint) => hint === "name") &&
      autocomplete !== "name"
    ) {
      for (const scope of relatedScopes(el)) {
        const given = [...scope.querySelectorAll("input")].filter((other) => {
          if (other === el || !visible(other)) return false;
          const context = sectionContext(other);
          if (foreignPersonScope(context) || historyScope(context))
            return false;
          const meaningful = [
            other.getAttribute("aria-label") || "",
            referencedText(other),
            ...labelsFor(other).map((node) => node.textContent),
          ]
            .map(cleanHint)
            .filter(Boolean);
          if (
            meaningful.length &&
            !meaningful.some((hint) => matchHint(hint) === "first_name")
          )
            return false;
          const signals = [
            other.getAttribute("aria-label") || "",
            referencedText(other),
            ...labelsFor(other).map((node) => node.textContent),
            other.name || "",
          ];
          const found = new Set(
            signals.map(cleanHint).map(matchHint).filter(Boolean),
          );
          return found.size === 1 && found.has("first_name");
        });
        if (given.length === 1) return "last_name";
        if (given.length > 1) break;
      }
    }
    return matches.size === 1 ? result : null;
  }
  function relatedHouseNumber(el) {
    return [...relatedScopes(el)].some((scope) =>
      [...scope.querySelectorAll("input")].some(
        (other) =>
          other !== el && visible(other) && key(other) === "house_number",
      ),
    );
  }
  const controls = (root) =>
    [
      ...root.querySelectorAll(
        'input,select,textarea,[contenteditable="true"],[role="combobox"]',
      ),
    ].filter(
      (el) =>
        visible(el) &&
        (el.getAttribute("role") === "combobox" ||
          !["hidden", "submit", "button", "reset", "image"].includes(el.type)),
    );
  const hasAuthenticationControls = () =>
    [
      ...document.querySelectorAll(
        'input[type="password"],input[autocomplete="one-time-code"]',
      ),
    ].some(visible);
  function roots(linkedApplication = false) {
    if (hasAuthenticationControls()) return [];
    let forms = [...document.querySelectorAll("form")].filter((form) => {
      const fields = controls(form);
      const known = fields.filter((el) => key(el));
      return (
        !form.querySelector('input[type="password"]') &&
        ((linkedApplication && fields.length > 0) ||
          known.length >= 2 ||
          (known.length && fields.some((el) => el.type === "file")))
      );
    });
    if (forms.length > 1) {
      forms = forms.filter(
        (form) =>
          form.querySelector("input[type=file]") ||
          /\b(application|apply|bewerbung|bewerben)\b/i.test(form.textContent),
      );
      if (forms.length !== 1) return [];
    }
    const dialogs = [
      ...document.querySelectorAll('dialog[open],[role="dialog"]'),
    ].filter((dialog) => visible(dialog) && controls(dialog).length);
    if (forms.length)
      return [
        ...forms,
        ...dialogs.filter(
          (dialog) => !forms.some((form) => form.contains(dialog)),
        ),
      ];
    const known = controls(document).filter((el) => key(el));
    return known.length >= 2 && !document.querySelector("form")
      ? [document]
      : [];
  }
  function inventory(linkedApplication = false) {
    const result = [];
    const seen = new Set();
    for (const root of roots(linkedApplication)) {
      for (const el of controls(root))
        if (!seen.has(el)) {
          seen.add(el);
          if (
            el.type === "radio" &&
            el.name &&
            result.some(
              (field) =>
                field.el.type === "radio" &&
                field.el.name === el.name &&
                field.el.form === el.form &&
                choiceGroup(field.el) === choiceGroup(el),
            )
          )
            continue;
          let name = label(el);
          if (el.type === "radio") name = questionLabel(el) || el.name || name;
          result.push({
            el,
            label: name.replace(/\s+/g, " ").trim().slice(0, 180),
            key: key(el),
            required:
              el.required ||
              el.getAttribute("aria-required") === "true" ||
              /(?:\*|\brequired\b|pflichtfeld)/i.test(name),
          });
        }
    }
    return result;
  }
  function parseAnswers(text) {
    const answers = new Map();
    try {
      const data = JSON.parse(text);
      if (data && typeof data === "object" && !Array.isArray(data))
        for (const [q, a] of Object.entries(data))
          if (typeof a === "string") answers.set(normalize(q), a);
      return answers;
    } catch {}
    let question = null;
    for (const line of text.split(/\r?\n/)) {
      const cleaned = line.replace(/\*\*/g, "").trim();
      const q = cleaned.match(/^(?:Q(?:uestion)?|Frage)\s*:\s*(.+)$/i);
      const a = cleaned.match(/^(?:A(?:nswer)?|Antwort)\s*:\s*(.+)$/i);
      if (q) question = q[1];
      else if (a && question) {
        answers.set(normalize(question), a[1]);
        question = null;
      } else {
        const pair = cleaned.match(/^[-*]?\s*([^:]{3,180}):\s*(.+)$/);
        if (pair) answers.set(normalize(pair[1]), pair[2]);
      }
    }
    return answers;
  }
  function sameApplication(url) {
    if (!url) return false;
    try {
      const expected = new URL(url);
      return (
        expected.origin === location.origin &&
        expected.pathname === location.pathname &&
        expected.search === location.search &&
        expected.hash === location.hash
      );
    } catch {
      return false;
    }
  }
  function dateValue(el, value) {
    if (
      !/^[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}$/.test(value || "") ||
      el.type === "date"
    )
      return value;
    const format =
      el.getAttribute("data-date-format") ||
      (location.hostname === "www.bewerber-online.de" &&
      el.matches("input.datepicker")
        ? "dd.mm.yyyy"
        : "");
    return format.toLowerCase() === "dd.mm.yyyy"
      ? value.split("-").reverse().join(".")
      : value;
  }
  function experienceMonths(experiences) {
    if (!experiences?.length) return null;
    const monthRange = (value) => {
      const match = String(value || "").match(
        /^([1-9][0-9]{3})(?:-(0[1-9]|1[0-2]))?$/,
      );
      if (!match) return null;
      const yearStart = Number(match[1]) * 12;
      if (!match[2]) return { earliest: yearStart, latest: yearStart + 11 };
      const month = yearStart + Number(match[2]) - 1;
      return { earliest: month, latest: month };
    };
    const now = new Date();
    const currentMonth = now.getFullYear() * 12 + now.getMonth();
    const shortestPeriods = [];
    const longestPeriods = [];
    for (const entry of experiences) {
      const start = monthRange(entry.start);
      const end = entry.current
        ? { earliest: currentMonth, latest: currentMonth }
        : monthRange(entry.end);
      if (!start || !end || end.latest <= start.earliest) return null;
      if (end.earliest > start.latest)
        shortestPeriods.push([start.latest, end.earliest]);
      longestPeriods.push([start.earliest, end.latest]);
    }
    const totalMonths = (periods) => {
      if (!periods.length) return 0;
      periods.sort((left, right) => left[0] - right[0] || left[1] - right[1]);
      let total = 0;
      let [start, end] = periods[0];
      for (const [nextStart, nextEnd] of periods.slice(1)) {
        if (nextStart <= end) end = Math.max(end, nextEnd);
        else {
          total += end - start;
          [start, end] = [nextStart, nextEnd];
        }
      }
      return total + end - start;
    };
    return {
      minimum: totalMonths(shortestPeriods),
      maximum: totalMonths(longestPeriods),
    };
  }
  function experienceYearBand(el, profile) {
    const months = experienceMonths(profile.experiences || []);
    if (months === null || months.maximum < 1)
      return {
        value: "",
        source: "CV work dates",
        reason:
          "The saved CV dates do not establish a reliable total work-experience duration.",
      };
    const options = [...el.options].filter(
      (option) => !option.disabled && option.value,
    );
    const categories = options.flatMap((option) => {
      const text = cleanHint(option.textContent || "");
      if (/\b(einsteiger|entry level|no experience)\b/.test(text))
        return [{ option, kind: "entry", limit: 0 }];
      const number =
        text.match(/\d+(?:[.,]\d+)?/) ||
        option.value.match(/^\d+(?:[.,]\d+)?$/);
      if (!number) return [];
      const limit = Number(number[0].replace(",", "."));
      if (!Number.isFinite(limit)) return [];
      if (/\b(or more|oder mehr|at least|and over|plus|ab)\b/.test(text))
        return [{ option, kind: "minimum", limit }];
      if (/\b(up to|bis|max(?:imum)?)\b/.test(text))
        return [{ option, kind: "maximum", limit }];
      return [];
    });
    const bandFor = (years) => {
      const minimums = categories
        .filter(({ kind, limit }) => kind === "minimum" && years >= limit)
        .sort((left, right) => right.limit - left.limit);
      const maximums = categories
        .filter(
          ({ kind, limit }) =>
            (kind === "maximum" && years <= limit) ||
            (kind === "entry" && years === 0),
        )
        .sort((left, right) => left.limit - right.limit);
      return minimums[0] || maximums[0] || null;
    };
    const lowerBand = bandFor(months.minimum / 12);
    const upperBand = bandFor(months.maximum / 12);
    if (
      !lowerBand ||
      !upperBand ||
      lowerBand.option.value !== upperBand.option.value
    )
      return {
        value: "",
        source: "CV work dates",
        reason:
          "The approximate CV dates span more than one work-experience range, so this answer needs review.",
      };
    const selected = lowerBand;
    if (!selected)
      return {
        value: "",
        source: "CV work dates",
        reason:
          "The form does not offer one clear range for the duration in the saved work history.",
      };
    return {
      value: selected.option.value,
      source: "CV work dates (approximate range)",
      reason:
        "The range uses saved work dates, merges overlapping roles, and checks the CV's year/month precision.",
    };
  }
  function valueFor(field, profile, answers, scoped, preparedAnswer) {
    const { el, key } = field;
    const structured = globalThis.applicationHistory?.mapping(el, profile);
    if (structured) return structured;
    const pref = profile.preferences || {};
    if (key === "experience_years") return experienceYearBand(el, profile);
    if (key?.startsWith("pref:")) {
      const property = key.slice(5);
      let value =
        property === "available_from"
          ? dateValue(el, pref[property])
          : pref[property];
      let reason = "This reusable answer has not been supplied.";
      const name = normalize(field.label);
      if (property === "authorized_to_work") {
        const country = normalize(pref.authorization_country || "");
        const aliases = ["germany", "deutschland", "de"].includes(country)
          ? ["germany", "deutschland"]
          : country
            ? [country]
            : [];
        const askedCountry = name.replace(
          /^(are you (legally )?authori[sz]ed to work in |work authori[sz]ation )/,
          "",
        );
        if (!aliases.includes(askedCountry)) {
          value = "";
          reason =
            "Work permission is country-specific; the question country does not match the confirmed country in your profile.";
        }
      }
      if (property === "salary_amount") {
        const currency = (name.match(/\b(eur|usd|gbp|chf|cad|aud)\b/) ||
          [])[1]?.toUpperCase();
        const period = /\b(annual|annually|year|yearly|jahr|jahrlich)\b/.test(
          name,
        )
          ? "year"
          : /\b(month|monthly|monat|monatlich)\b/.test(name)
            ? "month"
            : /\b(hour|hourly|stunde|stundlich)\b/.test(name)
              ? "hour"
              : "";
        if (
          !pref.salary_amount ||
          !pref.salary_currency ||
          !pref.salary_period
        ) {
          value = "";
          reason =
            "Salary needs an amount, currency and pay period in the profile.";
        } else if (
          (currency && currency !== pref.salary_currency) ||
          (period && period !== pref.salary_period) ||
          (el.type === "number" && (!currency || !period))
        ) {
          value = "";
          reason =
            "Salary currency or pay period is absent or differs from the profile; no conversion or unit assumption was made.";
        } else
          value =
            currency && period
              ? pref.salary_amount
              : `${pref.salary_amount} ${pref.salary_currency}/${pref.salary_period}`;
      }
      if (
        (value === "" || value === undefined) &&
        ![
          "authorized_to_work",
          "sponsorship_required",
          "willing_to_relocate",
          "willing_to_work_onsite",
          "salary_amount",
        ].includes(property) &&
        scoped &&
        (preparedAnswer || answers.has(name))
      )
        return {
          value: preparedAnswer || answers.get(name),
          source: preparedAnswer ? "Prepared form answer" : "ChatGPT package",
        };
      return {
        value: value ?? "",
        source: "User-provided common answer",
        reason,
        choice:
          (el.type === "radio" && typeof value === "string" && !!value) ||
          [
            "authorized_to_work",
            "sponsorship_required",
            "willing_to_relocate",
            "willing_to_work_onsite",
          ].includes(property),
      };
    }
    const row = el.closest("[data-experience]");
    if (row) {
      const index = Number(row.dataset.experience);
      const entry = profile.experiences[index];
      if (entry) {
        if (el.dataset.key)
          return { value: entry[el.dataset.key] ?? "", source: "CV/profile" };
        if (el.dataset.year)
          return {
            value: entry[el.dataset.year]?.slice(0, 4) || "",
            source: "CV/profile",
          };
        if (el.dataset.month)
          return {
            value:
              entry[el.dataset.month]?.length === 7
                ? [
                    "January",
                    "February",
                    "March",
                    "April",
                    "May",
                    "June",
                    "July",
                    "August",
                    "September",
                    "October",
                    "November",
                    "December",
                  ][Number(entry[el.dataset.month].slice(5)) - 1]
                : "",
            source: "CV/profile",
            reason: "The CV gives a year but no exact month.",
          };
      }
    }
    if (key === "cover_letter")
      return {
        value: scoped ? profile.application?.cover_letter || "" : "",
        source: "ChatGPT package",
        reason: scoped
          ? "No cover letter supplied."
          : "Link the ChatGPT package to this exact application URL.",
      };
    if (key === "postal_city" || key === "postal_address") {
      const complete =
        profile.postal_code &&
        profile.city &&
        (key !== "postal_address" || profile.street);
      return {
        value: complete
          ? (key === "postal_address" ? profile.street + ", " : "") +
            profile.postal_code +
            " " +
            profile.city
          : "",
        source: "Saved profile",
        reason:
          "The full address requires each component to be supplied in your profile.",
      };
    }
    if (key === "street") {
      if (relatedHouseNumber(el))
        return {
          value: "",
          source: "Saved profile",
          reason:
            "This form separates street and house number; the profile contains a combined address line. Review both fields manually.",
        };
    }
    // These questions previously used the exact job-linked answer path.
    // A new reusable fact must not hide an already prepared explicit answer.
    if (
      key &&
      [
        "salutation",
        "birth_name",
        "birth_date",
        "birth_place",
        "nationality",
        "driving_licence",
        "additional_information",
      ].includes(key) &&
      !profile[key] &&
      scoped
    ) {
      const answer = preparedAnswer || answers.get(normalize(field.label));
      if (answer)
        return {
          value: key === "birth_date" ? dateValue(el, answer) : answer,
          source: preparedAnswer ? "Prepared form answer" : "ChatGPT package",
        };
    }
    if (key)
      return {
        value:
          key === "full_name"
            ? [profile.first_name, profile.last_name].filter(Boolean).join(" ")
            : key === "birth_date"
              ? dateValue(el, profile[key])
              : profile[key] || "",
        source: "CV/profile",
        reason:
          "Save this confirmed fact in your profile, then sync and fill again.",
      };
    if (scoped && profile.application?.form_answers)
      return {
        value: preparedAnswer || "",
        source: preparedAnswer ? "Prepared form answer" : "",
        reason:
          "This question is unanswered, requires manual entry, or the captured form changed. Scan the current step to prepare new answers.",
      };
    const answer = answers.get(normalize(field.label));
    return {
      value: scoped ? answer || "" : "",
      source: answer ? "ChatGPT package" : "",
      reason:
        answer && !scoped
          ? "The ChatGPT answer belongs to a different or unlinked application."
          : "No exact known fact or answer matches this field.",
    };
  }
  function fieldId(field, index) {
    const row = field.el.closest("[data-experience]");
    return row
      ? `experience-${row.dataset.experience}-${field.el.dataset.key || field.el.dataset.year || field.el.dataset.month || index}-${field.el.dataset.year ? "year" : field.el.dataset.month ? "month" : "value"}`
      : field.el.id || `field-${index}`;
  }
  function snapshot(linkedApplication = false) {
    return new Map(
      inventory(linkedApplication).map((field, i) => [
        fieldId(field, i),
        read(field.el),
      ]),
    );
  }

  function capture(linkedApplication = false) {
    const controls = inventory(linkedApplication);
    if (!controls.length || controls.length > 100)
      throw new Error("Open one application step with at most 100 fields.");
    const fields = controls.map((field, index) => {
      const el = field.el;
      const type =
        el.tagName === "TEXTAREA"
          ? "textarea"
          : el.tagName === "SELECT"
            ? "select"
            : el.type || el.getAttribute("role") || "custom";
      const options =
        el instanceof HTMLSelectElement
          ? [...el.options]
              .filter((option) => !option.disabled)
              .map((option) => ({
                value: option.value,
                label: option.textContent.trim(),
              }))
          : [];
      const manual =
        ![
          "text",
          "textarea",
          "select",
          "email",
          "tel",
          "url",
          "number",
          "date",
        ].includes(type) ||
        el.disabled ||
        el.readOnly ||
        el.multiple ||
        el.getAttribute("role") === "combobox" ||
        /(?:consent|privacy|signature|declaration|authori[sz]|sponsor|einwilligung|datenschutz|arbeitserlaubnis)/i.test(
          field.label,
        );
      return {
        id: `q${index}`,
        label: field.label,
        type,
        required: !!field.required,
        max_length: el.maxLength > 0 ? el.maxLength : null,
        options,
        accept: el.type === "file" ? el.accept : "",
        document_kind:
          el.type === "file"
            ? (() => {
                const hint = `${field.label} ${el.id} ${el.name}`.replace(
                  /[_-]/g,
                  " ",
                );
                const cv = /\b(cv|resume|résumé|lebenslauf)\b/i.test(hint);
                const letter = /cover.?letter|anschreiben|motivation/i.test(
                  hint,
                );
                return cv !== letter ? (cv ? "cv" : "cover_letter") : "";
              })()
            : "",
        manual: !!manual,
      };
    });
    const safeURL =
      globalThis.applicationPageContext?.sanitizeURL(location.href) ||
      `${location.origin}${location.pathname}`;
    const shape = JSON.stringify({
      url: safeURL,
      identity: globalThis.applicationPageContext?.identity(),
      fields,
    });
    if (shape.length > 30000)
      throw new Error("This form is too large to prepare automatically.");
    return { url: safeURL, fields, shape, controls };
  }

  async function describeCapture({ url, fields, shape }) {
    const digest = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(shape),
    );
    return {
      url,
      form_id: [...new Uint8Array(digest)]
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join(""),
      fields,
    };
  }

  async function describe(linkedApplication = false, refresh = false) {
    const description = await describeCapture(capture(linkedApplication));
    if (refresh) requiresCapture = false;
    return description;
  }

  async function run(profile, { fill = true, before = new Map() } = {}) {
    const scoped = sameApplication(profile.application?.url);
    const fields = inventory(scoped);
    const prepared = profile.application?.form_answers;
    const captured = scoped && prepared ? capture(true) : null;
    const form = captured ? await describeCapture(captured) : null;
    const boundShape =
      form?.form_id === prepared?.form_id ? captured?.shape : null;
    const preparedAnswers =
      form && prepared && form.form_id === prepared.form_id
        ? new Map(prepared.answers.map((answer) => [answer.id, answer.value]))
        : new Map();
    const answers = prepared
      ? new Map()
      : parseAnswers(profile.application?.application_answers || "");
    const report = [];
    let filled = 0;
    let unchanged = 0;
    let mutated = false;
    let halted = prepared && requiresCapture ? choicePause : "";
    const formStillMatches = () => {
      if (!boundShape) return true;
      try {
        const current = capture(true);
        if (
          current.shape === boundShape &&
          current.controls.every(
            (field, index) => field.el === fields[index]?.el,
          )
        )
          return true;
      } catch {}
      halted =
        "The captured form changed while filling. Accepted entries were preserved. Scan the current step to prepare new answers.";
      return false;
    };
    const pauseAfterChoice = () => {
      if (boundShape) {
        requiresCapture = true;
        halted = choicePause;
      }
    };
    const deadline = performance.now() + 20000;
    const keyCounts = new Map();
    for (const field of fields)
      if (
        field.key &&
        !field.el.closest("[data-experience]") &&
        !globalThis.applicationHistory?.describe(field.el)
      )
        keyCounts.set(field.key, (keyCounts.get(field.key) || 0) + 1);
    const reusableContacts = new Set();
    for (const contact of ["email", "phone"]) {
      const matches = fields.filter((field) => field.key === contact);
      // One primary plus explicit confirmation fields may reuse a fact.
      // Two independent primary fields remain ambiguous.
      if (
        matches.filter(
          (field) => confirmationFact(cleanHint(label(field.el))) !== contact,
        ).length === 1
      )
        reusableContacts.add(contact);
    }
    for (const [index, field] of fields.entries()) {
      formStillMatches();
      const { el } = field;
      const id = fieldId(field, index);
      const record = {
        id,
        label: field.label,
        required: field.required,
        status: "unfilled",
        reason: "",
        source: "",
      };
      const mapped = valueFor(
        field,
        profile,
        answers,
        scoped,
        form?.fields[index]?.manual ? "" : preparedAnswers.get(`q${index}`),
      );
      const value = mapped.value;
      record.source = mapped.source;
      const stop = (status, reason) => {
        record.status = status;
        record.reason = reason;
        report.push(record);
      };
      if (halted) {
        stop("not_attempted", halted);
        continue;
      }
      const liveLabel = (
        el.type === "radio"
          ? questionLabel(el) || el.name || label(el)
          : label(el)
      )
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 180);
      if (
        !el.isConnected ||
        key(el) !== field.key ||
        liveLabel !== field.label
      ) {
        halted =
          "A question changed while filling. Review the page before filling again.";
        stop("changed", halted);
        continue;
      }
      if (mapped.status) {
        stop(mapped.status, mapped.reason);
        continue;
      }
      if (mapped.notApplicable) {
        record.status = "already_filled";
        record.reason = "No end date applies to a current role.";
        report.push(record);
        continue;
      }
      if (el.type === "file") {
        stop("manual", "Choose the required file yourself.");
        continue;
      }
      if (
        el.type === "checkbox" &&
        el.dataset.key === "current" &&
        el.closest("[data-experience]") &&
        el.checked === value
      ) {
        record.status = "already_filled";
        report.push(record);
        unchanged++;
        continue;
      }
      if (
        (typeof value === "boolean" ||
          (el.type === "radio" && typeof value === "string" && !!value)) &&
        field.key &&
        !globalThis.applicationHistory?.describe(el) &&
        keyCounts.get(field.key) > 1 &&
        !reusableContacts.has(field.key)
      ) {
        stop(
          "ambiguous",
          "Multiple fields match this fact; choose the correct field.",
        );
        continue;
      }
      const historyChoice =
        globalThis.applicationHistory?.describe(el)?.key === "current";
      if (
        ["checkbox", "radio"].includes(el.type) &&
        (typeof value === "boolean" ||
          (el.type === "radio" && typeof value === "string" && !!value)) &&
        (mapped.choice || historyChoice)
      ) {
        if (!fill) {
          stop(
            "not_attempted",
            "An earlier adapter conflict stopped this section.",
          );
          continue;
        }
        if (el.type === "radio") {
          const radios = radioOptions(el);
          if (radios.some((r) => choiceGroup(r) !== choiceGroup(el))) {
            stop(
              "ambiguous",
              "The same native radio group spans multiple question containers.",
            );
            continue;
          }
          const options = radios.filter((r) => {
            const optionLabel =
              r.getAttribute("aria-label") ||
              referencedText(r) ||
              [...(r.labels || [])]
                .map((l) => l.textContent)
                .join(" ")
                .trim();
            // The visible option is authoritative; opaque values such as 0/1
            // have no reliable Yes/No meaning across applicant systems.
            return equivalentChoice(value, optionLabel || r.value, field.key);
          });
          const selected = radios.find((r) => r.checked);
          if (options.length !== 1) {
            stop(
              "unsupported",
              "No unique explicit option matches the saved answer.",
            );
            continue;
          }
          if (selected && selected !== options[0]) {
            stop("conflict", "A different existing choice was preserved.");
            continue;
          }
          if (selected) {
            record.status = "already_filled";
            report.push(record);
            unchanged++;
            continue;
          }
          if (options[0].disabled) {
            stop("disabled", "The matching choice is disabled.");
            continue;
          }
          options[0].click();
          mutated = true;
          pauseAfterChoice();
          if (!options[0].isConnected || !options[0].checked) {
            stop("rejected", "The site did not retain the selected answer.");
            continue;
          }
        } else {
          if (el.disabled) {
            stop("disabled", "This choice is disabled.");
            continue;
          }
          if (el.checked === value) {
            record.status = "already_filled";
            report.push(record);
            unchanged++;
            continue;
          }
          if (el.checked) {
            stop("conflict", "An existing checked answer was preserved.");
            continue;
          }
          el.click();
          mutated = true;
          pauseAfterChoice();
          if (!el.isConnected || el.checked !== value) {
            stop("rejected", "The site did not retain the selected answer.");
            continue;
          }
        }
        record.status = "filled";
        report.push(record);
        filled++;
        continue;
      }
      if (["password", "checkbox", "radio"].includes(el.type)) {
        stop(
          "manual",
          mapped.choice
            ? mapped.reason
            : "Make this choice yourself; the extension does not infer consent or legal declarations.",
        );
        continue;
      }
      if (el.disabled || el.readOnly) {
        stop("disabled", "This field is disabled or read-only.");
        continue;
      }
      if (value === "" || value === null || value === undefined) {
        if (
          (el instanceof HTMLInputElement ||
            el instanceof HTMLTextAreaElement) &&
          el.value &&
          !el.matches('[role="combobox"]') &&
          el.validity.valid
        ) {
          record.status = "provided_by_user";
          record.source = "Existing draft";
          report.push(record);
          continue;
        }
        stop(
          "missing",
          mapped.reason || "This fact is missing from the CV/profile.",
        );
        continue;
      }
      if (
        field.key &&
        !globalThis.applicationHistory?.describe(el) &&
        keyCounts.get(field.key) > 1 &&
        !reusableContacts.has(field.key)
      ) {
        stop(
          "ambiguous",
          "Multiple fields match this fact; choose the correct field.",
        );
        continue;
      }
      if (
        typeof value === "boolean" &&
        !(mapped.choice && el instanceof HTMLSelectElement)
      ) {
        stop("manual", "Review this selection.");
        continue;
      }
      const expected =
        typeof value === "boolean" ? (value ? "Yes" : "No") : String(value);
      if (
        (el.type === "month" && !/^\d{4}-\d{2}$/.test(expected)) ||
        (el.type === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(expected))
      ) {
        stop(
          "missing",
          "The CV does not give the date precision this field requires.",
        );
        continue;
      }
      const existing = read(el);
      let selectedOption = null;
      if (el instanceof HTMLSelectElement) {
        const equivalent = (text) => {
          const normalized = normalize(text);
          if (
            globalThis.applicationHistory?.describe(el)?.key?.endsWith("_month")
          ) {
            const monthNames = [
              ["january", "januar"],
              ["february", "februar"],
              ["march", "marz"],
              ["april"],
              ["may", "mai"],
              ["june", "juni"],
              ["july", "juli"],
              ["august"],
              ["september"],
              ["october", "oktober"],
              ["november"],
              ["december", "dezember"],
            ];
            return (
              (/^\d{1,2}$/.test(normalized) &&
                Number(normalized) === Number(expected)) ||
              monthNames[Number(expected) - 1]?.includes(normalized)
            );
          }
          return equivalentChoice(value, text, field.key);
        };
        const options = [...el.options].filter(
          (option) =>
            !option.disabled &&
            (equivalent(option.value) || equivalent(option.textContent)),
        );
        if (options.length !== 1) {
          stop("unsupported", "No unique option matches the known value.");
          continue;
        }
        selectedOption = options[0];
        if (el.value === selectedOption.value) {
          if (!el.validity.valid) {
            stop("rejected", "The existing selection is still invalid.");
            continue;
          }
          record.status = "already_filled";
          report.push(record);
          unchanged++;
          continue;
        }
      }
      if (existing === expected) {
        if (el.validity && !el.validity.valid) {
          stop("rejected", "The existing value is still invalid.");
          continue;
        }
        record.status =
          before.has(id) && before.get(id) === expected
            ? "already_filled"
            : "filled";
        report.push(record);
        unchanged++;
        continue;
      }
      if (
        existing &&
        !(el instanceof HTMLSelectElement && el.selectedOptions[0]?.disabled)
      ) {
        stop("conflict", "An existing different value was preserved.");
        continue;
      }
      if (!fill) {
        stop(
          "not_attempted",
          "An earlier adapter conflict stopped this section.",
        );
        continue;
      }
      if (performance.now() > deadline) {
        stop(
          "not_attempted",
          "The best-effort time limit was reached. Run again after reviewing the page.",
        );
        continue;
      }
      try {
        let accepted = expected;
        if (el.matches('[role="combobox"]') && el.tagName !== "SELECT") {
          mutated = true;
          el.focus();
          el.click();
          await new Promise((resolve) => setTimeout(resolve, 0));
          if (!formStillMatches()) {
            stop("not_attempted", halted);
            continue;
          }
          let listId =
            el.getAttribute("aria-controls") || el.getAttribute("aria-owns");
          if (!listId && el instanceof HTMLInputElement) {
            el.dispatchEvent(
              new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
            );
            await new Promise((resolve) => setTimeout(resolve, 0));
            if (!formStillMatches()) {
              stop("not_attempted", halted);
              continue;
            }
            listId =
              el.getAttribute("aria-controls") || el.getAttribute("aria-owns");
          }
          if (!listId) {
            stop(
              "unsupported",
              "Custom dropdown has no explicit option-list association.",
            );
            continue;
          }
          if (el instanceof HTMLInputElement) {
            Object.getOwnPropertyDescriptor(
              HTMLInputElement.prototype,
              "value",
            ).set.call(el, expected);
            el.dispatchEvent(
              new Event("input", { bubbles: true, composed: true }),
            );
          } else if (el instanceof HTMLButtonElement && el.type === "button")
            el.click();
          else {
            stop("unsupported", "Unsupported combobox control.");
            continue;
          }
          const until = Math.min(deadline, performance.now() + 1200);
          let matches = [];
          while (performance.now() < until) {
            matches = [
              ...(document
                .getElementById(listId)
                ?.querySelectorAll('[role="option"]') || []),
            ].filter(
              (option) =>
                visible(option) &&
                normalize(option.textContent) === normalize(expected),
            );
            if (matches.length) break;
            await new Promise((resolve) => setTimeout(resolve, 35));
          }
          if (!formStillMatches()) {
            stop("not_attempted", halted);
            continue;
          }
          if (matches.length !== 1) {
            if (el instanceof HTMLInputElement) {
              Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype,
                "value",
              ).set.call(el, "");
              el.dispatchEvent(new Event("input", { bubbles: true }));
            }
            stop(
              "unsupported",
              "No unique exact dropdown option was available.",
            );
            continue;
          }
          matches[0].click();
          pauseAfterChoice();
          await new Promise((resolve) => setTimeout(resolve, 0));
          if (
            read(el) !== expected ||
            el.getAttribute("aria-expanded") === "true"
          ) {
            halted =
              "An earlier dropdown changed without a verified selection. Review it before continuing.";
            stop(
              "changed_unverified",
              "The dropdown changed, but the chosen value could not be verified. Inspect the current selection before retrying.",
            );
            continue;
          }
          record.status = "filled";
          report.push(record);
          filled++;
          continue;
        } else if (el instanceof HTMLSelectElement) {
          accepted = selectedOption.value;
          Object.getOwnPropertyDescriptor(
            HTMLSelectElement.prototype,
            "value",
          ).set.call(el, accepted);
        } else if (
          el instanceof HTMLInputElement ||
          el instanceof HTMLTextAreaElement
        ) {
          if (el.maxLength > 0 && expected.length > el.maxLength) {
            stop(
              "unsupported",
              "The known answer exceeds the field length limit.",
            );
            continue;
          }
          Object.getOwnPropertyDescriptor(
            el instanceof HTMLTextAreaElement
              ? HTMLTextAreaElement.prototype
              : HTMLInputElement.prototype,
            "value",
          ).set.call(el, expected);
        } else {
          stop(
            "unsupported",
            "Rich-text editing is not supported by this adapter.",
          );
          continue;
        }
        mutated = true;
        el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
        el.dispatchEvent(
          new Event("change", { bubbles: true, composed: true }),
        );
        if (el instanceof HTMLSelectElement) pauseAfterChoice();
        el.blur();
        await new Promise((resolve) => setTimeout(resolve, 0));
        if (
          !el.isConnected ||
          el.value !== accepted ||
          (el.validity && !el.validity.valid)
        ) {
          stop(
            "rejected",
            "The site rejected or replaced this value; inspect the field.",
          );
          continue;
        }
        filled++;
        record.status = "filled";
        report.push(record);
      } catch {
        stop("unsupported", "Could not set this control; fill it manually.");
      }
    }
    for (const frame of document.querySelectorAll("iframe"))
      if (visible(frame)) {
        // A same-origin recommendations frame without editable controls is
        // unrelated to filling. Cross-origin frames remain reported for review.
        try {
          if (
            frame.contentDocument?.body &&
            frame.contentDocument.readyState === "complete" &&
            (frame.contentDocument.URL !== "about:blank" ||
              !frame.getAttribute("src") ||
              frame.src === "about:blank") &&
            !frame.contentDocument.querySelector(
              "input:not([type=hidden]),select,textarea,iframe,[contenteditable]:not([contenteditable=false]),[role=combobox],[role=textbox],[role=checkbox],[role=radio]",
            )
          )
            continue;
        } catch {}
        report.push({
          id: "frame",
          label: frame.title || "Embedded frame",
          required: false,
          status: "unsupported",
          reason:
            "This frame was not inspected. If it contains application fields, open it directly to fill them.",
          source: "",
        });
      }
    for (const element of document.querySelectorAll("*"))
      if (element.shadowRoot && visible(element))
        report.push({
          id: "shadow",
          label: element.getAttribute("aria-label") || "Shadow DOM section",
          required: false,
          status: "unsupported",
          reason: "Shadow DOM controls were not inspected.",
          source: "",
        });
    return { fields: report, filled, unchanged, mutated };
  }
  async function documentTargets() {
    const form = await describe(true);
    const controls = inventory(true);
    return {
      ...form,
      hidden_uploads: [
        ...document.querySelectorAll('input[type="file"]'),
      ].filter((el) => !controls.some((field) => field.el === el)).length,
      fields: form.fields.map((field, i) => ({
        ...field,
        available:
          field.type === "file" &&
          !controls[i].el.disabled &&
          !controls[i].el.files.length,
      })),
    };
  }
  async function attachDocuments(plan) {
    if (
      location.href !== plan.url ||
      (plan.page_guard &&
        globalThis.applicationPageContext.identity() !== plan.page_guard)
    )
      throw new Error("The application page changed. Inspect it again.");
    if ((await describe(true)).form_id !== plan.form_id)
      throw new Error(
        "The application form changed during export. Scan it again.",
      );
    const controls = inventory(true);
    const files = plan.files.map((file) => {
      const el = controls[Number(file.id.slice(1))]?.el;
      if (!el || el.type !== "file" || el.disabled || el.files.length)
        throw new Error(
          "An upload field changed or already contains a file. Review it before retrying.",
        );
      const bytes = Uint8Array.from(atob(file.base64), (char) =>
        char.charCodeAt(0),
      );
      return { el, file: new File([bytes], file.name, { type: file.type }) };
    });
    let selected = 0;
    for (const item of files) {
      // A previous input/change handler can replace controls or navigate the wizard.
      if (
        !item.el.isConnected ||
        item.el.disabled ||
        item.el.files.length ||
        location.href !== plan.url ||
        (await describe(true)).form_id !== plan.form_id
      )
        throw new Error(
          `${selected} documents selected before the form changed. Review the upload fields.`,
        );
      const transfer = new DataTransfer();
      transfer.items.add(item.file);
      item.el.files = transfer.files;
      item.el.dispatchEvent(new Event("input", { bubbles: true }));
      item.el.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (
        !item.el.isConnected ||
        item.el.files?.[0]?.name !== item.file.name ||
        item.el.files?.[0]?.size !== item.file.size
      )
        throw new Error(
          "The site did not retain the selected file. Check its upload confirmation before retrying.",
        );
      selected++;
    }
    return {
      selected,
      message: `${selected} documents selected. Check the site's upload confirmation and review the application.`,
    };
  }
  globalThis.applicationFields = {
    documentTargets,
    attachDocuments,
    describe,
    sameApplication,
    supports: (linkedApplication = false) => {
      const fields = inventory(linkedApplication);
      return (
        fields.some((field) => field.key) ||
        (linkedApplication && fields.length > 0)
      );
    },
    hasAuthenticationControls,
    acceptsControl: (el) => roots().some((root) => root.contains(el)),
    run,
    snapshot,
  };
})();
