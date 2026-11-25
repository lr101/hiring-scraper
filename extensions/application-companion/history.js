// Semantic inline work-history adapter. Never infers employment facts from prose.
(() => {
  if (globalThis.applicationHistory) return;
  const norm = (s) =>
    s
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  const visible = (el) =>
    el.getClientRects().length && !el.closest("[hidden],[aria-hidden=true]");
  const aliases = {
    company: [
      "name",
      "employer",
      "company",
      "company name",
      "arbeitgeber",
      "unternehmen",
    ],
    title: [
      "tatigkeitsbezeichnung",
      "job title",
      "position",
      "role title",
      "berufsbezeichnung",
      "tatigkeit",
      "position title",
    ],
    location: ["location", "job location", "standort", "arbeitsort"],
    description: [
      "responsibilities",
      "description",
      "job description",
      "aufgaben",
      "tatigkeitsbeschreibung",
    ],
    start: ["start date", "from", "beginn", "von"],
    end: ["end date", "to", "ende", "bis"],
    start_year: ["start year", "beginn jahr", "von jahr"],
    end_year: ["end year", "ende jahr", "bis jahr"],
    start_month: ["start month", "beginn monat", "von monat"],
    end_month: ["end month", "ende monat", "bis monat"],
    current: [
      "i currently work here",
      "current role",
      "currently employed here",
      "aktuelle tatigkeit",
      "bis heute",
      "ich arbeite derzeit hier",
    ],
  };
  function fieldLabel(el) {
    let labels = [...(el.labels || [])];
    const sibling = el.previousElementSibling;
    if (
      !labels.length &&
      sibling?.matches("label") &&
      (!sibling.htmlFor || !document.getElementById(sibling.htmlFor)) &&
      el.parentElement.querySelectorAll("input,select,textarea").length === 1
    )
      labels = [sibling];
    if (!labels.length) {
      const paragraph = el.closest("p");
      const ownedElsewhere = (node) =>
        node &&
        [...node.querySelectorAll("label[for]")].some((label) => {
          const target = document.getElementById(label.htmlFor);
          return target && target !== el;
        });
      if (
        paragraph &&
        !ownedElsewhere(paragraph) &&
        paragraph.querySelectorAll("input,select,textarea").length === 1
      ) {
        const copy = paragraph.cloneNode(true);
        copy
          .querySelectorAll("input,select,textarea")
          .forEach((node) => node.remove());
        const previous = paragraph.previousElementSibling;
        labels =
          !copy.textContent.trim() &&
          previous?.matches("p") &&
          !ownedElsewhere(previous) &&
          !previous.querySelector("input,select,textarea,button")
            ? [previous]
            : [paragraph];
      }
    }
    const text =
      el.getAttribute("aria-label") ||
      (el.getAttribute("aria-labelledby") || "")
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent || "")
        .join(" ")
        .trim() ||
      labels
        .map((label) => {
          const copy = label.cloneNode(true);
          copy
            .querySelectorAll("input,select,textarea")
            .forEach((node) => node.remove());
          return copy.textContent;
        })
        .join(" ");
    return norm(text)
      .replace(/^(?:(?:required|optional|erforderlich|pflichtfeld)\s+)+/, "")
      .replace(/(?: (?:required|optional|erforderlich|pflichtfeld))+$/, "");
  }
  function fieldKey(el, allowGeneric = true) {
    const text = fieldLabel(el);
    return (
      Object.keys(aliases).find(
        (key) =>
          aliases[key].includes(text) && (allowGeneric || text !== "name"),
      ) || null
    );
  }
  function localGroupNames(parent) {
    const referenced = (parent.getAttribute("aria-labelledby") || "")
      .split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent || "")
      .join(" ");
    const heading = parent.querySelector(
      ":scope > legend,:scope > h2,:scope > h3,:scope > h4",
    )?.textContent;
    return [parent.getAttribute("aria-label"), referenced, heading]
      .map((name) => norm(name || ""))
      .filter(Boolean);
  }
  const groupNames = (group) => {
    const names = [];
    for (let parent = group; parent; parent = parent.parentElement)
      names.push(...localGroupNames(parent));
    return names;
  };
  const foreignScope = (names) =>
    names.some((name) =>
      /\b(references?|referees?|emergency contacts?|referenz(?:en|person(?:en)?)?|notfallkontakt(?:e)?)\b/.test(
        name,
      ),
    );
  const historyHeading =
    /\b(work experience|professional experience|employment history|employment experience|work history|experience|berufserfahrung|beruflicher werdegang)\b/;
  const historyAddAction =
    /^(?:add (?:another )?(?:work )?experience|add employment|berufserfahrung hinzufugen|weitere berufserfahrung)$/;
  function historyContainer(el) {
    for (let parent = el.parentElement; parent; parent = parent.parentElement) {
      if (
        localGroupNames(parent).some(
          (name) =>
            !/\bemployment application\b/.test(name) &&
            historyHeading.test(name),
        )
      )
        return parent;
    }
    return null;
  }
  function historySectionForGroup(group) {
    // When an ATS creates its first row directly inside the labeled group,
    // that group is both the row and the section that owns the Add button.
    return historyContainer(group) || group;
  }
  function addExperienceButtons(scope = document) {
    return [...scope.querySelectorAll("button")].filter((button) => {
      const name = norm(
        button.getAttribute("aria-label") ||
          button.textContent ||
          button.title ||
          "",
      );
      return (
        visible(button) &&
        button.type === "button" &&
        !button.disabled &&
        historyAddAction.test(name)
      );
    });
  }
  const hasVisibleDialog = () =>
    [...document.querySelectorAll("dialog[open],[role=dialog]")].some(visible);
  let cachedGroups = null;
  const invalidateGroups = () => {
    cachedGroups = null;
  };
  const groupChanges = new MutationObserver(invalidateGroups);
  groupChanges.observe(document, {
    subtree: true,
    childList: true,
    attributes: true,
    characterData: true,
  });
  document.addEventListener("input", invalidateGroups, true);
  document.addEventListener("change", invalidateGroups, true);
  window.addEventListener("resize", invalidateGroups);
  function groups() {
    if (groupChanges.takeRecords().length) invalidateGroups();
    if (cachedGroups) return cachedGroups;
    // Start at identity fields, not thousands of decorative layout divs.
    const containers = new Set();
    for (const el of document.querySelectorAll("input,textarea,select")) {
      if (!visible(el) || !["company", "title"].includes(fieldKey(el)))
        continue;
      for (let parent = el.parentElement; parent; parent = parent.parentElement)
        if (parent.matches('fieldset,[role="group"],section,div'))
          containers.add(parent);
    }
    const candidates = [...containers].filter((group) => {
      if (
        !visible(group) ||
        group.closest("[data-experience]") ||
        group.closest("dialog,[role=dialog]")
      )
        return false;
      const names = groupNames(group);
      if (foreignScope(names)) return false;
      if (
        !names.some(
          (name) =>
            !/\b(?:employment|education|academic) application\b/.test(name) &&
            /\b(work experience|professional experience|experience|employment|work history|berufserfahrung|beruflicher werdegang|tatigkeit)\b/.test(
              name,
            ),
        )
      )
        return false;
      const fields = [
        ...group.querySelectorAll("input,textarea,select"),
      ].filter(visible);
      // A list wrapper with two employers/titles is not one record. Including
      // nested divs lets us discover the actual cards regardless of depth.
      const keys = fields.map((el) => fieldKey(el));
      if (
        keys.filter((key) => key === "company").length !== 1 ||
        keys.filter((key) => key === "title").length !== 1
      )
        return false;
      const explicitCompany = fields.some(
        (el) => fieldKey(el, false) === "company",
      );
      const dated = keys.some(
        (key) => key === "start" || key === "start_year" || key === "current",
      );
      if (!explicitCompany && !dated) return false;
      // A generic ancestor must not reclassify a descendant reference card
      // that was excluded as a record in its own right.
      return !fields.some((el) => foreignScope(groupNames(el.parentElement)));
    });
    // The smallest complete blocks are records. A section containing several
    // such blocks is their container, not an additional experience row.
    cachedGroups = candidates.filter(
      (group) =>
        !candidates.some((child) => child !== group && group.contains(child)),
    );
    return cachedGroups;
  }
  function describe(el) {
    const entries = groups();
    const group = entries.find((entry) => entry.contains(el));
    if (!group) return null;
    const index = entries.indexOf(group);
    if (index < 0) return null;
    return { group, index, key: fieldKey(el) };
  }
  let blocked = "";
  let extra = [];
  // Only DOM nodes and source indices persist between calls; no profile facts.
  let bindings = new Map();
  function mapping(el, profile) {
    const info = describe(el);
    if (!info) return presentationMapping(el, profile);
    const entry = profile.experiences[bindings.get(info.group) ?? info.index];
    if (blocked)
      return {
        value: "",
        source: "Structured work history",
        status: "conflict",
        reason: blocked,
      };
    if (!entry)
      return {
        value: "",
        source: "Structured work history",
        reason: "No structured job exists for this experience row.",
      };
    const { key } = info;
    if (!key)
      return {
        value: "",
        reason: "This control has no supported work-history label.",
      };
    if (key === "end" || key.startsWith("end_"))
      if (entry.current)
        return {
          value: "",
          notApplicable: true,
          source: "Structured work history",
        };
    let value = entry[key];
    if (key.endsWith("_year"))
      value = entry[key.split("_")[0]]?.slice(0, 4) || "";
    if (key.endsWith("_month"))
      value =
        entry[key.split("_")[0]]?.length === 7
          ? entry[key.split("_")[0]].slice(5)
          : "";
    return {
      value: value ?? "",
      source:
        key === "description" && entry.description_source
          ? "ChatGPT tailored CV bullets"
          : "Structured work history",
      reason: key.endsWith("_month")
        ? "The structured job gives no exact month."
        : "",
    };
  }
  const careerHeading =
    /\b(work history|employment history|professional history|beruflicher werdegang|berufserfahrung)\b/;
  function presentationInfo(el) {
    const name = fieldLabel(el);
    if (
      !name ||
      !visible(el) ||
      !el.matches("input:not([type=checkbox]):not([type=radio]),textarea")
    )
      return null;
    const names = groupNames(el.parentElement);
    if (
      foreignScope(names) ||
      names.some((name) =>
        /\b(education|school|ausbildung|bildung|studium)\b/.test(name),
      )
    )
      return null;
    if (
      /^(beruflicher werdegang(?: und tatigkeiten stichpunkte)?|work history|employment history)$/.test(
        name,
      ) &&
      el.tagName === "TEXTAREA"
    )
      return { key: "summary" };
    const numbered = name.match(
      /^(?:beruflicher werdegang|work history|employment history) ([1-9]|1[0-9]|20)$/,
    );
    if (numbered)
      return {
        key: "entry:" + (Number(numbered[1]) - 1),
        index: Number(numbered[1]) - 1,
      };
    if (
      /^(letzter arbeitgeber|latest employer|most recent employer)$/.test(name)
    )
      return { key: "latest", index: 0 };
    if (
      ![
        "arbeitgeber tatigkeiten",
        "employer duties",
        "von bis",
        "employment period",
      ].includes(name)
    )
      return null;
    let container = el.parentElement;
    while (
      container &&
      !careerHeading.test(
        norm(
          container.getAttribute("aria-label") ||
            container.querySelector(
              ":scope > legend,:scope > h2,:scope > h3,:scope > h4",
            )?.textContent ||
            "",
        ),
      )
    )
      container = container.parentElement;
    if (!container) return null;
    const fields = [...container.querySelectorAll("input,textarea")].filter(
      visible,
    );
    const employers = fields.filter((field) =>
      ["arbeitgeber tatigkeiten", "employer duties"].includes(
        fieldLabel(field),
      ),
    );
    const periods = fields.filter((field) =>
      ["von bis", "employment period"].includes(fieldLabel(field)),
    );
    if (!employers.length || employers.length !== periods.length) return null;
    const category = employers.includes(el) ? "duties" : "period";
    const index = (category === "duties" ? employers : periods).indexOf(el);
    return index < 0
      ? null
      : { key: category + ":" + index, index, employers, periods };
  }
  const periodText = (job) =>
    `${job.start} – ${job.current ? "Present" : job.end || ""}`;
  const dutiesText = (job) =>
    `${job.company} | ${job.title}${job.description ? ". " + job.description : ""}`;
  const entryText = (job) =>
    `${periodText(job)}: ${job.company} | ${job.title}${job.location ? " | " + job.location : ""}${job.description ? ". " + job.description : ""}`;
  function presentationMapping(el, profile) {
    const info = presentationInfo(el);
    if (!info) return null;
    const jobs = profile.experiences || [];
    const job = jobs[info.index];
    let value = "";
    if (info.key === "summary") value = jobs.map(entryText).join("\n");
    else if (job) {
      if (info.key === "latest") value = job.company;
      else if (info.key.startsWith("entry:")) {
        const scope = el.form || document;
        for (const peer of scope.querySelectorAll("input,textarea")) {
          const other = presentationInfo(peer);
          if (!other?.key.startsWith("entry:")) continue;
          const expected = jobs[other.index]
            ? entryText(jobs[other.index])
            : "";
          if (peer.value && peer.value !== expected)
            return {
              value: "",
              status: "conflict",
              source: "Saved work history",
              reason:
                "Existing numbered history differs from saved order. Review its records before filling.",
            };
        }
        value = entryText(job);
      } else {
        // Composite rows cannot be safely reordered by parsing free text.
        // An existing incompatible draft blocks the whole row collection.
        for (let index = 0; index < info.employers.length; index++) {
          const source = jobs[index];
          for (const [field, expected] of [
            [info.employers[index], source ? dutiesText(source) : ""],
            [info.periods[index], source ? periodText(source) : ""],
          ])
            if (field.value && field.value !== expected)
              return {
                value: "",
                status: "conflict",
                source: "Saved work history",
                reason:
                  "Existing combined history differs from saved order. Review its records before filling.",
              };
        }
        value = info.key.startsWith("duties:")
          ? dutiesText(job)
          : periodText(job);
        if (
          info.key.startsWith("period:") &&
          /dd|tt|day|tag|\\d/.test(
            (el.placeholder || "").toLowerCase() +
              (el.getAttribute("pattern") || ""),
          )
        )
          return {
            value: "",
            source: "Saved work history",
            reason:
              "This date range requests precision that is not stored in the profile.",
          };
      }
    }
    if (el.maxLength > 0 && value.length > el.maxLength)
      return {
        value: "",
        status: "unsupported",
        source: "Saved work history",
        reason:
          "The saved history exceeds this field's maximum length; shorten it manually without losing facts.",
      };
    if (value && el instanceof HTMLInputElement) {
      // Validate composed text before dispatching any events on the live form.
      // Native patterns can express date precision without DD or \\d hints.
      const probe = el.cloneNode(false);
      probe.value = value;
      if (probe.value !== value || !probe.validity.valid)
        return {
          value: "",
          status: "unsupported",
          source: "Saved work history",
          reason:
            "The saved history does not match this field's format; review it manually.",
        };
    }
    return {
      value,
      source: "Saved work history",
      reason: "No saved work record is available for this field.",
    };
  }
  function compatible(el, job) {
    const key = fieldKey(el);
    if (!key) return true;
    if (key === "current") return !el.checked || job.current;
    if (!el.value) return true;
    if ((key === "end" || key.startsWith("end_")) && job.current) return false;
    let expected = job[key];
    if (key.endsWith("_year")) expected = job[key.split("_")[0]]?.slice(0, 4);
    if (key.endsWith("_month")) {
      const date = job[key.split("_")[0]];
      expected = date?.length === 7 ? date.slice(5) : "";
    }
    if (expected === undefined || expected === "") return true;
    if (["company", "title", "location"].includes(key)) {
      const identity = (value) =>
        String(value).trim().replace(/\s+/g, " ").toLowerCase();
      return identity(el.value) === identity(expected);
    }
    if (key.endsWith("_month") && /^\d{1,2}$/.test(el.value))
      return Number(el.value) === Number(expected);
    return el.value === String(expected);
  }
  function bindRecords(entries, jobs) {
    const anchored = [];
    for (const [index, group] of entries.entries()) {
      const fields = [
        ...group.querySelectorAll("input,select,textarea"),
      ].filter(visible);
      if (
        !fields.some((el) => {
          const key = fieldKey(el);
          return key === "current"
            ? el.checked
            : [
                "company",
                "title",
                "start",
                "start_year",
                "end",
                "end_year",
              ].includes(key) && el.value.trim();
        })
      )
        continue;
      anchored.push({
        index,
        candidates: jobs.flatMap((job, source) =>
          fields.every((el) => compatible(el, job)) ? [source] : [],
        ),
      });
    }
    // Find an injective assignment of anchored rows to saved jobs. Re-running
    // with each chosen edge forbidden proves that the identity is unique;
    // source order or a score tie must not invent an anchored row's identity.
    const solve = (forbiddenRow = -1, forbiddenSource = -1) => {
      const owners = new Map();
      const visit = (row, seen) => {
        for (const source of row.candidates) {
          if (
            (row.index === forbiddenRow && source === forbiddenSource) ||
            seen.has(source)
          )
            continue;
          seen.add(source);
          const previous = owners.get(source);
          if (!previous || visit(previous, seen)) {
            owners.set(source, row);
            return true;
          }
        }
        return false;
      };
      for (const row of anchored) if (!visit(row, new Set())) return null;
      return new Map([...owners].map(([source, row]) => [row.index, source]));
    };
    const chosen = solve();
    if (!chosen) {
      blocked =
        "Existing work-history facts do not match distinct saved jobs. Existing history was preserved.";
      return false;
    }
    for (const [row, source] of chosen) {
      if (solve(row, source)) {
        blocked =
          "An existing work-history row matches multiple saved jobs. Add an employer, title or known date to identify it.";
        return false;
      }
    }
    const used = new Set(chosen.values());
    const remaining = jobs
      .map((_, index) => index)
      .filter((index) => !used.has(index));
    const next = new Map();
    for (const [index, group] of entries.entries())
      next.set(
        group,
        chosen.has(index) ? chosen.get(index) : remaining.shift(),
      );
    bindings = next;
    return true;
  }
  async function prepare(profile) {
    invalidateGroups();
    bindings = new Map();
    blocked = "";
    extra = [];
    let added = 0,
      mutated = false;
    let entries = groups();
    if (!entries.length) {
      const actions = addExperienceButtons();
      // Only bootstrap a completely empty history section on the ATS whose
      // inline row behavior has been verified. Existing rows can still use
      // the generic scoped Add flow below on other supported forms.
      const scoped =
        location.hostname === "jobs.smartrecruiters.com"
          ? actions.filter((button) => historyContainer(button))
          : [];
      if (scoped.length === 1 && profile.experiences.length) {
        const section = historyContainer(scoped[0]);
        scoped[0].click();
        mutated = true;
        const until = performance.now() + 1500;
        while (
          performance.now() < until &&
          groups().length === 0 &&
          !hasVisibleDialog()
        )
          await new Promise((r) => setTimeout(r, 30));
        entries = groups();
        if (
          entries.length !== 1 ||
          historySectionForGroup(entries[0]) !== section ||
          hasVisibleDialog()
        ) {
          extra.push({
            id: "work-history-add",
            label: "Work history",
            status: "unsupported",
            reason:
              "The Add experience action did not create one inline job row in this section. An employer-specific modal or save workflow needs manual completion; no Save action was attempted.",
            required: false,
            source: "",
          });
          return { added, mutated };
        }
        added++;
      } else if (actions.length && profile.experiences.length)
        extra.push({
          id: "work-history",
          label: "Work history",
          status: "unsupported",
          reason:
            "No recognizable inline experience row is visible. Open the employer editor manually; its save workflow needs a specific adapter.",
          required: false,
          source: "",
        });
      if (!entries.length) return { added, mutated };
    }
    if (entries.length > profile.experiences.length)
      blocked =
        "More visible job rows than structured jobs. Existing history was preserved.";
    if (blocked || !bindRecords(entries, profile.experiences))
      return { added, mutated };
    for (const [index, group] of entries.entries()) {
      const job = profile.experiences[bindings.get(group) ?? index];
      if (!job) break;
      for (const el of group.querySelectorAll("input,select,textarea")) {
        const key = fieldKey(el);
        if (
          job.current &&
          ["end", "end_year", "end_month"].includes(key) &&
          el.value
        )
          blocked =
            "The profile marks this job current, but the draft has an end date. Existing history was preserved.";
        if (!key) continue;
        if (key === "current") {
          if (el.checked && !job.current)
            blocked =
              "An existing current-role choice differs. History was preserved before adding rows.";
          continue;
        }
        if (!compatible(el, job))
          blocked =
            "A populated job field differs from the structured order or facts. History was preserved before adding rows; align the draft or update the profile.";
      }
    }
    if (blocked) return { added, mutated };
    while (entries.length < profile.experiences.length) {
      const section =
        historySectionForGroup(entries[0]) ||
        entries[0].parentElement.closest("section,[role=group]") ||
        entries[0].parentElement;
      const buttons = addExperienceButtons(section).filter(
        (button) => historyContainer(button) === section,
      );
      if (buttons.length !== 1) {
        extra.push({
          id: "work-history-add",
          label: "Additional work experiences",
          status: "unsupported",
          reason:
            "No unique inline Add experience button is associated with these rows. Add remaining jobs manually.",
          required: false,
          source: "",
        });
        break;
      }
      const count = entries.length;
      buttons[0].click();
      mutated = true;
      const until = performance.now() + 1500;
      while (
        performance.now() < until &&
        groups().length === count &&
        !hasVisibleDialog()
      )
        await new Promise((r) => setTimeout(r, 30));
      entries = groups();
      if (entries.length !== count + 1 || hasVisibleDialog()) {
        blocked =
          "The Add action did not create one recognizable inline job row. An employer-specific modal or asynchronous workflow needs manual completion; no Save action was attempted.";
        extra.push({
          id: "work-history-add",
          label: "Additional work experiences",
          status: "unsupported",
          reason: blocked,
          required: false,
          source: "",
        });
        break;
      }
      added++;
      if (!bindRecords(entries, profile.experiences)) break;
    }
    return { added, mutated };
  }
  globalThis.applicationHistory = {
    describe,
    presentationKey: (el) => presentationInfo(el)?.key || null,
    mapping,
    prepare,
    extra: () => extra,
  };
})();
