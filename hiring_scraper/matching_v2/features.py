"""Interpretable, domain-aware feature extraction and bounded soft penalties for v2.

All features lie in [0.0, 1.0]. Language and work-authorisation/visa clauses are
excluded from content features so ablating language/visa requirements cannot leak
into duty, requirement, skill, or domain scores. Every feature and penalty carries
source citations for the additive explanation ledger.
"""
from __future__ import annotations

from functools import lru_cache
import math
import re
from typing import Any

from hiring_scraper import task_alignment as _ta
from hiring_scraper.duty_evidence import analyze_duty_units, assess_duties
from hiring_scraper.matching import (
    OPTIONAL,
    REQUIREMENT,
    _scoped_experience_rows,
    plain_text,
)
from hiring_scraper.matching_v2.gates import (
    is_language_or_visa_clause,
    job_requirement_texts,
    parse_job_requirement_texts,
    strip_language_or_visa_subclauses,
)
from hiring_scraper.matching_v2.profile_cache import (
    _align_tasks_with_facts,
    _compute_role_alignment,
    get_profile_skill_context,
    precompute_profile,
)
from hiring_scraper.matching_v2.taxonomy import (
    ADJACENT_DISCIPLINES,
    ALL_ROLE_GROUPS,
    ALL_SKILLS,
    DISCIPLINES,
    EXTRA_ROLE_GROUPS,
    EXTRA_SKILLS,
    GENERIC_SKILLS,
    _FORMAL_LICENSE_RE,
    _SKILL_CASE_TRANSLATION,
    _SKILL_COMBINED,
    _SKILL_LITERALS,
    _SPECIALIST_DUTY_PATTERNS,
    _WORK_PRODUCTS,
    _cached_pattern,
)
from hiring_scraper.matching_v2.text_signals import (
    _detect_disciplines,
    _extract_content_tokens,
    _tenure_minimums,
    classify_skill_mention,
    detect_title_seniority,
    is_company_experience_claim,
    split_or_arms,
)
from hiring_scraper.matching_v2.weights import SOFT_PENALTIES
from hiring_scraper.requirements import (
    DEGREE,
    clause_key,
    education_evidence,
    is_source_instruction,
    qualification_rows,
    requirement_polarity,
)

_SUBSTANTIVE_WORD_RE = re.compile(r'[A-Za-zÄÖÜäöüß]{3,}')

# ---------------------------------------------------------------------------
# Main feature extraction entry point
# ---------------------------------------------------------------------------

# Identifiers in otherwise identical duty lines do not add another work product.
_DUTY_REFERENCE_SUFFIX = re.compile(r'(?<![\w.+-])\d+([.!?]?)$')


@lru_cache(maxsize=256)
def _analyze_job(title: str, parsed_texts: tuple) -> dict[str, Any]:
    """Analyze bounded scoring evidence once per hashable posting content and title."""
    # Strip ONLY language/visa sub-clauses so mixed sentences (e.g. "You must speak fluent
    # German and have advanced Kubernetes experience") preserve sibling requirement evidence
    # while ablating language/visa clauses has ZERO leakage into content features.
    filtered_sentences: list[tuple[str, str, str]] = []  # (source, cleaned_sentence, scope)
    seen: set[tuple[str, str]] = set()
    remaining = 24_000
    for source, force_req, sentences in parsed_texts:
        # Skip dedicated language/authorization metadata blocks from content features.
        if force_req:
            continue
        for sentence, scope in sentences:
            if remaining <= 0 or len(filtered_sentences) >= 160:
                break
            if is_source_instruction(sentence):
                continue
            cleaned = strip_language_or_visa_subclauses(sentence)
            if not cleaned:
                continue
            normalized = ' '.join(cleaned.casefold().split())
            if scope == 'duties':
                normalized = _DUTY_REFERENCE_SUFFIX.sub(r'\1', normalized).rstrip(' .!?')
            key = (normalized, scope)
            if key in seen:
                continue
            seen.add(key)
            cleaned = cleaned[:min(remaining, 1500)]
            remaining -= len(cleaned)
            filtered_sentences.append((source, cleaned, scope))

    filtered_body_texts = [sent for _, sent, scope in filtered_sentences if scope != 'benefits']
    filtered_body = '\n'.join(filtered_body_texts)
    duty_sentences = [
        (src, sent, scope) for src, sent, scope in filtered_sentences
        if src == 'description' or scope == 'duties'
    ]
    job = {'title': title}
    task_units = _ta.task_units(job, sentences=duty_sentences, deduplicate_deliverables=True)
    duty_analysis = analyze_duty_units(
        job, task_units, [(sent, scope) for src, sent, scope in duty_sentences if src == 'description'],
    )
    tasks = tuple((task, _ta._semantic_objects(task['job_quote'])) for task in task_units)

    combined_text = f'{title}\n{filtered_body}'
    normalized_text = combined_text.translate(_SKILL_CASE_TRANSLATION).lower()
    candidate_skills = tuple(
        skill for skill, literals in _SKILL_LITERALS.items()
        if any(literal in normalized_text for literal in literals) and _SKILL_COMBINED[skill].search(combined_text)
    )
    mentions = [
        (skill, 'title', title[:180], 'required', 1.3)
        for skill in candidate_skills if _SKILL_COMBINED[skill].search(title)
    ]
    for src, sentence, scope in filtered_sentences:
        for skill in candidate_skills:
            if match := _SKILL_COMBINED[skill].search(sentence):
                kind = classify_skill_mention(sentence, match.start(), match.end(), scope)
                if kind != 'incidental':
                    weight = 1.0 if kind == 'required' else 0.75 if kind == 'duty' else 0.35
                    mentions.append((skill, src, sentence[:200], kind, weight))

    job_product_quotes: dict[str, str] = {}
    if filtered_body:
        for wp, (pat, _) in _WORK_PRODUCTS.items():
            m_job = pat.search(filtered_body)
            if m_job:
                s0 = max(0, m_job.start() - 35)
                s1 = min(len(filtered_body), m_job.end() + 55)
                job_product_quotes[wp] = ' '.join(filtered_body[s0:s1].split())

    qual_rows: list[dict[str, Any]] = []
    for source, sentence, scope in filtered_sentences:
        if scope in {'optional', 'benefits'}:
            continue
        if scope == 'requirements' or REQUIREMENT.search(sentence):
            qual_rows.extend(qualification_rows(sentence, source))

    return {
        'sentences': filtered_sentences,
        'body': filtered_body,
        'content_tokens': _extract_content_tokens(filtered_body),
        'disciplines': _detect_disciplines(title, filtered_body_texts),
        'specialist_duties': tuple(d for d, pattern in _SPECIALIST_DUTY_PATTERNS if pattern.search(filtered_body)),
        'task_units': tasks,
        'duties': duty_analysis,
        'combined_text': combined_text,
        'normalized_text': normalized_text,
        'candidate_skills': candidate_skills,
        'skill_mentions': mentions,
        'product_quotes': job_product_quotes,
        'qualification_rows': qual_rows,
        'tenure_minimums': _tenure_minimums(filtered_sentences),
        'substantive_word_count': len(_SUBSTANTIVE_WORD_RE.findall(filtered_body)),
    }


@lru_cache(maxsize=1024)
def _specialist_disciplines_for_quote(quote: str) -> frozenset[str]:
    return frozenset(
        d for d, spec in DISCIPLINES.items()
        if spec['specialist'] and (spec['title'].search(quote) or spec['body'].search(quote))
    )


def extract_features(
    job: dict,
    profile: dict,
    enriched: dict,
    gate_result: dict,
    *,
    semantic: dict | None = None,
    profile_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute all interpretable [0,1] features, bounded soft penalties, and citations."""
    pc = profile_cache if profile_cache is not None else precompute_profile(profile)

    title = plain_text(job.get('title'))
    parsed_texts = gate_result.get('parsed_texts')
    if parsed_texts is None:
        parsed_texts = parse_job_requirement_texts(job_requirement_texts(job))
    analysis = _analyze_job(title, parsed_texts)
    filtered_sentences = analysis['sentences']
    filtered_body = analysis['body']

    sources = pc['sources']
    duty_sources = pc['duty_sources']
    duty_references = pc['duty_references']
    profile_full_text = pc['profile_full_text']
    has_profile_evidence = pc['has_profile_evidence']
    prof_disc: frozenset[str] = pc['prof_disc']
    prof_disc_strength: dict[str, int] = pc['prof_disc_strength']
    prof_products: frozenset[str] = pc['prof_products']
    profile_skills_norm: frozenset[str] = pc['profile_skills_norm']
    profile_evidenced_skills = set(pc['profile_evidenced_skills'])
    custom_profile_skills: tuple[tuple[str, str], ...] = pc['custom_profile_skills']
    prof_content_tokens: frozenset[str] = pc['content_tokens']

    # Continuous stopword-filtered content-token overlap (language/visa-free).
    job_content_tokens = analysis['content_tokens']
    if has_profile_evidence and job_content_tokens and prof_content_tokens:
        tok_hits = len(job_content_tokens & prof_content_tokens)
        raw_lex = tok_hits / max(1.0, min(len(job_content_tokens), 45) + 8.0)
        content_overlap = min(1.0, 1.0 - math.exp(-3.2 * raw_lex))
    else:
        content_overlap = 0.0

    # -----------------------------------------------------------------------
    # 1. Disciplines: domain_alignment & domain_mismatch (smoothly continuous)
    # -----------------------------------------------------------------------
    job_title_disc, job_body_disc, job_disc_counts = analysis['disciplines']
    job_disc = job_title_disc | job_body_disc

    if has_profile_evidence and job_disc and prof_disc:
        weighted_total = 0.0
        weighted_aligned = 0.0
        for d in job_disc:
            depth_w = 1.0 - 0.35 / math.sqrt(job_disc_counts.get(d, 1) + 1)
            w = (2.5 if d in job_title_disc else 1.0) * depth_w
            weighted_total += w
            if d in prof_disc:
                prof_str = 0.78 + 0.22 * (min(10, prof_disc_strength.get(d, 4)) / 10.0)
                weighted_aligned += w * prof_str
            elif ADJACENT_DISCIPLINES.get(d, set()) & prof_disc:
                weighted_aligned += 0.42 * w
        domain_alignment = weighted_aligned / max(1e-6, weighted_total)
        if weighted_aligned == 0.0:
            domain_alignment = 0.06 * content_overlap
    elif not has_profile_evidence:
        domain_alignment = 0.0
    elif not job_disc:
        domain_alignment = 0.16 + 0.12 * content_overlap
    else:
        domain_alignment = 0.06 + 0.06 * content_overlap

    # Specialist domain mismatch: smooth depth-based curve to avoid discrete pile-up at 1.0.
    uncovered_specialist: list[str] = []
    specialist_mismatch_num = 0.0
    specialist_mismatch_den = 0.0
    for d in job_disc:
        if not DISCIPLINES[d]['specialist']:
            continue
        w = 2.5 if d in job_title_disc else 1.0
        specialist_mismatch_den += w
        if d in prof_disc:
            continue
        if ADJACENT_DISCIPLINES.get(d, set()) & prof_disc:
            specialist_mismatch_num += 0.45 * w
        else:
            specialist_mismatch_num += w
            uncovered_specialist.append(d)

    specialist_duty_hits = 0
    for d in analysis['specialist_duties']:
        if d not in prof_disc:
            specialist_mismatch_den += 1.5
            specialist_duty_hits += 1
            if ADJACENT_DISCIPLINES.get(d, set()) & prof_disc:
                specialist_mismatch_num += 0.6
            else:
                specialist_mismatch_num += 1.5
                if d not in uncovered_specialist:
                    uncovered_specialist.append(d)

    if specialist_mismatch_den > 0 and specialist_mismatch_num > 0:
        depth_bonus = 0.14 * sum(min(6, job_disc_counts.get(d, 1)) for d in uncovered_specialist)
        raw_mismatch = specialist_mismatch_num + depth_bonus + 0.20 * specialist_duty_hits
        ratio_floor = specialist_mismatch_num / max(2.5, specialist_mismatch_den)
        smooth_depth = raw_mismatch / (raw_mismatch + 1.45)
        domain_mismatch = min(0.98, 0.45 * ratio_floor + 0.55 * smooth_depth)
    elif specialist_mismatch_den > 0:
        domain_mismatch = 0.0
    else:
        if job_title_disc and prof_disc and not (job_title_disc & prof_disc):
            has_adj = any(ADJACENT_DISCIPLINES.get(d, set()) & prof_disc for d in job_title_disc)
            domain_mismatch = (0.18 if has_adj else 0.62) * (1.0 - 0.15 * content_overlap)
        elif job_body_disc and prof_disc and not (job_disc & prof_disc):
            has_adj = any(ADJACENT_DISCIPLINES.get(d, set()) & prof_disc for d in job_body_disc)
            domain_mismatch = (0.14 if has_adj else 0.48) * (1.0 - 0.15 * content_overlap)
        else:
            domain_mismatch = 0.0

    domain_agreement_factor = max(0.15, min(1.0, 1.0 - 0.80 * domain_mismatch))

    # -----------------------------------------------------------------------
    # 2. Duty coverage (domain-aware: assess_duties + cached align_tasks + work products)
    # -----------------------------------------------------------------------
    task_alignment = _align_tasks_with_facts(analysis['task_units'], pc['task_facts'])
    duty_evidence = assess_duties(job, duty_sources, task_alignment, analysis=analysis['duties'])

    # Re-evaluate assess_duties rows with domain awareness (reading real 'credit' field).
    duty_rows = duty_evidence.get('duties') or []
    adjusted_duty_scores: list[float] = []
    best_duty_quote = ''
    best_duty_psrc = ''
    best_duty_pquote = ''
    best_duty_s = -1.0
    for row in duty_rows:
        # Attach profile duty reference if available (v1 parity).
        ev_src = str(row.get('evidence_source') or '')
        if ev_src in duty_references and 'evidence_reference' not in row:
            row['evidence_reference'] = duty_references[ev_src]
        jq = str(row.get('job_quote') or '')
        if is_language_or_visa_clause(jq):
            continue
        raw_s = float(
            row.get('credit')
            if row.get('credit') is not None
            else (row.get('score') or 0.0)
        )
        row_disc = _specialist_disciplines_for_quote(jq)
        uncovered_row = row_disc - prof_disc
        if uncovered_row:
            adj_s = min(raw_s, 0.20)
        else:
            adj_s = raw_s * (0.30 + 0.70 * domain_agreement_factor)
        adjusted_duty_scores.append(adj_s)
        if adj_s > best_duty_s and row.get('evidence_quote'):
            best_duty_s = adj_s
            best_duty_quote = jq[:220]
            best_duty_psrc = ev_src
            best_duty_pquote = str(row.get('evidence_quote') or '')[:220]

    structured_duty_cov = (
        sum(adjusted_duty_scores) / len(adjusted_duty_scores)
        if adjusted_duty_scores
        else None
    )

    # Bilingual work-product coverage across filtered job body vs cached profile products.
    job_product_quotes = analysis['product_quotes']
    job_products = job_product_quotes.keys()

    wp_cov: float | None = None
    if job_products and has_profile_evidence:
        wp_num = 0.0
        for wp in job_products:
            _, wp_discs = _WORK_PRODUCTS[wp]
            if wp in prof_products:
                is_wp_specialist = all(DISCIPLINES[d]['specialist'] for d in wp_discs if d in DISCIPLINES)
                if is_wp_specialist and not (wp_discs & prof_disc):
                    wp_num += 0.25
                else:
                    wp_num += 1.0 * (0.40 + 0.60 * domain_agreement_factor)
            elif wp_discs & prof_disc:
                wp_num += 0.30
        wp_cov = wp_num / len(job_products)
    elif job_products:
        wp_cov = 0.0

    if not has_profile_evidence:
        duty_coverage = 0.0
    else:
        if structured_duty_cov is not None and wp_cov is not None:
            core_duty = 0.55 * structured_duty_cov + 0.45 * wp_cov
        elif structured_duty_cov is not None:
            core_duty = structured_duty_cov
        elif wp_cov is not None:
            core_duty = wp_cov
        else:
            legacy_task_score = task_alignment['coverage']
            core_duty = legacy_task_score * domain_agreement_factor
        mod_lex = content_overlap * (0.35 + 0.65 * domain_agreement_factor)
        duty_coverage = 0.88 * core_duty + 0.12 * mod_lex

    duty_coverage = max(0.0, min(1.0, duty_coverage))
    if not best_duty_quote and job_products:
        matched_wps = sorted(job_products & prof_products)
        if matched_wps:
            best_duty_quote = job_product_quotes.get(matched_wps[0], '')
            best_duty_psrc = 'profile.cv_text'
            best_duty_pquote = ', '.join(matched_wps[:4])

    # -----------------------------------------------------------------------
    # 3. Substantive skill overlap (fast pre-filtered scan; incidental weight = 0)
    # -----------------------------------------------------------------------
    job_skill_weights: dict[str, float] = {}
    job_skill_quotes: dict[str, str] = {}
    job_skill_sources: dict[str, str] = {}
    job_skill_kinds: dict[str, str] = {}
    job_skill_required: set[str] = set()

    combined_job_text = analysis['combined_text']
    normalized_job_text = analysis['normalized_text']
    candidate_skills = analysis['candidate_skills']
    for skill, src, quote, kind, weight in analysis['skill_mentions']:
        if skill in GENERIC_SKILLS and src != 'title':
            weight *= 0.45 + 0.40 * domain_agreement_factor
        if weight > job_skill_weights.get(skill, 0.0):
            job_skill_weights[skill] = weight
            job_skill_quotes[skill] = quote
            job_skill_sources[skill] = src
            job_skill_kinds[skill] = kind
        if kind == 'required':
            job_skill_required.add(skill)

    # Match custom profile skills substantively against filtered posting text.
    # Keep the evidence shape consumed by the v1 job detail UI.
    custom_evidence: list[dict[str, Any]] = []
    for custom, custom_norm in custom_profile_skills:
        if custom_norm not in normalized_job_text:
            continue
        pat = _cached_pattern(custom)
        if not pat.search(combined_job_text):
            continue
        matched_custom = False
        if pat.search(title):
            job_skill_weights[custom] = max(job_skill_weights.get(custom, 0.0), 1.3)
            job_skill_quotes.setdefault(custom, title[:200])
            job_skill_sources.setdefault(custom, 'title')
            job_skill_kinds.setdefault(custom, 'required')
            job_skill_required.add(custom)
            profile_evidenced_skills.add(custom)
            custom_evidence.append({
                'name': custom,
                'source': 'title',
                'evidence': title[:200],
            })
            matched_custom = True
        if matched_custom:
            continue
        for src, sentence, scope in filtered_sentences:
            m = pat.search(sentence)
            if not m:
                continue
            kind = classify_skill_mention(sentence, m.start(), m.end(), scope)
            if kind == 'incidental':
                continue
            w = 1.0 if kind == 'required' else 0.75 if kind == 'duty' else 0.35
            job_skill_weights[custom] = max(job_skill_weights.get(custom, 0.0), w)
            job_skill_quotes.setdefault(custom, sentence[:200])
            job_skill_sources.setdefault(custom, src)
            job_skill_kinds.setdefault(custom, kind)
            if kind == 'required':
                job_skill_required.add(custom)
            profile_evidenced_skills.add(custom)
            custom_evidence.append({
                'name': custom,
                'source': src,
                'evidence': sentence[:200],
            })
            break

    matched_skills: list[str] = []
    missing_skills: list[str] = []
    skill_num = 0.0
    skill_den = 0.0
    for skill, w in sorted(job_skill_weights.items(), key=lambda kv: (-kv[1], kv[0])):
        skill_den += w
        if skill in profile_evidenced_skills:
            matched_skills.append(skill)
            credit = w * (domain_agreement_factor if skill in GENERIC_SKILLS else 1.0)
            skill_num += credit
        else:
            if w >= 0.7:
                missing_skills.append(skill)

    if not has_profile_evidence:
        skill_overlap = 0.0
    elif skill_den > 0:
        raw_ratio = skill_num / skill_den
        prior_strength = 0.8
        prior_val = 0.08 * domain_alignment
        skill_overlap = (skill_num + prior_strength * prior_val) / (skill_den + prior_strength)
        has_non_generic = any(sk not in GENERIC_SKILLS for sk in matched_skills)
        if len(matched_skills) >= 3 and raw_ratio >= 0.75 and (has_non_generic or domain_alignment >= 0.5):
            evidence_strength = skill_den / (skill_den + 0.20)
            skill_overlap = raw_ratio * evidence_strength
        skill_overlap *= 0.94 + 0.06 * content_overlap
    else:
        skill_overlap = 0.08 * domain_alignment + 0.12 * content_overlap
    skill_overlap = max(0.0, min(1.0, skill_overlap))

    # -----------------------------------------------------------------------
    # 4. Requirement coverage (deduplicated once via clause_key, skipping lang/visa)
    # -----------------------------------------------------------------------
    req_clauses_seen: set[str] = set()
    req_total = 0.0
    req_met = 0.0
    requirement_gaps: list[str] = []
    formal_prereq_gaps: list[dict[str, str]] = []
    formal_prereq_conditions: list[dict[str, Any]] = []
    requirement_observations: list[dict[str, Any]] = []
    seen_formal_keys: set[str] = set()

    edu_profile = pc['edu_profile']
    qual_rows = analysis['qualification_rows']

    for row in qual_rows:
        qtext = str(row.get('evidence') or row.get('quote') or row.get('value') or '').strip()
        qsource = str(row.get('source') or 'description')
        if not qtext or is_language_or_visa_clause(qtext) or is_company_experience_claim(qtext):
            continue
        if 'unspecified language level' in qtext.casefold():
            continue
        ckey = clause_key(qtext)
        if not ckey or ckey in req_clauses_seen:
            continue
        req_clauses_seen.add(ckey)

        req_total += 1.0
        arms = split_or_arms(qtext)
        arm_satisfied = False
        ev_src = ''
        ev_quote = ''
        is_degree = bool(DEGREE.search(qtext) or row.get('kind') == 'education')
        is_license = bool(_FORMAL_LICENSE_RE.search(qtext))

        for arm in arms:
            if DEGREE.search(arm):
                if evidence := education_evidence(row, edu_profile):
                    arm_satisfied = True
                    ev_src, ev_quote = evidence
                    break
            arm_skills = [
                sk for sk in candidate_skills
                if _SKILL_COMBINED[sk].search(arm)
            ]
            if arm_skills and any(sk in profile_evidenced_skills for sk in arm_skills):
                matched_arm_sk = [sk for sk in arm_skills if sk in profile_evidenced_skills]
                arm_satisfied = True
                ev_src = 'profile.skills'
                ev_quote = ', '.join(matched_arm_sk[:4])
                break
            arm_wps = [wp for wp, (pat, _) in _WORK_PRODUCTS.items() if pat.search(arm)]
            if arm_wps and any(wp in prof_products for wp in arm_wps):
                arm_satisfied = True
                ev_src = 'profile.cv_text'
                ev_quote = ', '.join(wp for wp in arm_wps if wp in prof_products)
                break
            arm_discs = [
                d for d, spec in DISCIPLINES.items()
                if spec['title'].search(arm) or spec['body'].search(arm)
            ]
            if arm_discs and any(d in prof_disc for d in arm_discs):
                arm_satisfied = True
                ev_src = 'profile.cv_text'
                ev_quote = ', '.join(d for d in arm_discs if d in prof_disc)
                break

        obs_kind = 'license' if is_license else 'education' if is_degree else str(row.get('kind') or 'qualification')
        if arm_satisfied:
            req_met += 1.0
            requirement_observations.append({
                'kind': obs_kind,
                'requirement': qtext[:200],
                'status': 'supported',
                'job_source': qsource,
                'job_quote': qtext[:240],
                'evidence_source': ev_src,
                'evidence_quote': ev_quote,
                'evidence_context': 'professional' if ev_src else 'unspecified',
                'explanation': f'Saved profile evidence aligns with requirement: {qtext[:140]}',
            })
            if (is_degree or is_license) and ckey not in seen_formal_keys:
                seen_formal_keys.add(ckey)
                formal_prereq_conditions.append({
                    'kind': 'license' if is_license else 'education',
                    'gate': 'qualification',
                    'status': 'pass',
                    'reason': f'Saved profile evidence covers stated {obs_kind}: {qtext[:160]}',
                    'detail': f'Saved profile evidence covers stated {obs_kind}: {qtext[:160]}',
                    'job_source': qsource,
                    'job_quote': qtext[:240],
                    'profile_source': ev_src,
                    'profile_quote': ev_quote,
                })
        else:
            requirement_gaps.append(qtext[:180])
            requirement_observations.append({
                'kind': obs_kind,
                'requirement': qtext[:200],
                'status': 'unknown',
                'job_source': qsource,
                'job_quote': qtext[:240],
                'evidence_source': '',
                'evidence_quote': '',
                'evidence_context': 'unspecified',
                'explanation': f'Required {obs_kind} is unverified in saved profile: {qtext[:140]}',
            })
            if (is_degree or is_license) and ckey not in seen_formal_keys:
                seen_formal_keys.add(ckey)
                fp_kind = 'license' if is_license else 'degree'
                formal_prereq_gaps.append({
                    'clause_key': ckey,
                    'quote': qtext[:200],
                    'kind': fp_kind,
                    'source': qsource,
                })
                formal_prereq_conditions.append({
                    'kind': 'license' if is_license else 'education',
                    'gate': 'qualification',
                    'status': 'unknown',
                    'reason': f'Required {fp_kind} is unverified: {qtext[:160]}',
                    'detail': f'Required {fp_kind} is unverified: {qtext[:160]}',
                    'job_source': qsource,
                    'job_quote': qtext[:240],
                    'profile_source': 'profile.education',
                    'profile_quote': str(profile.get('education') or '')[:140],
                })

    # Scan filtered requirements sentences for regulated licenses not caught by qualification_rows.
    for src, sentence, scope in filtered_sentences:
        if scope in {'optional', 'benefits'} or OPTIONAL.search(sentence):
            continue
        m_lic = _FORMAL_LICENSE_RE.search(sentence)
        if not m_lic:
            continue
        if requirement_polarity(sentence, m_lic.start(), m_lic.end()) != 'required':
            continue
        ckey = clause_key(sentence)
        if ckey in seen_formal_keys:
            continue
        lic_term = m_lic.group(0)
        if _cached_pattern(lic_term).search(profile_full_text):
            seen_formal_keys.add(ckey)
            formal_prereq_conditions.append({
                'kind': 'license',
                'gate': 'qualification',
                'status': 'pass',
                'reason': f'Profile references regulated credential ({lic_term}).',
                'detail': f'Profile references regulated credential ({lic_term}).',
                'job_source': src,
                'job_quote': sentence[:240],
                'profile_source': 'profile.cv_text',
                'profile_quote': lic_term,
            })
            continue
        seen_formal_keys.add(ckey)
        formal_prereq_gaps.append({
            'clause_key': ckey,
            'quote': sentence[:200],
            'kind': 'license',
            'source': src,
        })
        formal_prereq_conditions.append({
            'kind': 'license',
            'gate': 'qualification',
            'status': 'unknown',
            'reason': f'Required regulated license is unverified: {sentence[:160]}',
            'detail': f'Required regulated license is unverified: {sentence[:160]}',
            'job_source': src,
            'job_quote': sentence[:240],
            'profile_source': 'profile.cv_text',
            'profile_quote': '',
        })
        if ckey not in req_clauses_seen:
            req_clauses_seen.add(ckey)
            req_total += 1.0
            requirement_gaps.append(sentence[:180])
            requirement_observations.append({
                'kind': 'license',
                'requirement': sentence[:200],
                'status': 'unknown',
                'job_source': src,
                'job_quote': sentence[:240],
                'evidence_source': '',
                'evidence_quote': '',
                'evidence_context': 'unspecified',
                'explanation': f'Required regulated license is unverified: {sentence[:140]}',
            })

    # Include required substantive skills (each once) in requirement_coverage and observations.
    for skill in sorted(job_skill_required):
        skey = f'skill:{skill.casefold()}'
        if skey in req_clauses_seen:
            continue
        req_clauses_seen.add(skey)
        w = 0.6 if skill in GENERIC_SKILLS else 1.0
        req_total += w
        if skill in profile_evidenced_skills:
            req_met += w * (domain_agreement_factor if skill in GENERIC_SKILLS else 1.0)
            requirement_observations.append({
                'kind': 'skill',
                'requirement': skill,
                'status': 'supported',
                'job_source': job_skill_sources.get(skill, 'description'),
                'job_quote': job_skill_quotes.get(skill, skill)[:240],
                'evidence_source': 'profile.skills' if skill in profile_skills_norm else 'profile.cv_text',
                'evidence_quote': skill,
                'evidence_context': 'declared' if skill in profile_skills_norm else 'professional',
                'explanation': f'Required skill {skill} is evidenced in the saved profile.',
            })
        elif skill not in requirement_gaps:
            requirement_gaps.append(f'Missing required skill: {skill}')
            requirement_observations.append({
                'kind': 'skill',
                'requirement': skill,
                'status': 'unknown',
                'job_source': job_skill_sources.get(skill, 'description'),
                'job_quote': job_skill_quotes.get(skill, skill)[:240],
                'evidence_source': '',
                'evidence_quote': '',
                'evidence_context': 'unspecified',
                'explanation': f'Required skill {skill} was not found in saved profile evidence.',
            })

    # Detect title-only / non-substantive postings (Finding 6).
    substantive_word_count = analysis['substantive_word_count']
    is_title_only = not filtered_body or (
        substantive_word_count < 12
        and not adjusted_duty_scores
        and not job_products
        and req_total == 0
        and not job_skill_weights
    )
    extra_unknowns: list[str] = []
    if is_title_only:
        extra_unknowns.append(
            'Substantive responsibilities and qualification requirements are missing from this posting (title-only); verify role fit.'
        )
    if not has_profile_evidence:
        extra_unknowns.append(
            'Profile has no demonstrated CV, skill, or experience evidence; desired roles reflect preference only.'
        )

    if is_title_only or not has_profile_evidence:
        requirement_coverage = 0.0
        if is_title_only:
            duty_coverage = 0.0
            domain_alignment = 0.0 if not has_profile_evidence else min(domain_alignment, 0.25)
    elif req_total > 0:
        req_prior = ((domain_alignment + duty_coverage + skill_overlap) / 3.0) * domain_agreement_factor
        requirement_coverage = (req_met + 0.22 * req_prior) / (req_total + 0.22)
    else:
        requirement_coverage = 0.45 * max(domain_alignment, duty_coverage, skill_overlap) * domain_agreement_factor
    requirement_coverage = max(0.0, min(1.0, requirement_coverage))

    # -----------------------------------------------------------------------
    # 5. Role alignment
    # -----------------------------------------------------------------------
    role_alignment, role_detail, role_psrc, role_pquote = _compute_role_alignment(
        title, pc, job_title_disc, prof_disc
    )

    # -----------------------------------------------------------------------
    # 6. Seniority & tenure fit (continuous + bounded soft penalties)
    # -----------------------------------------------------------------------
    job_sen = detect_title_seniority(title)
    tenure_minimums = analysis['tenure_minimums']
    req_years, req_years_quote, _ = max(tenure_minimums, key=lambda row: row[0], default=(None, '', ''))
    req_years_quote = req_years_quote[:240]
    prof_sen = pc['prof_sen']
    prof_years = pc['prof_years']
    prof_sen_quote = pc['prof_sen_quote']

    requirement_observations.extend(
        {**row, 'job_source': source}
        for _, quote, source in tenure_minimums
        for row in _scoped_experience_rows(quote, prof_years)
    )

    if job_sen is None and req_years is not None:
        if req_years >= 8:
            job_sen = 4
        elif req_years >= 5:
            job_sen = 3
        elif req_years >= 2:
            job_sen = 2
        else:
            job_sen = 1

    _TIER_EXPECTED_YRS = {0: 0.2, 1: 1.0, 2: 3.5, 3: 6.5, 4: 9.5, 5: 12.5}
    sen_scores: list[float] = []
    seniority_gap_pen = 0.0
    seniority_gap_detail = ''
    if job_sen is not None:
        diff = job_sen - prof_sen
        if diff == 0:
            tier_s = 1.0
        elif abs(diff) == 1:
            tier_s = 0.75 if diff < 0 else 0.68
        elif diff >= 2:
            tier_s = max(0.10, 0.50 - 0.18 * (diff - 1))
            seniority_gap_pen = min(
                SOFT_PENALTIES['seniority_gap'],
                SOFT_PENALTIES['seniority_gap'] * min(1.0, (diff - 1) / 2.0),
            )
            seniority_gap_detail = f'Job seniority tier ({job_sen}) exceeds profile seniority tier ({prof_sen})'
        else:
            tier_s = max(0.12, 0.55 - 0.20 * (abs(diff) - 1))
            if job_sen == 0 and prof_sen >= 2:
                seniority_gap_pen = min(
                    SOFT_PENALTIES['seniority_gap'],
                    SOFT_PENALTIES['seniority_gap'] * 0.75,
                )
                seniority_gap_detail = 'Internship/student-level posting vs experienced profile'
        if prof_years is not None:
            exp_yrs = _TIER_EXPECTED_YRS.get(job_sen, 3.5)
            cont_s = math.exp(-abs(exp_yrs - prof_years) / 6.5)
            sen_scores.append(0.70 * tier_s + 0.30 * cont_s)
        else:
            sen_scores.append(tier_s)

    tenure_shortfall_pen = 0.0
    tenure_detail = ''
    if req_years is not None and prof_years is not None:
        shortfall = req_years - prof_years
        if shortfall <= 0:
            sen_scores.append(1.0)
        elif shortfall <= 1.0:
            sen_scores.append(max(0.75, 1.0 - 0.20 * shortfall))
        else:
            sen_scores.append(max(0.10, 1.0 - 0.15 * shortfall))
            frac = min(1.0, (shortfall - 1.0) / 5.0)
            tenure_shortfall_pen = round(SOFT_PENALTIES['tenure_shortfall_max'] * frac, 4)
            tenure_detail = (
                f'Posting asks for {req_years:g}y experience vs profile {prof_years:g}y '
                f'(shortfall {shortfall:g}y)'
            )

    if not has_profile_evidence:
        seniority_fit = 0.0
    else:
        raw_seniority_fit = sum(sen_scores) / len(sen_scores) if sen_scores else 0.50
        relevance_gate = max(domain_alignment, duty_coverage, skill_overlap)
        # Retain a gradient above moderate relevance instead of flattening
        # distinct evidence at a threshold.
        relevance_strength = -math.expm1(-4.0 * relevance_gate) / -math.expm1(-4.0)
        seniority_fit = raw_seniority_fit * (0.12 + 0.88 * relevance_strength)
        if is_title_only:
            seniority_fit = min(seniority_fit, 0.25)
    seniority_fit = max(0.0, min(1.0, seniority_fit))

    # -----------------------------------------------------------------------
    # 7. Semantic similarity (0.0 when absent; smooth monotone normalization)
    # -----------------------------------------------------------------------
    sem_sim = 0.0
    sem_detail = 'Semantic evidence not provided'
    sem_quote = ''
    if isinstance(semantic, dict):
        raw_sim = semantic.get('similarity')
        if raw_sim is None and semantic.get('score') is not None:
            raw_sim = float(semantic['score']) / 100.0
        if raw_sim is not None:
            raw_sim_f = float(raw_sim)
            alignment = semantic.get('alignment')
            if alignment is None:
                alignment = (raw_sim_f - 0.35) / 0.55
            sem_sim = max(0.0, min(1.0, float(alignment)))
            sem_detail = f'Semantic similarity {raw_sim_f:.3f} (normalized {sem_sim:.3f})'
            sem_quote = str(semantic.get('job_evidence') or semantic.get('job_quote') or '')[:180]

    # -----------------------------------------------------------------------
    # 8. Remaining bounded soft penalties
    # -----------------------------------------------------------------------
    soft_conflicts: list[str] = []
    penalties: list[dict[str, Any]] = []

    if tenure_shortfall_pen > 0:
        soft_conflicts.append(tenure_detail)
        penalties.append({
            'signal': 'penalty:tenure_shortfall',
            'amount': round(tenure_shortfall_pen, 4),
            'detail': tenure_detail,
            'job_quote': req_years_quote,
            'profile_source': 'profile.experience_years',
            'profile_quote': prof_sen_quote,
        })

    if seniority_gap_pen > 0:
        soft_conflicts.append(seniority_gap_detail)
        penalties.append({
            'signal': 'penalty:seniority_gap',
            'amount': round(seniority_gap_pen, 4),
            'detail': seniority_gap_detail,
            'job_quote': title[:180],
            'profile_source': 'profile.seniority',
            'profile_quote': prof_sen_quote,
        })

    # Work style preference mismatch (only when NOT already a hard fail).
    pref_styles: frozenset[str] = pc['pref_styles']
    job_style = str(
        job.get('work_arrangement')
        or ('remote' if job.get('is_remote') else enriched.get('work_style') or '')
    ).strip().casefold()
    if pref_styles and job_style and job_style not in pref_styles and gate_result['location']['status'] != 'fail':
        pen_amt = SOFT_PENALTIES['work_style_mismatch']
        if job_style == 'hybrid' and ('remote' in pref_styles or 'onsite' in pref_styles):
            pen_amt *= 0.5
        detail = f'Work arrangement ({job_style}) differs from preferred {", ".join(sorted(pref_styles))}'
        soft_conflicts.append(detail)
        penalties.append({
            'signal': 'penalty:work_style',
            'amount': round(pen_amt, 4),
            'detail': detail,
            'job_quote': f'work_arrangement={job_style}',
            'profile_source': 'profile.work_styles',
            'profile_quote': ', '.join(sorted(pref_styles)),
        })

    # Employment type preference mismatch.
    pref_emp: frozenset[str] = pc['pref_emp']
    job_emp = str(
        job.get('employment_type') or enriched.get('employment_type') or ''
    ).strip().casefold().replace('_', '-').replace(' ', '-')
    if not job_emp and job_sen == 0:
        job_emp = 'internship'
    if pref_emp and job_emp and job_emp not in pref_emp:
        equiv = {'full-time', 'fulltime', 'permanent', 'vollzeit'}
        if not (job_emp in equiv and (pref_emp & equiv)):
            pen_amt = SOFT_PENALTIES['employment_type_mismatch']
            detail = f'Employment type ({job_emp}) differs from preferred {", ".join(sorted(pref_emp))}'
            soft_conflicts.append(detail)
            penalties.append({
                'signal': 'penalty:employment_type',
                'amount': round(pen_amt, 4),
                'detail': detail,
                'job_quote': f'employment_type={job_emp}',
                'profile_source': 'profile.employment_types',
                'profile_quote': ', '.join(sorted(pref_emp)),
            })

    # Unverified formal prerequisites (licenses/degrees, each counted once, bounded).
    if formal_prereq_gaps:
        has_license = any(g['kind'] == 'license' for g in formal_prereq_gaps)
        raw_fp = (
            SOFT_PENALTIES['formal_prerequisite_max']
            if has_license
            else min(SOFT_PENALTIES['formal_prerequisite_max'] * 0.6, 2.5 * len(formal_prereq_gaps))
        )
        first_fp = formal_prereq_gaps[0]
        detail = f'Unverified formal prerequisite ({first_fp["kind"]}): {first_fp["quote"][:140]}'
        soft_conflicts.append(detail)
        penalties.append({
            'signal': 'penalty:formal_prerequisite',
            'amount': round(raw_fp, 4),
            'detail': detail,
            'job_quote': first_fp['quote'],
            'profile_source': 'profile.education',
            'profile_quote': str(profile.get('education') or '')[:120],
        })

    # Two raw points at the maximum calibration slope move the rounded score
    # by at most five points, including when both flags are present.
    lang_visa_flags = [
        f for f in gate_result['flags']
        if f.get('gate') in {'language', 'eligibility'}
    ]
    if lang_visa_flags:
        lv_max = min(5.0, float(SOFT_PENALTIES['language_visa_flag_max']))
        lv_amt = min(lv_max, 0.75 * lv_max if len(lang_visa_flags) == 1 else lv_max)
        first_lv = lang_visa_flags[0]
        penalties.append({
            'signal': 'penalty:language_visa_flag',
            'amount': round(lv_amt, 4),
            'detail': f'Language/work-authorization verification flag ({len(lang_visa_flags)}): {first_lv["detail"][:140]}',
            'job_quote': first_lv.get('job_quote', ''),
            'profile_source': first_lv.get('profile_source', ''),
            'profile_quote': first_lv.get('profile_quote', ''),
        })

    # Enforce total_soft_penalty_cap proportionally so every ledger row stays transparent.
    total_pen = sum(p['amount'] for p in penalties)
    cap = float(SOFT_PENALTIES['total_soft_penalty_cap'])
    if total_pen > cap and total_pen > 0:
        scale = cap / total_pen
        for p in penalties:
            p['amount'] = round(p['amount'] * scale, 4)

    features: dict[str, float] = {
        'duty_coverage': round(duty_coverage, 4),
        'requirement_coverage': round(requirement_coverage, 4),
        'skill_overlap': round(skill_overlap, 4),
        'role_alignment': round(role_alignment, 4),
        'domain_alignment': round(max(0.0, min(1.0, domain_alignment)), 4),
        'seniority_fit': round(seniority_fit, 4),
        'semantic_similarity': round(sem_sim, 4),
        'domain_mismatch': round(max(0.0, min(1.0, domain_mismatch)), 4),
    }

    requirement_citation = next(
        (row for row in requirement_observations if row['status'] == 'supported'),
        next(iter(requirement_observations), {}),
    )
    feature_citations: dict[str, dict[str, str]] = {
        'duty_coverage': {
            'detail': (
                f'Domain-aware duty coverage {features["duty_coverage"]:.2f} '
                f'({len(adjusted_duty_scores)} duties, {len(job_products & prof_products)}/{len(job_products)} work products)'
            ),
            'job_quote': best_duty_quote,
            'profile_source': best_duty_psrc,
            'profile_quote': best_duty_pquote,
        },
        'requirement_coverage': {
            'detail': f'Requirement coverage {features["requirement_coverage"]:.2f} ({req_met:.1f}/{req_total:.1f} weighted clauses)',
            'job_quote': requirement_citation.get('job_quote', ''),
            'profile_source': requirement_citation.get('evidence_source', ''),
            'profile_quote': requirement_citation.get('evidence_quote', ''),
        },
        'skill_overlap': {
            'detail': f'Substantive skill overlap {features["skill_overlap"]:.2f} ({len(matched_skills)} matched, {len(missing_skills)} missing)',
            'job_quote': job_skill_quotes.get(matched_skills[0], '') if matched_skills else (job_skill_quotes.get(missing_skills[0], '') if missing_skills else ''),
            'profile_source': 'profile.skills' if matched_skills else '',
            'profile_quote': ', '.join(matched_skills[:6]),
        },
        'role_alignment': {
            'detail': role_detail,
            'job_quote': title[:180],
            'profile_source': role_psrc,
            'profile_quote': role_pquote,
        },
        'domain_alignment': {
            'detail': (
                f'Discipline alignment {features["domain_alignment"]:.2f} '
                f'(job: {", ".join(sorted(job_disc)) or "general"}; '
                f'profile: {", ".join(sorted(prof_disc)) or "general"})'
            ),
            'job_quote': title[:180],
            'profile_source': 'profile.cv_text' if prof_disc else '',
            'profile_quote': ', '.join(sorted(prof_disc)[:5]),
        },
        'seniority_fit': {
            'detail': f'Seniority/tenure fit {features["seniority_fit"]:.2f} (job tier={job_sen}, req_years={req_years}, profile tier={prof_sen}, years={prof_years})',
            'job_quote': req_years_quote or title[:180],
            'profile_source': 'profile.experience_years' if prof_years is not None else 'profile.seniority',
            'profile_quote': prof_sen_quote,
        },
        'semantic_similarity': {
            'detail': sem_detail,
            'job_quote': sem_quote,
            'profile_source': 'profile.cv_text' if sem_sim > 0 else '',
            'profile_quote': '',
        },
        'domain_mismatch': {
            'detail': (
                f'Uncovered specialist disciplines: {", ".join(sorted(uncovered_specialist))}'
                if uncovered_specialist
                else 'No uncovered specialist discipline mismatch'
            ),
            'job_quote': title[:180] if uncovered_specialist else '',
            'profile_source': 'profile.cv_text' if uncovered_specialist else '',
            'profile_quote': ', '.join(sorted(prof_disc)[:5]) if uncovered_specialist else '',
        },
    }

    content_match_matches = [
        {
            'skill': sk,
            'profile_source': 'skills' if sk in profile_skills_norm else 'cv_text',
            'weight': round(float(job_skill_weights.get(sk, 1.0)), 4),
            'source': job_skill_sources.get(sk, 'description'),
            'evidence': job_skill_quotes.get(sk, ''),
            'kind': job_skill_kinds.get(sk, 'required'),
        }
        for sk in matched_skills
    ]

    return {
        'features': features,
        'feature_citations': feature_citations,
        'penalties': penalties,
        'matched_skills': matched_skills,
        'missing_skills': missing_skills,
        'requirement_gaps': requirement_gaps[:12],
        'soft_conflicts': soft_conflicts,
        'extra_unknowns': extra_unknowns,
        'duty_evidence': duty_evidence,
        'task_alignment': task_alignment,
        'custom_skill_evidence': custom_evidence,
        'profile_skill_context': {
            sk: get_profile_skill_context(pc, sk)
            for sk in matched_skills
        },
        'formal_prereq_gaps': formal_prereq_gaps,
        'formal_prereq_conditions': formal_prereq_conditions,
        'requirement_observations': requirement_observations,
        'content_match_matches': content_match_matches,
        'job_disciplines': sorted(job_disc),
        'profile_disciplines': sorted(prof_disc),
        'uncovered_specialist': sorted(uncovered_specialist),
        'is_title_only': is_title_only,
    }
