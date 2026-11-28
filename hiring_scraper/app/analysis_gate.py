"""Freshness, embedding pre-rank, and user-intent gate for local LLM job analysis.

Every enqueue path, startup reconciliation, worker selection, and progress counter
uses these helpers so gated-out postings neither consume LLM time nor inflate
the remaining-work ETA.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import timedelta

from sqlalchemy import Integer, and_, cast, exists, func, literal_column, not_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hiring_scraper.app.models import (
    Job, JobAnalysis, JobAnalysisQueue, JobApplication, SemanticSearchDocument, UserProfile, utcnow,
)
from hiring_scraper.job_analysis import VERSION as ANALYSIS_VERSION
from hiring_scraper.matching_v3.profile_text import profile_roles, profile_sources
from hiring_scraper.semantic_matching import MODEL_KEY as EMBEDDING_MODEL_KEY

DEFAULT_MAX_AGE_DAYS = 14
# intfloat/multilingual-e5-small cosine similarities are anisotropic (concentrated
# in ~0.75-0.93). On the public eval set, grade-3 fits score >= 0.831 and grade-2
# fits score >= 0.808, while unrelated German postings cluster around 0.76-0.79.
# 0.80 keeps full recall on relevant roles while dropping the unrelated tail;
# descending similarity order keeps the ~800/day LLM budget on the best candidates.
DEFAULT_MIN_SIMILARITY = 0.80
ISO_DATE_PREFIX_PG = r'^[0-9]{4}-[0-9]{2}-[0-9]{2}'
# Pre-rank score stored for a posting that cheap rules rule out (see ``analysis_rule_out``):
# below any configurable threshold, so it is skipped exactly like a low-similarity posting.
RULED_OUT_SIMILARITY = -2.0
# Bump when ``analysis_rule_out`` changes so queued postings are checked again.
RULE_OUT_VERSION = 'rule-out-1'
DEFAULT_ANALYSIS_COUNTRY = 'DE'
# Below this many description characters the model has nothing to read: the posting
# would be "Not enough text to score" anyway.
MIN_DESCRIPTION_CHARS = 40


# Pre-rank boost for a job title that names a target role. E5 similarities of whole postings
# are compressed (unrelated engineering postings reach 0.84-0.87, like many real fits), while
# the title is the clearest cheap signal of the role. 0.06 lifts a title naming a desired
# role above almost every posting without one; partial or past-title matches get less.
TITLE_BOOST = 0.06
# Order of the LLM queue loses this much per day since first seen: a fresh posting is
# open longer, so the same LLM time buys more days of a visible score.
AGE_PENALTY_PER_DAY = 0.003
# One LLM pick in this many from the scored queue is ordered by plain E5 similarity instead,
# so postings that fit without naming a target role in their title ("Warehouse Team Leader"
# for "Teamleiter Logistik") are still read while the queue exceeds capacity.
DEFAULT_EXPLORE_EVERY = 4
TITLE_ROLE_FACTORS = {'desired': 1.0, 'secondary': 0.85, 'past': 0.7, 'past_excluded': 0.0}
_TITLE_WORD_RE = re.compile(r'\w+')
# Level, gender and stop words carry no role meaning ("Senior", "(m/w/d)").
_TITLE_NOISE = frozenset({
    'senior', 'sr', 'junior', 'jr', 'of', 'and', 'und', 'the', 'der', 'die', 'das', 'for', 'für', 'in', 'im',
    'm', 'w', 'd', 'f', 'x', 'mwd', 'all', 'genders', 'gn', 'r',
})
# German role-word stems and their English equivalents, so a role in one language finds a
# title in the other: "Produktmanager" names "Product Manager", "Requirements Manager" names
# "Anforderungsmanager". Words spelled the same in both (Manager, Analyst, Owner) need no entry.
ROLE_WORDS_DE_EN = (
    ('anforderung', 'requirement'), ('analytiker', 'analyst'), ('berater', 'consultant'),
    ('beratung', 'consulting'), ('buchhalter', 'accountant'), ('daten', 'data'),
    ('digitalisierung', 'digitalization'), ('einkauf', 'purchasing'), ('elektrotechnik', 'electrical engineering'),
    ('entwickler', 'developer'), ('entwicklung', 'development'), ('fertigung', 'manufacturing'),
    ('forscher', 'researcher'), ('forschung', 'research'), ('ingenieur', 'engineer'),
    ('innovation', 'innovation'), ('koordinator', 'coordinator'), ('lager', 'warehouse'),
    ('leiter', 'lead'), ('logistik', 'logistics'), ('maschinenbau', 'mechanical engineering'),
    ('produkt', 'product'), ('produktion', 'production'), ('projekt', 'project'), ('prozess', 'process'),
    ('qualität', 'quality'), ('spezialist', 'specialist'), ('technisch', 'technical'),
    ('technologie', 'technology'), ('vertrieb', 'sales'),
)


def _translator(pairs, *, linking_s: bool):
    table = dict(pairs)
    # Longest first, so "produktion" is not read as "produkt"; German compounds may add a linking s.
    keys = sorted(table, key=len, reverse=True)
    pattern = re.compile('(' + '|'.join(re.escape(key) for key in keys) + ')' + ('s?' if linking_s else ''))
    return lambda text: pattern.sub(lambda match: f' {table[match.group(1)]} ', text)


_TO_ENGLISH = _translator(ROLE_WORDS_DE_EN, linking_s=True)
_TO_GERMAN = _translator([(english, german) for german, english in ROLE_WORDS_DE_EN], linking_s=False)


def _role_words(text: str) -> list[str]:
    return [word for word in _TITLE_WORD_RE.findall(str(text or '').casefold())
            if len(word) > 1 and word not in _TITLE_NOISE]


def _role_variants(role: str) -> list[list[str]]:
    """The role's words as written, read as English, and read as German. Leftover inflection
    fragments of a split compound ("technisch|er") are dropped."""
    original = _role_words(role)
    variants = [original]
    for translate in (_TO_ENGLISH, _TO_GERMAN):
        words = [word for word in _role_words(translate(' '.join(original)))
                 if len(word) > 3 or word in original]
        if words and words not in variants:
            variants.append(words)
    return variants


def _word_found(part: str, title_words: list[str]) -> bool:
    # German compounds: "Projektmanager" is in "IT-Projektmanagerin" and "Softwareentwickler"
    # is in "Fullstack-Softwareentwickler"; short words must start a title word.
    return any(word.startswith(part) or (len(part) >= 5 and part in word) for word in title_words)


def title_roles(preferences: dict) -> list[tuple[str, float]]:
    """(role, factor) for each target role a title can name: desired, secondary, then past titles."""
    localized = [content for content in (preferences.get('localized_content') or {}).values()
                 if isinstance(content, dict)]
    roles: list[tuple[str, float]] = []
    for kind, field in (('desired', 'desired_roles'), ('secondary', 'secondary_roles')):
        for role in [*(preferences.get(field) or []), *(r for c in localized for r in c.get(field) or [])]:
            if isinstance(role, str) and role.strip():
                roles.append((role, TITLE_ROLE_FACTORS[kind]))
    excluded = [term.casefold() for term in preferences.get('excluded_terms') or [] if isinstance(term, str) and term.strip()]
    for entry in ((preferences.get('cv_career_facts') or {}).get('experiences') or [])[:12]:
        title = entry.get('title') if isinstance(entry, dict) else None
        if isinstance(title, str) and title.strip():
            kind = 'past_excluded' if any(re.search(rf'(?<!\w){re.escape(term)}(?!\w)', title.casefold())
                                          for term in excluded) else 'past'
            roles.append((title, TITLE_ROLE_FACTORS[kind]))
    return roles


def title_role_fit(title: str, roles: list[tuple[str, float]]) -> float:
    """0..1: how fully the job title names a target role in English or German (role words
    found in the title, squared so one shared generic word such as "Engineer" counts little),
    times the role's factor."""
    title_words = _role_words(title)
    best = 0.0
    for role, factor in roles:
        if factor <= 0:
            continue
        for parts in _role_variants(role):
            if parts:
                share = sum(_word_found(part, title_words) for part in parts) / len(parts)
                best = max(best, factor * share * share)
    return best


def analysis_country() -> str | None:
    """Country a workplace must be in (or open remotely to) for LLM analysis; empty disables."""
    value = os.getenv('RADIUS_ANALYSIS_COUNTRY', DEFAULT_ANALYSIS_COUNTRY).strip().upper()
    return value or None


def analysis_rule_out(job: Job, profiles) -> str | None:
    """Why the LLM should not read this posting, or None. Only cheap, rule-based reasons that
    hide the posting for every profile regardless of what the model would extract:

    - ``no_description``: nothing beyond the title to analyse;
    - ``foreign_only``: every stated workplace is outside the analysis country and the
      posting is not open to remote work from it (missing locations are not evidence);
    - ``gated_out``: the eligibility gates (excluded title terms, explicit language,
      location or work-permit conflicts) hide it for every profile.
    """
    from hiring_scraper.app.board_scope import workplace_outside_country
    from hiring_scraper.app.enrichment import current_enrichment, job_input
    from hiring_scraper.gates import evaluate_gates

    if len((job.description or '').strip()) < MIN_DESCRIPTION_CHARS:
        return 'no_description'
    if workplace_outside_country(job, analysis_country()):
        return 'foreign_only'
    preferences = [profile.matching_preferences for profile in profiles]
    if preferences:
        source, enrichment = job_input(job), current_enrichment(job)
        if all(not evaluate_gates(source, prefs, enrichment)['eligible'] for prefs in preferences):
            return 'gated_out'
    return None


def max_age_days() -> int:
    raw = os.getenv('RADIUS_ANALYSIS_MAX_AGE_DAYS', str(DEFAULT_MAX_AGE_DAYS)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_AGE_DAYS
    return max(0, value)


def min_similarity() -> float:
    raw = os.getenv('RADIUS_ANALYSIS_MIN_SIMILARITY', str(DEFAULT_MIN_SIMILARITY)).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MIN_SIMILARITY
    return max(-1.0, min(1.0, value))


def explore_every() -> int:
    """Every Nth LLM pick from the scored queue ignores the title boost (0 disables)."""
    raw = os.getenv('RADIUS_ANALYSIS_EXPLORE_EVERY', str(DEFAULT_EXPLORE_EVERY)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_EXPLORE_EVERY


def prerank_order(*, explore: bool = False):
    """LLM queue order for pre-ranked postings: stored pre-rank score (plain E5 similarity for
    an exploration slot), minus a small amount per day since first seen, so among similar fits
    the posting with more open days left goes first."""
    age_days = func.extract('epoch', func.now() - Job.first_seen_at) / 86400.0
    score = JobAnalysisQueue.raw_similarity if explore else JobAnalysisQueue.similarity
    return (score - AGE_PENALTY_PER_DAY * age_days).desc().nulls_last()


def prerank_score(similarity: float, title: str, roles_by_profile: list[list[tuple[str, float]]]) -> float:
    """Stored pre-rank score: best profile E5 similarity plus the title boost of the best profile."""
    fit = max((title_role_fit(title, roles) for roles in roles_by_profile), default=0.0)
    return float(similarity) + TITLE_BOOST * fit


def fresh_job_clause(max_days: int | None = None):
    """SQL predicate for postings within the freshness window.

    Uses ``jobs.date_posted`` when it starts with ``YYYY-MM-DD`` and compares as
    TEXT against ``to_char(current_date - N, 'YYYY-MM-DD')`` without ever casting
    ``date_posted`` to ``date`` (production contains invalid dates like
    ``2026-98-12``). Falls back to ``jobs.first_seen_at`` otherwise.
    """
    days = max_age_days() if max_days is None else max(0, int(max_days))
    cutoff_ts = utcnow() - timedelta(days=days)
    has_iso = and_(Job.date_posted.is_not(None), Job.date_posted.op('~')(ISO_DATE_PREFIX_PG))
    no_iso = or_(Job.date_posted.is_(None), Job.date_posted.op('!~')(ISO_DATE_PREFIX_PG))
    cutoff_text = func.to_char(literal_column('current_date') - cast(days, Integer), 'YYYY-MM-DD')
    return or_(
        and_(has_iso, func.left(Job.date_posted, 10) >= cutoff_text),
        and_(no_iso, Job.first_seen_at >= cutoff_ts),
    )


def user_intent_job_exists(job_id_col=Job.id):
    """Whether any profile saved this posting or moved it past auto-created ``new``."""
    return exists(select(1).where(
        JobApplication.job_id == job_id_col,
        not_(and_(JobApplication.auto_created.is_(True), JobApplication.status == 'new')),
    ))


def queue_row_eligible_clause(
    *,
    max_days: int | None = None,
    min_sim: float | None = None,
    profile_set_hash: str | None = None,
    require_current_hash: bool = False,
):
    """Whether a ``job_analysis_queue`` row (joined to ``jobs``) passes the analysis gate."""
    threshold = min_similarity() if min_sim is None else float(min_sim)
    override = or_(JobAnalysisQueue.priority.is_(True), user_intent_job_exists(JobAnalysisQueue.job_id))
    scored = [
        fresh_job_clause(max_days=max_days),
        JobAnalysisQueue.similarity.is_not(None),
        JobAnalysisQueue.similarity >= threshold,
    ]
    if require_current_hash:
        if profile_set_hash is None:
            return override
        scored.append(JobAnalysisQueue.profile_set_hash == profile_set_hash)
    return or_(override, and_(*scored))


def enqueue_priority_analysis(session, job_id: int) -> None:
    """Queue a job with priority override so it bypasses age and similarity gates."""
    statement = pg_insert(JobAnalysisQueue).values(
        job_id=job_id,
        queued_at=utcnow(),
        priority=True,
    ).on_conflict_do_update(
        index_elements=['job_id'],
        set_={'priority': True},
        where=JobAnalysisQueue.priority.is_(False),
    )
    session.execute(statement)


def enqueue_priority_if_needed(session, job_id: int, *, is_active: bool) -> bool:
    """Priority-queue an active job unless it already has a current ``ok`` analysis."""
    if not is_active:
        return False
    has_current = session.scalar(select(JobAnalysis.job_id).where(
        JobAnalysis.job_id == job_id,
        JobAnalysis.version == ANALYSIS_VERSION,
        JobAnalysis.status == 'ok',
    )) is not None
    if has_current:
        return False
    enqueue_priority_analysis(session, job_id)
    return True


def profile_embedding_text(preferences: dict) -> str:
    """Compact career-fact query text for embedding a profile with the shared E5 model."""
    parts: list[str] = []
    roles = profile_roles(preferences)
    if roles:
        parts.append('Target roles: ' + ', '.join(roles[:10]))
    skills = [skill.strip() for skill in (preferences.get('skills') or [])
              if isinstance(skill, str) and skill.strip()]
    if skills:
        parts.append('Skills: ' + ', '.join(skills[:30]))
    summary = preferences.get('summary')
    if isinstance(summary, str) and summary.strip():
        parts.append('Summary: ' + ' '.join(summary.split()))
    for source_key, snippet in profile_sources(preferences):
        if source_key in ('summary', 'skills'):
            continue
        clean = ' '.join(snippet.split())
        if clean:
            parts.append(clean)
        if sum(len(part) for part in parts) >= 1000:
            break
    return '. '.join(parts).strip()[:900]


def profile_embedding_hash(text: str) -> str:
    """Content digest matching the E5 ``query:`` representation."""
    return hashlib.sha256(f'query: {text}'.encode('utf-8')).hexdigest()


def current_profile_set_hash(session) -> str | None:
    """Digest of everything a queued posting's pre-rank depends on: all current profile
    embeddings, the profiles' matching preferences (eligibility gates) and the rule-out
    rules. None when no profile is embedded."""
    rows = session.execute(
        select(SemanticSearchDocument.entity_id, SemanticSearchDocument.content_hash)
        .where(
            SemanticSearchDocument.entity_type == 'profile',
            SemanticSearchDocument.model_revision == EMBEDDING_MODEL_KEY,
        )
        .order_by(SemanticSearchDocument.entity_id)
    ).all()
    if not rows:
        return None
    # Editors bump ``source_version`` on every profile input change (see ``source_changes``).
    profile_versions = session.execute(
        select(UserProfile.id, UserProfile.source_version).order_by(UserProfile.id)).all()
    payload = json.dumps({
        'embeddings': [[entity_id, content_hash] for entity_id, content_hash in rows],
        'profiles': [[profile_id, version] for profile_id, version in profile_versions],
        'rule_out': [RULE_OUT_VERSION, analysis_country(), MIN_DESCRIPTION_CHARS, TITLE_BOOST],
    }, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
