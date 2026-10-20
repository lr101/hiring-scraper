# Real German company forms: offline DOM replay

This page records the original baseline. See [implemented improvements and
updated results](improvements.md) for the subsequent production changes.

Captured on **2026-10-20**, without changing the production autofill runtime.
This is a separate, previously unseen sample after the synthetic study.

## What was actually tested

Nine German company pages were discovered with web search and loaded using
**one initial document GET per company**. No redirect was needed. Acquisition
used Python's HTML downloader: it did not execute site scripts, load images or
stylesheets, press Apply/Next, log in, upload anything, or send applicant values.
[Capture log](capture-log.json) records those nine initial requests and raw
response hashes. Search-provider traffic is separate from this capture log.

Eight pages exposed a form in initial HTML. Lilli Mobility Services did not;
it is excluded. Of the eight snapshots, compact service stores its questions
in Alpine `<template>` elements. Its 55 template controls never render without
site JavaScript, so they are **unrendered, not scored as passes or failures**.
Seven company forms actually exposed scored questions in the offline replay.
Two additional fill targets were hidden in the captured initial state and
are reported separately rather than called missed fills.

Snapshots retain real field names, IDs, labels, nesting, input types, inline
styles and the original document's inline CSS. They remove site scripts,
external resources, network targets, event handlers and hidden token values.
HTML snapshots are losslessly compressed. The runner injects the unchanged
production `history.js` and `best-effort.js` files; it does not exercise the MV3
background worker, permission prompt or profile-sync bridge. `data-real-field`
is the only added field-identification attribute and supplies no matching hints. External
CSS, framework hydration, server validation and dynamic next/add/save actions
are **not** exercised. This is genuine form-DOM testing, not live ATS
end-to-end verification.

## Results

| Company | Correct fills | Missed fills | Format errors | Correctly preserved |
| --- | ---: | ---: | ---: | ---: |
| [Ammerländer Versicherung](https://www.ammerlaender-versicherung.de/bewerbung) | 6 | 4 | 1 | 20 |
| [ARGE GRÜN](https://www.arge-gruen.de/bewerbung) | 3 | 4 | 0 | 14 |
| [Peterhoff-Gruppe](https://www.peterhoff.de/karriere/online-bewerbung/) | 6 | 2 | 1 | 15 |
| [e-unit](https://jobs.e-unit.de/bewerbungsformular/) | 4 | 2 | 0 | 8 |
| [Wütschner Fahrzeugteile](https://www.wuetschner.com/unternehmen/karriere/bewerbung.html) | 1 | 15 | 0 | 11 |
| [Grasenhiller](https://www.karriere-grasenhiller.de/bewerbungsformular) | 4 | 0 | 1 | 19 |
| [SZM Moosburger](https://www.sz-moosburger.de/karriere) | 4 | 2 | 0 | 13 |
| compact service: unhydrated template only | — | — | — | 1 |
| **Total scored: 161 fields** | **28** | **29** | **3** | **101** |

Of 59 visible targets answerable from saved facts, 28 filled correctly, 29
stayed empty and two received a partial/wrong format. A third format error
changed a street-only field that our explicit policy required preserving
because the profile only stores a combined street/house-number line.

**Experience coverage was 0/18 visible answerable fields.** Contact coverage
was 28/41. Experience is counted separately: successful contact fills do not
make the work-history section a success. Seven more experience targets were
in compact's unrendered template; one free-text history was initially hidden.
These are not in the 18-field denominator.

The three questionable writes were inspected against the original markup:

- Peterhoff has `Name` beside `Vorname`. Autofill inserted the complete name
  into the surname field.
- Grasenhiller's `Adresse` placeholder asks for street, postal code and city.
  Autofill inserted only the street/house-number line.
- Ammerländer has separate street and house-number controls. Autofill put
  the combined street/house-number line into the street-only control.

An early extraction omitted an original inline rule hiding ARGE GRÜN's
honeypot and therefore produced a false-positive fill. That extraction was
corrected before these final results; the final replay preserves the original
rule and leaves the honeypot empty. No production alias was adjusted to these
examples, and production runtime hashes still match the prior synthetic run.

## Best findings for the next implementation

1. Recognize career headings such as `Beruflicher Werdegang`, including h4
   headings, and labels such as `Tätigkeitsbezeichnung`. Ammerländer also has
   labels whose `for` IDs do not exist on their controls, so narrowly local
   label association matters. Recognizing a label alone is not enough to
   automate its row's add/save workflow.
2. Support deterministic presentations of saved work records: separate
   employer/role controls, combined employer-and-duties fields, individual
   one-line history entries, and a complete free-text work-history section.
   Formatting should copy confirmed facts and retain known date precision.
   Several real forms are not the repeated semantic card structure used in
   the synthetic examples.
3. Model split and combined addresses explicitly. A street-only field, an
   address line and a full postal address require different values. Do not
   invent exact days when the user saved only a year or month.
4. Use sibling questions to resolve `Name` plus `Vorname`, and strip bounded
   required markers such as `Pflichtfeld` before matching. Wütschner's labels
   exposed multiple misses from this prefix.
5. Test framework hydration and multi-step widgets separately only when an
   allowed offline recording contains their required assets. compact remains
   a documented gap; its templates were not artificially made visible.

## Network isolation and reproducibility

Every replay starts with an offline Chromium context, blocked service workers,
an abort-all request route and a dead loopback proxy. It also denies fetch,
XHR, beacon, WebSocket and EventSource, overrides direct form submission and
prevents submit events. A self-check exercises all five transports, two direct
submission paths and an independent browser-route abort. These probes are
intercepted locally and never reach an external server.

The company-form replays recorded **zero transport attempts, zero browser
request attempts, zero submission attempts and zero requests allowed**. Repeated
fills were stable in all eight snapshots. These counts exclude the deliberately
blocked isolation self-check probes, which are reported separately. Fictional
profile values are introduced only after the browser is offline.

```sh
# Uses the checked-in captures; never fetches company pages.
.venv/bin/python experiments/evaluate_real_autofill.py
```

[Independent expectations](expectations.json) were written before replay.
They explicitly specify contact values and source facts required for work
history. Free-text history accepts any formatting containing the specified
facts. Unlisted controls must retain their initial values, including consent,
captcha, unknown qualifications and upload fields. Combined date fields that
explicitly demand unknown exact days are preserved for manual completion.

[Results](results.json) contain every inspected value, runtime reports,
isolation checks and SHA-256 hashes. [Sources](sources.json) contain original
control metadata, URLs, capture timestamps and snapshot hashes. Replaying the
saved snapshots requires no network. This small convenience sample does not
estimate overall accuracy across German employers.
