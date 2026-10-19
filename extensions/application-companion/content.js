// Injected into Chrome's isolated world only after the user invokes the action.
// Re-injection keeps the existing lock; no profile is retained on this object.
(() => {
  if (globalThis.applicationCompanion?.version === 3) return;
  let busy = false;
  const hosts = new Set([
    "job-boards.greenhouse.io",
    "job-boards.eu.greenhouse.io",
    "boards.greenhouse.io",
    "boards.eu.greenhouse.io",
  ]);
  const visible = (el) =>
    el && el.getClientRects().length > 0 && !el.closest("[hidden]");
  const all = (scope, selector) =>
    [...scope.querySelectorAll(selector)].filter(visible);
  const one = (scope, selector, path) => {
    const matches = all(scope, selector);
    if (matches.length !== 1)
      throw new Error(
        `${path}: expected one visible field. Review this section.`,
      );
    return matches[0];
  };

  function inspect(linkedApplication = false) {
    if (globalThis.applicationFields?.hasAuthenticationControls())
      return {
        supported: false,
        reason:
          "Finish signing in, then open the application form to fill its fields.",
      };
    if (globalThis.radiusApplicationSource?.read())
      return {
        supported: true,
        mode: "import",
        adapter: "radius-source",
        name: "Prepared Radius application",
        scope: "Connect this saved ChatGPT result to your existing CV profile",
      };
    const local =
      location.protocol === "file:" ||
      ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
    if (
      location.protocol === "https:" &&
      hosts.has(location.hostname) &&
      /^\/[^/]+\/jobs\/[0-9]+\/?$/.test(location.pathname)
    ) {
      if (
        ["first_name", "last_name", "email"].every(
          (id) =>
            all(document, `input#${id}`).length === 1 &&
            globalThis.applicationFields.acceptsControl(
              document.querySelector(`input#${id}`),
            ),
        )
      ) {
        return {
          supported: true,
          adapter: "greenhouse-contact",
          name: "Greenhouse application",
          scope:
            "Known facts and exact job-linked answers; other controls reported for review",
        };
      }
      if (!linkedApplication || !globalThis.applicationFields?.supports(true))
        return {
          supported: false,
          reason:
            "The Greenhouse contact fields are not visible yet. Open the application form, then try again.",
        };
    }
    if (
      (location.protocol === "https:" || local) &&
      globalThis.applicationFields?.supports(linkedApplication)
    )
      return {
        supported: true,
        adapter: "best-effort",
        name: "Application form (best effort)",
        scope:
          "Recognized English/German fields; uncertain controls remain for review",
      };
    return {
      supported: false,
      reason:
        "No recognizable application fields were found in the top document. Embedded forms and login steps may need to be opened directly.",
    };
  }

  async function run(profile) {
    if (
      (profile.page_url && location.href !== profile.page_url) ||
      (profile.page_guard &&
        globalThis.applicationPageContext?.identity() !== profile.page_guard)
    ) {
      return {
        status: "needs_review",
        error:
          "The application form changed. Confirm the intended job on this page again.",
        filled: 0,
        unchanged: 0,
        experiences_added: 0,
        partial_fill: false,
        fields: [],
        unresolved: [],
      };
    }
    if (busy)
      return { status: "busy", error: "This page is already being filled." };
    const scoped = globalThis.applicationFields.sameApplication(
      profile.application?.url,
    );
    const info = inspect(scoped);
    if (!info.supported) return { status: "unsupported", error: info.reason };
    busy = true;
    profile = {
      ...profile,
      experiences: profile.experiences.map((entry) => {
        const tailored = scoped
          ? profile.application?.experience_bullets?.find(
              (group) => group.experience_id === entry.id,
            )
          : null;
        return tailored
          ? {
              ...entry,
              description: tailored.bullets.join("\n"),
              description_source: true,
            }
          : entry;
      }),
    };
    const started = performance.now();
    const before = globalThis.applicationFields.snapshot(scoped);
    const report = {
      adapter: info.adapter,
      status: "ready_for_review",
      filled: 0,
      unchanged: 0,
      experiences_added: 0,
      partial_fill: false,
      error: null,
      needs_review: [
        "Salary and availability",
        "Work authorization and sponsorship",
        "Consent choices",
        "Attachments",
        "Final review and submission",
      ],
    };
    let mutated = false;
    const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
    const fill = async (getControl, value, path) => {
      if (!value) {
        report.needs_review.push(path);
        return;
      }
      const control = getControl();
      if (control.disabled || control.readOnly)
        throw new Error(`${path}: the field cannot be edited.`);
      if (control.value === value) {
        report.unchanged++;
        return;
      }
      if (control.value)
        throw new Error(
          `${path}: an existing value differs and was preserved.`,
        );
      const proto =
        control instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      control.focus();
      setter.call(control, value);
      mutated = true;
      report.filled++;
      control.dispatchEvent(
        new Event("input", { bubbles: true, composed: true }),
      );
      control.dispatchEvent(
        new Event("change", { bubbles: true, composed: true }),
      );
      control.blur();
      await tick();
      if (getControl().value !== value)
        throw new Error(`${path}: the page did not accept the supplied value.`);
    };
    try {
      const history = await globalThis.applicationHistory.prepare(profile);
      report.experiences_added = history.added;
      mutated = history.mutated;
      if (info.adapter === "greenhouse-contact") {
        // Explicit known field IDs only. No fuzzy matching of legal/custom questions.
        for (const key of ["first_name", "last_name", "email", "phone"]) {
          if (key === "phone" && all(document, "input#phone").length !== 1) {
            if (profile.phone) report.needs_review.push("Phone");
            continue;
          }
          await fill(
            () => one(document, `input#${key}`, key),
            profile[key],
            key,
          );
        }
        report.needs_review.push(
          "Work history",
          "Education",
          "LinkedIn and other employer questions",
        );
      }
    } catch (error) {
      report.status = "needs_review";
      report.partial_fill = mutated;
      report.error = error.message;
    } finally {
      const details = await globalThis.applicationFields.run(profile, {
        fill: !report.error,
        before,
      });
      details.fields.push(...globalThis.applicationHistory.extra());
      report.fields = details.fields;
      report.filled += details.filled;
      report.unresolved = details.fields.filter(
        (field) =>
          !["filled", "already_filled", "provided_by_user"].includes(
            field.status,
          ),
      );
      if (details.mutated && report.unresolved.length)
        report.partial_fill = true;
      report.needs_review = report.unresolved.map(
        (field) => `${field.label}: ${field.reason}`,
      );
      if (report.unresolved.length && report.status === "ready_for_review")
        report.status = "partial";
      if (report.status === "partial" && report.filled)
        report.partial_fill = true;
      report.elapsed_seconds = Number(
        ((performance.now() - started) / 1000).toFixed(3),
      );
      report.needs_review = [...new Set(report.needs_review)];
      busy = false;
    }
    return report;
  }
  globalThis.applicationCompanion = { version: 3, inspect, run };
})();
