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
      "employer",
      "company",
      "company name",
      "arbeitgeber",
      "unternehmen",
    ],
    title: [
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
  function fieldKey(el) {
    const text = norm(
      el.getAttribute("aria-label") ||
        [...(el.labels || [])]
          .map((l) => {
            const c = l.cloneNode(true);
            c.querySelectorAll("input,select,textarea").forEach((n) =>
              n.remove(),
            );
            return c.textContent;
          })
          .join(" "),
    ).replace(/ (required|optional|erforderlich)$/, "");
    return (
      Object.keys(aliases).find((key) => aliases[key].includes(text)) || null
    );
  }
  function groups() {
    return [...document.querySelectorAll("fieldset")].filter((group) => {
      if (
        !visible(group) ||
        group.closest("[data-experience]") ||
        group.closest("dialog,[role=dialog]")
      )
        return false;
      const legend = norm(
        group.querySelector(":scope > legend")?.textContent || "",
      );
      if (!/(experience|employment|work history|beruf|tatigkeit)/.test(legend))
        return false;
      const fields = [
        ...group.querySelectorAll("input,textarea,select"),
      ].filter((el) => el.closest("fieldset") === group);
      return (
        fields.some((el) => fieldKey(el) === "company") &&
        fields.some((el) => fieldKey(el) === "title")
      );
    });
  }
  function describe(el) {
    const group = el.closest("fieldset");
    if (!group) return null;
    const entries = groups(),
      index = entries.indexOf(group);
    if (index < 0) return null;
    return { group, index, key: fieldKey(el) };
  }
  let blocked = "";
  let extra = [];
  function mapping(el, profile) {
    const info = describe(el);
    if (!info) return null;
    const entry = profile.experiences[info.index];
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
  async function prepare(profile) {
    blocked = "";
    extra = [];
    let added = 0,
      mutated = false;
    let entries = groups();
    if (!entries.length) {
      const actions = [...document.querySelectorAll("button")].filter(
        (b) =>
          visible(b) &&
          /^(add (work )?experience|add employment|berufserfahrung hinzufugen)$/.test(
            norm(b.textContent),
          ),
      );
      if (actions.length && profile.experiences.length)
        extra.push({
          id: "work-history",
          label: "Work history",
          status: "unsupported",
          reason:
            "No recognizable inline experience row is visible. Open the employer editor manually; its save workflow needs a specific adapter.",
          required: false,
          source: "",
        });
      return { added, mutated };
    }
    if (entries.length > profile.experiences.length)
      blocked =
        "More visible job rows than structured jobs. Existing history was preserved.";
    for (const [index, group] of entries.entries()) {
      const job = profile.experiences[index];
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
        const mapped = mapping(el, profile),
          expected = mapped?.value;
        if (
          !el.value ||
          expected === "" ||
          expected === undefined ||
          mapped.notApplicable
        )
          continue;
        const monthMatch =
          key.endsWith("_month") &&
          /^\d{1,2}$/.test(el.value) &&
          Number(el.value) === Number(expected);
        if (el.value !== String(expected) && !monthMatch)
          blocked =
            "A populated job field differs from the structured order or facts. History was preserved before adding rows; align the draft or update the profile.";
      }
    }
    if (blocked) return { added, mutated };
    while (entries.length < profile.experiences.length) {
      const section =
        entries[0].closest("section,[role=group]") || entries[0].parentElement;
      const buttons = [...section.querySelectorAll("button")].filter(
        (b) =>
          visible(b) &&
          b.type === "button" &&
          !b.disabled &&
          /^(add (work )?experience|add employment|berufserfahrung hinzufugen|weitere berufserfahrung)$/.test(
            norm(b.textContent),
          ),
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
        !document.querySelector("dialog[open],[role=dialog]")
      )
        await new Promise((r) => setTimeout(r, 30));
      entries = groups();
      if (
        entries.length !== count + 1 ||
        document.querySelector("dialog[open],[role=dialog]")
      ) {
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
    }
    return { added, mutated };
  }
  globalThis.applicationHistory = {
    describe,
    mapping,
    prepare,
    extra: () => extra,
  };
})();
