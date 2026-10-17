// Field discovery and conservative value mapping. No values are retained globally.
(() => {
  if (globalThis.applicationFields) return;
  const normalize = (text) =>
    text
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  const visible = (el) =>
    el.getClientRects().length &&
    !el.closest('[hidden],[aria-hidden="true"]') &&
    getComputedStyle(el).visibility !== "hidden";
  const read = (el) =>
    el instanceof HTMLInputElement ||
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLSelectElement
      ? el.value
      : el.textContent.trim();
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
      [...(el.labels || [])].map(clean).join(" ") ||
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
    first_name: ["first name", "given name", "vorname"],
    last_name: ["last name", "surname", "family name", "nachname"],
    full_name: ["full name", "name", "vollstandiger name", "voller name"],
    email: [
      "email",
      "e mail",
      "email address",
      "e mail address",
      "e mail adresse",
      "email adresse",
    ],
    phone: [
      "phone",
      "phone number",
      "telephone",
      "telephone number",
      "telefon",
      "telefonnummer",
      "mobile phone",
    ],
    city: [
      "city",
      "town",
      "wohnort",
      "ort",
      "current location",
      "where are you based",
    ],
    country: ["country", "land", "country of residence", "wohnland"],
    street: [
      "street address",
      "street",
      "strasse",
      "adresse",
      "address line 1",
    ],
    postal_code: ["postal code", "zip code", "postcode", "postleitzahl", "plz"],
    linkedin: [
      "linkedin",
      "linkedin profile",
      "linkedin url",
      "linkedin profile url",
    ],
    website: ["website", "personal website", "webseite"],
    portfolio: ["portfolio", "portfolio url"],
    github: ["github", "github url"],
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
    "family-name": "last_name",
    name: "full_name",
    email: "email",
    tel: "phone",
    "address-level2": "city",
    country: "country",
    "country-name": "country",
    "postal-code": "postal_code",
    "address-line1": "street",
  };
  function key(el) {
    const history = globalThis.applicationHistory?.describe(el);
    if (history?.key) return "experience:" + history.key;
    const question =
      el.type === "radio"
        ? el.closest("fieldset")?.querySelector(":scope > legend")?.textContent
        : null;
    const name = normalize(question || label(el)).replace(
      /\s+(required|optional|erforderlich|pflichtfeld)$/,
      "",
    );
    if (
      /^(are you (legally )?authori[sz]ed to work in |work authori[sz]ation |durfen sie in .+ arbeiten)/.test(
        name,
      )
    )
      return "pref:authorized_to_work";
    if (
      /^(expected |desired |salary expectation|gehaltsvorstellung)/.test(
        name,
      ) &&
      /(salary|gehalt)/.test(name)
    )
      return "pref:salary_amount";
    const field = el.getAttribute("autocomplete")?.split(/\s+/).at(-1);
    if (auto[field]) return auto[field];
    for (const [key, values] of Object.entries(aliases))
      if (values.includes(name)) return key;
    const canonical = normalize(
      (el.name || "").replace(/([a-z])([A-Z])/g, "$1 $2"),
    );
    for (const candidate of [
      "first_name",
      "last_name",
      "email",
      "phone",
      "linkedin",
    ])
      if (aliases[candidate].includes(canonical)) return candidate;
    if (el.type === "email") return "email";
    if (el.type === "tel") return "phone";
    return null;
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
  function roots() {
    if (hasAuthenticationControls()) return [];
    let forms = [...document.querySelectorAll("form")].filter((form) => {
      const fields = controls(form);
      const known = fields.filter((el) => key(el));
      return (
        !form.querySelector('input[type="password"]') &&
        (known.length >= 2 ||
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
  function inventory() {
    const result = [];
    const seen = new Set();
    for (const root of roots()) {
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
                field.el.form === el.form,
            )
          )
            continue;
          let name = label(el);
          if (el.type === "radio")
            name =
              el.closest("fieldset")?.querySelector("legend")?.textContent ||
              el
                .closest(".application-question")
                ?.querySelector(".application-label")?.textContent ||
              el.name ||
              name;
          result.push({
            el,
            label: name.replace(/\s+/g, " ").trim().slice(0, 180),
            key: key(el),
            required:
              el.required || el.getAttribute("aria-required") === "true",
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
  function valueFor(field, profile, answers, scoped) {
    const { el, key } = field;
    const structured = globalThis.applicationHistory?.mapping(el, profile);
    if (structured) return structured;
    const pref = profile.preferences || {};
    if (key?.startsWith("pref:")) {
      const property = key.slice(5);
      let value = pref[property];
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
          "salary_amount",
        ].includes(property) &&
        scoped &&
        answers.has(name)
      )
        return { value: answers.get(name), source: "ChatGPT package" };
      return {
        value: value ?? "",
        source: "User-provided common answer",
        reason,
        choice: [
          "authorized_to_work",
          "sponsorship_required",
          "willing_to_relocate",
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
    if (key)
      return {
        value:
          key === "full_name"
            ? [profile.first_name, profile.last_name].filter(Boolean).join(" ")
            : profile[key] || "",
        source: "CV/profile",
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
  function snapshot() {
    return new Map(
      inventory().map((field, i) => [fieldId(field, i), read(field.el)]),
    );
  }

  async function run(profile, { fill = true, before = new Map() } = {}) {
    const fields = inventory();
    const answers = parseAnswers(
      profile.application?.application_answers || "",
    );
    const scoped = sameApplication(profile.application?.url);
    const report = [];
    let filled = 0;
    let unchanged = 0;
    let mutated = false;
    let halted = false;
    const deadline = performance.now() + 20000;
    const keyCounts = new Map();
    for (const field of fields)
      if (
        field.key &&
        !field.el.closest("[data-experience]") &&
        !globalThis.applicationHistory?.describe(field.el)
      )
        keyCounts.set(field.key, (keyCounts.get(field.key) || 0) + 1);
    for (const [index, field] of fields.entries()) {
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
      const mapped = valueFor(field, profile, answers, scoped);
      const value = mapped.value;
      record.source = mapped.source;
      const stop = (status, reason) => {
        record.status = status;
        record.reason = reason;
        report.push(record);
      };
      if (halted) {
        stop(
          "not_attempted",
          "An earlier dropdown changed without a verified selection. Review it before continuing.",
        );
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
        typeof value === "boolean" &&
        field.key &&
        !globalThis.applicationHistory?.describe(el) &&
        keyCounts.get(field.key) > 1
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
        typeof value === "boolean" &&
        (mapped.choice || historyChoice)
      ) {
        if (!fill) {
          stop(
            "not_attempted",
            "An earlier adapter conflict stopped this section.",
          );
          continue;
        }
        const boolText = (text) =>
          ({
            yes: true,
            ja: true,
            true: true,
            no: false,
            nein: false,
            false: false,
          })[normalize(text)];
        if (el.type === "radio") {
          const radios = [
            ...(el.form || document).querySelectorAll("input[type=radio]"),
          ].filter(
            (r) =>
              r.name === el.name &&
              r.form === el.form &&
              r.closest("fieldset") === el.closest("fieldset") &&
              visible(r),
          );
          const options = radios.filter(
            (r) =>
              boolText(
                [...(r.labels || [])].map((l) => l.textContent).join(" "),
              ) === value,
          );
          const selected = radios.find((r) => r.checked);
          if (options.length !== 1) {
            stop(
              "unsupported",
              "No unique explicit Yes/No option matches the saved answer.",
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
        keyCounts.get(field.key) > 1
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
          if (typeof value === "boolean")
            return (
              value ? ["yes", "ja", "true"] : ["no", "nein", "false"]
            ).includes(normalized);
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
          return (
            normalize(expected) === normalized ||
            (field.key === "country" &&
              ["germany", "deutschland", "de", "ger"].includes(
                normalize(expected),
              ) &&
              ["germany", "deutschland", "de", "ger"].includes(normalized))
          );
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
          let listId =
            el.getAttribute("aria-controls") || el.getAttribute("aria-owns");
          if (!listId && el instanceof HTMLInputElement) {
            el.dispatchEvent(
              new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
            );
            await new Promise((resolve) => setTimeout(resolve, 0));
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
          await new Promise((resolve) => setTimeout(resolve, 0));
          if (
            read(el) !== expected ||
            el.getAttribute("aria-expanded") === "true"
          ) {
            halted = true;
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
      if (visible(frame))
        report.push({
          id: "frame",
          label: frame.title || "Embedded application frame",
          required: false,
          status: "unsupported",
          reason:
            "Open the embedded application directly; this run operates on the top document.",
          source: "",
        });
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
  globalThis.applicationFields = {
    sameApplication,
    supports: () => inventory().some((field) => field.key),
    hasAuthenticationControls,
    acceptsControl: (el) => roots().some((root) => root.contains(el)),
    run,
    snapshot,
  };
})();
