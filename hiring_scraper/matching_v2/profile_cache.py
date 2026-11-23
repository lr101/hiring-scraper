"""Cached profile-side extraction, task-fact alignment, and role alignment for v2."""
from __future__ import annotations

from functools import lru_cache
from itertools import islice
import json
import re
from typing import Any

from hiring_scraper import task_alignment as _ta
from hiring_scraper.matching import (
    _profile_skill_contexts,
    _skill_context,
    normalize_skills,
    profile_content_sources,
)
from hiring_scraper.matching_v2.taxonomy import (
    ALL_SKILLS,
    _SKILL_CASE_TRANSLATION,
    _SKILL_COMBINED,
    _SKILL_LITERALS,
    _WORK_PRODUCTS,
    _cached_pattern,
)
from hiring_scraper.matching_v2.text_signals import (
    _extract_content_tokens,
    _profile_disciplines,
    _profile_experience_titles,
    _profile_seniority_and_years,
    _role_group_ids,
    _role_tokens,
)

# ---------------------------------------------------------------------------
# Cached profile-side extraction (reused across all jobs for the same profile)
# ---------------------------------------------------------------------------

_PROFILE_CACHE_FIELDS = (
    'skills',
    'skill_evidence',
    'desired_roles',
    'secondary_roles',
    'experience_entries',
    'experience_years',
    'seniority',
    'education',
    'cv_text',
    'summary',
    'headline',
    'duty_evidence',
    'cv_career_facts',
    'content_sources',
    'application_personalization',
    'application_details',
    'work_styles',
    'employment_types',
)


def _extract_profile_task_facts(
    profile_sources: tuple[tuple[str, str], ...],
    source_contexts: dict[str, str],
) -> tuple[tuple[str, str, frozenset[str], frozenset[str], str], ...]:
    """Cached profile-side fact extraction identical to task_alignment.align_tasks."""
    facts: list[tuple[str, str, frozenset[str], frozenset[str], str]] = []
    for source, text in islice(profile_sources, 80):
        if not isinstance(text, str):
            continue
        for full_sentence, _ in _ta._sentences(text[:12000]):
            full_sentence = full_sentence[:1000]
            bounded = bool(_ta._BOUNDED.search(full_sentence))
            for sentence, _ in _ta._atomic_duties(full_sentence):
                if _ta._excluded(sentence) or _ta._INTEREST.search(sentence):
                    continue
                actions = _ta._profile_actions(sentence)
                objects = _ta._semantic_objects(sentence)
                if actions and objects:
                    quote = full_sentence if bounded else sentence
                    declared = source_contexts.get(source)
                    context = (
                        'academic' if _ta._academic(source, quote) else
                        declared if declared in {'academic', 'research', 'professional', 'unspecified'} else
                        'professional' if source.startswith(('experience.', 'cv_career_facts.experiences[')) else
                        'unspecified'
                    )
                    facts.append((source, quote, frozenset(actions), frozenset(objects), context))
                if len(facts) >= 240:
                    break
            if len(facts) >= 240:
                break
        if len(facts) >= 240:
            break
    return tuple(facts)


def _align_tasks_with_facts(
    tasks: tuple,
    facts: tuple[tuple[str, str, frozenset[str], frozenset[str], str], ...],
) -> dict[str, Any]:
    """Run task_alignment matching against pre-extracted profile task facts."""
    rows = []
    for task, objects in tasks:
        best = None
        best_rank = (0.0, 0.0)
        actions = set(task['actions'])
        for source, quote, fact_actions, fact_objects, context in facts:
            shared = objects & fact_objects
            if not shared:
                continue
            coverage = len(shared) / max(1, len(objects))
            common_action = bool(actions & fact_actions)
            ownership_missing = 'own' in actions and 'own' not in fact_actions
            bounded_scope = (
                context in {'academic', 'research'}
                or bool(_ta._BOUNDED.search(quote))
                or 'support' in fact_actions
            )
            ownership_missing = ownership_missing or (
                bounded_scope and ('own' in actions or task['independent_scope'])
            )
            direct = (
                common_action
                and len(shared) >= min(2, len(objects))
                and coverage >= 0.5
                and not ownership_missing
                and not ((objects - shared) and (fact_objects - shared))
            )
            adjacent_action = any(
                frozenset((action, fact_action)) in _ta._ADJACENT_ACTIONS
                for action in actions
                for fact_action in fact_actions
            )
            adjacent = bool(shared) and (
                (common_action and objects == fact_objects)
                or len(shared) >= 2
                or (
                    any(obj.startswith('concept:') for obj in shared)
                    and (common_action or adjacent_action)
                )
            )
            support_lifecycle = (
                'support' in actions
                and bool(fact_actions & {'create', 'prepare', 'maintain'})
                and any(obj.startswith('concept:') for obj in shared)
                and objects <= fact_objects
            )
            adjacent = adjacent or support_lifecycle
            strength = 1.0 if direct else 0.5 if adjacent else 0.0
            if (strength, coverage) > best_rank:
                best_rank = (strength, coverage)
                best = (source, quote, sorted(set(task['objects']) & _ta._objects(quote)), context)
        status = 'supported' if best_rank[0] == 1.0 else 'transferable' if best_rank[0] else 'unknown'
        source, quote, shared_objs, context = best or ('', '', [], 'unspecified')
        rows.append({
            **task,
            'actions': list(task['actions']),
            'objects': list(task['objects']),
            'status': status,
            'evidence_source': source,
            'evidence_quote': quote,
            'evidence_context': context,
            'shared_objects': shared_objs,
            'explanation': {
                'supported': 'Same documented action and equivalent deliverable; verify any separate authority, domain or tenure prerequisites.',
                'transferable': 'Adjacent action or deliverable evidence; complete task scope is not established.',
                'unknown': 'No source-backed action and deliverable alignment found; absence is not proof of inability.',
            }[status],
        })
    coverage = (
        sum({'supported': 1.0, 'transferable': 0.5, 'unknown': 0.0}[row['status']] for row in rows)
        / len(rows) if rows else 0.0
    )
    return {
        'version': _ta.VERSION,
        'score': round(100 * coverage) if rows else None,
        'coverage': coverage,
        'requirements': rows,
        'facts_considered': len(facts),
        'qualification_proof': False,
    }


def _profile_cache_key(profile: dict) -> str:
    projection = {k: profile.get(k) for k in _PROFILE_CACHE_FIELDS if k in profile}
    return json.dumps(projection, sort_keys=True, ensure_ascii=False, default=str)


@lru_cache(maxsize=128)
def _precompute_profile_cached(key_json: str) -> dict[str, Any]:
    profile = json.loads(key_json)

    raw_sources = list(profile_content_sources(profile))
    if isinstance(profile.get('cv_text'), str) and profile['cv_text'].strip():
        raw_sources.append(('cv_text', profile['cv_text'].strip()))
    sources = tuple(dict.fromkeys((src, txt) for src, txt in raw_sources if txt))
    source_contexts = dict(_profile_skill_contexts(profile))

    duty_references: dict[str, Any] = {}
    extra_duty_sources: list[tuple[str, str, str, str]] = []
    for idx, row in enumerate((profile.get('duty_evidence') or [])[:20]):
        if isinstance(row, dict) and isinstance(row.get('quote'), str):
            src_key = f'duty_evidence[{idx}]'
            extra_duty_sources.append((
                src_key,
                row['quote'],
                str(row.get('context', '')),
                str(row.get('responsibility', '')),
            ))
            if row.get('reference') is not None:
                duty_references[src_key] = row.get('reference')

    duty_sources = tuple(
        (src, val, source_contexts.get(src, ''), '')
        for src, val in sources
    ) + tuple(extra_duty_sources)

    exp_titles = tuple(_profile_experience_titles(profile))
    desired_roles = tuple(str(r).strip() for r in (profile.get('desired_roles') or []) if str(r).strip())
    secondary_roles = tuple(str(r).strip() for r in (profile.get('secondary_roles') or []) if str(r).strip())

    # Demonstrated profile text ONLY — desired_roles and secondary_roles are preference only!
    edu_texts: list[str] = []
    for edu in (profile.get('education') or []):
        if isinstance(edu, dict):
            edu_texts.append(' '.join(str(v) for v in edu.values() if v))
        elif isinstance(edu, str) and edu.strip():
            edu_texts.append(edu.strip())
    profile_full_text = '\n'.join(
        part
        for part in (
            ' '.join(exp_titles),
            ' '.join(str(s) for s in (profile.get('skills') or []) if s),
            ' '.join(edu_texts),
            *(txt for _, txt in sources if txt),
            *(val for _, val, _, _ in extra_duty_sources if val),
        )
        if part and part.strip()
    )
    has_profile_evidence = bool(
        profile_full_text.strip()
        or profile.get('experience_years') is not None
        or str(profile.get('seniority') or '').strip()
    )

    prof_disc, prof_disc_strength = _profile_disciplines(profile, sources)
    prof_products = frozenset(
        wp for wp, (pat, _) in _WORK_PRODUCTS.items()
        if profile_full_text and pat.search(profile_full_text)
    )

    profile_skills_norm = frozenset(normalize_skills(profile.get('skills') or []))
    profile_evidenced_skills = set(profile_skills_norm)
    if profile_full_text:
        normalized_profile_text = profile_full_text.translate(_SKILL_CASE_TRANSLATION).lower()
        for skill, literals in _SKILL_LITERALS.items():
            if (
                skill not in profile_evidenced_skills
                and any(literal in normalized_profile_text for literal in literals)
                and _SKILL_COMBINED[skill].search(profile_full_text)
            ):
                profile_evidenced_skills.add(skill)

    custom_profile_skills = tuple(
        (str(s).strip(), str(s).strip().translate(_SKILL_CASE_TRANSLATION).lower())
        for s in (profile.get('skills') or [])
        if str(s).strip() and str(s).strip() not in ALL_SKILLS
    )

    prof_sen, prof_years, prof_sen_quote = _profile_seniority_and_years(profile)
    task_facts = _extract_profile_task_facts(sources, source_contexts)
    content_tokens = _extract_content_tokens(profile_full_text)

    # Precompute normalized role strings, group IDs, and tokens for fast role_alignment.
    # Regex compilation is deferred until a literal substring match is present.
    def _prep_roles(roles: tuple[str, ...]) -> tuple[tuple[str, str, frozenset[int], frozenset[str]], ...]:
        return tuple(
            (r, r.translate(_SKILL_CASE_TRANSLATION).lower(), _role_group_ids(r), _role_tokens(r))
            for r in roles
        )

    raw_edu = profile.get('education')
    if isinstance(raw_edu, list) and all(isinstance(e, dict) for e in raw_edu):
        edu_profile = profile
    elif isinstance(raw_edu, list):
        edu_profile = {**profile, 'education': [e for e in raw_edu if isinstance(e, dict)]}
    else:
        edu_profile = {**profile, 'education': []}

    return {
        'sources': sources,
        'source_contexts': source_contexts,
        'profile_projection': profile,
        'skill_contexts': {},
        'duty_sources': duty_sources,
        'duty_references': duty_references,
        'task_facts': task_facts,
        'exp_titles': exp_titles,
        'desired_prep': _prep_roles(desired_roles),
        'secondary_prep': _prep_roles(secondary_roles),
        'exp_prep': _prep_roles(exp_titles),
        'profile_full_text': profile_full_text,
        'has_profile_evidence': has_profile_evidence,
        'prof_disc': frozenset(prof_disc),
        'prof_disc_strength': prof_disc_strength,
        'prof_products': prof_products,
        'profile_skills_norm': profile_skills_norm,
        'profile_evidenced_skills': frozenset(profile_evidenced_skills),
        'custom_profile_skills': custom_profile_skills,
        'prof_sen': prof_sen,
        'prof_years': prof_years,
        'prof_sen_quote': prof_sen_quote,
        'content_tokens': content_tokens,
        'edu_profile': edu_profile,
        'pref_styles': frozenset(
            str(s).strip().casefold()
            for s in (profile.get('work_styles') or [])
            if str(s).strip()
        ),
        'pref_emp': frozenset(
            str(e).strip().casefold().replace('_', '-').replace(' ', '-')
            for e in (profile.get('employment_types') or [])
            if str(e).strip()
        ),
    }


def precompute_profile(profile: dict) -> dict[str, Any]:
    """Return cached profile-side extractions for fast repeated scoring across jobs."""
    return _precompute_profile_cached(_profile_cache_key(profile))


def _compute_role_alignment(
    job_title: str,
    profile_cache: dict[str, Any],
    job_title_disciplines: set[str],
    prof_disciplines: frozenset[str],
) -> tuple[float, str, str, str]:
    """Compute role_alignment in [0, 1] + (detail, profile_source, profile_quote)."""
    desired_prep = profile_cache['desired_prep']
    secondary_prep = profile_cache['secondary_prep']
    exp_prep = profile_cache['exp_prep']
    if not desired_prep and not secondary_prep and not exp_prep:
        return 0.35, 'No saved target roles; neutral role alignment', '', ''

    title_groups = _role_group_ids(job_title)
    title_toks = _role_tokens(job_title)
    title_norm = job_title.translate(_SKILL_CASE_TRANSLATION).lower()
    can_match_title_in_role = len(job_title) >= 4

    best_score = 0.0
    best_src = ''
    best_quote = ''
    best_detail = 'Job title did not align with saved target roles'

    def _eval_role_list(
        roles_prep: tuple[tuple[str, str, frozenset[int], frozenset[str]], ...],
        source_name: str,
        exact_base: float,
        exact_span: float,
        group_base: float,
        group_span: float,
        tok_w: float,
    ) -> None:
        nonlocal best_score, best_src, best_quote, best_detail
        for role, role_norm, r_groups, r_toks in roles_prep:
            union_len = len(title_toks | r_toks)
            tok_jacc = (len(title_toks & r_toks) / union_len) if union_len else 0.0
            exact_hit = (
                (role_norm in title_norm and _cached_pattern(role).search(job_title))
                or (
                    can_match_title_in_role
                    and title_norm in role_norm
                    and _cached_pattern(job_title).search(role)
                )
            )
            if exact_hit:
                sc = exact_base + exact_span * tok_jacc
                if sc > best_score:
                    best_score = sc
                    best_src = source_name
                    best_quote = role
                    best_detail = f'Exact title match with {source_name}: {role}'
                continue
            if title_groups and r_groups and (title_groups & r_groups):
                sc = group_base + group_span * tok_jacc
                if sc > best_score:
                    best_score = sc
                    best_src = source_name
                    best_quote = role
                    best_detail = f'Role-family match with {source_name}: {role}'
                continue
            if title_toks and r_toks:
                overlap = title_toks & r_toks
                if overlap:
                    min_cov = len(overlap) / max(1, min(len(title_toks), len(r_toks)))
                    sc = tok_w * (0.65 * min(1.0, min_cov) + 0.35 * tok_jacc)
                    if sc > best_score:
                        best_score = sc
                        best_src = source_name
                        best_quote = role
                        best_detail = f'Partial title token overlap ({", ".join(sorted(overlap))}) with {role}'

    _eval_role_list(desired_prep, 'profile.desired_roles', 0.90, 0.10, 0.78, 0.10, 0.55)
    _eval_role_list(secondary_prep, 'profile.secondary_roles', 0.74, 0.08, 0.64, 0.08, 0.45)
    _eval_role_list(exp_prep, 'profile.experience_entries', 0.65, 0.08, 0.55, 0.08, 0.38)

    # Modest cross-language title-discipline fallback when job title discipline directly matches
    # a demonstrated profile discipline (never for loose adjacency or desired-only roles).
    if job_title_disciplines and prof_disciplines:
        shared = job_title_disciplines & prof_disciplines
        if shared and best_score < 0.35:
            best_score = 0.35
            best_detail = f'Title discipline matches profile ({", ".join(sorted(shared))})'
            best_src = best_src or 'profile.disciplines'
            best_quote = best_quote or ', '.join(sorted(shared))

    return round(min(1.0, max(0.0, best_score)), 4), best_detail, best_src, best_quote




def get_profile_skill_context(pc: dict[str, Any], skill: str) -> dict[str, Any]:
    """Return cached profile skill context lazily computed on first matched-skill lookup."""
    if skill not in pc['profile_evidenced_skills']:
        return {
            'context': 'declared',
            'scope_verified': False,
            'source': 'profile.skills',
            'quote': skill,
        }
    cache = pc['skill_contexts']
    ctx = cache.get(skill)
    if ctx is None:
        ctx = _skill_context(pc['profile_projection'], skill)
        cache[skill] = ctx
    return dict(ctx)
