# Matching strategy: read the posting, then check the CV in context

## Findings that retired static matching

The rules matchers (v1, and the v2 additive scorer) recognised skills, disciplines and
work products with regular expressions and counted a requirement as met when any of
its recognised terms also appeared in the profile. Two production cases showed the
structural limits:

- **Context-blind terms.** A clause about corrosion-protection training was "covered"
  because the profile mentioned *Schulungen* in a SaaS support role; *Korrosionsschutz,
  galvanische Systeme, Lacksysteme* were unknown to the vocabulary and therefore ignored
  instead of counted as missing. Two such clauses produced requirement coverage 0.98.
- **Benefits as requirements.** Without a recognised "Wir bieten" heading, lines such as
  "Weihnachts- und Urlaubsgeld … 30 Tage Urlaub … strukturierte Einarbeitung" were scored
  as qualifications, and the offered *Einarbeitung* matched the profile skill *Onboarding*.

More vocabulary would not fix either: the vocabulary is bounded, and the decision
whether a CV line shows a requirement depends on the field it comes from.

## Decision

Match locally with two small models (see `docs/local-models.md`):

1. A local LLM (Qwen3.5-4B) reads each posting once and lists its occupational field,
   industry, duties and requirements, each as a verbatim quote of a numbered line. A
   grammar fixes the output format, ungrounded quotes are dropped, and benefits,
   company descriptions and application instructions are never items.
2. A local reranker (Qwen3-Reranker-0.6B) scores each duty and requirement against the
   closest CV lines (pre-selected with the existing E5 model), with the job's occupation
   and industry in the query. Role and field fit are scored the same way.
3. `matching_v3` combines duties, mandatory and preferred requirements, role and field
   into an exact additive ledger, applies bounded penalties and a monotone calibration
   fitted on the DEV split. The existing eligibility gates still hide a job only for an
   explicit incompatibility cited from both sides.

There is no remote AI scoring and no subscription quota involved; AI requirement
grounding for drafting stays inside **Prepare inside Radius**.

Jobs the models have not analysed yet are pending and stay out of Journey. In-area
jobs are analysed first. Edits keep the previous analysis visible until replaced.

## Trade-offs

- **Throughput.** On four CPU cores the LLM needs about 25–50 s per posting and the
  reranker about 15–25 s per profile/posting pair. Priority ordering keeps the user's
  area current; the long tail of the inventory follows in the background.
- **Model errors.** A small model can mislabel an item (duty versus requirement) or miss
  a split. Grounding prevents invented text; the evaluation set measures the rest.
- **Reproducibility.** Results depend on pinned model revisions; the version of every
  stored analysis and evidence row names them, and CI evaluates recorded outputs.

## Evaluation

`tests/eval` scores the recorded model outputs for the public fictional set (and the
private real-CV set when present). Report false strong matches, missed suitable jobs,
DE/EN parity and the hide gates; regenerate the recordings whenever a model, the
prompt or the evidence queries change.
